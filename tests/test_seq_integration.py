"""Integration checks for the sequencer v1 prototype (contract §9.3), run after
R, W and F merged.

- Parity (guardrail): 200 random placement lists; mh2.seq_guardrail.compute ==
  the JS computeGuardrail (node -e over seq_static/app.js).
- Parity (compare): 20 random G2 key sets; seq_read.build_compare == the JS
  assembleCompare fed compare_column outputs.
- Round trip: everything export_seq_demo embeds equals what seq_service's GET
  functions return for the same DBs.
- Route table: tests/seq_http_harness.py serves exactly seq_api.py's routes.
- Auth through the harness (seq_api's own require_writer/current_writer): with
  MH2_AUTH_USERS set, /seq/, /seq/assets/app.js and /api/seq/slice are 401
  without credentials, and a write is attributed to the Basic user even if the
  body claims otherwise.
- Shape parity DemoApi vs service over a whole scenario, and the live HTTP
  scenario: tests/js/seq_e2e_test.js (run here when node is available).

Real data is copied to a temp dir; the real DB files are never opened for writing.
    MH2_DATA_DIR=... python3 tests/test_seq_integration.py
"""
from __future__ import annotations

import ast
import atexit
import base64
import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

import config  # noqa: E402
from mh2 import seq_guardrail, seq_read, seq_service  # noqa: E402

APP_JS = ROOT / "seq_static" / "app.js"
_TMP = []
_CACHE = {}


class Skip(Exception):
    pass


def _skip(msg):
    """pytest.skip under pytest, else our own Skip (the __main__ runner prints SKIP)."""
    if "pytest" in sys.modules and "_pytest" in sys.modules:
        import pytest
        pytest.skip(msg)
    raise Skip(msg)


def _cleanup():
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)
    _TMP.clear()


atexit.register(_cleanup)     # under pytest too (real-DB copies are ~150 MB)


def _tmpdir():
    d = tempfile.mkdtemp(prefix="seqint_")
    _TMP.append(d)
    return Path(d)


def _copies():
    """(ctx, mh2_path) over temp copies of the real DBs (cached per run)."""
    if "ctx" not in _CACHE:
        if not Path(config.DB).exists():
            _skip(f"no real mh2.db at {config.DB}")
        d = _tmpdir()
        shutil.copy(config.DB, d / "mh2.db")
        if Path(config.SEQ_DB).exists():
            shutil.copy(config.SEQ_DB, d / "mh2_seq.db")
        _CACHE["ctx"] = seq_service.Ctx(d / "mh2.db", d / "mh2_seq.db")
    return _CACHE["ctx"]


def _node(script: str, data) -> object:
    node = shutil.which("node")
    if not node:
        _skip("node not installed")
    src = ("const A = require(%s); let s=''; process.stdin.on('data', d => s += d);"
           "process.stdin.on('end', () => { const IN = JSON.parse(s); const OUT = (%s);"
           " process.stdout.write(JSON.stringify(OUT)); });") % (json.dumps(str(APP_JS)), script)
    r = subprocess.run([node, "-e", src], input=json.dumps(data), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# ------------------------------------------------------------------ parity

def test_guardrail_parity_200_random_lists():
    rng = random.Random(20260930)
    cals = ["deep", "functional", "illuminating", None]
    lists = []
    for _ in range(200):
        n = rng.randint(0, 12)
        lists.append([{"calibration": rng.choice(cals),
                       "period_estimate": rng.choice([None] + [q / 4 for q in range(0, 21)])}
                      for _ in range(n)])
    js = _node("IN.map(A.computeGuardrail)", lists)
    py = [seq_guardrail.compute(pl) for pl in lists]
    # JSON round trip on the Python side too (2.0 vs 2 are equal after json.loads)
    py = json.loads(json.dumps(py))
    bad = [i for i, (a, b) in enumerate(zip(js, py)) if a != b]
    assert not bad, (bad[:3], lists[bad[0]], js[bad[0]], py[bad[0]])


def test_compare_parity_20_random_g2_sets():
    ctx = _copies()
    con = seq_read.connect_ro(ctx.mh2_path)
    try:
        sl = seq_read.build_slice(con, "2")
        keys = [n["source_key"] for ss in sl["super_stems"] for st in ss["stems"]
                for c in st["concept_skills"] for n in c["nodes"]]
        rng = random.Random(7)
        sets = [rng.sample(keys, rng.randint(1, 4)) for _ in range(20)]
        fdefs = seq_read.compare_field_defs(con)
        cases = [{"cols": [seq_read.compare_column(con, k, "2") for k in ks], "fdefs": fdefs} for ks in sets]
        py = json.loads(json.dumps([seq_read.build_compare(con, ks, "2") for ks in sets]))
    finally:
        con.close()
    js = _node("IN.map(c => A.assembleCompare(c.cols, c.fdefs, '2'))", cases)
    for i, (a, b) in enumerate(zip(js, py)):
        assert a == b, (i, sets[i], [r["key"] for r in a["rows"]][:10], [r["key"] for r in b["rows"]][:10])
    assert any(len(s) >= 2 for s in sets)


# -------------------------------------------------------------- round trip

def test_export_round_trip_equals_service_gets():
    import export_seq_demo as X
    ctx = _copies()
    p = X.build_payload_from_db(ctx.mh2_path, "2", True, ctx.seq_path)
    X.validate_payload(p)
    assert p["slice"] == seq_service.get_slice(ctx, "2")
    assert p["grades"] == seq_service.grades(ctx)
    assert p["sequence"] == seq_service.get_sequence(ctx, "2")
    keys = X.slice_keys(p["slice"])
    for k in keys:
        assert p["drawers"][k] == seq_service.get_node(ctx, k, "2"), k
    con = seq_read.connect_ro(ctx.mh2_path)
    try:
        assert p["field_defs"] == seq_read.compare_field_defs(con)
        for k in keys:
            assert p["compare_columns"][k] == seq_read.compare_column(con, k, "2"), k
    finally:
        con.close()
    # without --with-sequence (the committed demo): same as the service on an empty placement DB
    q = X.build_payload_from_db(ctx.mh2_path, "2", False)
    empty = seq_service.Ctx(ctx.mh2_path, _tmpdir() / "empty_seq.db")
    assert q["slice"] == seq_service.get_slice(empty, "2")
    assert q["grades"] == seq_service.grades(empty)
    assert q["sequence"] == seq_service.get_sequence(empty, "2")
    assert set(q) - set(p) == set() and q["source"] == "mh2.db"


# ------------------------------------------------------ harness and routes

def _decorated_routes():
    tree = ast.parse((ROOT / "seq_api.py").read_text())
    out = set()
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for d in fn.decorator_list:
                if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                        and isinstance(d.func.value, ast.Name) and d.func.value.id == "router"):
                    out.add((d.func.attr.upper(), d.args[0].value, fn.name))
    return out


def test_harness_serves_exactly_seq_api_routes():
    import seq_http_harness as H
    app = H.App(H.load_seq_api())
    assert set(app.route_table()) == _decorated_routes()
    assert len(app.route_table()) == 33
    # the stand-ins were only installed for the import
    assert getattr(sys.modules.get("fastapi"), "FastAPI", None) is not H.FastAPI
    assert getattr(sys.modules.get("pydantic"), "BaseModel", None) is not H.BaseModel


def _auth(u, p):
    return {"Authorization": "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()}


def test_auth_on_through_harness_401_and_attribution():
    import seq_http_harness as H
    ctx = seq_service.Ctx(_copies().mh2_path, _tmpdir() / "auth_seq.db")
    old = os.environ.get("MH2_AUTH_USERS")
    os.environ["MH2_AUTH_USERS"] = "a:x,b:y"
    try:
        mod = H.load_seq_api()
        mod.CTX = ctx
        app = H.App(mod)
        for path in ("/seq/", "/seq/assets/app.js", "/api/seq/slice?grade=2", "/api/seq/whoami"):
            st, hdrs, _ = app.handle("GET", path, {}, b"")
            assert st == 401 and hdrs.get("WWW-Authenticate") == "Basic", (path, st)
        st, _, _ = app.handle("GET", "/api/seq/slice?grade=2", _auth("a", "wrong"), b"")
        assert st == 401
        st, _, body = app.handle("GET", "/api/seq/whoami", {**_auth("b", "y"), "X-MH2-User": "zed"}, b"")
        assert st == 200 and json.loads(body) == {"user": "b", "auth_enabled": True, "source": "basic"}
        st, _, body = app.handle("POST", "/api/seq/sequences", _auth("a", "x"),
                                 json.dumps({"grade": "2", "title": "G2", "created_by": "evil"}).encode())
        assert st == 200, body
        v = json.loads(body)["sequence"]
        assert v["sequence"]["created_by"] == "a" and v["sequence"]["owner"] == "a"
        st, _, body = app.handle("POST", f"/api/seq/sequences/{v['sequence']['sequence_id']}/modules", _auth("a", "x"),
                                 json.dumps({"expected_rev": v["sequence"]["rev"], "title": "M1"}).encode())
        r = json.loads(body)
        key = next(n["source_key"] for ss in seq_service.get_slice(ctx, "2")["super_stems"] for st_ in ss["stems"]
                   for c in st_["concept_skills"] for n in c["nodes"] if n["owed"])
        st, _, body = app.handle("POST", "/api/seq/placements", {**_auth("a", "x"), "X-MH2-User": "mallory"},
                                 json.dumps({"expected_rev": r["sequence"]["sequence"]["rev"],
                                             "sequence_id": v["sequence"]["sequence_id"],
                                             "module_id": r["result"]["module_id"], "source_key": key,
                                             "placed_by": "evil", "placed_at": "1999"}).encode())
        assert st == 200, body
        pl = json.loads(body)["sequence"]["modules"][0]["slots"][0]["placements"][0]
        assert pl["placed_by"] == "a" and pl["placed_at"] != "1999"
    finally:
        if old is None:
            os.environ.pop("MH2_AUTH_USERS", None)
        else:
            os.environ["MH2_AUTH_USERS"] = old


def test_harness_errors_follow_the_contract_shapes():
    import seq_http_harness as H
    mod = H.load_seq_api()
    mod.CTX = seq_service.Ctx(_copies().mh2_path, _tmpdir() / "err_seq.db")
    app = H.App(mod)
    st, _, b = app.handle("GET", "/api/seq/slice?grade=Z9", {}, b"")
    assert st == 404 and json.loads(b)["detail"]["error"] == "not_found"
    st, _, b = app.handle("GET", "/api/seq/slice", {}, b"")
    assert st == 422 and isinstance(json.loads(b)["detail"], list)
    st, _, b = app.handle("GET", "/api/seq/sequences/1/events?limit=500", {}, b"")
    assert st == 422
    st, _, b = app.handle("GET", "/api/seq/compare?grade=2&keys=a,b,c,d,e", {}, b"")
    assert st == 422 and json.loads(b)["detail"]["error"] == "invalid"
    st, _, _ = app.handle("GET", "/api/seq/nope", {}, b"")
    assert st == 404
    st, hdrs, _ = app.handle("GET", "/seq", {}, b"")
    assert st == 307 and hdrs["Location"] == "seq/"


# ------------------------------------------------------------ node e2e

def test_node_e2e_scenario():
    node = shutil.which("node")
    if not node:
        _skip("node not installed")
    if not Path(config.DB).exists():
        _skip("no real mh2.db")
    r = subprocess.run([node, str(ROOT / "tests" / "js" / "seq_e2e_test.js")], cwd=ROOT,
                       capture_output=True, text=True, timeout=900,
                       env={**os.environ, "PYTHON": sys.executable})
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    assert "all e2e checks passed" in r.stdout


def _main():
    failed = 0
    try:
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
    finally:
        _cleanup()
    print(f"\n{failed} failed" if failed else "\nall passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
