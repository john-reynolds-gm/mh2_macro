"""
parse_node_standards.py — turn the ladders' standards cells into rows.

The `Notes related to Standards, Grade, or Leaf` cell is the richest hand-
authored data in the project and the hardest to read: codes with no reliable
delimiter, prose interleaved, and three different kinds of statement mixed
together. This module converts one cell into three things:

  node_standards_parsed     a code, with a relation and any note about it
  node_absence_assertions   'No CCSSM for ordinal numbers' — a claim about the
                            FRAMEWORK, carrying no code at all
  residual prose            everything else, kept verbatim and reported

Absence assertions are not annotations and not codes. They say the CCSS
framework does not cover this node. They scope to the named framework ONLY and
must never suppress state candidate generation — a node CCSS omits is exactly
where a state standard is most likely to exist and most valuable to surface.
There is no sentinel code; the assertion has nowhere to live in the tag table
and does not belong there.

Parsing runs line by line, because that is how attachment works: prose after a
code on the same line belongs to that code, and a line with no code at all is a
statement about the cell.

Run: python -m mh2.parse_node_standards --db mh2.db
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

from mh2.ingest_ladders import read_docx  # noqa: E402
from mh2.load_stems import ladder_paths  # noqa: E402
from mh2.normalize import find_codes  # noqa: E402


class ParsedCode(NamedTuple):
    code: str
    state: str | None           # None for CCSS
    relation: str               # aligned | partial | exceeds
    annotation: str | None


class Absence(NamedTuple):
    framework: str              # CCSS | unknown
    note: str                   # verbatim


class Residual(NamedTuple):
    text: str                   # verbatim
    label: str                  # partial|exceeds|provenance|absence|unclassified
    attached_to: str | None     # the code it was attached to, or None


class ParsedCell(NamedTuple):
    codes: list[ParsedCode]
    absences: list[Absence]
    residuals: list[Residual]
    # (parent code, fragment, recovered code) for sibling shorthand. Reported so
    # a recovered tag is always visible as a recovery, never as a plain tag.
    siblings: list[tuple[str, str, str]] = []


# --------------------------------------------------------------- classifying
#
# None of these rules has a crisp right answer, which is why every fragment is
# reported with the label it got rather than just counted. Order matters: the
# first rule that fires wins, so the specific patterns come before the general
# ones.

# 'partially tagged', 'Does not include rays in standard' — the node covers only
# part of the standard.
_PARTIAL_RE = re.compile(
    r"\bpartial|\bdoes ?n[o']t include\b|\bdoesn.t include\b|\bonly the\b|\bjust the\b",
    re.I)

# '("to at least 20")', 'requires by division', 'larger place values than core
# G3' — the standard demands something past what the node teaches. This row is
# the leaf signal.
_EXCEEDS_RE = re.compile(
    r"\bto at least\b|\bat least\b|\bexceeds?\b|\bbeyond\b|\bgoes to\b"
    r"|\brequires?\b|\brequired\b|\blarger than\b|\bmore than\b|\bdifferent from\b"
    r"|\bnumber range is different\b|\bspecifies\b",
    re.I)

# 'In EM2 this work is noted as being foundational to...', '--added MF 5/29',
# 'I didn't double check' — where the tag came from or who touched it, not a
# statement about the standard's content.
_PROVENANCE_RE = re.compile(
    r"\bEM2\b|\bTX BB\b|\badded\b|\bdouble check\b|\bI did ?n.t\b|\bnoted as\b"
    r"|\bfoundational\b|\bprecursor\b|\bgaps? that are\b|\bDONE\b"
    r"|\b[A-Z]{2,3} \d{1,2}/\d{1,2}\b",
    re.I)

# '(to 10,000)', '(2-digit x 2-digit)', '(denominators of 2, 3, 4, 5, 6, 8, and
# 10)', '(5-digit dividend, SA, remainder as fraction)'. About a hundred
# fragments say how far a state's version RANGES — and nothing about whether
# the node covers more or less than it. §1's four labels have no name for that,
# and forcing them into partial or exceeds would need the node's own intended
# range, which is recorded nowhere. So they get their own label, keep their
# prose in `annotation`, and leave `relation` at 'aligned'. Deciding partial vs
# exceeds is the leaf generator's job, with the node text in front of it.
_SCOPE_RE = re.compile(
    r"\bto\s+[\d,]|\bwithin\s+[\d,]|\bup to\b|\blimited to\b"
    r"|\bless than or equal\b|\ball denominators\b|\bdenominators?\b"
    r"|\b\d+ ?-? ?digit\b|\bfour-digit\b|\bseven-digit\b|\bsix digit\b"
    r"|\bdividend\b|\bdivisor\b|\bremainder\b|\bdecimals? to\b"
    r"|\bhalves\b|\bfourths\b|\bfluency\b|\bmultiple of \d",
    re.I)

_NEGATION_RE = re.compile(r"\b(?:no|none|not|n/a|lacks?|missing|absent)\b", re.I)
_FRAMEWORK_RE = re.compile(r"\bCCSSM?\b|\bcommon core\b|\bstandards?\b", re.I)
_CCSS_NAMED_RE = re.compile(r"\bCCSSM?\b|\bcommon core\b", re.I)

# Fragments shorter than this are punctuation and list debris ('and', ',', 'B'),
# not prose worth reporting. Kept low deliberately — under-reporting here hides
# exactly the authored detail step 2 exists to capture.
MIN_FRAGMENT = 3


def is_absence_assertion(text: str) -> bool:
    """
    True for 'No CCSSM for ordinal numbers'.

    Deliberately conservative: a negation AND a framework name. The caller only
    offers fragments from lines carrying no code, because an assertion that the
    framework omits something cannot also be a note about a specific standard.
    """
    return bool(_NEGATION_RE.search(text) and _FRAMEWORK_RE.search(text))


def framework_of(text: str) -> str:
    """'CCSS' when the text names CCSS or CCSSM, else 'unknown' for a human."""
    return "CCSS" if _CCSS_NAMED_RE.search(text) else "unknown"


def classify(text: str, has_code: bool) -> str:
    """
    Label a residual fragment. 'unclassified' is a real answer, not a failure.

    Order matters: an explicit partial/exceeds claim beats a scope reading, so
    '("to at least 20")' stays `exceeds` rather than being swallowed by the
    'to <number>' scope pattern.
    """
    if not has_code and is_absence_assertion(text):
        return "absence"
    if _PARTIAL_RE.search(text):
        return "partial"
    if _EXCEEDS_RE.search(text):
        return "exceeds"
    if _SCOPE_RE.search(text):
        return "scope"
    if _PROVENANCE_RE.search(text):
        return "provenance"
    return "unclassified"


# Sibling shorthand: a breakout of the SAME parent as the code just before it.
#   TX.3.3A   'B (less than or equal to 1)'   -> TX.3.3B
#   VA.3.NS.2.a   'and 2.b'                   -> VA.3.NS.2.b
#   MD.3.NOS.A.1  '. and 1.b'                 -> MD.3.NOS.A.1.b
#   5.NBT.A.3     'and 3a'                    -> 5.NBT.A.3a
# These are real hand-authored tags. The tokenizer cannot see them, because on
# their own '2.b' and 'B' are not codes — they only mean something relative to
# the code they follow.
_SIBLING_NUMERIC_RE = re.compile(r"^[.,]?\s*(?:and\s+)?(\d+)(\.?)([a-z])\b")
_SIBLING_TEKS_RE = re.compile(r"^([A-Z])\b")


def sibling_code(attached: str, fragment: str) -> str | None:
    """
    Resolve sibling shorthand against the code it follows. None if it isn't one.

    Two shapes, both keyed on the fragment repeating part of the parent so a
    coincidental letter cannot be mistaken for a breakout.
    """
    if not attached:
        return None

    # TEKS: a bare capital after a code ending in a capital. TX.3.3A + 'B'.
    m = _SIBLING_TEKS_RE.match(fragment)
    if m and attached[-1].isupper() and attached[-2:-1].isdigit():
        return attached[:-1] + m.group(1)

    m = _SIBLING_NUMERIC_RE.match(fragment)
    if not m:
        return None
    head, dot, letter = m.group(1), m.group(2), m.group(3)
    segments = attached.split(".")

    # 'and 3a' after 5.NBT.A.3 — the parent's LAST segment repeats, so the
    # letter hangs off it.
    if segments[-1] == head:
        return f"{attached}.{letter}" if dot else f"{attached}{letter}"

    # 'and 2.b' after VA.3.NS.2.a — the parent's SECOND-TO-LAST segment repeats,
    # so the letter replaces the final one.
    if len(segments) >= 2 and segments[-2] == head:
        return ".".join(segments[:-1] + [letter])

    return None


def _clean_fragment(text: str) -> str:
    """Trim list punctuation from a residual fragment, keeping the words."""
    return text.strip().strip(",;:| \t").strip()


def parse_cell(cell: str) -> ParsedCell:
    """
    Split one standards cell into codes, absence assertions and residual prose.

    Line by line, because that is how attachment works: prose following a code
    on the same line is a note about that code, and a line with no code is a
    statement about the cell as a whole.
    """
    order: list[str] = []               # codes, first-seen order
    state_of: dict[str, str | None] = {}
    note_of: dict[str, str] = {}        # code -> the prose attached to it
    absences: list[Absence] = []
    residuals: list[Residual] = []
    siblings: list[tuple[str, str, str]] = []   # (parent, fragment, recovered)
    if not cell:
        return ParsedCell([], absences, residuals, siblings)

    def keep(fragment: str, attached_to: str | None) -> None:
        """Record a residual fragment and attach it to its code, if it has one."""
        if len(fragment) < MIN_FRAGMENT:
            return

        # A fragment may open with sibling shorthand ('and 2.b', 'B (…)'). That
        # part is a real code, not prose — recover it before recording the rest.
        if attached_to is not None:
            sibling = sibling_code(attached_to, fragment)
            if sibling and sibling not in state_of:
                state_of[sibling] = state_of.get(attached_to)
                order.append(sibling)
                siblings.append((attached_to, fragment, sibling))

        residuals.append(
            Residual(fragment, classify(fragment, has_code=True), attached_to))
        if attached_to is not None:
            note_of[attached_to] = (
                f"{note_of[attached_to]} {fragment}".strip()
                if attached_to in note_of else fragment)

    for line in str(cell).split("\n"):
        if not line.strip():
            continue
        matches = find_codes(line)

        if not matches:
            frag = _clean_fragment(line)
            if len(frag) < MIN_FRAGMENT:
                continue
            label = classify(frag, has_code=False)
            if label == "absence":
                absences.append(Absence(framework_of(frag), frag))
            residuals.append(Residual(frag, label, None))
            continue

        # Walk the gaps between code spans. A gap belongs to the code before it;
        # anything before the first code belongs to the cell. Range expansion
        # makes several matches share one span, so track the furthest point
        # consumed rather than assuming the spans are disjoint.
        cursor = 0
        previous: str | None = None
        for m in matches:
            if m.start >= cursor:
                keep(_clean_fragment(line[cursor:m.start]), previous)
            cursor = max(cursor, m.end)
            if m.code not in state_of:
                state_of[m.code] = m.state
                order.append(m.code)
            previous = m.code

        keep(_clean_fragment(line[cursor:]), previous)

    codes = [
        ParsedCode(code, state_of[code],
                   _relation_for(note_of.get(code)), note_of.get(code))
        for code in order
    ]
    return ParsedCell(codes, absences, residuals, siblings)


def _relation_for(note: str | None) -> str:
    """A code's relation follows the note attached to it."""
    if not note:
        return "aligned"
    label = classify(note, has_code=True)
    return label if label in ("partial", "exceeds") else "aligned"


# ------------------------------------------------------------------- loading

def load(cur, ladder_dir: Path) -> dict:
    """
    Parse every ladder's standards cells into the two tables.

    Nodes are matched on (source_file, node_text), which is unique within a
    ladder — the merged-cell fix in ingest_ladders is what makes that true. An
    unmatched node is an error, not a warning: it would silently drop a cell.
    """
    node_ids = {}
    for node_id, source_file, node_text in cur.execute(
            "SELECT node_id, source_file, node_text FROM nodes"):
        node_ids[(source_file, node_text)] = node_id

    stats = {"cells": 0, "codes": 0, "absences": 0, "residuals": 0, "unmatched": 0}
    report = {"residuals": [], "absences": [], "cells": []}

    for path in ladder_paths(ladder_dir):
        for node in read_docx(path, path.stem):
            cell = (node.get("standards_notes") or "").strip()
            if not cell:
                continue
            key = (node["source_file"], node["node_text"])
            node_id = node_ids.get(key)
            if node_id is None:
                stats["unmatched"] += 1
                print(f"  ! no node row for {key[0]} / {key[1][:60]!r}")
                continue

            parsed = parse_cell(cell)
            stats["cells"] += 1

            for c in parsed.codes:
                cur.execute(
                    "INSERT OR IGNORE INTO node_standards_parsed (node_id,"
                    " standard_code, state, relation, annotation, source_cell)"
                    " VALUES (?,?,?,?,?,?)",
                    (node_id, c.code, c.state, c.relation, c.annotation, cell))
                stats["codes"] += 1

            for a in parsed.absences:
                cur.execute(
                    "INSERT OR IGNORE INTO node_absence_assertions (node_id,"
                    " framework, note, source_cell) VALUES (?,?,?,?)",
                    (node_id, a.framework, a.note, cell))
                stats["absences"] += 1
                report["absences"].append((node_id, a.framework, a.note))

            for r in parsed.residuals:
                stats["residuals"] += 1
                report["residuals"].append(
                    (node_id, node["concept_skill"], node["node_text"],
                     r.text, r.label, r.attached_to))

            report["cells"].append((node_id, path.name, node["concept_skill"],
                                    node["node_text"], cell, parsed))
    return stats | {"report": report}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB))
    ap.add_argument("--ladders", default=str(config.LADDERS))
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    stats = load(cur, Path(args.ladders))
    con.commit()
    con.close()

    print(f"cells parsed:        {stats['cells']}")
    print(f"codes written:       {stats['codes']}")
    print(f"absence assertions:  {stats['absences']}")
    print(f"residual fragments:  {stats['residuals']}")
    if stats["unmatched"]:
        print(f"UNMATCHED NODES:     {stats['unmatched']}")


if __name__ == "__main__":
    main()
