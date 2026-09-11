"""
HTTP Basic auth gate added for hosting (see docs/HOSTING.md).

Two states matter: MH2_AUTH_USERS unset (today's default everywhere except
the deployed container) must leave every route exactly as it always
behaved -- no login prompt, no regression to the tests in
test_review_api.py. MH2_AUTH_USERS set must gate every route, including
"/", behind valid per-writer credentials.
"""
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_reconcile_review import _mh2_db, _node, _seq_db, _standard, _tag  # noqa: E402


@pytest.fixture
def client_env(tmp_path, monkeypatch):
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
    return review_api, monkeypatch


def test_auth_disabled_when_env_unset(client_env):
    review_api, monkeypatch = client_env
    monkeypatch.delenv("MH2_AUTH_USERS", raising=False)
    client = TestClient(review_api.app)

    resp = client.get("/api/audit")
    assert resp.status_code == 200  # no credentials sent, still works


def test_auth_required_when_env_set(client_env):
    review_api, monkeypatch = client_env
    monkeypatch.setenv("MH2_AUTH_USERS", "jane:s3cret,amanda:othersecret")
    client = TestClient(review_api.app)

    resp = client.get("/api/audit")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Basic"


def test_auth_rejects_wrong_password(client_env):
    review_api, monkeypatch = client_env
    monkeypatch.setenv("MH2_AUTH_USERS", "jane:s3cret")
    client = TestClient(review_api.app)

    resp = client.get("/api/audit", auth=("jane", "wrong"))
    assert resp.status_code == 401


def test_auth_rejects_unknown_user(client_env):
    review_api, monkeypatch = client_env
    monkeypatch.setenv("MH2_AUTH_USERS", "jane:s3cret")
    client = TestClient(review_api.app)

    resp = client.get("/api/audit", auth=("mallory", "s3cret"))
    assert resp.status_code == 401


def test_auth_accepts_correct_credentials(client_env):
    review_api, monkeypatch = client_env
    monkeypatch.setenv("MH2_AUTH_USERS", "jane:s3cret,amanda:othersecret")
    client = TestClient(review_api.app)

    resp = client.get("/api/audit", auth=("jane", "s3cret"))
    assert resp.status_code == 200
    resp = client.get("/api/audit", auth=("amanda", "othersecret"))
    assert resp.status_code == 200


def test_auth_gates_index_route_too(client_env):
    review_api, monkeypatch = client_env
    monkeypatch.setenv("MH2_AUTH_USERS", "jane:s3cret")
    client = TestClient(review_api.app)

    resp = client.get("/")
    assert resp.status_code == 401
