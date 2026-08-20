"""
reconcile.py — find drift between the stem/leaf workbook and the ladder docs.

The workbook is where tagging starts. Ladder drafting changes things. Nobody
is confident the changes flow back. This produces the diff.

Three tiers, deliberately separated by how much you should trust them:

  TIER 1  stem-level standard drift        EXACT. No text matching involved.
          For a stem, compare the set of standards tagged in the workbook
          against the set tagged in the ladder. Set arithmetic only, so a
          difference here is a real difference, not a matching artifact.

  TIER 2  cross-stem relocations           EXACT. A standard tagged under
          stem A in the workbook and stem B in the ladder. These are the
          highest-value findings: somebody moved a standard during drafting
          and the workbook still points at the old home. The workbook's own
          prose notes ("All orange standards were moved to applying
          properties row") confirm this happens and is tracked by hand.

  TIER 3  concept <-> node mapping         FUZZY. Advisory only. Concept text
          and node text are worded differently on purpose, so matching is a
          judgement call. Uses token overlap + sequence ratio here; swap in
          the fine-tuned SentenceTransformers model for the real thing.

Findings are written to the `drift` table and to CSVs.

Run: python reconcile.py --db mh2.db --out drift/
"""

import argparse
import csv
import re
import sqlite3
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

# The ladder -> workbook stem mapping used to live here as a hand-maintained
# dict. It now lives in stems.csv and is loaded into stem_map (see
# mh2/load_stems.py). It is still hand-maintained and still must never be
# inferred -- it only moved from Python into a data file the team can edit.

STOP = {
    "a", "an", "the", "to", "of", "and", "or", "in", "on", "by", "with", "for",
    "from", "as", "is", "are", "be", "using", "use", "that", "this", "up",
    "given", "within", "than", "at", "least", "how", "many",
}


def tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-z]+", (text or "").lower()) if w not in STOP and len(w) > 2}


def similarity(a: str, b: str) -> float:
    """Token Jaccard blended with sequence ratio. Placeholder for the real model."""
    ta, tb = tokens(a), tokens(b)
    jac = len(ta & tb) / len(ta | tb) if (ta or tb) else 0.0
    seq = SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()
    return round(0.7 * jac + 0.3 * seq, 3)


def ensure_drift_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS drift (
            drift_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            tier        TEXT,
            kind        TEXT,
            stem_id     TEXT,
            other_stem  TEXT,
            standard_id TEXT,
            concept_id  INTEGER,
            node_id     TEXT,
            score       REAL,
            detail      TEXT,
            status      TEXT DEFAULT 'open'
        )""")
    cur.execute("DELETE FROM drift")


def build_stem_groups(cur) -> dict:
    """
    ladder stem_id -> the workbook stem_ids it should be compared against.

    Read from stem_map, never inferred. The route is nodes.source_file ->
    stem_map.ladder_path -> every stem_map row naming that same ladder ->
    their workbook_stem_id. That is what makes the Comparing and Ordering
    ladder compare against BOTH 'Comparing' and 'Ordering': two stem_map rows
    share one ladder file, by design.
    """
    ladder_to_workbook = defaultdict(list)
    for ladder_path, wb_id in cur.execute(
            "SELECT ladder_path, workbook_stem_id FROM stem_map"
            " WHERE ladder_path IS NOT NULL AND workbook_stem_id IS NOT NULL"):
        ladder_to_workbook[Path(ladder_path).name].append(wb_id)

    groups = {}
    for sid, source_file in cur.execute(
            "SELECT DISTINCT stem_id, source_file FROM nodes"):
        wb_stems = ladder_to_workbook.get(source_file or "")
        # Falling back to the ladder's own stem_id keeps a ladder that is not
        # yet listed in stems.csv comparable with itself rather than silently
        # dropping it from the drift report.
        groups.setdefault(sid, []).extend(wb_stems or [sid])

    return {sid: sorted(set(v)) for sid, v in groups.items()}


def tier1_standard_drift(cur, groups) -> list:
    rows = []
    for ladder_stem, wb_stems in groups.items():
        placeholders = ",".join("?" * len(wb_stems))
        wb = {r[0]: r[1] for r in cur.execute(
            f"""SELECT cs.standard_id, GROUP_CONCAT(DISTINCT co.text)
                FROM concept_standards cs JOIN concepts co USING(concept_id)
                WHERE co.stem_id IN ({placeholders}) GROUP BY 1""", wb_stems)}
        ld = {r[0]: r[1] for r in cur.execute(
            """SELECT ns.standard_id, GROUP_CONCAT(DISTINCT n.node_text)
               FROM node_standards ns JOIN nodes n USING(node_id)
               WHERE n.stem_id = ? GROUP BY 1""", (ladder_stem,))}

        for code in sorted(set(wb) - set(ld)):
            rows.append(("tier1", "workbook_only", ladder_stem, None, code,
                         None, None, None, f"tagged to concept: {wb[code][:120]}"))
        for code in sorted(set(ld) - set(wb)):
            rows.append(("tier1", "ladder_only", ladder_stem, None, code,
                         None, None, None, f"tagged to node: {ld[code][:120]}"))
    return rows


def tier2_relocations(cur, groups) -> list:
    """Standard tagged under one stem in the workbook, a different one in a ladder."""
    wb_home = defaultdict(set)
    for code, sid in cur.execute(
            "SELECT cs.standard_id, co.stem_id FROM concept_standards cs "
            "JOIN concepts co USING(concept_id)"):
        wb_home[code].add(sid)

    ld_home = defaultdict(set)
    for code, sid in cur.execute(
            "SELECT ns.standard_id, n.stem_id FROM node_standards ns "
            "JOIN nodes n USING(node_id)"):
        ld_home[code].add(sid)

    # Expand each ladder stem to the workbook stems it legitimately covers,
    # so the Comparing/Ordering merge is not reported as a relocation.
    rows = []
    for code, ld_stems in ld_home.items():
        if code not in wb_home:
            continue
        allowed = set()
        for s in ld_stems:
            allowed.update(groups.get(s, [s]))
            allowed.add(s)
        moved_from = wb_home[code] - allowed
        if moved_from and not (wb_home[code] & allowed):
            rows.append(("tier2", "relocated", ",".join(sorted(ld_stems)),
                         ",".join(sorted(moved_from)), code, None, None, None,
                         "standard's workbook stem differs from every ladder stem it appears in"))
    return rows


def tier3_concept_node(cur, groups, threshold=0.35) -> list:
    rows = []
    for ladder_stem, wb_stems in groups.items():
        placeholders = ",".join("?" * len(wb_stems))
        concepts = cur.execute(
            f"SELECT concept_id, text FROM concepts WHERE stem_id IN ({placeholders})",
            wb_stems).fetchall()
        nodes = cur.execute(
            "SELECT node_id, node_text, concept_skill FROM nodes WHERE stem_id = ?",
            (ladder_stem,)).fetchall()
        if not concepts or not nodes:
            continue

        used_nodes, used_concepts = set(), set()
        scored = []
        for cid, ctext in concepts:
            for nid, ntext, cskill in nodes:
                s = max(similarity(ctext, ntext), similarity(ctext, cskill or ""))
                scored.append((s, cid, ctext, nid, ntext))
        scored.sort(reverse=True)

        for s, cid, ctext, nid, ntext in scored:
            if s < threshold or cid in used_concepts or nid in used_nodes:
                continue
            used_concepts.add(cid)
            used_nodes.add(nid)
            rows.append(("tier3", "matched", ladder_stem, None, None, cid, nid, s,
                         f"{ctext[:70]}  ||  {ntext[:70]}"))

        for cid, ctext in concepts:
            if cid not in used_concepts:
                rows.append(("tier3", "concept_no_node", ladder_stem, None, None,
                             cid, None, None, ctext[:140]))
        for nid, ntext, _ in nodes:
            if nid not in used_nodes:
                rows.append(("tier3", "node_no_concept", ladder_stem, None, None,
                             None, nid, None, ntext[:140]))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="mh2.db")
    ap.add_argument("--out", default="drift")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    ensure_drift_table(cur)
    groups = build_stem_groups(cur)

    all_rows = tier1_standard_drift(cur, groups) \
        + tier2_relocations(cur, groups) \
        + tier3_concept_node(cur, groups)

    cur.executemany(
        "INSERT INTO drift (tier, kind, stem_id, other_stem, standard_id,"
        " concept_id, node_id, score, detail) VALUES (?,?,?,?,?,?,?,?,?)", all_rows)
    con.commit()

    outdir = Path(args.out)
    outdir.mkdir(exist_ok=True)
    for tier in ("tier1", "tier2", "tier3"):
        rows = cur.execute(
            "SELECT kind, stem_id, other_stem, standard_id, concept_id, node_id,"
            " score, detail FROM drift WHERE tier=? ORDER BY kind, stem_id", (tier,)).fetchall()
        with open(outdir / f"{tier}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["kind", "stem_id", "other_stem", "standard_id",
                        "concept_id", "node_id", "score", "detail"])
            w.writerows(rows)

    print("drift findings:")
    for tier, kind, n in cur.execute(
            "SELECT tier, kind, COUNT(*) FROM drift GROUP BY 1,2 ORDER BY 1,3 DESC"):
        print(f"  {tier}  {kind:18s} {n:5d}")
    con.close()


if __name__ == "__main__":
    main()
