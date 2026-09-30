"""
load_grade_ruling.py — load the human grade ruling into node_grade /
node_grade_ruling.

    python -m mh2.load_grade_ruling --db data/build/mh2.db

`nodes.grade_or_leaf` is uncontrolled free text: 93 distinct raw values across
353 nodes. `data/source/workbooks/mh2_grade_normalization_worksheet.xlsx`
(sheet 'Worksheet') is a human ruling, one row per distinct raw value, saying
what grade(s) that value actually means. This loader is the join between the
two: canonicalize both sides with `canonical_grade_key()` and record, per
node, which grades it belongs to and how that was decided.

Four worksheet columns drive node_grade: raw_value, default_grades, is_leaf,
notes. Everything else on the sheet (count, n_ladders, suggested_type,
suggested_grades, state_overrides, examples) is derived, superseded, or a
pre-consultation artifact -- see the brief for why each one is skipped. In
particular `state_overrides` is empty by design: state-specific grade
extensions were folded into `default_grades`, with the prose kept in `notes`.

Additive, for the Grade Sequencing Tool only (the audit never reads any of
this -- DEFERRED.md section 6): ruling_type, needs_writer_review and
states_mentioned are copied onto node_grade_ruling, and node_grade_kind
records per (node, grade) whether the grade is core / span / state_extension
/ unconfirmed. An optional worksheet column `ccss_default_grades`, if John
adds it, upgrades state_conditional / range_prose rows from 'unconfirmed' to
core + state_extension. None of it changes node_grade.

Resolution is per node, not per raw string, because `is_leaf` wins
unconditionally: five leaf rows in the worksheet also carry grades (they are
annotation -- 'this state teaches it, but it is still a leaf here'), and a
leaf node must never fail a grade-containment test regardless of what
node_grade rows it carries.
"""

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

import config  # noqa: E402

from mh2.normalize import canonical_grade_key  # noqa: E402

# Grade tokens are not numeric -- PK < K < 1 -- so ordering is a lookup table,
# not arithmetic. OUT (9-12, A2, Geometry, ...) is deliberately unordered: it
# never appears in this worksheet's data, and no span containment logic should
# ever compare against it.
GRADE_ORDER = [
    ("PK", 0), ("K", 1), ("1", 2), ("2", 3), ("3", 4), ("4", 5),
    ("5", 6), ("6", 7), ("7", 8), ("8", 9), ("A1", 10), ("OUT", 99),
]
VALID_GRADES = {g for g, _ in GRADE_ORDER}

WORKSHEET_SHEET = "Worksheet"
WORKSHEET_COLUMNS = ["raw_value", "default_grades", "is_leaf", "notes"]

# Present in the real worksheet; absent columns (older files, test fixtures)
# are treated as blank.
OPTIONAL_COLUMNS = ["ruling_type", "needs_writer_review", "states_mentioned",
                    "ccss_default_grades"]

# ruling_types where "which grades are the CCSS default" is an open question.
OPEN_TYPES = ("state_conditional", "range_prose", "alternative", "unparsed")

RESOLUTIONS = ("ruled", "leaf", "unresolved", "no_grade_field")


def seed_grade_order(con) -> None:
    con.execute("DELETE FROM grade_order")
    con.executemany("INSERT INTO grade_order (grade, ord) VALUES (?, ?)",
                    GRADE_ORDER)


def parse_is_leaf(raw_value, cell) -> bool:
    """
    Case-insensitive against 'true'/'false' after stripping. The file mixes
    'TRUE', 'FALSE', and 'False' -- anything else is a hard error, not a
    silent FALSE, because a typo here would silently promote a leaf row to a
    normal one or vice versa.
    """
    s = str(cell).strip().casefold()
    if s == "true":
        return True
    if s == "false":
        return False
    raise SystemExit(
        f"load_grade_ruling: unreadable is_leaf value {cell!r} for worksheet"
        f" row {raw_value!r}")


def parse_default_grades(raw_value, cell) -> list[str]:
    """Comma-split, stripped, validated against grade_order. [] for blank."""
    if pd.isna(cell):
        return []
    tokens = [t.strip() for t in str(cell).split(",")]
    for token in tokens:
        if token not in VALID_GRADES:
            raise SystemExit(
                f"load_grade_ruling: unrecognized grade token {token!r} in"
                f" default_grades {cell!r} for worksheet row {raw_value!r}")
    return tokens


def blank_to_none(cell):
    """NaN / empty / whitespace -> None, otherwise the stripped string."""
    if cell is None or pd.isna(cell) or not str(cell).strip():
        return None
    return str(cell).strip()


def parse_review_flag(raw_value, cell):
    """needs_writer_review: blank -> None, else strict true/false -> 1/0."""
    if blank_to_none(cell) is None:
        return None
    return 1 if parse_is_leaf(raw_value, cell) else 0


def derive_kinds(entry: dict) -> list[tuple[str, str, str]]:
    """
    (grade, kind, basis) per node_grade grade of a ruled entry. Only the
    deterministic cases; anything else yields [] (no rows, never a guess).

      single                      -> 'core'
      span                        -> 'span' for every grade
      state_conditional,
      range_prose, alternative,
      unparsed                    -> 'unconfirmed' for every grade, unless the
                                     worksheet carries ccss_default_grades:
                                     those grades 'core', the rest of
                                     default_grades 'state_extension'
      leaf, out_of_band, blank    -> nothing
    """
    rtype = entry.get("ruling_type")
    grades = entry["default_grades"]
    if rtype == "single":
        return [(g, "core", "ruling_type=single") for g in grades]
    if rtype == "span":
        return [(g, "span", "ruling_type=span") for g in grades]
    if rtype in OPEN_TYPES:
        ccss = entry.get("ccss_default_grades")
        if ccss:
            return [(g, "core" if g in ccss else "state_extension",
                     "worksheet ccss_default_grades") for g in grades]
        return [(g, "unconfirmed", f"ruling_type={rtype}") for g in grades]
    return []


def build_lookup(df: pd.DataFrame) -> dict[str, dict]:
    """
    {canonical_grade_key(raw_value): ruling}.

    Several raw values canonicalize onto the same key -- footnote-anchored
    twins ('G1[^c7]' / 'G1'), and case/spelling variants ('PK, GK' / 'PreK,
    GK'). Those rows must agree on what they rule; disagreement is a hard
    error naming both raw values, not a silent pick of one.
    """
    lookup: dict[str, dict] = {}
    for row in df.itertuples(index=False):
        raw = row.raw_value
        is_leaf = parse_is_leaf(raw, row.is_leaf)
        grades = parse_default_grades(raw, row.default_grades)
        notes = None if pd.isna(row.notes) else str(row.notes)
        ccss_cell = getattr(row, "ccss_default_grades", None)
        ccss = (parse_default_grades(raw, ccss_cell)
                if blank_to_none(ccss_cell) else None)
        if ccss and not set(ccss) <= set(grades):
            raise SystemExit(
                f"load_grade_ruling: ccss_default_grades {ccss} is not a"
                f" subset of default_grades {grades} for worksheet row"
                f" {raw!r}")
        key = canonical_grade_key(raw)

        prev = lookup.get(key)
        if prev is not None:
            if set(prev["default_grades"]) != set(grades) or prev["is_leaf"] != is_leaf:
                raise SystemExit(
                    "load_grade_ruling: worksheet rows "
                    f"{prev['raw_value']!r} and {raw!r} canonicalize to the"
                    f" same key {key!r} but disagree on default_grades or"
                    " is_leaf")
            continue
        lookup[key] = {"raw_value": raw, "default_grades": grades,
                       "is_leaf": is_leaf, "notes": notes,
                       "ruling_type": blank_to_none(
                           getattr(row, "ruling_type", None)),
                       "needs_writer_review": parse_review_flag(
                           raw, getattr(row, "needs_writer_review", None)),
                       "states_mentioned": blank_to_none(
                           getattr(row, "states_mentioned", None)),
                       "ccss_default_grades": ccss}
    return lookup


def load_worksheet(path: Path) -> dict[str, dict]:
    df = pd.read_excel(path, sheet_name=WORKSHEET_SHEET)
    keep = WORKSHEET_COLUMNS + [c for c in OPTIONAL_COLUMNS if c in df.columns]
    df = df[keep]
    return build_lookup(df)


def resolve_nodes(con, lookup: dict[str, dict]) -> tuple[Counter, int]:
    """
    One node_grade_ruling row per node, zero or more node_grade rows.

    Idempotent: both tables are cleared before inserting, so a re-run with a
    revised worksheet replaces the ruling rather than layering on top of it.
    """
    con.execute("DELETE FROM node_grade_kind")
    con.execute("DELETE FROM node_grade")
    con.execute("DELETE FROM node_grade_ruling")

    counts: Counter = Counter()
    ruling_rows = []
    grade_rows = []
    kind_rows = []

    for node_id, raw in con.execute("SELECT node_id, grade_or_leaf FROM nodes"):
        if raw is None or not str(raw).strip():
            counts["no_grade_field"] += 1
            ruling_rows.append((node_id, raw, None, "no_grade_field", 0, None,
                                None, None, None))
            continue

        canon = canonical_grade_key(raw)
        entry = lookup.get(canon)
        if entry is None:
            counts["unresolved"] += 1
            ruling_rows.append((node_id, raw, canon, "unresolved", 0, None,
                                None, None, None))
            continue

        resolution = "leaf" if entry["is_leaf"] else "ruled"
        counts[resolution] += 1
        ruling_rows.append((node_id, raw, canon, resolution,
                            int(entry["is_leaf"]), entry["notes"],
                            entry.get("ruling_type"),
                            entry.get("needs_writer_review"),
                            entry.get("states_mentioned")))
        for grade in entry["default_grades"]:
            grade_rows.append((node_id, grade))
        for grade, kind, basis in derive_kinds(entry):
            kind_rows.append((node_id, grade, kind, basis))

    con.executemany(
        "INSERT INTO node_grade_ruling"
        " (node_id, raw_value, canon_key, resolution, is_leaf, notes,"
        " ruling_type, needs_writer_review, states_mentioned)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", ruling_rows)
    con.executemany(
        "INSERT INTO node_grade (node_id, grade) VALUES (?, ?)", grade_rows)
    con.executemany(
        "INSERT INTO node_grade_kind (node_id, grade, kind, basis)"
        " VALUES (?, ?, ?, ?)", kind_rows)
    con.commit()
    return counts, len(grade_rows)


def report(counts: Counter, node_grade_rows: int) -> None:
    total = sum(counts.get(r, 0) for r in RESOLUTIONS)
    print(f"node_grade_ruling: {total} rows")
    for resolution in RESOLUTIONS:
        print(f"  {resolution:<15s} {counts.get(resolution, 0)}")
    print(f"node_grade rows: {node_grade_rows}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB))
    ap.add_argument("--worksheet", default=str(config.GRADE_WORKSHEET))
    args = ap.parse_args()

    path = Path(args.worksheet)
    if not path.exists():
        sys.exit(f"No grade ruling worksheet: {path}")

    con = sqlite3.connect(args.db)
    lookup = load_worksheet(path)
    seed_grade_order(con)
    counts, node_grade_rows = resolve_nodes(con, lookup)
    con.close()

    report(counts, node_grade_rows)


if __name__ == "__main__":
    main()
