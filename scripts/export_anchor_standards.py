"""
export_anchor_standards.py — the two CSVs the §3.5.1 state→state run needs.

The 28 unanchored nodes carry no CCSS code, so Paths 0, A and B cannot reach
them. §3.5.1's ruling is to anchor on the state codes the authors already wrote
by hand. A state→state run needs both sides of that:

  unanchored_node_anchors.csv   SOURCE — the codes we align FROM (the anchors)
  state_alignment_targets.csv   TARGET — the corpus we align TO

Both are state, grade, code, description.

The target corpus is every standard we hold with text, for the priority states
plus every state appearing on these nodes. It is NOT filtered by grade: §4 is
explicit that grade is a prior and never a filter, and 33% of FL and 31% of TX
hand tags sit at a grade offset. The grade column is there so the run can decay
by offset rather than exclude. Filter it there if you want to, deliberately.

Anything without text is reported to stdout rather than written as a blank row —
a blank description would come back from the model as a zero-similarity result
and read as a bad alignment rather than as missing input.

Run: python scripts/export_anchor_standards.py
"""

import csv
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from mh2.normalize import grade_of  # noqa: E402

# The gaps sheet writes some codes as 'IN.PK.M1.3 not numbered'. normalize_code
# strips spaces, so those landed in `standards` as 'IN.PK.M1.3notnumbered' --
# the text is present, under a mangled key. Undo that when matching. 15 rows are
# affected; see the loader note printed at the end.
GLUED_SUFFIX_RE = re.compile(r"notnumbered$", re.I)

# EXPORT-ONLY patches. These do NOT change node_standards_parsed — John's call
# was to patch the CSV and leave the database as it is, so both underlying
# problems are still live and are recorded in §9.1:
#
#   GA.PK.CD-MA  the ladder cell actually reads 'GA.PK.CD-MA3.4d' and
#                'GA.PK.CD-MA7.4a'. Our tokenizer stops at the hyphenated
#                segment and drops the rest into residual prose, so the parsed
#                code is a truncation of two real codes. The same truncation
#                will recur on any future code of that shape.
#   MN.PK.M1.1   not in the workbook. MN.PK.M1.14 is, and its text ('up to at
#                least 29') matches the annotation the author wrote in that
#                same cell. Reads as a dropped digit, but it is an authoring
#                fix in the ladder, not something to correct in code.
CODE_PATCHES = {
    "GA.PK.CD-MA": ["GA.PK.CD-MA3.4d", "GA.PK.CD-MA7.4a"],
    "MN.PK.M1.1": ["MN.PK.M1.14"],
}

# Priority states are always in the target corpus even if they do not anchor
# any of the 28 nodes -- they are what the tool exists to serve.
PRIORITY_STATES = {"TX", "FL", "CA"}

UNANCHORED_SQL = """
    SELECT node_id FROM nodes
    WHERE node_id NOT IN (
        SELECT node_id FROM node_standards_parsed WHERE state IS NULL)
"""


def main() -> None:
    con = sqlite3.connect(config.DB)
    cur = con.cursor()

    anchors = cur.execute(f"""
        SELECT DISTINCT p.state, p.standard_code
        FROM node_standards_parsed p
        WHERE p.node_id IN ({UNANCHORED_SQL})
          AND p.state IS NOT NULL
        ORDER BY p.state, p.standard_code
    """).fetchall()

    # Standard text, plus a lookup with the glued suffix removed.
    text_of, grade_of_code = {}, {}
    for code, grade, text in cur.execute(
            "SELECT standard_id, grade, text FROM standards"):
        if not text or not str(text).strip():
            continue
        text_of[code] = str(text).strip()
        grade_of_code[code] = grade
        unglued = GLUED_SUFFIX_RE.sub("", code)
        if unglued != code:
            text_of.setdefault(unglued, str(text).strip())
            grade_of_code.setdefault(unglued, grade)

    # Codes exactly as stored, so a code that only matched after ungluing can be
    # reported as a recovery rather than passed off as a clean hit.
    real_ids = {c for (c,) in cur.execute("SELECT standard_id FROM standards")}

    rows, missing, recovered, patched = [], [], [], []
    seen: set[tuple[str, str]] = set()
    for state, code in anchors:
        # A patched code stands in for one or more real ones.
        for actual in CODE_PATCHES.get(code, [code]):
            if actual not in text_of:
                missing.append((state, actual))
                continue
            if actual != code:
                patched.append(f"{code} -> {actual}")
            elif actual not in real_ids:
                recovered.append(actual)
            # A patch target can also be a real anchor on another node --
            # MN.PK.M1.14 is both. One row per code, not one per anchor.
            if (state, actual) in seen:
                continue
            seen.add((state, actual))
            grade = grade_of_code.get(actual) or grade_of(actual) or ""
            rows.append([state, grade, actual, text_of[actual]])

    out = config.REPORTS / "unanchored_node_anchors.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["state", "grade", "code", "description"])
        w.writerows(rows)

    print(f"anchor codes on the 28 unanchored nodes: {len(anchors)}")
    print(f"  written with text: {len(rows)}")
    print(f"  no text found:     {len(missing)}")
    print(f"  -> {out}")

    if recovered:
        print(f"\nRecovered past the 'not numbered' loader defect ({len(recovered)}):")
        for code in recovered:
            print(f"  {code}")

    if patched:
        print(f"\nExport-only patches ({len(patched)}) — the database still holds "
              "the wrong code, see §9.1:")
        for p in patched:
            print(f"  {p}")

    if missing:
        print("\nNO TEXT — excluded from the CSV, needs a ruling:")
        for state, code in missing:
            print(f"  {state}  {code}")
            near = [c for c in real_ids
                    if c.startswith(code) or code.startswith(c)][:6]
            for n in sorted(near):
                if n != code:
                    print(f"       near: {n}  {text_of.get(n, '')[:60]}")

    by_state: dict[str, int] = {}
    for state, _g, _c, _t in rows:
        by_state[state] = by_state.get(state, 0) + 1
    print("\nSource codes per state:")
    for state, n in sorted(by_state.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {state:4s} {n}")

    write_target_corpus(cur, states=set(by_state) | PRIORITY_STATES)
    con.close()


def write_target_corpus(cur, states: set[str]) -> None:
    """
    Every standard we hold with text, for the states in scope.

    Deliberately not grade-filtered — see the module docstring. The grade column
    carries PK/K/1..12 so the run can decay by offset, which is what §4 asks for.
    """
    K5 = {"PK", "K", "1", "2", "3", "4", "5"}
    rows, band = [], {"PK-5": 0, "6-12": 0}
    for code, grade, text in cur.execute(
            "SELECT standard_id, grade, text FROM standards"
            " WHERE text IS NOT NULL AND TRIM(text) <> ''"
            " ORDER BY standard_id"):
        state = code.split(".")[0].upper()
        if state not in states:
            continue
        grade = (grade or grade_of(code) or "").upper()
        rows.append([state, grade, code, str(text).strip()])
        band["PK-5" if grade in K5 else "6-12"] += 1

    out = config.REPORTS / "state_alignment_targets.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["state", "grade", "code", "description"])
        w.writerows(rows)

    print(f"\nTarget corpus: {len(rows):,d} standards across {len(states)} states")
    print(f"  PK-5 {band['PK-5']:,d}   6-12 {band['6-12']:,d}"
          "   (not filtered — filter on the grade column if you want to)")
    print(f"  -> {out}")


if __name__ == "__main__":
    main()
