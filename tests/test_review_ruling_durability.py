"""
app/db.py write-path tests -- the brief's §6, acceptance criteria #7 and #8.

  #7  write_ruling(..., "accepted", ...) creates a tag_proposal row in
      mh2_seq.db reachable through GET /api/worklist, and writes nothing
      to node_standards
  #8  after a rebuild, the Streamlit sidebar's ruled/total tally for a stem
      is unchanged -- the regression the read-path rerouting exists to
      prevent, and the reason #7 could not land on its own

`node_standards` lives in mh2.db, which every rebuild destroys. Rulings
recorded there were silently lost; rulings now go to mh2_seq.db, which is
never rebuilt. The tags themselves are still written nowhere -- they belong
to the Word ladders (§6) -- so an accepted candidate raises a tag_proposal,
which is an instruction to a human to go and edit one.
"""
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from mh2 import review_store  # noqa: E402
from test_reconcile_review import _mh2_db, _node, _seq_db, _standard  # noqa: E402

STEM = "COM"
NODE = "COM-0001"
SOURCE_KEY = "COM::count more"
STANDARD = "CA.1.NBT.2"
NODE_TEXT = "Count forward beyond 100 from any given number"
LADDER = "MH2_PK5_Counting.docx"


def _candidate(con, node_id, standard_code, state="CA", strength="strong"):
    con.execute(
        "INSERT INTO candidates (run_id, node_id, concept_skill_id, state,"
        " standard_code, combined_score, strength, auto_surface,"
        " evidence_json, generated_at)"
        " VALUES (1,?,?,?,?,0.9,?,1,'{}','2026-09-29T00:00:00Z')",
        (node_id, "cs1", state, standard_code, strength))


def _build_mh2(path: Path):
    """A minimal mh2.db holding exactly one reviewable suggested candidate.

    The candidate is built to clear `_ruled_totals_by_stem`'s filter
    (app/db.py) rather than merely to exist: auto_surface = 1, state 'CA',
    and no matching `node_standards_parsed` row. That is what makes the
    tally it produces a literal (ruled, total) = (0, 1) rather than an
    empty dict, so a ruling moving it to (1, 1) is an observable change.
    """
    con = _mh2_db(path)
    _standard(con, STANDARD, "1")
    _node(con, NODE, SOURCE_KEY, grades=("1",), stem_id=STEM)
    con.execute(
        "UPDATE nodes SET concept_skill = ?, node_text = ?, source_file = ?"
        " WHERE node_id = ?", ("Counting", NODE_TEXT, LADDER, NODE))
    _candidate(con, NODE, STANDARD)
    con.commit()
    con.close()


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    """app/db.py wired to throwaway databases. Streamlit's cache decorators
    are process-global, so every cached entry is cleared before each test or
    the previous test's connections leak into this one."""
    _build_mh2(tmp_path / "mh2.db")
    _seq_db(tmp_path / "mh2_seq.db").close()

    monkeypatch.setattr(config, "DB", tmp_path / "mh2.db")
    monkeypatch.setattr(config, "SEQ_DB", tmp_path / "mh2_seq.db")

    import db
    for cached in (db.get_connection, db.get_seq_connection,
                   db.suggested_pool, db.domain_tree):
        cached.clear()
    yield db
    for cached in (db.get_connection, db.get_seq_connection,
                   db.suggested_pool, db.domain_tree):
        cached.clear()


def _seq(path):
    return review_store.connect(path)


# ----------------------------------------------------------------------- #7

def test_accept_writes_a_ruling_and_a_proposal_to_the_review_db(app_db, tmp_path):
    db = app_db
    db.write_ruling(db.get_connection(), NODE, STANDARD, "accepted", "jane")

    seq = _seq(tmp_path / "mh2_seq.db")
    ruling = review_store.get_candidate_ruling(seq, SOURCE_KEY, STANDARD)
    assert ruling["ruling"] == "accepted"
    assert ruling["ruled_by"] == "jane"
    assert ruling["ruled_at"]

    proposals = review_store.list_open_proposals(seq)
    assert len(proposals) == 1
    assert proposals[0]["standard_id"] == STANDARD
    assert proposals[0]["source_key"] == SOURCE_KEY
    assert proposals[0]["proposed_by"] == "jane"
    seq.close()


def test_accept_writes_nothing_to_node_standards(app_db, tmp_path):
    """#7's second half. node_standards is in the database a rebuild
    destroys; a ruling landing there is the bug being fixed."""
    db = app_db
    db.write_ruling(db.get_connection(), NODE, STANDARD, "accepted", "jane")

    con = sqlite3.connect(tmp_path / "mh2.db")
    assert con.execute("SELECT COUNT(*) FROM node_standards").fetchone()[0] == 0
    con.close()


def test_reject_writes_nothing_to_node_standards(app_db, tmp_path):
    db = app_db
    db.write_ruling(db.get_connection(), NODE, STANDARD, "rejected", "jane")

    con = sqlite3.connect(tmp_path / "mh2.db")
    assert con.execute("SELECT COUNT(*) FROM node_standards").fetchone()[0] == 0
    con.close()


def test_accepted_proposal_is_reachable_through_the_worklist(app_db, tmp_path):
    """#7: 'reachable through GET /api/worklist', literally."""
    db = app_db
    db.write_ruling(db.get_connection(), NODE, STANDARD, "accepted", "jane")

    import review_api
    client = TestClient(review_api.app)
    body = client.get("/api/worklist").json()

    found = [p for group in body["by_ladder_file"] for p in group["proposals"]]
    assert [p["standard_id"] for p in found] == [STANDARD]
    assert found[0]["source_key"] == SOURCE_KEY


def test_reject_is_recorded_but_raises_no_proposal(app_db, tmp_path):
    """A rejection is a judgment about a machine suggestion, not an
    instruction to edit a ladder. It must still be recorded, or the tool
    re-surfaces the same rejected candidate after every rebuild."""
    db = app_db
    db.write_ruling(db.get_connection(), NODE, STANDARD, "rejected", "jane")

    seq = _seq(tmp_path / "mh2_seq.db")
    assert review_store.get_candidate_ruling(seq, SOURCE_KEY, STANDARD)["ruling"] \
        == "rejected"
    assert review_store.list_open_proposals(seq) == []
    seq.close()


def test_accepting_twice_does_not_duplicate_the_proposal(app_db, tmp_path):
    db = app_db
    con = db.get_connection()
    db.write_ruling(con, NODE, STANDARD, "accepted", "jane")
    db.write_ruling(con, NODE, STANDARD, "rejected", "jane")
    db.write_ruling(con, NODE, STANDARD, "accepted", "jo")

    seq = _seq(tmp_path / "mh2_seq.db")
    assert len(review_store.list_open_proposals(seq)) == 1
    # The ruling itself is last-writer-wins, like every other review write.
    assert review_store.get_candidate_ruling(seq, SOURCE_KEY, STANDARD)["ruled_by"] \
        == "jo"
    seq.close()


def test_unknown_node_is_refused_rather_than_written_under_a_bad_key(app_db):
    db = app_db
    with pytest.raises(ValueError):
        db.write_ruling(db.get_connection(), "NO-SUCH-NODE", STANDARD,
                        "accepted", "jane")


# ----------------------------------------------------------------------- #8

def test_sidebar_tally_counts_a_ruling(app_db):
    db = app_db
    con = db.get_connection()
    assert db._ruled_totals_by_stem(con) == {STEM: (0, 1)}

    db.write_ruling(con, NODE, STANDARD, "accepted", "jane")
    assert db._ruled_totals_by_stem(con) == {STEM: (1, 1)}


def test_sidebar_tally_survives_a_rebuild(app_db, tmp_path):
    """#8. The rebuild is simulated the way rebuild.py used to behave toward
    mh2.db -- the file is destroyed and built again from source, which is
    what used to take every ruling with it."""
    db = app_db
    db.write_ruling(db.get_connection(), NODE, STANDARD, "accepted", "jane")
    before = db._ruled_totals_by_stem(db.get_connection())
    assert before == {STEM: (1, 1)}

    db.get_connection.clear()
    (tmp_path / "mh2.db").unlink()
    _build_mh2(tmp_path / "mh2.db")

    after = db._ruled_totals_by_stem(db.get_connection())
    assert after == before, "the rebuild lost the reviewer's progress"


def test_suggested_pool_carries_the_ruling_after_a_rebuild(app_db, tmp_path):
    db = app_db
    db.write_ruling(db.get_connection(), NODE, STANDARD, "accepted", "jane")

    db.get_connection.clear()
    db.suggested_pool.clear()
    (tmp_path / "mh2.db").unlink()
    _build_mh2(tmp_path / "mh2.db")

    rows = db.suggested_pool(db.get_connection(), 1)
    assert len(rows) == 1
    assert rows[0]["ruled"] is True
    assert rows[0]["ruled_status"] == "accepted"
    assert rows[0]["reviewed_by"] == "jane"
    assert rows[0]["reviewed_at"]


def test_unruled_candidate_reads_as_unruled(app_db):
    db = app_db
    rows = db.suggested_pool(db.get_connection(), 1)
    assert len(rows) == 1
    assert rows[0]["ruled"] is False
    assert rows[0]["ruled_status"] is None
    assert rows[0]["reviewed_by"] is None


def test_rejected_ruling_records_what_the_reviewer_was_looking_at(app_db, tmp_path):
    """A rejection raises no tag_proposal, so the ruling row is the only
    record that this candidate was ever considered. Without an anchor it
    could be reported later only as a standard_id and an opaque source_key.
    """
    db = app_db
    db.write_ruling(db.get_connection(), NODE, STANDARD, "rejected", "jane")

    seq = _seq(tmp_path / "mh2_seq.db")
    row = review_store.get_candidate_ruling(seq, SOURCE_KEY, STANDARD)
    assert row["node_id_seen"] == NODE
    assert row["node_text_seen"] == NODE_TEXT
    assert row["ladder_file_seen"] == LADDER
    seq.close()


def test_accepted_ruling_records_the_same_anchor(app_db, tmp_path):
    db = app_db
    db.write_ruling(db.get_connection(), NODE, STANDARD, "accepted", "jane")

    seq = _seq(tmp_path / "mh2_seq.db")
    row = review_store.get_candidate_ruling(seq, SOURCE_KEY, STANDARD)
    assert (row["node_id_seen"], row["node_text_seen"], row["ladder_file_seen"]) \
        == (NODE, NODE_TEXT, LADDER)
    seq.close()


def test_read_paths_survive_a_rebuild_and_reconcile_against_a_real_ruling(
        app_db, tmp_path):
    """#8, made non-trivial.

    Driven by a REJECTION on purpose: it raises no tag_proposal, so the
    ruling row is the only thing carrying it and the three rerouted read
    paths are the only way it can show up. The sequence mirrors
    rebuild.py's real order -- destroy and rebuild mh2.db, swap, then run
    reconcile_review against what is now serving.

    Literal predicate for every count below: the one-candidate fixture in
    `_build_mh2`, so total is 1 and ruled moves 0 -> 1. No number here is
    derived from real data.
    """
    db = app_db
    con = db.get_connection()
    assert db._ruled_totals_by_stem(con) == {STEM: (0, 1)}
    assert db.known_reviewers(con) == []

    db.write_ruling(con, NODE, STANDARD, "rejected", "debbie")

    assert db._ruled_totals_by_stem(con) == {STEM: (1, 1)}
    db.suggested_pool.clear()
    assert [r["ruled_status"] for r in db.suggested_pool(con, 1)] == ["rejected"]
    assert db.known_reviewers(con) == ["debbie"]

    # The rebuild: mh2.db is destroyed and built again from source.
    db.get_connection.clear()
    db.suggested_pool.clear()
    (tmp_path / "mh2.db").unlink()
    _build_mh2(tmp_path / "mh2.db")

    # ... then reconcile_review, which rebuild.py runs after the swap. It is
    # the one step that mutates mh2_seq.db, so the tally has to hold across
    # it and not merely across the rebuild.
    proc = subprocess.run(
        [sys.executable, "-m", "mh2.reconcile_review",
         "--db", str(tmp_path / "mh2.db"),
         "--seq-db", str(tmp_path / "mh2_seq.db"),
         "--out", str(tmp_path)],
        capture_output=True, text=True, cwd=str(REPO), timeout=120)
    assert proc.returncode == 0, proc.stderr

    con = db.get_connection()
    assert db._ruled_totals_by_stem(con) == {STEM: (1, 1)}, \
        "the rebuild + reconcile lost the reviewer's progress"
    assert [r["ruled_status"] for r in db.suggested_pool(con, 1)] == ["rejected"]
    assert db.known_reviewers(con) == ["debbie"]

    # The anchor survives too -- it is what makes a stale row reportable.
    seq = _seq(tmp_path / "mh2_seq.db")
    row = review_store.get_candidate_ruling(seq, SOURCE_KEY, STANDARD)
    assert row["ladder_file_seen"] == LADDER
    assert row["node_text_seen"] == NODE_TEXT
    # And reconcile_review left the ruling itself alone, as it does every
    # other review table.
    assert row["ruling"] == "rejected" and row["ruled_by"] == "debbie"
    seq.close()


def test_known_reviewers_survives_a_rebuild(app_db, tmp_path):
    db = app_db
    db.write_ruling(db.get_connection(), NODE, STANDARD, "accepted", "jane")
    assert db.known_reviewers(db.get_connection()) == ["jane"]

    db.get_connection.clear()
    (tmp_path / "mh2.db").unlink()
    _build_mh2(tmp_path / "mh2.db")

    assert db.known_reviewers(db.get_connection()) == ["jane"]
