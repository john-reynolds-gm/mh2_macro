"""
coverage.py tests — the rev 4 handoff's rollup rules (§3), each pinned to the
scenario in the handoff that motivated it:

  containment, not equality      TX.1.3C-style: node grade range must CONTAIN
                                  the standard's grade, not equal it.
  is_leaf wins                   a leaf node's grade_match is 'n/a - leaf'
                                  regardless of any node_grade rows.
  color/flags are independent   a Green row can carry a flagged tag; an
                                  off-grade tag never downgrades a row that
                                  also has an on-grade or leaf tag.
  one alias resolution           a denominator code reachable only through
                                  standard_alias still rolls up Green, same
                                  as an exact match -- one number, not two.
  CCSS heading predicate          only leaves (4+ segments, digit in the
                                  4th) enter the CCSS tab; headings don't.
  reasons are a list              a Yellow row's `reasons` names every tag
                                  that failed to qualify, not a fixed string.

Run with: python -m pytest tests/ -q   (or: python tests/test_coverage.py)
"""
import csv
import dataclasses
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

from mh2 import coverage  # noqa: E402
from mh2.normalize import alias_key  # noqa: E402


def fresh_db():
    con = sqlite3.connect(":memory:")
    con.executescript(config.SCHEMA.read_text())
    return con


def seed_grade_order(con):
    rows = [("PK", 0, "PK5"), ("K", 1, "PK5"), ("1", 2, "PK5"), ("2", 3, "PK5"),
            ("3", 4, "PK5"), ("4", 5, "PK5"), ("5", 6, "PK5"),
            ("6", 7, "6_9"), ("7", 8, "6_9"), ("8", 9, "6_9"), ("A1", 10, "6_9"),
            ("HS", 14, None)]
    con.executemany("INSERT INTO grade_order (grade, ord, band) VALUES (?,?,?)", rows)


def add_standard(con, code, jurisdiction, grade, text, sheet, tagged_to_stem):
    con.execute(
        "INSERT INTO standards (standard_id, jurisdiction, grade, text, source)"
        " VALUES (?,?,?,?,?)", (code, jurisdiction, grade, text, f"tagging:{sheet}"))
    con.execute(
        "INSERT INTO standard_tag_status (standard_code, sheet, tagged_to_stem)"
        " VALUES (?,?,?)", (code, sheet, 1 if tagged_to_stem else 0))


def add_node(con, node_id, grades=(), is_leaf=False, resolution="ruled"):
    con.execute(
        "INSERT INTO nodes (node_id, source_key, node_text) VALUES (?,?,?)",
        (node_id, node_id, node_id))
    con.execute(
        "INSERT INTO node_grade_ruling (node_id, resolution, is_leaf)"
        " VALUES (?,?,?)", (node_id, resolution, 1 if is_leaf else 0))
    con.executemany(
        "INSERT INTO node_grade (node_id, grade) VALUES (?,?)",
        [(node_id, g) for g in grades])


def tag(con, node_id, standard_code):
    con.execute(
        "INSERT INTO node_standards (node_id, standard_id) VALUES (?,?)",
        (node_id, standard_code))


# --------------------------------------------------------------- CCSS heading

def test_ccss_heading_predicate_excludes_headings_keeps_leaves():
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "K.CC.A.1", "CCSS", "K", "count to 100", "CCSS", False)
    add_standard(con, "K.CC.A", "CCSS", "K", "heading", "CCSS", False)
    add_standard(con, "K.CC.B.4.a", "CCSS", "K", "leaf with subpart", "CCSS", False)
    rows = coverage.build_rows(con)
    codes = {r.code for r in rows}
    assert "K.CC.A.1" in codes
    assert "K.CC.B.4.a" in codes
    assert "K.CC.A" not in codes


def test_california_all_is_not_a_tab():
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "CA.K.CC.1", "CA", "K", "text", "California-All", False)
    rows = coverage.build_rows(con)
    assert rows == []


# --------------------------------------------------------------------- band

def test_band_null_is_not_coalesced():
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "TX.9.1A", "TX", "HS", "high school text", "Texas", False)
    rows = coverage.build_rows(con)
    assert rows[0].band is None
    pk5 = coverage.build_rows(con, band="PK5")
    assert pk5 == []


# ------------------------------------------------------------------- rollup

def test_red_row_has_no_tags_and_names_itself_in_reasons():
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "FL.2.AR.1.1", "FL", "2", "text", "Florida", True)
    row = coverage.build_rows(con)[0]
    assert row.color == "Red"
    assert row.reasons == ["no tags in any ladder"]
    assert row.flagged is False


def test_off_grade_only_is_yellow_containment_not_equality():
    """
    TX.1.3C, tagged only to a node whose declared grades are PK, K. Grade 1
    is outside that range -> off-grade -> Yellow, per the handoff's own
    worked example.
    """
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "TX.1.3C", "TX", "1", "compose 10 with two addends",
                "Texas", True)
    add_node(con, "ADD-0001", grades=("PK", "K"))
    tag(con, "ADD-0001", "TX.1.3C")
    row = coverage.build_rows(con)[0]
    assert row.color == "Yellow"
    assert row.flagged is True
    assert row.reasons == ["ADD-0001: off-grade (node declares PK, K; standard is grade 1)"]


def test_on_grade_tag_is_green_containment_within_a_span():
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "2.OA.A.1", "CCSS", "2", "text", "CCSS", True)
    add_node(con, "OA-0001", grades=("1", "2", "3"))
    tag(con, "OA-0001", "2.OA.A.1")
    row = coverage.build_rows(con)[0]
    assert row.color == "Green"
    assert row.flagged is False


def test_leaf_node_is_green_via_na_leaf_regardless_of_grade():
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "WI.PK.EL.5", "WI", "PK", "text",
                "Other State Standards Gaps", True)
    add_node(con, "MSC-0001", grades=("6",), is_leaf=True, resolution="leaf")
    tag(con, "MSC-0001", "WI.PK.EL.5")
    row = coverage.build_rows(con)[0]
    assert row.color == "Green"
    assert row.flagged is False
    assert row.tags[0].grade_match == "n/a - leaf"


def test_unresolved_grade_ruling_flags_but_does_not_by_itself_turn_red():
    """A node with no grade_ruling row at all -> unresolved, which flags but
    still counts as a tag, so a lone unresolved tag makes the row Yellow,
    not Red."""
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "NE.K.N.2.a", "NE", "K", "text",
                "Other State Standards Gaps", False)
    con.execute("INSERT INTO nodes (node_id, source_key, node_text) VALUES"
               " ('CNT-0099','CNT-0099','CNT-0099')")
    tag(con, "CNT-0099", "NE.K.N.2.a")
    row = coverage.build_rows(con)[0]
    assert row.tags[0].grade_match == "unresolved"
    assert row.color == "Yellow"
    assert row.flagged is True


def test_no_grade_field_ruling_with_zero_node_grade_rows_is_also_unresolved():
    """The OTHER path to 'unresolved', and the one the live DB actually
    takes: a node_grade_ruling row exists (resolution='no_grade_field',
    is_leaf=0) so the is_leaf lookup succeeds, but the node has zero
    node_grade rows, so containment has no range to test. All 25 unresolved
    pairs across both bands come from this branch, not the missing-ruling-row
    branch above."""
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "TX.1.1A", "TX", "1", "text", "Texas", False)
    add_node(con, "EQ-0002", grades=(), resolution="no_grade_field")
    tag(con, "EQ-0002", "TX.1.1A")
    row = coverage.build_rows(con)[0]
    assert row.tags[0].grade_match == "unresolved"
    assert row.flagged is True
    assert row.color == "Yellow"


# --------------------------------------------- color/flags are independent

def test_off_grade_tag_never_downgrades_a_row_that_also_has_on_grade():
    """
    The handoff's own example: a standard well covered at G2 but ALSO
    tagged on a node reading grades 4, 5 (off-grade) -> Green with a flag,
    never Yellow. This is the argument with writers §3 was written to avoid.
    """
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "2.NBT.A.1", "CCSS", "2", "text", "CCSS", True)
    add_node(con, "NBT-0001", grades=("2",))
    add_node(con, "NBT-0002", grades=("4", "5"))
    tag(con, "NBT-0001", "2.NBT.A.1")
    tag(con, "NBT-0002", "2.NBT.A.1")
    row = coverage.build_rows(con)[0]
    assert row.color == "Green"
    assert row.flagged is True
    assert row.reasons == []          # Green rows carry no rollup reason


# ------------------------------------------------------------- one alias, once

def test_alias_recovered_code_rolls_up_green_same_as_an_exact_match():
    """
    A ladder-written code that only reaches the denominator standard through
    standard_alias must still land Green -- and via a SINGLE resolution, so
    the tier is reported as non-exact rather than silently doubling as both
    an exact and an alias hit.
    """
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "3.NF.A.2.a", "CCSS", "3", "text", "CCSS", True)
    add_node(con, "FRA-0001", grades=("3",))
    # Ladder wrote it without the final dot -- a punctuation-only variant.
    tag(con, "FRA-0001", "3.NF.A2a")
    con.execute(
        "INSERT INTO standard_alias (alias_key, tier, standard_id) VALUES (?,?,?)",
        (alias_key("3.NF.A.2.a", "punct"), "punct", "3.NF.A.2.a"))
    row = coverage.build_rows(con)[0]
    assert row.color == "Green"
    assert len(row.tags) == 1
    assert row.tags[0].tier == "punct"
    assert row.tags[0].raw_code == "3.NF.A2a"


def test_exact_and_alias_hits_on_the_same_standard_both_survive():
    """Two DIFFERENT raw ladder spellings resolving to the same standard are
    two distinct tags, not deduplicated -- one exact, one alias."""
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "K.CC.B.4", "CCSS", "K", "text", "CCSS", True)
    add_node(con, "CNT-0001", grades=("K",))
    add_node(con, "CNT-0002", grades=("K",))
    tag(con, "CNT-0001", "K.CC.B.4")          # exact
    tag(con, "CNT-0002", "K.CC.B4")           # punct variant of the same code
    con.execute(
        "INSERT INTO standard_alias (alias_key, tier, standard_id) VALUES (?,?,?)",
        (alias_key("K.CC.B.4", "punct"), "punct", "K.CC.B.4"))
    row = coverage.build_rows(con)[0]
    tiers = {t.tier for t in row.tags}
    assert tiers == {"exact", "punct"}
    assert row.color == "Green"


# ---------------------------------------------------------------- claim join

def test_claim_join_reads_tagged_to_stem_yes_and_blank():
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "FL.1.AR.1.1", "FL", "1", "text", "Florida", True)
    add_standard(con, "FL.1.AR.1.2", "FL", "1", "text", "Florida", False)
    rows = {r.code: r for r in coverage.build_rows(con)}
    assert rows["FL.1.AR.1.1"].claim == "Yes"
    assert rows["FL.1.AR.1.1"].tagged_in_sheet is True
    assert rows["FL.1.AR.1.2"].claim == "blank"
    assert rows["FL.1.AR.1.2"].tagged_in_sheet is False


# ------------------------------------------------------------------ node_detail

def _add_bare_stem_and_node(con, stem_id, stem_name, node_id, goal=None):
    con.execute("INSERT INTO stems (stem_id, name) VALUES (?,?)", (stem_id, stem_name))
    con.execute(
        "INSERT INTO nodes (node_id, stem_id, source_key, node_text, goal)"
        " VALUES (?,?,?,?,?)",
        (node_id, stem_id, node_id, "text", goal))


def test_node_detail_collapses_whitespace_including_goal_newlines():
    """Goal is the important case -- 135 of 353 live rows carry an embedded
    newline, against 1 for stems.name."""
    con = fresh_db()
    seed_grade_order(con)
    _add_bare_stem_and_node(
        con, "WHO", "Whole Numbers and \nBase Ten Structure", "WHO-0001",
        "Represent numbers\nin  many ways")
    detail = coverage.node_detail(con, ["WHO-0001"])["WHO-0001"]
    assert detail.stem_name == "Whole Numbers and Base Ten Structure"
    assert detail.goal == "Represent numbers in many ways"


def test_node_detail_null_goal_is_empty_not_missing():
    con = fresh_db()
    seed_grade_order(con)
    _add_bare_stem_and_node(con, "WHO", "Whole Numbers", "WHO-0002")
    result = coverage.node_detail(con, ["WHO-0002"])
    assert "WHO-0002" in result
    assert result["WHO-0002"].goal == ""


def test_node_detail_zero_node_fields_rows_returns_empty_block_list():
    con = fresh_db()
    seed_grade_order(con)
    _add_bare_stem_and_node(con, "WHO", "Whole Numbers", "WHO-0003")
    detail = coverage.node_detail(con, ["WHO-0003"])["WHO-0003"]
    assert detail.fields == {}


def test_node_detail_field_blocks_group_by_field_and_order_by_ordinal():
    con = fresh_db()
    seed_grade_order(con)
    _add_bare_stem_and_node(con, "WHO", "Whole Numbers", "WHO-0004")
    con.executemany(
        "INSERT INTO node_fields (node_id, field, ordinal, value) VALUES (?,?,?,?)",
        [("WHO-0004", "misconceptions", 1, "second"),
         ("WHO-0004", "misconceptions", 0, "first"),
         ("WHO-0004", "terminology", 0, "only")])
    detail = coverage.node_detail(con, ["WHO-0004"])["WHO-0004"]
    assert detail.fields["misconceptions"] == ["first", "second"]
    assert detail.fields["terminology"] == ["only"]


# --------------------------------------------------------------- §4/§5 tables

def test_denominator_and_coverage_tables_agree_with_row_grain():
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "K.CC.A.1", "CCSS", "K", "text", "CCSS", True)
    add_node(con, "CNT-0001", grades=("K",))
    tag(con, "CNT-0001", "K.CC.A.1")
    add_standard(con, "TX.1.9A", "TX", "1", "text", "Texas", False)

    all_rows = coverage.build_rows(con)
    denom = coverage.denominator_table(all_rows)
    assert denom["CCSS"] == {"PK5": 1, "6_9": 0, "NULL": 0}
    assert denom["Texas"] == {"PK5": 1, "6_9": 0, "NULL": 0}

    pk5 = [r for r in all_rows if r.band == "PK5"]
    cov = coverage.coverage_table(pk5)
    assert cov["CCSS"] == dict(n=1, green=1, yellow=0, red=0, pct_has_tag=100.0)
    assert cov["Texas"] == dict(n=1, green=0, yellow=0, red=1, pct_has_tag=0.0)

    claim = coverage.claim_table(pk5)
    assert claim == dict(yes_tag=1, yes_notag=0, blank_tag=0, blank_notag=1)

    flags = coverage.flags_summary(pk5)
    assert flags == dict(flagged_rows=0, yellow_rows=0, green_with_flag=0)


# --------------------------------------------------- stem attribution (B.2)
#
# Session B.2's rollup rules (brief §5), each pinned to the scenario that
# motivated it:
#
#   concept_standards wins        both sources reach a standard, they name
#                                  different stems -> concept_standards'
#                                  answer is used, the disagreement reported.
#   ambiguous stem_map name       two stem_map rows share one (band, name)
#                                  key by design (schema.sql) -> both kept,
#                                  a genuine multi-stem row.
#   band-scoped category route    a category cell naming a 6-9 stem never
#                                  attributes a PK5-graded standard.
#   inert stem != undrafted stem  a stem_map row with no `stems` entity at
#                                  all is unattributed, not undrafted -- a
#                                  judgment call this session made (see
#                                  build_stem_attribution's docstring).
#   any owning stem drafts it     ladder_status is 'drafted' if ANY stem in
#                                  a multi-stem set has nodes.
#   plain dataclass fields        stem_id/stem_name/stem_ids/ladder_status
#                                  survive dataclasses.asdict() (§9).

def add_stem(con, stem_id, name, domain=None):
    con.execute("INSERT OR IGNORE INTO stems (stem_id, name, domain) VALUES (?,?,?)",
                (stem_id, name, domain))


def add_stem_node(con, stem_id, node_id):
    """A minimal `nodes` row for stem-attribution tests: ladder_status counts
    nodes.stem_id, nothing else about grade_match, so grade ruling is
    deliberately omitted here (unlike add_node, used by the rollup tests)."""
    con.execute(
        "INSERT INTO nodes (node_id, stem_id, source_key, node_text) VALUES (?,?,?,?)",
        (node_id, stem_id, node_id, node_id))


def add_stem_map(con, stem_id, band, masterlist_name=None, workbook_stem=None,
                  workbook_stem_id=None):
    con.execute(
        "INSERT INTO stem_map (stem_id, band, masterlist_name, workbook_stem,"
        " workbook_stem_id, ladder_drafted) VALUES (?,?,?,?,?,0)",
        (stem_id, band, masterlist_name, workbook_stem, workbook_stem_id))


def add_concept_standard(con, stem_id, standard_code, stem_name=None):
    cur = con.execute(
        "INSERT INTO concepts (stem_id, stem_name, text) VALUES (?,?,?)",
        (stem_id, stem_name or stem_id, "concept text"))
    con.execute(
        "INSERT INTO concept_standards (concept_id, standard_id) VALUES (?,?)",
        (cur.lastrowid, standard_code))


def add_leaf_category(con, standard_id, category, jurisdiction="XX"):
    con.execute(
        "INSERT INTO leaves (jurisdiction, standard_id, category, origin)"
        " VALUES (?,?,?,'gaps_sheet')", (jurisdiction, standard_id, category))


def write_category_csv(rows):
    """rows: [(category, [(band_raw, domain, stem_name), ...]), ...] ->
    path to a temp CSV shaped like mh2_category_to_stems.csv (up to 8 stem
    columns; only as many as the widest row needs)."""
    width = max((len(cells) for _cat, cells in rows), default=1)
    cols = ["category"] + (["stem"] if width >= 1 else []) + [
        f"stem_{i}" for i in range(2, width + 1)]
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", newline="", delete=False, encoding="utf-8")
    w = csv.writer(f)
    w.writerow(cols)
    for cat, cells in rows:
        row = [cat] + [f"{b} | {d} | {n}" for b, d, n in cells]
        row += [""] * (len(cols) - len(row))
        w.writerow(row)
    f.close()
    return f.name


def test_concept_standards_wins_and_disagreement_is_reported():
    """Both sources reach 'AK.1.CC.2': concept_standards says ADD, the
    category route says MUL. concept_standards wins (§5 rule 1); the
    disagreement lands in the conflicts list, not silently dropped."""
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "AK.1.CC.2", "AK", "1", "text",
                "Other State Standards Gaps", False)
    add_stem(con, "ADD", "Addition")
    add_stem(con, "MUL", "Multiplication")
    add_stem_node(con, "ADD", "ADD-0001")
    add_concept_standard(con, "ADD", "AK.1.CC.2")
    add_leaf_category(con, "AK.1.CC.2", "counting")
    # Both same band as the standard (grade 1 -> PK5): §2.2 band-aware
    # precedence needs a stem_map row for every candidate to read its band.
    add_stem_map(con, "OE_ADDSUB", "PK5", masterlist_name="Addition",
                 workbook_stem_id="ADD")
    add_stem_map(con, "OE_MUL", "PK5", masterlist_name="Multiplication",
                 workbook_stem_id="MUL")
    csv_path = write_category_csv(
        [("counting", [("PK-5", "Operations", "Multiplication")])])

    attribution, conflicts, _diag = coverage.build_stem_attribution(con, csv_path)
    a = attribution["AK.1.CC.2"]
    assert a.stem_ids == ("ADD",)
    assert a.source == "concept_standards"
    assert a.ladder_status == "drafted"
    assert conflicts == [("AK.1.CC.2", ["ADD"], ["MUL"])]


def test_band_aware_precedence_demotes_cross_band_concept_standards():
    """§2.2: a 6-9 ladder cites a PK5-graded standard as prerequisite
    content (concept_standards names RP_COORDINATE_SYSTEM, band 6_9) while
    the category route names a real, same-band PK5 stem. The same-band
    category candidate wins over the cross-band concept_standards one --
    band beats route. Recorded in band_flips since the winner and source
    both changed from the old band-blind rule."""
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "5.G.A.1", "US", "5", "text",
                "Other State Standards Gaps", False)
    add_stem(con, "FRA", "Fractions")
    add_stem_node(con, "FRA", "FRA-0001")
    add_stem_map(con, "NSS_FRACTIONS", "PK5", masterlist_name="Fractions",
                 workbook_stem_id="FRA")
    add_stem_map(con, "RP_COORDINATE_SYSTEM", "6_9",
                 masterlist_name="Coordinate System")
    add_stem(con, "RP_COORDINATE_SYSTEM", "Coordinate System")
    add_stem_node(con, "RP_COORDINATE_SYSTEM", "RP_COORDINATE_SYSTEM-0001")
    add_concept_standard(con, "RP_COORDINATE_SYSTEM", "5.G.A.1")
    add_leaf_category(con, "5.G.A.1", "geometry")
    csv_path = write_category_csv(
        [("geometry", [("PK-5", "Number Systems", "Fractions")])])

    attribution, _conflicts, diag = coverage.build_stem_attribution(con, csv_path)
    a = attribution["5.G.A.1"]
    assert a.stem_ids == ("FRA",)
    assert a.source == "category"
    assert a.ladder_status == "drafted"

    flips = {f["code"]: f for f in diag["band_flips"]}
    assert "5.G.A.1" in flips
    assert flips["5.G.A.1"]["old"] == {
        "source": "concept_standards", "stem_ids": ["RP_COORDINATE_SYSTEM"],
        "status": "drafted"}
    assert flips["5.G.A.1"]["new"] == {
        "source": "category", "stem_ids": ["FRA"], "status": "drafted"}


def test_band_aware_precedence_untouched_when_no_same_band_alternative():
    """§2.2: a cross-band concept_standards hit with no same-band
    alternative from either route still wins outright -- precedence only
    demotes when there is something to demote to. Not a flip."""
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "5.G.A.2", "US", "5", "text",
                "Other State Standards Gaps", False)
    add_stem_map(con, "RP_COORDINATE_SYSTEM", "6_9",
                 masterlist_name="Coordinate System")
    add_stem(con, "RP_COORDINATE_SYSTEM", "Coordinate System")
    add_stem_node(con, "RP_COORDINATE_SYSTEM", "RP_COORDINATE_SYSTEM-0001")
    add_concept_standard(con, "RP_COORDINATE_SYSTEM", "5.G.A.2")
    csv_path = write_category_csv([])

    attribution, _conflicts, diag = coverage.build_stem_attribution(con, csv_path)
    a = attribution["5.G.A.2"]
    assert a.stem_ids == ("RP_COORDINATE_SYSTEM",)
    assert a.source == "concept_standards"
    assert a.ladder_status == "drafted"
    assert "5.G.A.2" not in {f["code"] for f in diag["band_flips"]}


def test_ambiguous_stem_map_name_is_a_genuine_multi_stem_row():
    """Two stem_map rows share one (band, masterlist_name) key by design
    (schema.sql's own example) -- both are kept as owning stems, nobody
    picks a winner. drafted because COM has a node even though ORD has none."""
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "XX.1.CMP.1", "XX", "1", "text",
                "Other State Standards Gaps", False)
    add_stem(con, "COM", "Comparing")
    add_stem(con, "ORD", "Ordering")
    add_stem_node(con, "COM", "COM-0001")
    add_stem_map(con, "NSS_COM", "PK5", masterlist_name="Comparing and Ordering",
                 workbook_stem_id="COM")
    add_stem_map(con, "NSS_ORD", "PK5", masterlist_name="Comparing and Ordering",
                 workbook_stem_id="ORD")
    add_leaf_category(con, "XX.1.CMP.1", "comparison")
    csv_path = write_category_csv(
        [("comparison", [("PK-5", "Number Systems", "Comparing and Ordering")])])

    attribution, _conflicts, _diag = coverage.build_stem_attribution(con, csv_path)
    a = attribution["XX.1.CMP.1"]
    assert set(a.stem_ids) == {"COM", "ORD"}
    assert a.ladder_status == "drafted"

    rows = coverage.build_rows(con, band="PK5", csv_path=csv_path)
    row = {r.code: r for r in rows}["XX.1.CMP.1"]
    assert row.stem_id is None            # no single winner when multi
    assert row.stem_name is None
    assert set(row.stem_ids) == {"COM", "ORD"}
    assert row.ladder_status == "drafted"


def test_category_route_is_band_scoped():
    """A category naming only a 6-9 stem must not attribute a PK5-graded
    standard, even though the category itself is unambiguous."""
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "XX.1.PROB.1", "XX", "1", "text",
                "Other State Standards Gaps", False)
    add_stem(con, "PS_PROBABILITY", "Probability")
    add_stem_node(con, "PS_PROBABILITY", "PS_PROBABILITY-0001")
    add_stem_map(con, "PS_PROBABILITY", "6_9", masterlist_name="Probability")
    add_leaf_category(con, "XX.1.PROB.1", "probability")
    csv_path = write_category_csv(
        [("probability", [("6-9", "Probability and Statistics", "Probability")])])

    attribution, _conflicts, _diag = coverage.build_stem_attribution(con, csv_path)
    assert attribution["XX.1.PROB.1"].ladder_status == "unattributed"
    assert attribution["XX.1.PROB.1"].stem_ids == ()


def test_inert_stem_map_row_is_undrafted_not_unattributed():
    """Step 1 §2.1 (call-2 reversal): a stem_map row naming a workbook stem
    with NO `stems` entity at all (no ladder file on disk -- MD_LENGTH/AREA/
    VOLUME/DATA case) still counts as an owning stem -- a resolved stem name
    is an ownership claim regardless of whether `stems` has a row for it.
    undrafted (routed, nobody has drafted it), not unattributed. The
    `no_ladder_yet` diagnostic still names it, but purely informationally --
    it no longer excludes the stem from `by_code`."""
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "XX.1.LEN.1", "XX", "1", "text",
                "Other State Standards Gaps", False)
    # No `stems` row for MD_LENGTH -- the ladder file doesn't exist yet.
    add_stem_map(con, "MD_LENGTH", "PK5", masterlist_name="Length")
    add_leaf_category(con, "XX.1.LEN.1", "measurement")
    csv_path = write_category_csv(
        [("measurement", [("PK-5", "Measurement and Data", "Length")])])

    attribution, _conflicts, diag = coverage.build_stem_attribution(con, csv_path)
    a = attribution["XX.1.LEN.1"]
    assert a.ladder_status == "undrafted"
    assert a.stem_ids == ("MD_LENGTH",)
    assert a.stem_name == "Length"          # never minted; taken from stem_map
    assert diag["no_ladder_yet"] == [
        ("measurement", "PK5", "Length", ["MD_LENGTH"])]


def test_neither_source_reaches_standard_is_unattributed():
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "XX.1.NIL.1", "XX", "1", "text",
                "Other State Standards Gaps", False)
    add_leaf_category(con, "XX.1.NIL.1", "nothing maps here")
    csv_path = write_category_csv([])

    attribution, _conflicts, diag = coverage.build_stem_attribution(con, csv_path)
    a = attribution["XX.1.NIL.1"]
    assert a.stem_ids == ()
    assert a.source is None
    assert a.ladder_status == "unattributed"
    assert diag["unresolved_targets"] == []


def test_category_route_only_counts_the_matching_band():
    """A category with a PK5 target (real, drafted) and a 6-9 target
    (unrelated band, and one with no `stems` row -- irrelevant post-§2.1)
    attributes a PK5 standard to ONLY the PK5 stem."""
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "XX.2.MIX.1", "XX", "2", "text",
                "Other State Standards Gaps", False)
    add_stem(con, "FRA", "Fractions")
    add_stem_node(con, "FRA", "FRA-0001")
    add_stem_map(con, "NSS_FRACTIONS", "PK5", masterlist_name="Fractions",
                 workbook_stem_id="FRA")
    add_stem_map(con, "NS_IRRATIONAL_REAL_NUMBERS", "6_9",
                 masterlist_name="Irrational Real Numbers")
    add_leaf_category(con, "XX.2.MIX.1", "numbers")
    csv_path = write_category_csv([
        ("numbers", [("PK-5", "Number Systems", "Fractions"),
                     ("6-9", "Structure of the Number System",
                      "Irrational Real Numbers")]),
    ])

    attribution, _conflicts, _diag = coverage.build_stem_attribution(con, csv_path)
    a = attribution["XX.2.MIX.1"]
    assert a.stem_ids == ("FRA",)
    assert a.ladder_status == "drafted"


def test_standard_row_stem_fields_are_plain_dataclass_fields():
    """§9: stem_id/stem_name/stem_ids/ladder_status must survive
    dataclasses.asdict(), unlike color/flagged/reasons/tagged_in_sheet."""
    con = fresh_db()
    seed_grade_order(con)
    add_standard(con, "FL.1.AR.1.1", "FL", "1", "text", "Florida", True)
    row = coverage.build_rows(con)[0]
    d = dataclasses.asdict(row)
    assert "stem_id" in d
    assert "stem_name" in d
    assert "stem_ids" in d
    assert "ladder_status" in d
    assert d["ladder_status"] == "unattributed"          # no attribution source
    assert d["stem_ids"] == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("all passed")
