"""
paths.py — one adapter per generation path, for reach gating.

The harness needs to know two things about a path before it scores anything:

    vocabulary(state)              what this path could EVER emit for this state
    reachable_from(anchors, state) what it could emit from THESE anchors

Both are plain SELECTs. That matters: had this existed on day one, the Florida
finding — FL is in all_states only at grades 6-9 and in neither crosswalk file —
would have surfaced in a precondition report instead of after a full harness
build, printed as `recall = 0.000` and indistinguishable from a broken ranker.

Only Path B has a ranker today. The other three expose reach only, and gain
rankers in step 6. The interface is the same either way so the bucketing and
metric code is written once.
"""

import sqlite3
from collections import defaultdict


def canon(code: str) -> str:
    """
    §3 Path 0: strip a leading 'CA.', then drop segment 3 if it is a single
    letter. CA codes omit the cluster letter that CCSS carries.

        CA.1.MD.3 -> 1.MD.3
        1.MD.B.3  -> 1.MD.3
    """
    parts = code.split(".")
    if parts and parts[0].upper() == "CA":
        parts = parts[1:]
    if len(parts) >= 3 and len(parts[2]) == 1 and parts[2].isalpha():
        parts.pop(2)
    return ".".join(parts)


class Path:
    """Base adapter. Subclasses fill in the two reach methods."""

    name = "path"
    label = "path"

    def vocabulary(self, state: str) -> set[str]:
        raise NotImplementedError

    def reachable_from(self, anchors, state: str) -> set[str]:
        raise NotImplementedError

    def available(self, state: str) -> bool:
        """
        Does this path have ANY vocabulary for this state?

        False means every subject for that state is `path unavailable` and must
        never be scored as 0.000 — the path could not name a correct answer
        under any ranking, so a zero would describe the data, not the ranker.
        """
        return bool(self.vocabulary(state))


class Path0(Path):
    """CA code inheritance. California only, by construction."""

    name = "path0"
    label = "Path 0 (CA code)"

    def __init__(self, cur: sqlite3.Cursor):
        # canon(CCSS code) -> the CCSS codes sharing it.
        self._ccss_by_canon = defaultdict(set)
        for (code,) in cur.execute(
                "SELECT standard_id FROM standards WHERE jurisdiction='CCSS'"):
            self._ccss_by_canon[canon(code)].add(code)

        # canon -> CA codes, but only where a CCSS code shares the canon. A CA
        # code with no CCSS counterpart is not something Path 0 can produce.
        self._ca_by_canon = defaultdict(set)
        for (code,) in cur.execute(
                "SELECT standard_id FROM standards WHERE jurisdiction='CA'"):
            key = canon(code)
            if key in self._ccss_by_canon:
                self._ca_by_canon[key].add(code)

    def vocabulary(self, state: str) -> set[str]:
        if state != "CA":
            return set()
        return {c for codes in self._ca_by_canon.values() for c in codes}

    def reachable_from(self, anchors, state: str) -> set[str]:
        if state != "CA":
            return set()
        out = set()
        for anchor in anchors:
            out |= self._ca_by_canon.get(canon(anchor), set())
        return out


class PathA(Path):
    """Lesson overlap. A state code is reachable when it shares a lesson."""

    name = "pathA"
    label = "Path A (lesson)"

    def __init__(self, cur: sqlite3.Cursor):
        self._lessons = defaultdict(set)          # code -> lessons
        self._by_state = defaultdict(set)         # state -> its codes
        for code, lesson in cur.execute(
                "SELECT standard_code, lesson_id FROM standard_lessons"):
            self._lessons[code].add(lesson)
            head = code.split(".")[0].upper()
            if len(head) == 2 and head.isalpha():
                self._by_state[head].add(code)

        # lesson -> state codes, so reach is a lookup rather than a scan.
        self._codes_by_lesson = defaultdict(set)
        for state, codes in self._by_state.items():
            for code in codes:
                for lesson in self._lessons[code]:
                    self._codes_by_lesson[lesson].add(code)

    def vocabulary(self, state: str) -> set[str]:
        return set(self._by_state.get(state, ()))

    def reachable_from(self, anchors, state: str) -> set[str]:
        anchor_lessons = set()
        for anchor in anchors:
            anchor_lessons |= self._lessons.get(anchor, set())
        out = set()
        for lesson in anchor_lessons:
            out |= self._codes_by_lesson.get(lesson, set())
        return {c for c in out if c.split(".")[0].upper() == state}


class PathB(Path):
    """Crosswalk edges. The only path with a ranker before step 6."""

    name = "pathB"
    label = "Path B (crosswalk)"

    def __init__(self, cur: sqlite3.Cursor):
        self._by_state = defaultdict(set)
        self._edges = defaultdict(set)            # (ccss, state) -> state codes
        for state, state_code, ccss in cur.execute(
                "SELECT state, state_code, ccss_code FROM crosswalk"):
            self._by_state[state].add(state_code)
            self._edges[(ccss, state)].add(state_code)

    def vocabulary(self, state: str) -> set[str]:
        return set(self._by_state.get(state, ()))

    def reachable_from(self, anchors, state: str) -> set[str]:
        out = set()
        for anchor in anchors:
            out |= self._edges.get((anchor, state), set())
        return out

    def rank(self, subject) -> list[str]:
        """
        Rank by how many of the subject's anchors vote for a candidate.

        Deliberately thin — this is the placeholder from step 5, not the
        generator. Ties keep a stable order so runs stay comparable.
        """
        votes = defaultdict(int)
        for anchor in subject.anchors:
            for code in self._edges.get((anchor, subject.state), ()):
                votes[code] += 1
        return [c for c, _ in sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))]


class PathC(Path):
    """Model predictions. Empty until the offline run lands (step 8)."""

    name = "pathC"
    label = "Path C (model)"

    def __init__(self, cur: sqlite3.Cursor):
        self._by_state = defaultdict(set)
        self._edges = defaultdict(set)
        for state, state_code, ccss in cur.execute(
                "SELECT state, state_code, ccss_code FROM model_predictions"):
            self._by_state[state].add(state_code)
            self._edges[(ccss, state)].add(state_code)

    def vocabulary(self, state: str) -> set[str]:
        return set(self._by_state.get(state, ()))

    def reachable_from(self, anchors, state: str) -> set[str]:
        out = set()
        for anchor in anchors:
            out |= self._edges.get((anchor, state), set())
        return out


class UnionPath(Path):
    """
    Every path at once — the reach the step 6 generator actually has.

    Scoring the generator against one path's reach would understate it: a
    candidate Path A cannot name may still arrive through Path B. This unions
    the reach so `unreachable` means no path could have produced the truth
    code, which is the only reading under which a generator miss is the
    generator's fault.

    Note the states this makes `available`: the union is available wherever ANY
    path has vocabulary, so a state with Path A data and nothing else is a real
    test rather than `path unavailable`. TX has none of it and stays
    unavailable, which is exactly the §5 distinction working.
    """

    name = "generator"
    label = "Generator (0/A/B/C)"

    def __init__(self, paths: list[Path]):
        self._paths = paths

    def vocabulary(self, state: str) -> set[str]:
        out = set()
        for p in self._paths:
            out |= p.vocabulary(state)
        return out

    def reachable_from(self, anchors, state: str) -> set[str]:
        out = set()
        for p in self._paths:
            out |= p.reachable_from(anchors, state)
        return out


def all_paths(cur: sqlite3.Cursor) -> list[Path]:
    """Every path, in §3 order."""
    return [Path0(cur), PathA(cur), PathB(cur), PathC(cur)]
