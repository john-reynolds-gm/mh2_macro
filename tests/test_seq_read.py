"""
Real-data tests for mh2/seq_read.py (contract 8.1, items 1-10 and 15).
Runs on a temp COPY of config.DB; skipped if the DB is missing.

Run with: MH2_DATA_DIR=... python3 tests/test_seq_read.py
"""
from __future__ import annotations

import csv
import json
import re
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from mh2 import grade_kind  # noqa: E402
from mh2 import seq_read as R  # noqa: E402

CHIPS = {"PK": 27, "K": 57, "1": 59, "2": 57, "3": 70, "4": 82, "5": 79,
         "6": 106, "7": 107, "8": 67, "A1": 80}
IN_GRADE = {"PK": 24, "K": 45, "1": 37, "2": 35, "3": 37, "4": 61, "5": 54,
            "6": 81, "7": 77, "8": 37, "A1": 47}
OWED = {"PK": 24, "K": 44, "1": 36, "2": 33, "3": 36, "4": 61, "5": 52,
        "6": 79, "7": 75, "8": 35, "A1": 47}
COM12, WHO10 = "COM:58ec03661c5f2d1b", "WHO:d65c6bed74a5ba1c"


class Skip(Exception):
    pass


_CACHE = {}


def con():
    """Read-only connection to a temp copy of the real DB (SKIP if missing)."""
    if "con" not in _CACHE:
        if not Path(config.DB).exists():
            raise Skip("no real mh2.db")
        dst = Path(_mkdtemp()) / "mh2.db"
        shutil.copy(config.DB, dst)
        _CACHE["con"] = R.connect_ro(dst)
    return _CACHE["con"]


def slice_for(grade):
    if grade not in _CACHE:
        _CACHE[grade] = R.build_slice(con(), grade)
    return _CACHE[grade]


def all_nodes(sl):
    return [n for ss in sl["super_stems"] for st in ss["stems"]
            for cs in st["concept_skills"] for n in cs["nodes"]]


def all_cs(sl):
    return [cs for ss in sl["super_stems"] for st in ss["stems"]
            for cs in st["concept_skills"]]


# 1 -------------------------------------------------------------------------
def test_in_grade_keys_match_sql():
    c = con()
    for g in grade_kind.GRADES:
        expect = {r[0] for r in c.execute(
            "SELECT n.source_key FROM nodes n JOIN node_grade g USING(node_id) "
            "WHERE g.grade = ?", (g,))}
        got = {n["source_key"] for n in all_nodes(slice_for(g)) if n["in_grade"]}
        assert got == expect, g


# 2 -------------------------------------------------------------------------
def test_chips_and_counts_per_grade():
    con()
    for g in grade_kind.GRADES:
        c = slice_for(g)["counts"]
        assert c["chips"] == CHIPS[g], (g, c["chips"])
        assert c["in_grade_nodes"] == IN_GRADE[g], (g, c["in_grade_nodes"])
        assert c["owed_nodes"] == OWED[g], (g, c["owed_nodes"])
        assert c["chips"] == c["in_grade_nodes"] + c["context_nodes"]
        assert len(all_nodes(slice_for(g))) == c["chips"]
    infos = {i["grade"]: i for i in R.list_grades(con())}
    for g in grade_kind.GRADES:
        assert (infos[g]["in_grade_nodes"], infos[g]["owed_nodes"], infos[g]["chips"]) \
            == (IN_GRADE[g], OWED[g], CHIPS[g]), g
        assert infos[g]["sequence"] is None


# 3 -------------------------------------------------------------------------
def test_g2_counts_and_states():
    sl = slice_for("2")
    assert sl["counts"] == {
        "in_grade_nodes": 35, "in_grade_nodes_excl_leaf": 33, "owed_nodes": 33,
        "leaf_in_grade": 2, "concept_skills": 24, "concept_skills_excl_leaf": 22,
        "context_nodes": 22, "stems": 7, "chips": 57}, sl["counts"]
    nodes = all_nodes(sl)
    assert Counter(n["state"] for n in nodes if n["in_grade"]) == \
        Counter(core=13, span=14, unconfirmed=6, leaf=2)
    assert Counter(n["state"] for n in nodes if not n["in_grade"]) == \
        Counter(off_grade=21, no_grade=1)
    leaves = sorted(n["node_id"] for n in nodes if n["state"] == "leaf")
    assert leaves == ["COU-0024", "MUL-0004"], leaves
    assert sl["kind_source"] == "grade_type"
    # Build-dependent: every rebuild stamps a new ingest time, so check shape only.
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}",
                        sl["ladders_last_read"] or ""), sl["ladders_last_read"]


# 4 -------------------------------------------------------------------------
def test_strip_order_and_no_duplicates():
    for g in grade_kind.GRADES:
        sl = slice_for(g)
        seen = set()
        for cs in all_cs(sl):
            seqs = [n["seq"] for n in cs["nodes"]]
            assert seqs == sorted(set(seqs)), (g, cs["cs_id"])
            for n in cs["nodes"]:
                assert n["source_key"] not in seen, n["source_key"]
                seen.add(n["source_key"])


# 5 -------------------------------------------------------------------------
def test_label_display_has_no_marker():
    c = con()
    raw = {r[0] for r in c.execute("SELECT DISTINCT concept_skill FROM nodes")
           if r[0] and R.MARKER_RE.search(r[0])}
    assert len(raw) == 19, len(raw)
    for g in grade_kind.GRADES:
        for cs in all_cs(slice_for(g)):
            assert not R.MARKER_RE.search(cs["label_display"]), cs["label_display"]


# 6 -------------------------------------------------------------------------
def test_every_grade_builds_and_serialises():
    for g in grade_kind.GRADES:
        text = json.dumps(slice_for(g))
        assert json.loads(text)["grade"] == g
    json.dumps(R.list_grades(con()))


# 7 -------------------------------------------------------------------------
def test_g2_drawer_fields_match_sql():
    c = con()
    for n in all_nodes(slice_for("2")):
        d = R.node_drawer(c, n["source_key"], "2")
        expect = {r[0] for r in c.execute(
            "SELECT DISTINCT field FROM node_fields WHERE node_id = ? "
            "AND TRIM(value) <> ''", (n["node_id"],))}
        assert {f["key"] for f in d["fields"]} == expect, n["node_id"]
        assert {f["key"] for f in d["fields"]}.isdisjoint(
            {f["key"] for f in d["fields_empty"]})
        json.dumps(d)


# 8 -------------------------------------------------------------------------
def test_shared_ccss_pairs_match_cross_stem_csv():
    c = con()
    path = ROOT / "docs" / "review" / "cross_stem_links_shared_ccss.csv"
    if not path.exists():
        raise Skip("no cross_stem_links_shared_ccss.csv")
    with open(path, newline="", encoding="utf-8") as fh:
        expect = {tuple(sorted((r["node_a"], r["node_b"]))) for r in csv.DictReader(fh)}
    assert len(expect) == 180, len(expect)
    got = set()
    for g in grade_kind.GRADES:
        for n in all_nodes(slice_for(g)):
            for b in n["badges"]:
                for p in b["partners"]:
                    for m in p["nodes"]:
                        got.add(tuple(sorted((n["node_id"], m["node_id"]))))
    assert got == expect, (len(got), len(expect), sorted(got ^ expect)[:5])


# 9 -------------------------------------------------------------------------
def test_period_hints():
    c = con()
    by_id = {r[0]: r[1] for r in c.execute("SELECT node_id, source_key FROM nodes")}
    h = R.period_hint(c, "WHO-0010", "2")
    assert h["value"] == 2.0 and h["basis"] == "grade_named", h
    h = R.period_hint(c, "TIM-0010", "2")
    assert h["value"] is None and h["basis"] == "ungraded_multi_grade", h
    h = R.period_hint(c, "COM-0012", "2")
    assert h["value"] == 1.0 and h["basis"] == "single_grade_node", h
    assert by_id["COM-0012"] == COM12
    for g in grade_kind.GRADES:
        for n in all_nodes(slice_for(g)):
            h = n["period_hint"]
            if h is None:
                continue
            assert set(h) == {"text", "value", "unit", "qualifier", "low", "high",
                              "basis", "n_estimates"}
            if h["unit"] in ("day", "lesson"):
                assert h["value"] is None, (g, n["node_id"], h)
            if h["basis"] == "ungraded_multi_grade":
                assert h["value"] is None


# 10 ------------------------------------------------------------------------
def test_build_compare():
    c = con()
    cmp_ = R.build_compare(c, [COM12, WHO10], "2")
    assert cmp_["shared_ccss"] == ["2.NBT.A.4"], cmp_["shared_ccss"]
    assert cmp_["shared_lessons"] == ["G2-M1-L35"], cmp_["shared_lessons"]
    assert [col["node_id"] for col in cmp_["columns"]] == ["COM-0012", "WHO-0010"]
    json.dumps(cmp_)


def test_compare_key_limits():
    c = con()
    keys = [n["source_key"] for n in all_nodes(slice_for("2"))][:5]
    for k in range(1, 5):                         # 1..4 are accepted
        assert len(R.build_compare(c, keys[:k], "2")["columns"]) == k
    for bad in (keys[:5], []):
        try:
            R.build_compare(c, bad, "2")
        except ValueError:
            pass
        else:
            raise AssertionError(f"{len(bad)} keys should raise ValueError")
    try:
        R.build_compare(c, [keys[0], keys[0]], "2")
    except ValueError:
        pass
    else:
        raise AssertionError("duplicates should raise ValueError")
    try:
        R.build_compare(c, [keys[0], "NOPE:0000", "ALSO:1111"], "2")
    except KeyError as exc:
        assert exc.args[0] == "NOPE:0000"
    else:
        raise AssertionError("unknown key should raise KeyError")


# every grade token ----------------------------------------------------------
def test_every_grade_token_builds_a_slice():
    c = con()
    for token in ("PK", "K", "1", "2", "3", "4", "5", "6", "7", "8", "A1",
                  "pk", "k", "g2", "G2", "Grade 2", "a1"):
        g = R.normalize_grade(c, token)
        sl = R.build_slice(c, token)
        assert sl["grade"] == g and sl["grade_label"] == R.GRADE_LABELS[g]
    for bad in ("9", "OUT", "", "x", None):
        try:
            R.normalize_grade(c, bad)
        except ValueError as exc:
            assert "unknown grade" in str(exc)
        else:
            raise AssertionError(bad)


# drawer variants -------------------------------------------------------------
def test_drawer_leaf_and_no_grade_nodes():
    c = con()
    leaf_key = c.execute("SELECT source_key FROM nodes WHERE node_id = 'COU-0024'").fetchone()[0]
    d = R.node_drawer(c, leaf_key, "2")
    assert d["state"] == "leaf" and d["requires_confirm"] is True and d["owed"] is False
    assert d["ruling"]["resolution"] == "leaf"
    assert [b["code"] for b in d["badges"]][0] == "leaf"
    json.dumps(d)
    # A no_grade_field node that is not in any grade.
    row = c.execute(
        "SELECT n.source_key, n.node_id FROM nodes n JOIN node_grade_ruling r USING(node_id) "
        "WHERE r.resolution = 'no_grade_field' AND n.node_id NOT IN (SELECT node_id FROM node_grade) "
        "LIMIT 1").fetchone()
    d = R.node_drawer(c, row[0], "2")
    assert d["state"] == "no_grade" and d["in_grade"] is False
    assert d["requires_confirm"] is True
    assert [b["code"] for b in d["badges"]][:1] == ["no_grade"]
    json.dumps(d)
    # Off-grade context node and unknown key.
    d = R.node_drawer(c, "WHO:fdbd77e5556b420d", "2")
    assert d["state"] == "off_grade" and d["requires_confirm"] is True
    assert all(b["code"] != "off_grade" for b in d["badges"])
    try:
        R.node_drawer(c, "NOPE:1", "2")
    except KeyError:
        pass
    else:
        raise AssertionError("unknown key should raise KeyError")


def test_drawer_g2_example_shape():
    c = con()
    d = R.node_drawer(c, WHO10, "2")
    assert d["domain"] == "Number Systems and Structures"
    assert d["cs"]["cs_id"] == "WHO:7046b922" and d["cs"]["goal_missing"] is False
    assert "\n" in d["cs"]["goal"]
    assert [s["node_id"] for s in d["strip"]] == ["WHO-0010", "WHO-0011", "WHO-0012", "WHO-0013"]
    assert [s["is_self"] for s in d["strip"]] == [True, False, False, False]
    assert d["ruling"]["raw_value"] == "1, 2, 3, 4"
    assert [g["grade"] for g in d["grade_states"]] == ["1", "2", "3", "4"]
    assert {s["code"] for s in d["standards"]} == {"1.NBT.B.3", "2.NBT.A.4", "4.NBT.A.2", "K.CC.C.7"}
    assert all(s["shared"] for s in d["standards"])
    assert d["state_codes"] and all(s["state"] for s in d["state_codes"])
    assert {l["lesson_id"] for l in d["lessons"]} >= {"G2-M1-L35", "G1-M5-L7"}
    l35 = [l for l in d["lessons"] if l["lesson_id"] == "G2-M1-L35"][0]
    assert [s["stem_id"] for s in l35["shared_with"]] == ["COM"]
    assert [p["stem_id"] for p in d["pairings"]] == ["COM", "EST"]
    assert d["pairings"] == [b for b in d["badges"] if b["code"] == "shared_code"][0]["partners"]


# overlay / facts -------------------------------------------------------------
def test_overlay_passes_through_on_real_data():
    c = con()
    ref = {"grade": "3", "sequence_id": 2, "sequence_title": "Grade 3 sequence",
           "module_id": 7, "module_title": "M1 Place value", "placement_id": 31}
    sl = R.build_slice(c, "2", overlay={WHO10: [ref]})
    node = [n for n in all_nodes(sl) if n["source_key"] == WHO10][0]
    assert node["placed_elsewhere"] == [ref]
    b = [b for b in node["badges"] if b["code"] == "placed_elsewhere"][0]
    assert b["label"] == "also G3" and b["detail"] == "Placed in G3 · M1 Place value"
    codes = [b["code"] for b in node["badges"]]
    assert codes == ["span", "placed_elsewhere", "shared_code"], codes
    d = R.node_drawer(c, WHO10, "2", overlay={WHO10: [ref]})
    assert d["placed_elsewhere"] == [ref]


def test_node_facts_predecessors_and_unknown_keys():
    c = con()
    facts = R.node_facts(c, "2", ["WHO:dc726b29e8f61f18", "NOPE:1"])
    assert list(facts) == ["WHO:dc726b29e8f61f18"]
    f = facts["WHO:dc726b29e8f61f18"]
    assert f["predecessor_keys"] == [WHO10] and f["predecessor_node_ids"] == ["WHO-0010"]
    assert f["state"] == "span" and f["owed"] and not f["requires_confirm"]
    assert len(R.node_facts(c, "2")) == 353
    json.dumps(facts)


def test_field_defs_and_helpers():
    c = con()
    defs = R.compare_field_defs(c)
    assert [d["key"] for d in defs][:12] == [k for k, _ in R.FIELD_ORDER]
    assert R.cs_id("WHO", "Compare and order numbers by using place value.") == "WHO:7046b922"
    assert R.display_label("Foo  bar (Jane - done)") == "Foo bar"
    # Must equal the DB's own latest ingest stamp, whatever build this is.
    assert R.ladders_last_read(c) == \
        c.execute("SELECT MAX(ts) FROM ingest_log").fetchone()[0]


# 15 ------------------------------------------------------------------------
def test_static_checks():
    src = [ROOT / "mh2" / "seq_read.py", ROOT / "mh2" / "grade_kind.py"]
    bad_tokens = ["mh2." + "coverage", "SEQ" + "_DB", "mh2" + "_seq"]
    for path in src:
        text = path.read_text(encoding="utf-8")
        for tok in bad_tokens:
            assert tok not in text, (path.name, tok)
    assert "mode=ro" in src[0].read_text(encoding="utf-8")


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
