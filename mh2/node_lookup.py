"""
node_lookup.py -- the single boundary at which a coverage.Tag becomes
reviewable.

coverage.Tag carries node_id but not source_key, node_text, or source_file
(coverage.py is do-not-touch this session), and the review write path keys
tag-level review on (standard_id, source_key), not node_id. This module owns
the one query that bridges that gap. Every place that needs source_key,
node_text, or ladder_file for a tag goes through here; no other module
issues this query, and no route issues it at all.

Reads mh2.db only. Never writes.
"""
from __future__ import annotations

import sqlite3
from typing import NamedTuple

from mh2.coverage import _collapse_ws

_QUERY = "SELECT node_id, source_key, node_text, source_file, stem_id, seq FROM nodes"


class NodeInfo(NamedTuple):
    node_id: str
    source_key: str
    node_text: str
    source_file: str | None
    stem_id: str | None
    seq: int | None


def build_node_lookup(con: sqlite3.Connection) -> dict[str, NodeInfo]:
    """node_id -> NodeInfo, for every node currently in `nodes`."""
    return {row[0]: NodeInfo(*row) for row in con.execute(_QUERY)}


def build_source_key_lookup(con: sqlite3.Connection) -> dict[str, NodeInfo]:
    """source_key -> NodeInfo. source_key is UNIQUE NOT NULL on `nodes`
    (schema.sql), so this is a one-to-one re-indexing of build_node_lookup's
    result, not a second query."""
    return {info.source_key: info for info in build_node_lookup(con).values()}


def build_stem_names(con: sqlite3.Connection) -> dict[str, str]:
    """stem_id -> stems.name, for resolving a stem code to its display name.
    `stems` is the catalog table (schema.sql), not the hand-maintained
    stems.csv mapping in `stem_map` -- the latter has data-quality wrinkles
    (embedded newlines, many-to-many naming) that `stems.name` does not.
    Names are whitespace-collapsed -- at least one (WHO) carries an embedded
    newline, same wrinkle coverage.py already collapses for its own
    stem_info (mh2/coverage.py:407)."""
    return {stem_id: _collapse_ws(name)
            for stem_id, name in con.execute("SELECT stem_id, name FROM stems")}


class StemNode(NamedTuple):
    source_key: str
    node_text: str
    concept_skill: str


def nodes_for_stem(con: sqlite3.Connection, stem_id: str) -> list[StemNode]:
    """Every node in stem_id, ordered by concept_skill then seq -- the shape
    a grouped node picker needs. concept_skill is coalesced to 'Ungrouped'
    defensively; current data has none, but nothing enforces that.
    node_text/concept_skill are whitespace-collapsed for the same reason as
    build_stem_names: this is display label text for an <option>, not a
    stored value."""
    cur = con.execute(
        "SELECT source_key, node_text, COALESCE(concept_skill, 'Ungrouped') "
        "AS concept_skill FROM nodes WHERE stem_id = ? "
        "ORDER BY concept_skill, seq",
        (stem_id,),
    )
    return [StemNode(source_key, _collapse_ws(node_text), _collapse_ws(concept_skill))
            for source_key, node_text, concept_skill in cur.fetchall()]
