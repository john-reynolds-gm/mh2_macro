"""
diagnostics.py — the step 8 deliverable: what Path C actually bought.

    python -m eval.diagnostics --db data/build/mh2.db

Five numbers, all per state, all on 5a's reach bucketing so nothing prints a
misleading zero, plus the step 9 placement metric bundled in while the harness
is open. Everything here reads the hand-read ladder labels as truth. Nothing
here sets a threshold, blends a score, or filters on grade.

The question this exists to answer
----------------------------------
Step 7 invests in ranking quality. Number 1 below says whether that investment
can pay:

  large gap between recall@50 and recall@10   the right answers are IN the set
                                              and ranked badly. Step 7 is the
                                              highest-value work available.
  small gap at a low absolute number          the model is not retrieving them
                                              at all. Step 7 would tune rank
                                              order over a set that does not
                                              contain the answer.

For that comparison to mean anything the candidate list has to be deeper than
10, so the generator is run at two Path C cuts: the shipping cut
(PATH_C_TOP_K = 10) and a diagnostic cut of 50. Raising the cut is a parameter
of the measurement, not a fallback bolted on to improve a number -- at the
shipping cut, recall@50 could not exceed recall@10 by construction and the
comparison would read as "the model has nothing" no matter what the model had.

Direction of the state->state number
------------------------------------
§5 asks for state->state recall@25 against the other-state ladder codes that
co-occur with a TX or FL code on the same node. Those co-occurrences are hand
written, so they are ground truth for this direction.

The available runs only ever predict INTO the Big Three -- Run 3a is Big Three
-> Big Three and Run 3b is the gaps sheet -> Big Three -- so the arrow is
measured as `other-state anchor -> TX/FL target`, not the reverse. Same pairs,
same hand-written truth, the direction the data has. It is also the direction
§3.5.1 needs: given the state codes an author already put on a node, find the
TX and FL codes that correspond.
"""

import argparse
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

from eval.harness import (EXCLUDED, REACHABLE, UNAVAILABLE, Subject,  # noqa: E402
                          assign_splits, load_node_subjects, node_placement,
                          reach_key, reach_of)
from eval.paths import UnionPath, all_paths  # noqa: E402
from mh2.candidates import PATH_C_TOP_K, generate, ranker  # noqa: E402

BIG_THREE = ("CA", "FL", "TX")
DIAGNOSTIC_TOP_K = 50
STATE_TO_STATE_K = 25


# ------------------------------------------------- 1, 2, 4: the ranked numbers

def score_group(subjects, rank, reaches) -> dict:
    """
    One (state, split) group at both cuts, with the three-way reach split.

    recall@10 and recall@50 are computed over the SAME ranked list so the gap
    between them is a statement about rank order and nothing else.
    """
    scored = [s for s in subjects if reaches[reach_key(s)].bucket != EXCLUDED]
    if not scored:
        return {}
    if all(reaches[reach_key(s)].bucket == UNAVAILABLE for s in scored):
        return {"subjects": len(scored), "unavailable": True}

    raw, cond = defaultdict(float), defaultdict(float)
    n_reach = 0
    for s in scored:
        ranked = rank(s)
        top = {k: len(set(ranked[:k]) & s.truth) / len(s.truth)
               for k in (10, 50)}
        got = {"r10": top[10], "r50": top[50],
               "blind": 0.0 if set(ranked) & s.truth else 1.0,
               "listlen": float(len(ranked))}
        for k, v in got.items():
            raw[k] += v
        if reaches[reach_key(s)].bucket == REACHABLE:
            n_reach += 1
            for k, v in got.items():
                cond[k] += v

    truth, vocab, anchor = set(), set(), set()
    for s in scored:
        truth |= s.truth
        vocab |= reaches[reach_key(s)].vocab_reached
        anchor |= reaches[reach_key(s)].anchor_reached

    out = {"subjects": len(scored), "unavailable": False, "n_reachable": n_reach,
           "coverage_vocab": len(vocab) / len(truth) if truth else 0.0,
           "coverage_anchor": len(anchor) / len(truth) if truth else 0.0,
           "buckets": Counter(reaches[reach_key(s)].bucket for s in scored)}
    for k, v in raw.items():
        out[f"raw_{k}"] = v / len(scored)
    for k in ("r10", "r50", "blind", "listlen"):
        out[f"cond_{k}"] = cond[k] / n_reach if n_reach else None
    return out


def print_recall_block(title: str, rows: dict) -> None:
    print(f"\n{title}")
    print(f"  {'state':6s} {'split':9s} {'n':>4s} {'rch':>4s} | "
          f"{'cov_v':>6s} {'cov_a':>6s} | "
          f"{'cond@10':>8s} {'cond@50':>8s} {'gap':>7s} | "
          f"{'raw@10':>7s} {'raw@50':>7s} {'gap':>7s} | "
          f"{'blind':>6s} {'len':>6s}")
    print("  " + "-" * 116)
    for (state, split), m in sorted(rows.items()):
        if not m:
            continue
        if m.get("unavailable"):
            print(f"  {state:6s} {split:9s} {m['subjects']:4d} {'—':>4s} | "
                  f"{'path unavailable — no vocabulary for this state':<60s}")
            continue
        c10, c50 = m["cond_r10"], m["cond_r50"]
        cond = (f"{c10:8.3f} {c50:8.3f} {c50 - c10:+7.3f}"
                if c10 is not None else f"{'—':>8s} {'—':>8s} {'—':>7s}")
        print(f"  {state:6s} {split:9s} {m['subjects']:4d} {m['n_reachable']:4d} | "
              f"{m['coverage_vocab']:6.1%} {m['coverage_anchor']:6.1%} | {cond} | "
              f"{m['raw_r10']:7.3f} {m['raw_r50']:7.3f} "
              f"{m['raw_r50'] - m['raw_r10']:+7.3f} | "
              f"{m['raw_blind']:6.3f} {m['raw_listlen']:6.1f}")


# ------------------------------------------------ 3: state -> state recall@25

def state_to_state_recall(cur, k: int = STATE_TO_STATE_K) -> dict:
    """
    Ground truth is co-occurrence on a hand-written ladder node.

    An other-state code and a TX or FL code on the same node were put there by
    an author reading both standards' text. That is a hand-written statement
    that the two correspond, and it is the only ground truth this project has
    for the state->state direction.
    """
    by_node = defaultdict(lambda: defaultdict(set))
    for node_id, code, state in cur.execute(
            "SELECT node_id, standard_code, state FROM node_standards_parsed"):
        by_node[node_id][state].add(code)

    # anchor -> ranked target list, per target state.
    preds = defaultdict(dict)
    for anchor, state, code, rank in cur.execute(
            "SELECT anchor_code, state, state_code, rank"
            " FROM model_state_predictions"):
        prev = preds[(anchor, state)].get(code)
        if prev is None or rank < prev:
            preds[(anchor, state)][code] = rank

    out = {}
    for target_state in ("TX", "FL"):
        pairs = hit = 0
        anchors_present = anchors_missing = 0
        seen_anchor = set()
        truth_codes = set()
        node_truth = node_hit = 0        # any-anchor grain
        for node_id, tags in by_node.items():
            targets = tags.get(target_state, set())
            if not targets:
                continue
            others = {c for st, codes in tags.items()
                      if st not in (None,) + BIG_THREE for c in codes}
            if not others:
                continue
            union = {}                   # truth code -> best rank over anchors
            for anchor in others:
                ranked = preds.get((anchor, target_state))
                if anchor not in seen_anchor:
                    seen_anchor.add(anchor)
                    if ranked:
                        anchors_present += 1
                    else:
                        anchors_missing += 1
                if not ranked:
                    continue
                for truth in targets:
                    pairs += 1
                    truth_codes.add(truth)
                    r = ranked.get(truth)
                    if r is not None and r <= k:
                        hit += 1
                        if truth not in union or r < union[truth]:
                            union[truth] = r
            # §3.5.1 hands the generator every state code on the node at once,
            # so the union over anchors is the number that direction lives or
            # dies by. The per-pair figure is the strict floor beneath it.
            node_truth += len(targets)
            node_hit += len(union)
        out[target_state] = {
            "pairs": pairs, "hit": hit,
            "recall": hit / pairs if pairs else None,
            "anchors_in_run": anchors_present,
            "anchors_absent": anchors_missing,
            "truth_codes": len(truth_codes),
            "node_truth": node_truth, "node_hit": node_hit,
            "node_recall": node_hit / node_truth if node_truth else None,
        }
    return out


def print_state_to_state(res: dict, k: int) -> None:
    print(f"\nSTATE->STATE recall@{k} — other-state ladder anchor -> TX/FL truth")
    print("  Ground truth is co-occurrence on a hand-written node. Arrow runs")
    print("  other-state -> Big Three because that is the only direction the")
    print("  runs predict; targets are never anything but TX/FL/CA.")
    print(f"\n  {'target':7s} {'anchors':>8s} {'absent':>7s} | "
          f"{'pairs':>7s} {'hit':>6s} {'per-pair':>9s} | "
          f"{'truth':>6s} {'hit':>6s} {'any-anchor':>11s}")
    print("  " + "-" * 76)
    for state, m in sorted(res.items()):
        rec = f"{m['recall']:9.3f}" if m["recall"] is not None else f"{'—':>9s}"
        nrec = (f"{m['node_recall']:11.3f}" if m["node_recall"] is not None
                else f"{'—':>11s}")
        print(f"  {state:7s} {m['anchors_in_run']:8d} {m['anchors_absent']:7d} | "
              f"{m['pairs']:7d} {m['hit']:6d} {rec} | "
              f"{m['node_truth']:6d} {m['node_hit']:6d} {nrec}")
    print("\n  per-pair    every (anchor, truth) combination must hit. Strict")
    print("              floor: one node with 5 anchors and 3 truth codes is")
    print("              15 chances to be marked wrong.")
    print("  any-anchor  the truth code is found by ANY of the node's anchors.")
    print("              This is what §3.5.1 actually does — it hands the")
    print("              generator every state code on the node at once.")
    print("  absent      anchors with no row in the run at all. Run 3b is short")
    print("              134 leaf codes, and this is where that lands.")


# ------------------------------------------- 5: score distributions per path

def score_distributions(rows: list) -> dict:
    """
    Per path per state, over the generated candidates.

    Read from evidence_json, NOT from the score columns. `score_model` is one
    column carrying both model_predictions and model_suggestions (§6), and §4
    forbids those two from being read as one thing -- a suggestion is a flat
    0.25 constant with no ranking behind it, and averaging it into Path C's
    distribution would put a spike in the middle of the band step 7 has to cut
    against. evidence_json keeps them apart, so this does too.

    Top-1 is broken out because that is the score a threshold would actually
    cut against. A narrow band there means the model is not discriminating and
    step 7 has nothing to work with.
    """
    import json

    per = defaultdict(list)
    top1 = defaultdict(list)
    best_per_subject = {}

    for r in rows:
        evidence = json.loads(r["evidence_json"])
        for path, support in evidence.get("paths", {}).items():
            v = support.get("score")
            if v is None:
                continue
            per[(r["state"], path)].append(v)
            key = (path, (r["node_id"], r["state"]))
            if key not in best_per_subject or v > best_per_subject[key]["score"]:
                best_per_subject[key] = {"score": v}

    for (path, key), d in best_per_subject.items():
        top1[(key[1], path)].append(d["score"])

    out = {}
    for key, vals in per.items():
        vals.sort()
        t = sorted(top1.get(key, []))
        out[key] = {
            "n": len(vals),
            "min": vals[0], "p25": vals[len(vals) // 4],
            "median": statistics.median(vals),
            "p75": vals[3 * len(vals) // 4], "max": vals[-1],
            "top1_n": len(t),
            "top1_median": statistics.median(t) if t else None,
            "top1_iqr": (t[3 * len(t) // 4] - t[len(t) // 4]) if len(t) >= 4
            else None,
        }
    return out


def print_scores(dist: dict) -> None:
    print("\nSCORE DISTRIBUTIONS per path per state — never blended (§4)")
    print("  Read the top-1 IQR: a narrow band means the model is not"
          " discriminating\n  and step 7 has nothing to fit a threshold"
          " against.")
    print(f"\n  {'state':6s} {'path':7s} {'n':>7s} {'min':>7s} {'p25':>7s} "
          f"{'med':>7s} {'p75':>7s} {'max':>7s} | {'top1 n':>7s} "
          f"{'top1 med':>9s} {'top1 IQR':>9s}")
    print("  " + "-" * 100)
    for (state, path), m in sorted(dist.items()):
        iqr = f"{m['top1_iqr']:9.3f}" if m["top1_iqr"] is not None else f"{'—':>9s}"
        med = (f"{m['top1_median']:9.3f}" if m["top1_median"] is not None
               else f"{'—':>9s}")
        print(f"  {state:6s} {path:7s} {m['n']:7d} {m['min']:7.3f} "
              f"{m['p25']:7.3f} {m['median']:7.3f} {m['p75']:7.3f} "
              f"{m['max']:7.3f} | {m['top1_n']:7d} {med} {iqr}")


# ---------------------------------------------------- step 9: node placement

def placement(rows: list, subjects: list) -> dict:
    """
    Top-node-only placement (§4.3), against the author's SET of nodes.

    Multi-node assignment waits on step 7's threshold, so the generator's
    prediction here is one node per (concept/skill, state code): the highest
    node_confidence, tie-broken on combined_score then node id so a rerun is
    comparable. The truth is a set, because a standard may legitimately sit on
    several nodes and scoring against one would mark a correct assignment wrong
    whenever the author used more than one.

    Both numbers or neither (§5): hit rate alone is gamed by assigning every
    standard to every node, and top-node-only is the degenerate opposite -- it
    cannot over-assign, so its Jaccard is capped below 1.0 wherever the author
    used several nodes. That ceiling is reported alongside, or the Jaccard
    reads as a failure when it is a consequence of the design.
    """
    truth_nodes = defaultdict(set)
    for s in subjects:
        for code, nodes in s.truth_nodes.items():
            truth_nodes[(s.state, code)] |= set(nodes)

    best = {}
    for r in rows:
        key = (r["state"], r["standard_code"])
        cand = (r["node_confidence"] or 0.0, r["combined_score"], r["node_id"])
        if key not in best or cand > best[key]:
            best[key] = cand

    out = {}
    for state in BIG_THREE:
        hits, jac, ceiling, n, surfaced = 0, 0.0, 0.0, 0, 0
        for (st, code), actual in truth_nodes.items():
            if st != state or not actual:
                continue
            n += 1
            chosen = best.get((st, code))
            if chosen is None:
                continue                       # never surfaced: not placeable
            surfaced += 1
            hit, agreement = node_placement(frozenset({chosen[2]}), actual)
            hits += 1 if hit else 0
            jac += agreement
            ceiling += 1 / len(actual)         # best a single node can score
        out[state] = {
            "truth_standards": n, "surfaced": surfaced,
            "hit_rate": hits / surfaced if surfaced else None,
            "jaccard": jac / surfaced if surfaced else None,
            "jaccard_ceiling": ceiling / surfaced if surfaced else None,
        }
    return out


def print_placement(res: dict) -> None:
    print("\nNODE PLACEMENT (top-node-only, §4.3) — hit rate AND Jaccard (§5)")
    print(f"  {'state':6s} {'truth':>6s} {'surfaced':>9s} {'hit rate':>9s} "
          f"{'jaccard':>8s} {'ceiling':>8s}")
    print("  " + "-" * 52)
    for state, m in sorted(res.items()):
        if not m["surfaced"]:
            print(f"  {state:6s} {m['truth_standards']:6d} {0:9d} "
                  f"{'—':>9s} {'—':>8s} {'—':>8s}")
            continue
        print(f"  {state:6s} {m['truth_standards']:6d} {m['surfaced']:9d} "
              f"{m['hit_rate']:9.3f} {m['jaccard']:8.3f} "
              f"{m['jaccard_ceiling']:8.3f}")
    print("\n  hit rate  = the chosen node is one the author used.")
    print("  jaccard   = |chosen & author| / |chosen | author|, capped by")
    print("              `ceiling` because one predicted node cannot match a")
    print("              multi-node truth set. Read the gap to the ceiling,")
    print("              not the absolute Jaccard.")
    print("  surfaced  = truth standards the generator produced at all. The")
    print("              rest are an ALIGNMENT miss, not a placement miss.")


# -------------------------------------------------------------------- driver

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB))
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    cur = con.cursor()

    subjects, excluded = load_node_subjects(cur)
    subjects = assign_splits(subjects)
    generator = UnionPath(all_paths(cur))
    reaches = {reach_key(s): reach_of(s, generator) for s in subjects}

    groups = defaultdict(list)
    for s in subjects:
        groups[(s.state, s.split)].append(s)

    print("=" * 118)
    print("STEP 8 DIAGNOSTICS — Path C loaded, node grain, hand-read ladder"
          " labels as truth")
    print("=" * 118)
    print("\nExclusions (counted, never scored as misses):")
    for label, n in sorted(excluded.items()):
        print(f"  {label:34s} {n}")
    print("\nCA is quarantined as a TARGET state in Path C (step 8 gate):"
          " its Run 1 corpus\nwas the 14-code CA-not-CCSS set. CA numbers"
          " below are Path 0/A/B only.")

    for cut in (PATH_C_TOP_K, DIAGNOSTIC_TOP_K):
        rank = ranker(cur, top_k=cut)
        rows = {k: score_group(v, rank, reaches) for k, v in groups.items()}
        label = "shipping cut" if cut == PATH_C_TOP_K else "diagnostic cut"
        print_recall_block(
            f"RECALL@10 vs RECALL@50 — Path C top-{cut} ({label})", rows)

    print_state_to_state(state_to_state_recall(cur), STATE_TO_STATE_K)

    rows, _stats = generate(cur, top_k=DIAGNOSTIC_TOP_K)
    print_scores(score_distributions(rows))
    print_placement(placement(rows, subjects))

    con.close()


if __name__ == "__main__":
    main()
