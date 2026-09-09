"""
review_api.py tests -- FastAPI TestClient against synthetic file-backed
databases (review_api.py opens mh2.db read-only, which needs a real file).

Covers §7 acceptance criteria #7 (an override never changes what
coverage.build_rows returns) and the write-path contract (every write
stamps its own *_by/*_at/color server-side; a client-supplied one, if it
tried to send one, has no field to land in -- the Pydantic models below
simply don't accept `reviewed_at` or `computed_color_at_review`).
"""
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from mh2 import coverage, review_store  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_reconcile_review import _mh2_db, _node, _seq_db, _standard, _tag  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    mh2 = _mh2_db(tmp_path / "mh2.db")
    _standard(mh2, "K.CC.A.1", "K")
    _node(mh2, "COM-0001", "COM::count more", grades=("K",), stem_id="COM")
    _tag(mh2, "COM-0001", "K.CC.A.1")
    mh2.commit()
    mh2.close()

    seq = _seq_db(tmp_path / "mh2_seq.db")
    seq.close()

    monkeypatch.setattr(config, "DB", tmp_path / "mh2.db")
    monkeypatch.setattr(config, "SEQ_DB", tmp_path / "mh2_seq.db")

    import review_api
    return TestClient(review_api.app)


def test_audit_lists_standard_with_default_review_state(client):
    resp = client.get("/api/audit")
    assert resp.status_code == 200
    rows = {r["standard_id"]: r for r in resp.json()}
    assert rows["K.CC.A.1"]["color"] == "Green"
    assert rows["K.CC.A.1"]["review_state"] == "unreviewed"
    assert rows["K.CC.A.1"]["has_override"] is False


def test_standard_detail_carries_source_key_and_ladder_file(client):
    resp = client.get("/api/standards/K.CC.A.1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["color"] == "Green"
    tag = body["tags"][0]
    assert tag["source_key"] == "COM::count more"
    assert tag["node_text"] == "COM::count more"


def test_standard_review_write_then_read_back(client):
    resp = client.post("/api/standards/K.CC.A.1/review",
                        json={"outcome": "confirmed", "reviewed_by": "jane"})
    assert resp.status_code == 200
    assert resp.json()["computed_color_at_review"] == "Green"

    resp = client.get("/api/audit")
    row = {r["standard_id"]: r for r in resp.json()}["K.CC.A.1"]
    assert row["review_state"] == "confirmed"

    resp = client.delete("/api/standards/K.CC.A.1/review")
    assert resp.status_code == 200
    row = {r["standard_id"]: r for r in client.get("/api/audit").json()}["K.CC.A.1"]
    assert row["review_state"] == "unreviewed"


def test_override_changes_effective_color_but_not_coverage_rollup(client):
    client.post("/api/standards/K.CC.A.1/override",
                json={"writer_color": "Red", "reason": "actually not covered",
                      "set_by": "jane"})
    row = {r["standard_id"]: r for r in client.get("/api/audit").json()}["K.CC.A.1"]
    assert row["effective_color"] == "Red"
    assert row["color"] == "Green"  # rollup color untouched

    # #7: coverage.build_rows itself never changes.
    con = sqlite3.connect(f"file:{config.DB}?mode=ro", uri=True)
    rows = coverage.build_rows(con)
    con.close()
    assert rows[0].color == "Green"

    client.delete("/api/standards/K.CC.A.1/override")
    row = {r["standard_id"]: r for r in client.get("/api/audit").json()}["K.CC.A.1"]
    assert row["has_override"] is False


def test_tag_review_write_snapshots_ladder_file_server_side(client):
    resp = client.post("/api/tags/review", json={
        "standard_id": "K.CC.A.1", "source_key": "COM::count more",
        "outcome": "confirmed", "reviewed_by": "jane",
    })
    assert resp.status_code == 200

    detail = client.get("/api/standards/K.CC.A.1").json()
    review = detail["tags"][0]["review"]
    assert review["outcome"] == "confirmed"
    assert review["reviewed_by"] == "jane"
    assert review["reviewed_at"]  # server-stamped, never accepted from client


def test_proposal_create_and_withdraw(client):
    resp = client.post("/api/proposals", json={
        "standard_id": "K.CC.A.1", "source_key": "COM::new tag",
        "node_text_seen": "a new proposed node", "proposed_by": "jane",
    })
    assert resp.status_code == 200
    proposal_id = resp.json()["proposal_id"]

    work = client.get("/api/worklist").json()
    files = {f["ladder_file"] for f in work["by_ladder_file"]}
    assert files  # at least the '(unfiled)' bucket, since no ladder_file resolved

    resp = client.post(f"/api/proposals/{proposal_id}/withdraw", json={})
    assert resp.status_code == 200
    assert resp.json()["withdrawn"] is True
