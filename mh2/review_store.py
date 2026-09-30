"""
review_store.py -- owns every query shape against mh2_seq.db.

No other module issues SQL against mh2_seq.db (review_api.py routes call
only these functions -- see the brief's §6.8 "no SQL in any route"). Every
write here stamps `*_by` and `*_at`, plus whatever computed-color snapshot
the schema wants, itself: callers pass the reviewer identity and outcome,
never a timestamp or a color, so a route can never accept a client-supplied
value for either.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def ensure_schema(con: sqlite3.Connection, schema_path) -> None:
    con.executescript(schema_path.read_text())
    con.commit()


# ------------------------------------------------------------ standard_review

def get_standard_review(con: sqlite3.Connection, standard_id: str) -> dict | None:
    row = con.execute(
        "SELECT * FROM standard_review WHERE standard_id = ?", (standard_id,)
    ).fetchone()
    return dict(row) if row else None


def list_standard_reviews(con: sqlite3.Connection) -> dict[str, dict]:
    return {r["standard_id"]: dict(r)
            for r in con.execute("SELECT * FROM standard_review")}


def set_standard_review(con: sqlite3.Connection, standard_id: str, outcome: str,
                         computed_color_at_review: str, reviewed_by: str,
                         note: str | None = None) -> None:
    con.execute(
        "INSERT INTO standard_review (standard_id, outcome,"
        " computed_color_at_review, reviewed_by, reviewed_at, note)"
        " VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(standard_id) DO UPDATE SET"
        " outcome=excluded.outcome,"
        " computed_color_at_review=excluded.computed_color_at_review,"
        " reviewed_by=excluded.reviewed_by,"
        " reviewed_at=excluded.reviewed_at,"
        " note=excluded.note",
        (standard_id, outcome, computed_color_at_review, reviewed_by, _now(), note),
    )
    con.commit()


def clear_standard_review(con: sqlite3.Connection, standard_id: str) -> None:
    con.execute("DELETE FROM standard_review WHERE standard_id = ?", (standard_id,))
    con.commit()


# ------------------------------------------------------ standard_color_override

def get_standard_override(con: sqlite3.Connection, standard_id: str) -> dict | None:
    row = con.execute(
        "SELECT * FROM standard_color_override WHERE standard_id = ?", (standard_id,)
    ).fetchone()
    return dict(row) if row else None


def list_standard_overrides(con: sqlite3.Connection) -> dict[str, dict]:
    return {r["standard_id"]: dict(r)
            for r in con.execute("SELECT * FROM standard_color_override")}


def set_standard_override(con: sqlite3.Connection, standard_id: str, writer_color: str,
                           computed_color_at_set: str, reason: str, set_by: str) -> None:
    con.execute(
        "INSERT INTO standard_color_override (standard_id, writer_color,"
        " computed_color_at_set, reason, set_by, set_at) VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(standard_id) DO UPDATE SET"
        " writer_color=excluded.writer_color,"
        " computed_color_at_set=excluded.computed_color_at_set,"
        " reason=excluded.reason,"
        " set_by=excluded.set_by,"
        " set_at=excluded.set_at",
        (standard_id, writer_color, computed_color_at_set, reason, set_by, _now()),
    )
    con.commit()


def retire_standard_override(con: sqlite3.Connection, standard_id: str) -> None:
    con.execute("DELETE FROM standard_color_override WHERE standard_id = ?",
                (standard_id,))
    con.commit()


# --------------------------------------------------------------- standard_status

def get_standard_status(con: sqlite3.Connection, standard_id: str) -> dict | None:
    row = con.execute(
        "SELECT * FROM standard_status WHERE standard_id = ?", (standard_id,)
    ).fetchone()
    return dict(row) if row else None


def list_standard_statuses(con: sqlite3.Connection) -> dict[str, dict]:
    return {r["standard_id"]: dict(r)
            for r in con.execute("SELECT * FROM standard_status")}


def set_standard_status(con: sqlite3.Connection, standard_id: str, status: str,
                         set_by: str) -> None:
    con.execute(
        "INSERT INTO standard_status (standard_id, status, set_by, set_at)"
        " VALUES (?,?,?,?)"
        " ON CONFLICT(standard_id) DO UPDATE SET"
        " status=excluded.status,"
        " set_by=excluded.set_by,"
        " set_at=excluded.set_at",
        (standard_id, status, set_by, _now()),
    )
    con.commit()


# --------------------------------------------------------------- tag_review

def get_tag_review(con: sqlite3.Connection, standard_id: str, source_key: str) -> dict | None:
    row = con.execute(
        "SELECT * FROM tag_review WHERE standard_id = ? AND source_key = ?",
        (standard_id, source_key),
    ).fetchone()
    return dict(row) if row else None


def list_tag_reviews_for_standard(con: sqlite3.Connection, standard_id: str) -> dict[str, dict]:
    return {r["source_key"]: dict(r) for r in con.execute(
        "SELECT * FROM tag_review WHERE standard_id = ?", (standard_id,))}


def list_all_tag_reviews(con: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT * FROM tag_review")]


def set_tag_review(con: sqlite3.Connection, standard_id: str, source_key: str,
                    outcome: str, reviewed_by: str, node_id_seen: str | None = None,
                    node_text_seen: str | None = None, ladder_file_seen: str | None = None,
                    note: str | None = None) -> None:
    con.execute(
        "INSERT INTO tag_review (standard_id, source_key, outcome, node_id_seen,"
        " node_text_seen, ladder_file_seen, reviewed_by, reviewed_at, note)"
        " VALUES (?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(standard_id, source_key) DO UPDATE SET"
        " outcome=excluded.outcome,"
        " node_id_seen=excluded.node_id_seen,"
        " node_text_seen=excluded.node_text_seen,"
        " ladder_file_seen=excluded.ladder_file_seen,"
        " reviewed_by=excluded.reviewed_by,"
        " reviewed_at=excluded.reviewed_at,"
        " note=excluded.note",
        (standard_id, source_key, outcome, node_id_seen, node_text_seen,
         ladder_file_seen, reviewed_by, _now(), note),
    )
    con.commit()


def clear_tag_review(con: sqlite3.Connection, standard_id: str, source_key: str) -> None:
    con.execute(
        "DELETE FROM tag_review WHERE standard_id = ? AND source_key = ?",
        (standard_id, source_key),
    )
    con.commit()


# -------------------------------------------------------------- tag_proposal

def create_tag_proposal(con: sqlite3.Connection, standard_id: str, source_key: str,
                         node_text_seen: str, proposed_by: str,
                         node_id_seen: str | None = None, ladder_file: str | None = None,
                         rationale: str | None = None) -> int:
    cur = con.execute(
        "INSERT INTO tag_proposal (standard_id, source_key, node_id_seen,"
        " node_text_seen, ladder_file, rationale, proposed_by, proposed_at, state)"
        " VALUES (?,?,?,?,?,?,?,?,'open')",
        (standard_id, source_key, node_id_seen, node_text_seen, ladder_file,
         rationale, proposed_by, _now()),
    )
    con.commit()
    return cur.lastrowid


def withdraw_tag_proposal(con: sqlite3.Connection, proposal_id: int,
                           resolved_note: str | None = None) -> None:
    con.execute(
        "UPDATE tag_proposal SET state='withdrawn', resolved_at=?, resolved_note=?"
        " WHERE proposal_id = ?",
        (_now(), resolved_note, proposal_id),
    )
    con.commit()


def update_proposal_state(con: sqlite3.Connection, proposal_id: int, state: str,
                           resolved_note: str | None = None) -> None:
    """The only write reconcile_review.py makes: closing an open proposal
    out to 'landed' / 'landed_elsewhere' / 'needs_attention'. 'open' rows are
    left alone -- there is nothing to resolve them to yet."""
    con.execute(
        "UPDATE tag_proposal SET state=?, resolved_at=?, resolved_note=?"
        " WHERE proposal_id = ?",
        (state, _now(), resolved_note, proposal_id),
    )
    con.commit()


def open_proposal_exists(con: sqlite3.Connection, standard_id: str,
                          source_key: str) -> bool:
    """Is there already an unresolved proposal for this tag? Guards against
    a second 'accept' on the same candidate raising a duplicate instruction
    to edit the same ladder in the same way."""
    return con.execute(
        "SELECT 1 FROM tag_proposal WHERE standard_id = ? AND source_key = ?"
        " AND state = 'open' LIMIT 1",
        (standard_id, source_key),
    ).fetchone() is not None


def get_tag_proposal(con: sqlite3.Connection, proposal_id: int) -> dict | None:
    row = con.execute(
        "SELECT * FROM tag_proposal WHERE proposal_id = ?", (proposal_id,)
    ).fetchone()
    return dict(row) if row else None


def list_open_proposals(con: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT * FROM tag_proposal WHERE state = 'open'")]


def list_pending_proposals_for_standard(con: sqlite3.Connection,
                                        standard_id: str) -> list[dict]:
    """Proposals for one standard that the ladder has not absorbed yet:
    'open' (waiting on a ladder edit + rebuild) and 'needs_attention' (the
    proposed node vanished on rebuild -- still unresolved, so it must stay
    visible rather than silently drop out). Oldest first."""
    return [dict(r) for r in con.execute(
        "SELECT * FROM tag_proposal WHERE standard_id = ?"
        " AND state IN ('open','needs_attention') ORDER BY proposal_id",
        (standard_id,))]


def list_proposals(con: sqlite3.Connection, state: str | None = None) -> list[dict]:
    if state is None:
        return [dict(r) for r in con.execute("SELECT * FROM tag_proposal")]
    return [dict(r) for r in con.execute(
        "SELECT * FROM tag_proposal WHERE state = ?", (state,))]


# ---------------------------------------------------------- candidate_ruling

def get_candidate_ruling(con: sqlite3.Connection, source_key: str,
                          standard_id: str) -> dict | None:
    row = con.execute(
        "SELECT * FROM candidate_ruling WHERE source_key = ? AND standard_id = ?",
        (source_key, standard_id),
    ).fetchone()
    return dict(row) if row else None


def list_candidate_rulings(con: sqlite3.Connection) -> dict[tuple[str, str], dict]:
    """(source_key, standard_id) -> ruling row, for the whole table.

    Returned whole rather than filtered because the review tool's callers
    aggregate over the entire suggested-candidate pool (under 1,000 rows) in
    one pass, and they live in mh2.db while this table lives in mh2_seq.db --
    there is no join to push down.
    """
    return {(r["source_key"], r["standard_id"]): dict(r)
            for r in con.execute("SELECT * FROM candidate_ruling")}


def set_candidate_ruling(con: sqlite3.Connection, source_key: str, standard_id: str,
                          ruling: str, ruled_by: str, node_id_seen: str | None = None,
                          node_text_seen: str | None = None,
                          ladder_file_seen: str | None = None) -> None:
    con.execute(
        "INSERT INTO candidate_ruling (source_key, standard_id, ruling,"
        " node_id_seen, node_text_seen, ladder_file_seen, ruled_by, ruled_at)"
        " VALUES (?,?,?,?,?,?,?,?)"
        " ON CONFLICT(source_key, standard_id) DO UPDATE SET"
        " ruling=excluded.ruling,"
        " node_id_seen=excluded.node_id_seen,"
        " node_text_seen=excluded.node_text_seen,"
        " ladder_file_seen=excluded.ladder_file_seen,"
        " ruled_by=excluded.ruled_by,"
        " ruled_at=excluded.ruled_at",
        (source_key, standard_id, ruling, node_id_seen, node_text_seen,
         ladder_file_seen, ruled_by, _now()),
    )
    con.commit()


def candidate_ruling_reviewers(con: sqlite3.Connection) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT DISTINCT ruled_by FROM candidate_ruling"
        " WHERE ruled_by IS NOT NULL ORDER BY ruled_by")]
