# Brief: Ladder Files page (team-owned ladder uploads)

**Status:** ready to build · **Owner:** John · **Date:** 2026-10-12

## 1. Why

IT can't connect the hosted app to SharePoint. Right now the ladders the app reads
(`data/source/ladders/*.docx`) are committed to git and built into the Docker
image. That means every ladder change goes through John and a redeploy. This
brief adds a **Ladder Files** page to the deployed FastAPI app (`review_api.py`)
where writers upload and replace ladder `.docx` files themselves, one slot per
stem in `stems.csv`.

Not in scope: the Streamlit tool in `app/`. It isn't deployed and shouldn't be touched.

## 2. Facts from the current codebase that shape the design

| Fact | Where | Consequence |
|---|---|---|
| Only `/app/data/build` is a persistent Fly volume | `fly.toml` `[[mounts]]` | Uploads **must** live under `data/build`. Anything else is wiped on redeploy. |
| `data/source` is built into the image and read-only by convention | `config.py`, `Dockerfile` | The app never writes to `data/source`. Uploads are a separate layer placed on top of it. |
| `mh2.db` is rebuilt from source on every start; the build is atomic, with a lock | `entrypoint.sh`, `rebuild.py` (`acquire_lock`, `os.replace`) | An upload doesn't take effect until a rebuild runs. Rebuilding again is safe. |
| `/` serves a **pre-rendered** `coverage.html` with the data embedded | `render_static.py`, `review_api.index` | Every rebuild must be followed by `render_static.py`, or the page shows stale data. |
| Ingest refuses any ladder file not declared in `stems.csv` | `ingest_ladders.main`, `load_stems.resolve_ladder_files` | Uploads are always saved under the slot's declared `ladder_file` name. |
| **Only 17 of 53 stem_ids are drafted.** The other 36 have blank `ladder_file` and `ladder_drafted=0` | `stems.csv` | Uploading to an undrafted slot also has to register it. `stems.csv` is in the image, so registration needs a durable overlay (§4.2). |
| `stems.csv` is read directly by `load_standards.py:305`, not only by values passed as arguments | grep | Overriding the paths has to happen in `config.py`, not just in `rebuild.py`'s argument lists. |
| Node identity is a hash of node text. A reworded node becomes a new node, and the old one is deleted | `ingest_ladders` docstring | Replacing a ladder can orphan review state. Show a preview before committing (§5). |
| Auth is HTTP Basic, and `require_writer` returns the username | `review_api.py` | Use it for `uploaded_by` and `MH2_REBUILD_ACTOR`. |
| Committed Word lock files (`~$...docx`) sit in `data/source/ladders` | `ls` | Reject `~$` uploads. Optionally remove the committed lock files from git (cleanup). |

## 3. Precedence rule (state this in the UI and in HOSTING.md)

> For any stem that has an upload, **the uploaded file is the source of truth**.
> The copy in the repo is only a seed for stems that have never been uploaded.

A newer ladder committed to the repo does **not** override an existing upload. To
make the repo copy win again, a writer uploads it, or reverts to an older version.

## 4. Storage

### 4.1 Files: `data/build/ladder_uploads/` (on the volume, durable)

```
data/build/ladder_uploads/
  <stem_id>/
    <YYYYMMDDTHHMMSSZ>__<sha256[:8]>.docx   # every version is kept, nothing is overwritten
```

Add `LADDER_UPLOADS = BUILD / "ladder_uploads"` to `config.py`. Because
`data/build` is no longer entirely disposable (this was already true for
`mh2_seq.db`, see `brief_flag_a_seq_db_not_disposable.md`), update the
`config.py` docstring and HOSTING.md: **`rm -rf data/build` is no longer safe.
Delete only `mh2.db*`.**

### 4.2 Metadata: new tables in `mh2_seq.db` (durable)

Add to `mh2/schema_seq.sql` using `CREATE TABLE IF NOT EXISTS`, the same pattern as the existing tables:

```sql
CREATE TABLE IF NOT EXISTS ladder_upload (
  upload_id      INTEGER PRIMARY KEY,
  stem_id        TEXT NOT NULL,
  stored_path    TEXT NOT NULL,          -- relative to LADDER_UPLOADS
  original_name  TEXT NOT NULL,          -- what the writer uploaded
  sha256         TEXT NOT NULL,
  size_bytes     INTEGER NOT NULL,
  uploaded_by    TEXT NOT NULL,
  uploaded_at    TEXT NOT NULL,          -- UTC ISO
  node_count     INTEGER NOT NULL,       -- from validation parse
  parse_issues   TEXT,                   -- JSON list, may be empty
  is_live        INTEGER NOT NULL DEFAULT 0,
  note           TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_live_per_stem
  ON ladder_upload(stem_id) WHERE is_live = 1;

-- Registers a ladder for a stem whose stems.csv row has a blank ladder_file.
CREATE TABLE IF NOT EXISTS stem_registry_override (
  stem_id       TEXT PRIMARY KEY,
  ladder_file   TEXT NOT NULL,           -- canonical name, see §4.3
  set_by        TEXT NOT NULL,
  set_at        TEXT NOT NULL
);
```

Do all the logic in a new module, `mh2/ladder_store.py`. It follows
`review_store.py`'s pattern, and routes never write SQL themselves.

### 4.3 Canonical filename

- Drafted slot (declared `ladder_file`): save the live copy under that exact name.
- Undrafted slot: generate `MH2_<band>_<stem_id>.docx`. Write a
  `stem_registry_override` row on the first upload.
- The writer's original filename is kept only as metadata. That way a file named
  `Fractions FINAL v3 (2).docx` can't break registry resolution.

## 5. Upload flow (validate → preview → commit)

1. **POST `/api/ladders/{stem_id}/validate`** (multipart). Checks:
   - `stem_id` exists in `stems.csv`.
   - The filename ends in `.docx` and doesn't start with `~$`. The first bytes are `PK`. Size ≤ 20 MB.
   - `ingest_ladders.read_docx(tmp, stem_name, issues)` runs without raising and returns ≥ 1 node.
   - **Diff against the current live nodes** for that stem in `mh2.db`, computed from `source_key`:
     matched / new / removed counts, plus a short list of the removed node texts.
     Also count how many review rulings or tag proposals in `mh2_seq.db` point at the removed node_ids.
   - The temp file goes in `LADDER_UPLOADS/_pending/<token>.docx`. Return `{token, node_count,
     issues, diff, orphaned_review_count}`.
2. The UI shows the preview. If `orphaned_review_count > 0`, show a clear warning
   ("N review decisions are attached to nodes this version removes or rewords"),
   and require the writer to check a confirmation box.
3. **POST `/api/ladders/{stem_id}/commit`** `{token, note?}`. Move the file into its versioned path,
   insert the `ladder_upload` row, flip `is_live` in one transaction, write the registry override
   if needed, then request a rebuild (§6).
4. Delete pending files older than 24 h when the app starts.

## 6. Rebuild after upload

### 6.1 Assembling the effective inputs

New function `mh2/ladder_store.assemble_effective_inputs(dest: Path) -> tuple[Path, Path]`:

- `dest/ladders/`: copy each repo ladder (skip `~$`). Then, for each stem with a
  live upload, delete the repo file that resolves to that stem and copy the live upload in
  under its canonical name.
- `dest/stems.csv`: a copy of `stems.csv` with `stem_registry_override` applied
  (`ladder_file` set, `ladder_drafted=1`, on **every** row with that stem_id,
  because one ladder can cover several workbook stems).

Use `dest = data/build/_effective/`. Rebuild it from scratch each run.

### 6.2 Wiring

- `config.py`: add env overrides `MH2_LADDERS_DIR` and `MH2_STEMS_CSV`, defaulting to the
  current paths. `LADDER_GLOB` follows `LADDERS`. This covers every consumer, including
  `load_standards.py:305`, `parse_node_standards`, and `ingest_new.py`.
- `rebuild.py`: before the first step, call `assemble_effective_inputs`. Run every
  `run(...)` subprocess with `MH2_LADDERS_DIR`/`MH2_STEMS_CSV` set in its env, and pass
  `--csv` explicitly to `ingest_ladders` (it currently relies on the default). Add a
  `--no-uploads` flag that skips the overlay, for local runs against the repo only.
- `entrypoint.sh` doesn't change. Rebuilds at startup pick up uploads automatically.

### 6.3 Triggering from the app

- A single background worker thread in `review_api.py` with a `dirty` flag. Commit sets
  `dirty=True` and wakes the worker. The worker runs `rebuild.py --skip-review-reconcile`
  and then `render_static.py` as subprocesses, with `MH2_REBUILD_ACTOR=<username>`, and loops
  while `dirty` was set again during the run. Several quick uploads therefore collapse into one or two rebuilds.
- If the rebuild lock is already held, wait and retry. Don't fail.
- A failed rebuild leaves the previous `mh2.db` serving (that's already guaranteed). The
  status endpoint reports the failing step and the last 50 lines of output.
- **Decision for John:** keep `--skip-review-reconcile` to match startup, or run review
  reconcile after an upload? Reconcile is what flags rulings orphaned by reworded nodes, so running it is arguably the point.
  Default in this brief: match `entrypoint.sh` and revisit later.

## 7. Endpoints (all behind `require_writer`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/files` | The Ladder Files page (static HTML, served the same way as `seq_static`) |
| GET | `/api/ladders` | One row per distinct stem_id: band, stem_group, masterlist_name, source (`upload` / `repo` / `none`), live version metadata, version count |
| POST | `/api/ladders/{stem_id}/validate` | §5 step 1 |
| POST | `/api/ladders/{stem_id}/commit` | §5 step 3 |
| GET | `/api/ladders/{stem_id}/file` | Download the live file (upload, else repo copy) under its canonical name |
| GET | `/api/ladders/{stem_id}/versions` | Version history |
| GET | `/api/ladders/{stem_id}/versions/{upload_id}/file` | Download an older version |
| POST | `/api/ladders/{stem_id}/revert/{upload_id}` | Make an older version live again (also triggers a rebuild) |
| GET | `/api/rebuild/status` | `idle` / `running` / `failed`, last success time and actor, error tail |
| GET | `/api/ladders/export.zip` | Every live ladder plus a manifest CSV. This is the team's backup and the hand-off back to SharePoint. |

Add `python-multipart` to `requirements.txt`. FastAPI needs it for `UploadFile`.

## 8. The page (`/files`)

Plain HTML, CSS, and vanilla JS, matching the existing `coverage.html` style. No CDN.

- Header: a rebuild status pill that polls `/api/rebuild/status` every 5 s while running, plus a "Download all (zip)" button.
- Filters: band (PK5 / 6_9), stem_group, status.
- Table, one row per stem_id: Band · Group · Stem · Status · Last updated (date, by whom) · Versions · Actions.
  - Status chips: **Uploaded** · **Repo copy** (still the committed seed) · **Empty** · **Rejected/error** (last attempt failed).
  - Actions: Upload / Replace · Download · History (expands the version list with Revert buttons).
- Upload modal: file picker, then the validation preview (node count, parse issues, matched/new/removed, orphan warning), then an optional note and **Commit**.
- Add a "Ladder Files" link to `coverage.html`'s header (`render_static.py`) and a link back.

## 9. Tests (new: `tests/test_ladder_store.py`, `tests/test_ladder_upload_api.py`)

1. Validation rejects non-docx files, `~$` files, files without the `PK` magic bytes, oversized files, zero-node docs, and unknown stem_ids.
2. A valid upload to a **drafted** slot saves the file under the declared name, and the effective dir contains the upload, not the repo copy.
3. A valid upload to an **undrafted** slot creates the override. The effective `stems.csv` has `ladder_drafted=1` on every row for that stem_id, and `load_stems.resolve_ladder_files` passes.
4. Only one live version per stem. Revert flips it correctly.
5. The diff counts are correct for an edited fixture: one reworded node shows as 1 new + 1 removed, and the orphan count reflects a seeded ruling.
6. `rebuild.py` with no uploads produces a database identical to today's (regression). `--no-uploads` ignores existing uploads.
7. A failed rebuild after upload leaves the previous `mh2.db` serving, and the status endpoint reports the failure.
8. Durability: uploads and metadata survive deleting `mh2.db` and rerunning `entrypoint.sh` steps (mirror `test_review_ruling_durability.py`).
9. Auth: every new route returns 401 without credentials when `MH2_AUTH_USERS` is set.

## 10. Docs to update

- `docs/HOSTING.md`: add the precedence rule (§3), the corrected "what's safe to delete" (§4.1), the new
  env vars, and a backup routine (download the zip weekly, or after any big batch of uploads).
- `config.py` docstring: `data/build` now holds durable state.

## 11. Open questions for John

1. Run review reconcile after upload rebuilds (§6.3)?
2. Should any writer be able to upload any stem, or should uploads be limited to named owners per band? The default here is any writer.
3. Retention: keep every version forever (default; files are small), or cap at N per stem?
