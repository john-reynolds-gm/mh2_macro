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
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

from mh2.load_stems import normalize_stem_text, read_stems_csv
from mh2.normalize import (
    alias_key, jurisdiction_of, grade_of, normalize_code, normalize_grade,
    parse_code_cell, strip_trailing_prose,
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

STEM_SHEETS_G6 = [
    "Expressions and Equations",
    "Functions and Relationships",
    "Geometry and Measurement",
    "Ratios and Proportional Relatio",   # truncated by Excel's 31-char limit
    "Statistics and Probability",
    "Structure of the Number System",
]

BUCKET_COLS = {
    "CCSSM Standard":     "ccssm",
    "Big Three Standard": "big_three",
    "Big Three":          "big_three",
    "Other Standard":     "other",
}

# The coverage audit's denominator: standards sourced from a workbook tab or
# the gaps sheet, excluding California-All. all_states.csv rows are OUT --
# they appear in no coverage tab and are never a coverage subject. Used to
# scope standard_alias construction in build_standard_alias() below.
DENOMINATOR_SOURCE_SQL = "source LIKE 'tagging:%' AND source <> 'tagging:California-All'"


def _upsert_standard(cur, code, grade, text, source, seen, unrecognized_grades=None):
    """Insert a standard. First source to supply text wins; later ones only fill blanks."""
    code = normalize_code(code)
    if not code:
        return
    juris = jurisdiction_of(code)
    norm_grade = normalize_grade(grade)
    if (norm_grade is None and unrecognized_grades is not None
            and grade is not None and str(grade).strip()
            and str(grade).strip().lower() != "nan"):
        # normalize_grade didn't recognize the cell -- grade_of(code) below is
        # a best-effort fallback, not a correction, so the raw cell is kept
        # here as a review-queue item instead of silently vanishing.
        unrecognized_grades.append((str(grade).strip(), code))
    grade = norm_grade or grade_of(code)
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


def load_tagging_workbook(cur, path: Path, seen: set, unrecognized_grades=None) -> dict:
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
                f"tagging:{sheet}", seen, unrecognized_grades,
            )
            n += 1
        counts[sheet] = n
    return counts


def load_gaps_sheet(cur, path: Path, seen: set, unrecognized_grades=None) -> int:
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
        code = strip_trailing_prose(str(code))
        text = row.get("Standard Text")
        _upsert_standard(
            cur, code, row.get("Grade"),
            None if pd.isna(text) else str(text).strip(),
            "tagging:gaps", seen, unrecognized_grades,
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


def load_all_states(cur, path: Path, seen: set, unrecognized_grades=None) -> int:
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
                         "all_states.csv", seen, unrecognized_grades)
        n += 1
    return n


def _alias_key_groups(cur) -> dict[str, dict[str, set]]:
    """
    {tier: {alias_key: {standard_id, ...}}} over the same scoping rule
    build_standard_alias() and find_standard_alias_collisions() both need:
    DENOMINATOR_SOURCE_SQL for 'punct', that set further restricted to CCSS
    for 'nocluster' -- see build_standard_alias()'s docstring for why.
    """
    denom_ids = [r[0] for r in cur.execute(
        f"SELECT standard_id FROM standards WHERE {DENOMINATOR_SOURCE_SQL}")]
    ccss_ids = [r[0] for r in cur.execute(
        f"SELECT standard_id FROM standards WHERE {DENOMINATOR_SOURCE_SQL}"
        " AND jurisdiction = 'CCSS'")]
    ids_by_tier = {"punct": denom_ids, "nocluster": ccss_ids}

    groups: dict[str, dict[str, set]] = {}
    for tier in ("punct", "nocluster"):
        by_key: dict[str, set] = {}
        for sid in ids_by_tier[tier]:
            by_key.setdefault(alias_key(sid, tier), set()).add(sid)
        groups[tier] = by_key
    return groups


def find_standard_alias_collisions(cur) -> dict[str, list[tuple[str, list[str]]]]:
    """
    Which (alias_key, tier) pairs are ambiguous -- claimed by more than one
    DISTINCT standard_id -- without inserting anything.

    Safe to call AFTER `standard_alias` is already populated (e.g. from
    rebuild.py's report, once load_standards has already run in a separate
    process): a colliding key was never inserted in the first place, so it
    cannot be recovered by reading the table -- it has to be recomputed the
    same way build_standard_alias() computes it.
    """
    groups = _alias_key_groups(cur)
    return {
        tier: sorted((key, sorted(sids)) for key, sids in by_key.items()
                     if key and len(sids) > 1)
        for tier, by_key in groups.items()
    }


def build_standard_alias(cur) -> tuple[dict[str, int], dict[str, list[tuple[str, list[str]]]]]:
    """
    Populate `standard_alias` from the now-fully-loaded `standards` table.

    Two keys per standard_id ('punct', 'nocluster' -- see alias_key() in
    mh2.normalize). A key that two DISTINCT standard_ids both produce is
    ambiguous: neither is inserted, and both are returned in the collisions
    report instead of picking a winner.

    Construction is scoped to DENOMINATOR_SOURCE_SQL -- the tagging workbook's
    sheets plus the gaps sheet, excluding California-All -- NOT all of
    `standards`. 14,945 of standards' 18,804 rows come from all_states.csv and
    appear in no coverage tab; an alias built from one of them could resolve a
    ladder-written code to a standard the audit never displays, which is a
    correctness hazard, not just noise. The CCSS heading predicate the
    five-tab denominator applies elsewhere is deliberately NOT applied here --
    a ladder may legitimately tag a heading, and alias construction should not
    be coupled to a view-layer predicate.

    'nocluster' is further scoped to CCSS standards only, within that set. It
    targets one specific, verifiable shape -- grade.domain.CLUSTER.number
    [.subpart], the omitted CCSS cluster letter, where the standard number is
    unique within the DOMAIN so the letter is redundant. That is not a safe
    assumption for state jurisdictions: Louisiana's 'AR' domain has FIVE
    different letters (A-E) sharing the same trailing number, i.e. the letter
    there is load-bearing, not redundant -- and collision-checking alone does
    not catch a jurisdiction where the wrong assumption happens not to
    collide. 'punct' carries no structural assumption -- it is pure
    character-class stripping -- and applies to the whole denominator.

    Returns (counts, collisions): counts is {tier: rows_inserted},
    collisions is {tier: [(alias_key, [standard_id, ...]), ...]}.
    """
    groups = _alias_key_groups(cur)

    rows = []
    collisions: dict[str, list[tuple[str, list[str]]]] = {"punct": [], "nocluster": []}
    counts = {"punct": 0, "nocluster": 0}
    for tier, by_key in groups.items():
        for key, sids in by_key.items():
            if not key:
                continue
            if len(sids) > 1:
                collisions[tier].append((key, sorted(sids)))
                continue
            rows.append((key, tier, next(iter(sids))))
            counts[tier] += 1

    cur.executemany(
        "INSERT INTO standard_alias (alias_key, tier, standard_id) VALUES (?,?,?)",
        rows)
    return counts, collisions


# The crosswalk loader used to live here and wrote a different shape
# (state_standard_id / ccss_id). It moved to mh2/load_layer1.py along with the
# rest of layer 1, and the table now carries `state` and `state_code` as
# separate columns per §6. One writer per table.


def _build_g6_stem_lookup() -> dict[str, str]:
    """
    Canonical 6-9 stem IDs already live in stems.csv, keyed by masterlist_name
    and workbook_stem. The G6 workbook resolves against them; it never mints
    its own IDs -- see the module docstring's "resolve, never derive" note.
    """
    lookup: dict[str, str] = {}
    for r in read_stems_csv(config.STEMS_CSV):
        if r["band"] != "6_9":
            continue
        for name in (r["masterlist_name"], r["workbook_stem"]):
            if name and name.strip():
                lookup.setdefault(normalize_stem_text(name), r["stem_id"])
    return lookup


def load_stem_inventory(cur, path: Path, sheets: list[str], band: str) -> tuple[int, int, list[str]]:
    """
    H2_Stem_and_Leaf_Spreadsheet -> concepts + concept_standards.

    This workbook is the team's existing hand-tagging surface: Stem ->
    Concept/Skill -> {CCSSM, Big Three, Other} standard codes, with inline
    provenance notes. It is the seed for node_standards.

    band='PK5' derives a stem_id from the stem name (first three letters,
    numeric suffix on collision). band='6_9' resolves against the canonical
    IDs in stems.csv instead of minting a new namespace -- see the module
    docstring.
    """
    n_concepts = n_tags = 0
    unparsed_all: list[str] = []
    unresolved: list[tuple[str, str]] = []
    stem_codes: dict[str, str] = {}
    g6_lookup = _build_g6_stem_lookup() if band == "6_9" else None

    for sheet in sheets:
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

            if band == "PK5":
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
            else:
                code = g6_lookup.get(normalize_stem_text(stem_name))
                if code is None:
                    unresolved.append((sheet, stem_name))
                    continue
                cur.execute(
                    "INSERT OR IGNORE INTO stems (stem_id, name, domain, band)"
                    " VALUES (?,?,?,?)",
                    (code, stem_name, sheet, "6_9"))

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

    if unresolved:
        distinct = sorted(set(unresolved))
        report = config.REPORTS / "stem_resolution_unmatched.txt"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            "\n".join(f"{sheet}\t{name}" for sheet, name in distinct),
            encoding="utf-8")
        print(f"  ! {len(distinct)} unresolved stem name(s) -- wrote {report}")

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
    unrecognized_grades: list[tuple[str, str]] = []

    tagging = _find(proj, config.TAGGING_WORKBOOK)
    stemwb = _find(proj, config.STEM_WORKBOOK)
    stemwb_g6 = _find(proj, config.STEM_WORKBOOK_G6)

    print("standards from tagging workbook:",
          load_tagging_workbook(cur, tagging, seen, unrecognized_grades))
    print("gaps sheet rows:", load_gaps_sheet(cur, tagging, seen, unrecognized_grades))
    print("all_states.csv rows:",
          load_all_states(cur, _find(proj, config.ALL_STATES_CSV), seen,
                          unrecognized_grades))

    if unrecognized_grades:
        by_raw = Counter(raw for raw, _code in unrecognized_grades)
        print(f"unrecognized grade cells (fell back to grade_of(code)): "
              f"{len(unrecognized_grades)} rows, {len(by_raw)} distinct")
        example = dict(unrecognized_grades)
        for raw, n in by_raw.most_common():
            print(f"    {raw!r:30s} n={n:<4d} e.g. {example[raw]}")

    alias_counts, alias_collisions = build_standard_alias(cur)
    print(f"standard_alias: {alias_counts['punct']} punct, "
          f"{alias_counts['nocluster']} nocluster rows inserted")
    for tier, coll in alias_collisions.items():
        if not coll:
            continue
        print(f"  {tier} collisions (key claimed by >1 standard_id, "
              f"inserted for neither): {len(coll)}")
        for key, sids in coll:
            print(f"    {key!r:20s} -> {sids}")

    nc1, nt1, un1 = load_stem_inventory(cur, stemwb, STEM_SHEETS, "PK5")
    print(f"concepts (PK5): {nc1}   concept->standard tags: {nt1}"
          f"   unparsed cell lines: {len(un1)}")

    nc2, nt2, un2 = load_stem_inventory(cur, stemwb_g6, STEM_SHEETS_G6, "6_9")
    print(f"concepts (6_9): {nc2}   concept->standard tags: {nt2}"
          f"   unparsed cell lines: {len(un2)}")

    unparsed = un1 + un2
    if unparsed:
        report = config.REPORTS / "unparsed_codes.txt"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(sorted(set(unparsed))), encoding="utf-8")
        print(f"  -> wrote {report} for review")

    # §3.4 cross-band guard: PK5 derivation (band left NULL) must never mint a
    # stem_id that the 6_9 path resolves to. Two DISTINCT stems merging under
    # one ID is not recoverable once concepts/concept_standards are written.
    g6_ids = set(_build_g6_stem_lookup().values())
    pk5_written = {r[0] for r in cur.execute("SELECT stem_id FROM stems WHERE band IS NULL")}
    collision = g6_ids & pk5_written
    if collision:
        raise SystemExit(
            f"cross-band stem_id collision (written by both PK5 and 6_9): {sorted(collision)}")

    con.commit()
    con.close()


if __name__ == "__main__":
    main()
