"""
load_reranks.py — load the durable rerank cache into model_reranks.

    python -m mh2.load_reranks --db data/build/mh2.db

Makes NO API calls, ever. scripts/run_reranks.py is the only thing that spends
money; this just replays whatever it already cached at config.RERANK_CACHE
into the database, so a full `rm -rf data/build && rebuild.py` never has to
pay for reranking twice. If the cache file is empty or missing (nobody has
run scripts/run_reranks.py yet), this loads zero rows and candidates.py's
`browse_rank` stays NULL everywhere -- a quiet no-op, not an error.

Re-cleaning against the CURRENT candidate list
-----------------------------------------------
The cache holds the model's raw ranking from whenever it was called. Between
then and now the underlying candidates can shift (a source file reloads, a
threshold constant changes what Path C emits). Re-running `clean()` here
against TODAY's candidate list, rather than trusting the cache blindly, means
a code the model ranked that no longer exists in the candidate set is dropped
rather than inserted as an orphan `model_reranks` row with nothing in
`candidates` to attach to.
"""

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

from mh2.candidates import _TIER_RANK, generate  # noqa: E402
from mh2.rerank import RERANK_DEPTH, clean, load_cache  # noqa: E402

MODEL_VERSION = "mh2.rerank:claude-opus-5"
BROWSE_TOP_N = 10

INSERT = ("INSERT OR REPLACE INTO model_reranks"
          " (node_id, state, standard_code, rerank_rank, relevance,"
          " model_version) VALUES (?,?,?,?,?,?)")


def current_candidates(cur) -> dict:
    """(node_id, state) -> candidate codes in the SAME order rerank.py fed the
    model: tier then combined_score, capped at RERANK_DEPTH."""
    rows, _stats = generate(cur, states={"TX", "FL"}, top_k=RERANK_DEPTH)
    by_subject = defaultdict(list)
    for r in rows:
        by_subject[(r["node_id"], r["state"])].append(r)
    for group in by_subject.values():
        group.sort(key=lambda r: (_TIER_RANK[r["strength"]],
                                  -r["combined_score"], r["standard_code"]))
    return {k: [r["standard_code"] for r in v][:RERANK_DEPTH]
            for k, v in by_subject.items()}


def load(con, cache_path: Path) -> dict:
    cur = con.cursor()
    cache = load_cache(cache_path)
    candidates = current_candidates(cur)

    stats = {"cached_subjects": len(cache), "loaded": 0,
             "stale_no_candidates": 0, "rows": 0}
    con.execute("DELETE FROM model_reranks")
    for key, ranking in cache.items():
        node_id, state = key.rsplit("|", 1)
        cand = candidates.get((node_id, state))
        if not cand:
            stats["stale_no_candidates"] += 1
            continue
        ordered, _hygiene = clean(ranking, cand)
        by_code = {item.get("code"): item.get("relevance") for item in ranking}
        rows = [(node_id, state, code, i, by_code.get(code), MODEL_VERSION)
                for i, code in enumerate(ordered[:BROWSE_TOP_N], start=1)]
        cur.executemany(INSERT, rows)
        stats["loaded"] += 1
        stats["rows"] += len(rows)
    con.commit()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB))
    ap.add_argument("--cache", default=str(config.RERANK_CACHE))
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    cache_path = Path(args.cache)
    if not cache_path.exists():
        print(f"no rerank cache at {cache_path} — model_reranks stays empty."
              " Run scripts/run_reranks.py to populate it.")
        con.close()
        return

    stats = load(con, cache_path)
    print(f"rerank cache subjects   {stats['cached_subjects']:6,d}")
    print(f"loaded (had candidates) {stats['loaded']:6,d}")
    print(f"stale (no candidates)   {stats['stale_no_candidates']:6,d}")
    print(f"model_reranks rows      {stats['rows']:6,d}")
    con.close()


if __name__ == "__main__":
    main()
