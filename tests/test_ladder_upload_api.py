"""
review_api.py's Ladder Files routes and rebuild worker
(docs/brief_ladder_uploads.md). Numbers are the brief's §9 acceptance
criteria; #1-#6 and #8 are covered at the store level in
test_ladder_store.py and only exercised end-to-end here.

  #7  a failed rebuild after an upload leaves the previous mh2.db serving,
      and /api/rebuild/status reports the failing step and the output tail
  #9  every new route returns 401 without credentials when MH2_AUTH_USERS
      is set

Every test replaces review_api.rebuild_worker. The real one runs
scripts/rebuild.py against whatever config.DATA the subprocess sees -- the
real data tree unless MH2_DATA_DIR is set -- so a commit in a test must
never reach it.
"""
import io
import os
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from mh2 import ladder_store  # noqa: E402
from test_ladder_store import (DRAFTED, DRAFTED_ON_DISK, NODES, OTHER, OTHER_FILE,  # noqa: E402
                               UNDRAFTED, docx_bytes, point_config_at,
                               seed_tree, _rebuild_tree)

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
OK_CMD = [sys.executable, "-c", "pass"]


@pytest.fixture
def api(tmp_path, monkeypatch):
    data = seed_tree(tmp_path / "data")
    point_config_at(monkeypatch, data)
    monkeypatch.delenv("MH2_AUTH_USERS", raising=False)
    import review_api
    worker = review_api.RebuildWorker(rebuild_cmd=OK_CMD, render_cmd=OK_CMD,
                                      lock_retry_seconds=0.05)
    monkeypatch.setattr(review_api, "rebuild_worker", worker)
    return review_api, TestClient(review_api.app), data


def _validate(client, stem_id, body: bytes, name="Counting FINAL.docx"):
    return client.post(f"/api/ladders/{stem_id}/validate",
                       files={"file": (name, io.BytesIO(body), DOCX)})


def _upload(client, tmp_path, stem_id, nodes, **commit):
    r = _validate(client, stem_id, docx_bytes(nodes, tmp_path))
    assert r.status_code == 200, r.text
    c = client.post(f"/api/ladders/{stem_id}/commit", json={"token": r.json()["token"], **commit})
    assert c.status_code == 200, c.text
    return c.json()


# ----------------------------------------------------------- end-to-end flow

def test_validate_then_commit_then_download(api, tmp_path):
    review_api, client, _ = api
    body = docx_bytes(NODES + ["Count to 1000"], tmp_path)
    r = _validate(client, DRAFTED, body)
    assert r.status_code == 200
    p = r.json()
    assert p["node_count"] == 4
    assert p["diff"]["matched"] == 3 and p["diff"]["new"] == 1 and p["diff"]["removed"] == 0
    assert p["canonical_name"] == DRAFTED_ON_DISK

    c = client.post(f"/api/ladders/{DRAFTED}/commit", json={"token": p["token"], "note": "added 1000"})
    assert c.status_code == 200
    up = c.json()["upload"]
    assert up["uploaded_by"] == "local" and up["note"] == "added 1000" and up["is_live"] == 1
    assert review_api.rebuild_worker.wait_idle(10)

    d = client.get(f"/api/ladders/{DRAFTED}/file")
    assert d.status_code == 200 and d.content == body
    assert DRAFTED_ON_DISK.replace(" ", "%20") in d.headers["content-disposition"] \
        or DRAFTED_ON_DISK in d.headers["content-disposition"]

    rows = {r["stem_id"]: r for r in client.get("/api/ladders").json()}
    assert rows[DRAFTED]["status"] == "uploaded" and rows[DRAFTED]["version_count"] == 1
    assert rows[OTHER]["status"] == "repo"
    assert rows[UNDRAFTED]["status"] == "empty"


def test_rejections_are_422_with_a_message_and_show_as_rejected(api):
    _, client, _ = api
    r = _validate(client, DRAFTED, b"not a docx", name="notes.txt")
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "not_docx"
    rows = {r["stem_id"]: r for r in client.get("/api/ladders").json()}
    assert rows[DRAFTED]["status"] == "rejected"


def test_unknown_stem_is_404(api, tmp_path):
    _, client, _ = api
    assert _validate(client, "NOPE", docx_bytes(NODES, tmp_path)).status_code == 404
    assert client.get("/api/ladders/NOPE/file").status_code == 404
    assert client.get("/api/ladders/NOPE/versions").status_code == 404


def test_commit_with_orphans_needs_confirmation(api, tmp_path):
    _, client, _ = api
    from mh2 import review_store
    from mh2.ingest_ladders import source_key
    seq = ladder_store.connect()
    review_store.set_tag_review(seq, "K.CC.A.1", source_key(DRAFTED, NODES[1]), "confirmed", "amanda")
    seq.close()

    p = _validate(client, DRAFTED, docx_bytes([NODES[0], NODES[2]], tmp_path)).json()
    assert p["orphaned_review_count"] == 1
    c = client.post(f"/api/ladders/{DRAFTED}/commit", json={"token": p["token"]})
    assert c.status_code == 409
    c = client.post(f"/api/ladders/{DRAFTED}/commit",
                    json={"token": p["token"], "confirm_orphans": True})
    assert c.status_code == 200


def test_versions_download_and_revert(api, tmp_path):
    review_api, client, _ = api
    first = _upload(client, tmp_path, DRAFTED, NODES)["upload"]
    second = _upload(client, tmp_path, DRAFTED, NODES + ["Count to 1000"])["upload"]

    vs = client.get(f"/api/ladders/{DRAFTED}/versions").json()
    assert [v["upload_id"] for v in vs] == [second["upload_id"], first["upload_id"]]
    assert [v["is_live"] for v in vs] == [1, 0]

    old = client.get(f"/api/ladders/{DRAFTED}/versions/{first['upload_id']}/file")
    assert old.status_code == 200
    assert old.content == ladder_store.version_path(first).read_bytes()

    r = client.post(f"/api/ladders/{DRAFTED}/revert/{first['upload_id']}")
    assert r.status_code == 200
    assert client.get(f"/api/ladders/{DRAFTED}/file").content == old.content
    assert client.post(f"/api/ladders/{DRAFTED}/revert/99999").status_code == 404
    assert review_api.rebuild_worker.wait_idle(10)


def test_download_falls_back_to_the_repo_copy(api):
    _, client, data = api
    r = client.get(f"/api/ladders/{OTHER}/file")
    assert r.status_code == 200
    assert r.content == (data / "source/ladders" / OTHER_FILE).read_bytes()
    assert client.get(f"/api/ladders/{UNDRAFTED}/file").status_code == 404


def test_export_zip_holds_every_live_ladder_and_a_manifest(api, tmp_path):
    review_api, client, _ = api
    up = _upload(client, tmp_path, UNDRAFTED, ["Foo one"])["upload"]
    r = client.get("/api/ladders/export.zip")
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = sorted(zf.namelist())
    assert names == sorted([DRAFTED_ON_DISK, OTHER_FILE, f"MH2_PK5_{UNDRAFTED}.docx",
                            "manifest.csv"])
    manifest = zf.read("manifest.csv").decode()
    assert f"{UNDRAFTED}," in manifest and ",upload," in manifest and ",repo," in manifest
    assert zf.read(f"MH2_PK5_{UNDRAFTED}.docx") == ladder_store.version_path(up).read_bytes()
    # The temp file is removed once sent.
    import tempfile
    assert not list(Path(tempfile.gettempdir()).glob(".ladders_export_*"))
    assert review_api.rebuild_worker.wait_idle(10)


def test_files_page_is_served(api):
    _, client, _ = api
    r = client.get("/files")
    assert r.status_code == 200 and "Ladder Files" in r.text


def test_startup_purges_stale_pending_uploads(api, tmp_path):
    review_api, client, _ = api
    tok = _validate(client, DRAFTED, docx_bytes(NODES, tmp_path)).json()["token"]
    for p in ladder_store.pending_dir().glob(f"{tok}.*"):
        os.utime(p, (0, 0))
    with TestClient(review_api.app):          # runs the lifespan
        pass
    assert not list(ladder_store.pending_dir().glob(f"{tok}.*"))


# ----------------------------------------------------------- rebuild worker

def _script(tmp_path, name, body) -> list[str]:
    p = tmp_path / name
    p.write_text(body)
    return [sys.executable, str(p)]


def test_worker_reports_failure_with_step_and_tail(api, tmp_path):
    review_api, client, _ = api
    review_api.rebuild_worker.rebuild_cmd = _script(
        tmp_path, "fail.py",
        "import sys\nfor i in range(80): print('line', i)\n"
        "sys.exit('FAILED: ingesting ladders')\n")
    _upload(client, tmp_path, DRAFTED, NODES)
    assert review_api.rebuild_worker.wait_idle(10)
    s = client.get("/api/rebuild/status").json()
    assert s["state"] == "failed"
    assert s["failed_step"] == "rebuild: ingesting ladders"
    assert len(s["error_tail"]) == 50 and s["error_tail"][-1] == "FAILED: ingesting ladders"


def test_worker_waits_and_retries_while_the_lock_is_held(api, tmp_path):
    review_api, client, _ = api
    counter = tmp_path / "n"
    review_api.rebuild_worker.rebuild_cmd = _script(tmp_path, "locked.py", f"""
import sys, pathlib
p = pathlib.Path({str(counter)!r})
n = int(p.read_text()) if p.exists() else 0
p.write_text(str(n + 1))
if n < 2:
    sys.exit("Another rebuild is already running (lock held on x).")
""")
    _upload(client, tmp_path, DRAFTED, NODES)
    assert review_api.rebuild_worker.wait_idle(10)
    s = client.get("/api/rebuild/status").json()
    assert s["state"] == "idle" and s["last_success_actor"] == "local"
    assert counter.read_text() == "3"


def test_quick_uploads_collapse_into_few_rebuilds(api, tmp_path):
    review_api, client, _ = api
    log = tmp_path / "runs"
    review_api.rebuild_worker.rebuild_cmd = _script(tmp_path, "slow.py", f"""
import time
open({str(log)!r}, "a").write("x")
time.sleep(0.5)
""")
    for _ in range(4):
        _upload(client, tmp_path, DRAFTED, NODES, confirm_orphans=True)
    assert review_api.rebuild_worker.wait_idle(20)
    assert 1 <= len(log.read_text()) <= 2


def test_failed_real_rebuild_after_upload_leaves_previous_db_serving(tmp_path, monkeypatch):
    """#7 against the real scripts/rebuild.py: a data tree whose required
    inputs are present but empty clears pre-flight, assembles the upload,
    and fails in the first loader. mh2.db must be byte-identical afterwards
    and the status must name the step."""
    data = tmp_path / "data"
    point_config_at(monkeypatch, data)
    seed_tree(data)
    _rebuild_tree(data)
    monkeypatch.setenv("MH2_DATA_DIR", str(data))
    monkeypatch.delenv("MH2_AUTH_USERS", raising=False)
    import review_api
    worker = review_api.RebuildWorker()
    monkeypatch.setattr(review_api, "rebuild_worker", worker)
    client = TestClient(review_api.app)
    before = (data / "build/mh2.db").read_bytes()

    _upload(client, tmp_path, DRAFTED, NODES)
    assert worker.wait_idle(120)

    s = client.get("/api/rebuild/status").json()
    assert s["state"] == "failed"
    assert s["failed_step"] == "rebuild: loading standards + stem workbook"
    assert s["error_tail"]
    assert (data / "build/mh2.db").read_bytes() == before
    assert client.get("/api/ladders").status_code == 200     # still serving


# ------------------------------------------------------------------------ #9

NEW_ROUTES = [
    ("GET", "/files"), ("GET", "/api/ladders"), ("GET", "/api/ladders/export.zip"),
    ("POST", f"/api/ladders/{DRAFTED}/validate"), ("POST", f"/api/ladders/{DRAFTED}/commit"),
    ("GET", f"/api/ladders/{DRAFTED}/file"), ("GET", f"/api/ladders/{DRAFTED}/versions"),
    ("GET", f"/api/ladders/{DRAFTED}/versions/1/file"),
    ("POST", f"/api/ladders/{DRAFTED}/revert/1"), ("GET", "/api/rebuild/status"),
]


@pytest.mark.parametrize("method,path", NEW_ROUTES)
def test_every_new_route_requires_credentials_when_auth_is_on(api, monkeypatch, method, path):
    _, client, _ = api
    monkeypatch.setenv("MH2_AUTH_USERS", "jane:s3cret")
    r = client.request(method, path)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Basic"


def test_uploaded_by_is_the_authenticated_writer(api, monkeypatch, tmp_path):
    review_api, client, _ = api
    monkeypatch.setenv("MH2_AUTH_USERS", "jane:s3cret")
    client.auth = ("jane", "s3cret")
    up = _upload(client, tmp_path, DRAFTED, NODES)["upload"]
    assert up["uploaded_by"] == "jane"
    assert review_api.rebuild_worker.wait_idle(10)
    assert client.get("/api/rebuild/status").json()["last_success_actor"] == "jane"
