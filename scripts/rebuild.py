"""
rebuild.py — delete the database and build it again from data/source.

This is the safe default. data/build is disposable by design: if anything
looks wrong, delete it and run this. Nothing in data/source is ever modified.

    python scripts/rebuild.py
    python scripts/rebuild.py --skip-reconcile
    python scripts/rebuild.py --skip-candidates
    python scripts/rebuild.py --skip-review-reconcile
"""

import argparse
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from mh2.load_standards import find_standard_alias_collisions  # noqa: E402
from mh2.normalize import resolve_standard_alias  # noqa: E402


def _is_ccss_leaf(code: str) -> bool:
    """Four or more dot-separated segments with a digit in the fourth."""
    segs = code.split(".")
    return len(segs) >= 4 and any(ch.isdigit() for ch in segs[3])


def _five_tab_rows(con) -> list[tuple[str, str, str | None]]:
    """
    (standard_id, sheet, band) for the coverage audit's five-tab denominator:
    the four workbook tabs plus the Gaps tab, excluding California-All, with
    the CCSS heading predicate applied to the CCSS tab only.

    Since Change 4, standard_tag_status.sheet carries the Gaps tab directly
    ('Other State Standards Gaps') for every code that sheet lists --
    load_layer1.load_tag_status now reads the same sheet and the same
    Standard Code column that load_standards.load_gaps_sheet does. The
    `source == 'tagging:gaps'` branch below is kept as a safety net, not the
    primary path; it should not fire on a clean rebuild.
    """
    tag_sheet = dict(con.execute(
        "SELECT standard_code, sheet FROM standard_tag_status"))
    band_of = dict(con.execute("SELECT grade, band FROM grade_order"))
    rows = []
    for sid, grade, source in con.execute(
            "SELECT standard_id, grade, source FROM standards"):
        sheet = tag_sheet.get(sid)
        if sheet is None:
            if source != "tagging:gaps":
                continue
            sheet = "Other State Standards Gaps"
        if sheet == "California-All":
            continue
        if sheet == "CCSS" and not _is_ccss_leaf(sid):
            continue
        rows.append((sid, sheet, band_of.get(grade)))
    return rows


def report_grade_tokens(con) -> None:
    """Change 1/5: any standards.grade value outside grade_order's token set."""
    cur = con.cursor()
    known = {g for g, in cur.execute("SELECT grade FROM grade_order")}
    rows = cur.execute(
        "SELECT grade, COUNT(*), MIN(standard_id) FROM standards"
        " GROUP BY grade ORDER BY grade").fetchall()
    unknown = [(g, n, ex) for g, n, ex in rows if g not in known]
    print(f"\n=== standards.grade values outside grade_order: {len(unknown)}")
    if not unknown:
        print("    none")
    for g, n, ex in unknown:
        print(f"    {g!r:20s} n={n:<6,d} e.g. {ex}")


def report_band_distribution(con) -> None:
    """Change 2/5: band split over the five-tab denominator, per tab."""
    by_sheet: dict[str, Counter] = {}
    totals = Counter()
    for _sid, sheet, band in _five_tab_rows(con):
        key = band if band in ("PK5", "6_9") else "NULL"
        by_sheet.setdefault(sheet, Counter())[key] += 1
        totals[key] += 1
    print("\n=== band distribution (five-tab denominator)")
    print(f"    {'tab':28s} {'PK5':>6s} {'6_9':>6s} {'NULL':>6s}")
    for sheet in sorted(by_sheet):
        c = by_sheet[sheet]
        print(f"    {sheet:28s} {c['PK5']:6d} {c['6_9']:6d} {c['NULL']:6d}")
    print(f"    {'TOTAL':28s} {totals['PK5']:6d} {totals['6_9']:6d} {totals['NULL']:6d}")


def report_standard_alias(con) -> None:
    """Change 3/5: alias row counts by tier, PK-5 recoveries by tier, collisions."""
    cur = con.cursor()
    tier_counts = dict(cur.execute(
        "SELECT tier, COUNT(*) FROM standard_alias GROUP BY tier"))
    print(f"\n=== standard_alias  punct={tier_counts.get('punct', 0)} "
          f"nocluster={tier_counts.get('nocluster', 0)}")

    collisions = find_standard_alias_collisions(cur)
    for tier, coll in collisions.items():
        if not coll:
            continue
        print(f"    {tier} collisions (key claimed by >1 standard_id, "
              f"inserted for neither): {len(coll)}")
        for key, sids in coll:
            print(f"      {key!r:20s} -> {sids}")

    pk5_ids = {sid for sid, _sheet, band in _five_tab_rows(con) if band == "PK5"}
    tagged_exact = {r[0] for r in cur.execute(
        "SELECT DISTINCT standard_id FROM node_standards")}
    zero_tag = pk5_ids - tagged_exact

    standards_ids = {r[0] for r in cur.execute("SELECT standard_id FROM standards")}
    ladder_codes = {r[0] for r in cur.execute(
        "SELECT DISTINCT standard_id FROM node_standards")}

    recovered: dict[str, tuple[str, str]] = {}   # standard_id -> (tier, ladder_code)
    for lcode in ladder_codes:
        if lcode in standards_ids:
            continue
        sid, tier = resolve_standard_alias(cur, lcode)
        if sid is None or tier not in ("punct", "nocluster"):
            continue
        prev = recovered.get(sid)
        if prev is None or (prev[0] == "nocluster" and tier == "punct"):
            recovered[sid] = (tier, lcode)

    by_tier = {"punct": [], "nocluster": []}
    for sid, (tier, lcode) in recovered.items():
        if sid in zero_tag:
            by_tier[tier].append((lcode, sid))

    unmatched = len(zero_tag) - len(by_tier["punct"]) - len(by_tier["nocluster"])
    print(f"    PK-5 zero-tag denominator: {len(zero_tag)}")
    print(f"    recovered at punct:        {len(by_tier['punct'])}")
    print(f"    recovered at nocluster:    {len(by_tier['nocluster'])}")
    print(f"    still unmatched:           {unmatched}")
    for tier in ("punct", "nocluster"):
        if by_tier[tier]:
            print(f"    -- {tier} recoveries (ladder code -> standard_id) --")
            for lcode, sid in sorted(by_tier[tier]):
                print(f"       {lcode!r:24s} -> {sid}")


def report_claim_vs_coverage(con) -> None:
    """
    Change 4/5: standard_tag_status.tagged_to_stem ('claimed tagged') against
    node_standards ('actually tagged'), over the PK-5 denominator, per tab.

    Both directions are reported: a claim with zero exact tags (the writer
    said done but nothing lands on it -- a lead for report_standard_alias's
    keying-failure recoveries above), and the converse, a tag with no claim
    (tagged but the workbook was never marked). Exact standard_id match only
    -- alias recovery is a separate report.
    """
    cur = con.cursor()
    claimed = {sid for sid, in cur.execute(
        "SELECT standard_code FROM standard_tag_status WHERE tagged_to_stem = 1")}
    tagged_exact = {r[0] for r in cur.execute(
        "SELECT DISTINCT standard_id FROM node_standards")}

    pk5_by_sheet: dict[str, list[str]] = {}
    for sid, sheet, band in _five_tab_rows(con):
        if band == "PK5":
            pk5_by_sheet.setdefault(sheet, []).append(sid)

    print("\n=== claim vs coverage (PK-5 denominator, exact match)")
    print(f"    {'tab':28s} {'claimed':>8s} {'claimed_zero_tags':>18s}"
          f" {'tagged_not_claimed':>19s}")
    tot_claimed = tot_claimed_zero = tot_total = 0
    for sheet in sorted(pk5_by_sheet):
        ids = pk5_by_sheet[sheet]
        sheet_claimed = [sid for sid in ids if sid in claimed]
        claimed_zero = [sid for sid in sheet_claimed if sid not in tagged_exact]
        tagged_not_claimed = [sid for sid in ids if sid in tagged_exact and sid not in claimed]
        print(f"    {sheet:28s} {len(sheet_claimed):8d} {len(claimed_zero):18d}"
              f" {len(tagged_not_claimed):19d}")
        tot_claimed += len(sheet_claimed)
        tot_claimed_zero += len(claimed_zero)
        tot_total += len(ids)
    print(f"    {'TOTAL':28s} {tot_claimed:8d} {tot_claimed_zero:18d}"
          f"  (denominator {tot_total})")


def report_ladder_mismatches(con) -> None:
    """
    Change 3c/3d: ladder-written codes that don't exactly match a standard.

    3c: codes that resolve via standard_alias -- these are the typos and
    omissions a corrected .docx should fix; resolution masks them silently
    otherwise. 3d: codes that resolve EXACTLY but the standard they hit is
    outside the coverage denominator (source = 'all_states.csv') -- a writer
    tagged beyond the curated gap list. Not an error; reported separately so
    the count is visible.

    Written to config.REPORTS on every rebuild, same as the other pipeline
    reports.
    """
    cur = con.cursor()
    standards_ids = {r[0] for r in cur.execute("SELECT standard_id FROM standards")}
    ladder_codes = sorted({r[0] for r in cur.execute(
        "SELECT DISTINCT standard_id FROM node_standards")})

    mismatches = [c for c in ladder_codes if c not in standards_ids]
    resolved, unresolved = [], []
    for code in mismatches:
        sid, tier = resolve_standard_alias(cur, code)
        if sid:
            nodes = [r[0] for r in cur.execute(
                "SELECT node_id FROM node_standards WHERE standard_id = ?", (code,))]
            resolved.append((code, sid, tier, nodes))
        else:
            unresolved.append(code)

    outside = sorted({r[0] for r in cur.execute("""
        SELECT DISTINCT ns.standard_id FROM node_standards ns
        JOIN standards s ON s.standard_id = ns.standard_id
        WHERE s.source = 'all_states.csv'
    """)})

    lines = [
        "=== 3c: ladder-written codes resolved via standard_alias"
        " (fix the .docx; do not rely on this permanently) ===",
        f"{len(resolved)} rows",
    ]
    for code, sid, tier, nodes in resolved:
        lines.append(f"  {code!r:20s} -> {sid:15s} tier={tier:10s} nodes={nodes}")
    lines += [
        "",
        f"=== ladder-written codes with no exact or alias match: {len(unresolved)} rows ===",
    ]
    lines += [f"  {c}" for c in unresolved]
    lines += [
        "",
        "=== 3d: ladder-written codes that match EXACTLY but the standard is"
        " outside the coverage denominator (source = all_states.csv) --"
        " not an error, the writer tagged beyond the curated gap list ===",
        f"{len(outside)} rows",
    ]
    lines += [f"  {c}" for c in outside]

    out = config.REPORTS / "ladder_code_mismatches.txt"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n=== ladder code mismatches: {len(resolved)} resolved via alias, "
          f"{len(unresolved)} unresolved, {len(outside)} outside the denominator"
          f" (exact match)\n    -> wrote {out}")


def run(label: str, args: list[str]) -> None:
    print(f"\n=== {label}")
    result = subprocess.run([sys.executable, *args], cwd=config.ROOT)
    if result.returncode != 0:
        sys.exit(f"FAILED: {label}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-reconcile", action="store_true")
    ap.add_argument("--skip-candidates", action="store_true")
    ap.add_argument("--skip-review-reconcile", action="store_true")
    args = ap.parse_args()

    config.ensure_dirs()

    missing = [p.name for p in (config.STEM_WORKBOOK, config.STEM_WORKBOOK_G6,
                                 config.TAGGING_WORKBOOK)
               if not p.exists()]
    if missing:
        sys.exit("Missing source files in data/source/workbooks: " + ", ".join(missing))

    print("=== creating schema")
    if config.DB.exists():
        config.DB.unlink()
    sqlite3.connect(config.DB).executescript(config.SCHEMA.read_text())
    print(f"    {config.DB}")

    # mh2_seq.db holds durable human review decisions (Session C) and must
    # survive this unlink-and-rebuild -- created if absent, never deleted.
    print("=== ensuring review database (mh2_seq.db)")
    sqlite3.connect(config.SEQ_DB).executescript(config.SCHEMA_SEQ.read_text())
    print(f"    {config.SEQ_DB}")

    run("loading standards + stem workbook",
        ["-m", "mh2.load_standards", "--db", str(config.DB),
         "--project", str(config.SOURCE)])

    run("loading stems.csv (ladder <-> workbook stem mapping)",
        ["-m", "mh2.load_stems", "--db", str(config.DB),
         "--csv", str(config.STEMS_CSV), "--ladders", str(config.LADDERS)])

    run("ingesting ladders",
        ["-m", "mh2.ingest_ladders", "--db", str(config.DB),
         "--glob", config.LADDER_GLOB])

    run("parsing node standards cells",
        ["-m", "mh2.parse_node_standards", "--db", str(config.DB),
         "--ladders", str(config.LADDERS)])

    run("loading grade ruling into node_grade",
        ["-m", "mh2.load_grade_ruling", "--db", str(config.DB),
         "--worksheet", str(config.GRADE_WORKSHEET)])

    run("loading layer 1 (standard_lessons, crosswalk, tag status)",
        ["-m", "mh2.load_layer1", "--db", str(config.DB)])

    run("loading Path C predictions (step 8)",
        ["-m", "mh2.load_predictions", "--db", str(config.DB),
         "--predictions", str(config.PREDICTIONS)])

    run("loading reranks (step 7 browse list; cache-only, no API calls)",
        ["-m", "mh2.load_reranks", "--db", str(config.DB),
         "--cache", str(config.RERANK_CACHE)])

    if not args.skip_candidates:
        run("generating candidates (step 6, Paths 0/A/B)",
            ["-m", "mh2.candidates", "--db", str(config.DB)])

    if not args.skip_reconcile:
        run("reconciling workbook vs ladders",
            ["-m", "mh2.reconcile", "--db", str(config.DB),
             "--out", str(config.REPORTS)])

    if not args.skip_review_reconcile:
        run("reconciling review state vs rebuilt ladders",
            ["-m", "mh2.reconcile_review", "--db", str(config.DB),
             "--seq-db", str(config.SEQ_DB), "--out", str(config.REPORTS)])

    con = sqlite3.connect(config.DB)
    print("\n=== row counts")
    for table in ("standards", "concepts", "concept_standards", "nodes",
                  "node_standards", "node_standards_parsed",
                  "node_absence_assertions", "stem_map", "standard_lessons",
                  "crosswalk", "alignment_audit", "model_suggestions",
                  "standard_tag_status", "model_predictions",
                  "model_state_predictions", "model_reranks", "candidates",
                  "leaves", "node_links", "drift", "node_grade",
                  "node_grade_ruling", "standard_alias"):
        try:
            n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"    {table:20s} {n:7,d}")
        except sqlite3.OperationalError:
            pass

    report_grade_tokens(con)
    report_band_distribution(con)
    report_standard_alias(con)
    report_claim_vs_coverage(con)
    report_ladder_mismatches(con)

    print("\n=== unruled grade strings (resolution='unresolved')")
    unruled = con.execute(
        "SELECT raw_value, canon_key, COUNT(*) AS n,"
        " GROUP_CONCAT(DISTINCT nodes.source_file) AS files"
        " FROM node_grade_ruling JOIN nodes USING (node_id)"
        " WHERE resolution = 'unresolved'"
        " GROUP BY raw_value, canon_key ORDER BY n DESC").fetchall()
    if not unruled:
        print("    none")
    for raw_value, canon_key, n, files in unruled:
        print(f"    {raw_value!r:40s} canon={canon_key!r:20s} "
              f"n={n:<4d} {files}")

    print("\n=== span width distribution (resolution='ruled' nodes)")
    widths = Counter(
        width for (width,) in con.execute(
            "SELECT MAX(grade_order.ord) - MIN(grade_order.ord) + 1"
            " FROM node_grade_ruling"
            " JOIN node_grade USING (node_id)"
            " JOIN grade_order USING (grade)"
            " WHERE node_grade_ruling.resolution = 'ruled'"
            " GROUP BY node_grade_ruling.node_id"))
    for width in sorted(widths):
        print(f"    width {width}   {widths[width]}")

    con.close()
    print(f"\nDone. Database: {config.DB}\nReports: {config.REPORTS}")


if __name__ == "__main__":
    main()
