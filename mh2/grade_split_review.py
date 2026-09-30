"""
grade_split_review.py -- write the grade-split review worksheet for John.

    python -m mh2.grade_split_review --db data/build/mh2.db

One row per worksheet raw_value whose ruling_type leaves the CCSS-default
grade open (state_conditional, range_prose, alternative, unparsed). For each
it PROPOSES which grades are the CCSS default and which are state extensions,
parsed from the `notes` text, and says how sure it is. John fills in
`john_ruling`; the ruling then goes back into the grade worksheet as a
`ccss_default_grades` column, which mh2.load_grade_ruling picks up.

Proposals are never loaded anywhere. This file is a question, not an answer.
"""

import argparse
import csv
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

from mh2.load_grade_ruling import OPEN_TYPES, load_worksheet  # noqa: E402
from mh2.normalize import canonical_grade_key  # noqa: E402

COLUMNS = ["raw_value", "ruling_type", "node_count", "current_default_grades",
           "proposed_ccss_default_grades", "proposed_state_extension_grades",
           "confidence", "basis", "notes", "john_ruling"]

# A grade token in prose: '3', 'G3', 'K', 'A1'. The lookbehind stops the K of
# 'OK' and the G of 'FL' from matching.
GRADE_TOKEN = re.compile(r"(?<![A-Za-z0-9])G?(A1|PK|K|\d+)(?![A-Za-z0-9])")
STATE_TOKEN = re.compile(r"\b[A-Z]{2}\b")


def propose(entry: dict) -> tuple[list[str], dict, str, str]:
    """
    (ccss_default, {states: grades}, confidence, basis) for one worksheet row.

    state_conditional with a parseable note: the grades the note names are the
    extension, the rest of default_grades is the CCSS default -> 'parsed'.
    Everything else: the whole default_grades as CCSS default, no extension ->
    'guess'.
    """
    grades = entry["default_grades"]
    notes = entry["notes"] or ""
    if entry["ruling_type"] == "state_conditional" and notes:
        ext = []
        for tok in GRADE_TOKEN.findall(notes):
            if tok in grades and tok not in ext:
                ext.append(tok)
        default = [g for g in grades if g not in ext]
        if ext and default:
            states = STATE_TOKEN.findall(notes) or (
                ["some states"] if "some states" in notes else [])
            if not states:
                states = ["unspecified"]
            ext_by_state = {s: ", ".join(ext) for s in states}
            return default, ext_by_state, "parsed", f"notes: {notes}"
    return list(grades), {}, "guess", "whole default_grades taken as core"


def build_rows(lookup: dict, node_counts: dict) -> list[dict]:
    rows = []
    for key, entry in lookup.items():
        if entry["ruling_type"] not in OPEN_TYPES:
            continue
        default, ext, conf, basis = propose(entry)
        rows.append({
            "raw_value": entry["raw_value"],
            "ruling_type": entry["ruling_type"],
            "node_count": node_counts.get(key, 0),
            "current_default_grades": ", ".join(entry["default_grades"]),
            "proposed_ccss_default_grades": ", ".join(default),
            "proposed_state_extension_grades":
                json.dumps(ext) if ext else "",
            "confidence": conf,
            "basis": basis,
            "notes": entry["notes"] or "",
            "john_ruling": "",
        })
    rows.sort(key=lambda r: (r["ruling_type"], -r["node_count"], r["raw_value"]))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB))
    ap.add_argument("--worksheet", default=str(config.GRADE_WORKSHEET))
    ap.add_argument("--out", default=str(config.REPORTS / "grade_split_review.csv"))
    args = ap.parse_args()

    lookup = load_worksheet(Path(args.worksheet))
    con = sqlite3.connect(args.db)
    node_counts = dict(con.execute(
        "SELECT canon_key, COUNT(*) FROM node_grade_ruling"
        " WHERE canon_key IS NOT NULL GROUP BY canon_key"))
    con.close()

    rows = build_rows(lookup, node_counts)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()
