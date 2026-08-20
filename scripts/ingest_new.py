"""
ingest_new.py — add or re-ingest ladder documents without a full rebuild.

Always dry-runs first unless --apply is given, so you can see what would
change before anything is written.

    python scripts/ingest_new.py                        # preview all ladders
    python scripts/ingest_new.py --apply                # write them
    python scripts/ingest_new.py --file Counting.docx --apply

    # a file whose topic has never been ingested before: name it explicitly
    python scripts/ingest_new.py --file Probability.docx \\
        --stem-id PRO --stem-name Probability --apply

What the preview tells you:
    new         node text not seen before -> a node record will be created
    matched     node text unchanged -> keeps its ID and its alignments
    missing     in the database, not in this file -> possibly reworded or cut
    collision   two rows in the same ladder share node text -> they will
                merge into ONE node. Usually intentional (a node repeated
                across grades) but worth a glance.
    UNRESOLVED  the filename doesn't word-match any existing stem, and there
                is no safe way to invent one -- see resolve_stem()'s
                docstring in mh2/ingest_ladders.py for why. Re-run with
                --file <this file> --stem-id XXX --stem-name "..." once
                you've decided what the stem should be called.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from mh2.ingest_ladders import (  # noqa: E402
    bound_stem_of, derive_stem_name, read_docx, read_markdown, persist,
    resolve_stem, source_key,
)


def load_nodes(path: Path) -> tuple[list[dict], str]:
    stem_name = derive_stem_name(path.name)
    fmt = "docx" if path.open("rb").read(2) == b"PK" else "md"
    nodes = read_docx(path, stem_name) if fmt == "docx" else read_markdown(path, stem_name)
    return nodes, fmt


def preview(cur, path: Path, stem_id_override: str | None = None) -> dict:
    nodes, fmt = load_nodes(path)
    if not nodes:
        return {"file": path.name, "fmt": fmt, "parsed": 0}

    # A file that already has nodes has a SETTLED stem_id -- never re-derive
    # it from the filename text. See bound_stem_of()'s docstring: this is
    # what makes a human --stem-id decision stick across every later plain
    # preview/apply, instead of getting silently re-guessed back to whatever
    # resolve_stem()'s fuzzy match coincidentally lands on this time.
    bound = bound_stem_of(cur, path.name)
    stem_id = bound or stem_id_override or resolve_stem(
        cur, nodes[0]["stem_name"], allow_create=False)
    con_rollback_needed = stem_id_override is None
    if stem_id is None:
        return {"file": path.name, "fmt": fmt, "parsed": len(nodes),
               "unresolved": nodes[0]["stem_name"]}
    # resolve_stem(allow_create=False) never inserts, but a defensive
    # rollback costs nothing and guards against a future change to it.
    if con_rollback_needed:
        cur.connection.rollback()

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
    ap.add_argument("--stem-id", help="explicit stem_id for a brand-new stem"
                    " (requires --file; see UNRESOLVED in the preview)")
    ap.add_argument("--stem-name", help="display name for --stem-id, if it"
                    " doesn't already exist")
    args = ap.parse_args()

    if not config.DB.exists():
        sys.exit(f"No database at {config.DB}. Run scripts/rebuild.py first.")
    if args.stem_id and not args.file:
        sys.exit("--stem-id requires --file: it names ONE file's stem, and"
                  " applying it across every ladder in the directory would"
                  " assign every unresolved file to the same stem.")

    paths = [config.LADDERS / args.file] if args.file else sorted(config.LADDERS.glob("*.docx"))
    if not paths:
        sys.exit(f"No ladder files found in {config.LADDERS}")

    con = sqlite3.connect(config.DB)
    cur = con.cursor()

    unresolved = []
    for path in paths:
        if not path.exists():
            print(f"  MISSING FILE {path}")
            continue
        info = preview(cur, path, stem_id_override=args.stem_id)
        if info.get("unresolved"):
            unresolved.append(path.name)
            print(f"\n{info['file']}  [{info['fmt']}]  parsed={info['parsed']}")
            print(f"    UNRESOLVED  derived topic name: {info['unresolved']!r}")
            print(f"        no existing stem matches, and inventing one isn't"
                  f" safe -- re-run with:\n"
                  f"        --file {info['file']} --stem-id XXX"
                  f" --stem-name \"...\" --apply")
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
            stats = persist(cur, nodes, run_id="manual",
                            stem_id_override=args.stem_id,
                            stem_name_override=args.stem_name,
                            allow_create=False)
            print(f"  {path.name}: {stats}")
            if stats["unresolved"]:
                print(f"    !! {stats['unresolved']} node(s) skipped —"
                      " stem could not be resolved")
        con.commit()
        if unresolved:
            print(f"\nSkipped entirely (no stem resolved): {', '.join(unresolved)}")
    else:
        print("\nPreview only. Re-run with --apply to write.")
        if unresolved:
            print(f"UNRESOLVED, needs --stem-id/--stem-name before it can"
                  f" apply: {', '.join(unresolved)}")

    con.close()


if __name__ == "__main__":
    main()
