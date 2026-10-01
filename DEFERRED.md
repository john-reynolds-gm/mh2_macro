# DEFERRED

Items consciously not done. Each carries **what it is**, **what it would
change**, and **what would trigger doing it**.

Nothing here is a blocker. An item becomes work when its trigger fires, not
because it is on this list. If an item has no trigger, it is not deferred — it
is abandoned, and should be deleted.

**Two mechanisms make deferral safe, and every item must use one:**

- a **pinned assertion** in `verify_bands.py` — sets, not counts, wherever a
  list is the real object. This is what tells you, months later, whether a fix
  moved something it shouldn't have.
- a **report file** in `data/reports/` — resolution without reporting hides the
  defect; reporting without resolution is a perfectly good place to stop.

---

## 0. The v1 row contract — CLOSED

`StandardRow` is frozen for v1:

| Source | Fields |
|---|---|
| dataclass | `band`, `claim`, `code`, `grade`, `sheet`, `tags`, `text` |
| B.2 | `stem_id`, `stem_name`, `stem_ids`, `ladder_status` |
| `@property` | `color`, `flagged`, `reasons`, `tagged_in_sheet` |

Adding a field after the frontend exists costs a serializer change, an API
contract change, and a component change. Reopening this is a deliberate act.

**No open decisions.** `note_tagging` was the last candidate and was ruled out —
see §3.1.

Trigger to reopen: a writer names something they need as a column that cannot be
derived from the above. Cost of reopening is roughly an afternoon; it is not a
rewrite, and it should not be treated as one.

---

## 1. Data — deferred values

### 1.1 The 144 attribution conflicts
**What:** `concept_standards` and the category route name different stems for
144 standards.
**Ruled:** the row shows the precedence winner (`concept_standards` beats
category — standard-level beats category-level). The loser is written to
`data/reports/stem_attribution_conflicts.txt`, never silently dropped.
**Why deferred:** in v1 the row shows one stem with the full `stem_ids` list on
expand, so a conflict is invisible to the writer either way. The flip diagnostic
was cut for this reason.
**Trigger:** a writer reports a stem assignment that looks wrong. Check the
report first.

### 1.2 `PS_STATISTICAL_VARIABILITY` has no workbook section
**What:** the only `6_9` `stem_map` row with no matching section in
`H2 Stem and Leaf Spreadsheet G6 to Alg1.xlsm`. It will stay unattributed.
**Pinned:** `count(*) FROM stem_map WHERE band='6_9' AND workbook_stem_id IS
NULL` = 1. Any other value means something else broke.
**Trigger:** the workbook gains the section, or the stem is retired.

### 1.3 The 21 blank-grade nodes
**What:** nodes with no grade field render `unresolved`, which is honest but
useless. 9 in `MH2_6A1_NumberSystem_IrrationalRealNumbers`, 5 in `Integers and
Rationals`, 3 in `MH2_PK5_Measurement and Data_Time`, rest in `One-Variable
Equations`.
**Highest-leverage single fix:** `EE_ONE_VARIABLE_EQUATIONS_DEG_1-0011` absorbs
10 tag pairs on its own — one cell clears 10 of the 17 6–9 unresolved pairs.
**Cost:** one message to one writer.
**Trigger:** the 6–9 band goes in front of writers.

### 1.4 All 25 unresolved tag pairs share one cause
**What:** a `node_grade_ruling` row with `resolution='no_grade_field'` and
`is_leaf=0` but zero `node_grade` rows, so containment has no range to test.
Zero of the 324 tagged nodes are absent from `node_grade_ruling`, so this is
fully characterized.
**Trigger:** same as 1.3 — it is the same fix seen from the schema side.

### 1.5 `ADD-0001`-style grade rulings
**What:** `TX.1.3C` is Yellow because `ADD-0001` canonicalizes to `PK, K` while
its raw cell reads `G1+ – Application`. Some of the 52 Yellows will resolve by
amending the grade normalization worksheet.
**Trigger:** Yellow becomes visible in the UI and a writer disputes one.

### 1.6 `node_standards` 1,995 vs `node_standards_parsed` 1,954
**What:** PK is `(node_id, standard_code)` with `INSERT OR IGNORE`, so a code
appearing in two cells of one node keeps the first annotation.
**Open question:** did any dropped row carry `partial` or `exceeds`? Given only
21 non-aligned tags exist project-wide, probably not — but unverified.
**Trigger:** Yellow / scope work in v2. Cheap to check then.

### 1.7 Cross-stem relocations (25 rows)
**What:** from `reconcile.py`, which compares `concept_standards` rather than
`standard_tag_status` — not even the same denominator.
**Trigger:** post-demo, and only if the comparison is rebuilt on the same
denominator first.

### 1.8 Tagged to a standard we don't owe
**What:** 5 codes (`NE.K.N.2.a/.b/.c/.f`, `VA.3.CE.2`) whose only source is
`all_states.csv`. Writers tagged beyond the curated gap list. Not an error;
outside the denominator.
**Related:** `FL.6.DP.1.6` and the two welded gaps codes absorb silently.
**Trigger:** the Other States scope decision (2.1).

### 1.9 Core vs state-extension grades (sequencer input)
**What:** `node_grade` folds state extensions into `default_grades` (`4 (3 for
some states)` -> grades 3, 4), so it cannot say which grade is the CCSS
placement. Added, purely additively: `node_grade_ruling.ruling_type`,
`needs_writer_review`, `states_mentioned` (copied from the worksheet, blank ->
NULL), and a new table `node_grade_kind (node_id, grade, kind, basis)` with
kind `core | span | state_extension | unconfirmed`. `single` -> core, `span`
-> span, `state_conditional` / `range_prose` / `alternative` / `unparsed` ->
`unconfirmed` until John rules; leaf / out_of_band / blank get no rows.
**The audit does not read `node_grade_kind`** (or the new columns). Section 6
stands: the audit reads a range, the sequencer a placement.
**Review sheet:** `docs/review/grade_split_review.csv` (30 rows, regenerate with
`python -m mh2.grade_split_review`) proposes the split per raw value, parsed
from `notes`. Only the 16 `state_conditional` rows are `parsed`; the rest are
`guess`. Nothing in it is loaded. To apply a ruling, add a
`ccss_default_grades` column to the worksheet; the loader then marks those
grades core and the rest of `default_grades` state_extension.
**Schema:** existing databases need a rebuild (new columns are in
`CREATE TABLE`, not `ALTER`).
**Trigger:** the Grade Sequencing Tool reads placement.

---

## 2. Scope decisions left open

### 2.1 Other States panel scope
**What:** restricted to the hand-curated gaps sheet vs. the full
`all_states.csv` (86,375 rows). Note `all_states.csv` currently has **no
consumer in this tool** — gaps rows take their text from the workbook under
first-writer-wins.
**Trigger:** re-run the overlap script against the parser-fixed file, then
decide. Not before.

### 2.2 Writer identity per stem
**What:** not in the DB. Stem is the proxy for authorship in v1, on the
assumption that stems are roughly one writer each.
**Trigger:** Step 4 feedback shows stems are shared. Then it is a real ask, and
the data comes from John.

### 2.3 Default grouping key
**What:** stem, by assumption. Cheap to be wrong about — stem is a stored field
and grouping is a runtime operation over the cached list. Being wrong changes
the default view, not the payload shape.
**Trigger:** Step 4, question 1. This is the single largest unvalidated
assumption in the design.

---

## 3. v2 — features, not debt

### 3.1 Yellow / scope checking
Writers are not authoring scope distinctions in the **structured** field:
aligned 1,933 / exceeds 12 / partial 9. v2 gets a row-level "check scope"
button — one API call comparing the state standard's text against the CCSS
standard it maps to, returning a prose delta, cached, presentation-only.

**`note_tagging` — ruled out of v1, kept here as the seed for this work.**

Writers *were* recording partial coverage, just in free text rather than the
dropdown. Sample: *"only ordered pairs and plotting, will update later for
generating numerical patterns"* · *"so far just inequalities, will update when I
look through equations"* · *"does not yet include drawing polygons"*. Sixty
hand-written cases of exactly the signal the structured field was thought not to
carry. Better starting material than an empty dropdown.

**Corrected sizing.** The handoff's figure of 613 is the raw row count and is
misleading for any UI purpose:

| | rows |
|---|---|
| non-blank `note_tagging` | 613 |
| less `California-All` (excluded from the audit by ruling) | 80 |
| less CCSS headings (denominator is leaves only) | **60** |
| of those 60, in the **PK–5** band — the demo band | **~6** |

The PK–5 six: `5.OA.B.3`, `TX.4.5C`, `TX.4.5D`, `TX.4.8C`, `TX.5.4H`,
`CA.2.NBT.2`. The remaining 54 are grade 6+, mostly high school.

**Why deferred:** six visible rows in the demo band does not justify a field in
the frozen contract. Separately, the tagging workbook has drifted out of sync
with the ladders — which is the premise of the whole tool — so notes written
against it may describe coverage that has since changed. Their value is as
*examples of how writers describe partial coverage*, not as current fact.

**Trigger:** the team asks for them, or v2 scope work begins. Cost is about an
afternoon: add the field, serialize it, render it as a chip.

**Also noted:** `note_postladder` is non-blank in **zero** rows. Whatever it was
for, it has never been used. Do not build against it.

### 3.2 Red-row suggestions
`POST /suggest_nodes` with `{standard_code, stem_id}`. The server assembles
every node in that stem and asks for a ranked short list. Stems run 9–42 nodes,
so it fits one prompt. This inverts retrieval-over-a-corpus into
ranking-within-a-known-set, which is why the `ccss_to_state` direction asymmetry
stops mattering. Suggestions never write.

### 3.3 Attribution write path — the triage queue
**Ruling already made:** unattributed is not a measurement failure, it is a
worklist. Writer reviews the list → chooses the correct stem or stems → the
standard becomes attributed. **Multi-select is required**, not optional;
`stem_ids` already being a list is correct.
**Constraint when it lands:** the writer's choice must be distinguishable from
workbook attribution by a `source` field — same rule as
`node_standards.status`, which must never be read without `source`.
**Sizing:** ~455 PK–5 rows, ~29% of the denominator. Needs sort/filter by tab,
band, and grade to be tractable.

### 3.4 Read-write generally, proposals file, export, auth, hosting
Hosting is not blocking. Local screen-share for the first feedback round. For
5–10 users: Cloudflare Tunnel + Cloudflare Access. **Do not hand-roll auth.**

**Update, 2026-09-11 (see `docs/HOSTING.md`, `docs/handoff_rev12.md`):** ahead
of a 2-week gap with nobody around to babysit a tunnel, this went to a
managed host instead (Fly.io, persistent volume for `mh2_seq.db`) with HTTP
Basic + per-writer credentials in `MH2_AUTH_USERS` — rev 9 R1's originally
intended shape, not a new invention, and no custom session/token/password
code of its own (`secrets.compare_digest`, stdlib). Written and tested
locally (`tests/test_review_api_auth.py`, 6 new tests); **not yet deployed
or verified live** — this Cowork session had no outbound network access to
run `fly deploy` itself. `docs/HOSTING.md` is the exact runbook to finish
it. Cloudflare Tunnel/Access was reconsidered and set aside only because it
needs a machine of John's to stay powered on for two weeks; the "don't
hand-roll auth" ruling still holds and HTTP Basic is not a violation of it
per R1's own text.

**SUPERSEDED IN DIRECTION, 2026-09-29 — see §8.** IT is provisioning a
Python web app in Azure. Both hosting shapes described above (Cloudflare
Tunnel + Access; Fly.io + persistent volume) are set aside as the target,
and the HTTP Basic / `MH2_AUTH_USERS` credential plan (rev 11 §2.3) is
superseded **pending IT's answer** on Azure App Service built-in auth
(Easy Auth / Entra ID). Nothing is deleted: the Fly.io path stays written
and tested (`tests/test_review_api_auth.py`, `docs/HOSTING.md`) as the
fallback if Azure stalls, and "do not hand-roll auth" is the ruling that
survives all three shapes — it is why Easy Auth is the ask rather than
anything of our own. Do not delete `docs/HOSTING.md` until Azure is live.

---

## 4. Known decay risks — watch, don't fix

These get worse over time on their own. None is actionable now; all should be
re-read before any large re-ingestion.

- **Node IDs are content hashes.** A reworded node surfaces as deleted + new
  rather than carrying approved alignments forward. *Currently harmless because
  v1 is read-only and nothing is pinned to a node ID. This protection
  disappears the moment 3.3 lands.*
- **`stems.stem_id` is a function of the stem name string** (PK–5 derivation
  path only). Renaming a stem in the workbook changes its ID. The 6–9 path
  resolves rather than derives, so it is immune.
- **`stem_map.ladder_drafted` is stale** in 2 of 22 PK–5 rows, in both
  directions (`OE_ADDSUB` reads 0 while `ADD` has 27 nodes; `NSS_ORD` reads 1
  while `ORD` has zero). **Never read it.** Derive drafted status from node
  counts.
- **New ladders will surface new parser edge cases** — cf. the
  `_should_skip_right_cell()` regex anchoring bug, which discarded rows carrying
  genuine lesson references alongside supplemental-material notes. Found by
  running the pipeline, not by inspecting it.
- **`concept_standards` is workbook attribution, and the workbook is the
  artifact under audit.** 410 standards are tagged in a ladder but attached to a
  different stem in the workbook. For bucketing into "content area with a
  ladder" vs. "without," the imprecision barely matters — but say so before a
  writer finds it.

---

## 5. Session C — review write path (FastAPI + `mh2_seq.db`)

**DISCREPANCY, reported per this file's own standing principle (§6 below):** the
brief this session implemented from (its FLAG 1) assumed `coverage.py` still
spells the rollup `colour` and that a `colour` -> `color` rename was pending
as its own future commit. Reading `mh2/coverage.py` directly shows the
property is already named `color`, and `scripts/render_static.py`'s
`ROW_DICT_KEYS` and JS both already say `color` throughout. There is no
`colour` spelling anywhere in this codebase to rename. `mh2/schema_seq.sql`
uses `color` because that is simply the established spelling, not because a
rename was avoided — the "defer the rename" item folded into that brief's
FLAG 1 does not exist and nothing further is owed here.

Also reported: the same brief's §1.2 described `app.py`/`audit_app.py` as
Streamlit apps for an unrelated EM2/UC3 effort reading `learnosity.db`.
Neither file exists in this repo. What exists is the `app/` package
(`db.py`/`review.py`/`styles.py`/`pages/1_Gap_pool.py`) -- MH2's own existing
Standards Alignment Review Streamlit tool, reading `mh2.db` via
`config.DB`, with its own write path (`db.write_ruling()` -> `node_standards`,
full tag CRUD). The brief's actual instruction -- new code lives in
`review_api.py`, not `app.py`, and does not touch `app/` -- holds regardless
of the misattributed rationale, and this session followed it. Full tag CRUD
against `node_standards` is already out of scope here (R6) and is exactly
what that existing Streamlit tool already does; the two are not in conflict
and nothing here should be read as a reason to touch `app/`.

Carried forward, genuinely open:

- **Structural ladder edits that break the docx parser.** R4 excludes this;
  writers must edit inside content cells only.
- **Hosting, HTTPS, auth.** Hosting a single server-owned `mh2_seq.db`
  dissolves the distributed-copy merge problem rather than solving it --
  resolve that concern by reference once hosting lands, not before.
- **React scaffolding over the FastAPI layer.** `review_api.py` today serves
  the existing static render (`scripts/render_static.py`'s output) plus a
  JSON API with no consuming UI wired to the write endpoints yet -- see §6.1
  in the Session C brief: this session stood up the API, not the frontend
  that calls its write endpoints from a button.
- **Out-of-scope grades (GEO, A2, ambiguous HS).** Carried from rev 8, still
  unruled, unaffected by anything in this session.
- **Retiring `node_standards`' three unused review columns** (`status`,
  `reviewed_by`, `reviewed_at`) as its own commit. Labeled not-for-use in
  `mh2/schema.sql`'s comments already; not populated or dropped this session.
- **Standard-level review staleness is reported, not ignored (FLAG 3, as
  written).** `standard_review.computed_color_at_review` exists and
  `mh2/reconcile_review.py` reports a mismatch as "review may be stale,"
  naming both colors, and never modifies the row. This is the ruling in
  effect; revisit only if a future session wants staleness ignored instead.

---

## 6. Rulings that are closed — do not relitigate

Listed so that revisiting one is a visible decision rather than a drift.

- **Ladder status is a filter and a summary dimension. Never a colour, never a
  fourth rollup state.** Undrafted content still reads Red.
- **The ladders are the tagging source of truth, not the spreadsheet.**
- **The tool must not become a third source of truth.** Two sources produced
  this entire finding; a third would kill the premise.
  **Exception** (2026-09-30, John; applied 2026-10-01): `mh2_seq.db` is
  authoritative for grade sequences, modules, slots, placements and
  per-placement attributes only — facts with no Word home. It never holds
  tags, node content, or grade rulings, and nothing reads it back into
  `mh2.db`. See `docs/seq_orientation_rev1.md` §2.
- **One definition.** `coverage.py` owns the denominator and the rollup. No SQL
  in routes. Filter the returned list in Python.
- **`grade_match` is containment, not equality.** The ladder grade field is a
  plausibility bound, not a placement claim.
- **Colour and flags are independent axes.** Off-grade tags never downgrade a
  well-covered standard. PK–5: 101 flagged against 52 Yellow, so 49 are
  Green-with-flag. That is the design working.
- **`California-All` (533 rows) drops out by construction** — not in
  `coverage.TABS`, never looked up. A ruling, not a bug.
- **Do not tighten the `len(segs) > 3` branch in `_alias_nocluster_key`.** It
  would drop 4 real tags.
- **`node_standards.standard_id` is never rewritten.** The code the writer typed
  is the evidence.
- **The two tools read `node_grade` differently and that is correct.** The audit
  reads a range; the grade sequencing tool reads a placement. Do not unify.
- **Acceptance criteria carry the literal predicate that produced the number, or
  carry no number at all.**
- **If you cannot reproduce a stated figure, report the discrepancy rather than
  searching for a predicate that hits it.**

---

## 7. Session D — audit pass wired to the write endpoints

`scripts/render_static.py`'s single template now gates a full write UI
(mark reviewed, override color, tag review, propose a tag) on
`location.protocol !== 'file:'` (D1). Opening the file cold still renders all
2,987 rows read-only, zero fetch calls before the gate is defined. Served
over `uvicorn review_api:app`, the page fetches `GET /api/audit` once and
`GET /api/standards/{id}` on row expand, and re-fetches only that one
standard's detail after each write (D2/D6) — no SQL added anywhere, no new
runtime dependency, `app/` untouched (verified `md5sum` of every file under
`app/` before and after).

**DISCREPANCY, reported rather than silently patched:** the brief this
session implemented from required both (a) D1's single-file design, where
the write UI's `fetch()` calls live in the same template gated at runtime,
and (b) `tests/test_render_static.py` passing **unmodified**. But that
file's `test_rendered_html_has_no_network_dependencies` asserted the literal
substring `"fetch("` never appears in the emitted HTML at all — a static-text
check no runtime-gated single-file design can satisfy, since the calls exist
in the source regardless of which protocol later executes them. Flagged to
John directly; he chose to update that one test to check the actual
behavior instead (no fetch/XHR reachable before the `ONLINE` gate is
defined, no eager `<script src>`/`<link href>`, no hardcoded external URL)
rather than leave it failing or dodge the literal string. This is the one
edit made to an otherwise-frozen test file this session; every other
`ROW_DICT_KEYS`/`row_dict()` assertion in that file is untouched and still
passes as written.

**Measured, not a problem (brief flagged this as a stop-and-report gate):**
`GET /api/audit` against the real 160MB `mh2.db` took ~114ms;
`GET /api/standards/{id}` took ~15–25ms. Both rebuild coverage from scratch
per request (no cache), but at this data size neither made row-expand feel
broken, so no cache was built. **Trigger to revisit:** `mh2.db` grows enough
that these figures degrade noticeably, or `/api/audit` starts being called
more than once per page load.

Also verified live against the real database: an override left
`coverage.build_rows`'s row count and Green/Yellow/Red distribution
unchanged (2,987 / 682 / 102 unaffected); one standard review round-tripped
through `scripts/rebuild.py` + `reconcile_review.py` and came out the other
side still at `standard_review` count 1; a proposal against a real
`source_key` in `MH2_PK5_NumberSystemsandStructures_ComparingandOrdering_
LessonLadder.docx` landed in `GET /api/worklist` under that one file, not
split across two entries. All QA writes made during this verification
(review, override, proposal) were cleared/withdrawn through the same DELETE
endpoints afterward — but `tag_proposal` is not keyed on the pair, precisely
so a withdrawal and a later re-proposal remain two events, so the row itself
persists: `mh2_seq.db` holds one `withdrawn` `K.CC.A.1` / `COM:df4004fa423035ff`
row (writer `qa-session-d`, reason "qa verification cleanup"). That row is
correct and intended, not leftover mess — the schema's history-preserving
design means `tag_proposal` will never legitimately return to empty.

Carried forward, genuinely open:

- **Writer identity is provisional (D4).** A single header name field, held
  in JS state and mirrored to `localStorage`, trusted entirely — not
  authentication, not per-stem identity ([[2.2]]), and not resolved by this
  session. Superseded by HTTP Basic with per-writer credentials once hosting
  lands (R1).
- **`GET /api/worklist` has no consuming UI.** Verified correct
  (acceptance criterion 8) by calling it directly; no writer can see it. The
  edit pass that builds that surface is a separate, later session.
- **FLAG A — closed.** `mh2_seq.db`'s home directory was git-ignored and
  marked disposable while the file itself held real writer judgment; `rm -rf
  data/build` would have silently destroyed it with no error and no backup.
  Ruling: un-ignore the file in place. `mh2_seq.db` stays at
  `data/build/mh2_seq.db` and is now tracked; everything else in that
  directory (`mh2.db`, the `mh2.db.bak-*` files) stays ignored.
  **Gotcha (do not "simplify" this later):** git cannot re-include a file
  whose parent directory is itself excluded, so `data/build/` +
  `!data/build/mh2_seq.db` silently leaves the file ignored. The working
  form excludes the directory's *contents* instead:
  `data/build/*` + `!data/build/mh2_seq.db`. README's "`data/build` is
  disposable" rule now names this exception.
  **Tradeoff accepted:** `mh2_seq.db` is a small SQLite binary; tracking it
  means opaque binary diffs per writer session and unresolvable merge
  conflicts for concurrent writers. Accepted because the alternative was
  losing the file outright, and because hosting (R1) — a single
  server-owned `mh2_seq.db` — dissolves the concurrent-copy problem rather
  than solving it, same reasoning as the `DEFERRED.md` distributed-copy item
  above. If binary churn becomes painful before hosting lands, that is a new
  DEFERRED entry, not a reopening of this one.

---

## 8. Azure hosting — rulings (2026-09-29)

Scope of this section: hosting the **standards gap audit tool only** (the
FastAPI review layer, `review_api.py` + `scripts/render_static.py`). The
grade/module sequencing tool follows later on the same pattern. The
Streamlit `app/` tool is **not** in scope and is not being hosted.

### R-H1. Ladders are pulled read-only from SharePoint
Ladders are fetched at rebuild time via Microsoft Graph. Never uploaded to
the app, never written back. The Word ladder remains the sole source of
truth for tagging (§6). Python side is `msal` + HTTP requests to Graph — no
new language, no new runtime shape.

### R-H2. Config inputs move to SharePoint
The tagging workbook, `stems.csv`, `mh2_category_to_stems.csv`, and the
grade normalization worksheet move off John's machine to SharePoint. App
reads them read-only.

**Which side each one lands on (clarified 2026-09-30).** Three of these are
pipeline inputs — `rebuild.py` opens them and they are in its pre-flight.
`mh2_category_to_stems.csv` is **not**: it is read by the serving layer
(`mh2/coverage.py`'s `build_stem_attribution`, and `app/db.py`), and by no
step the rebuild runs. The fetch step must land it for the app, not gate a
rebuild on it — a rebuild that fails over a file the pipeline never reads
would be coupling the two for no reason, on a host where they need not be
deployed together. No existence check was added for it anywhere: both
readers `open()` it directly and the resulting `FileNotFoundError` already
names the full path, in the tool that actually wanted it.

**Recommended, not yet confirmed:** put these in a folder editable only by
the pipeline owner, separate from the ladder folder. A stray edit to any of
them silently changes attribution across many stems, which is a different
blast radius from editing one ladder.

### R-H3. Neither database is a source of truth
Writers keep editing Word docs. `mh2.db` is destroyed every rebuild;
`mh2_seq.db` holds judgments beside the data and never holds tags. Making
either authoritative would create the forbidden third source of truth
(§6).
**Amended by the §6 exception (2026-10-01):** `mh2_seq.db` is
authoritative for sequence/placement data only. `mh2.db` remains
non-authoritative.

### R-H4. Azure runtime constraints — state these to IT explicitly
Exactly one instance, one worker, databases on storage that survives
restarts and redeploys. **No automatic failover** — accepted as a tradeoff
for an internal tool. This likely conflicts with IT's defaults, so it is
stated rather than assumed.

Why single-instance, in the terms that actually apply: user count is not
the constraint. SQLite serializes writes and this tool's writes are single
small rows; Python `sqlite3` defaults to a 5s busy timeout. The failure mode
is multiplicity, not load. Multiple instances break it one of two ways —
separate local DB files per instance (silent divergence of writer
judgments), or one file on a network share (SQLite locking over SMB/Azure
Files is unreliable, so corruption rather than divergence). `mh2_seq.db` is
the only record of writer judgments, which is what makes both intolerable.

**Trigger for an architecture change:** IT mandates scale-out, or only
network storage is durable. The fallback is moving authored data to a
managed DB (Azure SQL / PostgreSQL). Do not raise this unless forced — it
is a real architecture change, not a config change.

### R-H5. Team-triggered rebuilds require safeguards first
The rebuild button is not exposed until these exist:

1. **Build beside, then swap.** Build into a new file; swap in only on
   success. **Not currently how it works** — see the verification below.
2. **Failed build changes nothing.** New ladders (especially 6–9) will
   surface parser edge cases (§4); a parse failure must leave current data
   live and name the failing file.
3. **One rebuild at a time (lock).** Does not currently exist — see below.
4. **Visible log:** who triggered, when, which files changed, outcome.
5. **Pre-flight file check:** every `ladder_file` in `stems.csv` present
   before ingestion starts.
6. **Change preview** from SharePoint `lastModifiedDateTime` /
   last-modified-by, showing which ladders changed since the last rebuild.

SharePoint version history covers undo for bad ladder edits. Nothing to
build there.

### Verified against the live repo, 2026-09-29
The handoff this section came from carried six claims marked VERIFY. All
were checked against code, not against other docs:

- **CONFIRMED — rebuild is destructive in place.** `scripts/rebuild.py`
  unlinks `config.DB` and re-executes the schema; there is no
  build-beside-then-swap. `mh2_seq.db` is explicitly never unlinked. R-H5.1
  is therefore unimplemented work, not a description of current behavior.
- **CONFIRMED — no rebuild lock.** No flock, lockfile, or pidfile anywhere
  in `scripts/rebuild.py`. Two concurrent runs race on the same
  unlink-and-recreate. Harmless while one person runs it from a laptop;
  this is the safeguard that most directly gates R-H5.
- **CONFIRMED — missing ladder files fail loudly.** `resolve_ladder_files`
  in `mh2/load_stems.py` raises `SystemExit` on a missing or ambiguous file
  for any `stems.csv` row with `ladder_drafted=1`, and it runs before
  ingestion. A missing file does *not* silently turn a stem Red. R-H5.5
  exists for the `ladder_drafted=1` case; the gap is that `rebuild.py`'s own
  pre-flight covers only the three workbook files.
- **CONTRADICTED — there is no coverage cache and no `POST /reload`.** Every
  route opens a fresh read-only connection per request (`_mh2_con()` /
  `_seq_con()`). This matches §7's own measurement ("Both rebuild coverage
  from scratch per request (no cache)"); the handoff's claim was wrong.
  **Consequence for the IT conversation: drop it.** Multi-worker cache
  staleness is not a reason for single-instance — R-H4's two real reasons
  stand on their own, and leading with a stale one weakens them.
- **CONFIRMED — review writes are upsert, last-writer-wins.**
  `standard_review` (PK `standard_id`), `standard_color_override` (PK
  `standard_id`), and `tag_review` (PK `(standard_id, source_key)`) all use
  `INSERT ... ON CONFLICT ... DO UPDATE` in `mh2/review_store.py`. Only
  `tag_proposal` preserves history, by design (§7). **Answer for the team
  lead on two writers on the same standard:** the second silently replaces
  the first everywhere except proposals. Whether that needs ownership by
  grade/sheet ([[2.2]]) is a question for the team lead, not a code fix.
- **CONFIRMED — staleness anchors are named as documented.**
  `computed_color_at_review` and `computed_color_at_set`, both in
  `mh2/schema_seq.sql`.
- **CONFIRMED — the `write_ruling()` risk is real.** `app/db.py`'s
  `get_connection()` connects to `config.DB`, and `write_ruling()` upserts
  into `node_standards` in `mh2.db` — destroyed on every rebuild. Out of
  hosting scope, but it gets more dangerous as rebuilds get more frequent
  and more people can trigger them. Carried, not fixed.
- **CORRECTION — `eval/paths.py` is not about file paths.** It is
  reach-gating logic for scoring generator paths, unrelated to file I/O.
  All input locations are plain `Path` objects in `config.py` (`LADDERS`,
  `LADDER_GLOB`, `STEM_WORKBOOK`, `STEM_WORKBOOK_G6`, `TAGGING_WORKBOOK`,
  `STEMS_CSV`, `CATEGORY_TO_STEMS_CSV`, `GRADE_WORKSHEET`), consumed
  directly by `open()` / `python-docx` / `pandas`. **There is no fetch
  abstraction to swap.** R-H1 and R-H2 therefore need a fetch step that
  lands files into the paths `config.py` already names, rather than a
  rewrite of the loaders — which is the cheaper shape and should stay that
  way.

### Figures not carried forward
Two numbers travelled in the handoff prose without their predicates: "82
computed-Green-and-flagged rows" as the writers' best starting set, and
"~455 unattributed PK–5 rows" as the worklist size. Neither is reproduced
here. §6 records 49 Green-with-flag for PK–5 against a different predicate,
so the 82 is at minimum scoped differently. Per §6's own ruling, these are
to be re-measured with literal predicates before being quoted to the team
lead, and the discrepancy reported rather than reverse-engineered into a
predicate that hits the number.

### Open with IT
Answers to these change the deployment, not the design: scale-out disabled
and worker count settable to one; where DB files physically live (local disk
vs. Azure Files) and whether they survive redeploys and restarts;
backup/snapshot schedule; whether the boilerplate accepts a FastAPI /
`uvicorn` entrypoint rather than assuming Flask or Django; Easy Auth /
Entra ID instead of HTTP Basic; an Entra ID app registration with Graph
`Sites.Selected` scoped to one SharePoint site, read-only, and whether that
same registration can cover user login; and whether a rebuild triggered
from inside the app is acceptable or batch jobs must run separately. The
sequencing tool will later use the same pattern — worth saying once, now.

### Phased rollout (PK–5 before 6–9) — compatible with the design
Undrafted reads Red by ruling and ladder status is a filter, never a colour
(§6). Drafted status is derived from node counts (§4), so it flips
automatically when a ladder with nodes appears. Rebuild risk is per stem:
new 6–9 ladders do not renumber PK–5 nodes, and review records key on
`(standard_id, source_key)` rather than `node_id`, which is what makes the
content-hash decay risk in §4 survivable here.

**Still unverified, and do not promise it to the team lead until it is:**
that review records survive a real rebuild that adds 6–9 ladders. §7
round-tripped one `standard_review` through `rebuild.py` +
`reconcile_review.py` on PK–5 data only. The 6–9 case is the one being
promised and it has not been run.

Talking points that follow from the above: headline Red counts include
undrafted 6–9 standards, so filter to PK–5 or to drafted to measure real
progress (if the team always applies the same filter, that is pilot
feedback for a default view); don't review undrafted rows; undrafted
resolves as ladders arrive, unattributed is a worklist someone must decide,
and they are not the same number; the first 6–9 rebuilds are the likeliest
to fail parsing; each new ladder file name must be registered in
`stems.csv`, which belongs in the 6–9 team's handoff, not in ours; and
rebuild when a writer says their edits are done, because SharePoint
autosave means a mid-edit rebuild ingests half-finished work.

### Writer workflow, as confirmed — and the one gap
Review in the app; **record judgments, not edits** (mark reviewed, override
colour as a separate layer with the computed colour untouched, propose a
tag with an outcome and rationale — none of this changes coverage); edit
the Word doc in SharePoint, which is the only step that changes coverage;
rebuild; then clear overrides and judgments the rebuild made redundant,
which is detectable because the override stores the computed colour at the
time it was set (`computed_color_at_set`, verified above).

Message for the team lead: **the app is where you decide what needs fixing;
the Word doc is where you fix it.** A standing override is legitimate only
when a writer is confident coverage exists that tagging does not capture.

**Gap: step 3 has no UI support.** `GET /api/worklist` exists, grouped by
ladder file, with no consuming screen ([[7]]). Until that session lands,
writers keep their own edit lists by hand. Recommend building it before
wider rollout — the workflow's only coverage-changing step is the one step
the tool does not support.

### Landed 2026-09-29 — hardening, and what it deferred

R-H5.1/5.2 (build beside, swap on success), R-H5.3 (flock), R-H5.4
(`data/reports/rebuild_log.tsv`), R-H5.5 (pre-flight over every consumed
input), R-H4's `MH2_DATA_DIR`, and §6's `write_ruling()` reroute are all
in. Verified end to end against a full copy of real `data/` under
`MH2_DATA_DIR`, not only against fixtures: a clean run swaps and passes
`PRAGMA integrity_check`; a run failed mid-pipeline (corrupted grade
worksheet) left `mh2.db` byte-identical and logged
`failed:loading grade ruling into node_grade`.

Follow-up, 2026-09-30: `candidate_ruling` gained `node_id_seen`,
`node_text_seen` and `ladder_file_seen` (written on both branches, so a
rejection carries an anchor even though it raises no proposal — done as a
schema line while the table still held 0 rows, rather than as a migration
against live writer judgment later); `CATEGORY_TO_STEMS_CSV` came out of
the rebuild pre-flight, since it is read by `mh2/coverage.py` and
`app/db.py` and by no step the rebuild runs; and the read-path rerouting
gained a test that observes a non-zero tally across a rebuild *and* a real
`reconcile_review` run, replacing a check that had compared 0 against 0.

Carried forward, each with a trigger:

- **`candidate_ruling` has no staleness reconciliation.**
  `reconcile_review.py` reports staleness for `tag_review`,
  `standard_review` and `standard_color_override` against rebuilt ladders;
  it knows nothing about `candidate_ruling`.

  *Wording corrected 2026-09-30.* This previously said such a ruling is
  "stranded silently rather than reported", implying detection was the
  gap. It is not. `source_key` is `stem_id` + a hash of normalized node
  text (`mh2/ingest_ladders.py`, `source_key()`), so rewording a node
  mints a new key and retires the old one; staleness is therefore fully
  detectable by membership against
  `node_lookup.build_source_key_lookup`, exactly as
  `reconcile_tag_reviews` already does it. Stale rulings are **detectable
  but not yet detected**, and the report would need to be actionable when
  they are — which is why the `*_seen` anchor columns landed first
  (2026-09-30): `tag_review`'s stale-row report prints `ladder_file_seen`
  and `node_text_seen` so a writer can find the node in a Word document,
  and until those columns existed a stale `candidate_ruling` row could
  have been reported only as a `standard_id` and an opaque `source_key`.

  **Trigger:** the first rebuild that changes PK–5 node text after writers
  have ruled on candidates — or sooner, if the 6–9 ladders land first,
  since they arrive with the most node churn.
- **A failed rebuild leaves `mh2.db.building` on disk** (~160 MB on
  current data). Deliberate — it is the evidence of what failed, and the
  next run unlinks it before starting. **Trigger:** disk pressure on the
  host, or more than one stale build file ever existing at once.
- **The rebuild lock is POSIX-only** (`fcntl.flock`). Covers a Mac laptop
  and a Linux App Service, which is every host currently contemplated.
  **Trigger:** IT provisions a Windows host; then `msvcrt.locking`, and
  not before.
- **No end-to-end test of a *successful* full rebuild.** The success path
  was verified by hand (above) but not pinned by a test: a real rebuild
  needs the true source workbooks, which are not in the repo.
  `tests/test_rebuild_hardening.py` pins the swap, the lock, pre-flight
  and the log; the twelve-step pipeline itself is unpinned.
  **Trigger:** the fetch step lands (R-H1/R-H2) and inputs become
  machine-obtainable, at which point this becomes cheap.

---

## 9. Ladder edits export (2026-09-30)

Brief: `docs/brief_ladder_edits_export.md`. The **Download latest ladder
edits** link in the writer bar serves `GET /api/export/ladder-edits.xlsx`:
every open tag proposal (Add) and every `incorrect_tag` review whose node
still carries the standard (Remove), in ladder order, for the one writer
who keeps the Word ladders current. The lead enters every suggestion, and
entering it counts as sign-off; there is no approval state. This is the
first surface to consume the §7 worklist data. `GET /api/worklist` itself
still has no screen.

**Removals are now checked.** `mh2/ladder_edits.removal_state()` classifies
each `incorrect_tag` as removed / still tagged / node reworded against the
current `mh2.db`. `reconcile_review.py` reports all three. The state is
derived, never stored: §5.2 still holds and no `tag_review` row is modified.
Accepted consequence: a tag typed back into the ladder after removal
resurfaces as a Remove row, because the judgment still stands.

**Verified against real data, 2026-09-30:** against the real `mh2.db` and
a scratch copy of `mh2_seq.db` with four added rows, the export took ~94ms.
A Remove for `4.NBT.B.4` showed the ladder's own spelling, `4.NBT.4`,
through the alias path. The copy also held three real open proposals of
John's from 2026-09-30. Two of them (#2/#3, `K.CC.A.1` on
`COU:64aadc1fe4cda757`) are the same proposal twice. They predate the
route's `open_proposal_exists` guard, and the export folds them into one
row. The rows themselves are untouched; withdraw one if it was a test.

Carried forward:

- **No "applied, waiting for rebuild" mark.** Between an edit and the next
  rebuild, the keeper re-sees edits already made. For now the download's
  header states the date the ladders were last read into the tool
  (`MAX(ingest_log.ts)` in `mh2.db`), and the keeper's Done column covers
  one download. The rebuild cadence is expected to be about weekly but is
  not settled, and the keeper, not John, owns it.
  **Trigger:** rebuilds turn out to run less than weekly, or the keeper
  reports re-doing or losing track of edits across downloads. Then add a
  keeper-set `applied` state, cleared by reconcile, that the export shows
  and does not count.
