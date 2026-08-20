"""Run with: python -m pytest tests/ -q   (or just: python tests/test_normalize.py)"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mh2.normalize import (
    expand_ranges, find_codes, grade_of, jurisdiction_of, normalize_grade,
    parse_code_cell,
)

CASES = [
    ("K.CC.A.1",                     [("K.CC.A.1", None)]),
    ("TX.K.2A",                      [("TX.K.2A", None)]),
    ("SD.PK.CD-4.g",                 [("SD.PK.CD-4.g", None)]),
    ("*2.MD.B.6",                    [("2.MD.B.6", None)]),
    ("CA.K.CC.3--added MF 5/29",     [("CA.K.CC.3", "added MF 5/29")]),
    ("TX.1.5A-This goes to 120",     [("TX.1.5A", "This goes to 120")]),
    ("K.CC.A.1\nCA.K.CC.4.a",        [("K.CC.A.1", None), ("CA.K.CC.4.a", None)]),
]

def test_codes():
    for cell, expected in CASES:
        got, _ = parse_code_cell(cell)
        assert got == expected, f"{cell!r} -> {got!r} != {expected!r}"

def test_jurisdiction():
    assert jurisdiction_of("TX.K.2A") == "TX"
    assert jurisdiction_of("K.CC.A.1") == "CCSS"

def test_grades():
    assert grade_of("TX.K.2A") == "K"
    assert grade_of("CA.2.NBT.7.1") == "2"
    assert normalize_grade("Algebra I") == "A1"
    assert normalize_grade("Grade 3") == "3"

def test_prose_is_not_a_code():
    got, unparsed = parse_code_cell("All orange standards were moved to the next row")
    assert got == [] and len(unparsed) == 1


# ---------------------------------------------------------------------------
# §1 tokenizer. These exist because the TEKS pattern fails SILENTLY: a tokenizer
# that requires the post-grade segment to begin with a letter returns zero TX
# codes and raises nothing, and TX is the state that most needs this to work.
# A green run only means something if these were written first.
# ---------------------------------------------------------------------------

def codes(text):
    """Just the normalized code strings, for readable assertions."""
    return [m.code for m in find_codes(text)]


def test_teks_post_grade_segment_starts_with_a_digit():
    """The silent-failure case. TX.4.6A is grade 4, segment '6A'."""
    got = find_codes("TX.4.6A")
    assert [m.code for m in got] == ["TX.4.6A"]
    assert got[0].state == "TX"
    assert got[0].grade == "4"


def test_teks_kindergarten():
    got = find_codes("TX.K.2A")
    assert [m.code for m in got] == ["TX.K.2A"]
    assert got[0].state == "TX" and got[0].grade == "K"


def test_ccss_prefix_is_optional_and_stripped():
    assert codes("CCSS.4.G.A.1") == ["4.G.A.1"]
    assert codes("4.G.A.1") == ["4.G.A.1"]
    assert find_codes("CCSS.4.G.A.1")[0].state is None


def test_two_letter_prefix_is_a_state_when_a_grade_follows():
    """MD, NC and OK all collide with plausible CCSS domain abbreviations."""
    got = find_codes("MD.1.GR.C.5")
    assert [m.code for m in got] == ["MD.1.GR.C.5"]
    assert got[0].state == "MD"


def test_two_letter_token_is_a_domain_when_a_grade_precedes():
    got = find_codes("4.MD.C.5")
    assert [m.code for m in got] == ["4.MD.C.5"]
    assert got[0].state is None


def test_three_codes_with_no_delimiter():
    assert codes("K.G.B.4 1.G.A.1 2.G.A.1") == ["K.G.B.4", "1.G.A.1", "2.G.A.1"]


def test_prekindergarten_with_three_segments():
    got = find_codes("MT.PK.4.14.h")
    assert [m.code for m in got] == ["MT.PK.4.14.h"]
    assert got[0].state == "MT" and got[0].grade == "PK"


def test_prekindergarten_letter_initial_segment():
    got = find_codes("MN.PK.M11.7")
    assert [m.code for m in got] == ["MN.PK.M11.7"]
    assert got[0].state == "MN"


def test_label_before_codes_is_not_a_code():
    assert codes("Clocks: MD.1.GR.C.5, TX.2.9G") == ["MD.1.GR.C.5", "TX.2.9G"]


def test_code_plus_relation_note():
    got = find_codes("IN.3.G.2 -partially tagged. Does not include rays")
    assert [m.code for m in got] == ["IN.3.G.2"]
    # The note is residual prose; the tokenizer must not swallow it into the code.
    assert got[0].end <= len("IN.3.G.2")


def test_the_representative_angles_cell():
    """§1's worked example, end to end."""
    cell = ("CCSS.4.G.A.1  CA.4.G.A.1  FL.3.GR.1.1  TX.4.6A  IN.3.G.2 "
            "-partially tagged. Does not include rays in standard   SC.3.MGSR.3.2")
    assert codes(cell) == ["4.G.A.1", "CA.4.G.A.1", "FL.3.GR.1.1", "TX.4.6A",
                           "IN.3.G.2", "SC.3.MGSR.3.2"]


def test_prose_yields_no_codes():
    """A decimal in running prose is not a standard."""
    assert codes("Round to 3.5 and compare") == []
    assert codes("No CCSSM for ordinal numbers") == []


# --- §3 Path A range notation ---------------------------------------------

def test_expand_ranges_ascii_hyphen():
    assert expand_ranges("1.NBT.B.2.a-b") == ["1.NBT.B.2.a", "1.NBT.B.2.b"]


def test_expand_ranges_en_dash():
    assert expand_ranges("3.MD.C.5.a–b") == ["3.MD.C.5.a", "3.MD.C.5.b"]


def test_expand_ranges_three_wide():
    assert expand_ranges("1.NBT.B.2.a–c") == \
        ["1.NBT.B.2.a", "1.NBT.B.2.b", "1.NBT.B.2.c"]


def test_expand_ranges_leaves_plain_codes_alone():
    assert expand_ranges("1.NBT.B.2") == ["1.NBT.B.2"]
    assert expand_ranges("K.CC.A.1") == ["K.CC.A.1"]


def test_expand_ranges_does_not_break_internal_hyphens():
    """SD.PK.CD-4.g and PA.M03.A-1.1.3 have hyphens inside a segment."""
    assert expand_ranges("SD.PK.CD-4.g") == ["SD.PK.CD-4.g"]
    assert expand_ranges("PA.M03.A-1.1.3") == ["PA.M03.A-1.1.3"]


def test_find_codes_expands_ranges_inline():
    assert codes("3.MD.C.5.a–b") == ["3.MD.C.5.a", "3.MD.C.5.b"]


# --- codes the §1 grammar does not describe, all found in the ladder cells ---

def test_state_code_with_no_grade_is_kept_with_grade_none():
    """
    Pennsylvania encodes the grade inside the domain (M03 = grade 3), so
    PA codes have no grade segment. §1's grammar requires one; dropping them
    silently loses seven real hand-authored tags.
    """
    for raw in ("PA.M03.A-T.1.1.4", "PA.M04.D-M.1.1.4", "PA.M03.D-M.1.3.3"):
        got = find_codes(raw)
        assert [m.code for m in got] == [raw], raw
        assert got[0].state == "PA" and got[0].grade is None, raw


def test_preschool_framework_code_with_no_grade():
    got = find_codes("VA.CD3.2e")
    assert [m.code for m in got] == ["VA.CD3.2e"]
    assert got[0].state == "VA" and got[0].grade is None


def test_missing_dot_after_state_prefix_is_repaired():
    """Source typos. The prefix is a real state and a grade digit follows."""
    assert codes("TX4.2H") == ["TX.4.2H"]
    assert codes("FL3.GR.1.1") == ["FL.3.GR.1.1"]


def test_unknown_two_letter_prefix_is_rejected_not_read_as_ccss():
    """
    'VS.2.NS.1.g' is a typo for VA. Reading it as the CCSS code '2.NS.1.g'
    files a state standard under the wrong jurisdiction, which is worse than
    dropping it — so it must yield nothing and be reported instead.
    """
    assert codes("VS.2.NS.1.g") == []
    assert codes("VA.2.CE.1.a, VS.2.NS.1.g round to nearest 10") == ["VA.2.CE.1.a"]


def test_teks_range_across_whole_codes():
    """TX.1.2E-1.2G means the E, F and G breakouts of TX.1.2."""
    assert expand_ranges("TX.1.2E-1.2G") == ["TX.1.2E", "TX.1.2F", "TX.1.2G"]
    assert codes("TX.1.2E-1.2G") == ["TX.1.2E", "TX.1.2F", "TX.1.2G"]


def test_ccss_parent_plus_subpart_range():
    """'2.NBT.A.1-1.b' is the parent plus its a and b sub-parts."""
    assert expand_ranges("2.NBT.A.1-1.b") == \
        ["2.NBT.A.1", "2.NBT.A.1.a", "2.NBT.A.1.b"]
    assert expand_ranges("1.NBT.B.2-2.c") == \
        ["1.NBT.B.2", "1.NBT.B.2.a", "1.NBT.B.2.b", "1.NBT.B.2.c"]


def test_wide_range_rules_do_not_fire_on_real_hyphenated_codes():
    """These hyphens are part of the code, not a range."""
    for code in ("SD.PK.CD-4.g", "SC.PK.MTE-5p", "AL.PK.MAT4aOP-4",
                 "PA.M03.A-T.1.1.4", "PA.M03.A-F.1.1.1"):
        assert expand_ranges(code) == [code], code


def test_annotation_dash_is_not_a_range():
    """'CA.K.CC.3--added MF 5/29' and 'TX.1.5A-This goes to 120' are notes."""
    assert codes("CA.K.CC.3--added MF 5/29") == ["CA.K.CC.3"]
    assert codes("TX.1.5A-This goes to 120") == ["TX.1.5A"]
    assert codes("SC.1.NR.2.2—note this standard") == ["SC.1.NR.2.2"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn(); print(f"  ok  {name}")
    print("all passed")
