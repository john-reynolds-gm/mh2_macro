"""
report_node_standards.py — step 2a instrumentation.

Step 2 must print, not just load. These counts settle open design questions and
are worthless if they live only in the database. In particular the count of
nodes with no CCSS anchor is the number that decides §3.5, and nothing else can
decide it.

Writes CSVs to data/reports/ as well as printing, because the residual-prose
table is long and is meant to be read row by row, not skimmed.

    python scripts/report_node_standards.py
"""

import csv
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from mh2.ingest_ladders import read_docx  # noqa: E402
from mh2.load_stems import ladder_paths  # noqa: E402
from mh2.normalize import find_codes  # noqa: E402
from mh2.parse_node_standards import parse_cell  # noqa: E402

# Text shaped like a dotted standard code. Used only to find what the tokenizer
# REFUSED, so the rejects can be shown verbatim rather than vanishing.
CODE_SHAPED = re.compile(r"\b[A-Za-z]{2,6}\d*\.[A-Za-z0-9][A-Za-z0-9.\-]*")

RULE = "=" * 78


def section(title: str) -> None:
    print(f"\n{RULE}\n{title}\n{RULE}")


def write_csv(name: str, header: list[str], rows: list) -> Path:
    path = config.REPORTS / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return path


def main() -> None:
    con = sqlite3.connect(config.DB)
    cur = con.cursor()

    nodes = {}
    for node_id, stem_id, cs, text in cur.execute(
            "SELECT node_id, stem_id, concept_skill, node_text FROM nodes"):
        nodes[node_id] = (stem_id, cs, text)

    # ---------------------------------------------------------- absence
    section("ABSENCE ASSERTIONS, BY FRAMEWORK")
    rows = cur.execute(
        "SELECT framework, COUNT(*) FROM node_absence_assertions"
        " GROUP BY framework ORDER BY 2 DESC").fetchall()
    if rows:
        for framework, n in rows:
            print(f"  {framework:10s} {n}")
    else:
        print("  none")
    print()
    for node_id, framework, note in cur.execute(
            "SELECT node_id, framework, note FROM node_absence_assertions"
            " ORDER BY node_id"):
        cs = nodes.get(node_id, ("", "", ""))[1]
        print(f"  {node_id:12s} {framework:8s} {note}")
        print(f"               under: {cs}")

    # ------------------------------------------------ unanchored nodes (§3.5)
    section("NODES WITH NO CCSS CODE  —  THIS IS THE NUMBER THAT DECIDES §3.5")
    with_ccss = {r[0] for r in cur.execute(
        "SELECT DISTINCT node_id FROM node_standards_parsed WHERE state IS NULL")}
    tagged = {r[0] for r in cur.execute(
        "SELECT DISTINCT node_id FROM node_standards_parsed")}
    asserted = {r[0] for r in cur.execute(
        "SELECT DISTINCT node_id FROM node_absence_assertions")}

    all_nodes = set(nodes)
    unanchored = sorted(all_nodes - with_ccss)
    print(f"  nodes total                              {len(all_nodes)}")
    print(f"  nodes with at least one CCSS code        {len(with_ccss)}")
    print(f"  nodes with NO CCSS code                  {len(unanchored)}")
    print(f"      ... of which carry an absence assertion  "
          f"{len(set(unanchored) & asserted)}")
    print(f"      ... of which have no assertion either    "
          f"{len(set(unanchored) - asserted)}")
    print(f"      ... of which carry no tags at all        "
          f"{len(set(unanchored) - tagged)}")
    print(f"      ... of which carry state codes only      "
          f"{len(set(unanchored) & tagged)}")
    print()
    unanchored_rows = []
    for node_id in unanchored:
        stem_id, cs, text = nodes[node_id]
        state_codes = [r[0] for r in cur.execute(
            "SELECT standard_code FROM node_standards_parsed WHERE node_id=?"
            " ORDER BY standard_code", (node_id,))]
        kind = ("absence assertion" if node_id in asserted
                else "state codes only" if state_codes else "no tags at all")
        print(f"  {node_id:12s} [{kind}]")
        print(f"      {cs}")
        print(f"      node: {text[:90]}")
        if state_codes:
            print(f"      codes: {', '.join(state_codes)}")
        unanchored_rows.append(
            [node_id, stem_id, cs, text, kind, "; ".join(state_codes)])
    write_csv("unanchored_nodes.csv",
              ["node_id", "stem_id", "concept_skill", "node_text", "kind",
               "state_codes"], unanchored_rows)

    # -------------------------------------------------- multi-node standards
    section("STANDARDS ON MORE THAN ONE NODE WITHIN A CONCEPT/SKILL  (§4)")
    by_cs = defaultdict(lambda: defaultdict(list))
    for node_id, code in cur.execute(
            "SELECT node_id, standard_code FROM node_standards_parsed"):
        stem_id, cs, _ = nodes.get(node_id, ("", "", ""))
        by_cs[(stem_id, cs)][code].append(node_id)

    dist = Counter()
    multi_rows = []
    for (stem_id, cs), codes in sorted(by_cs.items(), key=lambda kv: str(kv[0])):
        for code, node_list in codes.items():
            dist[len(node_list)] += 1
            if len(node_list) > 1:
                multi_rows.append([stem_id, cs, code, len(node_list),
                                   "; ".join(sorted(node_list))])
    print("  nodes-per-code distribution, within a concept/skill:")
    for n in sorted(dist):
        print(f"      on {n} node{'s' if n > 1 else ' '}   {dist[n]:5d} codes")
    print(f"\n  codes appearing on more than one node: {len(multi_rows)}")
    for stem_id, cs, code, n, node_list in multi_rows[:40]:
        print(f"      {code:20s} x{n}  {str(cs)[:52]}")
    if len(multi_rows) > 40:
        print(f"      ... and {len(multi_rows) - 40} more, see the CSV")
    write_csv("multinode_standards.csv",
              ["stem_id", "concept_skill", "standard_code", "n_nodes", "node_ids"],
              multi_rows)

    # ----------------------------------------- residual prose, every fragment
    section("RESIDUAL PROSE — EVERY FRAGMENT, VERBATIM, WITH ITS LABEL")
    residual_rows = []
    rejected_rows = []
    cells_with_prose = set()
    fraction_rows = []

    for path in ladder_paths(config.LADDERS):
        for node in read_docx(path, path.stem):
            cell = (node.get("standards_notes") or "").strip()
            if not cell:
                continue
            parsed = parse_cell(cell)
            node_id = next(
                (nid for nid, (_s, _c, t) in nodes.items()
                 if t == node["node_text"]), "")

            if path.name.startswith("MH2_PK5_NumberSystemsandStructures_Fract"):
                fraction_rows.append(
                    [node["concept_skill"], node["node_text"],
                     len(parsed.codes), cell])

            for r in parsed.residuals:
                cells_with_prose.add((path.name, node["node_text"]))
                residual_rows.append(
                    [path.name, node["concept_skill"], node["node_text"],
                     r.label, r.attached_to or "", r.text])

            # What the tokenizer refused.
            covered = set()
            for m in find_codes(cell):
                covered.update(range(m.start, m.end))
            for m in CODE_SHAPED.finditer(cell):
                if not (set(range(m.start(), m.end())) & covered):
                    rejected_rows.append(
                        [path.name, node["concept_skill"], node["node_text"],
                         m.group(0), cell])

    # §1 says '50 of the 143 cells carry substantial prose'. Fragments shorter
    # than this are list debris ('B', '3.4d', 'not numbered'), so both numbers
    # are shown rather than one being passed off as the other.
    SUBSTANTIAL = 15
    substantial_cells = {(r[0], r[2]) for r in residual_rows
                         if len(r[5]) >= SUBSTANTIAL}

    label_counts = Counter(r[3] for r in residual_rows)
    print(f"  cells carrying any residual prose:          {len(cells_with_prose)}")
    print(f"  cells carrying prose of {SUBSTANTIAL}+ characters:  "
          f"{len(substantial_cells)}   (§1 says 50)")
    print(f"  fragments total:                            {len(residual_rows)}")
    for label, n in label_counts.most_common():
        print(f"      {label:14s} {n}")
    print()
    print(f"  {'label':14s} {'attached to':16s} fragment")
    print(f"  {'-' * 14} {'-' * 16} {'-' * 44}")
    for _f, _cs, _n, label, attached, text in residual_rows:
        print(f"  {label:14s} {attached[:16]:16s} {text}")
    p = write_csv("residual_prose.csv",
                  ["source_file", "concept_skill", "node_text", "label",
                   "attached_to", "fragment"], residual_rows)
    print(f"\n  -> {p}")

    # ------------------------------------------------------ rejected codes
    section("CODES REJECTED BY THE TOKENIZER, VERBATIM, WITH THEIR CELL")
    if not rejected_rows:
        print("  none")
    for _f, cs, node_text, code, cell in rejected_rows:
        print(f"  {code}")
        print(f"      concept/skill: {cs}")
        print(f"      node:          {node_text[:80]}")
        first = next((ln.strip() for ln in cell.split("\n") if code in ln), "")
        print(f"      cell line:     {first[:110]}")
    write_csv("rejected_codes.csv",
              ["source_file", "concept_skill", "node_text", "rejected_text",
               "source_cell"], rejected_rows)

    # -------------------------------------------------- Fractions anomaly
    section("FRACTIONS — 19 FILLED CELLS AGAINST 15 NODES (§1, §9.1)")
    print(f"  filled standards cells in the Fractions ladder: {len(fraction_rows)}")
    print(f"  distinct node names carrying one:               "
          f"{len({r[1] for r in fraction_rows})}")
    print()
    seen_cells = Counter(r[3] for r in fraction_rows)
    for cs, node_text, n_codes, cell in fraction_rows:
        dup = "  <- IDENTICAL CELL ALSO ON ANOTHER NODE" if seen_cells[cell] > 1 else ""
        print(f"  {node_text[:70]}{dup}")
        print(f"      under: {cs[:70]}")
        print(f"      codes parsed: {n_codes}")
    write_csv("fractions_cells.csv",
              ["concept_skill", "node_text", "n_codes", "source_cell"],
              fraction_rows)

    con.close()
    print(f"\nCSVs written to {config.REPORTS}")


if __name__ == "__main__":
    main()
