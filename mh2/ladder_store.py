"""
ladder_store.py -- writer-uploaded ladder files (docs/brief_ladder_uploads.md).

Owns every query shape against the ladder_upload, stem_registry_override and
ladder_upload_rejection tables in mh2_seq.db, and every file under
config.LADDER_UPLOADS. review_api.py routes call these functions and never
write SQL or touch the upload directory themselves -- the same split as
review_store.py.

Precedence (brief §3): for any stem that has an upload, the uploaded file is
the source of truth. The repo copy in config.LADDERS is only a seed for stems
that have never been uploaded; a newer ladder committed to the repo does NOT
override an existing upload.

Storage:

    LADDER_UPLOADS/<stem_id>/<YYYYMMDDTHHMMSSZ>__<sha256[:8]>.docx
        every committed version; nothing is ever overwritten.
    LADDER_UPLOADS/_pending/<token>.docx + <token>.json
        a validated upload awaiting commit; purged after 24 h.

An upload takes effect only when a rebuild runs: rebuild.py calls
assemble_effective_inputs(), which lays the live uploads over the repo
ladders in data/build/_effective and points every pipeline step there.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

import config
from mh2 import review_store
from mh2.load_stems import (FIELDS, ladder_paths, normalize_ladder_filename)

MAX_BYTES = 20 * 1024 * 1024
PENDING_DIR_NAME = "_pending"
PENDING_MAX_AGE_SECONDS = 24 * 60 * 60
REMOVED_TEXT_LIMIT = 10
_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


class LadderUploadError(Exception):
    """A refusal the route turns into an HTTP error. `status` is the HTTP
    status, `code` a stable machine-readable reason."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def uploads_dir() -> Path:
    # Read at call time, not import time, so tests can repoint config.
    return config.LADDER_UPLOADS


def pending_dir() -> Path:
    return uploads_dir() / PENDING_DIR_NAME


def connect() -> sqlite3.Connection:
    con = review_store.connect(config.SEQ_DB)
    review_store.ensure_schema(con, config.SCHEMA_SEQ)
    return con


# ------------------------------------------------------------------ registry

@dataclass
class Slot:
    """One distinct stem_id from stems.csv. A stem_id can span several rows
    (one ladder over several workbook stems -- load_stems' docstring), so
    workbook_stems is a list."""
    stem_id: str
    band: str
    stem_group: str
    masterlist_name: str
    declared_file: str            # stems.csv ladder_file; "" when undrafted
    workbook_stems: list[str] = field(default_factory=list)


def _read_raw_stems(path: Path) -> tuple[list[str], list[dict]]:
    """stems.csv verbatim: (fieldnames, rows). Values are not coerced so the
    effective copy can be written back out unchanged."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        return list(reader.fieldnames or []), rows


def slots(stems_csv: Path | None = None) -> dict[str, Slot]:
    """stem_id -> Slot, in stems.csv order, from the REPO stems.csv (never
    the effective copy -- overrides are applied by the caller that needs
    them)."""
    _, rows = _read_raw_stems(stems_csv or config.STEMS_CSV)
    out: dict[str, Slot] = {}
    for r in rows:
        sid = (r.get("stem_id") or "").strip()
        if not sid:
            continue
        s = out.get(sid)
        if s is None:
            s = out[sid] = Slot(sid, r.get("band") or "", r.get("stem_group") or "",
                                r.get("masterlist_name") or sid,
                                (r.get("ladder_file") or "").strip())
        if not s.declared_file and (r.get("ladder_file") or "").strip():
            s.declared_file = r["ladder_file"].strip()
        if r.get("workbook_stem"):
            s.workbook_stems.append(r["workbook_stem"])
    return out


def get_slot(stem_id: str) -> Slot:
    s = slots().get(stem_id)
    if s is None:
        raise LadderUploadError(404, "unknown_stem",
                                f"{stem_id!r} is not a stem_id in stems.csv")
    return s


def repo_files_by_name(ladder_dir: Path | None = None) -> dict[str, Path]:
    """normalized filename -> repo ladder. Word lock files excluded."""
    out: dict[str, Path] = {}
    for p in ladder_paths(ladder_dir or config.LADDERS):
        out.setdefault(normalize_ladder_filename(p.name), p)
    return out


def repo_file(slot: Slot, by_name: dict[str, Path] | None = None) -> Path | None:
    """The repo copy a drafted slot resolves to, on the same normalized
    filename rule load_stems.resolve_ladder_files uses. None if undrafted or
    missing on disk (tolerant here -- the rebuild is what fails loudly)."""
    if not slot.declared_file:
        return None
    by_name = repo_files_by_name() if by_name is None else by_name
    return by_name.get(normalize_ladder_filename(slot.declared_file))


def get_override(con: sqlite3.Connection, stem_id: str) -> dict | None:
    row = con.execute("SELECT * FROM stem_registry_override WHERE stem_id = ?",
                      (stem_id,)).fetchone()
    return dict(row) if row else None


def list_overrides(con: sqlite3.Connection) -> dict[str, dict]:
    return {r["stem_id"]: dict(r)
            for r in con.execute("SELECT * FROM stem_registry_override")}


def canonical_name(slot: Slot, override: dict | None = None,
                   by_name: dict[str, Path] | None = None) -> str:
    """
    The filename a slot's live ladder is saved and ingested under (§4.3).

    Drafted slot: the repo file's actual on-disk name when one resolves, else
    the declared ladder_file. The on-disk name, not the declared string,
    because six of the sixteen drafted stem_ids declare a spelling that only
    matches after normalization ('..._Measurement_and_Data_Angles.docx' vs
    '..._Measurement and Data_Angles.docx', '..._Fractions__5_.docx'), and
    ingest stores the filename verbatim in nodes.source_file -- which
    tag_proposal.ladder_file and the worklist then group by. Switching the
    spelling on upload would split one ladder's proposals into two groups.

    Undrafted slot: the override's name if registered, else
    MH2_<band>_<stem_id>.docx. The writer's own filename is never used.
    """
    if slot.declared_file:
        p = repo_file(slot, by_name)
        return p.name if p else slot.declared_file
    if override:
        return override["ladder_file"]
    return f"MH2_{slot.band}_{slot.stem_id}.docx"


# ------------------------------------------------------------------- uploads

def live_uploads(con: sqlite3.Connection) -> dict[str, dict]:
    return {r["stem_id"]: dict(r) for r in con.execute(
        "SELECT * FROM ladder_upload WHERE is_live = 1")}


def get_live_upload(con: sqlite3.Connection, stem_id: str) -> dict | None:
    row = con.execute("SELECT * FROM ladder_upload WHERE stem_id = ? AND is_live = 1",
                      (stem_id,)).fetchone()
    return dict(row) if row else None


def list_versions(con: sqlite3.Connection, stem_id: str) -> list[dict]:
    out = []
    for r in con.execute("SELECT * FROM ladder_upload WHERE stem_id = ?"
                         " ORDER BY upload_id DESC", (stem_id,)):
        d = dict(r)
        d["parse_issues"] = json.loads(d["parse_issues"] or "[]")
        out.append(d)
    return out


def get_version(con: sqlite3.Connection, stem_id: str, upload_id: int) -> dict:
    row = con.execute("SELECT * FROM ladder_upload WHERE stem_id = ? AND upload_id = ?",
                      (stem_id, upload_id)).fetchone()
    if row is None:
        raise LadderUploadError(404, "unknown_version",
                                f"{stem_id} has no upload #{upload_id}")
    d = dict(row)
    d["parse_issues"] = json.loads(d["parse_issues"] or "[]")
    return d


def version_path(row: dict) -> Path:
    return uploads_dir() / row["stored_path"]


def version_counts(con: sqlite3.Connection) -> dict[str, int]:
    return dict(con.execute(
        "SELECT stem_id, COUNT(*) FROM ladder_upload GROUP BY stem_id").fetchall())


def get_rejection(con: sqlite3.Connection, stem_id: str) -> dict | None:
    row = con.execute("SELECT * FROM ladder_upload_rejection WHERE stem_id = ?",
                      (stem_id,)).fetchone()
    return dict(row) if row else None


def list_rejections(con: sqlite3.Connection) -> dict[str, dict]:
    return {r["stem_id"]: dict(r)
            for r in con.execute("SELECT * FROM ladder_upload_rejection")}


def _record_rejection(con, stem_id: str, original_name: str | None,
                      reason: str, by: str) -> None:
    con.execute(
        "INSERT INTO ladder_upload_rejection (stem_id, original_name, reason,"
        " attempted_by, attempted_at) VALUES (?,?,?,?,?)"
        " ON CONFLICT(stem_id) DO UPDATE SET original_name=excluded.original_name,"
        " reason=excluded.reason, attempted_by=excluded.attempted_by,"
        " attempted_at=excluded.attempted_at",
        (stem_id, original_name, reason, by, _iso(_now())))
    con.commit()


# ---------------------------------------------------------------- validation

def _live_nodes(mh2_db: Path, stem_id: str) -> dict[str, tuple[str, str, int | None]]:
    """source_key -> (node_id, node_text, seq) for the nodes mh2.db is
    serving for this stem. Empty when mh2.db has not been built yet."""
    if not Path(mh2_db).exists():
        return {}
    con = sqlite3.connect(f"file:{mh2_db}?mode=ro", uri=True)
    try:
        return {k: (nid, text, seq) for k, nid, text, seq in con.execute(
            "SELECT source_key, node_id, node_text, seq FROM nodes WHERE stem_id = ?",
            (stem_id,))}
    finally:
        con.close()


def _count_in(con, sql: str, keys: list[str]) -> int:
    """COUNT(*) over `sql` (which ends in `source_key IN (%s)`), or 0 when
    the table is absent -- the placement tables are created by the
    sequencing tool's own schema and may not exist on every mh2_seq.db."""
    if not keys:
        return 0
    try:
        return con.execute(sql % ",".join("?" * len(keys)), keys).fetchone()[0]
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return 0
        raise


def orphan_counts(con: sqlite3.Connection, removed_keys: list[str]) -> dict[str, int]:
    """Durable writer decisions in mh2_seq.db keyed on a source_key this
    upload removes (or rewords -- the same thing to source_key). Every table
    counted here is keyed on source_key, never node_id (R3)."""
    return {
        "tag_reviews": _count_in(
            con, "SELECT COUNT(*) FROM tag_review WHERE source_key IN (%s)",
            removed_keys),
        "open_proposals": _count_in(
            con, "SELECT COUNT(*) FROM tag_proposal WHERE state IN"
                 " ('open','needs_attention') AND source_key IN (%s)", removed_keys),
        "candidate_rulings": _count_in(
            con, "SELECT COUNT(*) FROM candidate_ruling WHERE source_key IN (%s)",
            removed_keys),
        "placements": _count_in(
            con, "SELECT COUNT(*) FROM placement WHERE removed_at IS NULL"
                 " AND source_key IN (%s)", removed_keys),
    }


def diff_nodes(con: sqlite3.Connection, mh2_db: Path, stem_id: str,
               parsed_nodes: list[dict]) -> dict:
    """Parsed upload vs. the stem's live nodes, on source_key -- the same
    identity ingest_ladders.persist matches on."""
    from mh2.ingest_ladders import source_key

    live = _live_nodes(mh2_db, stem_id)
    parsed = {source_key(stem_id, n["node_text"]) for n in parsed_nodes}
    removed = sorted(set(live) - parsed,
                     key=lambda k: (live[k][2] is None, live[k][2] or 0, k))
    orphans = orphan_counts(con, removed)
    return {
        "diff": {
            "matched": len(parsed & set(live)),
            "new": len(parsed - set(live)),
            "removed": len(removed),
            "live_node_count": len(live),
            "removed_texts": [live[k][1] for k in removed[:REMOVED_TEXT_LIMIT]],
        },
        "orphans": orphans,
        "orphaned_review_count": sum(orphans.values()),
    }


def _save_stream(stream: BinaryIO, dest: Path) -> tuple[int, str, bytes]:
    """Copy `stream` to dest, stopping one chunk past MAX_BYTES. Returns
    (size, sha256 hex, first bytes)."""
    h = hashlib.sha256()
    size = 0
    head = b""
    with open(dest, "wb") as out:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            if not head:
                head = chunk[:4]
            size += len(chunk)
            if size > MAX_BYTES:
                break
            h.update(chunk)
            out.write(chunk)
    return size, h.hexdigest(), head


def validate_upload(con: sqlite3.Connection, stem_id: str, filename: str,
                    stream: BinaryIO, uploaded_by: str,
                    mh2_db: Path | None = None) -> dict:
    """
    Brief §5 step 1. Saves the file to _pending and returns the preview:
    {token, stem_id, original_name, node_count, issues, diff, orphans,
     orphaned_review_count, same_as_live, canonical_name}.

    Any refusal after the stem_id check is recorded as the stem's last
    rejection (the page's "Rejected" status) and raised as a 422.
    """
    slot = get_slot(stem_id)
    name = Path(filename or "").name

    def reject(code: str, message: str):
        _record_rejection(con, stem_id, name or None, message, uploaded_by)
        raise LadderUploadError(422, code, message)

    if not name.lower().endswith(".docx"):
        reject("not_docx", f"{name or '(no filename)'} is not a .docx file.")
    if name.startswith("~$"):
        reject("lock_file", f"{name} is a Word lock file, not a ladder. "
                            "Upload the document itself (the name without '~$').")

    pdir = pending_dir()
    pdir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    tmp = pdir / f"{token}.docx"
    try:
        size, sha, head = _save_stream(stream, tmp)
        if size > MAX_BYTES:
            reject("too_large", f"{name} is over the {MAX_BYTES // (1024 * 1024)} MB limit.")
        if size == 0:
            reject("empty_file", f"{name} is empty.")
        if head[:2] != b"PK":
            reject("not_docx", f"{name} is not a Word .docx file (it may be an "
                               "older .doc renamed, or a different format).")

        from mh2.ingest_ladders import read_docx
        issues: list = []
        try:
            nodes = read_docx(tmp, slot.masterlist_name, issues)
        except Exception as exc:  # noqa: BLE001 -- any parse failure is a refusal
            reject("unreadable", f"{name} could not be read as a ladder: "
                                 f"{type(exc).__name__}: {exc}")
        if not nodes:
            reject("no_nodes", f"{name} has no Knowledge Graph Node tables -- "
                               "nothing in it would be ingested.")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    preview = diff_nodes(con, mh2_db or config.DB, stem_id, nodes)
    live = get_live_upload(con, stem_id)
    meta = {
        "token": token, "stem_id": stem_id, "original_name": name,
        "sha256": sha, "size_bytes": size, "node_count": len(nodes),
        "issues": issues, "validated_by": uploaded_by,
        "validated_at": _iso(_now()),
        "orphaned_review_count": preview["orphaned_review_count"],
    }
    (pdir / f"{token}.json").write_text(json.dumps(meta), encoding="utf-8")
    return {
        **preview, **meta,
        "same_as_live": bool(live and live["sha256"] == sha),
        "canonical_name": canonical_name(slot, get_override(con, stem_id)),
    }


def purge_pending(max_age_seconds: int = PENDING_MAX_AGE_SECONDS) -> int:
    """Delete pending files older than max_age_seconds. Returns how many."""
    pdir = pending_dir()
    if not pdir.exists():
        return 0
    cutoff = time.time() - max_age_seconds
    n = 0
    for p in pdir.iterdir():
        if p.is_file() and p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)
            n += 1
    return n


# -------------------------------------------------------------------- commit

def commit_upload(con: sqlite3.Connection, stem_id: str, token: str,
                  uploaded_by: str, note: str | None = None,
                  confirm_orphans: bool = False) -> dict:
    """
    Brief §5 step 3: move the pending file to its versioned path, insert the
    ladder_upload row and make it the one live version, and register the
    slot if it was undrafted -- all rows in one transaction. The caller
    requests the rebuild.

    If validation found review decisions on nodes this version removes, the
    commit is refused unless confirm_orphans is set -- the page's
    confirmation box, enforced here too so it cannot be skipped by a client.
    """
    slot = get_slot(stem_id)
    if not _TOKEN_RE.match(token or ""):
        raise LadderUploadError(400, "bad_token", "malformed upload token")
    pdir = pending_dir()
    tmp, meta_path = pdir / f"{token}.docx", pdir / f"{token}.json"
    if not tmp.exists() or not meta_path.exists():
        raise LadderUploadError(410, "token_expired",
                                "That upload is no longer pending -- upload the file again.")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta["stem_id"] != stem_id:
        raise LadderUploadError(400, "wrong_stem",
                                f"That upload was validated for {meta['stem_id']}, not {stem_id}.")
    if meta.get("orphaned_review_count") and not confirm_orphans:
        raise LadderUploadError(
            409, "confirm_orphans",
            f"{meta['orphaned_review_count']} review decisions are attached to nodes "
            "this version removes or rewords. Confirm to commit anyway.")

    now = _now()
    rel = Path(stem_id) / f"{now.strftime('%Y%m%dT%H%M%SZ')}__{meta['sha256'][:8]}.docx"
    dest = uploads_dir() / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, dest)
    try:
        with con:
            con.execute("UPDATE ladder_upload SET is_live = 0"
                        " WHERE stem_id = ? AND is_live = 1", (stem_id,))
            cur = con.execute(
                "INSERT INTO ladder_upload (stem_id, stored_path, original_name,"
                " sha256, size_bytes, uploaded_by, uploaded_at, node_count,"
                " parse_issues, is_live, note) VALUES (?,?,?,?,?,?,?,?,?,1,?)",
                (stem_id, rel.as_posix(), meta["original_name"], meta["sha256"],
                 meta["size_bytes"], uploaded_by, _iso(now), meta["node_count"],
                 json.dumps(meta["issues"]), note))
            upload_id = cur.lastrowid
            if not slot.declared_file:
                con.execute(
                    "INSERT OR IGNORE INTO stem_registry_override (stem_id,"
                    " ladder_file, set_by, set_at) VALUES (?,?,?,?)",
                    (stem_id, canonical_name(slot), uploaded_by, _iso(now)))
            con.execute("DELETE FROM ladder_upload_rejection WHERE stem_id = ?",
                        (stem_id,))
    except BaseException:
        # The row never landed, so the file is unreferenced: put it back
        # where a retry of the same token can find it.
        os.replace(dest, tmp)
        raise
    meta_path.unlink(missing_ok=True)
    return get_version(con, stem_id, upload_id)


def revert(con: sqlite3.Connection, stem_id: str, upload_id: int) -> dict:
    """Make an older version live again. The caller requests the rebuild."""
    get_slot(stem_id)
    row = get_version(con, stem_id, upload_id)
    if not version_path(row).exists():
        raise LadderUploadError(409, "file_missing",
                                f"upload #{upload_id}'s file is missing from the volume")
    with con:
        con.execute("UPDATE ladder_upload SET is_live = 0"
                    " WHERE stem_id = ? AND is_live = 1", (stem_id,))
        con.execute("UPDATE ladder_upload SET is_live = 1 WHERE upload_id = ?",
                    (upload_id,))
    return get_version(con, stem_id, upload_id)


# ------------------------------------------------------------------- reading

def list_slots(con: sqlite3.Connection) -> list[dict]:
    """One row per distinct stem_id, stems.csv order, for GET /api/ladders."""
    by_name = repo_files_by_name()
    live = live_uploads(con)
    overrides = list_overrides(con)
    counts = version_counts(con)
    rejections = list_rejections(con)
    out = []
    for s in slots().values():
        up = live.get(s.stem_id)
        repo = repo_file(s, by_name)
        source = "upload" if up else ("repo" if repo else "none")
        rejection = rejections.get(s.stem_id)
        status = "rejected" if rejection else {
            "upload": "uploaded", "repo": "repo", "none": "empty"}[source]
        if up:
            up = {**up, "parse_issues": json.loads(up["parse_issues"] or "[]")}
        out.append({
            "stem_id": s.stem_id, "band": s.band, "stem_group": s.stem_group,
            "masterlist_name": s.masterlist_name,
            "workbook_stems": s.workbook_stems,
            "drafted_in_repo": bool(s.declared_file),
            "canonical_name": canonical_name(s, overrides.get(s.stem_id), by_name),
            "source": source, "status": status,
            "live": up, "version_count": counts.get(s.stem_id, 0),
            "rejection": rejection,
        })
    return out


def live_file(con: sqlite3.Connection, stem_id: str) -> tuple[Path, str] | None:
    """(path, download name) of the stem's live ladder: the live upload,
    else the repo copy, else None."""
    slot = get_slot(stem_id)
    name = canonical_name(slot, get_override(con, stem_id))
    up = get_live_upload(con, stem_id)
    if up:
        return version_path(up), name
    repo = repo_file(slot)
    return (repo, name) if repo else None


def write_export_zip(con: sqlite3.Connection, out_path: Path) -> int:
    """Every live ladder under its canonical name plus manifest.csv. Written
    to a file, not memory: the repo ladders alone are ~130 MB. ZIP_STORED
    because a .docx is already deflated. Returns the number of ladders."""
    by_name = repo_files_by_name()
    live = live_uploads(con)
    overrides = list_overrides(con)
    manifest = io.StringIO()
    w = csv.writer(manifest)
    w.writerow(["stem_id", "band", "stem_group", "masterlist_name", "file_name",
                "source", "upload_id", "uploaded_by", "uploaded_at",
                "original_name", "sha256", "size_bytes", "note"])
    n = 0
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for s in slots().values():
            name = canonical_name(s, overrides.get(s.stem_id), by_name)
            up = live.get(s.stem_id)
            if up:
                src = version_path(up)
                zf.write(src, name)
                w.writerow([s.stem_id, s.band, s.stem_group, s.masterlist_name, name,
                            "upload", up["upload_id"], up["uploaded_by"],
                            up["uploaded_at"], up["original_name"], up["sha256"],
                            up["size_bytes"], up["note"] or ""])
                n += 1
                continue
            repo = repo_file(s, by_name)
            if repo:
                zf.write(repo, name)
                w.writerow([s.stem_id, s.band, s.stem_group, s.masterlist_name, name,
                            "repo", "", "", "", repo.name,
                            hashlib.sha256(repo.read_bytes()).hexdigest(),
                            repo.stat().st_size, ""])
                n += 1
        zf.writestr("manifest.csv", manifest.getvalue())
    return n


# --------------------------------------------------------- effective inputs

def _link_or_copy(link: Path, target: Path) -> None:
    """Symlink `link` -> `target`; copy instead if the OS refuses symlinks
    (Windows without admin rights or Developer Mode raises OSError)."""
    try:
        link.symlink_to(target)
    except OSError:
        shutil.copyfile(target, link)


def assemble_effective_inputs(dest: Path) -> tuple[Path, Path]:
    """
    Brief §6.1. Rebuild `dest` from scratch as the ladder set and stems.csv a
    rebuild should read, and return (ladders_dir, stems_csv):

      dest/ladders/   every repo ladder (Word lock files skipped), except that
                      a stem with a live upload has its repo file left out and
                      the upload put in under the canonical name.
      dest/stems.csv  the repo stems.csv with each stem_registry_override
                      applied to EVERY row of that stem_id (ladder_file set,
                      ladder_drafted=1). A byte-for-byte copy when no override
                      applies, so a run with no uploads reads exactly what it
                      read before this existed.

    Entries are symlinks, not copies. The sources never change under them --
    versioned uploads are never overwritten and the repo ladders are in the
    image -- and copying ~130 MB of ladders onto a 1 GB volume that already
    holds mh2.db twice during a swap is the space the volume does not have.
    Where symlinks are refused (Windows without admin/Developer Mode -- a
    writer's local pilot copy) _link_or_copy falls back to copying; a laptop
    disk has the room.

    A live upload whose stem_id is no longer in stems.csv cannot be ingested
    (ingest refuses undeclared files) and is left out with a warning rather
    than failing the build -- failing would stop the app at startup.
    """
    dest = Path(dest)
    con = connect()
    try:
        live = live_uploads(con)
        overrides = list_overrides(con)
    finally:
        con.close()

    if dest.exists():
        shutil.rmtree(dest)
    ladders = dest / "ladders"
    ladders.mkdir(parents=True)

    fieldnames, rows = _read_raw_stems(config.STEMS_CSV)
    all_slots = slots(config.STEMS_CSV)
    owner = {normalize_ladder_filename(s.declared_file): sid
             for sid, s in all_slots.items() if s.declared_file}
    by_name = repo_files_by_name()

    for p in ladder_paths(config.LADDERS):
        if owner.get(normalize_ladder_filename(p.name)) in live:
            continue        # replaced by the upload below
        _link_or_copy(ladders / p.name, p.resolve())

    applied: dict[str, str] = {}
    for sid, up in sorted(live.items()):
        slot = all_slots.get(sid)
        if slot is None:
            print(f"    WARNING: live upload #{up['upload_id']} is for {sid!r}, which is"
                  " no longer in stems.csv -- left out of this build")
            continue
        override = None if slot.declared_file else overrides.get(sid)
        name = canonical_name(slot, override, by_name)
        target = ladders / name
        if target.exists() or target.is_symlink():
            raise SystemExit(f"{name}: two ladders want the same filename in {ladders}")
        _link_or_copy(target, version_path(up).resolve())
        if not slot.declared_file:
            applied[sid] = name

    stems_out = dest / "stems.csv"
    if not applied:
        shutil.copyfile(config.STEMS_CSV, stems_out)
    else:
        if any(f not in fieldnames for f in FIELDS):
            raise SystemExit(f"{config.STEMS_CSV} is missing stems.csv columns")
        with open(stems_out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames)
            w.writeheader()
            for r in rows:
                sid = (r.get("stem_id") or "").strip()
                if sid in applied:
                    r = {**r, "ladder_file": applied[sid], "ladder_drafted": "1"}
                w.writerow(r)
    return ladders, stems_out
