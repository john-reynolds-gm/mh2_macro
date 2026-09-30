"""
load_grade_ruling.py tests.

Four things matter here, all traps in the real worksheet:

  is_leaf wins       a leaf row that also lists grades (5 of them in the real
                     file) must still resolve 'leaf', with the grades kept as
                     node_grade rows -- annotation, not a demotion.
  the twins          footnote-anchored and plain-spelling variants of the same
                     raw value canonicalize to one key and must agree on their
                     ruling, or the load fails loudly.
  no silent FALSE    is_leaf is read from text ('TRUE'/'FALSE'/'False'); any
                     other value is an error, not a default.
  idempotent         re-running replaces both tables rather than layering on.

Run with: python -m pytest tests/ -q   (or: python tests/test_load_grade_ruling.py)
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

from mh2.load_grade_ruling import (  # noqa: E402
    VALID_GRADES, blank_to_none, build_lookup, derive_kinds,
    parse_default_grades, parse_is_leaf, resolve_nodes, seed_grade_order,
)
from mh2.grade_split_review import build_rows, propose  # noqa: E402


def worksheet(rows):
    """rows: list of dicts with raw_value/default_grades/is_leaf/notes."""
    return pd.DataFrame(rows, columns=["raw_value", "default_grades",
                                       "is_leaf", "notes"])


def fresh_db():
    con = sqlite3.connect(":memory:")
    con.executescript(config.SCHEMA.read_text())
    return con


def add_node(con, node_id, grade_or_leaf, source_file="f.docx"):
    con.execute(
        "INSERT INTO nodes (node_id, source_key, node_text, grade_or_leaf,"
        " source_file) VALUES (?, ?, ?, ?, ?)",
        (node_id, node_id, node_id, grade_or_leaf, source_file))


# --------------------------------------------------------------- is_leaf

def test_is_leaf_reads_true_false_and_mixed_case():
    assert parse_is_leaf("r", "TRUE") is True
    assert parse_is_leaf("r", "FALSE") is False
    assert parse_is_leaf("r", "False") is False
    assert parse_is_leaf("r", True) is True


def test_is_leaf_rejects_anything_else():
    try:
        parse_is_leaf("some raw value", "yes")
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert "some raw value" in str(e)


# ---------------------------------------------------------- default_grades

def test_default_grades_splits_and_strips():
    assert parse_default_grades("r", "5, 6, 7") == ["5", "6", "7"]
    assert parse_default_grades("r", 6) == ["6"]


def test_default_grades_blank_is_empty_list():
    assert parse_default_grades("r", float("nan")) == []


def test_default_grades_rejects_unrecognized_token():
    try:
        parse_default_grades("some raw value", "5, G9")
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert "some raw value" in str(e)


def test_valid_grades_matches_grade_order_tokens():
    assert VALID_GRADES == {"PK", "K", "1", "2", "3", "4", "5", "6", "7", "8",
                            "A1", "OUT"}


# -------------------------------------------------------------- build_lookup

def test_leaf_row_with_grades_keeps_the_grades_as_annotation():
    df = worksheet([
        {"raw_value": "LEAF 6", "default_grades": 6, "is_leaf": "TRUE",
         "notes": None},
    ])
    lookup = build_lookup(df)
    entry = lookup["leaf 6"]
    assert entry["is_leaf"] is True
    assert entry["default_grades"] == ["6"]


def test_anchored_twin_agrees_and_collapses_to_one_key():
    df = worksheet([
        {"raw_value": "G1[^c7]", "default_grades": 1, "is_leaf": "FALSE",
         "notes": None},
        {"raw_value": "G1", "default_grades": 1, "is_leaf": "FALSE",
         "notes": None},
    ])
    lookup = build_lookup(df)
    assert len(lookup) == 1
    assert lookup["1"]["default_grades"] == ["1"]


def test_twins_disagreeing_fails_loudly_naming_both():
    df = worksheet([
        {"raw_value": "G1[^c7]", "default_grades": 1, "is_leaf": "FALSE",
         "notes": None},
        {"raw_value": "G1", "default_grades": 2, "is_leaf": "FALSE",
         "notes": None},
    ])
    try:
        build_lookup(df)
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert "G1[^c7]" in str(e) and "G1" in str(e)


# -------------------------------------------------------------- resolve_nodes

def test_blank_grade_field_is_no_grade_field():
    con = fresh_db()
    add_node(con, "N1", None)
    add_node(con, "N2", "   ")
    counts, n_grade_rows = resolve_nodes(con, {})
    assert counts["no_grade_field"] == 2
    assert n_grade_rows == 0
    rows = con.execute(
        "SELECT resolution, canon_key FROM node_grade_ruling").fetchall()
    assert all(r == ("no_grade_field", None) for r in rows)


def test_unmatched_raw_value_is_unresolved_and_names_the_canon_key():
    con = fresh_db()
    add_node(con, "N1", "7, 8")
    counts, n_grade_rows = resolve_nodes(con, {})
    assert counts["unresolved"] == 1
    assert n_grade_rows == 0
    canon_key = con.execute(
        "SELECT canon_key FROM node_grade_ruling WHERE node_id = 'N1'"
    ).fetchone()[0]
    assert canon_key == "7, 8"


def test_ruled_node_gets_one_node_grade_row_per_token():
    con = fresh_db()
    seed_grade_order(con)
    add_node(con, "N1", "Grade 1")
    lookup = {"1": {"raw_value": "1", "default_grades": ["1"],
                    "is_leaf": False, "notes": None}}
    counts, n_grade_rows = resolve_nodes(con, lookup)
    assert counts["ruled"] == 1
    assert n_grade_rows == 1
    grades = [g for (g,) in con.execute(
        "SELECT grade FROM node_grade WHERE node_id = 'N1'")]
    assert grades == ["1"]


def test_leaf_node_resolution_wins_even_with_grades_present():
    con = fresh_db()
    seed_grade_order(con)
    add_node(con, "N1", "LEAF 6")
    lookup = {"leaf 6": {"raw_value": "LEAF 6", "default_grades": ["6"],
                         "is_leaf": True, "notes": None}}
    counts, n_grade_rows = resolve_nodes(con, lookup)
    assert counts["leaf"] == 1
    assert counts["ruled"] == 0
    assert n_grade_rows == 1
    resolution, is_leaf = con.execute(
        "SELECT resolution, is_leaf FROM node_grade_ruling"
        " WHERE node_id = 'N1'").fetchone()
    assert (resolution, is_leaf) == ("leaf", 1)


def test_notes_copy_verbatim_onto_the_ruling():
    con = fresh_db()
    seed_grade_order(con)
    add_node(con, "N1", "4")
    lookup = {"4": {"raw_value": "4", "default_grades": ["4"],
                    "is_leaf": False, "notes": "3 for some states"}}
    resolve_nodes(con, lookup)
    notes = con.execute(
        "SELECT notes FROM node_grade_ruling WHERE node_id = 'N1'"
    ).fetchone()[0]
    assert notes == "3 for some states"


def test_rerun_is_idempotent():
    con = fresh_db()
    seed_grade_order(con)
    add_node(con, "N1", "4")
    lookup = {"4": {"raw_value": "4", "default_grades": ["4"],
                    "is_leaf": False, "notes": None}}
    resolve_nodes(con, lookup)
    resolve_nodes(con, lookup)
    assert con.execute(
        "SELECT COUNT(*) FROM node_grade_ruling").fetchone()[0] == 1
    assert con.execute(
        "SELECT COUNT(*) FROM node_grade").fetchone()[0] == 1


# ------------------------------------- ruling_type / node_grade_kind (additive)

def full_worksheet(rows):
    """Like worksheet() but with the optional sequencer columns."""
    return pd.DataFrame(rows, columns=[
        "raw_value", "default_grades", "is_leaf", "notes", "ruling_type",
        "needs_writer_review", "states_mentioned", "ccss_default_grades"])


def entry(rtype, grades, ccss=None, notes=None):
    return {"raw_value": "r", "default_grades": grades, "is_leaf": False,
            "notes": notes, "ruling_type": rtype, "ccss_default_grades": ccss}


def test_blank_to_none_never_invents_a_value():
    assert blank_to_none(float("nan")) is None
    assert blank_to_none("  ") is None
    assert blank_to_none(None) is None
    assert blank_to_none(" span ") == "span"


def test_build_lookup_carries_optional_columns_and_nulls_blanks():
    df = full_worksheet([
        ["4 (5 for SC)", "4, 5", "False", "5 for SC", "state_conditional",
         False, "SC", None],
        ["7, 8", "7, 8", "False", None, None, None, None, None]])
    lookup = build_lookup(df)
    a = lookup[list(lookup)[0]]
    assert (a["ruling_type"], a["needs_writer_review"],
            a["states_mentioned"]) == ("state_conditional", 0, "SC")
    b = lookup[list(lookup)[1]]
    assert (b["ruling_type"], b["needs_writer_review"],
            b["states_mentioned"]) == (None, None, None)


def test_build_lookup_still_works_with_the_four_column_worksheet():
    lookup = build_lookup(worksheet([["1", "1", "False", None]]))
    assert lookup["1"]["ruling_type"] is None


def test_ccss_default_grades_must_be_subset_of_default_grades():
    df = full_worksheet([["4 (5 for SC)", "4, 5", "False", None,
                          "state_conditional", False, "SC", "3"]])
    try:
        build_lookup(df)
    except SystemExit as e:
        assert "not a subset" in str(e)
    else:
        raise AssertionError("expected SystemExit")


def test_derive_kinds_single_span_and_unconfirmed():
    assert derive_kinds(entry("single", ["4"])) == [
        ("4", "core", "ruling_type=single")]
    assert [k for _, k, _ in derive_kinds(entry("span", ["3", "4", "5"]))] \
        == ["span"] * 3
    for rtype in ("state_conditional", "range_prose", "alternative",
                  "unparsed"):
        kinds = derive_kinds(entry(rtype, ["3", "4"]))
        assert [k for _, k, _ in kinds] == ["unconfirmed"] * 2


def test_derive_kinds_no_rows_for_leaf_out_of_band_or_blank():
    for rtype in ("leaf", "out_of_band", None):
        assert derive_kinds(entry(rtype, ["6"])) == []


def test_derive_kinds_uses_ccss_default_grades_when_ruled():
    kinds = derive_kinds(entry("state_conditional", ["3", "4"], ccss=["4"]))
    assert [(g, k) for g, k, _ in kinds] == [("3", "state_extension"),
                                             ("4", "core")]


def test_kind_table_is_written_and_node_grade_is_unchanged():
    con = fresh_db()
    seed_grade_order(con)
    add_node(con, "N1", "4 (5 for SC)")
    add_node(con, "N2", "3")
    add_node(con, "N3", "LEAF 6")
    lookup = {
        "4 (5 for sc)": {**entry("state_conditional", ["4", "5"]),
                         "raw_value": "4 (5 for SC)",
                         "states_mentioned": "SC",
                         "needs_writer_review": 0},
        "3": {**entry("single", ["3"]), "raw_value": "3"},
        "leaf 6": {**entry("leaf", ["6"]), "raw_value": "LEAF 6",
                   "is_leaf": True},
    }
    counts, n_grade_rows = resolve_nodes(con, lookup)
    assert n_grade_rows == 4           # 2 + 1 + 1, same as before the change
    assert con.execute("SELECT COUNT(*) FROM node_grade").fetchone()[0] == 4
    kinds = dict(((n, g), k) for n, g, k in con.execute(
        "SELECT node_id, grade, kind FROM node_grade_kind"))
    assert kinds == {("N1", "4"): "unconfirmed", ("N1", "5"): "unconfirmed",
                     ("N2", "3"): "core"}
    assert con.execute(
        "SELECT ruling_type, needs_writer_review, states_mentioned"
        " FROM node_grade_ruling WHERE node_id = 'N1'"
    ).fetchone() == ("state_conditional", 0, "SC")


def test_kind_table_rerun_is_idempotent():
    con = fresh_db()
    seed_grade_order(con)
    add_node(con, "N1", "3")
    lookup = {"3": {**entry("single", ["3"]), "raw_value": "3"}}
    resolve_nodes(con, lookup)
    resolve_nodes(con, lookup)
    assert con.execute(
        "SELECT COUNT(*) FROM node_grade_kind").fetchone()[0] == 1


# --------------------------------------------------- grade_split_review

def test_review_parses_state_extension_from_notes():
    e = entry("state_conditional", ["3", "4"], notes="3 for some states")
    default, ext, conf, _ = propose(e)
    assert default == ["4"]
    assert ext == {"some states": "3"}
    assert conf == "parsed"


def test_review_does_not_mistake_OK_or_FL_for_grades():
    e = entry("state_conditional", ["5", "6"], notes="5 for FL, TX, OK")
    default, ext, conf, _ = propose(e)
    assert default == ["6"]
    assert ext == {"FL": "5", "TX": "5", "OK": "5"}


def test_review_range_prose_is_a_guess_with_no_extension():
    default, ext, conf, _ = propose(entry("range_prose", ["PK", "K"]))
    assert (default, ext, conf) == (["PK", "K"], {}, "guess")


def test_review_rows_only_cover_open_types_and_carry_node_counts():
    lookup = {"a": {**entry("span", ["1", "2"]), "raw_value": "a"},
              "b": {**entry("range_prose", ["6"]), "raw_value": "b"}}
    rows = build_rows(lookup, {"b": 7})
    assert [(r["raw_value"], r["node_count"], r["john_ruling"])
            for r in rows] == [("b", 7, "")]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("all passed")
