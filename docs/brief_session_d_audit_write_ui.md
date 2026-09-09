# Brief — Session D: wire the audit pass to the write endpoints

Scoped Claude Code brief. Self-contained; paste as the first message of a new
session. Written against the repo as it stands after Session C, read directly
— not against a snapshot. Where this brief and any earlier document disagree
about what is in the repo, this brief was measured and they were not.

Predicates are given alongside figures throughout so they can be re-run
rather than trusted.

---

## 0. What was measured, and against what

All predicates below were run in the repo root on the post-Session-C tree.

| Predicate | Result |
|---|---|
| `ls app.py audit_app.py` | absent (both) |
| `find . -name "app.py" -o -name "audit_app.py"` (excl. `.venv`) | no hits |
| `grep -n "def color" mh2/coverage.py` | `131: def color(self) -> str:` |
| `grep -rn "colour" --include="*.py" --include="*.sql" --include="*.js" --include="*.html" .` (excl. `.venv`, `.git`) | 2 hits, both `mh2/schema_seq.sql:10-11`, both comments saying `colour` is *not* used |
| `grep -c "@app\." review_api.py` | 12 routes — 4 GET (`/`, `/api/audit`, `/api/standards/{id}`, `/api/worklist`), 8 writes (5 POST, 3 DELETE) |
| `grep -cin "select \|insert \|update \|delete " review_api.py` | 0 |
| `grep -cn "fetch(\|XMLHttpRequest\|/api/" scripts/render_static.py` | **0** |
| `grep -rhn "^def test_\|^    def test_" tests/*.py \| wc -l` | 236 |
| `.pytest_cache/v/cache/nodeids` length | 236 |
| `.pytest_cache/v/cache/lastfailed` | `{}` |
| Row counts in every `data/build/mh2_seq.db` table | **0** (all four) |
| `data/reports/review_reconcile.txt` | 8 sections, "0 rows" each |
| `grep -n "mode=ro"` | `mh2/reconcile_review.py:202`, `review_api.py:34` |
| `scripts/rebuild.py` | `:265` `--skip-review-reconcile`, `:278` `config.DB.unlink()`, `:285` creates `SEQ_DB`, never unlinks it |
| `wc -l scripts/render_static.py` | 593 |
| `len(coverage.build_rows(con))` against `data/build/mh2.db` | 2,987 |
| Green / Yellow from those rows | 682 / 102 |
| `sum(1 for r in rows if r.color == "Green" and r.flagged)` | 82 |
| `git status --porcelain app/` | `?? app/` — **untracked** |
| `.gitignore` | ignores `data/build/` — see FLAG A |

The 2,987 / 682 / 102 / 82 figures were re-measured for this brief directly
against the real 160MB `data/build/mh2.db` opened `mode=ro`, not carried over
from rev 9. They match rev 9 exactly. Predicate:

```
python -c "import sqlite3; from mh2 import coverage
con=sqlite3.connect('file:data/build/mh2.db?mode=ro',uri=True)
rows=coverage.build_rows(con)
print(len(rows), sum(1 for r in rows if r.color=='Green'),
      sum(1 for r in rows if r.color=='Yellow'),
      sum(1 for r in rows if r.color=='Green' and r.flagged))"
```

**Not verified by execution.** The test suite was not run for this brief:
`.venv` is a macOS build and `pytest` was unavailable in the environment this
was written in. The 236/green claim rests on the static function count plus an
empty `lastfailed`. Re-run predicate: `.venv/bin/python -m pytest -q`. Do this
first in Session D, before changing anything, so a pre-existing failure is not
mistaken for one you introduced.

Four findings are load-bearing for this session:

1. **The eight write endpoints have no caller.** `review_api.py` exposes
   `POST`/`DELETE` for standard review, override, tag review, and proposals.
   `scripts/render_static.py` contains zero `fetch(` calls and zero `/api/`
   references. `review_api.py`'s `index()` (`:71-77`) merely `FileResponse`s
   the prebuilt `data/reports/coverage.html`. No human can reach a write path
   today. **This session closes exactly that gap.**

2. **`mh2_seq.db` has never held a row.** All four tables are empty and the
   reconcile report is eight "0 rows" sections. The reconcile logic is covered
   by fixture tests (`tests/test_reconcile_review.py`, 8 tests, including
   `test_landed_proposal_reports_before_and_after_color` and
   `test_proposal_with_vanished_source_key_needs_attention` — rev 9's
   acceptance criteria 4 and 5). So this is not a coverage hole. But no writer
   judgment has ever round-tripped against real data, and this session's UI is
   what first puts real rows in that file.

3. **The embedded tag payload has no `source_key`.** `tag_dict`
   (`scripts/render_static.py:82-94`) carries seven fields: `node_id`,
   `raw_code`, `tier`, `node_grades`, `is_leaf`, `grade_match`, `note`. R3
   keys tag-level review on `source_key`. The page therefore **cannot** post a
   tag review from embedded data alone. See D3.

4. **The row contract is closed and test-enforced.** `ROW_DICT_KEYS`
   (`:60-64`) is a frozen 15-name set — re-counted programmatically, 15 —
   asserted at `tests/test_render_static.py:60` and `:152`, and DEFERRED.md §0
   declares the contract CLOSED with reopening "a deliberate act". This
   session does not reopen it.

---

## 0.1 FLAG A — `mh2_seq.db` sits in a git-ignored, explicitly disposable directory

Reported per DEFERRED.md's standing discrepancy principle. **This is not a
Session D task**, but it should be ruled on before writers put real judgment
into that file.

Measured:

- `config.py:61` → `SEQ_DB = BUILD / "mh2_seq.db"`, i.e. `data/build/`.
- `.gitignore` ignores `data/build/`, under the comment: *"Disposable build
  artifacts (`rm -rf data/build && rebuild.py` is always safe)"*.
- `scripts/rebuild.py:285` creates `SEQ_DB` and never unlinks it — correct,
  and exactly what R2 requires.

The conflict: `rebuild.py` is careful never to delete `mh2_seq.db`, but the
documented safe-reset gesture, `rm -rf data/build`, deletes the whole
directory it lives in. `rebuild.py` regenerates `mh2.db` and recreates an
*empty* `mh2_seq.db`; it cannot regenerate authored review state, and there is
no error and no backup. R2 exists precisely because `config.DB.unlink()`
destroyed authored state silently — the same failure mode has reappeared one
directory up, from the `.gitignore` side rather than the code side.

Today the exposure is zero: all four tables have 0 rows. **The moment Session
D's UI works, it stops being zero.** Note also that `data/build/` already
holds three `mh2.db.bak-*` files, so someone's instinct has been to back up by
hand.

Options, unranked, for a ruling rather than a fix here: move `SEQ_DB` outside
`data/build/`; un-ignore `data/build/mh2_seq.db` specifically
(`!data/build/mh2_seq.db`); or amend the `.gitignore` comment and README so
`rm -rf data/build` is documented as destructive. Do not decide this inside
Session D.

---

## 1. Scope

**In:** the audit pass only. The 2,987-row sheet-then-grade render gains the
three v1 writer actions from R6 — mark reviewed, override color, propose a tag
— wired to the existing endpoints.

**Out:** the edit pass. `GET /api/worklist` stays unconsumed and is a later
session. Also out, unchanged from rev 9 §5: React, hosting, HTTPS, auth, and
full tag CRUD against `node_standards`.

Rationale for splitting it this way: writers cannot create proposals until the
audit surface can create them, so an edit worklist built first would start
empty and stay empty. Audit first is the smaller scope *and* the one that
produces the data the edit pass needs.

---

## 2. Rulings

**D1 — One page, progressive enhancement. Write affordances appear only when
the page is served over HTTP.**

`scripts/render_static.py` keeps emitting a single self-contained HTML file
that works opened from disk. The page gates every write affordance on
`location.protocol !== 'file:'`. No second template, no `--with-write-path`
flag, no build variant.

This is not cosmetic: gating on protocol means every fetch is same-origin
against the server that served the page, so **no CORS configuration is needed
and none should be added**. It also preserves the module docstring's "one
Python script, one HTML file, no server" contract for the offline case, which
is how the file is read today.

**D2 — Review state comes from the API, never from embedded JSON.**

`ROWS` continues to load from the embedded `rows-data` script tag exactly as
today. When served over HTTP, the page additionally fetches `GET /api/audit`
and joins on `code` ↔ `standard_id` to layer in `review_state`,
`effective_color`, and `has_override`. The embedded rows remain the offline
read-only truth and gain nothing.

Note the deliberate name difference: `coverage.StandardRow` exposes `code`,
and `review_api._row_out` (`:54-66`) renames it to `standard_id` to match
`mh2/schema_seq.sql`. The join is on that pair. Do not "fix" either name.

**D3 — Tag-level `source_key` is fetched, not embedded. The row contract stays
closed.**

Do **not** add `source_key` to `tag_dict`, and do not touch `ROW_DICT_KEYS`.
On row expand, the page fetches `GET /api/standards/{standard_id}`, which
already returns `source_key`, `node_text`, `ladder_file`, and any existing
`review` per tag (`review_api.py:133-145`), plus `standard_review` and
`override`. Tag review and proposal writes use the `source_key` from that
response.

Consequence to accept, not work around: tag-level actions require the row to
be expanded first. That matches the workflow — a writer judges a tag after
reading the node text — and it keeps the initial page identical in payload to
today's.

**D4 — Writer identity: one name field in the page header. Provisional by
construction.**

A single writer-name input in the header, held in JS state and mirrored to
`localStorage` so it survives a reload, sent as `reviewed_by` / `set_by` /
`proposed_by` on every write. Write controls are disabled until it is
non-empty.

This does **not** resolve DEFERRED §2.2 (writer identity per stem), and the
brief should not be read as resolving it. It is the smallest thing that lets
the eight write endpoints be called correctly, and it is superseded by HTTP
Basic with per-writer credentials when hosting lands (R1). Record it in
DEFERRED as provisional — see §6.

**D5 — The Color filter and its facet counts use `effective_color`, with
provenance.**

R5: display resolves to the override. So when the API is reachable, the
existing three Color chips and their counts filter on `effective_color`; an
overridden row shows a marker on its color chip, and the detail pane carries
the writer color, the computed color, the reason, and who set it. Offline, the
filter uses the computed `color` as today and no marker appears.

What this must not do: change `facetCounts`' *shape* or add a filter
dimension. It changes the value that one key function reads. The computed
rollup is still never written to, and `coverage.build_rows` returns exactly
what it returned before — that is acceptance criterion 5.

**D6 — After a write, re-fetch the detail for that standard only.**

No optimistic local mutation, and no full `/api/audit` refetch per write. On a
successful write, re-fetch `GET /api/standards/{standard_id}` and patch that
one row's review/override state from the response. The server's answer is the
displayed state.

Measure before assuming this is free: `GET /api/audit` calls
`coverage.build_rows` over the 160MB `mh2.db` on every request, and
`GET /api/standards/{id}` additionally calls `build_tags_by_standard` and
`node_lookup.build_node_lookup`. Time both against the real database early
(`curl -o /dev/null -s -w "%{time_total}\n"`). If detail latency is bad enough
to make expanding a row feel broken, report it and stop — do not invent a
cache in this session.

---

## 3. Do not touch

- `mh2/coverage.py` and the rollup computation. Color stays derived.
- `ROW_DICT_KEYS`, `row_dict`, `tag_dict` field sets. DEFERRED §0 is closed.
- `mh2.db`'s schema, `mh2/schema.sql`, and `scripts/rebuild.py`'s ingest order.
- `node_standards.status` / `.reviewed_by` / `.reviewed_at` — not-for-use. Do
  not populate, do not drop.
- **The whole `app/` package** (`db.py`, `review.py`, `styles.py`,
  `pages/1_Gap_pool.py`). This is MH2's existing Streamlit alignment-review
  tool. It reads `mh2.db` via `config.DB` and its single write path,
  `db.write_ruling()` (`app/db.py:318`), upserts `node_standards` — called
  from `app/review.py:228,234,264,287,292`. It already does the full tag CRUD
  that R6 puts out of scope here. The two tools are not in conflict; nothing
  in this session is a reason to open `app/`.
- The static render's sheet-then-grade shape and its existing filters. Rev 8
  §1.2 holds: no stem filter is added.
- SQL in routes. `review_api.py` has none (`grep -cin` → 0) and must still
  have none. New query shapes belong in `mh2/review_store.py` or
  `mh2/node_lookup.py`.
- SQLite from the page. R1: all state moves through HTTP.
- React, hosting, HTTPS, auth.
- The standards-coverage tool (standing project-wide restriction).

---

## 4. Acceptance criteria

Literal predicates. Not restatements of §0's figures.

1. `.venv/bin/python -m pytest -q` passes, with no fewer passing tests than
   the pre-change run recorded at the start of the session.
2. `set(render_static.row_dict(r).keys()) == render_static.ROW_DICT_KEYS`
   still holds and `ROW_DICT_KEYS` still contains exactly 15 names —
   i.e. `tests/test_render_static.py` passes **unmodified**.
3. `grep -c "fetch(" scripts/render_static.py` > 0, and loading `/` from a
   running `uvicorn review_api:app` shows the writer-name field and the three
   R6 actions.
4. Opening `data/reports/coverage.html` directly as `file://` renders 2,987
   rows, shows **zero** write affordances, and logs no console error and no
   failed network request.
5. Setting an override through the UI, then running `coverage.build_rows(con)`
   against `mh2.db`, returns the same row count and the same
   Green/Yellow/Red distribution as before the override. (Rev 9 criterion 6.)
6. After one review recorded through the UI:
   `SELECT COUNT(*) FROM standard_review` in `data/build/mh2_seq.db` is 1, and
   `SELECT COUNT(*) FROM node_standards` in `mh2.db` is unchanged from before.
7. `python scripts/rebuild.py` then reconcile leaves that
   `standard_review` count at 1, and `data/reports/review_reconcile.txt`
   regenerates without error. (Rev 9 criterion 1.)
8. A proposal created through the UI appears in
   `GET /api/worklist`'s `by_ladder_file` under the correct
   `ladder_file`, grouped by file and not by stem — and
   `ComparingandOrdering` appears as a single file entry, not two. (Rev 9
   criterion 7. The worklist UI is out of scope; its *endpoint* answering
   correctly for UI-created data is not.)
9. `grep -n "sqlite3\|INSERT\|UPDATE\|DELETE" scripts/render_static.py` shows
   no SQL beyond the existing read path, and the emitted HTML contains no
   SQL at all.
10. `grep -cin "select \|insert \|update \|delete " review_api.py` is still 0.
11. `app/` is byte-identical before and after. Note that `app/` is
    **untracked** in git (`git status --porcelain app/` → `?? app/`), so a
    clean-status check does not work here. Use instead, before and after:
    `find app -name "*.py" -print0 | sort -z | xargs -0 md5sum` — the two
    outputs must match exactly.
12. No new third-party runtime dependency. No CDN script tag, no bundler, no
    npm. The emitted HTML stays self-contained (D1).

---

## 5. Suggested entry point

Not row 1 of 2,987, and not row 1 of the 682 Green. Rev 9 §4's suggestion
stands: the **82 computed-Green-and-flagged** rows are the highest-yield
starting filter, because they are where the rollup and the flags disagree.
Predicate for that figure:
`coverage.flags_summary`'s `green_with_flag`, i.e.
`sum(1 for r in rows if r.color == "Green" and r.flagged)`
(`mh2/coverage.py:746`).

That is a suggestion about where a writer starts, not a new filter to build.

---

## 6. New DEFERRED entries this session owes

Add to §5's carried-forward list:

- **Writer identity is provisional (D4).** A header name field mirrored to
  `localStorage`, trusted entirely. Not authentication, not per-stem identity,
  and not a resolution of §2.2. Superseded by HTTP Basic at hosting.
- **`GET /api/worklist` has no consuming UI.** The edit pass is a separate
  session. The endpoint is exercised by acceptance criterion 8 but no writer
  can see it.
- **`/api/audit` and `/api/standards/{id}` latency is unmeasured against the
  160MB database.** Both rebuild coverage from scratch per request. If D6's
  timing check finds a problem, this is where the caching question gets
  recorded rather than solved.
- **Small doc drift, not code drift:** DEFERRED.md §0's own field table still
  spells the property `colour`, while the code and §5 both say `color`. The §0
  table is the last `colour` in the repo outside `schema_seq.sql`'s two
  explanatory comments. Fix it in whatever commit next touches that file; it
  is a typo in a closed contract's table, not a rename.
