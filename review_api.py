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

Auth (added for hosting, see docs/HOSTING.md): HTTP Basic, per-writer
credentials read from the MH2_AUTH_USERS environment variable
("user1:pass1,user2:pass2,..."). If that variable is unset -- true for
every local dev/test run today -- auth is a no-op and every route behaves
exactly as before. Set it only in the deployed environment. This matches
rev 9 R1's intended shape (per-writer creds in env vars) and DEFERRED.md
§3.4's ruling not to hand-roll anything fancier than that.

Ladder Files (docs/brief_ladder_uploads.md): /files and /api/ladders/* let
writers upload and replace ladder .docx files. mh2/ladder_store.py owns the
tables and the upload directory; RebuildWorker below runs rebuild.py and
render_static.py after each commit or revert so the upload takes effect.
"""
from __future__ import annotations

import os
import re
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from starlette.background import BackgroundTask

import config
from mh2 import ladder_edits, ladder_store, node_lookup, review_store
from mh2.coverage import GREEN_MATCH, build_rows, build_tags_by_standard

_security = HTTPBasic(auto_error=False)


def _configured_writers() -> dict[str, str]:
    """Parse MH2_AUTH_USERS ("user:pass,user:pass,..."). Empty/unset means
    no writers are configured, which is read below as "auth disabled" --
    deliberate, so every existing test and local `uvicorn --reload` run
    keeps working with zero setup. Only the deployed container sets this."""
    raw = os.environ.get("MH2_AUTH_USERS", "")
    users: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        user, _, pw = pair.partition(":")
        if user and pw:
            users[user] = pw
    return users


def require_writer(
    credentials: HTTPBasicCredentials | None = Depends(_security),
) -> str:
    users = _configured_writers()
    if not users:
        return "local"  # MH2_AUTH_USERS unset -- auth off, dev/test default
    unauthorized = HTTPException(
        status_code=401, detail="Login required",
        headers={"WWW-Authenticate": "Basic"})
    if credentials is None:
        raise unauthorized
    expected = users.get(credentials.username)
    ok = expected is not None and secrets.compare_digest(credentials.password, expected)
    if not ok:
        raise unauthorized
    return credentials.username


@asynccontextmanager
async def _lifespan(_app):
    # Brief §5 step 4: validated-but-never-committed uploads are dropped
    # after 24 h, checked once per start.
    ladder_store.purge_pending()
    yield


app = FastAPI(title="MH2 Review API", dependencies=[Depends(require_writer)],
              lifespan=_lifespan)


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
        statuses = review_store.list_standard_statuses(seq)
    finally:
        seq.close()

    out = []
    for row in rows:
        d = _row_out(row)
        review = reviews.get(row.code)
        override = overrides.get(row.code)
        status = statuses.get(row.code)
        d["review_state"] = review["outcome"] if review else "unreviewed"
        d["effective_color"] = override["writer_color"] if override else row.color
        d["has_override"] = override is not None
        d["writer_status"] = status["status"] if status else None
        d["writer_status_by"] = status["set_by"] if status else None
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
        writer_status = review_store.get_standard_status(seq, standard_id)
        pending = review_store.list_pending_proposals_for_standard(seq, standard_id)
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
            "stem_id": info.stem_id if info else None,
            "ladder_file": info.source_file if info else None,
            "review": tag_reviews.get(source_key) if source_key else None,
        })

    # A proposal is shown beside the real tags until a rebuild puts its node
    # into node_standards. One whose source_key already appears among the
    # tags has landed but not been reconciled yet (--skip-review-reconcile);
    # showing it twice would contradict the ladder, so it is dropped here.
    # Never affects color -- only the ladder does.
    tagged_keys = {t["source_key"] for t in tag_out if t["source_key"]}
    node_by_key = {info.source_key: info for info in node_by_id.values()}
    proposals_out = []
    for p in pending:
        if p["source_key"] in tagged_keys:
            continue
        info = node_by_key.get(p["source_key"])
        proposals_out.append({
            **p,
            "node_text": info.node_text if info else None,
            "stem_id": info.stem_id if info else None,
        })

    return {
        "standard_id": standard_id,
        "color": _color(tags),
        "tags": tag_out,
        "proposals": proposals_out,
        "standard_review": standard_review,
        "override": override,
        "writer_status": writer_status,
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


@app.get("/api/export/ladder-edits.xlsx")
def export_ladder_edits():
    """The ladder keeper's download: every tag to add or remove, as Excel.
    A snapshot derived from current state on each call -- nothing about the
    download itself is recorded (docs/brief_ladder_edits_export.md)."""
    mh2 = _mh2_con()
    seq = _seq_con()
    try:
        edits = ladder_edits.build_ladder_edits(mh2, seq)
        ladders_read = ladder_edits.ladders_last_read(mh2)
    finally:
        mh2.close()
        seq.close()

    today = date.today().isoformat()
    body = ladder_edits.write_xlsx(edits, exported_on=today, ladders_read=ladders_read)
    return Response(
        content=body,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f'attachment; filename="ladder_edits_{today}.xlsx"'})


# ---------------------------------------------------------------------- writes

class StandardReviewIn(BaseModel):
    outcome: Literal["confirmed", "partial_coverage", "not_covered"]
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


class StandardStatusIn(BaseModel):
    status: Literal["review_complete", "in_progress", "not_yet_reviewed"]
    set_by: str


@app.post("/api/standards/{standard_id}/status")
def set_standard_status(standard_id: str, body: StandardStatusIn):
    seq = _seq_con()
    try:
        review_store.set_standard_status(seq, standard_id, body.status, body.set_by)
    finally:
        seq.close()
    return {"standard_id": standard_id, "status": body.status}


class TagReviewIn(BaseModel):
    standard_id: str
    source_key: str
    outcome: Literal["confirmed", "partial_coverage", "incorrect_tag"]
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
    # Snapshot of the node's wording, so the ladder editor can find it even if
    # it is later reworded. Stamped server-side from the node lookup, like
    # app/db.py's write_ruling(); the client value is only a fallback for a
    # source_key the lookup does not know.
    node_text_seen: str | None = None
    proposed_by: str
    rationale: str | None = None


@app.post("/api/proposals")
def create_proposal(body: ProposalIn):
    mh2 = _mh2_con()
    try:
        info = node_lookup.build_source_key_lookup(mh2).get(body.source_key)
    finally:
        mh2.close()

    node_text_seen = info.node_text if info else body.node_text_seen
    if not node_text_seen:
        raise HTTPException(400, f"unknown node {body.source_key!r} and no node_text_seen given")

    seq = _seq_con()
    try:
        if review_store.open_proposal_exists(seq, body.standard_id, body.source_key):
            raise HTTPException(409, "an open proposal for this node already exists")
        proposal_id = review_store.create_tag_proposal(
            seq, body.standard_id, body.source_key, node_text_seen,
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


# ---------------------------------------------------------------- ladder files

class RebuildWorker:
    """
    One background thread that runs rebuild.py then render_static.py after
    ladder uploads (brief §6.3).

    request() sets a dirty flag and wakes the thread. A run clears the flag
    when it STARTS, so an upload landing mid-run sets it again and the loop
    runs once more afterwards: any number of quick uploads collapse into one
    or two rebuilds, and the last one always sees every commit.

    A failed rebuild leaves the previous mh2.db serving -- rebuild.py builds
    beside it and only swaps on success -- so a failure here is reported,
    never retried in a loop. If another process holds the rebuild lock
    (someone running rebuild.py by hand), the run waits and retries instead
    of failing.

    The thread starts on the first request, not at import, so importing this
    module (every test does) never spawns it.
    """

    LOCK_HELD_MARKER = "Another rebuild is already running"
    TAIL_LINES = 50

    def __init__(self, rebuild_cmd: list[str] | None = None,
                 render_cmd: list[str] | None = None,
                 lock_retry_seconds: float = 15.0):
        self.rebuild_cmd = rebuild_cmd or [
            sys.executable, str(config.ROOT / "scripts" / "rebuild.py"),
            "--skip-review-reconcile"]
        self.render_cmd = render_cmd or [
            sys.executable, str(config.ROOT / "scripts" / "render_static.py")]
        self.lock_retry_seconds = lock_retry_seconds
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None
        self._dirty = False
        self._actors: list[str] = []
        self._status = {
            "state": "idle", "actor": None, "started_at": None,
            "waiting_for_lock": False,
            "last_success_at": None, "last_success_actor": None,
            "last_failure_at": None, "failed_step": None, "error_tail": [],
        }

    def request(self, actor: str) -> None:
        with self._cond:
            self._dirty = True
            if actor not in self._actors:
                self._actors.append(actor)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._loop, name="ladder-rebuild", daemon=True)
                self._thread.start()
            self._cond.notify()

    def status(self) -> dict:
        with self._cond:
            out = dict(self._status, pending=self._dirty)
        if out["last_success_at"] is None:
            out["last_success_at"], out["last_success_actor"] = _last_logged_success()
        return out

    def wait_idle(self, timeout: float = 60.0) -> bool:
        """For tests: block until no run is in progress or queued."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._cond:
                if not self._dirty and self._status["state"] != "running":
                    return True
            time.sleep(0.05)
        return False

    def _loop(self) -> None:
        while True:
            with self._cond:
                while not self._dirty:
                    self._cond.wait()
                self._dirty = False
                actor = ", ".join(self._actors) or "unknown"
                self._actors = []
                self._status.update(state="running", actor=actor,
                                    started_at=_utc_now(), waiting_for_lock=False)
            outcome, step, tail = self._run_once(actor)
            with self._cond:
                if outcome == "locked":
                    self._status["waiting_for_lock"] = True
                    self._dirty = True
                    for a in actor.split(", "):
                        if a not in self._actors:
                            self._actors.append(a)
                elif outcome == "ok":
                    self._status.update(
                        state="idle", last_success_at=_utc_now(),
                        last_success_actor=actor, failed_step=None, error_tail=[])
                else:
                    self._status.update(
                        state="failed", last_failure_at=_utc_now(),
                        failed_step=step, error_tail=tail)
            if outcome == "locked":
                time.sleep(self.lock_retry_seconds)

    def _run_once(self, actor: str) -> tuple[str, str | None, list[str]]:
        env = {**os.environ, "MH2_REBUILD_ACTOR": actor}
        for name, cmd in (("rebuild", self.rebuild_cmd), ("render", self.render_cmd)):
            proc = subprocess.run(cmd, cwd=config.ROOT, env=env, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0:
                continue
            output = proc.stdout or ""
            if name == "rebuild" and self.LOCK_HELD_MARKER in output:
                return "locked", None, []
            failed = re.findall(r"^FAILED: (.+)$", output, re.M)
            step = (f"{name}: {failed[-1]}" if failed
                    else f"{name} exited with status {proc.returncode}")
            return "failed", step, output.splitlines()[-self.TAIL_LINES:]
        return "ok", None, []


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _last_logged_success() -> tuple[str | None, str | None]:
    """(timestamp, actor) of the newest 'ok' line in rebuild_log.tsv -- so the
    status survives an app restart, when this process has run nothing yet."""
    path = config.REPORTS / "rebuild_log.tsv"
    if not path.exists():
        return None, None
    last = (None, None)
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) >= 3 and cols[2] == "ok":
            last = (cols[0], cols[1])
    return last


rebuild_worker = RebuildWorker()

LADDER_STATIC = Path(__file__).parent / "ladder_static"


def _ladder_error(exc: ladder_store.LadderUploadError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.code, "message": exc.message})


@app.get("/files")
def ladder_files_page():
    path = LADDER_STATIC / "files.html"
    if not path.exists():
        raise HTTPException(404, "ladder_static/files.html not found")
    return FileResponse(path, media_type="text/html")


@app.get("/api/ladders")
def list_ladders():
    seq = _seq_con()
    try:
        return ladder_store.list_slots(seq)
    finally:
        seq.close()


@app.get("/api/ladders/export.zip")
def export_ladders():
    """Every live ladder plus manifest.csv: the team's backup, and the
    hand-off back to SharePoint. Built in a temp file, not memory (the
    ladders are ~130 MB), and deliberately not on the data/build volume,
    which is 1 GB and already holds mh2.db twice during a rebuild. Deleted
    once sent."""
    fd, tmp = tempfile.mkstemp(prefix=".ladders_export_", suffix=".zip")
    os.close(fd)
    seq = _seq_con()
    try:
        ladder_store.write_export_zip(seq, Path(tmp))
    except BaseException:
        os.unlink(tmp)
        raise
    finally:
        seq.close()
    return FileResponse(tmp, media_type="application/zip",
                        filename=f"mh2_ladders_{date.today().isoformat()}.zip",
                        background=BackgroundTask(os.unlink, tmp))


@app.post("/api/ladders/{stem_id}/validate")
def validate_ladder(stem_id: str, file: UploadFile = File(...),
                    user: str = Depends(require_writer)):
    seq = _seq_con()
    try:
        return ladder_store.validate_upload(
            seq, stem_id, file.filename or "", file.file, user)
    except ladder_store.LadderUploadError as exc:
        raise _ladder_error(exc)
    finally:
        seq.close()


class LadderCommitIn(BaseModel):
    token: str
    note: str | None = None
    # Must be true when validation reported orphaned review decisions; the
    # page's confirmation box sets it.
    confirm_orphans: bool = False


@app.post("/api/ladders/{stem_id}/commit")
def commit_ladder(stem_id: str, body: LadderCommitIn,
                  user: str = Depends(require_writer)):
    seq = _seq_con()
    try:
        upload = ladder_store.commit_upload(
            seq, stem_id, body.token, user, note=(body.note or "").strip() or None,
            confirm_orphans=body.confirm_orphans)
    except ladder_store.LadderUploadError as exc:
        raise _ladder_error(exc)
    finally:
        seq.close()
    rebuild_worker.request(user)
    return {"upload": upload, "rebuild": rebuild_worker.status()}


@app.get("/api/ladders/{stem_id}/file")
def download_live_ladder(stem_id: str):
    seq = _seq_con()
    try:
        found = ladder_store.live_file(seq, stem_id)
    except ladder_store.LadderUploadError as exc:
        raise _ladder_error(exc)
    finally:
        seq.close()
    if found is None:
        raise HTTPException(404, detail={"error": "no_ladder",
                                         "message": f"{stem_id} has no ladder yet"})
    path, name = found
    return FileResponse(path, filename=name, media_type=_DOCX)


@app.get("/api/ladders/{stem_id}/versions")
def ladder_versions(stem_id: str):
    seq = _seq_con()
    try:
        ladder_store.get_slot(stem_id)
        return ladder_store.list_versions(seq, stem_id)
    except ladder_store.LadderUploadError as exc:
        raise _ladder_error(exc)
    finally:
        seq.close()


@app.get("/api/ladders/{stem_id}/versions/{upload_id}/file")
def download_ladder_version(stem_id: str, upload_id: int):
    seq = _seq_con()
    try:
        row = ladder_store.get_version(seq, stem_id, upload_id)
    except ladder_store.LadderUploadError as exc:
        raise _ladder_error(exc)
    finally:
        seq.close()
    stamp = row["uploaded_at"][:10]
    return FileResponse(ladder_store.version_path(row), media_type=_DOCX,
                        filename=f"{stem_id}_v{upload_id}_{stamp}_{row['original_name']}")


@app.post("/api/ladders/{stem_id}/revert/{upload_id}")
def revert_ladder(stem_id: str, upload_id: int, user: str = Depends(require_writer)):
    seq = _seq_con()
    try:
        upload = ladder_store.revert(seq, stem_id, upload_id)
    except ladder_store.LadderUploadError as exc:
        raise _ladder_error(exc)
    finally:
        seq.close()
    rebuild_worker.request(user)
    return {"upload": upload, "rebuild": rebuild_worker.status()}


@app.get("/api/rebuild/status")
def rebuild_status():
    return rebuild_worker.status()


_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
