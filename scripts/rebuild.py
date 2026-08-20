"""
rebuild.py — delete the database and build it again from data/source.

This is the safe default. data/build is disposable by design: if anything
looks wrong, delete it and run this. Nothing in data/source is ever modified.

    python scripts/rebuild.py
    python scripts/rebuild.py --skip-reconcile
    python scripts/rebuild.py --skip-candidates
"""

import argparse
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402


def run(label: str, args: list[str]) -> None:
    print(f"\n=== {label}")
    result = subprocess.run([sys.executable, *args], cwd=config.ROOT)
    if result.returncode != 0:
        sys.exit(f"FAILED: {label}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-reconcile", action="store_true")
    ap.add_argument("--skip-candidates", action="store_true")
    args = ap.parse_args()

    config.ensure_dirs()

    missing = [p.name for p in (config.STEM_WORKBOOK, config.TAGGING_WORKBOOK)
               if not p.exists()]
    if missing:
        sys.exit("Missing source files in data/source/workbooks: " + ", ".join(missing))

    print("=== creating schema")
    if config.DB.exists():
        config.DB.unlink()
    sqlite3.connect(config.DB).executescript(config.SCHEMA.read_text())
    print(f"    {config.DB}")

    run("loading standards + stem workbook",
        ["-m", "mh2.load_standards", "--db", str(config.DB),
         "--project", str(config.SOURCE)])

    run("loading stems.csv (ladder <-> workbook stem mapping)",
        ["-m", "mh2.load_stems", "--db", str(config.DB),
         "--csv", str(config.STEMS_CSV), "--ladders", str(config.LADDERS)])

    run("ingesting ladders",
        ["-m", "mh2.ingest_ladders", "--db", str(config.DB),
         "--glob", config.LADDER_GLOB])

    run("parsing node standards cells",
        ["-m", "mh2.parse_node_standards", "--db", str(config.DB),
         "--ladders", str(config.LADDERS)])

    run("loading layer 1 (standard_lessons, crosswalk, tag status)",
        ["-m", "mh2.load_layer1", "--db", str(config.DB)])

    run("loading Path C predictions (step 8)",
        ["-m", "mh2.load_predictions", "--db", str(config.DB),
         "--predictions", str(config.PREDICTIONS)])

    run("loading reranks (step 7 browse list; cache-only, no API calls)",
        ["-m", "mh2.load_reranks", "--db", str(config.DB),
         "--cache", str(config.RERANK_CACHE)])

    if not args.skip_candidates:
        run("generating candidates (step 6, Paths 0/A/B)",
            ["-m", "mh2.candidates", "--db", str(config.DB)])

    if not args.skip_reconcile:
        run("reconciling workbook vs ladders",
            ["-m", "mh2.reconcile", "--db", str(config.DB),
             "--out", str(config.REPORTS)])

    con = sqlite3.connect(config.DB)
    print("\n=== row counts")
    for table in ("standards", "concepts", "concept_standards", "nodes",
                  "node_standards", "node_standards_parsed",
                  "node_absence_assertions", "stem_map", "standard_lessons",
                  "crosswalk", "alignment_audit", "model_suggestions",
                  "standard_tag_status", "model_predictions",
                  "model_state_predictions", "model_reranks", "candidates",
                  "leaves", "node_links", "drift"):
        try:
            n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"    {table:20s} {n:7,d}")
        except sqlite3.OperationalError:
            pass
    con.close()
    print(f"\nDone. Database: {config.DB}\nReports: {config.REPORTS}")


if __name__ == "__main__":
    main()
