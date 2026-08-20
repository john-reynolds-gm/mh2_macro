"""
rerank.py — does a second model pass fix the ranking Path C gets wrong?

    python -m eval.rerank --db data/build/mh2.db --limit 5      # smoke test
    python -m eval.rerank --db data/build/mh2.db                # full run
    python -m eval.rerank --score-only                          # re-score cache

The question, and why this is the right shape for it
----------------------------------------------------
Step 8's diagnostics said the retrieval is fine and the ordering is not:
TX recall@50 is 0.946 and recall@10 is 0.804; FL is 0.963 against 0.862. The
correct answer is already in the candidate set about 95% of the time and is
sitting below rank 10. That is the exact failure a reranker addresses — a fast
bi-encoder gets the right answer into the top 50 but cannot order it, so a
slower model reads the actual standard text and reorders.

It can only fail to find what is already there, which bounds the upside at
recall@50 and makes the experiment cheap to interpret: if reranked recall@10
does not move toward 0.95, the ordering was not the recoverable problem.

What it is NOT
--------------
Not ground truth, ever. The ladder hand tags stay the labels (§5), so this is
measured the same way every other path is measured and can be shown not to
work. It also does not repeat the `scored_alignments` circularity §5 warns
about: that was a model verdict used as a LABEL. This is a model used as a
RANKER over candidates, scored against hand-written labels it never sees.

Leakage
-------
The prompt carries the node text and the node's CCSS anchors — precisely what
the generator gets. The held-out state codes are never in the prompt. The
candidate list comes from `mh2.candidates`, which reads anchors only.
"""

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

from eval.harness import assign_splits, load_node_subjects  # noqa: E402
from mh2.candidates import generate  # noqa: E402
from mh2.rerank import (RERANK_DEPTH as CANDIDATE_DEPTH, append_cache,  # noqa: E402
                        build_prompt, call_model, clean, load_cache)

CACHE = config.RERANK_CACHE


# ------------------------------------------------------------------ scoring

def metrics(ranked: list[str], truth) -> dict:
    top = lambda k: len(set(ranked[:k]) & truth) / len(truth)  # noqa: E731
    rr = next((1 / i for i, c in enumerate(ranked, 1) if c in truth), 0.0)
    return {"r5": top(5), "r10": top(10), "r50": top(50),
            "p5": len(set(ranked[:5]) & truth) / 5,
            "mrr": rr, "blind": 0.0 if set(ranked) & truth else 1.0}


def report(rows: list[dict]) -> None:
    by_state = defaultdict(list)
    for row in rows:
        by_state[row["state"]].append(row)

    print(f"\n{'':6s} {'':4s} | {'baseline (embedding order)':^33s} | "
          f"{'reranked':^33s}")
    print(f"{'state':6s} {'n':>4s} | {'r@5':>6s} {'r@10':>7s} {'P@5':>6s} "
          f"{'MRR':>6s} {'blind':>6s} | {'r@5':>6s} {'r@10':>7s} {'P@5':>6s} "
          f"{'MRR':>6s} {'blind':>6s} | {'Δr@10':>7s}")
    print("-" * 100)
    for state, group in sorted(by_state.items()):
        n = len(group)
        b = {k: sum(r["base"][k] for r in group) / n for k in group[0]["base"]}
        m = {k: sum(r["rank"][k] for r in group) / n for k in group[0]["rank"]}
        print(f"{state:6s} {n:4d} | {b['r5']:6.3f} {b['r10']:7.3f} "
              f"{b['p5']:6.3f} {b['mrr']:6.3f} {b['blind']:6.3f} | "
              f"{m['r5']:6.3f} {m['r10']:7.3f} {m['p5']:6.3f} {m['mrr']:6.3f} "
              f"{m['blind']:6.3f} | {m['r10'] - b['r10']:+7.3f}")

    ceiling = {s: sum(r["base"]["r50"] for r in g) / len(g)
               for s, g in by_state.items()}
    print("\n  Ceiling (recall@50 — the reranker cannot exceed this): "
          + "  ".join(f"{s} {v:.3f}" for s, v in sorted(ceiling.items())))
    bad = defaultdict(int)
    for row in rows:
        for k, v in row["clean"].items():
            bad[k] += v
    print(f"  Output hygiene: {dict(bad)}")


# -------------------------------------------------------------------- driver

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB))
    ap.add_argument("--limit", type=int, help="stop after N subjects")
    ap.add_argument("--split", default="held_out",
                    choices=["held_out", "tuning", "all"])
    ap.add_argument("--score-only", action="store_true",
                    help="score the cache, make no API calls")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    cur = con.cursor()

    subjects = assign_splits(load_node_subjects(cur)[0])
    subjects = [s for s in subjects if s.state in ("TX", "FL")
                and (args.split == "all" or s.split == args.split)]

    rows, _ = generate(cur, states={"TX", "FL"}, top_k=CANDIDATE_DEPTH)
    ranked = defaultdict(list)
    for r in rows:
        ranked[(r["node_id"], r["state"])].append(r)
    from mh2.candidates import _TIER_RANK
    for group in ranked.values():
        group.sort(key=lambda r: (_TIER_RANK[r["strength"]],
                                  -r["combined_score"], r["standard_code"]))

    cache = load_cache(CACHE)
    print(f"cache: {len(cache)} subjects already reranked")

    client = None
    if not args.score_only:
        try:
            import anthropic
        except ModuleNotFoundError:
            sys.exit("pip install anthropic  (declared in requirements-ml.txt)")
        client = anthropic.Anthropic()

    results, calls = [], 0
    for subject in subjects:
        # Capped at CANDIDATE_DEPTH, not at whatever the generator emitted.
        # A node with many anchors accumulates 100-170 candidates; reranking
        # all of them would be a different experiment with a different ceiling.
        # recall@50 is the number step 8 measured, so the reranker gets exactly
        # the set that number was computed over and its upside is bounded by it.
        candidates = [r["standard_code"]
                      for r in ranked.get((subject.key, subject.state), ())
                      ][:CANDIDATE_DEPTH]
        if not candidates:
            continue
        key = f"{subject.key}|{subject.state}"

        if key in cache:
            order = cache[key]
        elif args.score_only:
            continue
        else:
            if args.limit is not None and calls >= args.limit:
                break
            prompt = build_prompt(cur, subject.key, subject.anchors,
                                  subject.state, candidates)
            order = call_model(client, prompt)
            calls += 1
            append_cache(CACHE, key, order)
            print(f"  {calls:4d}  {key:24s} {len(candidates):3d} candidates")

        cleaned, hygiene = clean(order, candidates)
        results.append({"state": subject.state, "clean": hygiene,
                        "base": metrics(candidates, subject.truth),
                        "rank": metrics(cleaned, subject.truth)})

    if not results:
        sys.exit("Nothing scored. Run without --score-only to populate the cache.")
    print(f"\nscored {len(results)} subjects ({calls} new API calls)")
    report(results)
    con.close()


if __name__ == "__main__":
    main()
