"""
seq_reconcile.py -- derived placement status (pure) and the report CLI.

Contract: docs/seq_v1_contract.md §3.6; data model §6.  Status is DERIVED at
read time and never stored (entrypoint.sh rebuilds with
--skip-review-reconcile, so a stored status would be stale by construction).
The CLI writes a report file only, never rows, and opens both databases
read-only (mode=ro).

Run: python -m mh2.seq_reconcile [--db PATH] [--seq-db PATH] [--out PATH]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - direct script run
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

EXEMPT_TRANSITIONS = {("unknown", "core"), ("unknown", "span"), ("unknown", "unconfirmed"),
                      ("unconfirmed", "core"), ("unconfirmed", "span")}


def derive_status(placement_row: dict, facts: dict | None) -> dict:
    """PURE. facts None -> 'orphaned'. Else state_now = facts['state'];
    state_now != grade_kind_seen and (seen, now) not in EXEMPT_TRANSITIONS
    -> 'grade_changed'; else 'ok'. `relabelled` (only when not orphaned) =
    concept_skill != concept_skill_seen or source_file != ladder_file_seen.
    A NULL *_seen value is never a relabel (nothing was captured to compare)."""
    if facts is None:
        return {"status": "orphaned", "state_now": None, "relabelled": False,
                "relabel": None}
    seen = placement_row.get("grade_kind_seen")
    now = facts.get("state")
    if now != seen and (seen, now) not in EXEMPT_TRANSITIONS:
        status = "grade_changed"
    else:
        status = "ok"
    cs_seen = placement_row.get("concept_skill_seen")
    file_seen = placement_row.get("ladder_file_seen")
    cs_now = facts.get("concept_skill")
    file_now = facts.get("source_file")
    relabelled = ((cs_seen is not None and cs_now != cs_seen)
                  or (file_seen is not None and file_now != file_seen))
    relabel = None
    if relabelled:
        relabel = {"concept_skill_seen": cs_seen, "concept_skill_now": cs_now,
                   "ladder_file_seen": file_seen, "ladder_file_now": file_now}
    return {"status": status, "state_now": now, "relabelled": relabelled,
            "relabel": relabel}


# --------------------------------------------------------------------- report

def _ro(path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def build_report(mh2_con, seq_con) -> str:
    from mh2 import seq_read, seq_store

    sequences = [dict(r) for r in seq_con.execute(
        "SELECT * FROM grade_sequence WHERE archived_at IS NULL ORDER BY grade, sequence_id")]
    orphaned, changed, relabelled = [], [], []
    per_stem = defaultdict(lambda: [0, 0])      # stem -> [n placements, n orphaned]
    n_total = 0
    for seq in sequences:
        tree = seq_store.load_tree(seq_con, seq["sequence_id"])
        placements = [(m, s, p) for m in tree["modules"] for s in m["slots"]
                      for p in s["placements"]]
        keys = {p["source_key"] for _, _, p in placements}
        facts = seq_read.node_facts(mh2_con, seq["grade"], keys) if keys else {}
        for m, s, p in placements:
            n_total += 1
            info = derive_status(p, facts.get(p["source_key"]))
            stem = p.get("stem_id_seen") or "?"
            per_stem[stem][0] += 1
            where = (f"G{seq['grade']} '{seq['title']}' / M{m['position']} {m['title']}"
                     f" / slot {s['position']}")
            if info["status"] == "orphaned":
                per_stem[stem][1] += 1
                sug = seq_read.suggest_successors(
                    mh2_con, source_key_seen=p["source_key"],
                    node_text_seen=p["node_text_seen"], stem_id_seen=p.get("stem_id_seen"),
                    concept_skill_seen=p.get("concept_skill_seen"), exclude_keys=keys)
                orphaned.append((where, p, sug))
            elif info["status"] == "grade_changed":
                changed.append((where, p, info))
            if info["relabelled"]:
                relabelled.append((where, p, info))
    out = ["placement_reconcile -- derived status of active placements vs current mh2.db",
           f"sequences: {len(sequences)}    active placements: {n_total}",
           f"orphaned: {len(orphaned)}    grade_changed: {len(changed)}"
           f"    relabelled: {len(relabelled)}", ""]
    out.append("== orphaned ==")
    for where, p, sug in orphaned:
        out.append(f"  {where}")
        out.append(f"    was {p['node_id_seen']}: {p['node_text_seen']!r}")
        out.append(f"    ladder file: {p['ladder_file_seen']}")
        if sug:
            top = sug[0]
            out.append(f"    top suggestion ({top['reason']}, {top['ratio']}): "
                       f"{top['node_id']} {top['node_text']!r}")
        else:
            out.append("    no suggestion -- deleted?")
    out += ["", "== grade_changed =="]
    for where, p, info in changed:
        out.append(f"  {where}: {p['node_id_seen']} {p['grade_kind_seen']} -> {info['state_now']}")
    out += ["", "== relabelled =="]
    for where, p, info in relabelled:
        r = info["relabel"]
        out.append(f"  {where}: {p['node_id_seen']}")
        if r["concept_skill_seen"] != r["concept_skill_now"]:
            out.append(f"    concept/skill was {r['concept_skill_seen']!r}, now {r['concept_skill_now']!r}")
        if r["ladder_file_seen"] != r["ladder_file_now"]:
            out.append(f"    ladder file was {r['ladder_file_seen']!r}, now {r['ladder_file_now']!r}")
    out += ["", "== orphan ratio by stem =="]
    for stem in sorted(per_stem):
        n, o = per_stem[stem]
        if o:
            tail = "  -- check ingest before acting" if o == n else ""
            out.append(f"  stem {stem}: {o} of {n} placements orphaned{tail}")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    import config
    from mh2 import seq_read

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(config.DB))
    ap.add_argument("--seq-db", default=str(config.SEQ_DB))
    ap.add_argument("--out", default=str(config.REPORTS / "placement_reconcile.txt"))
    args = ap.parse_args(argv)
    out = Path(args.out)
    if out.is_dir() or not out.suffix:
        out = out / "placement_reconcile.txt"
    mh2_con = seq_read.connect_ro(args.db)
    seq_con = _ro(args.seq_db)
    try:
        has = seq_con.execute("SELECT 1 FROM sqlite_master WHERE name='grade_sequence'").fetchone()
        if not has:
            print("no placement tables in", args.seq_db, "-- nothing to report")
            text = "placement_reconcile -- no placement tables yet\n"
        else:
            text = build_report(mh2_con, seq_con)
    finally:
        mh2_con.close()
        seq_con.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
