"""
Phrasing families for scripts/report_period_estimates.parse_period_notes().

Pure functions, no fixtures, no database. Every string below is a real phrasing
from node_fields (field='additional_notes') unless a comment says otherwise.

Run with: python -m pytest tests/test_period_estimates.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.report_period_estimates import (  # noqa: E402
    grade_token, parse_line, parse_period_notes, usable,
)


def one(text, header_mode=False):
    rows = parse_line(text, header_mode=header_mode)
    assert len(rows) == 1, rows
    return rows[0]


def shape(e):
    return (e.grade, e.low, e.high, e.qualifier, e.unit)


# --- inline phrases ---------------------------------------------------------

def test_exact_single():
    assert shape(one("Likely 1 instructional period")) == (None, 1, 1, "exact", "period")


def test_exact_plural_and_possibly():
    assert shape(one("Likely 3 instructional periods")) == (None, 3, 3, "exact", "period")
    assert shape(one("Possibly 1 instructional period")) == (None, 1, 1, "exact", "period")


def test_range_hyphen_and_en_dash():
    assert shape(one("Likely 1-2 instructional periods")) == (None, 1, 2, "range", "period")
    assert shape(one("Likely 2–3 instructional periods")) == (None, 2, 3, "range", "period")


def test_range_or():
    assert shape(one("Likely 0 or 1 instructional periods")) == (None, 0, 1, "range", "period")


def test_open_ended():
    e = one("Likely 1 or more instructional periods")
    assert (e.low, e.high, e.qualifier) == (1, None, "range")
    assert "open_ended" in e.note and usable(e)
    assert one("Likely 1+ instructional periods possibly combined with previous node").high is None
    assert one("Likely at least 1 instructional period to introduce the commutative "
               "property; additional parts of instructional periods").low == 1


def test_parenthetical_detail_is_not_counted():
    # "(1 whole numbers, 1 decimals)" is a breakdown of the 2, not two more counts.
    assert shape(one("G4: Likely 2 instructional periods (1 whole numbers, 1 decimals)")) \
        == ("4", 2, 2, "exact", "period")


def test_combination_note_does_not_change_number():
    assert shape(one("Likely 1-2 instructional periods (potentially combined with other nodes)")) \
        == (None, 1, 2, "range", "period")


# --- part_of / embedded / multiple / not_fixed ------------------------------

def test_part_of_with_number_is_usable_upper_bound():
    e = one("G4: Likely part of 1 instructional period")
    assert shape(e) == ("4", None, 1, "part_of", "period") and usable(e)
    assert one("Likely part of an instructional period").high == 1
    assert one("Part of 1 instructional period, woven into other activities").qualifier == "part_of"


def test_part_of_other_is_embedded_and_unusable():
    e = one("G5: Likely part of other instructional periods (whole numbers, decimals)")
    assert shape(e) == ("5", None, None, "embedded", "period") and not usable(e)
    assert one("Embedded in other instructional periods").qualifier == "embedded"
    assert one("Likely parts of multiple instructional periods").qualifier == "embedded"


def test_multiple_and_not_fixed():
    assert one("Multiple instructional periods throughout 6+").qualifier == "multiple"
    e = one("Fluency is not taught in a fixed number of instructional periods; "
            "it should be practiced and maintained throughout the school year.")
    assert e.qualifier == "not_fixed" and not usable(e)
    assert one("Number of days depends on how irrational numbers are introduced").qualifier \
        == "not_fixed"


def test_earliest_cue_wins():
    assert one("Likely part of other instructional periods, could be 1–2 "
               "instructional periods").qualifier == "embedded"
    assert shape(one("Likely 1 instructional period, but can/should be embedded across "
                     "grade levels")) == (None, 1, 1, "exact", "period")


def test_group_total_is_not_a_per_node_number():
    e = one("Likely 2+ days for the first 6 nodes altogether (possibly split with 1 day "
            "for <, > and 1 day for ≤, ≥)")
    assert e.qualifier == "part_of" and "group_total" in e.note and not usable(e)
    assert not usable(one("Likely 1-2 days (for all 4 nodes)"))


# --- grade prefixes and suffixes --------------------------------------------

def test_grade_token_maps_to_grade_order():
    assert [grade_token(x) for x in ("PK", "GK", "K", "G1", "G8")] == ["PK", "K", "K", "1", "8"]
    assert grade_token("junk") is None


def test_grade_prefix_colon_dash_and_k():
    assert shape(one("G2: Likely 2 instructional periods")) == ("2", 2, 2, "exact", "period")
    assert shape(one("G1 – likely 10 instructional periods")) == ("1", 10, 10, "exact", "period")
    assert one("GK: Likely 5 instructional periods, with additional practice time needed").grade == "K"
    assert one("PK: Likely part of other instructional periods (one-to-one correspondence)").grade == "PK"


def test_grade_prefix_with_state_note():
    e = one("G1 (for FL and AR): Likely 1 instructional period")
    assert e.grade == "1" and e.note.startswith("state:")


def test_joint_prefix_emits_one_row_per_grade():
    rows = parse_line("PK/GK: Likely 4-5 instructional periods")
    assert [(r.grade, r.low, r.high) for r in rows] == [("PK", 4, 5), ("K", 4, 5)]


def test_grade_suffix():
    assert shape(one("Likely 1 instructional period in G5")) == ("5", 1, 1, "exact", "period")
    assert shape(one("Likely part of an instructional period in G6 (shared with subtraction)")) \
        == ("6", None, 1, "part_of", "period")


def test_two_grades_in_one_sentence():
    rows = parse_line("Likely 1 instructional period in G4 and 2 instructional periods in G5")
    assert [(r.grade, r.low, r.high) for r in rows] == [("4", 1, 1), ("5", 2, 2)]


def test_per_grade_keeps_grade_none():
    e = one("Likely 1-2 instructional periods per grade")
    assert e.grade is None and "per_grade" in e.note


# --- header-style (Instructional Period / value rows) -----------------------

def test_header_then_bare_count():
    rows = parse_period_notes(["Fluency Ideas", "Instructional Period", "1-2",
                               "Associated Concepts from Other Stems:", "Spatial Thinking"])
    assert [shape(r) for r in rows] == [(None, 1, 2, "range", "period")]
    assert "header_style" in rows[0].note


def test_header_then_grade_rows():
    rows = parse_period_notes(["Instructional Period", "GK: 1-2", "G1: 1-2"])
    assert [(r.grade, r.low, r.high) for r in rows] == [("K", 1, 2), ("1", 1, 2)]


def test_header_with_label_repeats():
    rows = parse_period_notes(["Instructional Period-Up to 5 Objects", "PK: 3", "GK: 3",
                               "Instructional Period-Up to 10 Objects", "PK: 3", "GK: 3"])
    assert len(rows) == 4 and all(r.qualifier == "exact" and r.low == 3 for r in rows)


def test_header_colon_then_inline_phrase():
    rows = parse_period_notes(["Instructional periods:", "Likely 2 periods with lots of "
                               "Fluency activities added in"])
    assert [shape(r) for r in rows] == [(None, 2, 2, "exact", "period")]


def test_header_stops_at_non_estimate_line():
    rows = parse_period_notes(["Instructional Periods:", "PK/GK: Likely 4-5 instructional periods",
                               "G1 and G2 begin to decompose based on place value – see Base Ten Stem"])
    assert sorted(r.grade for r in rows) == ["K", "PK"]


def test_header_without_value_is_unparsed():
    rows = parse_period_notes(["Instructional Period", "Associated Concepts from Other Stems:"])
    assert [r.qualifier for r in rows] == ["unparsed"]


def test_header_value_with_trailing_prose_and_paren():
    assert shape(one("2 (1 for arrangement, 1 for order)", header_mode=True)) \
        == (None, 2, 2, "exact", "period")
    assert one("1 or this might also live in Practice/Fluency", header_mode=True).low == 1
    assert shape(one("2-3?", header_mode=True)) == (None, 2, 3, "range", "period")


# --- other units (the second convention) ------------------------------------

def test_days():
    assert shape(one("1 day")) == (None, 1, 1, "exact", "day")
    assert shape(one("2 instructional days")) == (None, 2, 2, "exact", "day")
    assert shape(one("2-3 days")) == (None, 2, 3, "range", "day")


def test_lessons_including_fractions():
    assert shape(one("1/2 lesson – combine with next node")) == (None, 0.5, 0.5, "exact", "lesson")
    assert shape(one("1/2 to 1 lesson")) == (None, 0.5, 1, "range", "lesson")
    assert shape(one("1-2 lessons")) == (None, 1, 2, "range", "lesson")


def test_day_breakdown_lines_are_not_second_estimates():
    rows = parse_period_notes(["2 instructional days",
                               "1 day for converting terminating decimals to fractions",
                               "1 day for converting repeating decimals to fractions"])
    assert len(rows) == 1 and rows[0].low == 2


def test_different_grades_are_not_breakdowns():
    rows = parse_period_notes(["G1 – likely 10 instructional periods",
                               "G2 – likely 10 instructional periods"])
    assert [r.grade for r in rows] == ["1", "2"]


# --- things that must NOT parse ---------------------------------------------

def test_no_false_positives_on_ordinary_notes():
    for t in ("See EM2 G1 M2—Whiteboard Exchange: Commutative Property",
              "Show Me Attributes (see EM2 G1 M6 L3)",
              "Day 1- evaluate rational numbers raised to power",
              "5-Group Hands",
              "Fluency Ideas",
              "Consistent language: categories vs groups?",
              "By the time children are three years old, they may accurately name groups of 1, 2, or 3 objects."):
        assert parse_line(t) == [], t


def test_two_counts_in_one_clause_is_unparsed_not_summed():
    rows = parse_line("GK: Identify numerals: Likely 1 instructional period (new), "
                      "Writing numerals 1 instructional period (new)")
    assert [r.qualifier for r in rows] == ["unparsed"] and not usable(rows[0])


def test_unparseable_instructional_period_mention_is_reported():
    rows = parse_period_notes(["Could possibly be combined with previous node in latter "
                               "portion of a second instructional period per grade"])
    assert [r.qualifier for r in rows] == ["unparsed"]


def test_empty_and_none():
    assert parse_line("") == [] and parse_line(None) == []
    assert parse_period_notes([]) == []


if __name__ == "__main__":
    import traceback
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception:
                failed += 1
                print("FAIL", name)
                traceback.print_exc()
    sys.exit(1 if failed else 0)
