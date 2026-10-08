"""
ingest_new.py — add or re-ingest ladder documents without a full rebuild.

Always dry-runs first unless --apply is given, so you can see what would
change before anything is written.

    python scripts/ingest_new.py                        # preview all ladders
    python scripts/ingest_new.py --apply                # write them
    python scripts/ingest_new.py --file Counting.docx --apply

A file's stem_id comes from stems.csv's `ladder_file` column, read live, so a
new ladder needs a stems.csv row (stem_id, ladder_file, ladder_drafted=1)
before it can be applied. stems.csv is the registry of ladder codes
(DEFERRED.md §10); no code is ever derived from a filename.

What the preview tells you:
    new         node text not seen before -> a node record will be created
    matched     node text unchanged -> keeps its ID and its alignments
    missing     in the database, not in this file -> possibly reworded or cut
    collision   two rows in the same ladder share node text -> they will
                merge into ONE node. Usually intentional (a node repeated
                across grades) but worth a glance.
    UNRESOLVED  stems.csv does not list this file. Add its row, then
                re-run.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from mh2.ingest_ladders import (  # noqa: E402
    derive_stem_name, persist, read_docx, read_markdown, source_key,
    stem_for_file,
)
from mh2.load_stems import declared_ladders, read_stems_csv  # noqa: E402


def load_nodes(path: Path) -> tuple[list[dict], str]:
    stem_name = derive_stem_name(path.name)
    fmt = "docx" if path.open("rb").read(2) == b"PK" else "md"
    nodes = read_docx(path, stem_name) if fmt == "docx" else read_markdown(path, stem_name)
    return nodes, fmt


def preview(cur, path: Path, declared: dict) -> dict:
    nodes, fmt = load_nodes(path)
    if not nodes:
        return {"file": path.name, "fmt": fmt, "parsed": 0}

    hit = stem_for_file(cur, path.name, declared)
    if hit is None:
        return {"file": path.name, "fmt": fmt, "parsed": len(nodes),
                "unresolved": True}
    stem_id = hit[0]

    keys, collisions = {}, []
    for n in nodes:
        k = source_key(stem_id, n["node_text"])
        if k in keys:
            collisions.append(n["node_text"])
        keys[k] = n["node_text"]

    existing = {r[0]: r[1] for r in cur.execute(
        "SELECT source_key, node_text FROM nodes WHERE stem_id = ?", (stem_id,))}

    return {
        "file": path.name, "fmt": fmt, "stem": stem_id, "parsed": len(nodes),
        "new": [keys[k] for k in keys if k not in existing],
        "matched": [keys[k] for k in keys if k in existing],
        "missing": [existing[k] for k in existing if k not in keys],
        "collision": collisions,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="single filename inside data/source/ladders")
    ap.add_argument("--apply", action="store_true", help="write changes")
    args = ap.parse_args()

    if not config.DB.exists():
        sys.exit(f"No database at {config.DB}. Run scripts/rebuild.py first.")
    declared = declared_ladders(read_stems_csv(config.STEMS_CSV))

    paths = [config.LADDERS / args.file] if args.file else sorted(
        p for p in config.LADDERS.glob("*.docx") if not p.name.startswith("~$"))
    if not paths:
        sys.exit(f"No ladder files found in {config.LADDERS}")

    con = sqlite3.connect(config.DB)
    cur = con.cursor()

    unresolved = []
    for path in paths:
        if not path.exists():
            print(f"  MISSING FILE {path}")
            continue
        info = preview(cur, path, declared)
        if info.get("unresolved"):
            unresolved.append(path.name)
            print(f"\n{info['file']}  [{info['fmt']}]  parsed={info['parsed']}")
            print("    UNRESOLVED  not listed in stems.csv -- add a row for it"
                  " (stem_id, ladder_file, ladder_drafted=1) and re-run")
            continue
        print(f"\n{info['file']}  [{info['fmt']}]  stem={info.get('stem')}  parsed={info['parsed']}")
        for kind in ("new", "matched", "missing", "collision"):
            items = info.get(kind) or []
            if not items:
                continue
            print(f"    {kind:10s} {len(items)}")
            if kind in ("new", "missing", "collision"):
                for t in items[:6]:
                    print(f"        - {t[:88]}")
                if len(items) > 6:
                    print(f"        ... {len(items) - 6} more")

    if args.apply:
        print("\n=== applying")
        for path in paths:
            if not path.exists() or path.name in unresolved:
                continue
            nodes, _ = load_nodes(path)
            if not nodes:
                continue
            stem_id, name = stem_for_file(cur, path.name, declared)
            stats = persist(cur, nodes, run_id="manual",
                            stem_id=stem_id, stem_name=name)
            print(f"  {path.name}: {stats}")
        con.commit()
        if unresolved:
            print(f"\nSkipped (not in stems.csv): {', '.join(unresolved)}")
    else:
        print("\nPreview only. Re-run with --apply to write.")
        if unresolved:
            print(f"UNRESOLVED, needs a stems.csv row before it can apply:"
                  f" {', '.join(unresolved)}")

    con.close()


if __name__ == "__main__":
    main()
