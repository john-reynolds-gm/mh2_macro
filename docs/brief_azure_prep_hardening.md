# Brief — pre-Azure hardening (deployment-agnostic)

**Written:** 2026-09-29. **For:** a Claude Code session in this repo.
**Companion:** `DEFERRED.md` §8 (Azure hosting rulings R-H1–R-H5). Read that
section first; it is the authority for every ruling cited here by number.

## 0. What this session is and is not

IT is provisioning a Python web app in Azure. This session's job is to get
the scripts to the state where **the answers IT gives cannot invalidate the
work**. Everything here is correct whether the DB ends up on local disk or
an Azure Files mount, whether auth is Easy Auth or HTTP Basic, and whether
the rebuild runs in-process or as a separate job.

**Explicitly out of scope this session** — do not start these, do not
scaffold them, do not add TODOs for them:

- The Microsoft Graph / SharePoint fetch step (R-H1, R-H2). Blocked on IT
  approving an Entra ID app registration with `Sites.Selected`. Note that
  `config.py` already centralizes every input path, so this lands later as
  a fetch step that *populates* those paths — not as a loader rewrite. Keep
  it that way; do not make loaders path-agnostic in anticipation.
- Any auth change (Easy Auth vs. HTTP Basic). Blocked on IT question 7.
  `docs/HOSTING.md` and `tests/test_review_api_auth.py` stay as they are.
- The worklist UI (`GET /api/worklist` has no consuming screen). That is its
  own session and is the recommendation for *after* the rollout question is
  settled, not now.
- Re-measuring the Green-and-flagged and unattributed PK–5 figures. Needed
  for the team-lead conversation, not the IT one. See §8's "Figures not
  carried forward" — when that work happens, it carries literal predicates
  or it carries no number.

## 1. Rebuild becomes non-destructive (R-H5.1, R-H5.2)

**Current behavior, verified:** `scripts/rebuild.py:277-279` does
`config.DB.unlink()` then executes the schema into that same path. Every
subsequent step receives `--db str(config.DB)`. A parse failure mid-run
therefore leaves a half-populated database live, and a failure in step one
leaves no database at all. `mh2_seq.db` is correctly never unlinked
(`rebuild.py:282-286`) — leave that alone.

**Required behavior:** build into a new file, swap on success only.

Implementation is mechanical because the `--db` value is already threaded
through every subprocess call rather than read from config inside them:

1. Introduce one local, e.g. `build_db = config.BUILD / "mh2.db.building"`.
   Unlink any stale copy of it at start — not `config.DB`.
2. Substitute `build_db` for `config.DB` in the schema create and in every
   `run(...)` call's `--db` argument, and in the row-count/diagnostic
   connection at the end of `main()`.
3. On success, `os.replace(build_db, config.DB)` — atomic on the same
   filesystem, and existing readers keep the old inode until they close.
4. On any failure, `build_db` is left in place or removed; `config.DB` is
   untouched. `run()` already `sys.exit`s on a non-zero return code
   (`rebuild.py:255-258`), so the swap simply never happens. Verify no
   `except` swallows that path.

**Do not** change `config.DB` itself, and do not add a build-path constant to
`config.py`. `config.DB` is the live database that `review_api.py`
(`_mh2_con()`) and `app/db.py` (`get_connection()`) read; the build path is
an implementation detail of one script.

**Two gotchas, both real:**

- **SQLite sidecar files.** `os.replace` moves the `.db` only. Confirm the
  build database's `journal_mode` (default is `delete`, not `wal`, so there
  should be no `-wal`/`-shm` to orphan). If you find WAL in play, checkpoint
  and remove the sidecars before the swap rather than moving three files.
- **Where `reconcile_review` runs.** It is the one step that mutates durable
  state — it writes `tag_proposal` state transitions in `mh2_seq.db`
  (`mh2/review_store.py:210`, and it is the *only* writer of those
  transitions). Running it before the swap means a later swap failure leaves
  `mh2_seq.db` reconciled against a database that never went live.
  **Ruling for this session: run it after the swap**, against `config.DB`,
  so durable state only ever reflects what is actually serving. Say so in a
  comment, because the obvious reading is to leave it in sequence.

## 2. One rebuild at a time (R-H5.3)

**Current behavior, verified:** there is no lock of any kind in
`scripts/rebuild.py`. Two concurrent runs race on the same unlink-and-create.
Harmless while one person runs it from a laptop; not harmless the moment a
button exists.

Add an advisory lock at the top of `main()`: `fcntl.flock` on
`config.BUILD / ".rebuild.lock"` with `LOCK_EX | LOCK_NB`. On failure, exit
with a message naming the lock file and that another rebuild is running — a
clean exit, not a traceback, and not a wait.

`fcntl` is POSIX-only. That covers John's Mac and Linux App Service; note in
a comment that a Windows host would need `msvcrt.locking`, and do not build
that abstraction now.

## 3. Data paths become relocatable (R-H4)

IT's answers to "where do DB files physically live" and "do they survive
redeploys" are unknown, and both possible answers require the same thing:
the ability to point `data/` somewhere other than the repo without editing
code.

In `config.py`, have `DATA` honor an environment variable — `MH2_DATA_DIR`,
falling back to `ROOT / "data"` exactly as today. `SOURCE`, `BUILD`,
`REPORTS` and everything derived stay relative to `DATA` and need no other
change.

Keep this to **one** variable. Resist a second for `BUILD` alone: the moment
build and source can be separated by configuration, the question of which one
`RERANK_CACHE` follows becomes a live bug (it is deliberately outside
`data/build` — see the comment at `config.py`'s `RERANK_CACHE`, and do not
disturb that reasoning).

Add the variable to `docs/HOSTING.md`'s environment section so the Azure
runbook inherits it whichever host wins.

## 4. Pre-flight covers every declared input (R-H5.5)

**Current behavior, verified:** `rebuild.py:270-274` checks exactly three
workbook files and exits naming the missing ones. Ladder `.docx` files are
checked separately and correctly — `resolve_ladder_files`
(`mh2/load_stems.py:97-128`) raises `SystemExit` on any `ladder_drafted=1`
row whose `ladder_file` is blank, missing, or ambiguous on disk, and it runs
before ingestion. **A missing ladder does not silently turn a stem Red.**
That is already right; do not rewrite it.

The gap is the rest of `config.py`'s declared inputs. Extend the existing
`missing` check to cover `STEMS_CSV`, `CATEGORY_TO_STEMS_CSV`,
`GRADE_WORKSHEET`, and the `standards/` CSVs the run actually consumes —
listing **every** missing file in one message, not failing on the first.
*(Corrected 2026-09-30, per the follow-up brief §4: this argued from
"twelve minutes into a rebuild is the wrong place to learn a file is
absent." A real full rebuild is ~14 seconds, so run length was never the
reason.)* Pre-flight earns its place for the SharePoint fetch step that is
coming: there, a missing file means a download that failed or a permission
that was never granted, not a typo — and the run should say so up front,
naming every absent file at once, rather than surfacing it as a parse
error partway through.

*(Also corrected: `CATEGORY_TO_STEMS_CSV` does not belong in this set. It
is read by the serving layer — `mh2/coverage.py` and `app/db.py` — and by
no step the rebuild runs. It was removed from the pre-flight on
2026-09-30; see the follow-up brief §3.)*

Do not check `PREDICTIONS` or `RERANK_CACHE` as hard requirements; both are
legitimately absent on a clean checkout and their loaders already handle it.
Confirm that before assuming it.

## 5. Rebuild leaves a record (R-H5.4)

Append one line per run to `data/reports/rebuild_log.tsv`: UTC timestamp,
who triggered it (`MH2_REBUILD_ACTOR` env var, falling back to the OS user),
outcome (`ok` / `failed:<step label>`), wall-clock duration, and the count of
ladder files ingested. Append-only, never rewritten, no rotation logic.

This is deliberately a file and not a table: `mh2.db` is destroyed on every
rebuild, and putting rebuild history in `mh2_seq.db` would make that database
about pipeline operations rather than writer judgment, which R-H3 rules out.
`data/reports/` is where output-for-humans already lives.

Out of scope here: R-H5.6's change preview (which ladders changed since the
last rebuild). It needs SharePoint `lastModifiedDateTime`, so it arrives with
the fetch step.

## 6. `write_ruling()` stops writing tags to a disposable database

**The problem, verified.** `app/db.py:318-333` upserts into
`node_standards` on a connection to `config.DB` (`app/db.py:32-36`) —
`mh2.db`, destroyed on every rebuild. Its own docstring says the opposite:
line 319 claims "layer 3, authoritative, durable across a rebuild" and
lines 9-12 repeat it. **That docstring is false and has been false since
`rebuild.py` started unlinking the database.** Fix the prose in the same
commit as the behavior; a corrected comment is part of the deliverable.

**Why not simply repoint it at `mh2_seq.db`.** `node_standards` rows are tag
assignments. Persisting writer-authored tags in `mh2_seq.db` contradicts
R-H3 ("holds judgments beside the data, never tags") and §6's standing
rulings that the ladders are the tagging source of truth and the tool must
not become a third source of truth. Today the destructiveness of `mh2.db` is
the only thing preventing that divergence — the leak is self-cleaning.
Making it durable converts a leak into a second tagging authority, which is
worse than the bug.

**Required shape.** A writer accepting a suggested candidate is asserting
that a tag *should exist* in a Word document. That already has a designed
home: `tag_proposal` in `mh2_seq.db`, which exists precisely to be "an
instruction to a human editing a Word document" (`mh2/schema_seq.sql`) and
is history-preserving by design.

- **`accepted`** → `review_store.create_tag_proposal(...)`.
  `write_ruling()` receives `node_id`; `source_key`, `node_text_seen` and
  `ladder_file` come from `mh2/node_lookup.py`'s `build_node_lookup()`,
  which is the single sanctioned boundary for exactly this translation — do
  not issue that query anywhere else. Pass the Streamlit reviewer as
  `proposed_by`.
- **`rejected`** → see FLAG 1. Do not drop it silently.

**This is not a one-function change, and that is the main risk in this
item.** Three read paths derive the Streamlit tool's progress display from
`node_standards.source = 'human'` and its `status` column:

- `_ruled_totals_by_stem` (`app/db.py:74-92`) — the ruled/total tally on
  every stem in the sidebar tree.
- the `suggested_pool` join (`app/db.py:227`).
- `known_reviewers` (`app/db.py:338`).

Reroute the write without rerouting these and the tool silently reports zero
progress forever, which is a worse regression than the bug being fixed. All
three must read the new durable state. Landing the write change without them
is not an acceptable partial delivery — either both halves land or neither
does.

`app/` has been treated as frozen by prior sessions (Session D verified
`md5sum` of every file under it before and after). This item deliberately
breaks that freeze. Confine the change to `app/db.py`; `app/review.py`'s five
call sites keep the same `write_ruling(con, node_id, standard_id, ruling,
reviewer)` signature and should not need editing. If you find yourself
editing `app/review.py`, stop and say why.

### FLAG 1 — needs John's decision before you write the rerouted path

`tag_proposal` has no way to express a **rejection**. Its `state` values are
`open` / `landed` / `landed_elsewhere` / `needs_attention` / `withdrawn` —
all stages of a proposal's life, none of them "a reviewer looked at this
suggested candidate and it is wrong." Dropping rejections means the
Streamlit tool re-surfaces the same rejected candidate on every rebuild,
forever.

**Recommended:** add a small `candidate_ruling` table to
`mh2/schema_seq.sql`, keyed `(source_key, standard_id)`, holding
`ruling` (`accepted` / `rejected`), `ruled_by`, `ruled_at`. This is a
judgment about a machine-generated suggestion, not a tag, so it sits
comfortably inside R-H3. `accepted` writes both a `candidate_ruling` row and
a `tag_proposal`; `rejected` writes only the ruling. The three read paths
above then join `candidate_ruling` instead of `node_standards`, keyed on
`source_key` via `node_lookup` — which also makes them survive the node-ID
content-hash decay risk in §4, the same reason tag-level review is keyed
that way (R3).

Do not implement this without confirmation. If John declines the new table,
the fallback is making `write_ruling()` refuse rejections with a message
pointing at the ladder — smaller, but it removes a path writers use today,
so it is his call and not yours.

## 7. Acceptance criteria

Behavior, not line counts. Each of these is a test in `tests/`:

1. A rebuild that fails partway leaves the pre-existing `mh2.db` byte-identical
   (compare `md5sum` before and after) and leaves `mh2_seq.db` untouched.
2. A successful rebuild replaces `mh2.db` and the swapped-in file passes
   `PRAGMA integrity_check`.
3. A second `rebuild.py` invoked while the lock is held exits non-zero with a
   message naming the lock file, and does not touch `mh2.db`.
4. With `MH2_DATA_DIR` set to a temp directory, a rebuild reads and writes
   entirely inside it and creates nothing under the repo's own `data/`.
5. Pre-flight with three inputs deliberately absent names all three in one
   message and exits before the schema is created.
6. `rebuild_log.tsv` gains exactly one line per run, for both the success and
   the failure case, with the failing step named.
7. `write_ruling(..., "accepted", ...)` creates a `tag_proposal` row in
   `mh2_seq.db` reachable through `GET /api/worklist`, and writes nothing to
   `node_standards`.
8. After a rebuild, the Streamlit sidebar's ruled/total tally for a stem is
   unchanged from before it — the regression that §6's read-path rerouting
   exists to prevent.
9. `tests/test_review_api_auth.py` and every existing assertion in
   `tests/test_render_static.py` pass unmodified. If you believe a frozen
   test must change, report it rather than changing it — see §7 of
   `DEFERRED.md` for how the one prior exception was handled.

## 8. Standing norms for this session

From `DEFERRED.md` §6, and they apply to your own output:

- **Report discrepancies; do not search for a predicate that fits a stated
  number.** If something in this brief does not match the code, say so in
  your summary rather than quietly implementing around it. Two claims in the
  handoff this brief descends from were wrong, and both were caught that way.
- **Acceptance criteria carry the literal predicate that produced the number,
  or carry no number at all.**
- Every deferral you create needs a trigger, or it is abandonment. Add
  anything you defer to `DEFERRED.md` with one.
- `mh2/coverage.py` owns the denominator and the rollup. No SQL in routes.
- Do not touch `mh2/coverage.py`, `docs/HOSTING.md`'s auth section, or
  anything under `app/` other than `app/db.py`.
