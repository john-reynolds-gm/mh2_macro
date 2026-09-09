"""
render_static.py tests -- pinned to the brief's §8 acceptance criteria.

  #2  row_dict()'s key set is exactly the names in §2's contract table --
      set equality, not a count. The brief's own prose says "fourteen,"
      but its table (7 dataclass + 4 stem-attribution + 4 @property
      fields, same as DEFERRED.md §0) enumerates fifteen; render_static.
      ROW_DICT_KEYS documents the discrepancy and keeps all fifteen.
  #3  row_dict() runs over the full denominator without error, with
      color/flagged/reasons/tagged_in_sheet present and non-trivial.
  #4  rendered row count equals the denominator exactly.
  #5  the node lookup covers every node_id appearing in any tag -- set
      containment.
  #9  the file opens with no network access -- no http(s) references, no
      fetch/XHR, nothing but inline <script>/<style>.

Two synthetic-DB tests (fast, always run) plus one real-DB smoke test that
skips cleanly when data/build/mh2.db is absent, per config.DB.
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

from mh2 import coverage  # noqa: E402
from scripts import render_static  # noqa: E402

# Reuse test_coverage's fixture helpers rather than re-deriving them.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_coverage import (  # noqa: E402
    fresh_db, seed_grade_order, add_standard, add_node, tag,
)


def _sample_db():
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "FL.2.AR.1.1", "FL", "2", "no tags", "Florida", True)
    add_standard(con, "TX.1.3C", "TX", "1", "off-grade tag", "Texas", True)
    add_node(con, "ADD-0001", grades=("PK", "K"))
    tag(con, "ADD-0001", "TX.1.3C")
    add_standard(con, "2.OA.A.1", "CCSS", "2", "on-grade tag", "CCSS", True)
    add_node(con, "OA-0001", grades=("1", "2", "3"))
    tag(con, "OA-0001", "2.OA.A.1")
    return con


# ------------------------------------------------------------- row_dict()

def test_row_dict_key_set_matches_the_contract_table():
    con = _sample_db()
    rows = coverage.build_rows(con)
    assert rows
    for r in rows:
        assert set(render_static.row_dict(r).keys()) == render_static.ROW_DICT_KEYS


def test_row_dict_carries_the_properties_asdict_would_drop():
    con = _sample_db()
    rows = {r.code: r for r in coverage.build_rows(con)}
    red = render_static.row_dict(rows["FL.2.AR.1.1"])
    assert red["color"] == "Red"
    assert red["reasons"] == ["no tags in any ladder"]
    assert red["tagged_in_sheet"] is True

    yellow = render_static.row_dict(rows["TX.1.3C"])
    assert yellow["color"] == "Yellow"
    assert yellow["flagged"] is True
    assert yellow["tags"][0]["node_id"] == "ADD-0001"
    assert yellow["tags"][0]["grade_match"] == "off-grade"


# --------------------------------------------------------- node lookup

def test_node_lookup_covers_every_node_id_in_any_tag():
    con = _sample_db()
    rows = coverage.build_rows(con)
    referenced = {t.node_id for r in rows for t in r.tags}
    assert referenced == {"ADD-0001", "OA-0001"}

    lookup = render_static.build_node_lookup(con, rows)
    assert referenced <= set(lookup)          # set containment, not a count
    assert lookup["ADD-0001"] == "ADD-0001"    # add_node sets node_text = node_id


def test_node_lookup_is_empty_when_no_row_carries_a_tag():
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "FL.2.AR.1.1", "FL", "2", "no tags", "Florida", True)
    rows = coverage.build_rows(con)
    assert render_static.build_node_lookup(con, rows) == {}


# -------------------------------------------------------------- render()

def test_render_produces_one_row_per_denominator_standard(tmp_path):
    con = _sample_db()
    out = tmp_path / "coverage.html"
    rows = render_static.render(con, out)
    assert len(rows) == len(coverage.build_rows(con))

    html = out.read_text(encoding="utf-8")
    payload = json.loads(
        re.search(
            r'<script id="rows-data" type="application/json">(.*?)</script>',
            html, re.S).group(1))
    assert len(payload) == len(rows)
    assert {r["code"] for r in payload} == {r.code for r in rows}


def test_render_summary_buckets_are_mutually_exclusive_and_sum_to_total():
    con = _sample_db()
    rows = coverage.build_rows(con)
    s = render_static.summary_counts(rows)
    assert s["total"] == len(rows)
    assert (s["drafted_red"] + s["drafted_ok"] + s["undrafted"] + s["unattributed"]
            == s["total"])


def test_rendered_html_has_no_network_dependencies(tmp_path):
    """§8 #9 / Session D's D1: the file must still make zero network calls
    when opened as file://. Session D adds fetch() calls for the write UI,
    gated at runtime on `location.protocol !== 'file:'` (the same file
    serves both cases -- D1). So this no longer bans the substring "fetch("
    outright; it checks the gate instead -- no fetch()/XHR call is defined
    or reachable before that runtime check exists, and no external resource
    is ever loaded eagerly regardless of protocol."""
    con = _sample_db()
    out = tmp_path / "coverage.html"
    render_static.render(con, out)
    html = out.read_text(encoding="utf-8")
    assert "http://" not in html
    assert "https://" not in html
    assert re.search(r'<script[^>]+src=', html) is None
    assert re.search(r'<link[^>]+href=', html) is None
    assert "ONLINE = location.protocol" in html
    before_gate = html.split("ONLINE = location.protocol", 1)[0]
    assert "fetch(" not in before_gate
    assert "XMLHttpRequest" not in before_gate


# ------------------------------------------------------- real-DB smoke test

def test_row_dict_runs_over_the_full_real_denominator_without_error():
    """§8 #3: row_dict() over all 2,987 real rows, color/flagged/reasons/
    tagged_in_sheet present and non-trivial. Skips cleanly if the built
    database isn't present in this environment."""
    if not config.DB.exists():
        return
    con = sqlite3.connect(f"file:{config.DB}?mode=ro", uri=True)
    rows = coverage.build_rows(con, band=None)
    assert len(rows) == 2987          # §8 #4: the pinned denominator figure
    data = [render_static.row_dict(r) for r in rows]
    assert len(data) == len(rows)
    for d in data:
        assert set(d.keys()) == render_static.ROW_DICT_KEYS
    assert any(d["color"] == "Green" for d in data)
    assert any(d["color"] == "Yellow" for d in data)
    assert any(d["color"] == "Red" for d in data)
    assert any(d["flagged"] for d in data)
    assert any(d["tagged_in_sheet"] for d in data)
    assert any(d["reasons"] for d in data)

    referenced = {t["node_id"] for d in data for t in d["tags"]}
    lookup = render_static.build_node_lookup(con, rows)
    assert referenced <= set(lookup)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            import inspect
            if "tmp_path" in inspect.signature(fn).parameters:
                import tempfile
                with tempfile.TemporaryDirectory() as d:
                    fn(Path(d))
            else:
                fn()
            print(f"  ok  {name}")
    print("all passed")
