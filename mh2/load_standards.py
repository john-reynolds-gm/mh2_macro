"""
load_standards.py — populate `standards`, `crosswalk`, `concepts`,
`concept_standards`, and seed `leaves` from the gaps sheet.

Sources, in priority order for standard TEXT (first writer wins):
  1. the tagging workbook             — CCSS, California-All, Florida,
     Texas sheets. Authoritative for the big three + CCSS canon.
  2. Other State Standards Gaps sheet — 37 states, hand-curated gaps.
  3. all_states.csv                   — 36 states, broad but no CA/TX.

Filenames come from config.py — do not hardcode them here.

Run: python -m mh2.load_standards --db mh2.db
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

from mh2.normalize import (
    jurisdiction_of, grade_of, normalize_code, normalize_grade,
    parse_code_cell,
)

BIG_THREE = {"CA", "TX", "FL"}

# sheet name -> (jurisdiction hint, code column, text column, grade column)
TAGGING_SHEETS = {
    "CCSS":                ("CCSS", "Number", "Description", "Grade"),
    "California-All":      ("CA",   "Number", "Description", "Grade"),
    "California-not CCSS": ("CA",   "Number", "Description", "Grade"),
    "Florida":             ("FL",   "Number", "Description", "Grade"),
    "Texas":               ("TX",   "Number", "Description", "Grade"),
}

STEM_SHEETS = [
    "Measurement and Data",
    "Number Systems and Structures",
    "Operations and Equations",
    "Shapes and Space",
    "Structure Pattern and Reasoning",
]

BUCKET_COLS = {
    "CCSSM Standard":     "ccssm",
    "Big Three Standard": "big_three",
    "Big Three":          "big_three",
    "Other Standard":     "other",
}


def _upsert_standard(cur, code, grade, text, source, seen):
    """Insert a standard. First source to supply text wins; later ones only fill blanks."""
    code = normalize_code(code)
    if not code:
        return
    juris = jurisdiction_of(code)
    grade = normalize_grade(grade) or grade_of(code)
    if code in seen:
        if text:
            cur.execute(
                "UPDATE standards SET text = COALESCE(NULLIF(text,''), ?) WHERE standard_id = ?",
                (text, code),
            )
        return
    cur.execute(
        "INSERT INTO standards (standard_id, jurisdiction, grade, text, is_ccss,"
        " is_big_three, source) VALUES (?,?,?,?,?,?,?)",
        (code, juris, grade, text, 1 if juris == "CCSS" else 0,
         1 if juris in BIG_THREE else 0, source),
    )
    seen.add(code)


def load_tagging_workbook(cur, path: Path, seen: set) -> dict:
    """CCSS / CA / FL / TX sheets -> standards. Returns per-sheet counts."""
    counts = {}
    xl = pd.ExcelFile(path)
    for sheet, (_juris, code_col, text_col, grade_col) in TAGGING_SHEETS.items():
        if sheet not in xl.sheet_names:
            continue
        df = pd.read_excel(path, sheet_name=sheet).dropna(how="all")
        df.columns = [str(c).strip() for c in df.columns]
        n = 0
        for _, row in df.iterrows():
            code = row.get(code_col)
            if pd.isna(code):
                continue
            text = row.get(text_col)
            _upsert_standard(
                cur, str(code),
                row.get(grade_col),
                None if pd.isna(text) else str(text).strip(),
                f"tagging:{sheet}", seen,
            )
            n += 1
        counts[sheet] = n
    return counts


def load_gaps_sheet(cur, path: Path, seen: set) -> int:
    """
    'Other State Standards Gaps' — 1,493 hand-curated rows across 37 states.
    Loaded as standards AND seeded into `leaves`, because that is what they are:
    a hand-built leaf catalogue with a category taxonomy already attached.
    """
    df = pd.read_excel(path, sheet_name="Other State Standards Gaps").dropna(how="all")
    df.columns = [str(c).strip() for c in df.columns]
    n = 0
    for _, row in df.iterrows():
        code = row.get("Standard Code")
        if pd.isna(code):
            continue
        text = row.get("Standard Text")
        _upsert_standard(
            cur, str(code), row.get("Grade"),
            None if pd.isna(text) else str(text).strip(),
            "tagging:gaps", seen,
        )
        cat = row.get("Category (new list)")
        # Category strings are case-inconsistent in the source
        # ("Geometry: Shape attributes" vs "...shape attributes"). Fold case
        # on the part after the colon so they collapse to one taxonomy entry.
        if isinstance(cat, str) and ":" in cat:
            head, tail = cat.split(":", 1)
            cat = f"{head.strip()}: {tail.strip().lower()}"
        elif isinstance(cat, str):
            cat = cat.strip()
        tagged = str(row.get("Tagged to a Stem", "")).strip().lower() == "yes"
        cur.execute(
            "INSERT INTO leaves (jurisdiction, standard_id, category, description,"
            " origin, status) VALUES (?,?,?,?,?,?)",
            (jurisdiction_of(normalize_code(str(code))), normalize_code(str(code)),
             cat if isinstance(cat, str) else None,
             None if pd.isna(row.get("EM2 Alignment Notes")) else str(row.get("EM2 Alignment Notes")),
             "gaps_sheet", "covered" if tagged else "proposed"),
        )
        n += 1
    return n


def load_all_states(cur, path: Path, seen: set) -> int:
    """all_states.csv — broad state coverage. Standard rows only, deduped."""
    df = pd.read_csv(path, dtype=str, usecols=["state", "grade", "level", "standard_id", "standard_text"])
    df = df[df["standard_id"].notna() & (df["standard_id"].str.strip() != "")]
    df = df.drop_duplicates(subset=["state", "standard_id"])
    n = 0
    for _, row in df.iterrows():
        raw = str(row["standard_id"]).strip()
        # all_states.csv stores bare codes; prefix with the state when absent.
        code = raw if raw.split(".")[0].upper() == row["state"].upper()[:2] else f"{row['state'][:2]}.{raw}"
        _upsert_standard(cur, code, row.get("grade"),
                         (row.get("standard_text") or "").strip() or None,
                         "all_states.csv", seen)
        n += 1
    return n


# The crosswalk loader used to live here and wrote a different shape
# (state_standard_id / ccss_id). It moved to mh2/load_layer1.py along with the
# rest of layer 1, and the table now carries `state` and `state_code` as
# separate columns per §6. One writer per table.


def load_stem_inventory(cur, path: Path) -> tuple[int, int, list[str]]:
    """
    H2_Stem_and_Leaf_Spreadsheet -> concepts + concept_standards.

    This workbook is the team's existing hand-tagging surface: Stem ->
    Concept/Skill -> {CCSSM, Big Three, Other} standard codes, with inline
    provenance notes. It is the seed for node_standards.
    """
    n_concepts = n_tags = 0
    unparsed_all: list[str] = []
    stem_codes: dict[str, str] = {}

    for sheet in STEM_SHEETS:
        try:
            df = pd.read_excel(path, sheet_name=sheet, header=0)
        except ValueError:
            continue
        df.columns = [str(c).strip() for c in df.columns]
        concept_col = next(
            (c for c in df.columns if c.lower().replace("s", "") == "concept/kill"
             or c.lower().startswith("concept/skill")), None)
        if concept_col is None:
            continue

        for _, row in df.iterrows():
            concept = row.get(concept_col)
            if pd.isna(concept) or not str(concept).strip():
                continue
            stem_name = str(row.get("Stem") or "").strip() or "(unassigned)"
            code = stem_codes.get(stem_name)
            if code is None:
                base = re.sub(r"[^A-Z]", "", stem_name.upper())[:3] or "GEN"
                code = base
                i = 1
                while code in stem_codes.values():
                    i += 1
                    code = f"{base}{i}"
                stem_codes[stem_name] = code
                cur.execute(
                    "INSERT OR IGNORE INTO stems (stem_id, name, domain) VALUES (?,?,?)",
                    (code, stem_name, sheet))

            cur.execute(
                "INSERT INTO concepts (stem_id, stem_name, text, details, grade_leaf,"
                " source_sheet) VALUES (?,?,?,?,?,?)",
                (code, stem_name, str(concept).strip(),
                 None if pd.isna(row.get("Details")) else str(row.get("Details")).strip(),
                 None if pd.isna(row.get("Grade/Leaf")) else str(row.get("Grade/Leaf")).strip(),
                 sheet))
            concept_id = cur.lastrowid
            n_concepts += 1

            for col, bucket in BUCKET_COLS.items():
                if col not in df.columns:
                    continue
                pairs, unparsed = parse_code_cell(row.get(col))
                unparsed_all.extend(unparsed)
                for scode, annot in pairs:
                    cur.execute(
                        "INSERT OR IGNORE INTO concept_standards (concept_id,"
                        " standard_id, bucket, annotation) VALUES (?,?,?,?)",
                        (concept_id, scode, bucket, annot))
                    n_tags += 1
    return n_concepts, n_tags, unparsed_all


def _find(root: Path, expected: Path) -> Path:
    """Resolve a source file declared in config.py.

    Uses the configured location first, then falls back to searching for that
    same filename anywhere under root, so data/source/<subdir>/ reshuffles and
    an overridden --project root both keep working.
    """
    if expected.exists():
        return expected
    matches = sorted(root.rglob(expected.name))
    if not matches:
        raise SystemExit(
            f"Source file not found at {expected} or anywhere under {root}: "
            f"{expected.name}  (declared in config.py)")
    return matches[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB))
    ap.add_argument("--project", default=str(config.SOURCE),
                    help="root to search when a configured file has moved")
    args = ap.parse_args()

    proj = Path(args.project)
    con = sqlite3.connect(args.db)
    cur = con.cursor()
    seen: set = set()

    tagging = _find(proj, config.TAGGING_WORKBOOK)
    stemwb = _find(proj, config.STEM_WORKBOOK)

    print("standards from tagging workbook:", load_tagging_workbook(cur, tagging, seen))
    print("gaps sheet rows:", load_gaps_sheet(cur, tagging, seen))
    print("all_states.csv rows:",
          load_all_states(cur, _find(proj, config.ALL_STATES_CSV), seen))
    nc, nt, unparsed = load_stem_inventory(cur, stemwb)
    print(f"concepts: {nc}   concept->standard tags: {nt}   unparsed cell lines: {len(unparsed)}")
    if unparsed:
        report = Path(args.db).parent.parent / "reports" / "unparsed_codes.txt"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(sorted(set(unparsed))), encoding="utf-8")
        print(f"  -> wrote {report} for review")

    con.commit()
    con.close()


if __name__ == "__main__":
    main()
