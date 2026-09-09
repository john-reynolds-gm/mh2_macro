"""
reconcile_review.py -- reconcile mh2_seq.db's review state against a freshly
rebuilt mh2.db.

This is a different reconcile from mh2.reconcile (workbook-vs-ladder drift,
the `drift` table): this one asks whether human review decisions recorded in
mh2_seq.db still describe the ladders as they now stand. Runs after
mh2.reconcile in scripts/rebuild.py, under its own `--skip-review-reconcile`
flag so the two never share a log label.

Reads mh2.db read-only (opened with mode=ro -- a structural guarantee, not
just a convention, that this pass cannot write there) and mh2_seq.db
read-write. Writes only mh2_seq.db (tag_proposal.state transitions) and a
report file under config.REPORTS. Never touches standard_review or
tag_review rows -- see §5.2/§5.3: those are reported, never modified.

Run: python -m mh2.reconcile_review --db mh2.db --seq-db mh2_seq.db --out reports/
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from mh2 import node_lookup, review_store  # noqa: E402
from mh2.coverage import GREEN_MATCH, build_tags_by_standard  # noqa: E402


def _color(tags) -> str:
    """Same rollup rule as coverage.StandardRow.color, applied to a Tag list
    directly -- reimplemented rather than imported because there is no
    StandardRow to build here, just the tag set for one standard."""
    if not tags:
        return "Red"
    if any(t.grade_match in GREEN_MATCH for t in tags):
        return "Green"
    return "Yellow"


def reconcile_proposals(mh2_con: sqlite3.Connection, seq_con: sqlite3.Connection,
                         tags_by_standard: dict, node_by_id: dict, node_by_key: dict) -> list[dict]:
    """§5.1. Resolves each open proposal's (standard_id, source_key) against
    the CURRENT ladders, through the same alias-resolved tag set the rollup
    itself uses (tags_by_standard, from coverage.build_tags_by_standard) --
    so a proposal never reads as unlanded just because the ladder spelled
    the code differently."""
    events = []
    for proposal in review_store.list_open_proposals(seq_con):
        standard_id = proposal["standard_id"]
        source_key = proposal["source_key"]
        tags = tags_by_standard.get(standard_id, [])

        info = node_by_key.get(source_key)
        if info is None:
            review_store.update_proposal_state(
                seq_con, proposal["proposal_id"], "needs_attention",
                resolved_note="source_key no longer present in nodes -- node"
                              " reworded or removed")
            events.append({"kind": "needs_attention", "proposal": proposal})
            continue

        tag_node_ids = {t.node_id for t in tags}
        if info.node_id in tag_node_ids:
            before = _color([t for t in tags if t.node_id != info.node_id])
            after = _color(tags)
            review_store.update_proposal_state(
                seq_con, proposal["proposal_id"], "landed",
                resolved_note=f"color {before} -> {after}")
            events.append({"kind": "landed", "proposal": proposal,
                           "before_color": before, "after_color": after})
            continue

        same_stem = any(
            (t.node_id in node_by_id and node_by_id[t.node_id].stem_id == info.stem_id)
            for t in tags
        )
        if same_stem:
            review_store.update_proposal_state(
                seq_con, proposal["proposal_id"], "landed_elsewhere",
                resolved_note="standard is now tagged on a different node in"
                              " the same stem")
            events.append({"kind": "landed_elsewhere", "proposal": proposal})
            continue

        events.append({"kind": "open", "proposal": proposal})
    return events


def reconcile_tag_reviews(seq_con: sqlite3.Connection, node_by_key: dict) -> list[dict]:
    """§5.2. Never modifies or deletes a tag_review row -- only reports the
    ones whose source_key has gone missing from `nodes`."""
    changed = []
    for row in review_store.list_all_tag_reviews(seq_con):
        if row["source_key"] not in node_by_key:
            changed.append(row)
    return changed


def reconcile_standard_reviews(seq_con: sqlite3.Connection, tags_by_standard: dict) -> list[dict]:
    """§5.3 / FLAG 3. Reports staleness; never modifies the row."""
    stale = []
    for standard_id, row in review_store.list_standard_reviews(seq_con).items():
        now_color = _color(tags_by_standard.get(standard_id, []))
        if now_color != row["computed_color_at_review"]:
            stale.append({"standard_id": standard_id,
                          "reviewed_color": row["computed_color_at_review"],
                          "now_color": now_color, "row": row})
    return stale


def reconcile_overrides(seq_con: sqlite3.Connection, tags_by_standard: dict) -> dict:
    """§5.4. Three disjoint outcomes; never modifies a row."""
    redundant, conflicts = [], []
    for standard_id, row in review_store.list_standard_overrides(seq_con).items():
        now_color = _color(tags_by_standard.get(standard_id, []))
        if now_color == row["writer_color"]:
            redundant.append({"standard_id": standard_id, "now_color": now_color, "row": row})
        elif now_color == row["computed_color_at_set"]:
            continue  # stands, no report
        else:
            conflicts.append({"standard_id": standard_id, "now_color": now_color, "row": row})
    return {"redundant": redundant, "conflicts": conflicts}


def _write_report(out_path: Path, proposal_events, changed_tag_reviews,
                   stale_standard_reviews, override_findings) -> None:
    lines = []

    landed = [e for e in proposal_events if e["kind"] == "landed"]
    landed_elsewhere = [e for e in proposal_events if e["kind"] == "landed_elsewhere"]
    needs_attention = [e for e in proposal_events if e["kind"] == "needs_attention"]
    still_open = [e for e in proposal_events if e["kind"] == "open"]

    lines.append("=== tag_proposal: landed (closed, with before/after color) ===")
    lines.append(f"{len(landed)} rows")
    for e in landed:
        p = e["proposal"]
        lines.append(f"  #{p['proposal_id']:<6d} {p['standard_id']:20s} "
                     f"{p['source_key']:40s} {e['before_color']} -> {e['after_color']}")

    lines.append("")
    lines.append("=== tag_proposal: landed_elsewhere (coverage achieved, node differs) ===")
    lines.append(f"{len(landed_elsewhere)} rows")
    for e in landed_elsewhere:
        p = e["proposal"]
        lines.append(f"  #{p['proposal_id']:<6d} {p['standard_id']:20s} {p['source_key']}")

    lines.append("")
    lines.append("=== tag_proposal: needs_attention (source_key reworded or removed) ===")
    lines.append(f"{len(needs_attention)} rows")
    for e in needs_attention:
        p = e["proposal"]
        lines.append(f"  #{p['proposal_id']:<6d} {p['standard_id']:20s} {p['source_key']}")

    lines.append("")
    lines.append("=== tag_proposal: still open (outstanding work) ===")
    lines.append(f"{len(still_open)} rows")
    for e in still_open:
        p = e["proposal"]
        lines.append(f"  #{p['proposal_id']:<6d} {p['standard_id']:20s} {p['source_key']}")

    lines.append("")
    lines.append("=== tag_review: referring to changed content (source_key absent"
                  " from nodes) -- never auto-deleted ===")
    lines.append(f"{len(changed_tag_reviews)} rows")
    for r in changed_tag_reviews:
        lines.append(f"  {r['standard_id']:20s} {r['source_key']:40s} "
                     f"ladder_file={r['ladder_file_seen']!r} node_text={r['node_text_seen']!r}")

    lines.append("")
    lines.append("=== standard_review: may be stale (rollup color changed since review) ===")
    lines.append(f"{len(stale_standard_reviews)} rows")
    for r in stale_standard_reviews:
        lines.append(f"  {r['standard_id']:20s} reviewed_as={r['reviewed_color']:8s} "
                     f"now={r['now_color']}")

    lines.append("")
    lines.append("=== standard_color_override: redundant (computed now equals writer_color)"
                  " -- offered for retirement, not auto-retired ===")
    lines.append(f"{len(override_findings['redundant'])} rows")
    for r in override_findings["redundant"]:
        lines.append(f"  {r['standard_id']:20s} writer_color={r['row']['writer_color']:8s} "
                     f"now={r['now_color']}")

    lines.append("")
    lines.append("=== standard_color_override: conflict (computed changed, still != writer_color)"
                  " -- needs human attention ===")
    lines.append(f"{len(override_findings['conflicts'])} rows")
    for r in override_findings["conflicts"]:
        row = r["row"]
        lines.append(f"  {r['standard_id']:20s} writer_color={row['writer_color']:8s} "
                     f"set_at_computed={row['computed_color_at_set']:8s} now={r['now_color']}")

    out_path.write_text("\n".join(lines), encoding="utf-8")


def run(mh2_db_path: Path, seq_db_path: Path, out_dir: Path) -> Path:
    mh2_con = sqlite3.connect(f"file:{mh2_db_path}?mode=ro", uri=True)
    seq_con = review_store.connect(seq_db_path)
    review_store.ensure_schema(seq_con, config.SCHEMA_SEQ)

    tags_by_standard = build_tags_by_standard(mh2_con)
    node_by_id = node_lookup.build_node_lookup(mh2_con)
    node_by_key = node_lookup.build_source_key_lookup(mh2_con)

    proposal_events = reconcile_proposals(mh2_con, seq_con, tags_by_standard,
                                           node_by_id, node_by_key)
    changed_tag_reviews = reconcile_tag_reviews(seq_con, node_by_key)
    stale_standard_reviews = reconcile_standard_reviews(seq_con, tags_by_standard)
    override_findings = reconcile_overrides(seq_con, tags_by_standard)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "review_reconcile.txt"
    _write_report(out_path, proposal_events, changed_tag_reviews,
                  stale_standard_reviews, override_findings)

    landed_n = sum(1 for e in proposal_events if e["kind"] == "landed")
    print(f"proposals: {landed_n} landed, "
          f"{sum(1 for e in proposal_events if e['kind'] == 'landed_elsewhere')} landed_elsewhere, "
          f"{sum(1 for e in proposal_events if e['kind'] == 'needs_attention')} needs_attention, "
          f"{sum(1 for e in proposal_events if e['kind'] == 'open')} still open")
    print(f"tag_review referring to changed content: {len(changed_tag_reviews)}")
    print(f"standard_review possibly stale: {len(stale_standard_reviews)}")
    print(f"overrides redundant: {len(override_findings['redundant'])}  "
          f"conflicts: {len(override_findings['conflicts'])}")
    print(f"-> wrote {out_path}")

    mh2_con.close()
    seq_con.close()
    return out_path


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(config.DB))
    ap.add_argument("--seq-db", default=str(config.SEQ_DB))
    ap.add_argument("--out", default=str(config.REPORTS))
    args = ap.parse_args(argv)

    run(Path(args.db), Path(args.seq_db), Path(args.out))


if __name__ == "__main__":
    main()
