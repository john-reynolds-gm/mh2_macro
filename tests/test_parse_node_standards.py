"""
Node standards cell parser tests.

Every cell string below is real, taken from a ladder's 'Notes related to
Standards, Grade, or Leaf' row.

Run with: python -m pytest tests/ -q  (or: python tests/test_parse_node_standards.py)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mh2.parse_node_standards import classify, parse_cell, sibling_code


def codes(cell):
    return [c.code for c in parse_cell(cell).codes]


def relation_of(cell, code):
    return next(c.relation for c in parse_cell(cell).codes if c.code == code)


# --------------------------------------------------------- the §1 example

def test_representative_angles_cell():
    cell = ("CCSS.4.G.A.1  CA.4.G.A.1  FL.3.GR.1.1  TX.4.6A  IN.3.G.2 "
            "-partially tagged. Does not include rays in standard   SC.3.MGSR.3.2")
    parsed = parse_cell(cell)
    assert [c.code for c in parsed.codes] == [
        "4.G.A.1", "CA.4.G.A.1", "FL.3.GR.1.1", "TX.4.6A", "IN.3.G.2",
        "SC.3.MGSR.3.2"]
    note = next(c for c in parsed.codes if c.code == "IN.3.G.2")
    assert note.relation == "partial"
    assert "Does not include rays" in note.annotation
    # The note attaches to the code it followed, not to the cell or the next code.
    assert next(c for c in parsed.codes if c.code == "SC.3.MGSR.3.2").annotation is None


# ----------------------------------------------------- absence assertions

def test_absence_assertion_is_not_a_code_and_carries_no_code():
    parsed = parse_cell("K.CC.A.1\nNo CCSSM for ordinal numbers")
    assert [c.code for c in parsed.codes] == ["K.CC.A.1"]
    assert len(parsed.absences) == 1
    assert parsed.absences[0].framework == "CCSS"
    assert parsed.absences[0].note == "No CCSSM for ordinal numbers"


def test_absence_assertion_does_not_suppress_state_codes():
    """
    §1 and §3.5: the assertion scopes to CCSS only. The state codes in the same
    cell must survive it untouched — a node CCSS omits is exactly where a state
    standard is most valuable to surface.
    """
    parsed = parse_cell("No CCSS standard\nTX.K.2A\nFL.K.NSO.1.3\nAK.1.CC.2")
    assert [c.code for c in parsed.codes] == ["TX.K.2A", "FL.K.NSO.1.3", "AK.1.CC.2"]
    assert len(parsed.absences) == 1


def test_negation_next_to_a_code_is_not_an_absence_assertion():
    """'Does not include rays' is a note about IN.3.G.2, not a framework claim."""
    parsed = parse_cell("IN.3.G.2 -partially tagged. Does not include rays in standard")
    assert parsed.absences == []


def test_unknown_framework_is_flagged_for_a_human():
    parsed = parse_cell("No standard for this in any framework")
    assert parsed.absences and parsed.absences[0].framework == "unknown"


# ------------------------------------------------------------- relations

def test_exceeds():
    assert relation_of('TX.K.2A ("to at least 20")', "TX.K.2A") == "exceeds"
    assert relation_of("MN.5.3.5.5 requires by division", "MN.5.3.5.5") == "exceeds"


def test_partial():
    assert relation_of("4.NF.5 (just the equivalent fractions part)", "4.NF.5") \
        == "partial"
    assert relation_of("IN.4.CA.3 (does not include distributive property)",
                       "IN.4.CA.3") == "partial"


def test_scope_notes_leave_the_relation_aligned():
    """
    A scope qualifier says how far the STATE's version ranges. It says nothing
    about whether the node covers more or less, so the relation stays 'aligned'
    and the prose is kept for the leaf generator to read against the node text.
    """
    for cell, code in (("FL.3.NSO.1.3 (to 10,000)", "FL.3.NSO.1.3"),
                       ("TX.4.4C (2-digit x 2-digit)", "TX.4.4C"),
                       ("VA.3.NS.3.a.11 (denominators of 2, 3, 4, 5, 6, 8, and 10)",
                        "VA.3.NS.3.a.11")):
        parsed = parse_cell(cell)
        entry = next(c for c in parsed.codes if c.code == code)
        assert entry.relation == "aligned", cell
        assert entry.annotation, cell
    assert classify("(to 10,000)", has_code=True) == "scope"


def test_explicit_exceeds_beats_the_scope_pattern():
    """'to at least 20' contains 'to <number>' but is an exceeds claim."""
    assert classify('("to at least 20")', has_code=True) == "exceeds"


# --------------------------------------------------- recovered sibling codes

def test_teks_sibling_letter():
    assert sibling_code("TX.3.3A", "B (less than or equal to 1)") == "TX.3.3B"
    assert "TX.3.3B" in codes("TX.3.3A, B (less than or equal to 1)")


def test_sibling_replacing_the_final_segment():
    assert sibling_code("VA.3.NS.2.a", "and 2.b") == "VA.3.NS.2.b"
    assert sibling_code("VA.3.NS.1.a", "and 1.c") == "VA.3.NS.1.c"


def test_sibling_hanging_off_the_parent():
    assert sibling_code("MD.3.NOS.A.1", ". and 1.b") == "MD.3.NOS.A.1.b"
    assert sibling_code("5.NBT.A.3", "and 3a") == "5.NBT.A.3a"
    assert sibling_code("5.NBT.A.3", "and 3.b (decimals to thousandths)") \
        == "5.NBT.A.3.b"


def test_sibling_requires_the_parent_to_repeat():
    """A coincidental letter must not be read as a breakout."""
    assert sibling_code("VA.3.NS.2.a", "and 7.b") is None
    assert sibling_code("FL.3.NSO.1.3", "to 10,000") is None
    assert sibling_code("", "and 2.b") is None


def test_recovered_siblings_are_reported_as_recoveries():
    parsed = parse_cell("VA.3.NS.2.a and 2.b")
    assert [c.code for c in parsed.codes] == ["VA.3.NS.2.a", "VA.3.NS.2.b"]
    assert parsed.siblings == [("VA.3.NS.2.a", "and 2.b", "VA.3.NS.2.b")]


def test_space_delimited_teks_is_recovered():
    assert codes("TX 4.4E (up to 4 by 1, using arrays, area models, equations)") \
        == ["TX.4.4E"]
    assert codes("TX 4.4F (use strategies and algorithms)") == ["TX.4.4F"]


# ------------------------------------------------------- multi-node tagging

def test_the_same_code_on_two_nodes_is_not_deduped_away():
    """
    §4: one standard may legitimately sit on several nodes. parse_cell works on
    one cell, so this checks the within-cell case: a repeat inside one cell IS
    one row, but the code list keeps first-seen order and does not drop others.
    """
    parsed = parse_cell("TX.3.3A\nTX.3.3A\nFL.3.FR.1.1")
    assert [c.code for c in parsed.codes] == ["TX.3.3A", "FL.3.FR.1.1"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("all passed")
