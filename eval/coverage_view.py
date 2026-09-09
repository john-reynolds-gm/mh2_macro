"""
coverage_view.py — CLI over the coverage query layer (mh2.coverage).

Prints the rev 4 handoff's §4 denominator/band table and §5 coverage tables
(rollup, claim axis, flags), all from the SAME alias-resolved rollup --
one Red/Yellow/Green number per standard, not the two the retired
eval/coverage_audit.py used to print (its coverage table was exact-match-only;
its content-area block was post-resolution).

Read-only diagnostic, not part of the build. No FastAPI, no endpoints --
this prints to stdout.

Usage:
    python eval/coverage_view.py [path/to/mh2.db]

Resolves the DB from, in order: argv[1], $MH2_DB, mh2.config.DB, then a short
list of likely paths. Opens read-only so it can never create an empty
database (the retired coverage_audit.py's original bug, hit once already).
"""
import os
import sqlite3
import sys
from pathlib import Path


def resolve_db() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1]).expanduser()
    if os.environ.get("MH2_DB"):
        return Path(os.environ["MH2_DB"]).expanduser()
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import config
        return Path(config.DB)
    except Exception:
        pass
    root = Path(__file__).resolve().parent.parent
    for c in [root / "data" / "build" / "mh2.db", root / "data" / "mh2.db",
              root / "mh2.db"]:
        if c.exists():
            return c
    sys.exit("Could not locate mh2.db. Pass the path as an argument or set $MH2_DB.")


DB = resolve_db()
if not DB.exists():
    sys.exit(f"No database at {DB}")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mh2 import coverage  # noqa: E402

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
print(f"db: {DB}")

tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
missing = {"grade_order", "standards", "standard_tag_status", "node_standards"} - tables
if missing:
    sys.exit(f"Database is missing expected tables: {sorted(missing)}. Wrong file?")


def _row_label(sheet: str) -> str:
    return {"Other State Standards Gaps": "Other Gaps"}.get(sheet, sheet)


# ---------------------------------------------------------------------- §4

all_rows = coverage.build_rows(con, band=None)
denom = coverage.denominator_table(all_rows)

print("\n=== §4 denominator by tab x band ===")
print(f"{'tab':28s} {'PK5':>6s} {'6_9':>6s} {'NULL':>6s}")
totals = {"PK5": 0, "6_9": 0, "NULL": 0}
for sheet in coverage.TABS:
    c = denom[sheet]
    for k in totals:
        totals[k] += c[k]
    print(f"{_row_label(sheet):28s} {c['PK5']:6d} {c['6_9']:6d} {c['NULL']:6d}")
print(f"{'TOTAL':28s} {totals['PK5']:6d} {totals['6_9']:6d} {totals['NULL']:6d}")

# ---------------------------------------------------------------------- §5

pk5_rows = [r for r in all_rows if r.band == "PK5"]

cov = coverage.coverage_table(pk5_rows)
print("\n=== §5 PK-5 coverage by tab (alias-resolved; one number, not two) ===")
print(f"{'tab':28s} {'n':>5s} {'Green':>6s} {'Yellow':>7s} {'Red':>5s} {'has>=1 tag':>11s}")
tot_n = tot_g = tot_y = tot_r = 0
for sheet in coverage.TABS:
    c = cov.get(sheet)
    if not c:
        continue
    tot_n += c["n"]; tot_g += c["green"]; tot_y += c["yellow"]; tot_r += c["red"]
    print(f"{_row_label(sheet):28s} {c['n']:5d} {c['green']:6d} {c['yellow']:7d} "
          f"{c['red']:5d} {c['pct_has_tag']:10.1f}%")
print(f"{'TOTAL':28s} {tot_n:5d} {tot_g:6d} {tot_y:7d} {tot_r:5d} "
      f"{100*(tot_g+tot_y)/tot_n:10.1f}%")

claim = coverage.claim_table(pk5_rows)
print("\n=== §5 claim axis (PK-5) ===")
print(f"{'':22s} {'has a tag':>10s} {'no tag':>8s}")
print(f"{'Tagged in sheet = yes':22s} {claim['yes_tag']:10d} {claim['yes_notag']:8d}")
print(f"{'Tagged in sheet = blank':22s} {claim['blank_tag']:10d} {claim['blank_notag']:8d}")

flags = coverage.flags_summary(pk5_rows)
print("\n=== §5 flags axis (PK-5) ===")
print(f"rows with >=1 flagged tag: {flags['flagged_rows']}")
print(f"Yellow rows:               {flags['yellow_rows']}")
print(f"Green rows with a flag:    {flags['green_with_flag']}"
      "   (color and flags are independent axes -- see §3)")

gmc = coverage.grade_match_distribution(pk5_rows)
print("\n=== tag-grain grade_match distribution (PK-5) ===")
for k, v in sorted(gmc.items(), key=lambda kv: -kv[1]):
    print(f"  {k:14s} {v}")

con.close()
