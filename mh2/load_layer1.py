"""
load_layer1.py — the lookup tables the generator reads: standard_lessons,
crosswalk, standard_tag_status.

Layer 1 means regenerated on every rebuild and containing no human decisions.
Nothing here is a judgement about whether a tag is right; these are the raw
correspondences the four paths in §3 are built from.

  standard_lessons     Path A. Which lessons a standard is tagged to, CCSS side
                       and state side, so overlap is a self-join. `source` is in
                       its primary key: the same pair from the guide and from
                       lesson_metadata is two rows, and that agreement is real
                       corroboration between two independently built files.
  crosswalk            Path B edges, from learnosity ONLY.
  alignment_audit      scored_alignments' verdicts. Their subject is an
                       all_states tag, so this is a PATH A flag.
  model_suggestions    where the audit proposed a different CCSS code.
  standard_tag_status  the tagging workbook's own work queue.

**Path B is one source, not two.** Rev 3 unioned learnosity and
scored_alignments into `crosswalk`, which recorded one claim as two rows and let
a row-counting boost treat an audit of a claim as independent corroboration of
it. The union is gone.

**And the audit is not about Path B at all.** Rev 4 assumed scored_alignments
audits the learnosity tags. Measured, its anchor codes overlap all_states at 84%
and learnosity at 3.9% — and that 3.9% is entirely Massachusetts, the one state
whose own prefix collides with the item bank's 'MA' for Mathematics. It audits
Path A's state side. See load_audit().

Either way the independence conclusion holds and is if anything stronger: the
audit was produced by running lesson overlap plus the Path C model, so it can
never corroborate Path A or Path C (§4). Use it to demote, never to promote.

Run: python -m mh2.load_layer1 --db mh2.db
"""

import argparse
import csv
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

import config  # noqa: E402

from mh2.normalize import expand_ranges, normalize_code  # noqa: E402

# Rows are inserted in batches; the state CSVs are large (all_states.csv is
# 31 MB) and row-at-a-time inserts dominate the rebuild otherwise.
CHUNK = 100_000

# 'CCSS Math Standard: A.APR.A.1/Curriculum: Eureka Math 2 UTE'.
#
# targetTags carries four granularities and only two of them are standards:
#
#   CCSS Math Standard        12,304   parent standards
#   CCSS Math Child Standard   4,084   sub-standards: 3.MD.C.5.a, 1.NBT.B.2.b
#   CCSS Math Cluster          4,433   NOT a standard
#   CCSS Math Domain           2,259   NOT a standard
#
# §3 names only the first, but 'Child Standard' rows are real standards and
# 1,442 of them are K-5 -- exactly the sub-standard codes the ladder authors
# write and exactly what §3's own range notation expands to. Dropping them
# throws away in-scope evidence. Cluster and Domain rows stay out: they are
# coarser objects, not standards.
#
# The two patterns are disjoint: 'CCSS Math Child Standard:' does not contain
# the substring 'CCSS Math Standard:'.
LEARNOSITY_CCSS_RE = re.compile(
    r"CCSS Math (?:Child )?Standard:\s*([^/]+)")

TAG_STATUS_SHEETS = [
    # Loaded in this order. 'California-not CCSS' comes AFTER 'California-All'
    # because its 17 codes also appear there, and the not-CCSS sheet is the more
    # specific claim — Path 0 excludes exactly those 17 from code inheritance.
    ("CCSS", "Notes from standards tagging step", "Notes from post-ladder check step"),
    ("Florida", "Notes from standards tagging step", "Notes from post-ladder check step"),
    ("Texas", "Notes from standards tagging step", "Notes from post-ladder check step"),
    ("California-All", "Notes", None),
    ("California-not CCSS", "Notes from standards tagging step",
     "Notes from post-ladder check step"),
]


def _flush(cur, sql: str, rows: list) -> int:
    if not rows:
        return 0
    cur.executemany(sql, rows)
    n = len(rows)
    rows.clear()
    return n


# ------------------------------------------------------------ standard_lessons

SQL_LESSON = ("INSERT OR IGNORE INTO standard_lessons (standard_code, lesson_id,"
              " source) VALUES (?,?,?)")


def qualify(state: str, code: str) -> str:
    """
    Give an all_states code the jurisdiction prefix the rest of the project uses.

    all_states.csv stores BARE codes: Arizona's '1.G.A.3' and the CCSS
    '1.G.A.3' are byte-identical in that file. Storing them unqualified makes
    Path A join Arizona's lessons to CCSS's as though they were one standard,
    which is silently, catastrophically wrong — the overlap score would be
    perfect for pairs that share nothing.

    Two states (KY, NC) already carry their own prefix and are left alone. Six
    states use a framework prefix instead of a grade — FL writes 'MA.6.AR.1.1',
    WI and WV write 'M.…', ME 'QR.…', NY 'NY-6.…', PA 'CC.…'. Those are NOT
    rewritten here: no transform for them could be checked against a real hand
    tag, and inventing one produces codes that look joinable and are not. They
    are counted and reported instead.
    """
    state = (state or "").strip().upper()[:2]
    code = normalize_code(str(code))
    if not state or not code:
        return code
    if code.split(".")[0].upper() == state:
        return code
    return f"{state}.{code}"


def load_lessons_from_ref_csv(cur, path: Path, source: str,
                              qualify_state: bool) -> int:
    """
    CCSS_alignment_guide.csv and all_states.csv share an 11-column schema, so
    one reader covers both — that shared schema is what makes Path A a
    self-join rather than a fuzzy match.

    `qualify_state` is the one difference: the guide is entirely CCSS and its
    codes are already canonical, while all_states needs qualify() above.
    """
    total, batch = 0, []
    cols = ["standard_id", "lesson_id", "ref_type"]
    if qualify_state:
        cols.append("state")
    for chunk in pd.read_csv(path, dtype=str, chunksize=CHUNK, usecols=cols):
        chunk = chunk[(chunk["ref_type"] == "lesson")
                      & chunk["standard_id"].notna()
                      & chunk["lesson_id"].notna()]
        states = chunk["state"] if qualify_state else [None] * len(chunk)
        for state, code, lesson in zip(states, chunk["standard_id"],
                                       chunk["lesson_id"]):
            code = qualify(state, code) if qualify_state else normalize_code(str(code))
            lesson = str(lesson).strip()
            if code and lesson:
                batch.append((code, lesson, source))
        total += _flush(cur, SQL_LESSON, batch)
    return total


def load_lessons_from_metadata(cur, path: Path) -> int:
    """
    lesson_metadata.content_standards_list is a JSON array of codes.

    Range notation is expanded FIRST — '3.MD.C.5.a–b', '1.NBT.B.2.a-b'. Left
    alone, each range is one code that matches nothing on the other side of the
    join, so the lessons behind it silently contribute no overlap at all.
    """
    total, batch = 0, []
    df = pd.read_csv(path, dtype=str, usecols=["lesson_id", "content_standards_list"])
    for lesson, raw in zip(df["lesson_id"], df["content_standards_list"]):
        if not isinstance(raw, str) or not isinstance(lesson, str):
            continue
        try:
            listed = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for item in listed:
            for code in expand_ranges(normalize_code(str(item))):
                if code:
                    batch.append((code, lesson.strip(), "metadata"))
        if len(batch) >= CHUNK:
            total += _flush(cur, SQL_LESSON, batch)
    total += _flush(cur, SQL_LESSON, batch)
    return total


# ------------------------------------------------------------------ crosswalk

SQL_CROSSWALK = ("INSERT OR IGNORE INTO crosswalk (state, state_code, ccss_code,"
                 " source_code, granularity) VALUES (?,?,?,?,?)")

_GRADE_SEGMENT_RE = re.compile(r"^(PK|K|1[0-2]|[1-9])$", re.I)


def shorten_learnosity_code(code: str) -> str:
    """
    Strip the framework segments learnosity inserts between state and grade.

        IN.2023.MATH.1.NS.1  ->  IN.1.NS.1
        MO.LS.MA.1.GM.A.1    ->  MO.1.GM.A.1
        AK.CS.MA.1.G.1       ->  AK.1.G.1

    Learnosity stores item-bank tag paths; every other surface in this project
    — the ladders, the workbook, all_states, scored_alignments — uses each
    state's short standard code. Left alone the two vocabularies share NOTHING:
    learnosity intersected the ladders' 424 hand-tagged codes at exactly zero,
    so Path B was structurally silent for every state.

    Verified rather than assumed: shortening makes 6,210 of 9,842 codes match
    `all_states`, a file that had no part in producing them, and takes the
    ladder-tag overlap from 0 to 47. That independent check is why this
    transform is applied while the six framework-prefix states in qualify() are
    not — there, nothing could confirm a guess.

    Returns the code unchanged when no grade segment is found in the first few
    positions, which is what happens to the high-school paths
    ('SC.CCRS.MA.9-12.A1.AAPR.1'). Those are out of scope anyway.
    """
    parts = code.split(".")
    if len(parts) < 3:
        return code
    for i in range(1, min(len(parts) - 1, 6)):
        if _GRADE_SEGMENT_RE.match(parts[i]):
            # i == 1 means the code is already short; nothing to strip.
            return code if i == 1 else ".".join([parts[0]] + parts[i:])
    return code


def load_audit(cur, path: Path) -> dict:
    """
    scored_alignments.csv — verdicts over the all_states tag set, plus the
    alternates it proposed.

    Rev 4 assumed this audits the learnosity edges. It does not, and the codes
    say so plainly: the audit's anchors overlap all_states at 84% and learnosity
    at 3.9%, and that 3.9% is entirely Massachusetts — the one state whose own
    two-letter prefix collides with the item bank's 'MA' for Mathematics. The
    two files do not share a code vocabulary:

        audit      SC  '7.PAFR.2.2'          ND  '2.DPS.D.2'
        learnosity SC  'SC.CCRS.MA.9-12...'  ND  'ND.MA.9-12.HS.A-APR.1'

    So each row is split by what it actually says:

      the verdict on the audited tag   -> alignment_audit  (a Path A flag)
      a DIFFERENT recommended code     -> model_suggestions (Path C evidence)

    A row whose recommendation equals the tag it just confirmed suggests
    nothing and is not a suggestion — that is ~93% of the file.

    Neither table may corroborate anything (§4 independence): the audit was
    produced by running lesson overlap, which is Path A's own method, plus the
    Path C model. Use it to demote, never to promote.
    """
    edges = {(sc, cc) for sc, cc in cur.execute(
        "SELECT state_code, ccss_code FROM crosswalk")}

    stats = Counter()
    audits, suggestions = [], []
    df = pd.read_csv(path, dtype=str)
    for _, r in df.iterrows():
        state = str(r.get("state") or "").strip().upper()[:2]
        # anchor_id is a BARE state code — South Dakota's row reads '5.NF.B.4',
        # identical to the CCSS code it is being aligned to. Same qualification
        # as all_states, and for the same reason.
        state_code = qualify(state, str(r.get("anchor_id") or ""))
        existing = normalize_code(str(r.get("existing_ccss_id") or ""))
        if not state or not state_code or not existing:
            stats["unusable_row"] += 1
            continue

        verdict = str(r.get("verdict") or "").strip().lower() or None
        try:
            confidence = float(r.get("confidence"))
        except (TypeError, ValueError):
            confidence = None
        raw_rec = r.get("recommended_ccss_id")
        recommended = (normalize_code(str(raw_rec))
                       if isinstance(raw_rec, str) and raw_rec.strip() else None)
        rationale = r.get("rationale")
        rationale = str(rationale).strip() if isinstance(rationale, str) else None

        audits.append((state, state_code, existing, verdict, confidence, rationale))
        stats["audited"] += 1
        if state_code in {sc for sc, _ in edges}:
            stats["also_a_learnosity_code"] += 1

        if recommended and recommended != existing:
            stats["changed_the_tag"] += 1
            suggestions.append((state, state_code, recommended, rationale))

    cur.executemany(
        "INSERT OR IGNORE INTO alignment_audit (state, state_code, ccss_code,"
        " verdict, confidence, rationale) VALUES (?,?,?,?,?,?)", audits)
    cur.executemany(
        "INSERT OR IGNORE INTO model_suggestions (state, state_code, ccss_code,"
        " rationale) VALUES (?,?,?,?)", suggestions)
    stats["audit_rows"] = len(df)
    return dict(stats)


def load_crosswalk_learnosity(cur, path: Path) -> tuple[int, int]:
    """
    learnosity_combined.csv. Deterministic: parse the CCSS code out of
    targetTags and pair it with newTagValue.

    Read with utf-8-sig — the file carries a BOM and the first column comes
    back named '﻿state' without it.
    """
    kept, skipped, batch = 0, 0, []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            m = LEARNOSITY_CCSS_RE.search(row.get("targetTags") or "")
            state_code = (row.get("newTagValue") or "").strip()
            state = (row.get("state") or "").strip().upper()[:2]
            if not m or not state_code:
                skipped += 1          # Domain and Cluster rows land here
                continue
            # newTagName reads 'MD-MDDOE Standard', 'AK-DEED Cluster'. The last
            # word is the level of the STATE side of the edge.
            name = (row.get("newTagName") or "").split()
            # Several states' exports use 'MA' as a MATHEMATICS prefix, not a
            # state one: Maryland writes 'MA.9-12.AI.HSA.APR.A.1' under
            # newTagName 'MD-MDDOE Standard', and New York and Wyoming do the
            # same. Left alone, those codes are indistinguishable from
            # Massachusetts' and collide with them on standard_code.
            qualified = qualify(state, state_code)
            batch.append((
                state,
                shorten_learnosity_code(qualified),
                normalize_code(m.group(1)),
                qualified,
                name[-1] if name else None))
            if len(batch) >= CHUNK:
                kept += _flush(cur, SQL_CROSSWALK, batch)
    kept += _flush(cur, SQL_CROSSWALK, batch)
    return kept, skipped


# -------------------------------------------------------- standard_tag_status

def load_tag_status(cur, path: Path) -> tuple[int, list[str]]:
    """
    The 'Tagged to a Stem' work queue from the five standards sheets.

    The primary key is the code alone, so the 17 codes appearing in both
    'California-All' and 'California-not CCSS' can only keep one row. Sheet
    order decides it and the collisions are returned so they are visible rather
    than assumed.
    """
    total, collisions = 0, []
    seen: dict[str, str] = {}
    for sheet, note_a, note_b in TAG_STATUS_SHEETS:
        try:
            df = pd.read_excel(path, sheet_name=sheet)
        except ValueError:
            continue
        df.columns = [str(c).strip() for c in df.columns]
        if "Number" not in df.columns:
            continue
        for _, r in df.iterrows():
            raw = r.get("Number")
            if pd.isna(raw):
                continue
            code = normalize_code(str(raw))
            if not code:
                continue
            # Only a CROSS-sheet repeat is interesting. A code listed twice
            # within one sheet is just a duplicate row and says nothing about
            # which sheet owns the standard.
            if code in seen and seen[code] != sheet:
                collisions.append(f"{code}: {seen[code]} -> {sheet}")
            seen[code] = sheet
            tagged = str(r.get("Tagged to a Stem", "")).strip().lower() == "yes"

            def note(col):
                if col is None or col not in df.columns:
                    return None
                v = r.get(col)
                return None if pd.isna(v) else str(v).strip() or None

            cur.execute(
                "INSERT OR REPLACE INTO standard_tag_status (standard_code,"
                " sheet, tagged_to_stem, note_tagging, note_postladder)"
                " VALUES (?,?,?,?,?)",
                (code, sheet, 1 if tagged else 0, note(note_a), note(note_b)))
            total += 1
    return total, collisions


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB))
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    cur = con.cursor()

    print("standard_lessons")
    n = load_lessons_from_ref_csv(cur, config.CCSS_ALIGNMENT_GUIDE_CSV,
                                  "guide", qualify_state=False)
    print(f"  guide       {n:8,d}")
    n = load_lessons_from_metadata(cur, config.LESSON_METADATA_CSV)
    print(f"  metadata    {n:8,d}")
    n = load_lessons_from_ref_csv(cur, config.ALL_STATES_CSV,
                                  "all_states", qualify_state=True)
    print(f"  all_states  {n:8,d}")

    # Learnosity FIRST: it is the only edge source. The audit is then joined
    # onto those edges, never unioned alongside them.
    print("crosswalk — Path B edges (learnosity only)")
    kept, skipped = load_crosswalk_learnosity(cur, config.LEARNOSITY_COMBINED_CSV)
    edges = cur.execute("SELECT COUNT(*) FROM crosswalk").fetchone()[0]
    print(f"  learnosity rows read {kept:8,d}, {skipped:,d} skipped (not a standard row)")
    print(f"  distinct edges       {edges:8,d}")

    audit = load_audit(cur, config.SCORED_ALIGNMENTS_CSV)
    print("\naudit — scored_alignments (verdicts over the all_states tag set)")
    print(f"  audit rows                       {audit.get('audit_rows', 0):8,d}")
    print(f"  -> alignment_audit               {audit.get('audited', 0):8,d}")
    print(f"  recommended a different code     {audit.get('changed_the_tag', 0):8,d}"
          "   -> model_suggestions")
    print(f"  anchors also a learnosity code   {audit.get('also_a_learnosity_code', 0):8,d}"
          "   <- Massachusetts prefix collision, not a join")
    if audit.get("unusable_row"):
        print(f"  unusable rows                    {audit['unusable_row']:8,d}")

    total, collisions = load_tag_status(cur, config.TAGGING_WORKBOOK)
    print(f"standard_tag_status rows read: {total:,d}")
    if collisions:
        print(f"  {len(collisions)} codes present in more than one sheet; "
              f"the later sheet wins:")
        for c in collisions:
            print(f"    {c}")

    con.commit()
    for table in ("standard_lessons", "crosswalk", "alignment_audit",
                  "model_suggestions", "standard_tag_status"):
        n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:22s} {n:9,d}")
    con.close()


if __name__ == "__main__":
    main()
