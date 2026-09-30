"""
export_seq_demo.py -- build the offline single-file demo of the Grade Sequencing
Tool (docs/seq_v1_contract.md section 6).

The page is seq_static/index.html with its stylesheet and script inlined and a
JSON payload embedded; the page's DemoApi serves that payload, so the whole tool
can be tried by double-clicking one file. Edits made in the demo live in memory
and, when the browser allows, localStorage. They are never sent anywhere.

Usage:
    python scripts/export_seq_demo.py --grade 2 [--out PATH] [--db PATH]
                                      [--with-sequence --seq-db PATH]
    python scripts/export_seq_demo.py --fixture tests/js/fixtures/g2_payload.json

  --grade           any grade_order token (PK K 1..8 A1; "g2", "Grade 2" tolerated)
  --out             default <config.REPORTS>/seq_demo_<g2|pk|k|a1>.html
  --db              mh2.db (default config.DB; opened read-only via mh2.seq_read)
  --with-sequence   also embed the current placements from mh2_seq.db (needs
                    mh2.seq_service); --seq-db defaults to config.SEQ_DB
  --fixture         build from a ready-made DemoPayload JSON instead of mh2.db
                    (used before mh2.seq_read was available, and by tests). The
                    page footer then says "demo data built from fixture"; built
                    from the database it says "from mh2.db".

Data source is recorded in payload["source"] ("mh2.db" or "fixture"), an
additive key that the page only uses for its footer.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402

STATIC = ROOT / "seq_static"
CSS_TAG = '<link rel="stylesheet" href="assets/app.css">'
JS_TAG = '<script src="assets/app.js"></script>'
FORMAT = "mh2-seq-demo/1"


class ExportError(Exception):
    """A problem the user can fix; main() prints it and returns non-zero."""


def grade_slug(grade: str) -> str:
    """File-name slug: digits get a 'g' prefix (2 -> g2), others lowercase (PK -> pk)."""
    return f"g{grade}" if grade.isdigit() else grade.lower()


def embed_json(data) -> str:
    """json.dumps that is safe inside <script type="application/json">.

    '</' (would end the tag), U+2028/U+2029 (line terminators) and '://' (keeps
    literal URLs out of the file) are replaced by JSON escapes, which all parse
    back to the same text."""
    s = json.dumps(data, ensure_ascii=False)
    return (s.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
            .replace("://", ":\\/\\/"))


def slice_keys(slice_: dict) -> list[str]:
    """Every source_key in a Slice, in display order."""
    return [n["source_key"]
            for ss in slice_["super_stems"] for st in ss["stems"]
            for c in st["concept_skills"] for n in c["nodes"]]


def empty_guardrail() -> dict:
    """compute([]) (contract 5.8, G0): mh2.seq_guardrail when present, else the same literal."""
    try:
        from mh2 import seq_guardrail
        return seq_guardrail.compute([])
    except ImportError:
        z3 = {"deep": 0, "functional": 0, "illuminating": 0}
        return {"n": 0, "n_calibrated": 0, "n_timed": 0, "counts": {**z3, "unset": 0},
                "count_share": {k: None for k in z3},
                "periods": {**{k: 0.0 for k in z3}, "unset": 0.0}, "total_periods": 0.0,
                "time_share": {k: None for k in z3}, "time_coverage": None,
                "show_time_targets": False,
                "count_target": {"deep": 0.25, "functional": 0.5, "illuminating": 0.25},
                "time_target": {"deep": 0.4, "functional": 0.45, "illuminating": 0.15},
                "time_mark_min_coverage": 0.5}


def empty_sequence_view(grade: str, ladders_last_read) -> dict:
    return {"grade": grade, "ladders_last_read": ladders_last_read, "sequence": None, "modules": [],
            "placed_index": {}, "slice_badges": {}, "guardrail": empty_guardrail(), "attention": []}


def build_payload_from_db(db: Path, grade_token: str, with_sequence: bool = False,
                          seq_db: Path | None = None) -> dict:
    """DemoPayload (contract 6.1) from mh2.db through R's functions."""
    try:
        from mh2 import seq_read
    except ImportError as e:  # pragma: no cover - depends on the merge
        raise ExportError(
            "mh2.seq_read is not importable (it is built by another implementer and arrives at "
            f"integration): {e}. Use --fixture <json> to build from a fixture instead.")
    con = seq_read.connect_ro(db)
    try:
        grade = seq_read.normalize_grade(con, grade_token)
        ctx = None
        if with_sequence:
            try:
                from mh2 import seq_service
            except ImportError as e:
                raise ExportError(f"--with-sequence needs mh2.seq_service, which is not importable: {e}")
            ctx = seq_service.Ctx(mh2_path=Path(db), seq_path=Path(seq_db or config.SEQ_DB))
        if ctx:
            slice_ = seq_service.get_slice(ctx, grade)
            grades = seq_service.grades(ctx)
            sequence = seq_service.get_sequence(ctx, grade)
        else:
            slice_ = seq_read.build_slice(con, grade)
            ladders = seq_read.ladders_last_read(con)
            grades = {"grades": seq_read.list_grades(con), "kind_source": slice_["kind_source"],
                      "ladders_last_read": ladders}
            sequence = empty_sequence_view(grade, ladders)
        keys = slice_keys(slice_)
        if ctx:
            drawers = {k: seq_service.get_node(ctx, k, grade) for k in keys}
        else:
            drawers = {k: seq_read.node_drawer(con, k, grade) for k in keys}
        columns = {k: seq_read.compare_column(con, k, grade) for k in keys}
        payload = {
            "format": FORMAT,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "grade": grade, "source": "mh2.db",
            "whoami": {"user": "demo", "auth_enabled": False, "source": "demo"},
            "grades": grades, "slice": slice_, "drawers": drawers, "compare_columns": columns,
            "field_defs": seq_read.compare_field_defs(con), "sequence": sequence, "views": [],
        }
    finally:
        con.close()
    return payload


def load_fixture(path: Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    payload["source"] = "fixture"
    return payload


def validate_payload(payload: dict) -> None:
    """Fail early on a payload the page could not serve."""
    if payload.get("format") != FORMAT:
        raise ExportError(f"payload format is {payload.get('format')!r}, expected {FORMAT!r}")
    for k in ("grade", "generated_at", "whoami", "grades", "slice", "drawers", "compare_columns",
              "field_defs", "sequence", "views"):
        if k not in payload:
            raise ExportError(f"payload is missing {k!r}")
    keys = slice_keys(payload["slice"])
    if len(set(keys)) != len(keys):
        raise ExportError("a node appears twice in the slice")
    if set(keys) != set(payload["drawers"]) or set(keys) != set(payload["compare_columns"]):
        raise ExportError("drawers / compare_columns keys differ from the slice's nodes")
    if payload["slice"]["counts"]["chips"] != len(keys):
        raise ExportError("slice counts.chips does not match the number of chips")


def bundle(payload: dict, static_dir: Path = STATIC) -> str:
    """index.html with app.css and app.js inlined and the payload embedded (contract 6.1)."""
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    css = (static_dir / "app.css").read_text(encoding="utf-8")
    js = (static_dir / "app.js").read_text(encoding="utf-8")
    for tag in (CSS_TAG, JS_TAG):
        if html.count(tag) != 1:
            raise ExportError(f"seq_static/index.html must contain exactly one {tag!r} (found {html.count(tag)})")
    for name, text in (("app.css", css), ("app.js", js)):
        if "</style" in text.lower() or "</script" in text.lower():
            raise ExportError(f"{name} contains a closing tag that would break inlining")
    data_tag = f'<script id="seq-demo-data" type="application/json">{embed_json(payload)}</script>'
    html = html.replace(CSS_TAG, f"<style>\n{css}\n</style>")
    html = html.replace(JS_TAG, f"{data_tag}\n<script>\n{js}\n</script>")
    return html


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--grade", default="2", help="grade token, e.g. PK, K, 2, A1 (default 2)")
    ap.add_argument("--out", help="output html path (default <reports>/seq_demo_<slug>.html)")
    ap.add_argument("--db", help="path to mh2.db (default config.DB)")
    ap.add_argument("--with-sequence", action="store_true", help="also embed placements from mh2_seq.db")
    ap.add_argument("--seq-db", help="path to mh2_seq.db (default config.SEQ_DB)")
    ap.add_argument("--fixture", help="build from a DemoPayload JSON file instead of mh2.db")
    args = ap.parse_args(argv)
    try:
        if args.fixture:
            payload = load_fixture(Path(args.fixture))
        else:
            payload = build_payload_from_db(Path(args.db) if args.db else config.DB, args.grade,
                                            args.with_sequence, Path(args.seq_db) if args.seq_db else None)
        validate_payload(payload)
        page = bundle(payload)
    except ExportError as e:
        print(f"export_seq_demo: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"export_seq_demo: file not found: {e.filename}", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else config.REPORTS / f"seq_demo_{grade_slug(payload['grade'])}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    c = payload["slice"]["counts"]
    print(f"Wrote {out}  ({out.stat().st_size // 1024} KB, data from {payload['source']})")
    print(f"Grade {payload['grade']}: {c['chips']} chips, {c['in_grade_nodes']} in-grade nodes, "
          f"{c['stems']} stems, {c['concept_skills']} concept/skills")
    return 0


if __name__ == "__main__":
    sys.exit(main())
