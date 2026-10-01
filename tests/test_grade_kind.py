"""
Tests for mh2/grade_kind.py (contract 8.1).

Synthetic databases are built in memory from config.SCHEMA; the real-data
tests run on a temp COPY of config.DB (skipped if it is missing).

Run with: MH2_DATA_DIR=... python3 tests/test_grade_kind.py
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
import traceback
from collections import Counter
from pathlib import Path


# Integrator: temp dirs (real-DB copies are ~150 MB each) are removed at exit.
import atexit as _atexit  # noqa: E402
_TMPDIRS = []


def _mkdtemp(**kw):
    d = tempfile.mkdtemp(**kw)
    _TMPDIRS.append(d)
    return d


_atexit.register(lambda: [shutil.rmtree(d, ignore_errors=True) for d in _TMPDIRS])

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from mh2 import grade_kind as gk  # noqa: E402

GRADE_ROWS = [("PK", 0), ("K", 1), ("1", 2), ("2", 3), ("3", 4), ("OUT", 99)]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def synth():
    """In-memory DB from the real schema with grade_order, 3 nodes and rulings."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(config.SCHEMA.read_text())
    con.executemany("INSERT INTO grade_order(grade, ord) VALUES (?, ?)", GRADE_ROWS)
    con.execute("INSERT INTO stems(stem_id, name) VALUES ('AAA', 'Alpha')")
    for nid in ("AAA-1", "AAA-2", "AAA-3"):
        con.execute("INSERT INTO nodes(node_id, stem_id, seq, source_key, node_text) "
                    "VALUES (?, 'AAA', 1, ?, 't')", (nid, "AAA:" + nid))
    return con


_REAL = {}


def real_con():
    """Read-only connection to a temp copy of the real DB, or None (SKIP)."""
    if "con" not in _REAL:
        if not Path(config.DB).exists():
            _REAL["con"] = None
        else:
            tmp = Path(_mkdtemp())
            dst = tmp / "mh2.db"
            shutil.copy(config.DB, dst)
            con = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            _REAL["con"] = con
    return _REAL["con"]


class Skip(Exception):
    pass


def need_real():
    con = real_con()
    if con is None:
        raise Skip("no real mh2.db")
    return con


# ---------------------------------------------------------------------------
# synthetic tests
# ---------------------------------------------------------------------------

def test_constants():
    assert gk.GRADES == ("PK", "K", "1", "2", "3", "4", "5", "6", "7", "8", "A1")
    assert "OUT" not in gk.GRADES
    assert set(gk.STORED_KINDS) <= set(gk.READ_STATES)
    assert gk.OWED_STATES == ("core", "span", "unconfirmed", "unknown")
    assert set(gk.OWED_STATES).isdisjoint(gk.BRIDGE_STATES)


def test_kind_source_empty_table_is_fallback():
    con = synth()
    assert gk.kind_source(con) == "fallback"          # table exists, 0 rows
    con.execute("INSERT INTO node_grade_kind VALUES ('AAA-1', '2', 'core', 'b')")
    assert gk.kind_source(con) == "grade_type"


def test_kind_source_missing_table_is_fallback():
    con = synth()
    con.execute("DROP TABLE node_grade_kind")
    assert gk.kind_source(con) == "fallback"
    con.execute("INSERT INTO node_grade VALUES ('AAA-1', '2')")
    assert gk.build_grade_kinds(con) == {("AAA-1", "2"): "unknown"}


def test_fallback_all_unknown():
    con = synth()
    con.executemany("INSERT INTO node_grade VALUES (?, ?)",
                    [("AAA-1", "2"), ("AAA-1", "3"), ("AAA-2", "K")])
    kinds = gk.build_grade_kinds(con)
    assert set(kinds) == {("AAA-1", "2"), ("AAA-1", "3"), ("AAA-2", "K")}
    assert set(kinds.values()) == {"unknown"}


def test_row_without_kind_is_unknown_never_off_grade():
    con = synth()
    con.executemany("INSERT INTO node_grade VALUES (?, ?)",
                    [("AAA-1", "2"), ("AAA-2", "2")])
    con.execute("INSERT INTO node_grade_kind VALUES ('AAA-1', '2', 'span', 'b')")
    kinds = gk.build_grade_kinds(con)
    assert kinds[("AAA-1", "2")] == "span"
    assert kinds[("AAA-2", "2")] == "unknown"
    assert gk.kind_for("AAA-2", "2", kinds, {}) == "unknown"
    assert gk.kind_for("AAA-2", "3", kinds, {}) == "off_grade"   # no row, other grade


def test_kind_for_precedence():
    kinds = {("N", "2"): "core"}
    leaf = {"N": {"resolution": "leaf"}}
    nog = {"N": {"resolution": "no_grade_field"}}
    ruled = {"N": {"resolution": "ruled"}}
    assert gk.kind_for("N", "2", kinds, leaf) == "leaf"          # leaf beats kind
    assert gk.kind_for("N", "9", kinds, leaf) == "leaf"          # ... and any grade
    assert gk.kind_for("N", "2", kinds, nog) == "no_grade"       # no_grade beats kind
    assert gk.kind_for("N", "2", kinds, ruled) == "core"
    assert gk.kind_for("N", "3", kinds, ruled) == "off_grade"
    assert gk.kind_for("N", "2", kinds, {}) == "core"            # no ruling row


def test_ruling_by_node_columns_and_old_schema():
    con = synth()
    con.execute("INSERT INTO node_grade_ruling(node_id, raw_value, resolution, is_leaf, "
                "notes, ruling_type, needs_writer_review, states_mentioned) "
                "VALUES ('AAA-1', '2', 'ruled', 0, 'n', 'single', 1, 'TX')")
    r = gk.ruling_by_node(con)["AAA-1"]
    assert set(r) == {"raw_value", "resolution", "is_leaf", "notes", "ruling_type",
                      "states_mentioned", "needs_writer_review"}
    assert r["ruling_type"] == "single" and r["states_mentioned"] == "TX"
    # Pre-grade_type table: the three newer columns are missing -> None.
    old = sqlite3.connect(":memory:")
    old.execute("CREATE TABLE node_grade_ruling (node_id TEXT PRIMARY KEY, raw_value TEXT, "
                "canon_key TEXT, resolution TEXT NOT NULL, is_leaf INTEGER, notes TEXT)")
    old.execute("INSERT INTO node_grade_ruling VALUES ('X', '3', 'k', 'ruled', 0, NULL)")
    r = gk.ruling_by_node(old)["X"]
    assert r["raw_value"] == "3" and r["resolution"] == "ruled"
    assert r["ruling_type"] is None and r["states_mentioned"] is None
    assert r["needs_writer_review"] is None
    assert gk.ruling_by_node(sqlite3.connect(":memory:")) == {}   # no table


# ---------------------------------------------------------------------------
# real-data tests
# ---------------------------------------------------------------------------

def test_real_kind_source_and_key_set():
    con = need_real()
    assert gk.kind_source(con) == "grade_type"
    kinds = gk.build_grade_kinds(con)
    rows = {(r[0], r[1]) for r in con.execute("SELECT node_id, grade FROM node_grade")}
    assert set(kinds) == rows
    assert len(kinds) == 535, len(kinds)


def test_real_counter():
    con = need_real()
    c = Counter(gk.build_grade_kinds(con).values())
    assert c == Counter(core=155, span=213, unconfirmed=143, unknown=24), c


def test_real_kind_for_split_and_no_off_grade():
    con = need_real()
    kinds = gk.build_grade_kinds(con)
    rulings = gk.ruling_by_node(con)
    states = Counter(gk.kind_for(n, g, kinds, rulings) for (n, g) in kinds)
    assert "off_grade" not in states, states
    assert states["leaf"] == 13 and states["unknown"] == 11, states
    assert sum(states.values()) == 535


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------

def main() -> int:
    failed = 0
    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        try:
            fn()
            print(f"ok {name}")
        except Skip as exc:
            print(f"SKIP {name}: {exc}")
        except Exception:                      # noqa: BLE001
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
