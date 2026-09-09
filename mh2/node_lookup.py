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
