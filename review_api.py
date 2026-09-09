"""
review_api.py -- FastAPI entry point for the review write path (Session C).

Not `app.py`: this repo already has an `app/` package (the Streamlit
alignment-review tool, db.py/review.py/styles.py/pages/) that this module
does not touch, import from, or read state from. Every SQLite query shape
lives in mh2/coverage.py (read, do-not-touch), mh2/node_lookup.py, and
mh2/review_store.py -- routes below call those and never write SQL
themselves (see the module docstring's own grep-checked acceptance
criterion). All paths returned to the client are relative; nothing here
assumes a fixed loopback host or port, so hosting this behind anything else
is a deploy decision, not a rebuild.

Run: uvicorn review_api:app --reload
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import config
from mh2 import node_lookup, review_store
from mh2.coverage import GREEN_MATCH, build_rows, build_tags_by_standard

app = FastAPI(title="MH2 Review API")


def _mh2_con() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{config.DB}?mode=ro", uri=True)


def _seq_con() -> sqlite3.Connection:
    con = review_store.connect(config.SEQ_DB)
    review_store.ensure_schema(con, config.SCHEMA_SEQ)
    return con


def _color(tags) -> str:
    """Same rollup rule as coverage.StandardRow.color (mh2/reconcile_review.py
    carries an identical copy for the same reason: there is no StandardRow
    to build from a bare tag list)."""
    if not tags:
        return "Red"
    if any(t.grade_match in GREEN_MATCH for t in tags):
        return "Green"
    return "Yellow"


def _row_out(row) -> dict:
    """StandardRow.code -> `standard_id`, to match the mh2_seq.db schema's
    naming (mh2/schema_seq.sql, standard_id everywhere) rather than
    coverage.py's `code` (do-not-touch, so the field itself never moves --
    see the brief's §1.4). Every other name is passed through unchanged."""
    return {
        "standard_id": row.code, "text": row.text, "grade": row.grade,
        "band": row.band, "sheet": row.sheet, "claim": row.claim,
        "stem_id": row.stem_id, "stem_name": row.stem_name,
        "stem_ids": row.stem_ids, "ladder_status": row.ladder_status,
        "color": row.color, "flagged": row.flagged, "reasons": row.reasons,
        "tagged_in_sheet": row.tagged_in_sheet,
    }


# --------------------------------------------------------------- static render

@app.get("/")
def index():
    path = config.REPORTS / "coverage.html"
    if not path.exists():
        raise HTTPException(404, "coverage.html not built yet -- run "
                                  "scripts/render_static.py")
    return FileResponse(path)


# ------------------------------------------------------------------- §6.1 audit

@app.get("/api/audit")
def audit():
    con = _mh2_con()
    try:
        rows = build_rows(con)
    finally:
        con.close()

    seq = _seq_con()
    try:
        reviews = review_store.list_standard_reviews(seq)
        overrides = review_store.list_standard_overrides(seq)
    finally:
        seq.close()

    out = []
    for row in rows:
        d = _row_out(row)
        review = reviews.get(row.code)
        override = overrides.get(row.code)
        d["review_state"] = review["outcome"] if review else "unreviewed"
        d["effective_color"] = override["writer_color"] if override else row.color
        d["has_override"] = override is not None
        out.append(d)
    return out


# ------------------------------------------------------------ §6 detail / tags

@app.get("/api/standards/{standard_id}")
def standard_detail(standard_id: str):
    mh2 = _mh2_con()
    try:
        tags_by_standard = build_tags_by_standard(mh2)
        node_by_id = node_lookup.build_node_lookup(mh2)
    finally:
        mh2.close()

    # build_tags_by_standard only has a key for a standard_id that appears in
    # node_standards -- a Red standard (zero tags) is never a key, so `.get()
    # is None` does not mean "unknown standard_id," it means "no tags," which
    # is the majority of rows (~2,200 of 2,987) and a completely valid state
    # that still needs review/override controls. Treat the missing key as an
    # empty tag list rather than 404ing; _color([]) already resolves that to
    # "Red" correctly.
    tags = tags_by_standard.get(standard_id, [])

    seq = _seq_con()
    try:
        standard_review = review_store.get_standard_review(seq, standard_id)
        override = review_store.get_standard_override(seq, standard_id)
        tag_reviews = review_store.list_tag_reviews_for_standard(seq, standard_id)
    finally:
        seq.close()

    tag_out = []
    for t in tags:
        info = node_by_id.get(t.node_id)
        source_key = info.source_key if info else None
        tag_out.append({
            "node_id": t.node_id, "raw_code": t.raw_code, "tier": t.tier,
            "node_grades": list(t.node_grades), "is_leaf": t.is_leaf,
            "grade_match": t.grade_match, "note": t.note,
            "source_key": source_key,
            "node_text": info.node_text if info else None,
            "ladder_file": info.source_file if info else None,
            "review": tag_reviews.get(source_key) if source_key else None,
        })

    return {
        "standard_id": standard_id,
        "color": _color(tags),
        "tags": tag_out,
        "standard_review": standard_review,
        "override": override,
    }


# ------------------------------------------------------------------ node picker

@app.get("/api/nodes")
def nodes_for_stem(stem_id: str):
    con = _mh2_con()
    try:
        nodes = node_lookup.nodes_for_stem(con, stem_id)
    finally:
        con.close()
    return [
        {"source_key": n.source_key, "node_text": n.node_text,
         "concept_skill": n.concept_skill}
        for n in nodes
    ]


# ------------------------------------------------------------- §6.2 worklist

@app.get("/api/worklist")
def worklist():
    seq = _seq_con()
    try:
        open_proposals = review_store.list_open_proposals(seq)
        overrides = review_store.list_standard_overrides(seq)
    finally:
        seq.close()

    mh2 = _mh2_con()
    try:
        node_by_key = node_lookup.build_source_key_lookup(mh2)
    finally:
        mh2.close()

    def seq_of(p):
        info = node_by_key.get(p["source_key"])
        return info.seq if info and info.seq is not None else float("inf")

    by_file: dict[str, list[dict]] = {}
    for p in sorted(open_proposals, key=seq_of):
        by_file.setdefault(p["ladder_file"] or "(unfiled)", []).append(p)

    return {
        "by_ladder_file": [
            {"ladder_file": f, "proposals": items}
            for f, items in sorted(by_file.items())
        ],
        "standing_overrides": list(overrides.values()),
    }


# ---------------------------------------------------------------------- writes

class StandardReviewIn(BaseModel):
    outcome: Literal["confirmed", "insufficient"]
    reviewed_by: str
    note: str | None = None


@app.post("/api/standards/{standard_id}/review")
def set_standard_review(standard_id: str, body: StandardReviewIn):
    mh2 = _mh2_con()
    try:
        tags = build_tags_by_standard(mh2).get(standard_id, [])
    finally:
        mh2.close()
    computed_color = _color(tags)

    seq = _seq_con()
    try:
        review_store.set_standard_review(
            seq, standard_id, body.outcome, computed_color,
            body.reviewed_by, body.note)
    finally:
        seq.close()
    return {"standard_id": standard_id, "outcome": body.outcome,
            "computed_color_at_review": computed_color}


@app.delete("/api/standards/{standard_id}/review")
def clear_standard_review(standard_id: str):
    seq = _seq_con()
    try:
        review_store.clear_standard_review(seq, standard_id)
    finally:
        seq.close()
    return {"standard_id": standard_id, "cleared": True}


class OverrideIn(BaseModel):
    writer_color: Literal["Green", "Yellow", "Red"]
    reason: str
    set_by: str


@app.post("/api/standards/{standard_id}/override")
def set_override(standard_id: str, body: OverrideIn):
    mh2 = _mh2_con()
    try:
        tags = build_tags_by_standard(mh2).get(standard_id, [])
    finally:
        mh2.close()
    computed_color = _color(tags)

    seq = _seq_con()
    try:
        review_store.set_standard_override(
            seq, standard_id, body.writer_color, computed_color,
            body.reason, body.set_by)
    finally:
        seq.close()
    return {"standard_id": standard_id, "writer_color": body.writer_color,
            "computed_color_at_set": computed_color}


@app.delete("/api/standards/{standard_id}/override")
def retire_override(standard_id: str):
    seq = _seq_con()
    try:
        review_store.retire_standard_override(seq, standard_id)
    finally:
        seq.close()
    return {"standard_id": standard_id, "retired": True}


class TagReviewIn(BaseModel):
    standard_id: str
    source_key: str
    outcome: Literal["confirmed", "insufficient", "wrong_node"]
    reviewed_by: str
    note: str | None = None


@app.post("/api/tags/review")
def set_tag_review(body: TagReviewIn):
    mh2 = _mh2_con()
    try:
        info = node_lookup.build_source_key_lookup(mh2).get(body.source_key)
    finally:
        mh2.close()

    seq = _seq_con()
    try:
        review_store.set_tag_review(
            seq, body.standard_id, body.source_key, body.outcome,
            body.reviewed_by,
            node_id_seen=info.node_id if info else None,
            node_text_seen=info.node_text if info else None,
            ladder_file_seen=info.source_file if info else None,
            note=body.note)
    finally:
        seq.close()
    return {"standard_id": body.standard_id, "source_key": body.source_key,
            "outcome": body.outcome}


@app.delete("/api/tags/review")
def clear_tag_review(standard_id: str, source_key: str):
    seq = _seq_con()
    try:
        review_store.clear_tag_review(seq, standard_id, source_key)
    finally:
        seq.close()
    return {"standard_id": standard_id, "source_key": source_key, "cleared": True}


class ProposalIn(BaseModel):
    standard_id: str
    source_key: str
    node_text_seen: str
    proposed_by: str
    rationale: str | None = None


@app.post("/api/proposals")
def create_proposal(body: ProposalIn):
    mh2 = _mh2_con()
    try:
        info = node_lookup.build_source_key_lookup(mh2).get(body.source_key)
    finally:
        mh2.close()

    seq = _seq_con()
    try:
        proposal_id = review_store.create_tag_proposal(
            seq, body.standard_id, body.source_key, body.node_text_seen,
            body.proposed_by,
            node_id_seen=info.node_id if info else None,
            ladder_file=info.source_file if info else None,
            rationale=body.rationale)
    finally:
        seq.close()
    return {"proposal_id": proposal_id}


class WithdrawIn(BaseModel):
    resolved_note: str | None = None


@app.post("/api/proposals/{proposal_id}/withdraw")
def withdraw_proposal(proposal_id: int, body: WithdrawIn = WithdrawIn()):
    seq = _seq_con()
    try:
        if review_store.get_tag_proposal(seq, proposal_id) is None:
            raise HTTPException(404, f"no proposal #{proposal_id}")
        review_store.withdraw_tag_proposal(seq, proposal_id, body.resolved_note)
    finally:
        seq.close()
    return {"proposal_id": proposal_id, "withdrawn": True}
