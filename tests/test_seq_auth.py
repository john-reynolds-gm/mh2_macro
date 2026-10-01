"""seq_auth tests: ast drift test against review_api._configured_writers,
check_basic / resolve_writer under a hand-patched os.environ."""
import ast
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mh2 import seq_auth  # noqa: E402


def _body_dump(path, fname):
    tree = ast.parse(Path(path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fname:
            return "\n".join(ast.dump(stmt) for stmt in node.body), ast.dump(node.returns)
    raise AssertionError(f"{fname} not found in {path}")


def test_configured_writers_body_matches_review_api():
    a = _body_dump(ROOT / "review_api.py", "_configured_writers")
    b = _body_dump(ROOT / "mh2" / "seq_auth.py", "configured_writers")
    assert a == b, "seq_auth.configured_writers drifted from review_api._configured_writers"


def test_drift_detector_actually_detects():
    src = (ROOT / "mh2" / "seq_auth.py").read_text().replace('split(",")', 'split(";")')
    tmp = ROOT / "tests" / "_tmp_seq_auth_drift.py"
    try:
        tmp.write_text(src)
        a = _body_dump(ROOT / "review_api.py", "_configured_writers")
        b = _body_dump(tmp, "configured_writers")
        assert a != b
    finally:
        if tmp.exists():
            tmp.unlink()


class _Env:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        self.old = os.environ.get("MH2_AUTH_USERS")
        if self.value is None:
            os.environ.pop("MH2_AUTH_USERS", None)
        else:
            os.environ["MH2_AUTH_USERS"] = self.value

    def __exit__(self, *a):
        if self.old is None:
            os.environ.pop("MH2_AUTH_USERS", None)
        else:
            os.environ["MH2_AUTH_USERS"] = self.old


def test_auth_off():
    with _Env(None):
        assert seq_auth.auth_enabled() is False
        assert seq_auth.check_basic(None, None) == "local"
        assert seq_auth.check_basic("x", "y") == "local"
        assert seq_auth.resolve_writer(None, None) == ("local", "default")
        assert seq_auth.resolve_writer(None, "alice") == ("alice", "dev_header")
        assert seq_auth.resolve_writer(None, "a.b_c@d-1") == ("a.b_c@d-1", "dev_header")
        assert seq_auth.resolve_writer(None, "a b") == ("local", "default")
        assert seq_auth.resolve_writer(None, "bob\n") == ("local", "default")
        assert seq_auth.resolve_writer(None, "") == ("local", "default")
        assert seq_auth.resolve_writer(None, "x" * 65) == ("local", "default")


def test_auth_on():
    with _Env("a:x, b:y"):
        assert seq_auth.auth_enabled() is True
        assert seq_auth.check_basic("a", "x") == "a"
        assert seq_auth.check_basic("b", "y") == "b"
        assert seq_auth.check_basic("a", "y") is None
        assert seq_auth.check_basic("zed", "x") is None
        assert seq_auth.check_basic(None, None) is None
        assert seq_auth.check_basic("a", None) is None
        assert seq_auth.check_basic("a", "xé") is None       # non-ASCII must not raise
        # dev header is ignored when auth is on
        assert seq_auth.resolve_writer("a", "evil") == ("a", "basic")


def test_empty_or_malformed_env_means_off():
    with _Env(" , ,nopass,:x"):
        assert seq_auth.auth_enabled() is False


def test_no_fastapi_import():
    assert "fastapi" not in (ROOT / "mh2" / "seq_auth.py").read_text()


def _main():
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok", name)
            except Exception as e:  # noqa: BLE001
                failed += 1
                print("FAIL", name, repr(e))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
