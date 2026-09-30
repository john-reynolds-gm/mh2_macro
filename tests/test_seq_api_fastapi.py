"""seq_api.py under the REAL FastAPI (TestClient). SKIPs when FastAPI/httpx are
not importable (the sandbox); on John's Mac it is the first real run of the
sequencer's FastAPI layer, inside the normal `pytest -q`.

Covers contract §9.6 items 1-3 as far as a TestClient can: static routes and
the /seq redirect, reads, a short write scenario, the 409 stale revision with
the current view, FastAPI's own 422 list, and auth on (401 on the page, an
asset and the API; the writer is the Basic user even if the body claims
otherwise). Data: temp copies of config.DB / config.SEQ_DB.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402


class Skip(Exception):
    pass


def _skip(msg):
    if "_pytest" in sys.modules:
        import pytest
        pytest.skip(msg)
    raise Skip(msg)


_STATE = {}


def _client():
    if "client" in _STATE:
        return _STATE["client"], _STATE["api"]
    try:
        from fastapi.testclient import TestClient
    except Exception as e:  # noqa: BLE001  (ImportError, or httpx missing)
        _skip(f"FastAPI TestClient not importable: {e}")
    if not Path(config.DB).exists():
        _skip(f"no mh2.db at {config.DB}")
    import seq_api
    from mh2 import seq_service
    d = Path(tempfile.mkdtemp(prefix="seqfa_"))
    atexit.register(shutil.rmtree, d, True)
    shutil.copy(config.DB, d / "mh2.db")
    seq_api.CTX = seq_service.Ctx(d / "mh2.db", d / "mh2_seq.db")   # routes read the module global
    _STATE["client"], _STATE["api"] = TestClient(seq_api.app), seq_api
    return _STATE["client"], seq_api


_ORIG_AUTH = os.environ.get("MH2_AUTH_USERS")


def _no_auth():
    os.environ.pop("MH2_AUTH_USERS", None)


def _restore_auth():
    if _ORIG_AUTH is None:
        os.environ.pop("MH2_AUTH_USERS", None)
    else:
        os.environ["MH2_AUTH_USERS"] = _ORIG_AUTH


def test_static_routes_and_redirect():
    c, _ = _client()
    _no_auth()
    try:
        _static(c)
    finally:
        _restore_auth()


def _static(c):
    r = c.get("/seq", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "seq/"
    r = c.get("/seq/")
    assert r.status_code == 200 and '<script src="assets/app.js"></script>' in r.text
    r = c.get("/seq/assets/app.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert c.get("/seq/assets/app.css").status_code == 200
    assert c.get("/seq/assets/index.html").status_code == 404


def test_reads_and_a_write_scenario():
    c, _ = _client()
    _no_auth()
    try:
        _scenario(c)
    finally:
        _restore_auth()


def _scenario(c):
    who = c.get("/api/seq/whoami", headers={"X-MH2-User": "amy"}).json()
    assert who == {"user": "amy", "auth_enabled": False, "source": "dev_header"}
    g = c.get("/api/seq/grades").json()
    assert any(x["grade"] == "2" for x in g["grades"])
    sl = c.get("/api/seq/slice", params={"grade": "2"}).json()
    nodes = [n for ss in sl["super_stems"] for st in ss["stems"] for cs in st["concept_skills"] for n in cs["nodes"]]
    owed = [n for n in nodes if n["owed"]]
    off = [n for n in nodes if n["requires_confirm"]]
    k = owed[0]["source_key"]
    assert c.get("/api/seq/node/" + k.replace(":", "%3A"), params={"grade": "2"}).json()["source_key"] == k
    cmp = c.get("/api/seq/compare", params={"grade": "2", "keys": ",".join(n["source_key"] for n in owed[:3])}).json()
    assert len(cmp["columns"]) == 3
    # FastAPI's own validation error is a list
    r = c.post("/api/seq/sequences", json={"grade": "2"})
    assert r.status_code == 422 and isinstance(r.json()["detail"], list)
    r = c.post("/api/seq/sequences", json={"grade": "2", "title": "Grade 2 sequence", "created_by": "evil"},
               headers={"X-MH2-User": "amy"})
    assert r.status_code == 200, r.text
    v = r.json()["sequence"]
    sid = v["sequence"]["sequence_id"]
    assert v["sequence"]["created_by"] == "amy"
    v = c.post(f"/api/seq/sequences/{sid}/modules", json={"expected_rev": v["sequence"]["rev"], "title": "M1"}).json()
    mid = v["result"]["module_id"]
    v = c.post("/api/seq/placements", json={"expected_rev": v["sequence"]["sequence"]["rev"], "sequence_id": sid,
                                            "module_id": mid, "source_key": k, "placed_by": "evil"}).json()
    p = v["sequence"]["modules"][0]["slots"][0]["placements"][0]
    assert p["placed_by"] == "local" and p["source_key"] == k
    if off:
        r = c.post("/api/seq/placements", json={"expected_rev": v["sequence"]["sequence"]["rev"], "sequence_id": sid,
                                                "module_id": mid, "source_key": off[0]["source_key"]})
        assert r.status_code == 422 and r.json()["detail"]["error"] == "confirm_off_grade_required"
    rev = v["sequence"]["sequence"]["rev"]
    r = c.patch(f"/api/seq/placements/{p['placement_id']}", json={"expected_rev": p["rev"], "calibration": "deep",
                                                                  "period_estimate": 1.5})
    assert r.status_code == 200, r.text
    v = r.json()["sequence"]
    assert v["sequence"]["rev"] == rev and v["guardrail"]["counts"]["deep"] == 1
    # null clears, absent keeps
    r = c.patch(f"/api/seq/placements/{p['placement_id']}",
                json={"expected_rev": v["modules"][0]["slots"][0]["placements"][0]["rev"], "period_estimate": None})
    pl = r.json()["sequence"]["modules"][0]["slots"][0]["placements"][0]
    assert pl["period_estimate"] is None and pl["calibration"] == "deep"
    # stale structural write: 409 with the current view
    r = c.post(f"/api/seq/sequences/{sid}/modules", json={"expected_rev": rev - 1, "title": "x"})
    assert r.status_code == 409
    det = r.json()["detail"]
    assert det["error"] == "stale_revision" and det["current_rev"] == rev and det["sequence"]["sequence"]["rev"] == rev
    r = c.delete(f"/api/seq/modules/{mid}", params={"expected_rev": rev})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "module_not_empty"
    r = c.delete(f"/api/seq/placements/{p['placement_id']}", params={"expected_rev": rev})
    assert r.status_code == 200 and r.json()["sequence"]["placed_index"] == {}
    ev = c.get(f"/api/seq/sequences/{sid}/events", params={"limit": 5}).json()
    assert ev["events"][0]["action"] == "remove"
    assert c.get(f"/api/seq/sequences/{sid}/events", params={"limit": 500}).status_code == 422
    # views
    vw = c.post("/api/seq/views", json={"grade": "2", "name": "Mine", "state": {"pairings": True}}).json()
    assert c.post("/api/seq/views", json={"grade": "2", "name": "Mine", "state": {}}).status_code == 409
    assert c.put(f"/api/seq/views/{vw['view_id']}", json={"name": "Renamed"}).json()["name"] == "Renamed"
    assert c.delete(f"/api/seq/views/{vw['view_id']}").json() == {"deleted": vw["view_id"]}


def test_auth_on_gates_everything_and_attributes_the_basic_user():
    c, _ = _client()
    os.environ["MH2_AUTH_USERS"] = "a:x,b:y"
    try:
        for path in ("/seq/", "/seq/assets/app.js", "/api/seq/slice?grade=2", "/api/seq/whoami"):
            r = c.get(path)
            assert r.status_code == 401 and r.headers.get("www-authenticate", "").lower().startswith("basic"), path
        assert c.get("/api/seq/whoami", auth=("a", "wrong")).status_code == 401
        who = c.get("/api/seq/whoami", auth=("b", "y"), headers={"X-MH2-User": "zed"}).json()
        assert who == {"user": "b", "auth_enabled": True, "source": "basic"}
        r = c.post("/api/seq/sequences", json={"grade": "3", "title": "G3", "created_by": "evil"},
                   auth=("a", "x"), headers={"X-MH2-User": "mallory"})
        assert r.status_code == 200, r.text
        assert r.json()["sequence"]["sequence"]["created_by"] == "a"
    finally:
        _restore_auth()


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok", name)
            except Skip as e:
                print(f"SKIP {name}: {e}")
            except Exception:  # noqa: BLE001
                import traceback
                failed += 1
                print("FAIL", name)
                traceback.print_exc()
    sys.exit(1 if failed else 0)
