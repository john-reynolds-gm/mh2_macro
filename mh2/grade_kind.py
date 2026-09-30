"""
grade_kind.py -- the one adapter that knows how a node's per-grade "kind" is
stored in mh2.db (docs/seq_data_model_rev1.md section 5.2, contract 3.1).

Everything else in the sequencer sees only the READ_STATES vocabulary below.

The stored kinds live in the sibling table `node_grade_kind` (built on
seq/grade_type). This module never re-derives a kind from `ruling_type`; that
rule lives in mh2.load_grade_ruling only, and this module does not import it.

Read-only: every function takes an open connection and only SELECTs.
"""
from __future__ import annotations

import sqlite3

# Kinds that node_grade_kind.kind may hold (its CHECK constraint).
STORED_KINDS = ("core", "span", "state_extension", "unconfirmed")

# Every state a (node, grade) can be READ as.
READ_STATES = STORED_KINDS + ("off_grade", "leaf", "no_grade", "unknown")

# States counted in a grade's inventory ("owed").
OWED_STATES = ("core", "span", "unconfirmed", "unknown")

# States where placing the node needs confirm_off_grade (a "bridge").
BRIDGE_STATES = ("off_grade", "state_extension", "leaf", "no_grade")

# = the grade_sequence CHECK list; excludes 'OUT'.
GRADES = ("PK", "K", "1", "2", "3", "4", "5", "6", "7", "8", "A1")

# node_grade_ruling columns we read. The last three only exist on DBs built
# from seq/grade_type; on older DBs they come back as None.
_RULING_COLUMNS = ("raw_value", "resolution", "is_leaf", "notes",
                   "ruling_type", "states_mentioned", "needs_writer_review")


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    """True if `name` is a table in this database."""
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _columns(con: sqlite3.Connection, table: str) -> set:
    """Column names of `table` (empty set if the table does not exist)."""
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def kind_source(con: sqlite3.Connection) -> str:
    """'grade_type' iff table node_grade_kind exists AND has >= 1 row.

    Otherwise 'fallback' (a DB built before the grade_type work, or before
    the rebuild that loads it)."""
    if not _has_table(con, "node_grade_kind"):
        return "fallback"
    row = con.execute("SELECT 1 FROM node_grade_kind LIMIT 1").fetchone()
    return "grade_type" if row is not None else "fallback"


def build_grade_kinds(con: sqlite3.Connection) -> dict:
    """(node_id, grade) -> kind for EVERY node_grade row.

    The value is node_grade_kind.kind when a kind row exists, else 'unknown'.
    In the fallback case every node_grade row maps to 'unknown'. The key set
    always equals the node_grade table, so an in-grade row can never fall
    through to 'off_grade'."""
    stored = {}
    if kind_source(con) == "grade_type":
        stored = {(r[0], r[1]): r[2] for r in
                  con.execute("SELECT node_id, grade, kind FROM node_grade_kind")}
    kinds = {}
    for node_id, grade in con.execute("SELECT node_id, grade FROM node_grade"):
        kinds[(node_id, grade)] = stored.get((node_id, grade), "unknown")
    return kinds


def ruling_by_node(con: sqlite3.Connection) -> dict:
    """node_id -> {raw_value, resolution, is_leaf, notes, ruling_type,
    states_mentioned, needs_writer_review}.

    Columns missing from an older node_grade_ruling come back as None. A DB
    with no node_grade_ruling table at all gives {}."""
    if not _has_table(con, "node_grade_ruling"):
        return {}
    have = _columns(con, "node_grade_ruling")
    select = ", ".join(c if c in have else f"NULL AS {c}" for c in _RULING_COLUMNS)
    out = {}
    for r in con.execute(f"SELECT node_id, {select} FROM node_grade_ruling"):
        out[r[0]] = {col: r[i + 1] for i, col in enumerate(_RULING_COLUMNS)}
    return out


def kind_for(node_id: str, grade: str, kinds: dict, ruling_by_node: dict) -> str:
    """The read state for ANY (node, grade), including grades the node lacks.

    Precedence: leaf (resolution == 'leaf', wins unconditionally)
              > no_grade (resolution == 'no_grade_field')
              > kinds[(node_id, grade)]
              > 'off_grade'."""
    resolution = (ruling_by_node.get(node_id) or {}).get("resolution")
    if resolution == "leaf":
        return "leaf"
    if resolution == "no_grade_field":
        return "no_grade"
    return kinds.get((node_id, grade), "off_grade")
