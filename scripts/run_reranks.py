"""
run_reranks.py — the ONE script that spends money on reranking.

    pip install 'anthropic>=0.40'     # NOT -r requirements-ml.txt: pulls torch
    export ANTHROPIC_API_KEY=...
    python scripts/run_reranks.py --limit 5     # smoke test
    python scripts/run_reranks.py               # every TX/FL anchored node

Reranks every TX/FL node with a CCSS anchor -- the full production universe
from mh2.candidates.load_anchors(), NOT just the ~87-115 ground-truth subjects
eval/rerank.py measures against. As of 2026-08-17 that is 115 TX nodes + 115
FL nodes = 230 (node, state) pairs; at the $10/115-subject rate eval/rerank.py
measured, a full run costs roughly $20 and is a one-time cost per node --
results are cached forever at config.RERANK_CACHE (durable, NOT under
data/build) and this script skips anything already cached, so a rerun after
new nodes are added only pays for the new ones.

After this runs, `python -m mh2.load_reranks` (also part of every
scripts/rebuild.py) loads the cache into model_reranks at zero further cost.
This script does NOT touch the database -- it only appends to the cache file,
so it is safe to run against a stale or half-built DB and safe to interrupt
and resume.
"""

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

from mh2.candidates import _TIER_RANK, generate, load_anchors  # noqa: E402
from mh2.rerank import (RERANK_DEPTH, append_cache, build_prompt,  # noqa: E402
                        call_model, load_cache)

STATES = ("TX", "FL")


def subjects(cur) -> list[tuple[str, tuple, str]]:
    """Every (node_id, anchors, state) pair the production generator serves."""
    anchors, _meta, _unanchored = load_anchors(cur)
    return [(node_id, node_anchors, state)
            for node_id, node_anchors in sorted(anchors.items())
            for state in STATES]


def candidate_lists(cur) -> dict:
    rows, _stats = generate(cur, states=set(STATES), top_k=RERANK_DEPTH)
    by_subject = defaultdict(list)
    for r in rows:
        by_subject[(r["node_id"], r["state"])].append(r)
    for group in by_subject.values():
        group.sort(key=lambda r: (_TIER_RANK[r["strength"]],
                                  -r["combined_score"], r["standard_code"]))
    return {k: [r["standard_code"] for r in v][:RERANK_DEPTH]
            for k, v in by_subject.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB))
    ap.add_argument("--limit", type=int, help="stop after N new API calls")
    args = ap.parse_args()

    config.ensure_dirs()
    con = sqlite3.connect(args.db)
    cur = con.cursor()

    all_subjects = subjects(cur)
    candidates = candidate_lists(cur)
    cache = load_cache(config.RERANK_CACHE)
    print(f"{len(all_subjects)} (node, state) pairs in production;"
          f" {len(cache)} already cached")

    try:
        import anthropic
    except ModuleNotFoundError:
        sys.exit("pip install anthropic  (NOT requirements-ml.txt — that"
                  " pulls torch)")
    client = anthropic.Anthropic()

    calls = 0
    for node_id, anchors, state in all_subjects:
        cand = candidates.get((node_id, state))
        if not cand:
            continue
        key = f"{node_id}|{state}"
        if key in cache:
            continue
        if args.limit is not None and calls >= args.limit:
            break
        prompt = build_prompt(cur, node_id, anchors, state, cand)
        ranking = call_model(client, prompt)
        append_cache(config.RERANK_CACHE, key, ranking)
        calls += 1
        print(f"  {calls:4d}  {key:24s} {len(cand):3d} candidates")

    print(f"\n{calls} new API calls. Run `python -m mh2.load_reranks` to"
          " load the cache into model_reranks.")
    con.close()


if __name__ == "__main__":
    main()
