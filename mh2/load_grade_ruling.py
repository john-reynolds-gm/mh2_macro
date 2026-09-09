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

Only four worksheet columns are read: raw_value, default_grades, is_leaf,
notes. Everything else on the sheet (count, n_ladders, suggested_type,
suggested_grades, states_mentioned, ruling_type, state_overrides,
needs_writer_review, examples) is derived, superseded, or a pre-consultation
artifact -- see the brief for why each one is skipped. In particular
`state_overrides` is empty by design: state-specific grade extensions were
folded into `default_grades`, with the prose kept in `notes`.

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
                       "is_leaf": is_leaf, "notes": notes}
    return lookup


def load_worksheet(path: Path) -> dict[str, dict]:
    df = pd.read_excel(path, sheet_name=WORKSHEET_SHEET)[WORKSHEET_COLUMNS]
    return build_lookup(df)


def resolve_nodes(con, lookup: dict[str, dict]) -> tuple[Counter, int]:
    """
    One node_grade_ruling row per node, zero or more node_grade rows.

    Idempotent: both tables are cleared before inserting, so a re-run with a
    revised worksheet replaces the ruling rather than layering on top of it.
    """
    con.execute("DELETE FROM node_grade")
    con.execute("DELETE FROM node_grade_ruling")

    counts: Counter = Counter()
    ruling_rows = []
    grade_rows = []

    for node_id, raw in con.execute("SELECT node_id, grade_or_leaf FROM nodes"):
        if raw is None or not str(raw).strip():
            counts["no_grade_field"] += 1
            ruling_rows.append((node_id, raw, None, "no_grade_field", 0, None))
            continue

        canon = canonical_grade_key(raw)
        entry = lookup.get(canon)
        if entry is None:
            counts["unresolved"] += 1
            ruling_rows.append((node_id, raw, canon, "unresolved", 0, None))
            continue

        resolution = "leaf" if entry["is_leaf"] else "ruled"
        counts[resolution] += 1
        ruling_rows.append((node_id, raw, canon, resolution,
                            int(entry["is_leaf"]), entry["notes"]))
        for grade in entry["default_grades"]:
            grade_rows.append((node_id, grade))

    con.executemany(
        "INSERT INTO node_grade_ruling"
        " (node_id, raw_value, canon_key, resolution, is_leaf, notes)"
        " VALUES (?, ?, ?, ?, ?, ?)", ruling_rows)
    con.executemany(
        "INSERT INTO node_grade (node_id, grade) VALUES (?, ?)", grade_rows)
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
