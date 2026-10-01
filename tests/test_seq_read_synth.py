"""
Synthetic-database tests for mh2/seq_read.py (contract 8.1 items 11-14 plus the
fallback kind_source path, goals, domain order and period-hint branches).

No real data needed: every database is built in memory from config.SCHEMA.

Run with: python3 tests/test_seq_read_synth.py
"""
from __future__ import annotations

import difflib
import json
import sqlite3
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from mh2 import seq_read as R  # noqa: E402

GRADE_ROWS = [("PK", 0), ("K", 1), ("1", 2), ("2", 3), ("3", 4), ("4", 5), ("OUT", 99)]


def make_db(nodes, grades=(), kinds=(), rulings=(), stems=None, notes=()):
    """In-memory DB. nodes = (node_id, stem_id, seq, source_key, text, cs, goal).
    grades = (node_id, grade); kinds = (node_id, grade, kind);
    rulings = (node_id, raw_value, resolution); notes = (node_id, ordinal, text)."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(config.SCHEMA.read_text())
    con.executemany("INSERT INTO grade_order(grade, ord) VALUES (?, ?)", GRADE_ROWS)
    stems = stems or {"AAA": ("Alpha", "Domain B"), "BBB": ("Beta", "Domain A")}
    for sid, (name, dom) in stems.items():
        con.execute("INSERT INTO stems(stem_id, name, domain) VALUES (?, ?, ?)",
                    (sid, name, dom))
    for n in nodes:
        con.execute("INSERT INTO nodes(node_id, stem_id, seq, source_key, node_text, "
                    "concept_skill, goal) VALUES (?, ?, ?, ?, ?, ?, ?)", n)
    con.executemany("INSERT INTO node_grade VALUES (?, ?)", grades)
    con.executemany("INSERT INTO node_grade_kind VALUES (?, ?, ?, 'test')", kinds)
    con.executemany("INSERT INTO node_grade_ruling(node_id, raw_value, resolution) "
                    "VALUES (?, ?, ?)", rulings)
    con.executemany("INSERT INTO node_fields(node_id, field, ordinal, value) "
                    "VALUES (?, 'additional_notes', ?, ?)", notes)
    con.execute("INSERT INTO ingest_log(run_id, ts) VALUES ('r', '2026-01-01 00:00:00')")
    return con


def small_db(with_kinds=True):
    """AAA: A1 A2 A3 in C/S 'Heading (Jo - done)'; BBB: B1 in another C/S."""
    nodes = [
        ("A-1", "AAA", 1, "AAA:h1", "Count to ten.", "Heading (Jo - done)", "Goal one"),
        ("A-2", "AAA", 2, "AAA:h2", "Count to twenty.", "Heading (Jo - done)", None),
        ("A-3", "AAA", 3, "AAA:h3", "Count to one hundred.", "Heading (Jo - done)", ""),
        ("B-1", "BBB", 1, "BBB:h4", "Add within five.", "Adding", None),
    ]
    grades = [("A-1", "K"), ("A-2", "K"), ("A-2", "1"), ("A-3", "1"), ("B-1", "1")]
    kinds = [("A-1", "K", "core"), ("A-2", "K", "span"), ("A-2", "1", "span"),
             ("A-3", "1", "unconfirmed")] if with_kinds else []
    rulings = [("A-1", "K", "ruled"), ("A-2", "K, 1", "ruled"),
               ("A-3", "1 (2 some states)", "ruled"), ("B-1", "1", "ruled")]
    return make_db(nodes, grades, kinds, rulings)


def flat(sl):
    return [n for ss in sl["super_stems"] for st in ss["stems"]
            for cs in st["concept_skills"] for n in cs["nodes"]]


# ---------------------------------------------------------------------------
# slice on a synthetic DB
# ---------------------------------------------------------------------------

def test_small_slice_shape_and_states():
    con = small_db()
    sl = R.build_slice(con, "1")
    json.dumps(sl)
    assert sl["kind_source"] == "grade_type" and sl["ladders_last_read"] == "2026-01-01 00:00:00"
    states = {n["node_id"]: n["state"] for n in flat(sl)}
    assert states == {"A-1": "off_grade", "A-2": "span", "A-3": "unconfirmed", "B-1": "unknown"}
    by = {n["node_id"]: n for n in flat(sl)}
    assert [b["code"] for b in by["A-2"]["badges"]] == ["span"]
    assert by["A-2"]["badges"][0]["detail"] == "Also K"
    assert by["A-3"]["badges"][0]["detail"] == "Grade ruling not final: 1 (2 some states)"
    assert by["B-1"]["badges"][0]["code"] == "kind_pending" and by["B-1"]["owed"] is True
    assert by["A-1"]["badges"] == [] and by["A-1"]["requires_confirm"] is True
    assert sl["counts"]["in_grade_nodes"] == 3 and sl["counts"]["context_nodes"] == 1
    assert sl["counts"]["chips"] == 4 and sl["counts"]["owed_nodes"] == 3


def test_domain_and_stem_ordering():
    con = small_db()
    sl = R.build_slice(con, "1")
    assert [s["domain"] for s in sl["super_stems"]] == ["Domain A", "Domain B"]
    con.execute("UPDATE stems SET domain = NULL WHERE stem_id = 'BBB'")
    sl = R.build_slice(con, "1")
    assert [s["domain"] for s in sl["super_stems"]] == ["Domain B", "(no domain)"]


def test_label_display_cs_id_and_goal():
    con = small_db()
    cs = R.build_slice(con, "1")["super_stems"][1]["stems"][0]["concept_skills"][0]
    assert cs["label"] == "Heading (Jo - done)" and cs["label_display"] == "Heading"
    assert cs["cs_id"] == R.cs_id("AAA", "Heading (Jo - done)")
    assert cs["goal"] == "Goal one" and cs["goal_missing"] is False
    other = R.build_slice(con, "1")["super_stems"][0]["stems"][0]["concept_skills"][0]
    assert other["goal"] is None and other["goal_missing"] is True


def test_two_distinct_goals_raise():
    con = small_db()
    con.execute("UPDATE nodes SET goal = 'Another' WHERE node_id = 'A-2'")
    try:
        R.build_slice(con, "1")
    except ValueError:
        pass
    else:
        raise AssertionError("two distinct goals should raise")


def test_fallback_path_reads_unknown():
    con = small_db(with_kinds=False)
    sl = R.build_slice(con, "K")
    assert sl["kind_source"] == "fallback"
    in_grade = [n for n in flat(sl) if n["in_grade"]]
    assert {n["state"] for n in in_grade} == {"unknown"}
    assert all(n["owed"] and not n["requires_confirm"] for n in in_grade)
    assert all(n["badges"][0]["code"] == "kind_pending" for n in in_grade)
    assert sl["counts"]["owed_nodes"] == sl["counts"]["in_grade_nodes"] == 2
    json.dumps(sl)


def test_leaf_and_no_grade_are_context_with_badges():
    nodes = [("A-1", "AAA", 1, "AAA:h1", "One.", "H", "G"),
             ("A-2", "AAA", 2, "AAA:h2", "Leafy.", "H", "G"),
             ("A-3", "AAA", 3, "AAA:h3", "Nogr.", "H", "G")]
    con = make_db(nodes, [("A-1", "2")], [("A-1", "2", "core")],
                  [("A-1", "2", "ruled"), ("A-2", "leaf text", "leaf"),
                   ("A-3", None, "no_grade_field")])
    by = {n["node_id"]: n for n in flat(R.build_slice(con, "2"))}
    assert by["A-2"]["state"] == "leaf" and by["A-2"]["badges"][0]["code"] == "leaf"
    assert by["A-2"]["badges"][0]["detail"] == "leaf text" and not by["A-2"]["in_grade"]
    assert by["A-3"]["state"] == "no_grade" and by["A-3"]["badges"][0]["label"] == "no grade"
    assert by["A-3"]["requires_confirm"] and not by["A-3"]["owed"]


def test_overlay_adds_badge_and_refs_on_synthetic():
    con = small_db()
    refs = [{"grade": "K", "sequence_id": 1, "sequence_title": "K seq", "module_id": 3,
             "module_title": "M1", "placement_id": 9},
            {"grade": "2", "sequence_id": 2, "sequence_title": "G2 seq", "module_id": 4,
             "module_title": "M2", "placement_id": 12}]
    sl = R.build_slice(con, "1", overlay={"AAA:h2": refs})
    node = [n for n in flat(sl) if n["node_id"] == "A-2"][0]
    assert [r["grade"] for r in node["placed_elsewhere"]] == ["K", "2"]
    b = [b for b in node["badges"] if b["code"] == "placed_elsewhere"][0]
    assert b["label"] == "also K, G2" and b["detail"] == "Placed in K · M1; G2 · M2"
    assert [b["code"] for b in node["badges"]] == ["span", "placed_elsewhere"]
    d = R.node_drawer(con, "AAA:h2", "1", overlay={"AAA:h2": refs})
    assert len(d["placed_elsewhere"]) == 2
    json.dumps(d)


def test_shared_code_badge_partners():
    con = small_db()
    con.executemany(
        "INSERT INTO node_standards_parsed(node_id, standard_code, state, relation, source_cell) "
        "VALUES (?, ?, NULL, 'aligned', 'x')",
        [("A-2", "K.CC.1"), ("B-1", "K.CC.1"), ("A-3", "K.CC.1")])
    sl = R.build_slice(con, "1")
    by = {n["node_id"]: n for n in flat(sl)}
    b = by["A-2"]["badges"][-1]
    assert b["code"] == "shared_code" and b["tier"] == "pairing"
    assert b["label"] == "pairs: BBB" and b["detail"] == "Shares CCSS codes with Beta (1)"
    assert b["partners"][0]["nodes"] == [{"source_key": "BBB:h4", "node_id": "B-1", "in_grade": True}]
    assert b["partners"][0]["codes"] == ["K.CC.1"]
    # A-3 shares the same code with A-2 but in the SAME stem: not a partner of A-3's stem.
    assert [p["stem_id"] for p in by["A-3"]["badges"][-1]["partners"]] == ["BBB"]


# ---------------------------------------------------------------------------
# period hint branches
# ---------------------------------------------------------------------------

def hint_db(notes, grades):
    nodes = [("A-1", "AAA", 1, "AAA:h1", "One.", "H", "G")]
    return make_db(nodes, [("A-1", g) for g in grades],
                   [("A-1", g, "core") for g in grades],
                   [("A-1", "x", "ruled")],
                   notes=[("A-1", i, t) for i, t in enumerate(notes)])


def test_period_hint_branches():
    con = hint_db(["Likely 2 instructional periods"], ["2"])
    h = R.period_hint(con, "A-1", "2")
    assert h["basis"] == "single_grade_node" and h["value"] == 2.0 and h["n_estimates"] == 1
    con = hint_db(["Likely 2 instructional periods"], ["2", "3"])
    h = R.period_hint(con, "A-1", "2")
    assert h["basis"] == "ungraded_multi_grade" and h["value"] is None
    con = hint_db(["G2: Likely 3 instructional periods", "G3: Likely 1 instructional period"], ["2", "3"])
    assert R.period_hint(con, "A-1", "2")["value"] == 3.0
    assert R.period_hint(con, "A-1", "3")["value"] == 1.0
    con = hint_db(["Likely 1 instructional period"], [])
    assert R.period_hint(con, "A-1", "2") is None          # no grades at all
    con = hint_db(["nothing about time here"], ["2"])
    assert R.period_hint(con, "A-1", "2") is None
    con = hint_db(["Likely 1 to 2 instructional periods"], ["2"])
    h = R.period_hint(con, "A-1", "2")
    assert h["qualifier"] == "range" and h["value"] is None and h["low"] == 1.0 and h["high"] == 2.0
    con = hint_db(["Likely 3 instructional days"], ["2"])
    h = R.period_hint(con, "A-1", "2")
    assert h["unit"] == "day" and h["value"] is None


# ---------------------------------------------------------------------------
# ordering badges (pure)
# ---------------------------------------------------------------------------

def fact(key, node_id, preds):
    return {"source_key": key, "node_id": node_id,
            "predecessor_keys": [p[0] for p in preds],
            "predecessor_node_ids": [p[1] for p in preds]}


def test_ordering_badges_inversion():
    facts = {"k2": fact("k2", "N-2", [("k1", "N-1")]), "k1": fact("k1", "N-1", [])}
    out = R.ordering_badges({"k2": (1, 1), "k1": (2, 3)}, facts)
    assert out["k1"] == []
    (b,) = out["k2"]
    assert b["code"] == "before_predecessor" and b["tier"] == "structural"
    assert b["label"] == "before N-1" and b["refs"] == ["k1"] and b["partners"] == []
    assert b["detail"] == "Placed before its ladder predecessor N-1 (M2 · slot 3). Warning only."
    json.dumps(out)


def test_ordering_badges_ties_and_order_are_quiet():
    facts = {"k2": fact("k2", "N-2", [("k1", "N-1")]), "k1": fact("k1", "N-1", [])}
    assert R.ordering_badges({"k2": (1, 1), "k1": (1, 1)}, facts)["k2"] == []   # co-placed
    assert R.ordering_badges({"k2": (2, 1), "k1": (1, 1)}, facts)["k2"] == []   # correct order
    assert R.ordering_badges({"k2": (1, 2), "k1": (1, 1)}, facts)["k2"] == []   # same module
    assert [b["code"] for b in R.ordering_badges({"k2": (1, 1), "k1": (1, 2)}, facts)["k2"]] \
        == ["before_predecessor"]                                               # same module, later slot


def test_ordering_badges_unplaced_predecessor():
    facts = {"k3": fact("k3", "N-3", [("k1", "N-1"), ("k2", "N-2")])}
    out = R.ordering_badges({"k3": (1, 1)}, facts)
    (b,) = out["k3"]
    assert b["code"] == "predecessor_unplaced" and b["tier"] == "info"
    assert b["label"] == "N-1, N-2 unplaced" and b["refs"] == ["k1", "k2"]


def test_ordering_badges_both_codes_one_each():
    facts = {"k3": fact("k3", "N-3", [("k1", "N-1"), ("k2", "N-2")]),
             "k2": fact("k2", "N-2", [])}
    out = R.ordering_badges({"k3": (1, 1), "k2": (2, 1)}, facts)
    assert [b["code"] for b in out["k3"]] == ["before_predecessor", "predecessor_unplaced"]
    assert out["k3"][0]["refs"] == ["k2"] and out["k3"][1]["refs"] == ["k1"]


# ---------------------------------------------------------------------------
# assemble_compare (pure)
# ---------------------------------------------------------------------------

def column(key, **over):
    base = {"source_key": key, "node_id": key.upper(), "node_text": "t", "stem_id": "S",
            "stem_name": "Stem " + key, "cs_label_display": "CS " + key, "goal": None,
            "grades": ["2"], "state": "core", "in_grade": True, "grade_raw": None,
            "period_hint_text": None, "ccss": [], "lessons": [], "state_codes": [], "fields": {}}
    base.update(over)
    return base


def test_assemble_compare_rows_shared_empty():
    a = column("a", goal="G", grade_raw="2", period_hint_text="P", ccss=["1.A", "2.B"],
               lessons=["L1"], state_codes=["CA.1"], fields={"strategies": ["s1", "s2"]})
    b = column("b", grades=["2", "3"], ccss=["2.B", "3.C"], lessons=["L1"],
               fields={"terminology": ["t"]})
    defs = [{"key": "strategies", "label": "Strategies"}, {"key": "terminology", "label": "Terminology"},
            {"key": "misconceptions", "label": "Misconceptions"}]
    out = R.assemble_compare([a, b], defs, "2")
    json.dumps(out)
    assert out["grade"] == "2" and out["columns"] == [a, b] and out["field_defs"] == defs
    assert [r["key"] for r in out["rows"]] == [
        "stem", "concept_skill", "goal", "grades", "state", "grade_raw", "period_hint",
        "ccss:1.A", "ccss:2.B", "ccss:3.C", "lesson:L1",
        "strategies", "terminology", "misconceptions", "state_codes"]
    rows = {r["key"]: r for r in out["rows"]}
    assert rows["stem"]["cells"] == [["Stem a"], ["Stem b"]]
    assert rows["goal"]["cells"] == [["G"], []] and rows["goal"]["empty"] is False
    assert rows["grades"]["cells"] == [["2"], ["2", "3"]]
    assert rows["grade_raw"]["cells"] == [["2"], []]
    assert rows["ccss:1.A"]["cells"] == [True, False] and rows["ccss:1.A"]["shared"] is False
    assert rows["ccss:2.B"]["cells"] == [True, True] and rows["ccss:2.B"]["shared"] is True
    assert rows["lesson:L1"]["shared"] is True and rows["lesson:L1"]["group"] == "lesson"
    assert rows["misconceptions"]["empty"] is True and rows["misconceptions"]["cells"] == [[], []]
    assert rows["state_codes"]["group"] == "state" and rows["state_codes"]["cells"] == [["CA.1"], []]
    assert out["shared_ccss"] == ["2.B"] and out["shared_lessons"] == ["L1"]
    assert all(r["kind"] == ("flag" if r["group"] in ("ccss", "lesson") else "text") for r in out["rows"])
    assert all(set(r) == {"key", "label", "group", "kind", "cells", "shared", "empty"} for r in out["rows"])


def test_assemble_compare_single_column_never_shared():
    out = R.assemble_compare([column("a", ccss=["1.A"])], [], "2")
    assert out["shared_ccss"] == [] and out["shared_lessons"] == []
    assert [r["empty"] for r in out["rows"] if r["key"] == "ccss:1.A"] == [False]


# ---------------------------------------------------------------------------
# suggest_successors
# ---------------------------------------------------------------------------

def ratio(a, b):
    return R.round_half_up(difflib.SequenceMatcher(None, a.casefold(), b.casefold()).ratio(), 3)


def sugg_db():
    seen_text = "Compare numbers by using place value."
    nodes = [
        # rule 1: same hash suffix, other stem (listed LAST in seq so ordering proves priority)
        ("Z-9", "BBB", 9, "BBB:samehash", seen_text, "Other", None),
        ("A-1", "AAA", 1, "AAA:aaa1", "Compare numbers by using place value and models.", "Heading", None),
        ("A-2", "AAA", 2, "AAA:aaa2", "Compare numbers using place value.", "Heading", None),
        ("A-3", "AAA", 3, "AAA:aaa3", "Completely unrelated sentence about fractions.", "Heading", None),
        ("A-4", "AAA", 4, "AAA:aaa4", "Compare numbers by using place value!", "Different heading", None),
        ("A-5", "AAA", 5, "AAA:aaa5", "Compare numbers by using place values.", "Heading", None),
    ]
    return make_db(nodes), seen_text


def test_suggest_rule_order_and_reasons():
    con, seen = sugg_db()
    out = R.suggest_successors(con, source_key_seen="AAA:samehash", node_text_seen=seen,
                               stem_id_seen="AAA", concept_skill_seen="Heading (Jo - done)",
                               exclude_keys=set())
    assert len(out) == 3
    assert [s["reason"] for s in out] == ["same_text_other_stem", "same_cs_similar", "same_cs_similar"]
    assert out[0]["source_key"] == "BBB:samehash" and out[0]["ratio"] == 1.0
    # Rule 2: ratio desc. A-5 is closer to the seen text than A-2 / A-1.
    assert [s["node_id"] for s in out[1:]] == ["A-5", "A-2"]
    assert out[1]["ratio"] == ratio(seen, "Compare numbers by using place values.")
    assert set(out[0]) == {"source_key", "node_id", "node_text", "stem_id", "stem_name",
                           "concept_skill_display", "reason", "ratio"}
    json.dumps(out)


def test_suggest_rule2_threshold_and_rule3_fallthrough():
    con, seen = sugg_db()
    # A-3 is under 0.6: never suggested by rule 2 (same heading) or 3.
    assert ratio(seen, "Completely unrelated sentence about fractions.") < 0.6
    out = R.suggest_successors(con, source_key_seen="AAA:nohash", node_text_seen=seen,
                               stem_id_seen="AAA", concept_skill_seen="Heading",
                               exclude_keys=set())
    assert "A-3" not in [s["node_id"] for s in out]
    # A-4 is in another heading: only rule 3 (>= 0.8) can pick it.
    assert ratio(seen, "Compare numbers by using place value!") >= 0.8
    out = R.suggest_successors(con, source_key_seen="AAA:nohash", node_text_seen=seen,
                               stem_id_seen="AAA", concept_skill_seen="Heading",
                               exclude_keys={"AAA:aaa5", "AAA:aaa2", "AAA:aaa1"})
    assert [(s["node_id"], s["reason"]) for s in out] == [("A-4", "same_stem_similar")]


def test_suggest_rule3_threshold_excludes_between_06_and_08():
    nodes = [("A-1", "AAA", 1, "AAA:a1", "Compare numbers by using place value.", "H1", None),
             ("A-2", "AAA", 2, "AAA:a2", "Compare numbers up to one hundred thousand.", "H2", None)]
    con = make_db(nodes)
    seen = "Compare numbers by using place value."
    mid = ratio(seen, nodes[1][4])
    assert 0.3 < mid < 0.8, mid
    out = R.suggest_successors(con, source_key_seen="AAA:gone", node_text_seen=seen,
                               stem_id_seen="AAA", concept_skill_seen="H2",
                               exclude_keys=set())
    # A-2 shares the heading but is below 0.6 or only counted by rule 2 if >= 0.6.
    expect = ["A-2"] if mid >= 0.6 else []
    assert [s["node_id"] for s in out if s["node_id"] == "A-2"] == expect
    out = R.suggest_successors(con, source_key_seen="AAA:gone", node_text_seen=seen,
                               stem_id_seen="AAA", concept_skill_seen="Elsewhere",
                               exclude_keys=set())
    assert [s["node_id"] for s in out] == ["A-1"]          # identical text, rule 3
    assert out[0]["ratio"] == 1.0


def test_suggest_exclude_keys_and_cap_and_dedupe():
    con, seen = sugg_db()
    every = {"BBB:samehash", "AAA:aaa1", "AAA:aaa2", "AAA:aaa3", "AAA:aaa4", "AAA:aaa5"}
    assert R.suggest_successors(con, source_key_seen="AAA:samehash", node_text_seen=seen,
                                stem_id_seen="AAA", concept_skill_seen="Heading",
                                exclude_keys=every) == []
    out = R.suggest_successors(con, source_key_seen="AAA:samehash", node_text_seen=seen,
                               stem_id_seen="AAA", concept_skill_seen="Heading",
                               exclude_keys={"BBB:samehash"})
    keys = [s["source_key"] for s in out]
    assert len(keys) == len(set(keys)) and len(keys) <= 3
    assert "BBB:samehash" not in keys
    # no stem info -> only rule 1 can fire
    out = R.suggest_successors(con, source_key_seen="AAA:samehash", node_text_seen=seen,
                               stem_id_seen=None, concept_skill_seen=None, exclude_keys=set())
    assert [s["reason"] for s in out] == ["same_text_other_stem"]


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------

def test_normalize_grade_tokens():
    con = small_db()
    cases = {"2": "2", "g2": "2", "G2": "2", "Grade 2": "2", "k": "K", "K": "K",
             "pk": "PK", "a1": "A1", "A1": "A1", "8": "8", " g 8 ": "8"}
    for tok, want in cases.items():
        assert R.normalize_grade(con, tok) == want, tok
    for bad in ("OUT", "9", "g", "", "algebra"):
        try:
            R.normalize_grade(con, bad)
        except ValueError as exc:
            assert str(exc) == f"unknown grade {bad!r}"
        else:
            raise AssertionError(bad)


def test_unknown_key_errors_and_facts_absent():
    con = small_db()
    for call in (lambda: R.node_drawer(con, "NOPE:1", "1"),
                 lambda: R.compare_column(con, "NOPE:1", "1"),
                 lambda: R.build_compare(con, ["AAA:h1", "NOPE:1"], "1")):
        try:
            call()
        except KeyError:
            pass
        else:
            raise AssertionError("expected KeyError")
    assert R.node_facts(con, "1", ["NOPE:1"]) == {}
    assert sorted(R.node_facts(con, "1")) == ["AAA:h1", "AAA:h2", "AAA:h3", "BBB:h4"]


def test_facts_predecessors_only_owed_lower_seq():
    con = small_db()
    f = R.node_facts(con, "1", ["AAA:h3"])["AAA:h3"]
    # A-1 is off_grade in grade 1 (not owed); A-2 is span (owed).
    assert f["predecessor_keys"] == ["AAA:h2"]
    f = R.node_facts(con, "K", ["AAA:h3"])["AAA:h3"]
    assert f["predecessor_keys"] == ["AAA:h1", "AAA:h2"] and f["state"] == "off_grade"
    assert f["requires_confirm"] is True and f["in_grade"] is False
    assert f["concept_skill"] == "Heading (Jo - done)" and f["concept_skill_display"] == "Heading"
    json.dumps(f)


def test_list_grades_on_synthetic():
    infos = R.list_grades(small_db())
    assert [i["grade"] for i in infos] == list(R.GRADE_LABELS)
    g1 = [i for i in infos if i["grade"] == "1"][0]
    assert (g1["in_grade_nodes"], g1["owed_nodes"], g1["chips"]) == (3, 3, 4)
    assert g1["label"] == "Grade 1" and g1["short"] == "G1" and g1["sequence"] is None
    json.dumps(infos)


def main() -> int:
    failed = 0
    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        try:
            fn()
            print(f"ok {name}")
        except Exception:                      # noqa: BLE001
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
