# Grade Sequencing Tool — build plan rev 1 (2026-09-30)

> **Reconciled with branches 2026-09-30.** S1b is rewritten for the real
> `node_grade_kind` table. Gate B uses `seq/prototype`'s HTML and its design
> questions. S1/S8 reuse `seq/analysis`'s period parser, and S5/S6/S8/S10
> acceptance carry the analysis and prototype findings. Vocabulary is
> `unconfirmed` throughout. The stated-link capture fix is listed as an
> out-of-sequence pipeline item (O11).

This plan covers the ordered, scoped Claude Code (Sonnet) sessions from here to v1.
It depends on `docs/seq_orientation_rev1.md` (design, rulings R-S*, open
rulings O*) and `docs/seq_data_model_rev1.md` (DDL, reconcile contract, routes).

**Session rules (from brief §12, unchanged):**

- Opus designs and Sonnet implements.
- Each session covers one view or one concern.
- No full rebuilds without asking.
- Each session stops at its checkpoint and reports before the next one starts.
- Every acceptance number carries its literal predicate.
- A figure that cannot be reproduced is reported, never chased (DEFERRED §6).

---

## 0. Inherited for every session

**Do not touch** (list `S*` additions per session on top of this):

1. Everything in handoff rev 12 §5:
   - the `FastAPI(title=…, dependencies=[Depends(require_writer)])` construction line;
   - the rule that an unset `MH2_AUTH_USERS` means auth is off;
   - `entrypoint.sh` running `rebuild.py` on every start.
2. Handoff rev 10 §5, which is the recoverable form of rev 11 §4 (rev 11 is not in the repo):
   - `mh2/coverage.py` and the rollup;
   - `ROW_DICT_KEYS` / `row_dict` / `tag_dict`;
   - `mh2/schema.sql` and `scripts/rebuild.py`'s ingest order;
   - the `node_standards` review columns;
   - `app/`;
   - the `data/build/*` + `!data/build/mh2_seq.db` glob;
   - SQL in routes;
   - the audit's static render (`scripts/render_static.py`) and its tests.
3. DEFERRED §6 closed rulings. In particular, **never read `node_grade` as
   containment in the sequencer, and never let the audit read placements**.
   The sequencer does not import `mh2.coverage`.
4. `mh2/schema_seq.sql`, `mh2/review_store.py`, `mh2/reconcile_review.py`,
   `mh2/ladder_edits.py` and every existing review route's behavior.
5. `data/source/**` (read-only). Do not write to `mh2.db` from any sequencer
   module; open it with `mode=ro`.
6. The grade_type branch's files (another agent owns them): `mh2/load_grade_ruling.py`,
   `mh2/grade_split_review.py`, the `node_grade_ruling` / `node_grade_kind`
   DDL in `mh2/schema.sql`, and `docs/review/grade_split_review.csv`. Consume
   its output through `mh2/grade_kind.py` only.
7. The `seq/analysis` and `seq/prototype` scripts and their review CSVs
   (`scripts/report_period_estimates.py`, `scripts/report_cross_stem_links.py`,
   `scripts/render_seq_prototype.py`, `docs/review/*`, `docs/prototype/*`).
   Sequencer modules may **import** the pure parser from
   `scripts.report_period_estimates` (data model §5.4). They must not edit these
   files, except for the one S8 move described there.

**Every session's checkpoint also includes:**

- `md5sum` of every do-not-touch file it could plausibly reach, taken before and
  after, with the two lists equal;
- the full test suite passing (`python -m pytest -q`), reporting the
  pre-session count and the post-session count;
- a grep showing no `execute(` / `sqlite3` in `seq_api.py`;
- a short report in the style of the handoffs.

**Figures quoted below** are from `data/build/mh2.db` with
`MAX(ingest_log.ts) = '2026-09-09 15:11:11'`. If the database has been rebuilt
since, the session re-measures with the same predicate and reports the new
value. It does not force the old one.

---

## 1. Gates (decisions, not sessions)

| Gate | Before | Needs |
|---|---|---|
| **A** | S3 | Rulings O1 (ownership) and O2 (modules/topics) confirmed or defaulted. O4 (DEFERRED amendment) applied. |
| **B** | S5 | O6 (UI stack; §4 below). A paper prototype of the G2 slice walked through with one grade builder, and the orientation §6 interaction adjusted if they object. The prototype is **`docs/prototype/seq_prototype_g2.html`** (branch `seq/prototype`; regenerate any grade with `scripts/render_seq_prototype.py --grade N`; layout not yet eyeballed), plus the S1 text report. The walkthrough also answers: (1) does G6/G7 (106/107 chips) open with stems collapsed; (2) does a 9-chip strip wrap or scroll; (3) O10 (Flagged vs Pairings); (4) O12 (C/S band Goal-only); (5) O13 (goal gaps). These are the prototype README's questions 1, 2, 8, 4 and 5 (orientation §6.1, §6.8, §4.5). |
| **K** | any time | `seq/grade_type` merged **and** `scripts/rebuild.py` run, since its new columns are in `CREATE TABLE` and need a rebuild. Sessions before it run on `kind_source='fallback'`, and S1's adapter is re-checked when it lands (session **S1b**, about 30 minutes). John's later `ccss_default_grades` ruling needs no session: it changes rows, not shape. |
| **P** | pilot | O3 (grade builder logins). S13 done. Hosting live (DEFERRED §8). |

---

## 2. Sessions at a glance

| # | Concern | Touches existing files? | Depends on |
|---|---|---|---|
| S1 | Read-side slice module + G2 paper-prototype report | **No** | — |
| S1b | Point `grade_kind.py` at `node_grade_kind` on a rebuilt DB | No | Gate K, S1 |
| S2 | Read API: router, slice / node / compare routes, auth coverage | `review_api.py`: one `include_router` line | S1 |
| S3 | Identity extraction + placement schema + `seq_store` + event log | `review_api.py` (import only), `config.py` (+1 constant) | Gate A, S2 |
| S4 | Placement write routes + derived status + reconcile report CLI | No | S3 |
| S5 | Slice page, read-only: strips, grouping, view controls, URL state | No | Gate B, S2 |
| S6 | Node drawer | No | S5 |
| S7 | Module builder rail: place / move / co-place / ungroup / remove, cross-grade prompt, off-grade confirm | No | S4, S6 |
| S8 | Calibration + period estimate + guardrail readout | No | S7 |
| S9 | Comparison bottom sheet + co-place action | No | S7 |
| S10 | Consistency badges + Flagged filter | No | S7 |
| S11 | Needs-attention queue: reattach / acknowledge / remove | No | S4, S7 |
| S12 | Saved views | No | S5, S3 |
| S13 | Durability: backup + text dump + `.gitattributes` textconv + HOSTING addendum; v1 smoke test | `.gitattributes` (new), `docs/HOSTING.md` (append only) | S1–S12 |

**Outside the S-sequence (pipeline, not sequencer):** the stated-link capture
fix in `mh2/ingest_ladders.py` (ruling O11). It changes the `mh2.db` ingest, so
it needs John's authorization, as grade_type did, and it is not a sequencer
session. Nothing in S1–S13 depends on it. S6 shows `node_links` as captured.

---

## 3. Sessions in detail

### S1: read-side slice module and paper prototype

The full brief is in Appendix A.

### S1b: grade_type adapter re-point (after Gate K)

- **Goal:** `kind_source(con) == 'grade_type'` against a DB rebuilt after the
  merge. `build_grade_kinds` reads `SELECT node_id, grade, kind FROM
  node_grade_kind`. The values are already `STORED_KINDS`, so there is no
  mapping. Every `node_grade` row without a kind row becomes `unknown`
  (data model §5.2). If S1 already implemented this against the trial shape,
  S1b is a test-only session.
- **Files:** `mh2/grade_kind.py`, `tests/test_grade_kind.py`.
- **Acceptance:**
  1. The key set of `build_grade_kinds` equals `SELECT node_id, grade FROM node_grade` (535 on the 2026-09-09 data). Every value is in `STORED_KINDS + ('unknown',)`.
  2. Distribution: `Counter(build_grade_kinds(con).values())` equals `SELECT kind, COUNT(*) FROM node_grade_kind GROUP BY 1` plus `unknown` = `SELECT COUNT(*) FROM node_grade g LEFT JOIN node_grade_kind k ON k.node_id=g.node_id AND k.grade=g.grade WHERE k.node_id IS NULL`. Expected on 2026-09-09 data with no `ccss_default_grades` column: core 155, span 213, unconfirmed 143, state_extension 0, unknown 24. Re-measure; do not force.
  3. Through `kind_for`, the 24 split as leaf 13 and unknown 11 (out_of_band 9, blank `ruling_type` 2). No in-grade row reads as `off_grade`: `kind_for(n, g, …) != 'off_grade'` for every `node_grade` row.
  4. G2 read states: core 13, span 14, unconfirmed 6, leaf 2. The G2 state_extension set is reported as a set (today ∅). It is compared against `grade_split_review.csv`'s *proposed* G2 extensions, `{TIM-0010, TIM-0011}`, and the difference is reported as "awaiting `john_ruling`", not as a bug.
  5. A synthetic worksheet with `ccss_default_grades` produces `state_extension` read states and no `off_grade`.
  6. The S1 report is regenerated and diffed against the fallback version. Expected diff: `state=unknown` → core/span/unconfirmed on every in-grade line except the 11 `unknown` rows.
- **Checkpoint:** stop and report the distribution with its predicate, and whether O7's optional branch change is still wanted.

### S2: read API

- **Goal:** `seq_api.py` with `router = APIRouter(prefix="/api/seq")` exposing:
  - `GET /grades`,
  - `GET /grades/{grade}/slice`,
  - `GET /nodes/{source_key}`,
  - `GET /compare?keys=`.

  It is included into the app with `app.include_router(seq_api.router)` placed
  after the app is constructed. Routes call only `mh2.grade_slice`.
- **Inputs:** S1 modules; data model §9/§10.
- **May touch:** new `seq_api.py`, new `tests/test_seq_api.py`, and
  `review_api.py` for **one import plus one `include_router` line** only.
- **Do not touch:** §0, plus every other line of `review_api.py`.
- **Acceptance:**
  1. `GET /api/seq/grades/2/slice` returns `counts.in_grade_nodes` equal to `SELECT COUNT(DISTINCT node_id) FROM node_grade WHERE grade='2'`.
  2. `/compare` with 5 keys returns 422.
  3. `/compare` with an unknown key returns 404 naming the key.
  4. With `MH2_AUTH_USERS="t:p"`, each new route returns 401 without credentials and 200 with them. This is a new test that parallels `test_review_api_auth.py`, which stays unmodified.
  5. `grep -nE "execute\(|sqlite3\." seq_api.py` matches nothing.
  6. Every existing test passes unmodified.
- **Checkpoint:** report the route list, latency for the slice on the real DB (as DEFERRED §7 measured `/api/audit`), and the auth test results.

### S3: identity, placement schema, store

- **Goal:**
  - (a) Move `_configured_writers` and `require_writer` verbatim into `mh2/auth.py`. `review_api.py` imports `require_writer` under the same name, and the `FastAPI(...)` construction line is byte-identical. Add `current_writer` (it returns the same value, for per-route use).
  - (b) Add `mh2/schema_placement.sql` as specified in data model §3, plus `config.SCHEMA_PLACEMENT`.
  - (c) Add `mh2/seq_store.py`: sequence/module/slot/placement CRUD with sparse order keys (§4), `BEGIN IMMEDIATE` on multi-row operations, rev checks raising a `Conflict` exception, `placement_event` appended on every change, and `*_by` / `*_at` stamped internally.
- **Assumption (confirm):** the verbatim move of `require_writer` into
  `mh2/auth.py` is acceptable under rev 12 §5, which protects the gate's
  *construction* and not the function's module. The alternative is a
  `current_writer` that re-parses `MH2_AUTH_USERS`, which duplicates logic. If
  John prefers that, S3 does it instead and leaves `review_api.py` untouched.
- **May touch:** new `mh2/auth.py`, `mh2/schema_placement.sql`,
  `mh2/seq_store.py`, `tests/test_seq_store.py`, `tests/test_auth_module.py`;
  `review_api.py` (the definitions move out and one import line comes in);
  `config.py` (+1 constant).
- **Do not touch:** §0. `tests/test_review_api_auth.py` passes **unmodified**.
- **Acceptance:**
  1. After `ensure_schema` on a copy of the real `mh2_seq.db`, `SELECT name FROM sqlite_master WHERE type='table'` ⊇ {`grade_sequence`,`module`,`slot`,`placement`,`placement_event`,`saved_view`,`placement_schema_meta`}. The review tables' row counts are unchanged (`SELECT COUNT(*)` on each of the 6 review tables, before = after).
  2. Placing the same `source_key` twice in one sequence raises. Placing it in two sequences succeeds.
  3. 11 consecutive inserts at the same position trigger exactly one `renumber` event, and order is preserved.
  4. A stale `expected_rev` raises `Conflict`, and the row is unchanged.
  5. `SELECT COUNT(*) FROM placement_event` equals the number of store mutations made in the test.
  6. No store function accepts a `*_at` parameter. Tests pass `writer` and nothing else as identity.
  7. `grep -n "DELETE FROM" mh2/seq_store.py` matches only `saved_view`.
- **Checkpoint:** stop and report the DDL as applied, test names, and the md5 of `review_api.py`'s `FastAPI(` line before and after.

### S4: placement writes and derived status

- **Goal:**
  - Write routes from data model §9: sequences, modules, placements, move, co-place, ungroup, remove, reattach, acknowledge, attention, events. Every write uses `Depends(current_writer)`, and no body has a `*_by` field.
  - `mh2/placement_status.py` implements data model §6.2–§6.4.
  - `mh2/reconcile_placements.py` is a report-only CLI writing `data/reports/placement_reconcile.txt`.
- **May touch:** `seq_api.py`, new `mh2/placement_status.py`,
  `mh2/reconcile_placements.py`, `tests/test_placement_status.py`,
  `tests/test_seq_api_writes.py`.
- **Do not touch:** §0 and `scripts/rebuild.py` (a tail step is deferred; data model §6.1).
- **Acceptance** (synthetic DBs; each §6.3 scenario is a test):
  1. Reword → `orphaned`, with the old node's text in `node_text_seen`.
  2. The same text in another stem → the top suggestion is that node.
  3. Moving a node to another C/S in the same stem → `ok` with the `relabelled` flag.
  4. Removing the grade from `node_grade` → `grade_changed`, `seen=core`, `now=off_grade`.
  5. `unknown → core` → `ok`.
  6. Reattach preserves `placement_id`, `order_key`, and calibration, sets `reattached_from`, and writes a `reattach` event.
  7. After running `reconcile_placements` on a copy of real `mh2_seq.db` + `mh2.db`, a hash of every placement-table row is identical before and after (the run writes no rows).
  8. POST placement for an off-grade node without `confirm_off_grade` → 422.
  9. A structural write with a stale sequence `rev` → 409 whose body carries the current sequence.
  10. A request body containing `placed_by` is rejected, or the field is ignored and the stored value is the authenticated writer. Test both with auth on.
- **Checkpoint:** report the reconcile report's section counts on real data (expected: all zero, since no placements exist yet).

### S5: slice page, read-only (after Gate B)

- **Goal:**
  - `GET /seq` serves the page, and `GET /seq/assets/{name}` serves whitelisted `seq.js` / `seq.css` / module files by explicit routes. **No `app.mount`.**
  - The page fetches `/api/seq/grades` and `/api/seq/grades/{g}/slice` and renders super-stem → stem → C/S progression strips (in-grade solid, context dimmed, state badges).
  - It has a grade picker; per-stem and per-C/S show/hide; stem reorder by chevrons; and toggles for context, unplaced-only (inert until S7), leaves, state ext, and links.
  - The whole view state is one object mirrored to the URL query string.
- **Stack:** as ruled at Gate B (recommended: plain JS, §4).
- **May touch:** new `web/seq/*`, `seq_api.py` (the page routes), and new `tests/test_seq_page.py`.
- **Do not touch:** §0 and `scripts/render_static.py`.
- **Acceptance:**
  1. The rendered chip count for G2 equals the slice payload's `in_grade_nodes + context_nodes`. Today that is 35 + 22 = 57 with leaves on, per the orientation §4.4 predicates. Measured by a DOM count in a headless check, or by a documented manual count if no headless browser is available. For every grade, the payload's `in_grade_nodes + context_nodes` equals the orientation §6.1 predicate (today PK 27, K 57, 1 59, 2 57, 3 70, 4 82, 5 79, 6 106, 7 107, 8 67, A1 80; these match `seq/prototype`'s counts).
  2. With auth on, `/seq` and every asset return 401 without credentials.
  3. The page has no external URL (the same check `test_render_static.py` applies to the audit page).
  4. Reloading with a URL restores identical view state.
  5. No filtering or grouping logic lives in JS beyond show/hide of server-grouped data. Grep for `grade_order` / `kind` derivation in JS returns nothing.
- **Checkpoint:** screenshots of G2, G4 and **G7** (the largest slice), a 9-chip strip, and a narrow viewport. Stop for John's look before the drawer.
- **Note:** `scripts/render_seq_prototype.py` already renders strips, toggles and a compare sheet as a single static file. S5 may crib its CSS and markup. It must not import from it, and its grouping logic must come from `mh2/grade_slice.py`, not the prototype's Python (R-S4, R-S10). Two differences are deliberate: the prototype keeps toggles in `localStorage` and compare state in the URL **hash**, while the design uses one state object in the URL **query** (orientation §6.2, §6.4).

### S6: node drawer

- **Goal:** a non-modal right drawer from `/api/seq/nodes/{source_key}` with:
  - a C/S band (Goal, label, stem) visually separate from the node band;
  - empty rows collapsed, with "show all";
  - the grade ruling (raw text, state for every grade);
  - placements everywhere;
  - stated links as chips (an exact stem name links to that stem's filter);
  - product refs;
  - the period hint for the current grade.

  The slice stays interactive while the drawer is open.
- **May touch:** `web/seq/*`, `mh2/grade_slice.py` (drawer payload only), and its tests.
- **Acceptance:**
  1. For every node, the drawer's non-empty field set equals `SELECT DISTINCT field FROM node_fields WHERE node_id=?`, plus goal when non-empty. Tested in Python against the payload.
  2. Goal renders only in the C/S band, and it is the only field there (O12 default).
  3. Goal gaps (O13 default): for the 3 C/S with no goal (`HAVING SUM(goal IS NOT NULL AND TRIM(goal)<>'')=0`), the C/S band shows "No goal in ladder" and does not collapse it. For the 6 partly-filled C/S, every node's drawer shows the same goal.
  4. Stated links render as captured, labelled as such (O11). A chip whose text equals a stem name links to that stem's filter.
  5. Opening the drawer does not reset scroll or view state (manual check, stated in the report).
- **Checkpoint:** screenshot plus report.

### S7: module builder rail

- **Goal:**
  - A rail beside the slice: create or pick the grade's sequence; modules (create, rename, chevron reorder, soft remove when empty).
  - Place from a chip or the drawer into a chosen module (append).
  - Chevron move within and across modules, co-place into a slot, ungroup, and soft remove.
  - The cross-grade prompt: if `placed_elsewhere` is non-empty, show where, and ask for a differentiation note (optional, never blocking).
  - Off-grade placing asks for explicit confirmation.
  - The chip's "placed here" badge updates, and the unplaced-only toggle goes live.
  - A 409 re-fetches and re-renders with a notice.
- **May touch:** `web/seq/*`; `seq_api.py` only if a payload field is missing (report it).
- **Acceptance:**
  1. After placing N nodes through the UI, `SELECT COUNT(*) FROM placement WHERE sequence_id=? AND removed_at IS NULL` = N and the number of `place` events = N.
  2. The rail order equals `ORDER BY module.order_key, slot.order_key, placement.order_in_slot`.
  3. Two browser sessions editing the same sequence produce a 409 on the second structural write, with no lost row.
  4. Removing and re-placing leaves 2 rows for that key (1 removed, 1 active).
- **Checkpoint:** a screen recording or screenshots of a 3-module G2 draft. Stop.

### S8: calibration, period estimate, guardrail

- **Goal:** add `mh2/guardrail.py` per data model §7. The page gets:
  - per-placement calibration (three buttons plus unset);
  - a period estimate field (instructional periods; O8) with the ladder hint shown and offered as a prefill only when `hint_value` is set (data model §5.4), never auto-saved. Day/lesson, range, `part_of` and un-graded multi-grade hints show as text;
  - a guardrail readout per module and per sequence (counts, periods, time coverage, and reference marks at 25/50/25 and 40/45/15), refreshed from each write's response. The time marks are drawn only when `show_time_targets` is true (data model §7);
  - optionally, move `parse_period_notes` / `usable` / `Estimate` verbatim into `mh2/period_notes.py`, with `scripts/report_period_estimates.py` re-importing the same names. That is the only permitted edit to a `seq/analysis` file, and `tests/test_period_estimates.py` must pass unmodified.
- **Acceptance:**
  1. `guardrail` unit tests cover empty, all-unset, and mixed inputs.
  2. The output has no pass/fail key: `set(result) ∩ {'pass','fail','ok','status'} = ∅`.
  3. `period_estimate` stays NULL until the builder saves it: `SELECT COUNT(*) FROM placement WHERE period_estimate IS NOT NULL AND NOT EXISTS (SELECT 1 FROM placement_event e WHERE e.placement_id=placement.placement_id AND e.action='set_period')` = 0.
  4. The period hint, run over real data, is reported per grade as the count of in-grade nodes with `hint_value` not null. Report the numbers and do not target them. For orientation, `seq/analysis`'s strict period coverage is an upper bound (G2: 23 of 33 non-leaf). `hint_value` is stricter (exact only), so expect less.
  5. No hint with `unit in ('day','lesson')` has `hint_value` set.
  6. `show_time_targets` is false for coverage < 0.5 and true at 0.5, in unit tests.
- **Checkpoint:** report plus screenshot.

### S9: comparison bottom sheet

- **Goal:**
  - A compare tray, and a bottom sheet from `/api/seq/compare` that is live with the slice and can be open together with the drawer.
  - Maximum 4 columns, then horizontal scroll. Shared-standard rows self-highlight.
  - `?compare=` holds the selection in the URL.
  - **Co-place in a module** puts every compared node that is not yet placed into one new slot in the chosen module, after confirming the off-grade ones.
- **Acceptance:**
  1. Pasting a URL with 2 keys opens the sheet with those 2 columns.
  2. The rows marked shared equal the codes in the intersection of the columns' `node_standards_parsed` sets (`state IS NULL`), tested in Python.
  3. Co-place of k unplaced nodes gives one new slot with k placements and k `place` events.
- **Checkpoint:** screenshot plus report.

### S10: consistency badges and the Flagged filter

- **Goal:** `mh2/seq_checks.py` per data model §8. Badges render inline on chips and rail items. A **Flagged** filter shows only chips and placements that carry a `structural` badge. A separate **Pairings** toggle shows shared-code badges, grouped by partner stem (O10). There is no report page.
- **Acceptance:**
  1. The shared-code partner set for each node equals the SQL: codes with `state IS NULL` on that node, joined to other-stem nodes with the same code.
  2. The whole-corpus count of shared codes equals `SELECT COUNT(*) FROM (SELECT standard_code FROM node_standards_parsed p JOIN nodes n ON n.node_id=p.node_id WHERE p.state IS NULL GROUP BY standard_code HAVING COUNT(DISTINCT n.stem_id)>1)`, which was 30 on the 2026-09-09 build. The cross-stem node-pair set equals `docs/review/cross_stem_links_shared_ccss.csv` from `seq/analysis` (180 pairs), compared as a set.
  3. A predecessor-inversion fixture yields a warning. The save is never refused.
  4. The Flagged filter contains no chip whose only badge is shared-code. Report the G2 Flagged count with no sequence, and on a fixture sequence, each with its predicate. If "owed and unplaced" makes Flagged nearly the whole slice on an empty sequence, report it; do not change the tiering in-session.
- **Checkpoint:** report.

### S11: Needs-attention queue

- **Goal:** a panel at the top of the rail with a count, listing orphaned and grade_changed placements grouped by module. Orphans show `node_text_seen` and `ladder_file_seen` and up to 3 suggestions shown side by side. Actions are reattach, acknowledge, and remove. Ghost chips hold their position in the rail.
- **Acceptance:**
  1. On a fixture where one node is reworded between two `mh2.db` builds, the panel lists exactly that placement, and reattach to the suggestion clears it.
  2. Nothing changes in `placement` until a click (the row hash is unchanged after a page load).
- **Checkpoint:** report.

### S12: saved views

- **Goal:** `/api/seq/views` CRUD scoped to `current_writer`. The page can save, load, rename, and delete named views per grade. The stored object is the URL state object. Unknown stems or C/S in a loaded view are ignored silently.
- **Acceptance:**
  1. Views are writer-scoped: with auth on, user A cannot list or load user B's views.
  2. Save → reload → load gives identical state.
- **Checkpoint:** report.

### S13: durability and the v1 smoke test

- **Goal:** add `scripts/dump_seq.py` (`iterdump` → `data/seq_export/mh2_seq.sql`), `scripts/load_seq_dump.py`, and `scripts/backup_seq.py` (`Connection.backup`, keeping N copies). Add `.gitattributes` with `data/build/mh2_seq.db diff=sqlite` plus documented `textconv` setup. Append a "Sequencer" section to `docs/HOSTING.md`: the server copy is the record, backup schedule, and the write → restart → read-back smoke test for a placement.
- **Acceptance:**
  1. dump → load into an empty file → dump again gives byte-identical dumps.
  2. The backup of a live DB passes `PRAGMA integrity_check`.
  3. The HOSTING addendum's smoke test runs locally with auth on.
- **Checkpoint:** a v1 readiness report against orientation §7's ten items, each marked done or not done.

---

## 4. UI stack decision (Gate B, ruling O6)

**Recommendation: plain JavaScript**, meaning static `index.html` + `seq.css`
+ native ES modules (`<script type="module">`). There is no bundler, no Node
toolchain, and no vendored framework. The files are served by explicit
FastAPI routes. The page fetches JSON and renders. Architecture: one
`state` object; one `render<Region>(state, data)` function per region (slice,
drawer, sheet, rail, readout); an event handler updates `state`, calls the
API when needed, and re-renders only its region(s). There are 4–6 modules of
a few hundred lines each.

| Factor | React | Plain JS |
|---|---|---|
| **John reads Python, not TSX.** Brief §3's own mitigation ("keep the frontend dumb") admits the risk. | Every UI bug is in a language and toolchain he does not read | Readable enough to follow in a debugger. Logic is already forced into Python by R-S10. |
| **Toolchain on this project's machines.** The corporate Mac blocked the flyctl install script and IT was hesitant about Homebrew (HOSTING.md). The Azure target is a Python web app. | Needs Node/npm locally or a Node stage in the Docker build and CI | Nothing new. The Dockerfile and the Azure shape are unchanged. |
| **Existing pattern** | New | The audit is already FastAPI + static HTML + vanilla JS over about 3k rows (`render_static.py`) and is tested and hosted |
| **Browse-shaped interaction.** Brief §3's reason to leave Streamlit (full rerun per click). | Solves it | Also solves it. The objection was Streamlit's execution model, not the absence of React. Region re-render keeps scroll and drawer state. |
| **Drag-and-drop** | This was React's strongest case | Deferred (R-S8). Chevrons are a button. |
| **Several live panels at once** (slice + drawer + sheet + rail) | Component state is idiomatic | The real risk. It is mitigated by the single state object and by the server owning every derived value (status, badges, guardrail). |
| Claude Code strength | Strongest | Strong. The work is DOM rendering of server JSON, not framework-heavy. |

**Revisit trigger:** drag-and-drop becomes necessary in pilot; or `web/seq`
JS passes about 2,500 lines; or regressions caused by cross-region state
become recurrent (more than two in a session report). **Fallback before
React:** vendor Preact + htm as one committed file (about 10 KB, no build
step, no CDN, so the no-external-URL rule holds). It gives components without
a toolchain and is a smaller step than React.

**Why the decision sits at Gate B and not earlier:** S1–S4 are Python only and
stack-independent. Deciding after one grade builder has walked the paper
prototype (the S1 report) is what orientation rev 0 §6 asked for ("decide
after the interaction model, not before"). The recommendation is stated now
so nothing waits on it.

---

## Appendix A: S1 brief (ready to paste)

```text
# Session S1 — Grade sequencing: read-side slice module + G2 paper prototype

You are implementing in the MH2 repo (~/mh2_macro). Read these first, in full:
docs/seq_orientation_rev1.md (the design reference), docs/seq_data_model_rev1.md
§5 and §10, mh2/ingest_ladders.py (source_key(), FIELD_MAP, AUTHOR_STATUS_RE),
mh2/load_grade_ruling.py (incl. derive_kinds), mh2/node_lookup.py, mh2/schema.sql
(nodes, node_fields, node_grade, node_grade_ruling, node_grade_kind, grade_order,
stems, node_standards_parsed, node_links, product_refs, ingest_log).
Also skim, do not edit or import (except the parser named below):
scripts/report_period_estimates.py and docs/seq_analysis_rev1.md (seq/analysis);
scripts/render_seq_prototype.py and docs/prototype/README.md (seq/prototype),
which already groups a slice the same way and is a cross-check for your counts.

## Goal
A pure read layer over mh2.db that produces a grade "slice" — the inventory a
grade owes, grouped super-stem → stem → concept/skill → progression strip —
plus a plain-text paper-prototype report of it for one grade. No routes, no UI,
no writes to either database. This output is what John walks through with a
grade builder before any UI is built.

## Build
1. mh2/grade_kind.py — the ONLY module that knows how per-(node, grade) kind
   is stored. Contract (data model §5.2):
     STORED_KINDS = ("core","span","state_extension","unconfirmed")
     READ_STATES  = STORED_KINDS + ("off_grade","leaf","no_grade","unknown")
     kind_source(con) -> "grade_type" | "fallback"
     build_grade_kinds(con) -> dict[(node_id, grade), str]
     kind_for(node_id, grade, kinds, ruling_by_node) -> str
   The kind lives in table node_grade_kind(node_id, grade, kind, basis) from
   branch seq/grade_type; kind values already equal STORED_KINDS. kind_source:
   "grade_type" iff sqlite_master has node_grade_kind and it has >= 1 row, else
   "fallback" (the 2026-09-09 mh2.db predates the branch, so expect fallback
   unless John has merged and rebuilt). build_grade_kinds: one entry per
   node_grade row; value = node_grade_kind.kind if a row exists, else
   "unknown" (fallback: all "unknown"). No row is normal for out_of_band,
   blank ruling_type and leaf nodes. kind_for precedence: resolution 'leaf' ->
   "leaf"; 'no_grade_field' -> "no_grade"; (node_id, grade) in kinds -> that
   value; else "off_grade". Test both paths on synthetic DBs, one with a
   node_grade_kind table (include a node_grade row with no kind row). If the
   real table's columns or CHECK differ from data model §5.2, STOP and report.
2. mh2/grade_slice.py — build_slice(con, grade, *, include_leaves=True) -> dict
   in exactly the shape of data model §10. Rules:
   - in-grade = node has a node_grade row for `grade` (placement reading;
     NEVER containment — do not import mh2.coverage).
   - concept/skill key = (stem_id, concept_skill), the raw string. A C/S is
     included iff it has >= 1 in-grade node (respecting include_leaves).
   - every node of an included C/S appears in its strip, ordered by nodes.seq;
     non-in-grade ones are context (in_grade=false) with their state.
   - super-stem = stems.domain; stem name = stems.name, whitespace-collapsed
     (reuse node_lookup.build_stem_names).
   - label_display = raw label with a trailing author marker stripped,
     regex r"\s*\(\s*[A-Z][a-z]+\s*[-–—]?\s*done\s*\)\s*$", re.I. The raw label
     is kept in `label` and used for grouping.
   - C/S goal = the single distinct non-empty nodes.goal in the C/S; if more
     than one distinct non-empty goal exists, raise (today: 0 such C/S).
   - ladders_last_read = SELECT MAX(ts) FROM ingest_log.
   Open mh2.db with sqlite3.connect(f"file:{path}?mode=ro", uri=True).
   Also: period_hint(con, node_id, grade) -> {"text": str|None,
   "value": float|None, "unit": str|None, "qualifier": str|None,
   "low": float|None, "high": float|None} per data model §5.4. Do NOT write a
   parser: `from scripts.report_period_estimates import parse_period_notes,
   usable` (pure; seq/analysis). Selection: estimates naming this grade win;
   un-graded ones count only on a single-grade node; value only for
   qualifier 'exact', unit 'period', not group_total; everything else is
   text-only (value None).
3. CLI: `python -m mh2.grade_slice --grade 2 [--out PATH] [--no-leaves]`
   writes data/reports/grade_slice_G2.txt: a header (grade, kind_source,
   ladders_last_read, counts with the SQL predicates printed beside them), then
   per super-stem / stem / C/S: display label, goal (first 120 chars), and the
   strip as one line per node:
     [*] TIM-0010  3 (2 for some states)  state=unknown  hint="…"  node text…
   with [*] = in-grade, [ ] = context, [L] = leaf, [?] = no grade.
4. tests/test_grade_slice.py and tests/test_grade_kind.py on synthetic
   file-backed DBs (follow the fixtures style in tests/test_reconcile_review.py:
   _mh2_db/_node). Cover: strip order by seq; context inclusion; leaf toggle;
   off_grade/leaf/no_grade precedence; marker stripping; goal conflict raises;
   fallback kind = "unknown"; node_grade row with no node_grade_kind row =
   "unknown", never "off_grade"; period hint grade selection, multi-grade
   un-graded -> text only, day/lesson -> value None.

## May touch (create only)
mh2/grade_kind.py, mh2/grade_slice.py, tests/test_grade_kind.py,
tests/test_grade_slice.py, and the generated data/reports/grade_slice_G*.txt.
Nothing else. In particular no change to review_api.py, config.py, schema
files, or any existing module.

## Do not touch
Everything in docs/seq_build_plan_rev1.md §0 (rev 12 §5; rev 10 §5 incl.
mh2/coverage.py, mh2/schema.sql, rebuild.py ingest order, app/, render_static;
DEFERRED §6 closed rulings; schema_seq.sql / review_store / reconcile_review /
ladder_edits; data/source; the grade_type branch's files; the seq/analysis
and seq/prototype scripts, docs/review/*, docs/prototype/*). Do not run
scripts/rebuild.py. Do not write to mh2.db or mh2_seq.db.

## Acceptance criteria (literal predicates; run against data/build/mh2.db)
First record SELECT MAX(ts) FROM ingest_log. Figures below are from
'2026-09-09 15:11:11'; if it differs, re-measure with the same predicate and
report both — do not force the old number.
1. Set equality, every grade in ('PK','K','1','2','3','4','5','6','7','8','A1'):
   {source_key of in_grade nodes in build_slice(g)} ==
   {n.source_key FROM nodes n JOIN node_grade g ON g.node_id=n.node_id
    WHERE g.grade=?}.   (G2 count today: 35)
2. G2, include_leaves=False: in-grade count ==
   SELECT COUNT(DISTINCT g.node_id) FROM node_grade g JOIN node_grade_ruling r
   ON r.node_id=g.node_id WHERE g.grade='2' AND r.resolution<>'leaf'  (33).
3. G2 C/S count == SELECT COUNT(DISTINCT n.stem_id||'|'||n.concept_skill) FROM
   nodes n JOIN node_grade g ON g.node_id=n.node_id WHERE g.grade='2'  (24);
   with include_leaves=False, add the resolution<>'leaf' join (22).
4. G2 context count == SELECT COUNT(*) FROM nodes n WHERE
   (n.stem_id||'|'||n.concept_skill) IN (SELECT n2.stem_id||'|'||n2.concept_skill
   FROM nodes n2 JOIN node_grade g ON g.node_id=n2.node_id WHERE g.grade='2')
   AND n.node_id NOT IN (SELECT node_id FROM node_grade WHERE grade='2')  (22).
5. Within every strip, seq is strictly increasing; no node appears twice in a
   slice.
6. Labels: number of distinct raw labels in the whole corpus matching the
   marker regex == SELECT COUNT(DISTINCT stem_id||'|'||concept_skill) FROM nodes
   WHERE concept_skill LIKE '%(%done)%'  (19), and zero label_display values
   match it.
7. Goal: build_slice does not raise for any grade on the real DB (0 conflicting C/S:
   SELECT COUNT(*) FROM (SELECT stem_id, concept_skill FROM nodes WHERE goal IS
   NOT NULL AND TRIM(goal)<>'' GROUP BY 1,2 HAVING COUNT(DISTINCT goal)>1) = 0).
8. kind_source() reported; with fallback, every in-grade node's state in
   G2 is "unknown" except leaf nodes ("leaf": COU-0024, MUL-0004 today).
   With grade_type (merged + rebuilt): G2 states core 13, span 14,
   unconfirmed 6, leaf 2 (predicate: SELECT COALESCE(k.kind,'none'), COUNT(*)
   FROM node_grade g LEFT JOIN node_grade_kind k ON k.node_id=g.node_id AND
   k.grade=g.grade WHERE g.grade='2' GROUP BY 1). Re-measure; do not force.
9. grep -n "coverage" mh2/grade_slice.py mh2/grade_kind.py → no import of
   mh2.coverage. grep -n "mode=ro" mh2/grade_slice.py → present.
10. Full suite: python -m pytest -q passes; report pre- and post-session counts.
11. md5sum of review_api.py, config.py, mh2/schema.sql, mh2/schema_seq.sql,
    mh2/coverage.py, mh2/review_store.py, mh2/reconcile_review.py,
    scripts/render_static.py, scripts/rebuild.py, and data/build/mh2_seq.db —
    identical before and after.
12. data/reports/grade_slice_G2.txt exists; its header counts equal 1–4.

## Checkpoint — stop here and report
- kind_source, and whether node_grade_kind is present in mh2.db (merged +
  rebuilt) or only on branch seq/grade_type.
- The figures for criteria 1–8 with predicates, and any discrepancy.
- The first two C/S blocks of grade_slice_G2.txt pasted verbatim.
- Period hint coverage for G2 in-grade nodes: count with value / with text
  only / none (report; no target).
- Anything in the data that contradicts docs/seq_orientation_rev1.md §4.
Do not start S2.
```
