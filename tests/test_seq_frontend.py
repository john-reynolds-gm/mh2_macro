"""
Frontend checks for the Grade Sequencing Tool (contract section 8.3).

Runs the node test-suite, checks the static files against the hard rules (7.9:
no http(s):// strings, exactly the two replaceable tags), and builds the demo
bundle to check it is one self-contained file whose embedded payload the page's
DemoApi can serve.

Run: MH2_DATA_DIR=<data dir> python3 tests/test_seq_frontend.py
     (or python -m pytest tests/test_seq_frontend.py -q)
No test opens a real database for writing; database-backed checks use a temp copy
and print SKIP when mh2.seq_read or the data is missing.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STATIC = ROOT / "seq_static"
JS_TEST = ROOT / "tests" / "js" / "seq_app_test.js"
BUNDLE_CHECK = ROOT / "tests" / "js" / "demo_bundle_check.js"
FIXTURE = ROOT / "tests" / "js" / "fixtures" / "g2_payload.json"
COMMITTED_DEMO = ROOT / "docs" / "seq_demo" / "seq_demo_g2.html"
MAX_DEMO_BYTES = 3 * 1024 * 1024

_SKIPS: dict[str, str] = {}


def skip(reason: str):
    """Record a skip for the running test (the runner prints SKIP instead of ok)."""
    import inspect
    name = inspect.stack()[1].function
    _SKIPS[name] = reason


def _node():
    return shutil.which("node")


def _embedded_json(html: str) -> dict:
    m = re.search(r'<script id="seq-demo-data" type="application/json">(.*?)</script>', html, re.S)
    assert m, "no embedded payload"
    return json.loads(m.group(1))


def _assert_single_file(html: str) -> None:
    assert not re.search(r"https?://", html), re.search(r".{30}https?://.{30}", html)
    assert not re.search(r"<script[^>]*\ssrc=", html, re.I), "external <script src>"
    assert not re.search(r"<link\b", html, re.I), "<link> left in the bundle"
    assert "assets/app." not in html


def _node_bundle_check(path: Path) -> dict:
    r = subprocess.run(["node", str(BUNDLE_CHECK), str(path)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["ok"] is True, out
    return out


def _check_bundle_file(path: Path, expect_source: str | None = None) -> dict:
    html = path.read_text(encoding="utf-8")
    _assert_single_file(html)
    assert len(html.encode("utf-8")) < MAX_DEMO_BYTES, len(html)
    payload = _embedded_json(html)
    assert payload["format"] == "mh2-seq-demo/1"
    keys = [n["source_key"] for ss in payload["slice"]["super_stems"] for st in ss["stems"]
            for c in st["concept_skills"] for n in c["nodes"]]
    assert len(keys) == len(set(keys)) == payload["slice"]["counts"]["chips"]
    assert set(payload["drawers"]) == set(keys) == set(payload["compare_columns"])
    if expect_source:
        assert payload["source"] == expect_source, payload["source"]
    if _node():
        out = _node_bundle_check(path)
        assert out["chips"] == len(keys)
    return payload


def test_node_suite_passes():
    if not _node():
        return skip("node not installed")
    r = subprocess.run(["node", str(JS_TEST)], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "tests passed" in r.stdout, r.stdout[-500:]


def test_static_files_have_no_http_urls():
    for f in sorted(STATIC.iterdir()):
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"https?://", text), (f.name, re.search(r".{20}https?://.{20}", text))
    assert {f.name for f in STATIC.iterdir()} == {"index.html", "app.js", "app.css"}


def test_index_has_the_two_replaceable_tags_and_relative_urls():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.count('<link rel="stylesheet" href="assets/app.css">') == 1
    assert html.count('<script src="assets/app.js"></script>') == 1
    assert len(re.findall(r"<script\b", html)) == 1 and len(re.findall(r"<link\b", html)) == 1
    assert not re.search(r'(?:src|href)="/', html), "absolute asset path"
    for region in ("hdr", "toolbar", "rail", "slice", "drawer", "sheet", "tray", "notice", "toasts", "foot", "app", "banner"):
        assert f'id="{region}"' in html, region
    assert 'role="complementary"' in html and 'aria-label="Node details"' in html
    assert 'aria-live="polite"' in html


def test_app_js_shape_and_syntax():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "</script" not in js.lower() and "</style" not in (STATIC / "app.css").read_text().lower()
    for name in ("esc", "encodeState", "decodeState", "defaultState", "computeGuardrail", "assembleCompare",
                 "visibleSlice", "DemoApi", "HttpApi", "ApiError", "renderSlice", "renderDrawer",
                 "renderRail", "renderSheet", "renderGuardrail", "renderAttention", "renderToolbar"):
        assert re.search(rf"\b{name}\b", js.rsplit("module.exports = {", 1)[1]), name
    assert 'document.addEventListener("DOMContentLoaded"' in js
    if not _node():
        return skip("node not installed")
    r = subprocess.run(["node", "--check", str(STATIC / "app.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_js_does_not_derive_kinds_or_badges():
    """Contract 7.9: the page reads state/owed/badges; it never computes them (DemoApi aside)."""
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    render_and_controller = js.split("5. render functions", 1)[1]
    for bad in ("node_grade", "kind_for", "build_grade_kinds", "ruling_type ===", "resolution ===", "normalize_grade"):
        assert bad not in render_and_controller, bad


def test_embed_json_keeps_urls_and_closing_tags_out():
    from scripts.export_seq_demo import embed_json
    data = {"a": "</script><b>", "b": "see http://x.test/y and https://z.test", "c": "line sep "}
    s = embed_json(data)
    assert "</" not in s and "://" not in s and " " not in s and " " not in s
    assert json.loads(s) == data


def test_bundle_refuses_index_without_the_exact_tags():
    from scripts.export_seq_demo import ExportError, bundle
    tmp = Path(tempfile.mkdtemp())
    try:
        for f in STATIC.iterdir():
            shutil.copy(f, tmp / f.name)
        html = (tmp / "index.html").read_text().replace('<script src="assets/app.js"></script>', '<script src="assets/app.js" defer></script>')
        (tmp / "index.html").write_text(html)
        try:
            bundle(json.loads(FIXTURE.read_text()), tmp)
        except ExportError as e:
            assert "app.js" in str(e)
        else:
            raise AssertionError("bundle() accepted an index.html without the exact script tag")
        # and main() turns that into a non-zero exit code
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_export_from_fixture_builds_a_self_contained_bundle():
    from scripts import export_seq_demo
    tmp = Path(tempfile.mkdtemp())
    try:
        out = tmp / "seq_demo_g2.html"
        rc = export_seq_demo.main(["--fixture", str(FIXTURE), "--out", str(out)])
        assert rc == 0 and out.exists()
        payload = _check_bundle_file(out, expect_source="fixture")
        html = out.read_text(encoding="utf-8")
        assert html.count("<style>") == 1 and html.count('<script id="seq-demo-data"') == 1
        assert html.index('id="seq-demo-data"') < html.index("function esc(")   # payload precedes the app script
        assert payload["slice"]["counts"]["chips"] == 57
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_export_fixture_with_bad_payload_exits_nonzero():
    from scripts import export_seq_demo
    tmp = Path(tempfile.mkdtemp())
    try:
        bad = json.loads(FIXTURE.read_text())
        bad["drawers"].pop(next(iter(bad["drawers"])))
        p = tmp / "bad.json"
        p.write_text(json.dumps(bad))
        assert export_seq_demo.main(["--fixture", str(p), "--out", str(tmp / "o.html")]) == 2
        assert export_seq_demo.main(["--fixture", str(tmp / "missing.json"), "--out", str(tmp / "o.html")]) == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_committed_demo_bundle_is_valid():
    if not COMMITTED_DEMO.exists():
        return skip("docs/seq_demo/seq_demo_g2.html not built yet")
    payload = _check_bundle_file(COMMITTED_DEMO)
    assert payload["grade"] == "2" and payload["source"] in ("fixture", "mh2.db")
    html = COMMITTED_DEMO.read_text(encoding="utf-8")
    assert "Ordering warnings need the server" in html          # the demo footnote ships with the page


def test_export_from_database_matches_seq_read():
    try:
        from mh2 import seq_read
    except ImportError:
        return skip("mh2.seq_read not available (arrives at integration)")
    import config
    from scripts import export_seq_demo
    if not Path(config.DB).exists():
        return skip(f"no database at {config.DB}")
    tmp = Path(tempfile.mkdtemp())
    try:
        db = tmp / "mh2.db"
        shutil.copy(config.DB, db)
        out = tmp / "seq_demo_g2.html"
        assert export_seq_demo.main(["--grade", "2", "--db", str(db), "--out", str(out)]) == 0
        payload = _check_bundle_file(out, expect_source="mh2.db")
        con = seq_read.connect_ro(db)
        try:
            assert payload["slice"] == json.loads(json.dumps(seq_read.build_slice(con, "2")))
        finally:
            con.close()
        assert payload["sequence"]["sequence"] is None and payload["views"] == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import traceback
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"SKIP {name}: {_SKIPS[name]}" if name in _SKIPS else f"ok {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    sys.exit(1 if failed else 0)
