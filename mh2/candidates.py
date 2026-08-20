"""
candidates.py — step 6. Generate candidate state-standard tags at node grain.

Given the CCSS codes an author wrote on a node, produce the state standards
that plausibly correspond, one row per (node, state standard), with every
path's score carried separately and a derived `strength`.

    python -m mh2.candidates --db data/build/mh2.db
    python -m mh2.candidates --states TX,FL,CA        # narrow the report

Four paths, per §3. Three of them run here; the fourth is wired but has no data
until the offline run lands (step 8):

  Path 0  ca_code    CA inherits the CCSS tag when canon(code) matches AND the
                     standard text is identical. CA only, by construction.
  Path A  overlap    Jaccard over the lessons a standard is tagged to. The
                     intersecting lesson ids ARE the reviewer-facing evidence.
  Path B  crosswalk  learnosity edges. Boosts, never originates on its own
                     merit -- but it is an independent witness (§4).
  Path C  model      model_predictions (empty until step 8) and, separately,
                     model_suggestions.

**Everything TX and FL get is `weak`, by construction.** Both route exclusively
through Path C at PK-5 (§2): TX is absent from all three deterministic sources,
FL from both crosswalk files and from all_states below grade 6. So no second
independent path can ever corroborate a TX or FL candidate, and §4's ladder
puts every one of them at the bottom rung. That is now the majority case for
two of the three priority states, not an edge case, and the UI has to say so
prominently.

Before step 8 loaded Path C this file produced zero rows for both states, and
that was the correct output rather than a bug. If Path C is ever emptied again,
zero is what should come back — not a fallback and not a text-similarity
stopgap, either of which would manufacture rows for the two states the tool
most exists to serve, indistinguishable from real ones.

Independence (§4) is enforced, not assumed
------------------------------------------
`strength` derives from the set of DISTINCT INDEPENDENT paths supporting a
candidate, never from a count of supporting rows. Two crosswalk edges are one
path.

The subtle case is Path C, which has two sources that are not peers:

  model_predictions   the fine-tuned model. Independent of everything.
  model_suggestions   scored_alignments' recommended alternates. Built FROM
                      lesson overlap (Path A's own method) plus the Path C
                      model, so it can corroborate NEITHER (§4). It supports a
                      candidate and it scores one, but it never contributes to
                      the independent set -- an edge whose only support is a
                      model_suggestions row is `weak`, full stop.

Collapsing the two would let Path A agreeing with a laundered copy of itself
earn `strong`, which is the rev-3 defect §4 exists to fix.

The audit demotes and never promotes
------------------------------------
alignment_audit rules on all_states tags, i.e. PATH A's state side (measured:
84% overlap with all_states, 3.9% with learnosity, and that 3.9% is entirely
Massachusetts' prefix collision). It was produced by running lesson overlap, so
agreement with Path A is a method agreeing with itself. A rejecting verdict
drops the candidate one strength tier; a confirming verdict does nothing at all.

Grade is a prior, never a filter
--------------------------------
A same-grade filter discards 33% of FL and 31% of TX human tags (§4), so the
offset is a multiplicative decay over a +/-2 window and out-of-window pairs are
demoted rather than dropped. The constants are provisional and are fit on the
tuning split at step 7 -- they live in one block below so the fit has one place
to write.
"""

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

from eval.paths import canon  # noqa: E402
from mh2.normalize import grade_of  # noqa: E402

# --------------------------------------------------------------- provisional
# GRADE_DECAY/GRADE_WINDOW/GRADE_OUT_OF_WINDOW and PATH_C_TOP_K (below) are now
# step 7 FIT, not placeholders: eval/fit_thresholds.py grid-searched the grade
# prior and re-derived the Path C direction_k cut on the TUNING split
# (recall at precision>=0.60, TX+FL averaged) and both landed back on these
# exact defaults -- confirmed, not changed. Re-run that script if the
# underlying data changes enough to warrant refitting.
#
# What step 7 does NOT set: the precision score threshold itself. Per the
# rev 5 acceptance-bar ruling (spec §5), that numeric bar is John's to write,
# not a tuning pass -- eval/fit_thresholds.py reports the precision/recall
# curve it would be cut against, and stops there. He picked SURFACE_THRESHOLD
# below on 2026-08-17, off that curve: TX/FL held-out precision ~0.52-0.56 at
# this cut, more stable across tuning/held-out than either neighboring step in
# the grid. See eval/fit_thresholds.py section 3 for the full curve.

GRADE_DECAY = 0.85          # per grade of offset, inside the window
GRADE_WINDOW = 2            # §4: the window is +/-2
GRADE_OUT_OF_WINDOW = 0.35  # beyond it -- demoted, never dropped

# Deterministic-path (strong/moderate) candidates are always auto-surfaced --
# a second independent path or a Path 0 exact match already clears a much
# higher bar than any Path C score could claim. This threshold governs ONLY
# the weak, Path-C-only candidates that are the entirety of what TX and FL get
# at PK-5 (spec §5: "a precision-first policy for those two states is entirely
# a Path C threshold question"). Below it, a candidate can still exist in
# `candidates` and in the reranked browse list (§ below) -- it is just not
# asserted to the writer outright.
SURFACE_THRESHOLD = 0.70

# Path B edges do not all join two peers. Learnosity pairs a CCSS standard with
# a state Cluster 57 times and a Heading twice; those are edges between levels
# of the tree and must be down-weighted, not treated as standard-to-standard.
COARSE_GRANULARITY = {"Cluster", "Domain", "Heading"}
COARSE_WEIGHT = 0.5

# A CA code whose canon matches a CCSS code but whose TEXT differs is not the
# §3 Path 0 hit -- that hit requires code AND text (see Path0.load). It is
# emitted anyway, at a reduced score and never as `strong`, because dropping it
# silently costs real coverage: of CA's 40 hand-tagged truth codes, the text
# guard rejects one that canon accepts. Emitting it at `moderate` keeps it
# visible without letting it claim a certainty it does not have.
CA_CANON_ONLY_SCORE = 0.6

# model_suggestions carries no score of its own -- it is a recommendation, not
# a ranking. A constant keeps it below anything the model actually scored.
SUGGESTION_SCORE = 0.25

# §3 Path C's stated contract is "Top-10 per state standard". The offline run
# returned k=25, 50 and 100 depending on the file, and model_predictions stores
# every rank of it -- the table is the record of what the model said, and
# throwing ranks away at load would make the §5 question ("is top-5 enough, or
# is a recompute needed?") unanswerable. The cut belongs here, where it is one
# constant step 7 can fit.
#
# It is not cosmetic, and it costs different amounts in different directions:
# at k=10 the state_to_ccss run keeps TX at 98% truth reach and FL at 100%,
# while ccss_to_state falls to 45% and 58%. Both are 99-100% at full k. Raising
# this recovers ccss_to_state's tail at the price of a much longer shortlist,
# and §5 settles that against the blind-row rate rather than by preference.
#
# Step 7 re-derived the both-directions-at-10 cut on the TUNING split (the
# HANDOFF ablation favoring state_to_ccss@50-only ran on held-out, which does
# not count as a fit) and found direction_k does not move recall at a fixed
# precision floor at all once a score threshold is applied -- the extra
# candidates a wider k admits sit too low in score to ever clear a precision
# cut, so they cost list length without buying recall back. 10/10 confirmed;
# see eval/fit_thresholds.py.
PATH_C_TOP_K = 10

# ----------------------------------------------------------------- strengths

STRONG, MODERATE, WEAK = "strong", "moderate", "weak"
_TIERS = (STRONG, MODERATE, WEAK)

# §4's independence matrix. model_suggestions is deliberately absent: it is not
# a path and cannot appear there.
DETERMINISTIC = ("path0", "pathA", "pathB")
INDEPENDENT = DETERMINISTIC + ("pathC",)


def demote(strength: str) -> str:
    """One tier down. `weak` is the floor -- there is nothing below it."""
    i = _TIERS.index(strength)
    return _TIERS[min(i + 1, len(_TIERS) - 1)]


def strength_of(supports: dict, path0_exact: bool) -> str:
    """
    §4, literally: strong on a Path 0 exact code+text match or on >=2
    independent paths; moderate on one deterministic path; weak otherwise.

    `supports` is keyed by path name. Only the keys in INDEPENDENT count --
    a model_suggestions-only support ('suggestion') is Path C evidence and
    corroborates nothing, so it falls straight through to weak.
    """
    if path0_exact:
        return STRONG
    independent = [p for p in INDEPENDENT if p in supports]
    if len(independent) >= 2:
        return STRONG
    if len(independent) == 1 and independent[0] in DETERMINISTIC:
        return MODERATE
    return WEAK


# --------------------------------------------------------------- grade prior

_GRADE_ORDER = {"PK": -1, "K": 0}


def grade_index(code: str) -> int | None:
    """
    PK < K < 1..12. None when the code carries no grade at all -- Pennsylvania
    puts the grade inside the domain and the VA preschool codes have none, so
    the prior simply does not apply to them rather than guessing a value.
    """
    g = grade_of(code)
    if g is None:
        return None
    if g in _GRADE_ORDER:
        return _GRADE_ORDER[g]
    return int(g) if g.isdigit() else None


def grade_factor(anchor: str, code: str) -> tuple[float, int | None]:
    """(multiplier, offset). Offset is None when either side has no grade."""
    a, b = grade_index(anchor), grade_index(code)
    if a is None or b is None:
        return 1.0, None
    offset = b - a
    if abs(offset) > GRADE_WINDOW:
        return GRADE_OUT_OF_WINDOW, offset
    return GRADE_DECAY ** abs(offset), offset


def apply_decay(score: float, factor: float) -> float:
    """
    The grade prior, applied so that it always makes a score WORSE.

    Multiplying is only correct for a non-negative score. The model's bi_score
    is a cosine and it goes negative -- ccss_to_ca_k50 reaches -0.035 -- and
    -0.035 * 0.35 = -0.012, which RAISES a four-grade-off pair above an
    on-grade one. Dividing on the negative side keeps the prior monotone in the
    direction it is meant to push.
    """
    if score >= 0:
        return score * factor
    return score / factor if factor else score


# ------------------------------------------------------------------- helpers

def state_of(code: str) -> str:
    return code.split(".")[0].upper()


_TEXT_RE = re.compile(r"[^a-z0-9]+")


def normalize_text(text: str | None) -> str:
    """Case, punctuation and whitespace folded. Path 0's equality guard."""
    return _TEXT_RE.sub(" ", (text or "").lower()).strip()


# --------------------------------------------------------------------- paths
#
# Each path exposes edges(anchor) -> {state_code: (score, evidence)}. That is
# the whole interface. Scores are NEVER blended across paths (§4); they are
# carried side by side into separate columns and the reviewer sees all of them.


class Path0:
    """
    CA code inheritance. `canon` strips the cluster letter CCSS carries and CA
    omits, then normalized text equality guards against canon collisions --
    without it, 1.MD.A.3 and 1.MD.B.3 both canon to 1.MD.3 and CA would inherit
    the wrong standard's tags.
    """

    name = "path0"

    def __init__(self, cur):
        ccss = {c: t for c, t in cur.execute(
            "SELECT standard_id, text FROM standards WHERE jurisdiction='CCSS'")}
        by_canon = defaultdict(list)
        for code in ccss:
            by_canon[canon(code)].append(code)

        # The 17 'California-not CCSS' codes are excluded by §3 Path 0: the
        # sheet is the team's own statement that those standards are NOT
        # verbatim CCSS, which is a stronger claim than a text comparison.
        not_ccss = {r[0] for r in cur.execute(
            "SELECT standard_code FROM standard_tag_status"
            " WHERE sheet='California-not CCSS'")}

        self._exact = defaultdict(set)       # ccss code -> CA codes, text-equal
        self._canon_only = defaultdict(set)  # ccss code -> CA codes, text differs
        for ca_code, ca_text in cur.execute(
                "SELECT standard_id, text FROM standards WHERE jurisdiction='CA'"):
            if ca_code in not_ccss:
                continue
            body = normalize_text(ca_text)
            for ccss_code in by_canon.get(canon(ca_code), ()):
                if body and body == normalize_text(ccss[ccss_code]):
                    self._exact[ccss_code].add(ca_code)
                else:
                    self._canon_only[ccss_code].add(ca_code)

    def edges(self, anchor: str) -> dict:
        out = {}
        for code in self._canon_only.get(anchor, ()):
            out[code] = (CA_CANON_ONLY_SCORE, {"match": "canon_only"})
        # Exact wins where both fire: same code, stronger claim.
        for code in self._exact.get(anchor, ()):
            out[code] = (1.0, {"match": "code_and_text"})
        return out


class PathA:
    """
    Lesson overlap. Jaccard over the lesson sets, emitted whenever the
    intersection is at least one lesson (§3) -- the threshold is fit at step 7,
    not guessed here.
    """

    name = "pathA"

    def __init__(self, cur):
        self._lessons = defaultdict(set)
        state_codes = set()
        for code, lesson in cur.execute(
                "SELECT standard_code, lesson_id FROM standard_lessons"):
            self._lessons[code].add(lesson)
            head = state_of(code)
            if len(head) == 2 and head.isalpha():
                state_codes.add(code)

        self._by_lesson = defaultdict(set)
        for code in state_codes:
            for lesson in self._lessons[code]:
                self._by_lesson[lesson].add(code)

    def edges(self, anchor: str) -> dict:
        anchor_lessons = self._lessons.get(anchor, set())
        if not anchor_lessons:
            return {}
        out = {}
        seen = set()
        for lesson in anchor_lessons:
            seen |= self._by_lesson.get(lesson, set())
        for code in seen:
            shared = anchor_lessons & self._lessons[code]
            if not shared:
                continue
            union = anchor_lessons | self._lessons[code]
            out[code] = (len(shared) / len(union), {
                "shared_lessons": sorted(shared)[:20],
                "n_shared": len(shared),
                "n_anchor_lessons": len(anchor_lessons),
                "n_code_lessons": len(self._lessons[code]),
            })
        return out


class PathB:
    """learnosity crosswalk edges, down-weighted where the two sides are not peers."""

    name = "pathB"

    def __init__(self, cur):
        self._edges = defaultdict(dict)
        for ccss, state_code, source_code, granularity in cur.execute(
                "SELECT ccss_code, state_code, source_code, granularity"
                " FROM crosswalk"):
            weight = (COARSE_WEIGHT if granularity in COARSE_GRANULARITY
                      else 1.0)
            self._edges[ccss][state_code] = (weight, {
                "source_code": source_code, "granularity": granularity})

    def edges(self, anchor: str) -> dict:
        return dict(self._edges.get(anchor, {}))


class PathC:
    """
    Model evidence, from two sources that are NOT peers.

    `edges` returns model_predictions only -- the independent witness. The
    suggestions come back from `suggestions()` separately and are kept separate
    all the way through, because §4 forbids them from corroborating Path A or
    Path C. Merging them here would launder that away in one line.

    Only the top PATH_C_TOP_K ranks are read. Both directions are read, and
    where the same pair arrives from each the better RANK wins -- not the
    better score, which would be meaningless: the two runs' scores do not share
    a range (state_to_ccss's median top-1 is 0.604, ccss_to_state's 0.16-0.19).
    """

    name = "pathC"

    def __init__(self, cur, top_k: int = PATH_C_TOP_K,
                 direction_k: dict | None = None):
        # A single cut applied to both directions is a cut applied to two very
        # different rank profiles. At k=10 state_to_ccss reaches 98% of TX's
        # truth codes and ccss_to_state reaches 45% -- same model, same pairs,
        # opposite arrows: a state standard's own top-10 CCSS list is short and
        # focused, while a CCSS standard's top-10 STATE list has to pick ten
        # out of that state's whole corpus, so correct answers fall to rank
        # 11+. `direction_k` lets the two be cut independently. It defaults to
        # top_k for both, which is the historical behaviour.
        self._direction_k = dict(direction_k or {})
        self._edges = defaultdict(dict)
        # Both directions are read. They are separate rows with separate scores
        # (that is why `direction` is in the primary key), and the same pair can
        # arrive from each -- the better-ranked one wins, and evidence_json
        # records which run it came from. Scores are NOT comparable across
        # directions, so this picks by rank, not by score.
        for ccss, state_code, score, rank, direction, tier, version in cur.execute(
                "SELECT ccss_code, state_code, score, rank, direction, tier,"
                " model_version FROM model_predictions WHERE rank <= ?",
                (max([top_k] + list(self._direction_k.values())),)):
            if rank > self._direction_k.get(direction, top_k):
                continue
            prev = self._edges[ccss].get(state_code)
            if prev is None or rank < prev[1]["rank"]:
                self._edges[ccss][state_code] = (
                    score, {"rank": rank, "direction": direction, "tier": tier,
                            "model_version": version})

        # scored_alignments' recommended alternates, inverted: the row says
        # "this state code should have been tagged to that CCSS code", so read
        # from the CCSS side it names a state code.
        self._suggestions = defaultdict(dict)
        for ccss, state_code, rationale in cur.execute(
                "SELECT ccss_code, state_code, rationale FROM model_suggestions"):
            self._suggestions[ccss][state_code] = (
                SUGGESTION_SCORE, {"rationale": rationale})

    def edges(self, anchor: str) -> dict:
        return dict(self._edges.get(anchor, {}))

    def suggestions(self, anchor: str) -> dict:
        return dict(self._suggestions.get(anchor, {}))


class Reranks:
    """
    model_reranks lookup -- the writer-facing "browse" list.

    Loaded from mh2.load_reranks, which itself reads the durable, paid-for
    cache at config.RERANK_CACHE and makes no API calls. Empty for any
    (node, state) nobody has run scripts/run_reranks.py against yet, which is
    a normal, silent state, not an error: browse_rank is simply NULL there.
    """

    def __init__(self, cur):
        self._rank = {}
        for node_id, state, code, rank in cur.execute(
                "SELECT node_id, state, standard_code, rerank_rank"
                " FROM model_reranks"):
            self._rank[(node_id, state, code)] = rank

    def rank_of(self, node_id: str, state: str, code: str) -> int | None:
        return self._rank.get((node_id, state, code))


# ------------------------------------------------------------------ the join

class Audit:
    """
    alignment_audit, keyed on the tag it ruled on: (state code, CCSS code).

    Demote-only. `confirmed` and `likely_correct` are recorded in the evidence
    and change nothing -- the audit ran lesson overlap to reach them, so on a
    Path A candidate it is agreeing with itself.
    """

    REJECTING = {"likely_incorrect"}

    def __init__(self, cur):
        self._verdicts = {}
        for state_code, ccss, verdict, rationale in cur.execute(
                "SELECT state_code, ccss_code, verdict, rationale"
                " FROM alignment_audit"):
            self._verdicts[(state_code, ccss)] = (verdict, rationale)

    def verdict(self, state_code: str, anchor: str):
        return self._verdicts.get((state_code, anchor))

    def rejects(self, verdict: str | None) -> bool:
        return (verdict or "").strip().lower() in self.REJECTING


def load_anchors(cur) -> tuple[dict, dict, dict]:
    """
    node -> its CCSS anchor codes, plus the concept/skill each node sits in.

    Nodes with no CCSS anchor are returned separately, not dropped: they are
    the 28 §3.5 nodes, and the browser must render them as "no CCSS anchor"
    rather than as an empty result indistinguishable from "found nothing".
    An absence assertion is NEVER consulted here -- it scopes to CCSS and must
    not suppress state candidates (§1, §3.5).
    """
    meta = {}
    for node_id, stem_id, concept_skill in cur.execute(
            "SELECT node_id, stem_id, concept_skill FROM nodes"):
        meta[node_id] = (stem_id, f"{stem_id}|{concept_skill or ''}")

    anchors = defaultdict(set)
    for node_id, code in cur.execute(
            "SELECT node_id, standard_code FROM node_standards_parsed"
            " WHERE state IS NULL"):
        if node_id in meta:
            anchors[node_id].add(code)

    unanchored = {n: meta[n] for n in meta if not anchors.get(n)}
    return {n: tuple(sorted(c)) for n, c in anchors.items()}, meta, unanchored


def anchor_nodes_by_cs(anchors: dict, meta: dict) -> dict:
    """
    (concept_skill_id, anchor code) -> the nodes in that concept/skill carrying it.

    Node placement here is ANCHOR-derived, not text-derived: the candidate sits
    on the node whose cell carried the CCSS code that produced it, which is a
    stronger claim than similarity between the standard text and the node text.
    Where an author put one anchor on several sibling nodes, every one of them
    gets a row -- that is §9 Q1's multi-node tagging, made by the author rather
    than by a threshold. Step 9 adds the text-scored assignment on top.
    """
    out = defaultdict(set)
    for node_id, codes in anchors.items():
        _stem, cs = meta[node_id]
        for code in codes:
            out[(cs, code)].add(node_id)
    return out


class Candidate:
    """One (node, state standard) row, accumulating support across anchors."""

    __slots__ = ("node_id", "cs", "state", "code", "supports", "best_anchor",
                 "best_score", "grade_offset", "anchors")

    def __init__(self, node_id, cs, state, code):
        self.node_id, self.cs, self.state, self.code = node_id, cs, state, code
        self.supports: dict[str, dict] = {}
        # best_score starts at None, NOT 0.0. The model's bi_score is a cosine
        # and it goes negative -- ccss_to_ca_k50 bottoms out at -0.035. Seeded
        # at zero, every negative-scoring candidate keeps best_anchor = None
        # and the row is built with no anchor at all.
        self.best_anchor, self.best_score, self.grade_offset = None, None, None
        self.anchors: set[str] = set()

    def add(self, path_name, anchor, score, evidence, factor, offset):
        """
        Keep the best-scoring support per path, and the best anchor overall.

        Best per PATH, not best overall per path-and-anchor pair: two anchors
        both reaching a candidate through Path A is still one path (§4), and
        the column carries that path's strongest claim.
        """
        adjusted = apply_decay(score, factor)
        self.anchors.add(anchor)
        prev = self.supports.get(path_name)
        if prev is None or adjusted > prev["score"]:
            self.supports[path_name] = {
                "score": adjusted, "raw_score": score, "anchor": anchor,
                "grade_offset": offset, "evidence": evidence}
        if self.best_score is None or adjusted > self.best_score:
            self.best_score, self.best_anchor = adjusted, anchor
            self.grade_offset = offset


def generate(cur, states: set[str] | None = None,
             top_k: int = PATH_C_TOP_K,
             direction_k: dict | None = None) -> tuple[list, dict]:
    """
    Build every candidate at node grain. Returns (candidates, stats).

    `states` narrows the output for a focused run. It defaults to every state
    any path can name -- §4 forbids hard-filtering, and a state missing from
    the output because nobody listed it looks exactly like a state with no
    coverage.
    """
    anchors, meta, unanchored = load_anchors(cur)
    nodes_for_anchor = anchor_nodes_by_cs(anchors, meta)

    path0, path_a = Path0(cur), PathA(cur)
    path_b, path_c = PathB(cur), PathC(cur, top_k, direction_k)
    audit = Audit(cur)
    reranks = Reranks(cur)

    stats = defaultdict(int)
    stats["nodes_anchored"] = len(anchors)
    stats["nodes_unanchored"] = len(unanchored)

    rows: dict[tuple, Candidate] = {}
    for node_id, node_anchors in sorted(anchors.items()):
        _stem, cs = meta[node_id]
        for anchor in node_anchors:
            contributions = [
                ("path0", path0.edges(anchor)),
                ("pathA", path_a.edges(anchor)),
                ("pathB", path_b.edges(anchor)),
                ("pathC", path_c.edges(anchor)),
                # Not a path. Scored, never independent (§4).
                ("suggestion", path_c.suggestions(anchor)),
            ]
            for path_name, edges in contributions:
                for code, (score, evidence) in edges.items():
                    state = state_of(code)
                    if states is not None and state not in states:
                        continue
                    factor, offset = grade_factor(anchor, code)
                    key = (node_id, code)
                    cand = rows.get(key)
                    if cand is None:
                        cand = rows[key] = Candidate(node_id, cs, state, code)
                    cand.add(path_name, anchor, score, evidence, factor, offset)

    out = []
    for cand in rows.values():
        path0_support = cand.supports.get("path0")
        path0_exact = bool(
            path0_support
            and path0_support["evidence"].get("match") == "code_and_text")
        strength = strength_of(cand.supports, path0_exact)

        # The audit rules on Path A's state side. It only speaks to a candidate
        # Path A actually supports; on anything else it has no subject.
        verdict = rationale = None
        if "pathA" in cand.supports:
            found = audit.verdict(cand.code, cand.supports["pathA"]["anchor"])
            if found:
                verdict, rationale = found
                if audit.rejects(verdict):
                    strength = demote(strength)
                    stats["demoted_by_audit"] += 1

        siblings = set()
        for anchor in cand.anchors:
            siblings |= nodes_for_anchor.get((cand.cs, anchor), set())
        node_confidence = 1.0 if len(siblings) <= 1 else 1.0 / len(siblings)

        evidence = {
            "anchors": sorted(cand.anchors),
            "best_anchor": cand.best_anchor,
            "paths": {name: dict(s) for name, s in sorted(cand.supports.items())},
            "independent_paths": sorted(
                p for p in INDEPENDENT if p in cand.supports),
            "audit": ({"verdict": verdict, "rationale": rationale,
                       "effect": "demoted" if audit.rejects(verdict) else "none"}
                      if verdict else None),
            "node_siblings_sharing_anchor": sorted(siblings),
            "grade": {"anchor": grade_of(cand.best_anchor),
                      "candidate": grade_of(cand.code),
                      "offset": cand.grade_offset},
            "unfit": ["granularity_weight", "multi_node_threshold"],
        }

        # Step 7 precision cut (§ above). Deterministic support already clears
        # a higher bar than any Path C score claims to; only a weak,
        # Path-C-only row is gated on SURFACE_THRESHOLD, and a row with no
        # score at all (should not happen for WEAK, but never crash on it)
        # fails closed rather than auto-surfacing on a missing number.
        auto_surface = (strength != WEAK or
                        (cand.best_score is not None
                         and cand.best_score >= SURFACE_THRESHOLD))
        browse_rank = reranks.rank_of(cand.node_id, cand.state, cand.code)

        out.append({
            "node_id": cand.node_id,
            "concept_skill_id": cand.cs,
            "state": cand.state,
            "standard_code": cand.code,
            "anchor_ccss_code": cand.best_anchor,
            "anchor_state_code": None,       # §3.5.1; needs the state->state run
            "score_ca_code": _score(cand, "path0"),
            "score_overlap": _score(cand, "pathA"),
            "score_crosswalk": _score(cand, "pathB"),
            # One column for both model sources. Which one it came from is in
            # evidence_json and, decisively, in independent_paths.
            "score_model": _best(_score(cand, "pathC"),
                                 _score(cand, "suggestion")),
            "grade_offset": cand.grade_offset,
            # NOT a blend (§4). A ranking key WITHIN a strength tier: the
            # strongest single claim any path makes, after the grade prior.
            "combined_score": cand.best_score,
            "strength": strength,
            "node_confidence": node_confidence,
            "auto_surface": auto_surface,
            "browse_rank": browse_rank,
            "evidence_json": json.dumps(evidence, sort_keys=True),
        })
        stats[f"strength_{strength}"] += 1
        stats["rows"] += 1
        stats["auto_surfaced" if auto_surface else "browse_only"] += 1

    return out, dict(stats)


def _score(cand: Candidate, path_name: str):
    s = cand.supports.get(path_name)
    return s["score"] if s else None


def _best(*values):
    present = [v for v in values if v is not None]
    return max(present) if present else None


# -------------------------------------------------------------- the harness

# Ranked worst-last within a strength tier, because the tiers are not
# commensurable (§4: no blended score). A `moderate` backed by a deterministic
# path outranks a `weak` however high the model scored it, and sorting on
# combined_score alone would silently reintroduce the blend.
_TIER_RANK = {STRONG: 0, MODERATE: 1, WEAK: 2}


def ranker(cur, states: set[str] | None = None,
           top_k: int = PATH_C_TOP_K, direction_k: dict | None = None):
    """
    A `rank(subject) -> [state_code]` callable for eval/harness.py, at NODE
    grain only.

    No leakage: the generator reads a node's CCSS anchors, which is precisely
    what the harness hands it, and never reads a state tag from
    node_standards_parsed. The held-out truth is invisible to it.

    Concept/skill grain is deliberately not served here. §5 unions node
    candidates up to their concept/skill, and the node -> workbook-concept
    mapping is reconcile.py's business (88 nodes still have no concept). Faking
    that join to produce a second number would produce a wrong one.
    """
    rows, _stats = generate(cur, states, top_k, direction_k)
    by_subject = defaultdict(list)
    for r in rows:
        by_subject[(r["node_id"], r["state"])].append(r)
    for group in by_subject.values():
        group.sort(key=lambda r: (_TIER_RANK[r["strength"]],
                                  -r["combined_score"], r["standard_code"]))

    def rank(subject) -> list[str]:
        return [r["standard_code"]
                for r in by_subject.get((subject.key, subject.state), ())]

    return rank


# ------------------------------------------------------------------- writing

INSERT = """
INSERT INTO candidates (
    run_id, node_id, concept_skill_id, state, standard_code, anchor_ccss_code,
    anchor_state_code, score_ca_code, score_overlap, score_crosswalk,
    score_model, grade_offset, combined_score, strength, node_confidence,
    auto_surface, browse_rank, evidence_json, generated_at)
VALUES (:run_id, :node_id, :concept_skill_id, :state, :standard_code,
        :anchor_ccss_code, :anchor_state_code, :score_ca_code, :score_overlap,
        :score_crosswalk, :score_model, :grade_offset, :combined_score,
        :strength, :node_confidence, :auto_surface, :browse_rank,
        :evidence_json, :generated_at)
"""


def write(con, rows: list, run_id: int) -> None:
    """
    Replace this run's rows. `candidates` is layer 1 -- regenerable output,
    never authoritative, and nothing here touches candidate_reviews, which is
    layer 3 and append-only.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.execute("DELETE FROM candidates")
    con.executemany(INSERT, [r | {"run_id": run_id, "generated_at": now}
                             for r in rows])
    con.commit()


def next_run_id(con) -> int:
    return (con.execute(
        "SELECT COALESCE(MAX(run_id), 0) + 1 FROM candidates").fetchone()[0])


# -------------------------------------------------------------------- report

def report(con, rows: list, stats: dict) -> None:
    """
    Print, do not just load. A count that lives only in the database settles
    nothing, and two of these numbers are load-bearing: the zero for TX and FL,
    and the share of rows that are `weak`.
    """
    print(f"\nnodes with a CCSS anchor      {stats.get('nodes_anchored', 0):6,d}")
    print(f"nodes with none (§3.5)        {stats.get('nodes_unanchored', 0):6,d}"
          "   state-anchored, pending the state->state run")
    print(f"candidate rows                {stats.get('rows', 0):6,d}")
    for tier in _TIERS:
        print(f"  {tier:26s}{stats.get(f'strength_{tier}', 0):6,d}")
    if stats.get("demoted_by_audit"):
        print(f"  demoted by a rejecting audit{stats['demoted_by_audit']:6,d}")
    print(f"auto-surfaced (>= threshold)  {stats.get('auto_surfaced', 0):6,d}")
    print(f"browse only (< threshold)     {stats.get('browse_only', 0):6,d}"
          f"   SURFACE_THRESHOLD = {SURFACE_THRESHOLD}")

    by_state = defaultdict(lambda: defaultdict(int))
    for r in rows:
        by_state[r["state"]]["n"] += 1
        by_state[r["state"]][r["strength"]] += 1
        by_state[r["state"]]["surfaced" if r["auto_surface"] else "browse"] += 1
        if r["browse_rank"] is not None:
            by_state[r["state"]]["reranked"] += 1
        for col, path in (("score_ca_code", "0"), ("score_overlap", "A"),
                          ("score_crosswalk", "B"), ("score_model", "C")):
            if r[col] is not None:
                by_state[r["state"]][path] += 1

    print(f"\n{'state':6s} {'rows':>7s} {'strong':>7s} {'moder.':>7s} {'weak':>7s}"
          f" | {'surfaced':>8s} {'browse':>7s} {'rerank’d':>8s}"
          f" | {'path0':>7s} {'pathA':>7s} {'pathB':>7s} {'pathC':>7s}")
    print("-" * 100)
    for state in sorted(by_state, key=lambda s: -by_state[s]["n"]):
        c = by_state[state]
        print(f"{state:6s} {c['n']:7,d} {c[STRONG]:7,d} {c[MODERATE]:7,d}"
              f" {c[WEAK]:7,d} | {c['surfaced']:8,d} {c['browse']:7,d}"
              f" {c['reranked']:8,d} | {c['0']:7,d} {c['A']:7,d} {c['B']:7,d}"
              f" {c['C']:7,d}")

    # §2: TX and FL route exclusively through Path C at PK-5, and Path C is
    # empty until step 8. Anything other than zero here needs explaining, so
    # the explanation is printed rather than left for someone to reconstruct.
    print()
    for state in ("TX", "FL"):
        rows_for = [r for r in rows if r["state"] == state]
        if not rows_for:
            print(f"{state}: 0 candidates. Correct, not a bug — {state} routes"
                  " exclusively through Path C at PK–5 (§2)\n    and Path C has"
                  " no data until the offline run (step 8). Do not add a"
                  " fallback or a\n    text-similarity stopgap.")
            continue
        # FL is in all_states at grades 6-9 only, so Path A can reach a handful
        # of middle-school FL codes from the grade 5-6 anchors. They are real
        # Path A output and grade is a prior, not a filter (§4) -- so they are
        # emitted, demoted by the grade decay, and called out here.
        out_of_band = sorted({r["standard_code"] for r in rows_for})
        print(f"{state}: {len(rows_for)} candidates, all Path A reaching the "
              f"grades 6–9 rows that are\n    the only {state} data in "
              "all_states. Not PK–5 coverage. Demoted by the grade\n    decay, "
              "not filtered out (§4). Codes: " + ", ".join(out_of_band))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB))
    ap.add_argument("--states", default=None,
                    help="comma-separated; default is every state a path names")
    ap.add_argument("--dry-run", action="store_true",
                    help="report without writing")
    args = ap.parse_args()

    states = ({s.strip().upper() for s in args.states.split(",") if s.strip()}
              if args.states else None)

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    rows, stats = generate(cur, states)

    if args.dry_run:
        print("dry run — nothing written")
    else:
        write(con, rows, next_run_id(con))

    report(con, rows, stats)
    con.close()


if __name__ == "__main__":
    main()
