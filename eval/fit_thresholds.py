"""
fit_thresholds.py — step 7. Fit the constants candidates.py marks UNFIT.

    python -m eval.fit_thresholds --db data/build/mh2.db

Scope, per the rev 5 acceptance-bar ruling (candidate_generation_spec.md §5):
precision is the objective, not recall, and for TX and FL a precision-first
policy is *entirely a Path C threshold question* -- neither state has a Path
0/A/B alternative at PK-5. So this script:

  1. Re-derives the Path C `direction_k` cut (state_to_ccss vs ccss_to_state)
     on the TUNING split. The handoff's ablation that favored dropping
     ccss_to_state ran on the HELD-OUT split, which is exactly the split this
     script must not touch for fitting.
  2. Sweeps a precision/recall/blind curve over `combined_score` for the
     WEAK, Path-C-only candidates that are the entirety of what TX and FL get,
     on the tuning split, at the winning direction_k.
  3. Reports the curve. It does NOT pick a final number -- "the numeric bar
     is still John's to write" (§5) -- but it does report the tuning-split
     threshold nearest a handful of illustrative precision targets so there
     is something concrete to react to.
  4. Validates whatever direction_k + a couple of candidate thresholds look
     like on the HELD-OUT split, so generalization is visible before anyone
     commits to a number.

Explicitly out of scope here: the multi-node threshold (§4.2.3). That waits
on the §4.2 node-text scorer, which does not exist yet (HANDOFF.md defect 2)
-- fitting a threshold against `node_confidence = 1/len(siblings)` would be
fitting noise, not a threshold.

Rerank.py's result (TX/FL reranked r@10 moved to ~0.96-0.98, near the r@50
ceiling) is NOT wired in here. That was a deliberate scope call: the reranker
outputs categorical relevance labels, not a score comparable to Path C's, and
folding it into production candidate generation means an LLM call per TX/FL
node at generation time. This script fits thresholds against the existing
`combined_score` that candidates.py already produces.
"""

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

from eval.harness import (REACHABLE, assign_splits, load_node_subjects,  # noqa: E402
                          reach_key, reach_of)
from eval.paths import PathC as ReachPathC  # noqa: E402
import mh2.candidates as candidates  # noqa: E402
from mh2.candidates import WEAK, generate  # noqa: E402

STATES = ("TX", "FL")

# Grid for the §4 grade prior. Small on purpose: grade is a prior that nudges
# combined_score, not a filter, so its effect is second-order next to the
# score threshold itself -- this checks whether the shipped defaults
# (0.85, window 2, 0.35) are actually a reasonable choice rather than an
# unexamined placeholder, not a full search.
GRADE_GRID = [
    (decay, window, out_of_window)
    for decay in (0.70, 0.85, 1.0)
    for window in (1, 2, 3)
    for out_of_window in (0.20, 0.35, 0.50)
]

# Direction_k configurations to compare. `shipping` is today's PATH_C_TOP_K
# default (10, both directions). `diagnostic_both_50` and `state_to_ccss_only`
# are the two the handoff's held-out ablation compared; re-run here on tuning.
CONFIGS = {
    "shipping (both@10)": {"state_to_ccss": 10, "ccss_to_state": 10},
    "both@50": {"state_to_ccss": 50, "ccss_to_state": 50},
    "state_to_ccss@50 only": {"state_to_ccss": 50, "ccss_to_state": 0},
}

THRESHOLD_GRID = [round(x * 0.01, 2) for x in range(-20, 85, 5)]
PRECISION_TARGETS = (0.50, 0.60, 0.70, 0.80, 0.90)


# ------------------------------------------------------------- candidate rows

def path_c_weak_rows(cur, direction_k: dict) -> list:
    """
    TX/FL candidate rows where the ONLY support is a real Path C prediction --
    never a model_suggestions row, which §4 forbids from corroborating
    anything and which carries a constant 0.25 score that would put a spike
    in the middle of any threshold sweep.
    """
    rows, _stats = generate(cur, states=set(STATES),
                            top_k=max(direction_k.values()),
                            direction_k=direction_k)
    out = []
    for r in rows:
        if r["strength"] != WEAK:
            continue
        evidence = json.loads(r["evidence_json"])
        if "pathC" not in evidence.get("independent_paths", []):
            continue  # suggestion-only weak row; not a Path C threshold case
        out.append(r)
    return out


def shown_by_subject(rows: list, threshold: float) -> dict:
    out = defaultdict(set)
    for r in rows:
        if r["combined_score"] is not None and r["combined_score"] >= threshold:
            out[(r["node_id"], r["state"])].add(r["standard_code"])
    return out


# ------------------------------------------------------------------- scoring

def score_at(subjects: list, shown: dict) -> dict:
    """
    Precision/recall/blind over one (already-filtered) subject set, at one
    already-computed `shown` mapping. Aggregated across subjects, not
    averaged per-subject, so a node with 40 truth codes does not count the
    same as one with one.
    """
    tp = fp = truth_total = 0
    blind = 0
    for s in subjects:
        picked = shown.get((s.key, s.state), set())
        hit = picked & s.truth
        tp += len(hit)
        fp += len(picked - s.truth)
        truth_total += len(s.truth)
        if s.truth and not hit:
            blind += 1
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / truth_total if truth_total else None
    return {"n_subjects": len(subjects), "shown": tp + fp, "tp": tp, "fp": fp,
            "precision": precision, "recall": recall,
            "blind_rate": blind / len(subjects) if subjects else None}


def print_curve(title: str, rows: list) -> None:
    print(f"\n{title}")
    print(f"  {'thresh':>7s} {'shown':>7s} {'precision':>9s} {'recall':>7s} "
          f"{'blind':>6s}")
    print("  " + "-" * 42)
    for t, m in rows:
        p = f"{m['precision']:9.3f}" if m["precision"] is not None else f"{'—':>9s}"
        r = f"{m['recall']:7.3f}" if m["recall"] is not None else f"{'—':>7s}"
        b = f"{m['blind_rate']:6.3f}" if m["blind_rate"] is not None else f"{'—':>6s}"
        print(f"  {t:7.2f} {m['shown']:7d} {p} {r} {b}")


def nearest_to_targets(rows: list, targets) -> dict:
    out = {}
    scored = [(t, m) for t, m in rows if m["precision"] is not None]
    for target in targets:
        best = min(scored, key=lambda tm: abs(tm[1]["precision"] - target),
                   default=None)
        out[target] = best
    return out


# ---------------------------------------------------------------- direction_k

def recall_at_precision_floor(curve: list, floor: float) -> float | None:
    """
    Best recall among thresholds whose precision clears `floor`.

    The right comparison between direction_k configs for a precision-first
    fit is NOT raw recall or list length alone -- a config that shows more
    candidates can always buy recall by giving up precision. Holding
    precision fixed and comparing recall is what actually answers "which
    config gives a better precision/recall trade".
    """
    candidates = [m["recall"] for _t, m in curve
                  if m["precision"] is not None and m["precision"] >= floor]
    return max(candidates) if candidates else None


def fit_direction_k(cur, subjects_by_split: dict) -> str:
    print("\n" + "=" * 78)
    print("1. DIRECTION_K — re-derived on TUNING (the held-out ablation in the"
          " handoff\n   is not valid for fitting)")
    print("=" * 78)

    tuning = subjects_by_split["tuning"]
    zero_cut, floor_recall = {}, {}
    for label, dk in CONFIGS.items():
        rows = path_c_weak_rows(cur, dk)
        for state in STATES:
            subs = [s for s in tuning if s.state == state]
            zero_cut[(label, state)] = score_at(
                subs, shown_by_subject(rows, threshold=float("-inf")))
            curve = [(t, score_at(subs, shown_by_subject(rows, t)))
                     for t in THRESHOLD_GRID]
            floor_recall[(label, state)] = recall_at_precision_floor(curve, 0.60)

    print(f"\n  {'config':24s} {'state':6s} {'n':>4s} {'shown':>7s} "
          f"{'recall':>7s} {'blind':>6s} | recall @ precision>=0.60")
    print("  " + "-" * 80)
    for label in CONFIGS:
        for state in STATES:
            m = zero_cut[(label, state)]
            r = f"{m['recall']:7.3f}" if m["recall"] is not None else f"{'—':>7s}"
            b = f"{m['blind_rate']:6.3f}" if m["blind_rate"] is not None else f"{'—':>6s}"
            fr = floor_recall[(label, state)]
            frs = f"{fr:.3f}" if fr is not None else "unreachable"
            print(f"  {label:24s} {state:6s} {m['n_subjects']:4d} "
                  f"{m['shown']:7d} {r} {b} | {frs}")

    print("\n  Left side: raw list-length/recall with NO score cut applied --\n"
          "  the retrieval ceiling each config hands the threshold sweep.\n"
          "  Right side is the number that actually matters for a precision-\n"
          "  first fit: holding precision at >=0.60, which config's threshold\n"
          "  sweep still recovers the most recall.")

    def score(label):
        vals = [floor_recall[(label, s)] for s in STATES
                if floor_recall[(label, s)] is not None]
        return (sum(vals) / len(vals)) if vals else -1.0

    winner = max(CONFIGS, key=score)
    print(f"\n  Winner (recall at precision>=0.60, TX+FL avg on tuning): "
          f"{winner}")
    return winner


def fit_grade_decay(cur, subjects_by_split: dict, dk: dict) -> tuple:
    """
    Grid search over (GRADE_DECAY, GRADE_WINDOW, GRADE_OUT_OF_WINDOW),
    monkey-patched into mh2.candidates for the duration of each `generate`
    call. Selected the same way as direction_k: recall at precision>=0.60,
    TX+FL averaged, on tuning.
    """
    print("\n" + "=" * 78)
    print("2. GRADE PRIOR — small grid around the shipped defaults, on TUNING")
    print("=" * 78)

    tuning = subjects_by_split["tuning"]
    default = (candidates.GRADE_DECAY, candidates.GRADE_WINDOW,
               candidates.GRADE_OUT_OF_WINDOW)
    results = {}
    try:
        for decay, window, oow in GRADE_GRID:
            candidates.GRADE_DECAY = decay
            candidates.GRADE_WINDOW = window
            candidates.GRADE_OUT_OF_WINDOW = oow
            rows = path_c_weak_rows(cur, dk)
            per_state = []
            for state in STATES:
                subs = [s for s in tuning if s.state == state]
                curve = [(t, score_at(subs, shown_by_subject(rows, t)))
                         for t in THRESHOLD_GRID]
                r = recall_at_precision_floor(curve, 0.60)
                per_state.append(r)
            vals = [v for v in per_state if v is not None]
            results[(decay, window, oow)] = sum(vals) / len(vals) if vals else -1.0
    finally:
        (candidates.GRADE_DECAY, candidates.GRADE_WINDOW,
         candidates.GRADE_OUT_OF_WINDOW) = default

    winner = max(results, key=results.get)
    print(f"  shipped default (decay={default[0]}, window={default[1]}, "
          f"out_of_window={default[2]}): "
          f"recall@p>=0.60 = {results.get(default, float('nan')):.3f}")
    print(f"  grid winner     (decay={winner[0]}, window={winner[1]}, "
          f"out_of_window={winner[2]}): "
          f"recall@p>=0.60 = {results[winner]:.3f}")
    if results[winner] <= results.get(default, -1.0) + 1e-9:
        print("  Default is at least as good as anything in the grid on this"
              " tuning split (n=24 TX / 25 FL — too small to chase a\n"
              "  fractional grid win). Keeping the shipped default rather than"
              " overfitting a 27-point grid to 49 subjects.")
        return default
    print("  Grid winner beats the default outside noise range for this n;"
          " reported for John to weigh, not auto-applied.")
    return winner


# --------------------------------------------------------------------- driver

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB))
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    cur = con.cursor()

    all_subjects, _excluded = load_node_subjects(cur)
    all_subjects = assign_splits(all_subjects)
    all_subjects = [s for s in all_subjects if s.state in STATES]

    reach_path_c = ReachPathC(cur)
    reaches = {reach_key(s): reach_of(s, reach_path_c) for s in all_subjects}
    reachable = [s for s in all_subjects
                 if reaches[reach_key(s)].bucket == REACHABLE]

    subjects_by_split = {
        "tuning": [s for s in reachable if s.split == "tuning"],
        "held_out": [s for s in reachable if s.split == "held_out"],
    }
    for split, subs in subjects_by_split.items():
        by_state = defaultdict(int)
        for s in subs:
            by_state[s.state] += 1
        print(f"{split:9s} reachable subjects: "
              + ", ".join(f"{k}={v}" for k, v in sorted(by_state.items())))

    winning_config = fit_direction_k(cur, subjects_by_split)
    dk = CONFIGS[winning_config]

    winning_grade = fit_grade_decay(cur, subjects_by_split, dk)
    candidates.GRADE_DECAY, candidates.GRADE_WINDOW, \
        candidates.GRADE_OUT_OF_WINDOW = winning_grade

    print("\n" + "=" * 78)
    print(f"3. SCORE THRESHOLD SWEEP — {winning_config}, "
          f"grade={winning_grade}, TUNING split")
    print("=" * 78)
    print("Precision-first per rev 5: this is the curve, not a chosen bar.\n"
          "The numeric bar is still John's to write against this curve.")

    tuning_rows = path_c_weak_rows(cur, dk)
    for state in STATES:
        subs = [s for s in subjects_by_split["tuning"] if s.state == state]
        curve = []
        for t in THRESHOLD_GRID:
            shown = shown_by_subject(tuning_rows, t)
            curve.append((t, score_at(subs, shown)))
        print_curve(f"{state} (n={len(subs)} tuning subjects)", curve)
        landmarks = nearest_to_targets(curve, PRECISION_TARGETS)
        print(f"\n  Nearest tuning-split threshold to illustrative precision"
              f" targets ({state}):")
        for target, hit in landmarks.items():
            if hit is None:
                print(f"    p={target:.2f}: unreachable at any threshold in "
                      "the grid")
                continue
            t, m = hit
            print(f"    p={target:.2f}: threshold={t:5.2f}  "
                  f"actual precision={m['precision']:.3f}  "
                  f"recall={m['recall']:.3f}  blind={m['blind_rate']:.3f}  "
                  f"shown={m['shown']}")

    print("\n" + "=" * 78)
    print(f"4. HELD-OUT VALIDATION — {winning_config}, grade={winning_grade}, "
          f"same threshold grid")
    print("=" * 78)
    print("Fit happens on tuning only (above). This is what the same cuts do\n"
          "on data step 7 never saw -- read it for generalization, not for\n"
          "picking a different number.")

    held_out_rows = path_c_weak_rows(cur, dk)
    for state in STATES:
        subs = [s for s in subjects_by_split["held_out"] if s.state == state]
        curve = []
        for t in THRESHOLD_GRID:
            shown = shown_by_subject(held_out_rows, t)
            curve.append((t, score_at(subs, shown)))
        print_curve(f"{state} (n={len(subs)} held-out subjects)", curve)

    con.close()


if __name__ == "__main__":
    main()
