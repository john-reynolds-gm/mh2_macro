"""
seq_http_harness.py -- run seq_api.py's real route functions over stdlib
http.server, without FastAPI (the sandbox has no FastAPI; this is for tests
and for poking at the API by hand).

How it avoids drifting from seq_api.py: it does NOT keep its own route table.
It imports seq_api.py itself with tiny stand-ins for `fastapi`, `fastapi.*`
and `pydantic` placed in sys.modules for the duration of the import. The
stand-in APIRouter records every `@router.<method>(path)` decorator, so the
route table, the parameter lists, the body models and the one-line route
bodies (`return _run(seq_service.<fn>, CTX, ...)`) are exactly the ones in
seq_api.py. The app-level `dependencies=[Depends(require_writer)]` gate runs
before every route, as in FastAPI.

What the stand-ins emulate (enough for this API, not a general FastAPI):
- path params `{name}` (percent-decoded), query params, JSON bodies bound to
  BaseModel subclasses, `Depends(...)` (recursive), `Request.headers`,
  `HTTPBasic(auto_error=False)` credentials, `Query(default, ge=, le=)`;
- pydantic-style validation of body fields by annotation (int, float, str,
  bool, list[str] with Field(min_length, max_length), dict, X | None),
  `extra="ignore"`, `model_dump(exclude_unset=, exclude=)`;
- validation errors -> 422 {"detail": [...]}, HTTPException -> its status and
  {"detail": detail} (+ headers), no route -> 404 {"detail": "Not Found"},
  wrong method -> 405, FileResponse, RedirectResponse (307 + Location).
Pydantic's full lax-mode coercion table is not reproduced; the first real
FastAPI run is still on John's Mac (docs/seq_v1_README.md).

Usage:
    MH2_DATA_DIR=... python3 tests/seq_http_harness.py [--port 8765] [--host 127.0.0.1]
        [--db PATH --seq-db PATH] [--test-hooks]
It prints "LISTENING http://HOST:PORT/" once ready; open /seq/ in a browser.
--port 0 picks a free port. --test-hooks enables POST /__harness/* helpers
used by tests/js/seq_e2e_test.js (never enable on shared data):
    /__harness/demo_payload   {"grade": "2", "with_sequence": false} -> DemoPayload
    /__harness/reword         {"node_id": "NS-BASE10-0011", "suffix": " today"} -> {old_key, new_key}
    /__harness/set_kind       {"node_id": "NS-COMP-ORDER-0012", "grade": "2", "kind": "state_extension"} -> {updated}
    /__harness/guardrail      {"placements": [...]} -> seq_guardrail.compute(...)
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import inspect
import json
import re
import sqlite3
import sys
import types
import typing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ------------------------------------------------------------ the stand-ins

class HTTPException(Exception):
    def __init__(self, status_code, detail=None, headers=None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.headers = headers or {}


class _Depends:
    def __init__(self, dependency=None):
        self.dependency = dependency


def Depends(dependency=None):  # noqa: N802 (FastAPI name)
    return _Depends(dependency)


class _Query:
    def __init__(self, default=..., ge=None, le=None, **kw):
        self.default, self.ge, self.le = default, ge, le


def Query(default=..., **kw):  # noqa: N802
    return _Query(default, **kw)


class Request:
    def __init__(self, headers):
        self.headers = headers


class HTTPBasicCredentials:
    def __init__(self, username, password):
        self.username, self.password = username, password


class HTTPBasic:
    def __init__(self, auto_error=True):
        self.auto_error = auto_error


class FileResponse:
    def __init__(self, path, media_type=None):
        self.path, self.media_type = Path(path), media_type


class RedirectResponse:
    def __init__(self, url, status_code=307):
        self.url, self.status_code = url, status_code


class APIRouter:
    def __init__(self, *a, **kw):
        self.routes = []   # (METHOD, path, fn)

    def _add(self, method, path):
        def deco(fn):
            self.routes.append((method, path, fn))
            return fn
        return deco

    def get(self, path, **kw): return self._add("GET", path)
    def post(self, path, **kw): return self._add("POST", path)
    def patch(self, path, **kw): return self._add("PATCH", path)
    def put(self, path, **kw): return self._add("PUT", path)
    def delete(self, path, **kw): return self._add("DELETE", path)


class FastAPI:
    def __init__(self, title=None, dependencies=None, **kw):
        self.title = title
        self.dependencies = list(dependencies or [])
        self.routes = []

    def include_router(self, router, **kw):
        self.routes.extend(router.routes)


def ConfigDict(**kw):  # noqa: N802
    return dict(kw)


class _FieldInfo:
    def __init__(self, default=..., min_length=None, max_length=None, **kw):
        self.default, self.min_length, self.max_length = default, min_length, max_length


def Field(default=..., **kw):  # noqa: N802
    return _FieldInfo(default, **kw)


class ValidationError(Exception):
    def __init__(self, errors):
        super().__init__(errors)
        self.errors = errors


_NONE = type(None)


def _coerce(value, tp, loc):
    """Validate/coerce one JSON value against a (resolved) annotation."""
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin in (typing.Union, types.UnionType):
        if value is None and _NONE in args:
            return None
        errs = []
        for a in args:
            if a is _NONE:
                continue
            try:
                return _coerce(value, a, loc)
            except ValidationError as e:
                errs.extend(e.errors)
        raise ValidationError(errs or [{"loc": loc, "msg": "invalid value", "type": "value_error"}])
    if tp is typing.Any:
        return value
    if origin is list:
        if not isinstance(value, list):
            raise ValidationError([{"loc": loc, "msg": "Input should be a valid list", "type": "list_type"}])
        return [_coerce(v, args[0], loc + [i]) if args else v for i, v in enumerate(value)]
    if origin is dict or tp is dict:
        if not isinstance(value, dict):
            raise ValidationError([{"loc": loc, "msg": "Input should be a valid dictionary", "type": "dict_type"}])
        if not args:
            return dict(value)
        return {_coerce(k, args[0], loc + [k]): _coerce(v, args[1], loc + [k]) for k, v in value.items()}
    if tp is bool:
        if isinstance(value, bool):
            return value
        if value in (0, 1) and not isinstance(value, float):
            return bool(value)
        if isinstance(value, str) and value.lower() in ("true", "false", "1", "0", "yes", "no"):
            return value.lower() in ("true", "1", "yes")
        raise ValidationError([{"loc": loc, "msg": "Input should be a valid boolean", "type": "bool_parsing"}])
    if tp is int:
        if isinstance(value, bool):
            raise ValidationError([{"loc": loc, "msg": "Input should be a valid integer", "type": "int_type"}])
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and re.fullmatch(r"\s*-?\d+\s*", value):
            return int(value)
        raise ValidationError([{"loc": loc, "msg": "Input should be a valid integer", "type": "int_parsing"}])
    if tp is float:
        if isinstance(value, bool):
            raise ValidationError([{"loc": loc, "msg": "Input should be a valid number", "type": "float_type"}])
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                pass
        raise ValidationError([{"loc": loc, "msg": "Input should be a valid number", "type": "float_parsing"}])
    if tp is str:
        if isinstance(value, str):
            return value
        raise ValidationError([{"loc": loc, "msg": "Input should be a valid string", "type": "string_type"}])
    return value


class BaseModel:
    model_config: dict = {}

    @classmethod
    def _fields(cls):
        hints = typing.get_type_hints(cls, globalns=sys.modules[cls.__module__].__dict__)
        out = {}
        for name, tp in hints.items():
            if name == "model_config":
                continue
            default = getattr(cls, name, ...)
            info = default if isinstance(default, _FieldInfo) else _FieldInfo(default)
            out[name] = (tp, info)
        return out

    def __init__(self, **data):
        errors, values, fields_set = [], {}, set()
        for name, (tp, info) in self._fields().items():
            loc = ["body", name]
            if name in data:
                try:
                    v = _coerce(data[name], tp, loc)
                    if isinstance(v, list) and info.min_length is not None and len(v) < info.min_length:
                        raise ValidationError([{"loc": loc, "msg": f"List should have at least {info.min_length} item",
                                                "type": "too_short"}])
                    if isinstance(v, list) and info.max_length is not None and len(v) > info.max_length:
                        raise ValidationError([{"loc": loc, "msg": f"List should have at most {info.max_length} items",
                                                "type": "too_long"}])
                    values[name] = v
                    fields_set.add(name)
                except ValidationError as e:
                    errors.extend(e.errors)
            elif info.default is ...:
                errors.append({"loc": loc, "msg": "Field required", "type": "missing"})
            else:
                values[name] = info.default
        if errors:
            raise ValidationError(errors)
        # extra="ignore": anything else in `data` is dropped
        object.__setattr__(self, "_values", values)
        object.__setattr__(self, "_fields_set", fields_set)
        for k, v in values.items():
            object.__setattr__(self, k, v)

    def model_dump(self, exclude_unset=False, exclude=None):
        ex = set(exclude or ())
        return {k: v for k, v in self._values.items()
                if k not in ex and (not exclude_unset or k in self._fields_set)}


def _stub_modules():
    fa = types.ModuleType("fastapi")
    for n in ("APIRouter", "Depends", "FastAPI", "HTTPException", "Query", "Request"):
        setattr(fa, n, globals()[n])
    resp = types.ModuleType("fastapi.responses")
    resp.FileResponse, resp.RedirectResponse = FileResponse, RedirectResponse
    sec = types.ModuleType("fastapi.security")
    sec.HTTPBasic, sec.HTTPBasicCredentials = HTTPBasic, HTTPBasicCredentials
    fa.responses, fa.security = resp, sec
    pd = types.ModuleType("pydantic")
    pd.BaseModel, pd.ConfigDict, pd.Field = BaseModel, ConfigDict, Field
    return {"fastapi": fa, "fastapi.responses": resp, "fastapi.security": sec, "pydantic": pd}


def load_seq_api(path: Path = ROOT / "seq_api.py"):
    """Import seq_api.py under the stand-ins; returns the module (name 'seq_api_harnessed')."""
    stubs = _stub_modules()
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("seq_api_harnessed", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["seq_api_harnessed"] = mod
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return mod


# ----------------------------------------------------------- the dispatcher

def _compile(path):
    names = re.findall(r"{(\w+)}", path)
    rx = "^" + re.sub(r"{\w+}", "([^/]+)", re.escape(path).replace(r"\{", "{").replace(r"\}", "}")) + "$"
    return re.compile(rx), names


class App:
    def __init__(self, mod):
        self.mod = mod
        self.routes = [(m, p, fn) + _compile(p) for m, p, fn in mod.app.routes]

    def route_table(self):
        return sorted((m, p, fn.__name__) for m, p, fn, _, _ in self.routes)

    # -- dependency / parameter resolution
    def _hints(self, fn):
        return typing.get_type_hints(fn, globalns=self.mod.__dict__)

    def _resolve_dep(self, dep, req):
        d = dep.dependency
        if isinstance(d, HTTPBasic):
            return req["credentials"]
        return self._call(d, req, {}, None)

    def _call(self, fn, req, path_params, body):
        hints = self._hints(fn)
        kwargs, errors = {}, []
        for name, p in inspect.signature(fn).parameters.items():
            tp = hints.get(name, str)
            default = p.default
            if isinstance(default, _Depends):
                kwargs[name] = self._resolve_dep(default, req)
            elif tp is Request:
                kwargs[name] = Request(req["headers"])
            elif isinstance(tp, type) and issubclass(tp, BaseModel):
                if not isinstance(body, dict):
                    errors.append({"loc": ["body"], "msg": "Field required" if body is None
                                   else "Input should be a valid dictionary", "type": "missing"})
                    continue
                try:
                    kwargs[name] = tp(**body)
                except ValidationError as e:
                    errors.extend(e.errors)
            elif name in path_params:
                try:
                    kwargs[name] = _coerce(path_params[name], tp, ["path", name])
                except ValidationError as e:
                    errors.extend(e.errors)
            else:
                q = req["query"]
                qd = default if isinstance(default, _Query) else _Query(
                    default if default is not inspect.Parameter.empty else ...)
                if name in q:
                    try:
                        v = _coerce(q[name], tp, ["query", name])
                        if qd.ge is not None and v is not None and v < qd.ge:
                            raise ValidationError([{"loc": ["query", name], "msg": f"Input should be >= {qd.ge}",
                                                    "type": "greater_than_equal"}])
                        if qd.le is not None and v is not None and v > qd.le:
                            raise ValidationError([{"loc": ["query", name], "msg": f"Input should be <= {qd.le}",
                                                    "type": "less_than_equal"}])
                        kwargs[name] = v
                    except ValidationError as e:
                        errors.extend(e.errors)
                elif qd.default is ...:
                    errors.append({"loc": ["query", name], "msg": "Field required", "type": "missing"})
                else:
                    kwargs[name] = qd.default
        if errors:
            raise ValidationError(errors)
        return fn(**kwargs)

    def handle(self, method, raw_path, headers, body_bytes):
        """-> (status, headers dict, body bytes)."""
        parts = urlsplit(raw_path)
        query = {k: v[-1] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        path = parts.path
        creds = None
        auth = headers.get("Authorization") or headers.get("authorization")
        if auth and auth.lower().startswith("basic "):
            try:
                u, _, pw = base64.b64decode(auth[6:].strip()).decode("utf-8").partition(":")
                creds = HTTPBasicCredentials(u, pw)
            except Exception:  # noqa: BLE001
                creds = None
        req = {"headers": headers, "query": query, "credentials": creds}
        match, allowed = None, False
        for m, p, fn, rx, names in self.routes:
            mm = rx.match(path)
            if mm:
                allowed = True
                if m == method:
                    match = (fn, {n: unquote(v) for n, v in zip(names, mm.groups())})
                    break
        if not match:
            return self._json(405 if allowed else 404,
                              {"detail": "Method Not Allowed" if allowed else "Not Found"})
        fn, path_params = match
        body = None
        if body_bytes:
            try:
                body = json.loads(body_bytes.decode("utf-8"))
            except ValueError:
                return self._json(422, {"detail": [{"loc": ["body"], "msg": "JSON decode error",
                                                    "type": "json_invalid"}]})
        try:
            for dep in self.mod.app.dependencies:      # the app-level auth gate
                self._resolve_dep(dep, req)
            out = self._call(fn, req, path_params, body)
        except HTTPException as e:
            status, hdrs, data = self._json(e.status_code, {"detail": e.detail})
            hdrs.update(e.headers)
            return status, hdrs, data
        except ValidationError as e:
            return self._json(422, {"detail": e.errors})
        if isinstance(out, FileResponse):
            return 200, {"Content-Type": (out.media_type or "application/octet-stream")
                         + ("; charset=utf-8" if (out.media_type or "").startswith("text") else "")}, \
                out.path.read_bytes()
        if isinstance(out, RedirectResponse):
            return out.status_code, {"Location": out.url}, b""
        return self._json(200, out)

    @staticmethod
    def _json(status, obj):
        return status, {"Content-Type": "application/json"}, \
            json.dumps(obj, ensure_ascii=False).encode("utf-8")


# ------------------------------------------------------------- test hooks

def _hook(app, name, body):
    from mh2 import seq_guardrail
    ctx = app.mod.CTX
    if name == "demo_payload":
        sys.path.insert(0, str(ROOT / "scripts"))
        import export_seq_demo as X   # noqa: E402
        return X.build_payload_from_db(ctx.mh2_path, str(body.get("grade", "2")),
                                       bool(body.get("with_sequence")), ctx.seq_path)
    if name == "reword":
        from mh2.ingest_ladders import source_key
        con = sqlite3.connect(ctx.mh2_path)
        try:
            row = con.execute("SELECT stem_id, node_text, source_key FROM nodes WHERE node_id=?",
                              (body["node_id"],)).fetchone()
            if row is None:
                raise KeyError(body["node_id"])
            text = row[1] + body.get("suffix", " (reworded)")
            new_key = source_key(row[0], text)
            con.execute("PRAGMA foreign_keys=OFF")
            con.execute("UPDATE nodes SET node_text=?, source_key=? WHERE node_id=?",
                        (text, new_key, body["node_id"]))
            con.commit()
        finally:
            con.close()
        return {"old_key": row[2], "new_key": new_key, "node_text": text}
    if name == "set_kind":
        con = sqlite3.connect(ctx.mh2_path)
        try:
            n = con.execute("UPDATE node_grade_kind SET kind=? WHERE node_id=? AND grade=?",
                            (body["kind"], body["node_id"], body["grade"])).rowcount
            con.commit()
        finally:
            con.close()
        return {"updated": n}
    if name == "guardrail":
        return seq_guardrail.compute(body["placements"])
    raise KeyError(name)


# ------------------------------------------------------------------ server

def make_handler(app, test_hooks=False):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):   # quiet
            pass

        def _do(self):
            n = int(self.headers.get("Content-Length") or 0)
            data = self.rfile.read(n) if n else b""
            if test_hooks and self.path.startswith("/__harness/") and self.command == "POST":
                try:
                    out = _hook(app, self.path[len("/__harness/"):], json.loads(data or b"{}"))
                    status, hdrs, payload = App._json(200, out)
                except Exception as e:  # noqa: BLE001
                    status, hdrs, payload = App._json(500, {"detail": repr(e)})
            else:
                status, hdrs, payload = app.handle(self.command, self.path, dict(self.headers.items()), data)
            self.send_response(status)
            for k, v in hdrs.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = _do
    return H


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="seq_api.py over http.server (no FastAPI)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--db", help="mh2.db (default config.DB)")
    ap.add_argument("--seq-db", help="mh2_seq.db (default config.SEQ_DB)")
    ap.add_argument("--test-hooks", action="store_true", help="enable POST /__harness/* (tests only)")
    ap.add_argument("--routes", action="store_true", help="print the route table and exit")
    args = ap.parse_args(argv)
    mod = load_seq_api()
    if args.db or args.seq_db:
        from mh2 import seq_service
        mod.CTX = seq_service.Ctx(Path(args.db) if args.db else mod.CTX.mh2_path,
                                  Path(args.seq_db) if args.seq_db else mod.CTX.seq_path)
    app = App(mod)
    if args.routes:
        for r in app.route_table():
            print(*r)
        return 0
    # threaded, like uvicorn serving several browser connections (keep-alive would
    # otherwise block a second connection); every service call opens its own DB connections
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(app, args.test_hooks))
    srv.daemon_threads = True
    print(f"LISTENING http://{args.host}:{srv.server_address[1]}/  (routes: {len(app.routes)}; "
          f"mh2={mod.CTX.mh2_path}; seq={mod.CTX.seq_path})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
