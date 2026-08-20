"""
harness.py — measure candidate generation against the hand tags.

Built BEFORE the generator, on purpose. It sets every threshold and decides
whether anything ships, and a harness written after the thing it measures tends
to measure what that thing happens to do.

Two datasets, two grains, reported SEPARATELY and never blended:

  node grain           the parsed ladder cells. Input: the CCSS codes on a
                       node. Held out: the state codes on the same node.
                       Primary for TX and FL.
  concept/skill grain  the workbook rows. Input: the CCSSM tags. Held out: the
                       Big Three tags. Primary for CA, whose node-level labels
                       are too thin to tune on, and a secondary check for
                       TX and FL.

Metrics are also per state and never blended across states. A number averaged
over CA and TX describes neither.

Two claims are tracked apart because a generator can be good at one and bad at
the other:

  alignment       this state standard belongs in this concept/skill
  node placement  it belongs on THIS node

Node placement scores against a SET of nodes, because one standard may
legitimately sit on several. Both hit rate and set agreement are reported, and
that is not optional: hit rate alone is trivially gamed by assigning every
standard to every node, and the Jaccard is the only thing that catches it.

Reach gating (§5) comes before any scoring, and is the reason a zero from this
harness is now interpretable. Every subject lands in exactly one bucket:

  excluded          no anchor at all — not a test
  path unavailable  the path has no vocabulary for this state. Printed as
                    `path unavailable`, NEVER as 0.000: no ranking could have
                    produced a correct answer, so a zero would describe the
                    data and be read as a broken ranker.
  unreachable       vocabulary exists, but none of this subject's truth codes
                    are linked from its anchors
  reachable         at least one truth code is linked — the only real test

Three numbers per (state x path), never one:

  coverage      share of truth codes reachable. A property of the DATA.
  conditional   recall over reachable subjects. A property of the RANKER.
  raw           unconditioned. What the acceptance bar is written against.

Reach is computed at two levels and the gap is reported, because they imply
different work: a truth code with vocabulary reach but no anchor reach means the
crosswalk is incomplete and fixable; no vocabulary reach at all means a
different path is needed.

A `ranker` is any callable taking a Subject and returning state codes worst-
last. Path B's is deliberately thin — it exists so the plumbing and the metric
code run end to end and produce real numbers. The generator lands in step 6 and
plugs in here unchanged.

Run: python -m eval.harness
"""

import argparse
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

from eval.paths import UnionPath, all_paths  # noqa: E402
from mh2.candidates import ranker  # noqa: E402

BIG_THREE = ("CA", "FL", "TX")

# 30% tuning / 70% held out (§5). Fixed so a rerun is comparable with the last
# one; thresholds fitted on the tuning split would be meaningless otherwise.
TUNING_FRACTION = 0.30
SPLIT_SEED = 20250813


class Subject(NamedTuple):
    """One thing to be scored: a node or a concept/skill, for one state."""
    key: str
    grain: str                      # node | concept_skill
    stem_id: str
    state: str
    anchors: tuple[str, ...]        # the CCSS codes given to the generator
    truth: frozenset[str]           # the state codes held out
    truth_nodes: dict               # state code -> frozenset of node ids
    split: str = "held_out"


# ---------------------------------------------------------------- datasets

def load_node_subjects(cur) -> tuple[list[Subject], dict]:
    """
    Node grain, from node_standards_parsed.

    Truth is a SET of node ids per code, built across the whole concept/skill:
    if the author put TX.3.3A on three nodes, all three are correct answers for
    that standard. Scoring against one would mark two of them wrong.
    """
    anchors = defaultdict(set)
    state_tags = defaultdict(set)
    node_meta = {}

    for node_id, stem_id, concept_skill in cur.execute(
            "SELECT node_id, stem_id, concept_skill FROM nodes"):
        node_meta[node_id] = (stem_id, concept_skill)

    for node_id, code, state in cur.execute(
            "SELECT node_id, standard_code, state FROM node_standards_parsed"):
        if state is None:
            anchors[node_id].add(code)
        else:
            state_tags[(node_id, state)].add(code)

    # Which nodes each code sits on, within its concept/skill.
    nodes_for_code = defaultdict(set)
    for (node_id, state), codes in state_tags.items():
        _stem, cs = node_meta.get(node_id, ("", ""))
        for code in codes:
            nodes_for_code[(cs, code)].add(node_id)

    subjects, excluded = [], defaultdict(int)
    for (node_id, state), codes in sorted(state_tags.items()):
        stem_id, cs = node_meta.get(node_id, ("", ""))
        if not anchors.get(node_id):
            # No CCSS anchor: Paths 0, A and B cannot produce anything for it.
            # Excluded from the ALIGNMENT metric and counted, never quietly
            # scored as a miss -- that would blame the generator for a gap in
            # the input. These are the §3.5 nodes.
            excluded["node_no_ccss_anchor"] += 1
            continue
        subjects.append(Subject(
            key=node_id, grain="node", stem_id=stem_id, state=state,
            anchors=tuple(sorted(anchors[node_id])),
            truth=frozenset(codes),
            truth_nodes={c: frozenset(nodes_for_code[(cs, c)]) for c in codes}))
    return subjects, dict(excluded)


def load_concept_subjects(cur) -> tuple[list[Subject], dict]:
    """
    Concept/skill grain, from the workbook's hand tags.

    Codes with no standard text are excluded and counted (§5): they are source
    typos and range notation that no generator could ever return.
    """
    have_text = {r[0] for r in cur.execute(
        "SELECT standard_id FROM standards WHERE text IS NOT NULL AND text != ''")}

    anchors = defaultdict(set)
    state_tags = defaultdict(set)
    meta = {}
    for concept_id, stem_id in cur.execute(
            "SELECT concept_id, stem_id FROM concepts"):
        meta[concept_id] = stem_id

    excluded = defaultdict(int)
    for concept_id, code, bucket in cur.execute(
            "SELECT concept_id, standard_id, bucket FROM concept_standards"):
        state = code.split(".")[0].upper()
        if bucket == "ccssm" or state not in BIG_THREE:
            if bucket == "ccssm":
                anchors[concept_id].add(code)
            continue
        if code not in have_text:
            excluded["concept_tag_no_standard_text"] += 1
            continue
        state_tags[(concept_id, state)].add(code)

    subjects = []
    for (concept_id, state), codes in sorted(state_tags.items()):
        if not anchors.get(concept_id):
            excluded["concept_no_ccssm_anchor"] += 1
            continue
        subjects.append(Subject(
            key=str(concept_id), grain="concept_skill",
            stem_id=meta.get(concept_id, ""), state=state,
            anchors=tuple(sorted(anchors[concept_id])),
            truth=frozenset(codes), truth_nodes={}))
    return subjects, dict(excluded)


def assign_splits(subjects: list[Subject]) -> list[Subject]:
    """
    30% tuning / 70% held out, stratified by stem.

    Stratified because the stems differ enormously in size — Mult & Div has 33
    nodes and Subitization has 2 — and an unstratified draw can leave a whole
    stem out of one side.
    """
    rng = random.Random(SPLIT_SEED)
    by_stem = defaultdict(list)
    for i, s in enumerate(subjects):
        by_stem[s.stem_id].append(i)

    tuning = set()
    for stem_id in sorted(by_stem):
        idx = sorted(by_stem[stem_id])
        rng.shuffle(idx)
        n = round(len(idx) * TUNING_FRACTION)
        tuning.update(idx[:n])

    return [s._replace(split="tuning" if i in tuning else "held_out")
            for i, s in enumerate(subjects)]


# ----------------------------------------------------------------- metrics

def recall_at_k(ranked: list[str], truth: frozenset[str], k: int) -> float:
    if not truth:
        return 0.0
    return len(set(ranked[:k]) & truth) / len(truth)


def precision_at_k(ranked: list[str], truth: frozenset[str], k: int) -> float:
    top = ranked[:k]
    if not top:
        return 0.0
    return len(set(top) & truth) / len(top)


def reciprocal_rank(ranked: list[str], truth: frozenset[str]) -> float:
    for i, code in enumerate(ranked, start=1):
        if code in truth:
            return 1.0 / i
    return 0.0


def is_blind(ranked: list[str], truth: frozenset[str]) -> bool:
    """
    No correct standard anywhere in the list.

    This is the metric that matters most: a blind row is a place the tool
    silently offers an author nothing, which is worse than offering something
    wrong because there is no signal that anything is missing.
    """
    return not (set(ranked) & truth)


def node_placement(predicted: frozenset, actual: frozenset) -> tuple[bool, float]:
    """
    (hit, set agreement) for one standard's node assignment.

    hit         the predicted node set intersects the author's
    agreement    Jaccard between the two sets

    Both, always. Hit rate alone is gamed by assigning every standard to every
    node in the concept/skill; the Jaccard is what makes that strategy score
    badly. §5: report both or neither.
    """
    if not predicted and not actual:
        return False, 0.0
    union = predicted | actual
    return bool(predicted & actual), len(predicted & actual) / len(union)


# ------------------------------------------------------------ reach gating

EXCLUDED = "excluded"
UNAVAILABLE = "path unavailable"
UNREACHABLE = "unreachable"
REACHABLE = "reachable"


class Reach(NamedTuple):
    """What a path could have produced for one subject."""
    bucket: str
    vocab_reached: frozenset[str]     # truth codes anywhere in the path's space
    anchor_reached: frozenset[str]    # truth codes linked from THESE anchors


def reach_key(subject: Subject) -> tuple[str, str, str]:
    """
    Identity of a subject for reach lookup.

    NOT subject.key on its own: one node produces one subject PER STATE, so the
    node id alone collides and the states silently overwrite each other's reach.
    """
    return (subject.grain, subject.key, subject.state)


def reach_of(subject: Subject, path) -> Reach:
    """
    Bucket one subject against one path, without ranking anything.

    A subject usually carries several truth codes and they do not share a fate.
    The subject counts as `reachable` when AT LEAST ONE truth code is anchor-
    reachable; its unreachable siblings stay in the subject and count as misses
    in conditional recall. That keeps conditional recall an honest measure of
    the ranker rather than a way to quietly drop the hard codes, and coverage
    still reports per code so nothing is hidden.
    """
    if not subject.anchors:
        return Reach(EXCLUDED, frozenset(), frozenset())
    if not path.available(subject.state):
        return Reach(UNAVAILABLE, frozenset(), frozenset())

    vocab = subject.truth & path.vocabulary(subject.state)
    anchored = subject.truth & path.reachable_from(subject.anchors, subject.state)
    bucket = REACHABLE if anchored else UNREACHABLE
    return Reach(bucket, frozenset(vocab), frozenset(anchored))


def precondition_report(subjects: list[Subject], paths: list) -> None:
    """
    Vocabulary reach per state per path, before any scoring.

    Costs nothing — it is a SELECT DISTINCT. This is the table that would have
    caught Florida on day one.
    """
    states = sorted({s.state for s in subjects})
    truth_by_state = defaultdict(set)
    for s in subjects:
        truth_by_state[s.state] |= s.truth

    print(f"\n{'state':6s} {'truth':>6s}  " +
          "  ".join(f"{p.label:>18s}" for p in paths))
    print("-" * (14 + 20 * len(paths)))
    for state in states:
        truth = truth_by_state[state]
        cells = []
        for path in paths:
            if not path.available(state):
                cells.append(f"{'unavailable':>18s}")
                continue
            hit = len(truth & path.vocabulary(state))
            cells.append(f"{hit:>6d}/{len(truth):<4d}{100 * hit / len(truth):5.0f}%")
        print(f"{state:6s} {len(truth):6d}  " + "  ".join(cells))


# ------------------------------------------------------------------ report

def evaluate(subjects: list[Subject], rank: Callable[[Subject], list[str]],
             reaches: dict) -> dict:
    """
    Score one (grain, state, split) group, raw and conditional.

    Raw is over every subject with an anchor — the number the acceptance bar is
    written against. Conditional is over reachable subjects only — the number
    that says whether the ranker is any good.
    """
    scored = [s for s in subjects if reaches[reach_key(s)].bucket != EXCLUDED]
    if not scored:
        return {}
    if all(reaches[reach_key(s)].bucket == UNAVAILABLE for s in scored):
        return {"subjects": len(scored), "unavailable": True}

    raw, cond = defaultdict(float), defaultdict(float)
    n_reachable = 0
    for s in scored:
        ranked = rank(s)
        got = {
            "recall@5": recall_at_k(ranked, s.truth, 5),
            "recall@10": recall_at_k(ranked, s.truth, 10),
            "precision@5": precision_at_k(ranked, s.truth, 5),
            "mrr": reciprocal_rank(ranked, s.truth),
            "blind": 1.0 if is_blind(ranked, s.truth) else 0.0,
        }
        for k, v in got.items():
            raw[k] += v
        if reaches[reach_key(s)].bucket == REACHABLE:
            n_reachable += 1
            for k, v in got.items():
                cond[k] += v

    truth = set()
    vocab_hit, anchor_hit = set(), set()
    for s in scored:
        truth |= s.truth
        vocab_hit |= reaches[reach_key(s)].vocab_reached
        anchor_hit |= reaches[reach_key(s)].anchor_reached

    out = {"subjects": len(scored), "unavailable": False,
           "n_reachable": n_reachable,
           "coverage_vocab": len(vocab_hit) / len(truth) if truth else 0.0,
           "coverage_anchor": len(anchor_hit) / len(truth) if truth else 0.0,
           "buckets": Counter(reaches[reach_key(s)].bucket for s in scored)}
    for k, v in raw.items():
        out[f"raw_{k}"] = v / len(scored)
    # Conditional keys always exist, set to None when nothing was reachable —
    # 'no reachable subject to test the ranker on' is a distinct statement from
    # a score of zero, and must not collapse into one.
    for k in ("recall@5", "recall@10", "precision@5", "mrr", "blind"):
        out[f"cond_{k}"] = cond[k] / n_reachable if n_reachable else None
    return out


def print_block(title: str, rows: dict) -> None:
    """
    Coverage, conditional and raw side by side, never collapsed into one number.

    `path unavailable` prints as words. That is the whole point of rev 4's
    reach gating: a zero meaning 'no data exists' and a zero meaning 'the
    ranker failed' must never look the same.
    """
    print(f"\n{title}")
    print(f"  {'state':6s} {'split':9s} {'n':>4s} {'rch':>4s} "
          f"{'cov_v':>6s} {'cov_a':>6s} | "
          f"{'cond@5':>7s} {'cond@10':>8s} | "
          f"{'raw@5':>7s} {'raw@10':>7s} {'rawP@5':>7s} {'rawMRR':>7s} {'blind':>7s}")
    print("  " + "-" * 104)
    for (state, split), m in sorted(rows.items()):
        if not m:
            continue
        if m.get("unavailable"):
            print(f"  {state:6s} {split:9s} {m['subjects']:4d} "
                  f"{'—':>4s} {'—':>6s} {'—':>6s} | "
                  f"{'path unavailable — no vocabulary for this state':<50s}")
            continue
        c5 = m["cond_recall@5"]
        c10 = m["cond_recall@10"]
        cond = (f"{c5:7.3f} {c10:8.3f}" if c5 is not None
                else f"{'—':>7s} {'—':>8s}")
        print(f"  {state:6s} {split:9s} {m['subjects']:4d} {m['n_reachable']:4d} "
              f"{m['coverage_vocab']:6.1%} {m['coverage_anchor']:6.1%} | {cond} | "
              f"{m['raw_recall@5']:7.3f} {m['raw_recall@10']:7.3f} "
              f"{m['raw_precision@5']:7.3f} {m['raw_mrr']:7.3f} "
              f"{m['raw_blind']:7.3f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB))
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    paths = all_paths(cur)
    path_b = next(p for p in paths if p.name == "pathB")

    node_subjects, node_excluded = load_node_subjects(cur)
    cs_subjects, cs_excluded = load_concept_subjects(cur)
    node_subjects = assign_splits(node_subjects)
    cs_subjects = assign_splits(cs_subjects)

    print("=" * 72)
    print("EVAL HARNESS")
    print("=" * 72)
    print("\nExclusions (counted, never scored as misses):")
    for label, n in sorted(node_excluded.items()) + sorted(cs_excluded.items()):
        print(f"  {label:34s} {n}")

    print("\n" + "=" * 72)
    print("PRECONDITION — VOCABULARY REACH, before any scoring")
    print("=" * 72)
    print("Truth codes present anywhere in each path's output space for the\n"
          "state. 'unavailable' means the path has no vocabulary at all there,\n"
          "so no ranking could ever produce a correct answer.")
    for grain, subjects in (("\nNODE GRAIN", node_subjects),
                            ("\nCONCEPT/SKILL GRAIN", cs_subjects)):
        print(grain)
        precondition_report(subjects, paths)

    print("\n" + "=" * 72)
    print(f"SCORING — {path_b.label} only (the one path with a ranker)")
    print("=" * 72)
    print("cov_v/cov_a = vocabulary / anchor coverage. Their gap separates an\n"
          "incomplete crosswalk (fixable) from a missing path (needs Path C).\n"
          "cond = ranker quality on reachable subjects. raw = end to end.")

    for grain, subjects in (("NODE GRAIN  (primary: TX, FL)", node_subjects),
                            ("CONCEPT/SKILL GRAIN  (primary: CA)", cs_subjects)):
        reaches = {reach_key(s): reach_of(s, path_b) for s in subjects}
        groups = defaultdict(list)
        for s in subjects:
            groups[(s.state, s.split)].append(s)
        rows = {k: evaluate(v, path_b.rank, reaches) for k, v in groups.items()}
        print_block(grain, rows)

    print("\n" + "=" * 72)
    print("SCORING — the step 6 generator, node grain")
    print("=" * 72)
    print("Paths 0, A and B combined, ranked by strength tier then score.\n"
          "Reach is the UNION of all four paths: `unreachable` here means no\n"
          "path could have produced the truth code, so a miss is the ranker's.\n"
          "Thresholds and the grade decay are UNFIT until step 7 — read the\n"
          "shape of these numbers, not their third decimal.")

    generator = UnionPath(paths)
    rank = ranker(cur)
    reaches = {reach_key(s): reach_of(s, generator) for s in node_subjects}
    groups = defaultdict(list)
    for s in node_subjects:
        groups[(s.state, s.split)].append(s)
    rows = {k: evaluate(v, rank, reaches) for k, v in groups.items()}
    print_block("NODE GRAIN  (primary: TX, FL)", rows)

    # FL is the one state the three-bucket rule does not fully separate, and it
    # is worth saying out loud rather than leaving a reader to infer it. FL has
    # vocabulary — but only at grades 6-9, and every ladder node is PK-5 — so it
    # buckets as `unreachable` rather than `path unavailable` and its raw column
    # prints 0.000 right beside TX's honest `path unavailable`. The two mean the
    # same thing here. cov_v = 0.0% with rch = 0 is the tell.
    data_gap = sorted({state for (state, _split), m in rows.items()
                       if m and not m.get("unavailable")
                       and m["n_reachable"] == 0 and m["coverage_vocab"] == 0.0})
    if data_gap:
        print("\n  0.000 above is a DATA gap, not a ranker failure, for: "
              + " ".join(data_gap))
        print("  These states have vocabulary but none of it reaches their own\n"
              "  truth codes — a gap in the data, not a ranking to improve. It\n"
              "  is the same statement `path unavailable` makes; the states\n"
              "  differ only in whether the path has ANY vocabulary for them.")

    # Node placement needs a generator that assigns nodes (§4, step 9). The
    # metric is implemented and unit-tested; there is nothing to feed it yet,
    # and reporting a fabricated number here would be worse than reporting none.
    print("\nNODE PLACEMENT (TX, FL)")
    print("  not measurable yet — needs the step 9 node assignment.")
    print("  Truth sets are built and carried on every node subject:")
    multi = sum(1 for s in node_subjects
                for c in s.truth if len(s.truth_nodes.get(c, ())) > 1)
    print(f"  state tags whose truth is more than one node: {multi}")

    con.close()


if __name__ == "__main__":
    main()
