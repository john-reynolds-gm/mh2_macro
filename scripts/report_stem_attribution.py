"""
report_stem_attribution.py — Session B.2 reports over mh2.coverage's stem
attribution (concept_standards vs. the category route; see
mh2.coverage.build_stem_attribution).

"Resolution without reporting hides the defect" applies here as much as it
does to alias resolution: every place two sources disagree, or one route
returns more than one stem, or a category names a stem with no ladder file
yet, gets written down rather than silently picked for or folded away.

Writes three files to data/reports/ and prints a summary of each:
    stem_attribution_conflicts.txt   -- §5 rule 2
    stem_attribution_multi_fanout.txt -- §5 rule 5
    stem_attribution_unattributed.txt -- §5 rule 6

    python scripts/report_stem_attribution.py
"""
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from mh2 import coverage  # noqa: E402


def section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def _connect_ro() -> sqlite3.Connection:
    db = os.environ.get("MH2_DB") or str(config.DB)
    if not Path(db).exists():
        sys.exit(f"No such file: {db}")
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def main() -> None:
    con = _connect_ro()
    attribution, conflicts, category_diagnostics = coverage.build_stem_attribution(con)
    rows = coverage.build_rows(con, band=None)
    row_by_code = {r.code: r for r in rows}
    config.REPORTS.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------- conflicts
    section("stem_attribution_conflicts.txt -- concept_standards vs. category route")
    print(f"{len(conflicts)} standards where the two sources named different stems.")
    print("concept_standards wins (§5 rule 1); category's answer is recorded, not used.")
    lines = [
        "concept_standards wins over the category route (§5 rule 1) whenever both",
        "attribute a standard. This file lists every standard where they named",
        "DIFFERENT stems -- the category route's answer here was NOT used for",
        "ladder_status; it is recorded so the disagreement isn't silently lost.",
        f"\n{len(conflicts)} standards.\n",
    ]
    for code, cs_stems, cat_stems in sorted(conflicts):
        r = row_by_code.get(code)
        sheet = r.sheet if r else "?"
        band = r.band if r else "?"
        lines.append(
            f"  {code:20s} {sheet:28s} band={band!s:6s} "
            f"concept_standards={cs_stems}  category={cat_stems}")
    (config.REPORTS / "stem_attribution_conflicts.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    for line in lines[:15]:
        print(line)
    if len(lines) > 15:
        print(f"  ... and {len(lines) - 15} more, see the file")

    # ---------------------------------------------------------- multi fanout
    section("stem_attribution_multi_fanout.txt -- rows naming more than one stem")
    multi_rows = [r for r in rows if len(r.stem_ids) > 1]
    by_band = Counter(r.band for r in multi_rows)
    by_source_shape = Counter()
    for r in multi_rows:
        attrib = attribution.get(r.code)
        by_source_shape[attrib.source if attrib else None] += 1
    print(f"{len(multi_rows)} rows carry more than one owning stem, by band: "
          f"{dict(by_band)}")
    print(f"  by winning source: {dict(by_source_shape)}")

    # Distinct category -> resolved-stem-set fan-outs (category route only).
    fanouts = Counter()
    for r in multi_rows:
        attrib = attribution.get(r.code)
        if attrib and attrib.source == "category":
            fanouts[(r.band, tuple(sorted(attrib.stem_ids)))] += 1

    lines = [
        "Rows where build_stem_attribution() resolved more than one owning stem",
        "and did not pick a winner (§5 rules 3-4). Two distinct causes, both",
        "legitimate, not conflated in the counts below:",
        "  - the category route's CSV names several DIFFERENT stems for one",
        "    category (up to 8 columns), or an ambiguous stem_map name shared by",
        "    two stems (schema.sql: masterlist_name repeats by design, e.g. PK5",
        "    'Comparing and Ordering' -> {COM, ORD});",
        "  - concept_standards itself attributes one standard to concepts under",
        "    more than one stem -- not caused by the category route at all.",
        f"\n{len(multi_rows)} multi-stem rows total, by band: {dict(by_band)}",
        f"by winning source: {dict(by_source_shape)}\n",
        f"\n{len(fanouts)} distinct category-route (band, stem-set) fan-outs:",
    ]
    for (band, stem_ids), n in sorted(fanouts.items(), key=lambda kv: -kv[1]):
        lines.append(f"  band={band!s:6s} {list(stem_ids)!s:40s} -> {n} standards")
    lines.append("\nEvery multi-stem row, verbatim:")
    for r in sorted(multi_rows, key=lambda r: (str(r.band), r.sheet, r.code)):
        attrib = attribution.get(r.code)
        lines.append(
            f"  {r.code:20s} {r.sheet:28s} band={r.band!s:6s} "
            f"source={attrib.source if attrib else None!s:20s} stems={r.stem_ids}")
    (config.REPORTS / "stem_attribution_multi_fanout.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(fanouts)} distinct category-route fan-outs")
    for (band, stem_ids), n in sorted(fanouts.items(), key=lambda kv: -kv[1])[:10]:
        print(f"  band={band!s:6s} {list(stem_ids)} -> {n} standards")

    # ------------------------------------------------------- category-route
    # ------------------------------------------------------- diagnostics
    section("category route: targets that did not resolve")
    unresolved = category_diagnostics["unresolved_targets"]
    no_ladder_yet = category_diagnostics["no_ladder_yet"]
    print(f"unresolved_targets (named no stem_map row at all): {len(unresolved)}")
    for cat, band, name in unresolved:
        print(f"  {cat!r} band={band} -> {name!r}")
    print(f"\nno_ladder_yet (resolved, but every named stem is inert -- no `stems` "
          f"row, no ladder file on disk): {len(no_ladder_yet)}")
    seen_stems = sorted({s for _c, _b, _n, stems in no_ladder_yet for s in stems})
    print(f"  distinct inert stem keys named: {seen_stems}")

    # ------------------------------------------------------------ unattributed
    section("stem_attribution_unattributed.txt -- neither source reached the row")
    unattributed = [r for r in rows if r.ladder_status == "unattributed"]
    by_band_sheet = Counter((r.band, r.sheet) for r in unattributed)
    print(f"{len(unattributed)} unattributed rows total (all bands).")
    for k in sorted(by_band_sheet, key=lambda k: (str(k[0]), k[1])):
        print(f"  band={k[0]!s:6s} {k[1]:28s} {by_band_sheet[k]}")

    lines = [
        "Rows where neither concept_standards nor the category route named any",
        "stem (§5 rule 6). Ruled: surfaced here, not chased (session brief §8).",
        f"\n{len(unattributed)} rows.\n",
        f"{'band':6s} {'sheet':28s} {'code':20s}",
    ]
    for r in sorted(unattributed, key=lambda r: (str(r.band), r.sheet, r.code)):
        lines.append(f"{r.band!s:6s} {r.sheet:28s} {r.code}")
    (config.REPORTS / "stem_attribution_unattributed.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")

    con.close()
    print(f"\nReports written to {config.REPORTS}")


if __name__ == "__main__":
    main()
