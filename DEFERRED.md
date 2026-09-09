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
