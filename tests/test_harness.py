"""
Eval harness metric tests.

The metrics decide whether anything ships, so they are tested against
hand-worked examples rather than against whatever the harness happens to
produce.

Run with: python -m pytest tests/ -q  (or: python tests/test_harness.py)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.harness import (
    Subject, assign_splits, is_blind, node_placement, precision_at_k,
    recall_at_k, reciprocal_rank,
)

RANKED = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K"]


def test_recall_at_k():
    assert recall_at_k(RANKED, frozenset({"A", "B"}), 5) == 1.0
    assert recall_at_k(RANKED, frozenset({"A", "Z"}), 5) == 0.5
    # In the list but past the cutoff.
    assert recall_at_k(RANKED, frozenset({"K"}), 5) == 0.0
    assert recall_at_k(RANKED, frozenset({"K"}), 11) == 1.0
    assert recall_at_k(RANKED, frozenset(), 5) == 0.0


def test_precision_at_k():
    assert precision_at_k(RANKED, frozenset({"A", "B", "C", "D", "E"}), 5) == 1.0
    assert precision_at_k(RANKED, frozenset({"A"}), 5) == 0.2
    assert precision_at_k([], frozenset({"A"}), 5) == 0.0


def test_reciprocal_rank():
    assert reciprocal_rank(RANKED, frozenset({"A"})) == 1.0
    assert reciprocal_rank(RANKED, frozenset({"C"})) == 1 / 3
    assert reciprocal_rank(RANKED, frozenset({"Z"})) == 0.0
    # The FIRST hit is what counts, not the best one.
    assert reciprocal_rank(RANKED, frozenset({"B", "D"})) == 0.5


def test_blind_row():
    """A blind row is one where nothing correct appears ANYWHERE in the list."""
    assert is_blind(RANKED, frozenset({"Z"})) is True
    assert is_blind(RANKED, frozenset({"K"})) is False   # last place still counts
    assert is_blind([], frozenset({"A"})) is True


def test_node_placement_exact():
    hit, agreement = node_placement(frozenset({"n1"}), frozenset({"n1"}))
    assert hit is True and agreement == 1.0


def test_node_placement_partial_overlap():
    hit, agreement = node_placement(frozenset({"n1", "n2"}),
                                    frozenset({"n2", "n3"}))
    assert hit is True and agreement == 1 / 3


def test_node_placement_miss():
    hit, agreement = node_placement(frozenset({"n1"}), frozenset({"n2"}))
    assert hit is False and agreement == 0.0


def test_jaccard_punishes_assigning_everything_everywhere():
    """
    §5: hit rate alone is gameable by putting every standard on every node.
    The Jaccard is the only thing that stops it, which is why both are reported
    or neither is.
    """
    author = frozenset({"n1"})
    everywhere = frozenset({"n1", "n2", "n3", "n4", "n5"})
    hit, agreement = node_placement(everywhere, author)
    assert hit is True, "the gaming strategy does score a hit"
    assert agreement == 0.2, "and the set agreement is what exposes it"


def test_node_placement_handles_multi_node_truth():
    """A standard on three author nodes: matching any one of them is a hit."""
    author = frozenset({"n1", "n2", "n3"})
    hit, agreement = node_placement(frozenset({"n2"}), author)
    assert hit is True and agreement == 1 / 3


def _subject(key, stem_id):
    return Subject(key=key, grain="node", stem_id=stem_id, state="TX",
                   anchors=(), truth=frozenset(), truth_nodes={})


def test_splits_are_stratified_by_stem():
    """
    Every stem must be represented on the tuning side in proportion, not by
    luck. Stem sizes range from 2 nodes to 33, so an unstratified draw can
    leave a whole stem out of one side.
    """
    subjects = ([_subject(f"a{i}", "AAA") for i in range(10)]
                + [_subject(f"b{i}", "BBB") for i in range(10)])
    split = assign_splits(subjects)
    for stem_id in ("AAA", "BBB"):
        tuning = [s for s in split if s.stem_id == stem_id and s.split == "tuning"]
        assert len(tuning) == 3, f"{stem_id}: {len(tuning)}"


def test_splits_are_reproducible():
    subjects = [_subject(f"a{i}", "AAA") for i in range(20)]
    first = [s.split for s in assign_splits(subjects)]
    second = [s.split for s in assign_splits(subjects)]
    assert first == second


def test_every_subject_lands_in_exactly_one_split():
    subjects = [_subject(f"a{i}", "AAA") for i in range(20)]
    split = assign_splits(subjects)
    assert len(split) == 20
    assert all(s.split in ("tuning", "held_out") for s in split)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("all passed")
