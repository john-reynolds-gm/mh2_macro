"""
load_stems.py — read stems.csv, the recorded ladder <-> workbook stem mapping.

The mapping is DATA, not something to infer. Three naming surfaces disagree and
none of the disagreements is recoverable from string similarity:

    masterlist              workbook
    Comparing and Ordering  Comparing  +  Ordering     (one stem -> two)
    Subitizations           Subitization
    ...of Fractions         ...(Fractions)

So `masterlist_name` repeats (NSS_COM and NSS_ORD share it) and `ladder_file`
repeats (one Comparing and Ordering ladder, two workbook stems). Any code that
assumes one ladder maps to one stem is wrong.

Filenames are the one thing normalized here. stems.csv writes
`MH2_PK5_Measurement_and_Data_Angles.docx` where the file on disk is
`MH2_PK5_Measurement and Data_Angles.docx`, and `..._Fractions__5_.docx` where
the file is `..._Fractions.docx` — zero of the ten drafted rows join by exact
filename. resolve_ladder_files() normalizes both sides and FAILS LOUDLY if a
drafted row does not land on exactly one file, because a silent miss here
corrupts every comparison downstream.

Run: python -m mh2.load_stems --db mh2.db
"""

import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

# stem_id is an OPAQUE key. The slugs are provisional and may be replaced with
# the team's own stem codes, so nothing may hardcode or pattern-match them.
FIELDS = ("stem_id", "band", "stem_group", "masterlist_name",
          "workbook_sheet", "workbook_stem", "ladder_file", "ladder_drafted")

# Trailing download-duplicate suffix: 'Fractions__5_.docx', 'Fractions (5).docx'.
_DUP_SUFFIX_RE = re.compile(r"(?:__\d+_|\s*\(\d+\))(?=\.docx$|$)", re.I)


def normalize_stem_text(text: str) -> str:
    """
    Fold a stem name for comparison only — never for storage.

    The workbook writes 'Whole Numbers and  Base Ten Structure' with a double
    space and 'Whole Numbers and \\nBase Ten Structure' with an embedded newline;
    the masterlist has 'Identify,  Define, and Classify...' with another double
    space. Stored values keep their original whitespace.
    """
    return re.sub(r"\s+", " ", (text or "").replace(" ", " ")).strip().lower()


def normalize_ladder_filename(name: str) -> str:
    """
    Fold a ladder filename so stems.csv and the directory listing agree.

    stems.csv uses underscores where the real filenames use spaces, and carries
    a '__5_' browser-download suffix on the Fractions row. Underscores, spaces
    and case are all folded; the duplicate suffix is dropped.
    """
    stem = _DUP_SUFFIX_RE.sub("", (name or "").strip())
    stem = re.sub(r"\.docx$", "", stem, flags=re.I)
    stem = re.sub(r"[_\s]+", " ", stem)
    return stem.strip().lower()


def ladder_paths(ladder_dir: Path) -> list[Path]:
    """
    The .docx ladders in a directory, excluding Word's lock files.

    Having a ladder open in Word leaves a '~$MH2_...docx' owner file next to it.
    It is not a real document and python-docx raises PackageNotFoundError on it,
    so a plain *.docx glob crashes the build whenever somebody has a ladder open.
    """
    return sorted(p for p in ladder_dir.glob("*.docx")
                  if not p.name.startswith("~$"))


def read_stems_csv(path: Path) -> list[dict]:
    """Read stems.csv into a list of dicts, one per row, values verbatim."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    missing = [f for f in FIELDS if rows and f not in rows[0]]
    if missing:
        raise SystemExit(f"{path.name} is missing columns: {missing}")
    for r in rows:
        r["ladder_drafted"] = int(str(r.get("ladder_drafted") or 0).strip() or 0)
    return rows


def resolve_ladder_files(rows: list[dict], ladder_dir: Path) -> dict[str, Path]:
    """
    Map each drafted row's stem_id to the .docx on disk.

    Raises on any drafted row that does not resolve to exactly one file. A
    warning would be worse than useless: the whole point of stems.csv is that
    the mapping is reviewed rather than guessed, so a miss must stop the build.
    """
    on_disk: dict[str, list[Path]] = {}
    for p in ladder_paths(ladder_dir):
        on_disk.setdefault(normalize_ladder_filename(p.name), []).append(p)

    resolved: dict[str, Path] = {}
    problems: list[str] = []
    for r in rows:
        if not r["ladder_drafted"]:
            continue
        declared = (r.get("ladder_file") or "").strip()
        if not declared:
            problems.append(f"{r['stem_id']}: ladder_drafted=1 but ladder_file is blank")
            continue
        matches = on_disk.get(normalize_ladder_filename(declared), [])
        if len(matches) != 1:
            problems.append(
                f"{r['stem_id']}: {declared!r} matched {len(matches)} files on disk")
            continue
        resolved[r["stem_id"]] = matches[0]

    if problems:
        raise SystemExit("stems.csv could not be reconciled with "
                         f"{ladder_dir}:\n  " + "\n  ".join(problems))
    return resolved


def load(cur, csv_path: Path, ladder_dir: Path) -> dict:
    """
    Load stems.csv into stem_map, resolving both ends of the mapping.

    `workbook_stem` is matched to the stems already loaded from the workbook on
    normalized text, because the two disagree on whitespace: the workbook has
    'Whole Numbers and  Base Ten Structure' with a double space and an embedded
    newline. Stored values stay verbatim; only the comparison folds.
    """
    rows = read_stems_csv(csv_path)
    resolved = resolve_ladder_files(rows, ladder_dir)

    workbook_ids = {}
    for stem_id, name in cur.execute("SELECT stem_id, name FROM stems"):
        workbook_ids[normalize_stem_text(name)] = stem_id

    stats = {"rows": 0, "drafted": 0, "workbook_matched": 0, "unmatched": []}
    for r in rows:
        wb_id = workbook_ids.get(normalize_stem_text(r["workbook_stem"]))
        if r["workbook_stem"] and wb_id is None:
            stats["unmatched"].append(f"{r['stem_id']}: {r['workbook_stem']!r}")
        if wb_id:
            stats["workbook_matched"] += 1
        path = resolved.get(r["stem_id"])
        cur.execute(
            "INSERT OR REPLACE INTO stem_map (stem_id, band, stem_group,"
            " masterlist_name, workbook_sheet, workbook_stem, ladder_file,"
            " ladder_drafted, workbook_stem_id, ladder_path)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r["stem_id"], r["band"], r["stem_group"], r["masterlist_name"],
             r["workbook_sheet"], r["workbook_stem"], r["ladder_file"],
             r["ladder_drafted"], wb_id, str(path) if path else None))
        stats["rows"] += 1
        stats["drafted"] += r["ladder_drafted"]

    # §7.1 states the shape of this file. If it stops holding, the file changed
    # and every downstream comparison should be re-checked before trusting it.
    pk5 = sum(1 for r in rows if r["band"] == "PK5")
    band_6_9 = sum(1 for r in rows if r["band"] == "6_9")
    stats["pk5"] = pk5
    stats["band_6_9"] = band_6_9
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB))
    ap.add_argument("--csv", default=str(config.STEMS_CSV))
    ap.add_argument("--ladders", default=str(config.LADDERS))
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    stats = load(cur, Path(args.csv), Path(args.ladders))
    con.commit()
    con.close()

    print(f"stem_map rows:        {stats['rows']}"
          f"  (PK5 {stats['pk5']}, 6_9 {stats['band_6_9']})")
    print(f"ladders drafted:      {stats['drafted']}")
    print(f"workbook stems bound: {stats['workbook_matched']}")
    for u in stats["unmatched"]:
        print(f"  ! no workbook stem for {u}")


if __name__ == "__main__":
    main()
