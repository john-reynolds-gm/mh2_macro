"""
review_store.py tests -- CRUD round-trips against an in-memory mh2_seq.db.
Pinned behaviors: every write stamps `*_by`/`*_at` and a computed-color
snapshot itself (never accepts one from a caller as a pass-through value --
that guarantee lives one layer up, in review_api.py, but the store's own
contract is that `set_*` always overwrites the timestamp, which these tests
check by round-tripping and reading it back non-empty).
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from mh2 import review_store  # noqa: E402


def fresh_seq_db():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    review_store.ensure_schema(con, config.SCHEMA_SEQ)
    return con


def test_standard_review_round_trip_and_upsert():
    con = fresh_seq_db()
    review_store.set_standard_review(con, "K.CC.A.1", "confirmed", "Green", "jane")
    row = review_store.get_standard_review(con, "K.CC.A.1")
    assert row["outcome"] == "confirmed"
    assert row["computed_color_at_review"] == "Green"
    assert row["reviewed_by"] == "jane"
    assert row["reviewed_at"]

    review_store.set_standard_review(con, "K.CC.A.1", "insufficient", "Yellow", "jo")
    row = review_store.get_standard_review(con, "K.CC.A.1")
    assert row["outcome"] == "insufficient"
    assert row["reviewed_by"] == "jo"


def test_clear_standard_review():
    con = fresh_seq_db()
    review_store.set_standard_review(con, "K.CC.A.1", "confirmed", "Green", "jane")
    review_store.clear_standard_review(con, "K.CC.A.1")
    assert review_store.get_standard_review(con, "K.CC.A.1") is None


def test_standard_override_round_trip_and_retire():
    con = fresh_seq_db()
    review_store.set_standard_override(con, "TX.1.3C", "Green", "Yellow",
                                        "on-grade in practice", "jane")
    row = review_store.get_standard_override(con, "TX.1.3C")
    assert row["writer_color"] == "Green"
    assert row["computed_color_at_set"] == "Yellow"

    review_store.retire_standard_override(con, "TX.1.3C")
    assert review_store.get_standard_override(con, "TX.1.3C") is None


def test_tag_review_keyed_on_standard_and_source_key():
    con = fresh_seq_db()
    review_store.set_tag_review(con, "K.CC.A.1", "COM::count", "confirmed", "jane",
                                 node_id_seen="COM-0001", node_text_seen="count",
                                 ladder_file_seen="Comparing.docx")
    row = review_store.get_tag_review(con, "K.CC.A.1", "COM::count")
    assert row["outcome"] == "confirmed"
    assert row["ladder_file_seen"] == "Comparing.docx"

    reviews = review_store.list_tag_reviews_for_standard(con, "K.CC.A.1")
    assert set(reviews) == {"COM::count"}

    review_store.clear_tag_review(con, "K.CC.A.1", "COM::count")
    assert review_store.get_tag_review(con, "K.CC.A.1", "COM::count") is None


def test_tag_proposal_lifecycle():
    con = fresh_seq_db()
    pid = review_store.create_tag_proposal(
        con, "K.CC.A.1", "COM::count more", "count more", "jane",
        ladder_file="Comparing.docx")
    assert review_store.get_tag_proposal(con, pid)["state"] == "open"
    assert len(review_store.list_open_proposals(con)) == 1

    review_store.update_proposal_state(con, pid, "landed", resolved_note="Green -> Green")
    assert review_store.get_tag_proposal(con, pid)["state"] == "landed"
    assert review_store.list_open_proposals(con) == []


def test_withdraw_proposal():
    con = fresh_seq_db()
    pid = review_store.create_tag_proposal(con, "K.CC.A.1", "key", "text", "jane")
    review_store.withdraw_tag_proposal(con, pid, resolved_note="not needed after all")
    row = review_store.get_tag_proposal(con, pid)
    assert row["state"] == "withdrawn"
    assert row["resolved_note"] == "not needed after all"
