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
    VALID_GRADES, build_lookup, parse_default_grades, parse_is_leaf,
    resolve_nodes, seed_grade_order,
)


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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("all passed")
