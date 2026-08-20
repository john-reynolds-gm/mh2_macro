"""
Reach gating tests (§5, rev 4).

The point of reach gating is that a zero meaning "no data exists" and a zero
meaning "the ranker failed" must never print identically. These tests pin the
bucket boundaries, because getting them wrong reintroduces exactly the
uninterpretable output rev 4 exists to fix.

Run with: python -m pytest tests/ -q  (or: python tests/test_reach.py)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.harness import (
    EXCLUDED, REACHABLE, UNAVAILABLE, UNREACHABLE, Subject, evaluate, reach_key,
    reach_of,
)
from eval.paths import canon


class FakePath:
    """A path with a hand-specified vocabulary and edge set."""

    def __init__(self, vocab_by_state, edges=None):
        self._vocab = {k: set(v) for k, v in vocab_by_state.items()}
        self._edges = {k: set(v) for k, v in (edges or {}).items()}

    def vocabulary(self, state):
        return set(self._vocab.get(state, ()))

    def available(self, state):
        return bool(self.vocabulary(state))

    def reachable_from(self, anchors, state):
        out = set()
        for a in anchors:
            out |= self._edges.get((a, state), set())
        return out

    def rank(self, subject):
        return sorted(self.reachable_from(subject.anchors, subject.state))


def subject(key="n1", state="TX", anchors=("4.G.A.1",), truth=("TX.4.6A",),
            split="held_out"):
    return Subject(key=key, grain="node", stem_id="S", state=state,
                   anchors=tuple(anchors), truth=frozenset(truth),
                   truth_nodes={}, split=split)


# ------------------------------------------------------------------- canon

def test_canon_strips_ca_and_cluster_letter():
    assert canon("CA.1.MD.3") == "1.MD.3"
    assert canon("1.MD.B.3") == "1.MD.3"
    assert canon("CA.K.CC.4.a") == "K.CC.4.a"


def test_canon_leaves_a_digit_third_segment_alone():
    """Only a SINGLE LETTER third segment is the cluster; a digit is not."""
    assert canon("1.MD.3") == "1.MD.3"
    assert canon("CA.2.NBT.7.1") == "2.NBT.7.1"


# ------------------------------------------------------------------ buckets

def test_no_anchor_is_excluded_not_a_miss():
    """A node with no CCSS code is not a test the generator can fail."""
    path = FakePath({"TX": ["TX.4.6A"]})
    assert reach_of(subject(anchors=()), path).bucket == EXCLUDED


def test_no_vocabulary_is_path_unavailable():
    """
    The rev 3 defect. TX has zero crosswalk rows, so Path B could not name a
    correct answer under any ranking. That must not print as 0.000.
    """
    path = FakePath({"SC": ["SC.3.NR.1.3"]})
    assert reach_of(subject(state="TX"), path).bucket == UNAVAILABLE


def test_vocabulary_but_no_edge_is_unreachable():
    """
    Maryland's case: 437 crosswalk codes exist, none of them the ones the
    authors used. Different diagnosis from TX — the crosswalk is incomplete and
    fixable, rather than the state needing another path entirely.
    """
    path = FakePath({"MD": ["MD.9.OTHER.1"]})
    r = reach_of(subject(state="MD", truth=("MD.1.GR.C.5",)), path)
    assert r.bucket == UNREACHABLE
    assert r.vocab_reached == frozenset()


def test_edge_from_this_anchor_is_reachable():
    path = FakePath({"TX": ["TX.4.6A"]}, {("4.G.A.1", "TX"): ["TX.4.6A"]})
    r = reach_of(subject(), path)
    assert r.bucket == REACHABLE
    assert r.anchor_reached == frozenset({"TX.4.6A"})


def test_any_truth_code_reachable_makes_the_subject_reachable():
    """
    John's ruling: ANY, not ALL. The unreachable siblings stay in the subject
    and count as misses in conditional recall, so conditional recall cannot be
    inflated by quietly dropping the hard codes.
    """
    path = FakePath({"TX": ["TX.4.6A", "TX.9.9Z"]},
                    {("4.G.A.1", "TX"): ["TX.4.6A"]})
    r = reach_of(subject(truth=("TX.4.6A", "TX.9.9Z", "TX.1.1A")), path)
    assert r.bucket == REACHABLE
    assert r.anchor_reached == frozenset({"TX.4.6A"})
    # TX.9.9Z is in the vocabulary but not linked from this anchor: that gap is
    # what distinguishes "crosswalk incomplete" from "path missing".
    assert r.vocab_reached == frozenset({"TX.4.6A", "TX.9.9Z"})


def test_vocabulary_and_anchor_reach_are_reported_separately():
    path = FakePath({"SC": ["SC.A", "SC.B"]}, {("1.G.A.1", "SC"): ["SC.A"]})
    r = reach_of(subject(state="SC", anchors=("1.G.A.1",),
                         truth=("SC.A", "SC.B")), path)
    assert r.vocab_reached == frozenset({"SC.A", "SC.B"})
    assert r.anchor_reached == frozenset({"SC.A"})


# ------------------------------------------------------------- reach_key

def test_reach_key_separates_states_on_one_node():
    """
    One node produces one subject PER STATE. Keying reach on the node id alone
    lets states overwrite each other, which made a state read 'unavailable' on
    one split and available on the other.
    """
    tx = subject(key="n1", state="TX")
    fl = subject(key="n1", state="FL")
    assert reach_key(tx) != reach_key(fl)


# ------------------------------------------------------------- aggregation

def test_all_unavailable_reports_unavailable_not_zero():
    path = FakePath({"SC": ["SC.A"]})
    subs = [subject(key=f"n{i}", state="TX") for i in range(3)]
    reaches = {reach_key(s): reach_of(s, path) for s in subs}
    m = evaluate(subs, path.rank, reaches)
    assert m["unavailable"] is True
    assert "raw_recall@10" not in m, "an unavailable group must not report a score"


def test_conditional_is_none_when_nothing_is_reachable():
    """Distinct from a conditional score of zero, and must not collapse into it."""
    path = FakePath({"MD": ["MD.9.OTHER.1"]})
    subs = [subject(key=f"n{i}", state="MD", truth=("MD.1.GR.C.5",))
            for i in range(3)]
    reaches = {reach_key(s): reach_of(s, path) for s in subs}
    m = evaluate(subs, path.rank, reaches)
    assert m["unavailable"] is False
    assert m["cond_recall@10"] is None
    assert m["raw_recall@10"] == 0.0
    assert m["n_reachable"] == 0


def test_raw_counts_every_anchored_subject_conditional_only_reachable():
    path = FakePath({"TX": ["TX.4.6A"]}, {("4.G.A.1", "TX"): ["TX.4.6A"]})
    subs = [
        subject(key="hit"),                                  # reachable, found
        subject(key="miss", anchors=("9.Z.9",)),             # unreachable
    ]
    reaches = {reach_key(s): reach_of(s, path) for s in subs}
    m = evaluate(subs, path.rank, reaches)
    assert m["subjects"] == 2 and m["n_reachable"] == 1
    assert m["cond_recall@10"] == 1.0, "the ranker got the reachable one right"
    assert m["raw_recall@10"] == 0.5, "raw still carries the unreachable subject"


def test_excluded_subjects_never_reach_the_score():
    path = FakePath({"TX": ["TX.4.6A"]}, {("4.G.A.1", "TX"): ["TX.4.6A"]})
    subs = [subject(key="ok"), subject(key="noanchor", anchors=())]
    reaches = {reach_key(s): reach_of(s, path) for s in subs}
    m = evaluate(subs, path.rank, reaches)
    assert m["subjects"] == 1, "the anchorless subject is not a test"
    assert m["raw_recall@10"] == 1.0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("all passed")
