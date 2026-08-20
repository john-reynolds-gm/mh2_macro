"""
Ladder parser tests.

Every string below is a real paragraph copied out of a source .docx, not an
invented case. The three that matter:

  * a heading whose paragraph carries a second 'D+J:' line — this is the one
    that silently returned NO heading at all and cost the Comparing and
    Ordering ladder four of its seven concept/skills;
  * a heading whose label is only a drafting marker '(Megan—DONE)';
  * a heading with no label, where the label is the next paragraph.

Run with: python -m pytest tests/ -q   (or: python tests/test_ingest_ladders.py)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mh2.ingest_ladders import parse_concept_heading, read_docx

LADDERS = Path(__file__).resolve().parent.parent / "data" / "source" / "ladders"

# Concept/skill and node counts per ladder, from the corrected parser. Pinned so
# a regression in the heading or merged-cell handling is loud rather than a
# quietly smaller number.
EXPECTED = {
    "MH2_PK5_Measurement and Data_Angles.docx": (7, 12),
    "MH2_PK5_Measurement and Data_Time.docx": (9, 14),
    "MH2_PK5_NumberSystemsandStructures_ComparingandOrdering_LessonLadder.docx": (7, 14),
    "MH2_PK5_NumberSystemsandStructures_Estimating.docx": (6, 9),
    "MH2_PK5_NumberSystemsandStructures_Fractions.docx": (5, 19),
    "MH2_PK5_NumberSystemsandStructures_Whole Numbers and Base Ten Structure.docx": (6, 19),
    "MH2_PK5_Numbers Systems and Structures_Counting.docx": (14, 24),
    "MH2_PK5_Numbers Systems and Structures_Subitization.docx": (2, 2),
    "MH2_PK5_OperationsAndEquations_MultiplicationAndDivisionWholeNumbers_LessonLadder.docx": (9, 33),
}


def test_multiline_heading_takes_first_line_only():
    """The bug that cost four concept/skills: '$' cannot match past a newline."""
    got = parse_concept_heading(
        "Concept/Skill: Identify and use ordinal numbers.\n"
        "D+J: First-Second Ordinal Counter")
    assert got == "Identify and use ordinal numbers.", got


def test_multiline_heading_with_trailing_space_before_newline():
    got = parse_concept_heading(
        "Concept/Skill: Compare small sets by matching. \nD+J: Matching Comparer")
    assert got == "Compare small sets by matching.", got


def test_comment_anchors_stripped_before_matching():
    """Real headings are split by w:commentRangeStart runs on the markdown path."""
    assert parse_concept_heading(
        "Concept[^c3]/Skill[^c4]: Compare and order fractions."
    ) == "Compare and order fractions."
    assert parse_concept_heading(
        "C[^c63][^c64][^c65]oncept/Skill: Round numbers by using place value."
    ) == "Round numbers by using place value."


def test_author_status_marker_alone_reads_as_no_label():
    """Angles has two of these; the real label is the next paragraph."""
    for raw in ("Concept/Skill: (Megan—DONE)",
                "Concept/Skill: (Stella – DONE)",
                "Concept/Skill: (Katie)",
                "Concept/Skill: (Stella- DONE)"):
        assert parse_concept_heading(raw) == "", raw


def test_author_status_marker_at_end_of_a_real_label_is_kept():
    """
    Only a label that is NOTHING BUT a marker counts as empty. Stripping the
    marker off a real label would change the text that feeds source_key, and
    every affected node would look new on the next ingest.
    """
    raw = ("Concept/Skill: Estimate a number of objects or a measurement "
           "by using benchmarks. (Megan-DONE)")
    assert parse_concept_heading(raw) == (
        "Estimate a number of objects or a measurement by using benchmarks. "
        "(Megan-DONE)")


def test_empty_label_reads_as_no_label():
    assert parse_concept_heading("Concept/Skill:") == ""
    assert parse_concept_heading("Concept/Skill:   ") == ""


def test_plain_single_line_heading_unchanged():
    assert parse_concept_heading(
        "Concept/Skill: Measure and draw angles by using a protractor"
    ) == "Measure and draw angles by using a protractor"
    assert parse_concept_heading("Concept/Skills: Compare and order fractions.") \
        == "Compare and order fractions."


def test_non_heading_returns_none():
    assert parse_concept_heading("Make and describe turns)") is None
    assert parse_concept_heading("PROGRESSION") is None
    assert parse_concept_heading("") is None
    assert parse_concept_heading(
        "*GLOBAL NOTE ABOUT DENOMINATORS: Focus on denominators") is None


def test_markdown_requires_hash_prefix():
    """On the markdown path a bare line must not be mistaken for a heading."""
    assert parse_concept_heading(
        "### Concept/Skill: Compare and order fractions.", require_hash=True
    ) == "Compare and order fractions."
    assert parse_concept_heading(
        "Concept/Skill: Compare and order fractions.", require_hash=True) is None


# --------------------------------------------------------------- doc-level

def test_continuation_heading_is_absorbed():
    """Angles: 'Concept/Skill: (Megan—DONE)' then 'Make and describe turns)'."""
    nodes = read_docx(LADDERS / "MH2_PK5_Measurement and Data_Angles.docx", "Angles")
    labels = [n["concept_skill"] for n in nodes]
    assert "Make and describe turns)" in labels
    assert "Compare corners and identify square corners" in labels
    assert None not in labels and "" not in labels


def test_consolidated_concept_skills_are_split_by_bullet():
    """
    Time has one 'Concept/Skill:' heading with two bullets under it — 'Read and
    write moments to the nearest 5 minutes' and 'Use a.m. and p.m.' — sharing a
    single table with two node columns. The author consolidated two single-node
    concept/skills to save space. Absorbing only the first bullet loses the
    second concept/skill and orphans its node under the wrong label.
    """
    nodes = read_docx(LADDERS / "MH2_PK5_Measurement and Data_Time.docx", "Time")
    by_label = {n["concept_skill"]: n["node_text"] for n in nodes}
    assert by_label["Read and write moments to the nearest 5 minutes"] == \
        "Tell and write time to the nearest 5 minutes"
    assert by_label["Use a.m. and p.m."] == "Classify events by using a.m. or p.m."


def test_single_continuation_covers_all_its_nodes():
    """
    The opposite shape: one continuation label over a table with several node
    columns. Whole Numbers has a 4-node and a 5-node instance. These must NOT be
    split — only a bullet list matching the node count is a consolidation.
    """
    nodes = read_docx(
        LADDERS / "MH2_PK5_NumberSystemsandStructures_Whole Numbers and "
                  "Base Ten Structure.docx", "Whole Numbers")
    counts = {}
    for n in nodes:
        counts[n["concept_skill"]] = counts.get(n["concept_skill"], 0) + 1
    assert counts["Use place value to read and write numbers."] == 4
    assert counts["Recognize and describe the relationships between place value units."] == 5


def test_merged_node_columns_are_not_duplicate_nodes():
    """
    Counting has three node-name cells merged across two grid columns each.
    python-docx reports them twice; taken at face value that is 27 nodes with
    three identical pairs, which then collide on source_key and merge anyway.
    """
    nodes = read_docx(
        LADDERS / "MH2_PK5_Numbers Systems and Structures_Counting.docx", "Counting")
    texts = [n["node_text"] for n in nodes]
    assert len(texts) == len(set(texts)), "merged cells read as duplicate nodes"
    assert len(nodes) == 24


def test_per_ladder_counts():
    for name, (n_cs, n_nodes) in EXPECTED.items():
        nodes = read_docx(LADDERS / name, name)
        labels = []
        for n in nodes:
            if n["concept_skill"] not in labels:
                labels.append(n["concept_skill"])
        assert len(labels) == n_cs, f"{name}: {len(labels)} concept/skills, want {n_cs}"
        assert len(nodes) == n_nodes, f"{name}: {len(nodes)} nodes, want {n_nodes}"


def test_totals_match_the_spec_cell_count():
    """§0 and §2 both state 143 filled standards cells across the 9 ladders."""
    filled = 0
    for name in EXPECTED:
        for n in read_docx(LADDERS / name, name):
            if (n.get("standards_notes") or "").strip():
                filled += 1
    assert filled == 143, filled


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("all passed")
