"""
report_ladder_counts.py — checkpoint 1. Reconcile the ladders against the
workbook, per §7.

Reads the source files directly, no database. The ladder -> workbook stem
mapping comes from stems.csv (§7.1) and is never inferred; one ladder can cover
two workbook stems, which is why Comparing and Ordering is compared against
Comparing + Ordering together.

    python scripts/report_ladder_counts.py
"""

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

import config  # noqa: E402
from mh2.ingest_ladders import read_docx  # noqa: E402
from mh2.load_stems import (  # noqa: E402
    normalize_stem_text, read_stems_csv, resolve_ladder_files,
)

STEM_SHEETS = [
    "Measurement and Data",
    "Number Systems and Structures",
    "Operations and Equations",
    "Shapes and Space",
    "Structure Pattern and Reasoning",
]


def workbook_concept_counts(path: Path) -> dict[str, int]:
    """Concept/Skill row count per workbook stem, keyed on normalized stem text."""
    counts: dict[str, int] = defaultdict(int)
    for sheet in STEM_SHEETS:
        try:
            df = pd.read_excel(path, sheet_name=sheet)
        except ValueError:
            continue
        df.columns = [str(c).strip() for c in df.columns]
        concept_col = next(
            (c for c in df.columns if c.lower().startswith("concept/skill")), None)
        if concept_col is None or "Stem" not in df.columns:
            continue
        # The Stem column is written once per block and blank on the rows below.
        stems = df["Stem"].ffill()
        for stem, concept in zip(stems, df[concept_col]):
            if pd.isna(concept) or not str(concept).strip():
                continue
            counts[normalize_stem_text(str(stem))] += 1
    return dict(counts)


def main() -> None:
    rows = read_stems_csv(config.STEMS_CSV)
    resolved = resolve_ladder_files(rows, config.LADDERS)
    wb_counts = workbook_concept_counts(config.STEM_WORKBOOK)

    # Group stems.csv rows by the ladder they share, so the Comparing and
    # Ordering ladder is compared against both of its workbook stems at once.
    by_ladder: dict[Path, list[dict]] = defaultdict(list)
    for r in rows:
        if r["stem_id"] in resolved:
            by_ladder[resolved[r["stem_id"]]].append(r)

    issues: list[dict] = []
    print(f"{'ladder':46s} {'workbook':>9s} {'ladder':>7s} {'nodes':>6s} {'cells':>6s}")
    print("-" * 78)
    tot_wb = tot_cs = tot_nodes = tot_cells = 0

    for path in sorted(by_ladder):
        stem_rows = by_ladder[path]
        nodes = read_docx(path, path.stem, issues)

        labels: list[str] = []
        for n in nodes:
            if n["concept_skill"] not in labels:
                labels.append(n["concept_skill"])
        cells = sum(1 for n in nodes if (n.get("standards_notes") or "").strip())

        wb = 0
        missing_stems = []
        for r in stem_rows:
            key = normalize_stem_text(r["workbook_stem"])
            if key in wb_counts:
                wb += wb_counts[key]
            else:
                missing_stems.append(r["workbook_stem"])

        flag = "" if wb == len(labels) else "  <- differs"
        names = " + ".join(r["workbook_stem"].replace("\n", " ") for r in stem_rows)
        print(f"{names[:46]:46s} {wb:9d} {len(labels):7d} {len(nodes):6d} {cells:6d}{flag}")
        for s in missing_stems:
            print(f"    ! workbook stem not found in the workbook: {s!r}")

        tot_wb += wb
        tot_cs += len(labels)
        tot_nodes += len(nodes)
        tot_cells += cells

    print("-" * 78)
    print(f"{'TOTAL':46s} {tot_wb:9d} {tot_cs:7d} {tot_nodes:6d} {tot_cells:6d}")

    print("\nheading problems needing a human (source holes, ambiguous bullets):")
    if issues:
        for i in issues:
            print(f"    {i['file']}  {i['kind']}  {i.get('detail', '')}")
    else:
        print("    none")


if __name__ == "__main__":
    main()
