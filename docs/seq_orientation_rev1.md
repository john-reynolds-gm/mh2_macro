# Grade Sequencing Tool — orientation rev 1 (2026-09-30)

> **Reconciled with branches 2026-09-30.** Checked against `seq/grade_type`,
> `seq/analysis` and `seq/prototype` (trial merge of all three). Kind vocabulary
> is now the branch's (`node_grade_kind.kind`: core / span / state_extension /
> **unconfirmed**); §4.2 and §4.4 carry as-built figures; §4.3, §4.5, §4.6,
> §6.1, §6.8 and §9 take in the analysis and prototype findings; O7 is
> rewritten and O8–O13 added in §12.

**This is the single design reference for the grade sequencing tool.** It
supersedes `docs/seq_orientation_rev0.md` and `docs/seq_design_brief_rev0.md`.
Both stay in the repo as history and should not be edited. Where they disagree
with this file, this file wins. Companion documents:

- `docs/seq_data_model_rev1.md`: DDL, the placement reconcile contract, and route shapes.
- `docs/seq_build_plan_rev1.md`: ordered Sonnet sessions to v1.

Written unattended from the repo. Every figure was re-measured on 2026-09-30
against `data/build/mh2.db`, whose last ingest is `2026-09-09 15:11:11`
(`SELECT MAX(ts) FROM ingest_log`). Figures therefore describe that build.
Each figure carries its predicate, per DEFERRED §6. Where the numbers I was
given could not be reproduced, §10 reports the discrepancy. I did not search
for a predicate that would hit them.

---

## 0. Status in one paragraph

Design is complete enough to start building. Two things landed since the brief
and remove the brief's blockers: grade normalization (`node_grade`,
`node_grade_ruling`) and a hosted FastAPI shape. One thing is in flight and the
tool depends on it. That is the per-(node, grade) **kind**, which branch
`seq/grade_type` adds as a new table `node_grade_kind (node_id, grade, kind,
basis)` with `kind` ∈ core / span / state_extension / unconfirmed. The branch
exists locally and merges cleanly with the other two, but it is not on `main`
and `data/build/mh2.db` has not been rebuilt with it. Until it has been, sessions
degrade to `kind='unknown'` instead of blocking. Even after the merge,
**state_extension has no rows** until John fills `ccss_default_grades` (§4.2).

---

## 1. Rulings in force

| # | Ruling | Source | Status |
|---|---|---|---|
| R-S1 | **mh2_seq.db owns placements.** It is the authoritative record of grade sequences, modules, slots, placements, and their per-placement attributes. This is a narrow, explicit exception to DEFERRED §6 ("must not become a third source of truth") and R-H3 ("neither database is a source of truth"). Its scope is defined in §2. | John, 2026-09-30 | **Ruled** |
| R-S2 | **Calibration level is placement-scoped** (Deep / Functional / Illuminating), not node-scoped. | Brief §6; Math Pacing Prioritization v2.0; John, 2026-09-30 | **Ruled** |
| R-S3 | The two tools read `node_grade` differently. The audit reads a range and the sequencer reads a placement. **Do not unify.** The sequencer never imports `mh2/coverage.py`, and the audit never reads placement tables. | DEFERRED §6 | Closed |
| R-S4 | Reuse, do not fork. There is one ingest (`mh2/ingest_ladders.py`) and one `nodes` table, and no second parser against the Word docs. | Brief §2 | Valid |
| R-S5 | Placements key on `nodes.source_key` and carry `*_seen` snapshots. They never key on `node_id`, which is positional (`f"{stem_id}-{seq+1:04d}"` in `ingest_ladders.persist`). | Reconciliation, 2026-09-30; R3 | Ruled |
| R-S6 | **Surface, don't block.** Shared CCSS codes are pairings, not conflicts. Cross-grade reuse is expected. Ordering inversions warn and never prevent a save. | Brief §4.3, §6, §9 | Valid |
| R-S7 | Ladder changes **never** auto-change a placement. Reconcile reports and derives status. The builder re-attaches, acknowledges, or removes. | Brief §4.5, extended in data model §6 | Ruled here |
| R-S8 | **v1 is manual.** It has no LLM features, and drag-and-drop is deferred (chevrons). | Brief §1, §10 | Valid |
| R-S9 | Hosting follows the audit: same FastAPI app, one instance, one worker, durable storage for `mh2_seq.db`, ladders read-only from SharePoint. | DEFERRED §8 R-H1..R-H4 | Valid |
| R-S10 | **Keep the frontend dumb.** Parsing, checks, ordering rules, guardrail arithmetic, and all SQL stay in Python under `mh2/`. Routes contain no SQL. The page only renders and posts. | Brief §3; DEFERRED §6 "One definition" | Valid |
| R-S11 | PK–5 before 6–A1. | DEFERRED §8 phased rollout | Valid |

---

## 2. The source-of-truth exception (R-S1), stated precisely

### 2.1 What it covers

mh2_seq.db is authoritative for **these facts and no others**:

1. That a grade sequence exists for a grade, and its title.
2. The modules in that sequence, with their titles and order.
3. The teaching slots in each module, their order, and which placements share a slot (co-placement).
4. That a given node (by `source_key`) is placed in a given slot. Placement is a **teaching decision**: "in G2 we teach this in M3, slot 4."
5. Per-placement attributes: calibration level, period estimate, and the differentiation/scope note.
6. Whether an off-grade placement (a bridge) was made deliberately, and the builder's acknowledgement of a later grade-ruling change.
7. Saved views. These are per-writer UI state and not curriculum, but they live in the same file.

### 2.2 What it does not cover, and never will under this ruling

| Fact | Authority | What the sequencer does with it |
|---|---|---|
| A node exists; its wording | Word ladder, via ingest → `nodes` | Reads it. `node_text_seen` is a locator snapshot, never read as current content. |
| Concept/skill heading, stem membership, document order | Word ladder → `nodes.concept_skill`, `stem_id`, `seq` | Reads. `concept_skill_seen` is for change detection only. |
| Node attributes (goal, misconceptions, models, …) | Word ladder → `nodes.goal`, `node_fields` | Reads in the drawer. Never edits. |
| Standards tags | Word ladder → `node_standards_parsed` | Reads for shared-code badges. Never proposes tags. The audit tool owns proposals. |
| Grades as written, and their ruling (`node_grade`, `node_grade_ruling`, kind) | Word ladder + grade worksheet | Reads to decide slice membership and badges. **A placement never implies or overrides a grade ruling.** |
| Leaf status | Grade worksheet `is_leaf` | Reads. Leaves are excluded from the inventory by default. |
| Period-estimate prose in `additional_notes` | Word ladder (stem writer's hint) | Shown next to the placement's own estimate as a hint. Neither corrects the other (see 2.3). |

### 2.3 Why this does not create a competing truth

A competing truth means **the same fact** has two authorities that can
disagree. That is what produced the audit's original finding (DEFERRED §6).
The test applied here is that every fact the tool shows has exactly one
authority.

- **No fact appears on both sides.** Word says what a node *is* and which
  grades it is *appropriate for* (a plausibility range). A placement says
  where the grade builder *teaches* it. These are different facts. DEFERRED §6
  already rules that the audit reads the range and the sequencer reads a
  placement, and that the two are not unified. An off-grade placement does not
  contradict the ladder. It is a bridge, it is badged, and it writes nothing
  back.
- **Nothing flows from mh2_seq.db into mh2.db or Word.** No rebuild step reads
  placement tables. No placement changes `node_grade`, coverage colour, or a tag.
  The audit is not allowed to read placements (R-S3).
- **Snapshots are evidence, not truth.** When `node_text_seen` disagrees with
  `nodes.node_text`, `nodes` wins and the placement is orphaned. The snapshot
  never wins. This is the same contract as `tag_review` and `candidate_ruling`.
- **The one soft overlap is period estimates.** Ladders carry prose such as
  "G2: Likely 2 instructional periods". A placement carries a number. They are
  kept distinct as *stem writer's estimate* and *grade builder's plan for this
  placement*. The UI shows them side by side and never reconciles or flags
  divergence, because a builder co-teaching two nodes is expected to plan less
  time than the sum of the hints.
- **Why an exception is needed at all:** a grade sequence has no Word home.
  Without one, either the sequence has no authority (and is lost on every
  disagreement), or the sequence gets a document home. Appendix A drafts that
  alternative and explains why it was rejected.

### 2.4 Amendment text for DEFERRED (proposed, not applied)

This design session is not allowed to edit DEFERRED.md. The following lines
are proposed for John to paste:

> **§6, after "The tool must not become a third source of truth":** Exception
> (2026-09-30, John): `mh2_seq.db` is authoritative for grade sequences,
> modules, slots, placements and per-placement attributes only — facts with no
> Word home. It never holds tags, node content, or grade rulings, and nothing
> reads it back into `mh2.db`. See `docs/seq_orientation_rev1.md` §2.
>
> **§8 R-H3, append:** Amended by the §6 exception: `mh2_seq.db` is
> authoritative for sequence/placement data only. `mh2.db` remains
> non-authoritative.

### 2.5 Consequences accepted with the ruling

- **mh2_seq.db becomes the only copy of real work.** Losing it loses grade
  sequences, and unlike review judgments they cannot be re-derived by
  re-reviewing. R-H4's durable storage and IT's backup schedule stop being
  nice-to-haves. The data model §12 proposes an online backup and a text dump.
- **Attribution must be real.** Placements are contested authorship.
  `placed_by` must come from the authenticated user, not from a client string
  (today's `reviewed_by` gap, rev 12 §4). Build plan sessions S3/S4 close this
  for the sequencer's routes.
- **Trigger to reopen:** the ladders gain a field that encodes placement,
  calibration, or module (for example a writer adds "Calibration: Deep" to a
  ladder), or sign-off must happen in a document. Either one puts a second
  authority on a covered fact.

---

## 3. Purpose (still valid, from brief §1)

Grade builders assemble grades out of the stem ladders. They pull nodes from
many stems into one grade, order them into modules, and sometimes co-teach
nodes from different stems in one slot. Ladders are vertical (one stem, all
grades). Building a grade is horizontal (one grade, all stems). Today the
builder does that transposition by hand across several Word files. **The
tool's core job is the transpose.**

---

## 4. Data reality: reconciled against the repo

### 4.1 Identity and storage

| Brief assumed | Actual | Consequence |
|---|---|---|
| `node_id` is a content hash | `node_id` is positional/opaque (`PS_PROBABILITY-0001`). `nodes.source_key = stem_id + ":" + sha1(normalized node_text)[:16]` (`ingest_ladders.source_key()`), `UNIQUE NOT NULL`. | Key on `source_key`. `node_id` can renumber on a rebuild even with no text change, so keying on it would orphan placements even more often than the brief feared. |
| New tables in the same SQLite DB with FKs to `node_id` | `mh2.db` is destroyed and rebuilt every run (build-beside-then-swap since 2026-09-29). Authored state lives in `mh2_seq.db`, which is tracked in git and has no cross-DB FKs. | Placement tables go in `mh2_seq.db` with `*_seen` snapshots and a reconcile sibling. |
| Rewording orphans placements | True. A move to another stem also changes `source_key` (the stem prefix). A move to another concept/skill in the same stem does not. | Data model §6. |
| Standards tool stays in Streamlit | The gap audit is FastAPI (`review_api.py`) plus a static HTML page (`scripts/render_static.py`) behind HTTP Basic. `app/` (Streamlit) is not hosted and is do-not-touch. | The sequencer joins the FastAPI app as a router. |
| Concept/skill is an entity | It is a **string** (`nodes.concept_skill`, the heading text). There is no concept/skill table. | Group by `(stem_id, concept_skill)` at read time. Never key authored data on it. |

### 4.2 Grade kinds: the one dependency still in flight

On `main`, `load_grade_ruling.py` reads four worksheet columns only. It folds
state extensions into `default_grades` and does not load `ruling_type`
(docstring, lines 14–20). So `node_grade` over-states in-grade membership for
state-conditional nodes. The sequencer needs a kind per (node, grade). The
consumption contract is in data model §5.

**As built on `seq/grade_type`:** `node_grade_ruling` gains `ruling_type`,
`needs_writer_review` and `states_mentioned` (copied from the worksheet; blank →
NULL). A new table `node_grade_kind (node_id, grade, kind, basis)` holds one row
per `node_grade` row whose `ruling_type` has a deterministic answer, and **no
row otherwise**. `node_grade` itself is unchanged, so extension grades stay in
it. The audit reads none of this (DEFERRED §1.9 on the branch). The only way a
grade becomes `core` + `state_extension` for an open type is a new worksheet
column `ccss_default_grades`, which John fills from
`docs/review/grade_split_review.csv` (30 raw values: 16 state_conditional
`parsed`, 12 range_prose + 1 alternative + 1 unparsed `guess`).

| Worksheet `ruling_type` (joined to nodes) | nodes | `node_grade` rows | `node_grade_kind.kind` as built | Sequencer read state |
|---|---|---|---|---|
| single | 155 | 155 | core | core |
| span | 88 | 213 | span | span |
| state_conditional | 24 | 56 | **unconfirmed** until `ccss_default_grades` is filled; then core for those grades, state_extension for the rest | unconfirmed (→ core / state_extension after the ruling) |
| range_prose | 26 | 78 | unconfirmed (same upgrade path) | unconfirmed |
| out_of_band | 6 | 9 | **no row** | unknown (O7) |
| alternative | 3 | 6 | unconfirmed | unconfirmed |
| unparsed | 3 | 3 | unconfirmed | unconfirmed |
| (blank) | 1 ruled + 1 leaf | 4 | **no row** | unknown for the ruled node (2 rows, raw `7, 8`); leaf for the other |
| leaf | 25 | 11 | no row | leaf (excluded by default) |
| — (`no_grade_field`) | 21 | 0 | no row | no_grade ("no grade" context chip) |

*Predicate:* Worksheet sheet `Worksheet`, joined on
`canonical_grade_key(raw_value) = node_grade_ruling.canon_key` (the loader's
own join), counted per node and per `node_grade` row. There were 0 unmatched
canon keys. Row totals reconcile: ruled 306 and leaf 26
(`SELECT resolution, COUNT(*) FROM node_grade_ruling GROUP BY 1` → ruled 306,
leaf 26, no_grade_field 21). The as-built column was measured on a copy of the
2026-09-09 `mh2.db` with the branch DDL applied and
`python3 mh2/load_grade_ruling.py --db <copy> --worksheet <worksheet>` run from
the trial merge: `SELECT kind, COUNT(*) FROM node_grade_kind GROUP BY 1` →
core 155, span 213, unconfirmed 143 (= 56 + 78 + 6 + 3); state_extension 0.
That is 511 of 535 `node_grade` rows. The 24 without a kind row are leaf 13,
out_of_band 9 and blank-`ruling_type` 2 (`LEFT JOIN node_grade_kind … WHERE
k.node_id IS NULL`, grouped by `resolution, ruling_type`).

### 4.3 Corpus figures

| Figure | Value | Predicate |
|---|---|---|
| Nodes / stems with nodes | 353 / 17 | `SELECT COUNT(*), COUNT(DISTINCT stem_id) FROM nodes` |
| Stems by band | 10 PK–5, 7 6–A1 | `stems.band`/`stem_map.band`; PK–5 = source_file `MH2_PK5_*` (10), 6–A1 = `MH2_6A1_*`/`MH2_6-A1_*` (6) + `MH2_GA1_*` (1). See §10 D3. |
| Stem catalog | 42 | `SELECT COUNT(*) FROM stems` |
| Concept/skills | 116 | `SELECT COUNT(DISTINCT stem_id||'|'||concept_skill) FROM nodes` (the same count without `stem_id`, so no label is shared across stems) |
| Empty concept/skill labels | 0 | `... WHERE TRIM(concept_skill)=''`. The brief's empty-title bug is fixed. |
| Comment anchors in labels or text | 0 | Python `re.search(r"\[\^", ...)` over `node_text` and `concept_skill` |
| Author DONE markers in concept/skill labels | **32 nodes, 19 labels (COU, EST)** | `SELECT COUNT(*), COUNT(DISTINCT stem_id||'|'||concept_skill) FROM nodes WHERE concept_skill LIKE '%(%done)%'`. §10 D1. |
| Nodes per concept/skill | 1–9, mean 3.04. 35 C/S have exactly 1 node. | `SELECT COUNT(*) c FROM nodes GROUP BY stem_id, concept_skill` |
| Grade resolution | ruled 306, leaf 26, no_grade_field 21 | `SELECT resolution, COUNT(*) FROM node_grade_ruling GROUP BY 1` |
| `node_grade` rows | 535 | `SELECT COUNT(*) FROM node_grade` |
| Ruled nodes carrying >1 grade | 143 | `SELECT COUNT(*) FROM (SELECT g.node_id FROM node_grade g JOIN node_grade_ruling r ON r.node_id=g.node_id WHERE r.resolution='ruled' GROUP BY 1 HAVING COUNT(*)>1)`. §10 D4. |
| CCSS codes shared by >1 stem | 30 | `SELECT COUNT(*) FROM (SELECT standard_code FROM node_standards_parsed p JOIN nodes n ON n.node_id=p.node_id WHERE p.state IS NULL GROUP BY standard_code HAVING COUNT(DISTINCT n.stem_id)>1)` |
| … as cross-stem node pairs | 180 | distinct `(a.node_id, b.node_id)`, `a.node_id<b.node_id`, sharing a `standard_code` with both `state IS NULL`, `stem_id` differs. Equals `seq/analysis`'s figure. One code fans out (Equations ↔ Inequalities alone give 32 pairs). |
| EM2-lesson-shared cross-stem node pairs | 30 (26 share a grade) | `seq/analysis`, `scripts/report_cross_stem_links.py`: shared `product_refs.lesson_id`, TX BB / Bluebonnet / Maryland / Catalyst refs excluded, stems differ. Not re-derived here. |
| `node_links` | 88 rows, all `stated`, 0 resolved, from 35 nodes, 43 distinct texts | `SELECT link_type, COUNT(*), SUM(related_node_id IS NOT NULL), COUNT(DISTINCT node_id), COUNT(DISTINCT related_text) FROM node_links GROUP BY 1` |
| … of which exactly name a stem | 33 of 88 | Python: whitespace-collapsed, casefolded `related_text` ∈ `stems.name` |
| … of which are period prose leaking in | 7 of 88 | `related_text` contains "period" (the ingest skip matches only items that *start* with "instructional period") |
| … of which are real mentions / noise | **56 / 32** | `seq/analysis` `noise_reason()`: a node's run of real mentions ends at its first noise row (time estimates, headings, lesson refs, quotations). The 7 period rows are part of the 32. |
| Mentions `ASSOC_RE` misses | 19 | `seq/analysis` `extract_mentions(additional_notes)` minus `node_links`: headers `Related concepts:`, `Related to:`, `Associated concepts:`, and headerless "overlap with …" prose. Capture fix is O11. |
| `product_refs` | 779 rows, 465 with `lesson_id`, 311 nodes | `SELECT COUNT(*), SUM(lesson_id IS NOT NULL), COUNT(DISTINCT node_id) FROM product_refs` |
| Leaves | 1,493 rows | `SELECT COUNT(*) FROM leaves` |
| EM2 lessons | 1,378 | `len(pd.read_csv(config.LESSON_METADATA_CSV))` |

**Per-grade inventory**, including leaf nodes that carry grade rows:

| Grade | PK | K | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | A1 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Nodes | 24 | 45 | 37 | 35 | 37 | 61 | 54 | 81 | 77 | 37 | 47 |
| Concept/skills | 15 | 29 | 24 | 24 | 21 | 25 | 20 | 23 | 25 | 17 | 17 |

*Predicate:* `SELECT g.grade, COUNT(DISTINCT n.stem_id||'|'||n.concept_skill), COUNT(DISTINCT n.node_id) FROM nodes n JOIN node_grade g ON g.node_id=n.node_id GROUP BY g.grade`.

### 4.4 The G2 slice, measured

| Figure | Value | Predicate |
|---|---|---|
| In-grade nodes / C/S / stems | 35 / 24 / 7 | `... JOIN node_grade g ... WHERE g.grade='2'`, counting `DISTINCT node_id`, `stem_id||'|'||concept_skill`, `stem_id` |
| … excluding leaves | 33 nodes / 22 C/S | add `JOIN node_grade_ruling r USING(node_id) WHERE r.resolution='ruled'` |
| Leaf nodes with a G2 row | 2 (`COU-0024`, `MUL-0004`) | `resolution='leaf'` within the G2 set |
| Context (dimmed) nodes in those 24 C/S | 22 (57 chips total) | nodes in `(stem_id, concept_skill)` ∈ G2 C/S set `AND node_id NOT IN (SELECT node_id FROM node_grade WHERE grade='2')` |
| G2 nodes that are multi-grade | 20 | `SELECT COUNT(*) FROM (SELECT node_id FROM node_grade WHERE node_id IN (SELECT node_id FROM node_grade WHERE grade='2') GROUP BY node_id HAVING COUNT(*)>1)` |
| G2 nodes by worksheet type | single 13, span 14, state_conditional 4, range_prose 2, leaf 2 | worksheet join as in §4.2, restricted to `grade='2'` |
| G2 `node_grade` rows by kind, as built | core 13, span 14, unconfirmed 6, no row 2 (the two leaves) | `SELECT COALESCE(k.kind,'none'), COUNT(*) FROM node_grade g LEFT JOIN node_grade_kind k ON k.node_id=g.node_id AND k.grade=g.grade WHERE g.grade='2' GROUP BY 1`, on the reloaded copy described in §4.2 |
| … state_conditional where G2 is the **extension** | 2 (`TIM-0010` "3 (2 for some states)", `TIM-0011` "3 (2, 4 for some states)") | `grade_split_review.csv` `proposed_state_extension_grades` contains 2 (confidence `parsed`), agreeing with my reading of `raw_value`. **Proposed, not loaded:** today both are `unconfirmed` in G2. `WHO-0004` / `WHO-0005` are also state_conditional in G2, but the proposal makes G2 core for them (the extension is G1). |
| G2 nodes citing an EM2 G2 lesson | 17 of 35 (26 cite any lesson) | `... JOIN product_refs p ... WHERE g.grade='2' AND p.lesson_id LIKE 'G2-%'` |
| Shared-stem CCSS codes touching a G2 node | 17 of the 30 | codes in the 30-set with `node_id IN (SELECT node_id FROM node_grade WHERE grade='2')` |

The G2 **owed inventory** (core + span + unconfirmed + unknown) is **33** nodes
with the branch as built: 35 − 2 leaves, 13 + 14 + 6. Leaves are hidden by
default. If John rules the review CSV's G2 proposals as they stand, `TIM-0010`
and `TIM-0011` become state_extension, show dimmed and badged, and the owed
inventory drops to **31**.

### 4.5 Node attribute fill (the drawer's collapse-empty rule)

Per node, out of 353. *Predicate:* `SELECT field, COUNT(DISTINCT node_id) FROM node_fields GROUP BY field`. Goal uses `nodes.goal IS NOT NULL AND TRIM(goal)<>''`.

| Field | Nodes | Field | Nodes |
|---|---|---|---|
| goal (C/S-scoped) | 335 | intervention_notes | 231 |
| additional_notes | 331 | misconceptions | 228 |
| standards_notes | 329 | mathematical_models | 223 |
| product_reference | 311 | strategies | 190 |
| required_skills_cases | 310 | leaves_to_include | 98 |
| considerations | 268 | terminology | 92 |
| specifications | 255 | **student_facing_example** | **not stored** (§10 D5) |

Goal is consistent within a concept/skill. `SELECT COUNT(*) FROM (SELECT stem_id, concept_skill FROM nodes WHERE goal IS NOT NULL AND TRIM(goal)<>'' GROUP BY 1,2 HAVING COUNT(DISTINCT goal)>1)` returns 0. In 6 C/S some nodes carry the goal and others are blank
(`... HAVING COUNT(DISTINCT COALESCE(goal,''))>1` = 6). The drawer shows the
C/S's single non-empty goal once, in the C/S-scoped band.

**Goal gaps** (from `seq/prototype` design question 5, re-measured): 18 nodes
have a blank goal (`goal IS NULL OR TRIM(goal)=''`). 8 of them sit in those 6
partly-filled C/S. The other 10 make up **3 C/S with no goal at all**
(`GROUP BY stem_id, concept_skill HAVING SUM(goal IS NOT NULL AND
TRIM(goal)<>'')=0` → 3: two in `EE_ONE_VARIABLE_EQUATIONS_DEG_1`, one in
`EE_GENERAL_EXPRESSIONS`). All three appear only in 6–A1 slices. Ruling O13.

**Other fields that look C/S-scoped** (prototype question 4). The prototype's
`compute_cs_shared_fields()` finds a field whose non-empty value list is
identical on every node of a 2+-node C/S. Counted once per C/S over the 81
multi-node C/S, that is mathematical_models 19, leaves_to_include 12, strategies
11, standards_notes 10, additional_notes 7, product_reference 6, terminology 6,
and ≤4 for the rest. The prototype README's "39 / 31 / 21 / 21 / 14" are the
same test summed over the 11 grade slices, so a C/S is counted once per slice
it appears in. They are not distinct C/S. These look like merged ladder cells
rather than a scoping rule. Ruling O12.

### 4.6 Period estimates (answers brief §8 Q4)

| Figure | Value | Predicate |
|---|---|---|
| Nodes mentioning "instructional period" | 196 | `SELECT COUNT(DISTINCT node_id) FROM node_fields WHERE field='additional_notes' AND LOWER(value) LIKE '%instructional period%'` |
| … PK–5 / 6–A1 | 170 of 173 / 26 of 180 | same, split by source-file band. The only 6–A1 stem covered is `EE_GENERAL_EXPRESSIONS` (26/29). |
| Distinct phrasings | 117 | `COUNT(DISTINCT value)` over the matching `node_fields` rows. §10 D2. |
| Matching item rows with a number just before the phrase | 175 of 265 | regex `(\d+(\.\d+)?)\s*(-|–|to)?\s*(\d+(\.\d+)?)?\s*instructional period` |
| Nodes with grade-prefixed estimates ("G2: Likely …") | 42 | regex `\bG(K|\d)\s*[:\-–]` over per-node concatenated notes |

The rows above match the phrase "instructional period" only. `seq/analysis`
(`docs/seq_analysis_rev1.md`, parser `parse_period_notes()` in
`scripts/report_period_estimates.py`) also reads header-plus-value rows and
**other units**. The 6–9 ladders count in "days" (Equations, Integers and
Rationals, Inequalities, Probability, Irrational) or "lessons" (Coordinate
System). Its figures, with in-grade = has a `node_grade` row and `is_leaf=0`
(306):

| Figure | Value | Predicate (`seq/analysis`) |
|---|---|---|
| In-grade nodes with a usable estimate, any unit / periods only | 245 / 159 of 306 | `usable(e)`: exact, range, or part_of with an upper bound; `e.unit=='period'` |
| **Strict** per-grade coverage, periods (PK K 1 2 3 4 5 6 7 8 A1) | 29 48 61 70 56 61 60 11 0 0 2 % | the estimate names that grade, or is un-graded on a node in exactly one grade |
| Strict, any unit, 6 / 7 / 8 / A1 | 38 / 20 / 17 / 15 % | the same, counting days and lessons as if they were periods |
| 6–9 stems covered in days/lessons | Equations 90%, Integers 91%, Probability 100%, Coordinate System 100%; Inequalities 27%, Irrational 38% | per-stem, any unit |

**Answer (revised to the analysis verdict):** as a live total, no. With a
visible "N of M counted" caveat the time axis is usable for **G1–G5**, where
strict period coverage is 56–70%. It is **not usable for PK or K** (29%, 48%),
because most of their nodes are multi-grade and carry one un-graded figure. It
is **not usable for any 6–A1 grade** in periods. The 6–A1 gap is mostly a
**units** question and only partly missing data. Nothing in the repo says what
a day or a lesson is worth in periods (O8). The estimate is presented as a
**hint the builder confirms**, never a parsed value written silently. The
readout always shows time coverage ("periods known for n of m placements")
next to the time-by-level split. Data model §5.4 gives the hint rule. It reuses
`parse_period_notes()` rather than a second parser (R-S4).

---

## 5. Design, merged

### 5.1 Three-tier attribute scope (still valid; the brief's most important rule)

| Scope | Holds | Where it lives now |
|---|---|---|
| Concept/skill | Goal (shared across the progression; never shown per node) | Derived at read time: the single distinct non-empty `nodes.goal` in `(stem_id, concept_skill)` |
| Node | Text, standards, misconceptions, models, strategies, product refs, stated links | `mh2.db` (`nodes`, `node_fields`, `node_standards_parsed`, `product_refs`, `node_links`), read-only |
| Placement | Module, slot, order, calibration, period estimate, differentiation/scope note, bridge acknowledgement | `mh2_seq.db` `placement` / `slot` / `module` |

### 5.2 Placement and co-placement (valid; mechanism refined)

- A placement joins one `source_key` to one **slot** in one module of one
  grade sequence. It is never keyed on `node_id`.
- **Grouping, not merging.** A slot holds one or more placements. A solo node
  is a slot of one. Co-placing moves a placement into an existing slot, and
  un-grouping splits it back out. Nodes stay independent, the change is
  reversible, and no blend text is written (true composites stay deferred).
  Order lives on slots within a module, with a secondary order within a slot.
  This replaces the brief's unspecified "placement_group".
- Each placement has its own calibration and period estimate. A co-taught
  slot's time is the sum of its placements' estimates. So a builder
  co-teaching two 1-period hints in one period enters 0.5 each. **Assumption
  (confirm):** estimates stay per placement (as the brief lists) rather than
  per slot. This keeps time-by-level exact with no apportioning rule.

### 5.3 Ownership (valid, with one correction)

- Stem writers own nodes, in Word. Grade builders own placements, in the tool.
  The tables are disjoint by role.
- **Superseded:** "append-only merges keep working." Hosting replaced
  distributed copies with one server-owned `mh2_seq.db` (R-H4). Merging is no
  longer the concern. Concurrent edits on one server are, and the answer is
  optimistic concurrency (`rev` on each row, 409 on mismatch). See data model
  §11.
- Reuse across grades is expected. There are 143 ruled multi-grade nodes (§4.3).
  Placing a node that already has a placement in another grade's sequence
  shows where it is placed and prompts for a differentiation note (surface,
  don't block).
- Within one sequence, a node can have at most one active placement.
  **Assumption (confirm):** a same-year revisit is a later feature. It is
  enforced by a partial unique index that is easy to drop.

### 5.4 Bridges and off-grade (valid; now expressed through kind)

A bridge is a placement whose node has no `node_grade` row for the sequence's
grade (`kind='off_grade'`), or whose kind there is `state_extension`. The
builder confirms it explicitly when placing (the API requires
`confirm_off_grade=true`). It is badged in the rail. The browser filters to
in-grade by default and widens in one click. The dimmed context chips in each
strip are the one-click bridge path.

### 5.5 Orphans (valid; mechanism corrected)

A reworded node mints a new `source_key` and orphans every placement on the
old one, possibly in several grades. So does a move to another stem, or a
deletion. Orphans are derived at read time, not stored. They appear in a
**Needs attention** queue grouped by sequence and module, with
`node_text_seen` / `ladder_file_seen` and suggested successors. The builder
re-attaches or removes. Nothing is done automatically. Full contract: data model §6.

### 5.6 Calibration and the guardrail readout (valid; R-S2 ruled)

Math Pacing Prioritization v2.0 (June 2026) gives Deep / Functional /
Illuminating (Know it / Use it / See it), with year-level guardrails of about
**25/50/25 by target count** and **40/45/15 by instructional time**. It says
these are diagnostics, not quotas, and that time matters more than counts. The
module builder shows a live readout per module and per sequence:

- counts by level, plus "unset"
- periods by level, plus "unknown time"
- time coverage (`n of m placements have an estimate`)
- the guardrail targets drawn as reference marks, never as pass/fail
- the **count** panel ships first, because it needs no time data. The time
  panel draws its 40/45/15 marks only when the time coverage clears a threshold
  (data model §7; `seq/analysis` §3). On the ladder hints, PK, K and all of
  6–A1 would not clear it (§4.6).

The document was not in the repo (`find . -iname "*pacing*"` found nothing).
The figures are taken from the brief.

---

## 6. Interface (brief §5, still valid unless noted)

### 6.1 Grade slice (primary view)

- A fixed inventory of everything a grade owes, grouped super-stem → stem →
  concept/skill → nodes. Super-stem is `stems.domain`, populated for all 17
  node-bearing stems. **Assumption (confirm):** use `stems.domain` over
  `stem_map.stem_group`. They disagree for `RP_COORDINATE_SYSTEM` ("Geometry
  and Measurement" vs "Ratios and Proportional Relationships"), and
  `stem_map` does not join PK–5 node stems at all (their `stem_id`s are
  `ADD`/`COU`/… and `stem_map`'s are `OE_ADDSUB`/`NSS_COUNTING`/…).
- Only concept/skills that contain an in-grade node are shown (G2: 24, or 22
  excluding leaves).
- **Slice size varies by about 4×.** Chips per slice (in-grade + context, leaves
  on): PK 27, K 57, 1 59, 2 57, 3 70, 4 82, 5 79, 6 106, 7 107, 8 67, A1 80.
  The longest strip is 4 chips (PK), 6 (K–2), 8 (3–5) and 9 (6–A1). *Predicate:*
  per grade, `COUNT(DISTINCT node_id) FROM node_grade WHERE grade=?` plus the
  §4.4 context predicate; strip length is `MAX(COUNT(*))` over the included
  `(stem_id, concept_skill)` groups. These reproduce `seq/prototype` design
  questions 1–2. Whether G6/G7 open with stems collapsed, and whether a 9-chip
  strip wraps or scrolls, is decided at Gate B from the prototype walkthrough
  (build plan §1).
- Each concept/skill is a **progression strip** in ladder order
  (`nodes.seq`). In-grade nodes are solid. The C/S's other nodes are dimmed as
  context, with their grade labels. `no_grade_field` nodes show as "no grade"
  chips so they do not silently vanish.
- Chip badges: kind (span · state ext · unconfirmed · kind pending), placed-here (module
  label), placed-elsewhere (grade list), leaf, and consistency badges (§6.8).
- Labels have the trailing author status marker removed **at display only**.
  The raw `concept_skill` is untouched (§10 D1).

### 6.2 View controls and saved views

Per-stem and per-concept/skill show/hide. Stem reorder, using chevrons in v1
(the brief said drag; drag is deferred per R-S8). Toggles: context grades,
unplaced only, pairings (shared codes; O10), state extensions, leaves. The whole view is one
serializable state object. The same object is written to the URL query string
and to `saved_view.state_json`. **Saved, named views per writer** stay in v1.

### 6.3 Node drawer

Non-modal and enters from the right, with the slice still live behind it. There
is a visually separate **concept/skill band** (Goal, C/S label, stem) above the
**node band**. The C/S band holds Goal only, and other fields stay in the node
band even when identical across the C/S (O12). Empty rows collapse by default
(fill rates in §4.5). The drawer
also shows the grade ruling (raw value, kind for this grade), placements of
this node in every sequence, stated cross-stem links (text chips; exact stem
names link to that stem's filter), and product refs.

### 6.4 Comparison table (bottom sheet)

A synthetic ladder table: nodes as columns, attributes as rows, across stems.
It opens as a bottom sheet growing from a compare tray and stays live with the
slice. It can be open together with the drawer. Cap four columns before
horizontal scroll. Shared-standard rows self-highlight. The primary action is
**Co-place in a module**. The selection lives in the URL
(`?compare=<source_key>,…`). The brief's "trivial with React Router" became
`URLSearchParams`, which works with any stack.

### 6.5 Module builder

A sequence rail showing modules → slots → placements. Actions: create, rename
and reorder modules; place from the slice or drawer; move up/down (chevrons);
move to another module; co-place/un-group; remove (soft). Placing a node that
already has a placement in another grade opens the cross-grade prompt with its
differentiation note. Placing an off-grade node requires confirmation and adds
a bridge badge.

### 6.6 Guardrail readout

As §5.6, pinned at the top of the rail, recomputed after every write from the
server's response. The arithmetic runs in Python (`mh2/guardrail.py`) and the
page only displays it.

### 6.7 Needs attention (new relative to the brief)

This is the orphaned-placement queue plus grade-ruling-changed items. It sits
at the top of the rail, with a count, and never on a separate page (the
brief's §9 argument applies).

### 6.8 Consistency badges, tiered (brief §9; v1 = the two free tiers)

| Tier | Check | v1? | Data today |
|---|---|---|---|
| Structural | Placement off-grade; placement's kind changed; node placed before its ladder predecessor (same C/S, lower `seq`, placed later or not at all in this sequence); owed node unplaced | Yes | Free |
| Shared-code | Same CCSS code on nodes in different stems | Yes, as a **link** badge, not a warning | 30 codes, 180 node pairs; 17 codes touch G2; **22 of 35 G2 nodes** carry one (30 of 81 in G6) |
| Shared-lesson | Same EM2 `lesson_id` on nodes in different stems | Drawer / compare evidence only (reuse `seq/analysis`'s signal; see §7) | 30 node pairs |
| Declared link | `node_links` stated text | Drawer display only | 88 rows, **56 real mentions**, none resolved to nodes; stem-level |
| Textual | Near-duplicate wording across stems | Deferred | — |
| Semantic | Terminology drift | Deferred (LLM) | — |

Badges appear inline plus in a **Flagged** filter. There is no report page.
Ordering warnings warn and never block. **The shared-code badge does not count
toward Flagged** (O10). At 22 of 35 G2 nodes it would make the filter pass
almost everything, which is `seq/prototype` design question 8. Restricting it
to in-grade partners barely helps: 20 of 35 in G2 and 29 of 81 in G6 still
qualify (the same predicate, with both nodes' `node_grade` containing the
grade). Shared codes get their own **Pairings** toggle instead, grouped by stem
pair so that one code does not fan out into a badge per node pair
(`seq/analysis` §3.5).

---

## 7. v1 scope and deferred

**v1:**

1. Grade slice with progression strips, kind badges, and a leaf/state-ext toggle.
2. View controls, URL state, saved named views.
3. Node drawer (non-modal, right, scope bands, empty-row collapse).
4. Comparison bottom sheet (max 4, URL-encoded, co-place action).
5. Module builder: sequences, modules, slots, placements; co-place, un-group, chevron reorder, soft remove.
6. Calibration and period estimate per placement, with ladder hint, plus the live guardrail readout.
7. Structural and shared-code badges plus the Flagged filter.
8. Cross-grade prompt with differentiation note; off-grade confirmation and bridge badge.
9. **Needs attention** queue: re-attach, acknowledge, remove. `mh2/reconcile_placements.py` writes a report file.
10. Server-stamped identity (`placed_by`) on every sequencer write.

**Deferred** (each with its trigger):

| Item | Trigger |
|---|---|
| True node merging / authored blend text | Builders report co-placement is insufficient in pilot |
| LLM synthesis, pairing suggestions, semantic checks | v1 stable, then a scoped design pass |
| State-switching in the slice / state-variant sequences | A state program needs a different sequence, not only badges |
| Drag-and-drop reorder | Chevrons measured as a pain point in pilot |
| EM2 lesson-overlap pairing as a slice badge (`compute_lesson_overlap.py` is referenced by the brief but **not present in this repo**; `seq/analysis`'s `scripts/report_cross_stem_links.py` now computes the shared-lesson signal, 30 cross-stem pairs) | Pairing becomes the bottleneck. Promote that signal into `mh2/seq_checks.py`; do not write a third implementation. |
| EM2 product order as a comparison layer or starting seed | Builders ask for "what does EM2 do" (17 of 35 G2 nodes cite a G2 lesson) |
| Topics under modules | Ruling O2 (§12) |
| Sign-off / lock state on a sequence | Ruling O1 (§12) |
| `student_facing_example` in the drawer | Ruling O5 (§12) |
| Resolving `node_links` to nodes, or showing them as a stem-level badge | Links are needed for ordering checks. Most name stems, not nodes. The stem badge waits for the capture fix (O11). |
| One-way sequence export (xlsx/docx) | Anyone outside the tool needs to read a sequence. Cheap, derived, never re-imported. |

---

## 8. Ledger: every brief item, marked

| Brief § | Item | Status |
|---|---|---|
| 1 | Purpose, transpose, v1 manual | **Still valid** |
| 2 | Reuse, do not fork; one ingest, one node table | **Still valid** |
| 2 | "Same content-hash node IDs"; FKs to `node_id` | **Superseded** by §4.1: `node_id` is positional; key on `source_key`; no cross-DB FK |
| 2 | "Same SQLite DB" | **Superseded**: placements go in `mh2_seq.db`, because `mh2.db` is destroyed every rebuild |
| 2 | "Standards tool stays in Streamlit" | **Superseded**: the audit is FastAPI + static HTML. `app/` is not hosted and is do-not-touch. |
| 2 | Comment-anchor heading bug is hard-won | **Now answered**: 0 empty C/S labels, 0 anchors (§4.3) |
| 3 | FastAPI + SQLite | **Still valid** |
| 3 | React frontend | **Reopened, recommendation in build plan §4**: plain JS, no build step |
| 3 | Keep the frontend dumb | **Still valid**, strengthened as R-S10 |
| 4.1 | Three-tier scope; Goal is C/S-scoped | **Still valid**; Goal consistency verified (§4.5) |
| 4.2 | Placement; grouping not merging | **Still valid**; group = slot (§5.2) |
| 4.3 | Node vs placement ownership | **Still valid** |
| 4.3 | Append-only merges | **Superseded** by hosting (single DB) and optimistic concurrency |
| 4.3 | "70 of 137 node cells multi-grade" | **Superseded**: 143 of 306 ruled nodes (§4.3) |
| 4.3 | Surface, don't block, on duplication; differentiation note | **Still valid** |
| 4.4 | Bridges need no schema; filter default; badge | **Still valid**; expressed through kind `off_grade` / `state_extension` |
| 4.5 | Orphaned-placement queue | **Still valid**; mechanism corrected (`source_key`; derived; data model §6) |
| 5.1 | Grade slice, progression strips | **Still valid**; figures updated (G2: 24 C/S, 35 nodes, 22 context) |
| 5.2 | View controls, saved views | **Still valid**; drag → chevrons |
| 5.3 | Drawer | **Still valid** |
| 5.4 | Comparison bottom sheet, 4 columns, URL | **Still valid**; React Router → URLSearchParams |
| 6 | "10 ladders, 137 cells, 62 C/S" | **Superseded**: 353 nodes, 17 stems, 116 C/S |
| 6 | "56 distinct grade values; blocking problem" | **Now answered** by the worksheet: 93 distinct raw values (loader docstring) resolved into `node_grade`. Kind is the residue (§4.2). |
| 6 | "33 CCSS codes in 2+ stems" | **Superseded**: 30 |
| 6 | "38 prose cross-refs" | **Superseded**: 88 `stated` rows from 35 nodes, already parsed, of which 56 are real mentions (plus 19 the ingest misses); they mostly name stems |
| 6 | Fill rates per C/S | **Superseded** by per-node table (§4.5) |
| 6 | Label variants (Goal/Goals, …) | **Now answered**: `FIELD_MAP` in `ingest_ladders.py` |
| 6 | Empty-title Base Ten C/S | **Now answered**: 0 |
| 6 | DONE markers in node text | **Partly answered**: 0 in `node_text`, but 32 nodes / 19 labels still carry them in `concept_skill` (§10 D1) |
| 6 | Pacing framework is a live constraint | **Still valid** |
| 6 | Calibration placement-scoped | **Now ruled** (R-S2) |
| 7 | Typed grade normalization is the blocker | **Now answered**. Kind is built as `node_grade_kind` on `seq/grade_type` (not yet on `main`); core vs extension waits on `ccss_default_grades` (§4.2) |
| 7 | CCSS default + badge state exceptions; no state switching | **Still valid**: `state_extension` kind, dimmed + badged |
| 7 | node → grade many-to-many; no grade column on node | **Still valid** (`node_grade`) |
| 8 | Open questions | **Answered** in §9 |
| 9 | Tiered consistency; badges inline + Flagged filter; warn don't block | **Still valid**; figures updated (§6.8) |
| 10 | v1 scope | **Still valid**, plus the orphan queue and server-stamped identity (§7) |
| 11 | Deferred automation | **Still valid**; `compute_lesson_overlap.py` not found in repo |
| 12 | Opus designs, Sonnet implements, one view per session, checkpoint each | **Still valid**; see build plan |

---

## 9. Open questions: answered where the data answers them

**Brief §8**

1. *Are `K-5` / `1, 2, 3, 4` genuine spans?* The worksheet now types these.
   There are 12 `range_prose` raw values (26 nodes), which still need writer
   judgment. `seq/grade_type` stores them, together with state_conditional,
   alternative and unparsed, as kind `unconfirmed`. The sequencer shows them
   solid and badged "unconfirmed", and counts them as owed. The confirmation
   itself is John's `john_ruling` in `docs/review/grade_split_review.csv`,
   carried into the worksheet as `ccss_default_grades`. It is not the tool's job.
2. *Parse the prose cross-references in v1?* They are already parsed: 88 rows.
   The data shows they are stem-level ("Whole Numbers and Base Ten Structure"
   appears 27 times as exact text, and 29 times as a high/medium-confidence
   target in `seq/analysis`), not node-level. 33 of 88 name a stem exactly.
   `seq/analysis` finds only 56 real mentions. The other 32 are noise caught by
   the greedy `ASSOC_RE`, including 7 period-prose rows, and 19 mentions are
   missed. v1 shows them as drawer chips. Fixing the capture is O11. Node
   resolution is deferred.
3. *Hosting?* Answered by DEFERRED §8: Azure Python web app, same app,
   R-H1..R-H5, and the Fly path as fallback.
4. *Are period estimates consistent enough?* Not for a live total. As a
   confirmed hint with an "N of M counted" caveat, yes for G1–G5 (strict
   coverage 56–70%). No for PK and K (29%, 48%), and no for 6–A1 in periods
   (0–11%), which is mostly because 6–9 counts in days and lessons (O8). See
   §4.6 and `seq/analysis`.

**Orientation rev 0 §5**

1. *Source of truth?* Ruled: R-S1, §2.
2. *Users and ownership?* Not answered by data. Rulings O1/O3 in §12.
3. *Output shape?* Grade sequence → module → slot → placement. Lessons are
   out of scope for v1. Time is a per-placement estimate in instructional
   periods (REAL, so "part of 1 period" = 0.5 is expressible). The unit for
   6–A1 builders is O8.
4. *Multi-grade nodes: spiral or home grade?* Spiral. Placements in different
   grades are independent rows, with a differentiation note prompted.
5. *State variants?* One core sequence per grade in v1, with state extensions
   badged. Deferred per §7.
6. *Calibration in v1?* Yes, placement-scoped (R-S2).
7. *Stability?* Flag, never auto-carry or drop (R-S7; data model §6).
8. *UI stack?* Decided at Gate B in the build plan. Recommendation: plain JS.

---

## 10. Figure discrepancies (reported, not reverse-engineered)

| # | Given | Measured | Predicate / note |
|---|---|---|---|
| D1 | "DONE markers are fixed (0 hits)" | **0 in `node_text`, but 32 nodes / 19 concept/skill labels in COU and EST still end in `(Name—DONE)`** (31 by case-sensitive regex, +1 `(Stella – Done)`) | `SELECT COUNT(*), COUNT(DISTINCT stem_id||'|'||concept_skill) FROM nodes WHERE concept_skill LIKE '%(%done)%'` → 32, 19; `... WHERE node_text LIKE '%(%done)%'` → 0. The markers are in the Word files (13 paragraphs in the Counting docx, 6 in Estimating). `ingest_ladders.AUTHOR_STATUS_RE` strips a marker only when it is the *whole* label, by design. Consequence: the sequencer strips at display only. When a writer deletes a marker in Word, the node's C/S label changes, which is a "relabelled" status, not an orphan (`source_key` excludes the C/S). |
| D2 | "118 distinct phrasings" | 117 | `COUNT(DISTINCT value)` over `node_fields` rows `field='additional_notes' AND LOWER(value) LIKE '%instructional period%'`. Variants: 112 casefolded, 131 per-node concatenated, 119 sentence-extracted. None gives 118. |
| D3 | "17 stems (11 PK-5, 6 for 6-A1)" | 17 = **10** PK–5 + **7** 6–A1 | `stem_map.band='6_9'` covers 7 node stems. By file prefix it is 10 `MH2_PK5_`, 6 `MH2_6A1_`/`MH2_6-A1_`, and 1 `MH2_GA1_` (`EE_ONE_VARIABLE_INEQUALITIES_DE`). The handoff's own table lists 10 PK–5 ladders. |
| D4 | rev 0: "147 of the 306 ruled nodes carry more than one grade" | 147 is all nodes; **143** is ruled only | rev 0's predicate (`GROUP BY node_id HAVING COUNT(*)>1` on `node_grade`) does not filter `resolution='ruled'`, so it counts 4 leaf nodes. |
| D5 | Brief lists Student-Facing Example as a drawer row | **Not stored anywhere** | `ingest_ladders.SCALAR_FIELDS` includes `student_facing_example`, `persist()` skips scalars when writing `node_fields`, and `nodes` has no column for it. Ruling O5. |
| D6 | Brief: `2 (possibly just leaf for Texas)` is state-conditional | The worksheet rules it **is_leaf** (`MUL-0004`, resolution `leaf`) | It is out of the G2 core inventory. It is **not** in `grade_split_review.csv`, which covers only ruled open types. If John wants it reconsidered, that is a worksheet `is_leaf` edit, not a `john_ruling`. |
| — | All other figures given (353/17, 306/26/21, 116, G2 24/35, 30 shared codes, 88 unresolved `stated` links, 779 product_refs, 196 nodes) | Reproduced | Predicates above |

---

## 11. Do not touch (inherited, for every sequencer session)

- Everything in handoff rev 12 §5: the auth gate built with
  `FastAPI(dependencies=[Depends(require_writer)])`; auth staying off when
  `MH2_AUTH_USERS` is unset; `entrypoint.sh` rebuilding on every start.
- Handoff rev 10 §5 (the recoverable form of rev 11 §4, which is not in the
  repo): `mh2/coverage.py` and the rollup; `ROW_DICT_KEYS`/`row_dict`/`tag_dict`;
  **`mh2.db`'s schema and `rebuild.py`'s ingest order** (the grade_type branch
  is a separate, authorized additive change owned by another agent, and
  sequencer sessions do not extend it); `node_standards` review columns; `app/`;
  the `data/build/*` + `!data/build/mh2_seq.db` glob; SQL in routes; the static
  audit render's shape.
- DEFERRED §6 closed rulings, as amended by §2.4 once John applies it.
- The existing `mh2/schema_seq.sql` tables and `mh2/review_store.py` /
  `mh2/reconcile_review.py` behavior. Placement work goes in new files.

---

## 12. Open rulings for John

Only genuine decisions. Each has a recommended default that the build plan
assumes until you say otherwise.

| # | Decision | Recommended default | Blocks |
|---|---|---|---|
| O1 | **Ownership model.** Is there one owner per grade sequence, or shared editing, and is there sign-off? | Advisory `owner` on each sequence: anyone authenticated can edit, and the UI names the owner and warns non-owners. No sign-off state in v1. Every write is attributed and history is kept (`placement_event`). | S3 (schema) |
| O2 | **Modules only, or modules → topics?** | Modules only in v1. A slot `label` covers "Topic A" informally. Adding a `topic` level later is additive. | S3 (schema) |
| O3 | **Who are the grade builders, and do they need their own logins?** | The same per-writer credentials as the audit (Azure Easy Auth when IT lands it). No role separation in v1. | Pilot (after S13) |
| O4 | **Apply the §2.4 amendment text to DEFERRED §6 / R-H3.** | Apply as drafted. | Nothing technically, but it should land before S3 (the first write to authoritative data) so the exception is on record where the rule lives |
| O5 | **`student_facing_example` is parsed but never stored.** Store it? It requires an `mh2.db` schema/ingest change (do-not-touch). | Defer. The brief measured it as sparse (10/59 C/S), and the drawer does not need it for v1. | — |
| O6 | **UI stack** (build plan §4). | Plain JS: static files served by FastAPI routes, native ES modules, no build step, vendored nothing. Revisit if drag-and-drop becomes necessary. | S5 (Gate B) |
| O7 | **In-grade rows with no `node_grade_kind` row.** `seq/grade_type` settled alternative/unparsed as `unconfirmed`. It writes **no row** for `out_of_band` (6 nodes, 9 rows: raw `9-12` → A1, `8, 9, A1, A2` → 8, A1) or for the blank-`ruling_type` ruled node (raw `7, 8`, 2 rows). | The adapter reads them as `unknown`: solid, badged "kind pending", counted as owed, and exempt from `grade_changed` when a row later appears (data model §5.2, §6.2). **Optional change on `seq/grade_type`:** emit `out_of_band` rows as core (one in-band grade) or span (several), and give blank a worksheet `ruling_type`. | S1b (degrades gracefully) |
| O8 | **Time units.** 6–9 ladders estimate in days or lessons (`seq/analysis` §1.1). Is a day or a lesson a period, and at what rate? | No conversion. `placement.period_estimate` is always in instructional periods. A day/lesson hint is shown verbatim and never prefilled. The time panel for 6–A1 stays under the coverage threshold until this is ruled. | S8 (hint prefill); 6–A1 time readout |
| O9 | **Un-graded estimate on a multi-grade node**: per grade or in total? And is "part of 1 period" 0, 1, or a fraction? | Neither is decided by the tool. An un-graded estimate on a node with more than one `node_grade` grade is shown as text with no prefill. `part_of` is shown as "≤ n" with no prefill. The builder types the number. | S8 |
| O10 | **Flagged filter at high badge frequency** (`seq/prototype` Q8). The shared-code badge is on 22 of 35 G2 nodes. | Shared codes are excluded from Flagged. They get a separate **Pairings** toggle, grouped by stem pair (§6.8). Flagged = structural badges only. | S10 |
| O11 | **Stated-link capture fix.** `ASSOC_RE` in `mh2/ingest_ladders.py` over-captures (32 of 88 rows are noise) and misses 19 mentions under other headers (`seq/analysis` §2.3). The fix changes the `mh2.db` ingest, which sequencer sessions may not touch. | Authorize it as a separate pipeline session (like grade_type), outside the S-numbering: stop at the first non-concept line, and add `Related concepts:` / `Related to:` / `Associated concepts:`. Stem-only resolution. Until it lands, the drawer shows `node_links` as-is, labelled "as captured". | S6 (display only); stem-level link badge (deferred, §7) |
| O12 | **Concept/skill band beyond Goal** (`seq/prototype` Q4). Some fields are identical across every node of a C/S (mathematical_models in 19 of 81 multi-node C/S; §4.5). | Goal-only, per brief §4.1. Identical values look like merged Word cells, not an authored scope. Revisit after the Gate B walkthrough. | S6 |
| O13 | **Goal gaps** (`seq/prototype` Q5). In 6 C/S the goal is blank on some nodes, and in 3 C/S (10 nodes, all 6–A1) it is absent. | Partly-filled C/S: show the single non-empty goal for the whole C/S, with no marker (the goal is C/S-scoped by rule). No goal at all: the C/S band shows "No goal in ladder" and does **not** collapse it. Report the 3 C/S to the stem writers; the fix belongs in Word. | S6 |

---

## Appendix A: the rejected alternative: Word/export is the truth

Drafted in full so the decision is visible. Rejected on 2026-09-30 in favor of R-S1.

### A.1 Shape

- Each grade has a **Grade Sequence document** on SharePoint (xlsx is more
  parseable than docx): one row per placement, with columns for module, slot,
  order, node locator, calibration, periods, and note.
- The tool is an editor. Work in progress lives in `mh2_seq.db` as a *working
  copy*. **Publish** exports the working copy to the SharePoint file, and that
  file is the authority.
- `rebuild.py` gains an ingest step that reads every Grade Sequence file into
  `mh2.db` (`placement_ingested`), the same way ladders are read. Reconcile
  compares the working copy to the ingested copy and reports drift.
- R-H3 and DEFERRED §6 hold literally: neither database is authoritative, and
  Word/Excel on SharePoint holds all truth.

### A.2 What it buys

| Benefit | Weight |
|---|---|
| No exception to a closed ruling. The principle stays unqualified. | High on principle. In practice it is modest, because the exception is disjoint by fact (§2.3). |
| SharePoint version history, backup, and undo for free (R-H5 already relies on this for ladders) | Real. It removes the new backup obligation in §2.5. |
| The sequence is readable, printable, and shareable with no tool access | Real. It is also obtainable from a one-way export under R-S1 (deferred item §7). |
| Sign-off can use existing document review habits | Real if sign-off must be in a document (trigger in §2.5) |
| Survives the tool being retired | Real, and also obtainable from a one-way export |

### A.3 What it costs

| Cost | Why it is serious here |
|---|---|
| **It creates the competing truth it was meant to avoid.** Between an edit and a publish (and again between a publish and a rebuild), the working copy and the published file are two authorities for the same fact: "where is node X taught in G2". | This is the exact failure DEFERRED §6 forbids, created inside the tool's own edit loop. Every edit opens the window. |
| **A second parser against a hand-editable document.** Brief §2 rejected exactly this as "pure drift risk". The audit already found that structural ladder edits break the parser (DEFERRED §5, R4). | A sequence file gets edited by hand as soon as it is on SharePoint. Row reorders, merged cells, and typed-over locators all become ingest failures or silent misreads. |
| **Node anchoring in a document.** `source_key` is an opaque hash. It must be carried in a hidden or ugly column that a writer can delete or corrupt, or the file anchors on node text and re-derives the key (fragile on any reword). | Orphaning, the brief's biggest risk, gets worse. |
| **Changes the IT ask.** R-H1 says ladders are read-only from SharePoint and never written back, and the pending Graph request is `Sites.Selected` **read-only**. Publishing needs write scope. | It reopens a hosting ruling that was just made and adds a write credential to the attack surface. |
| **Guardrails and badges cannot see hand edits** until the next rebuild. The rebuild cadence is about weekly, is not settled, and is owned by the keeper (DEFERRED §9). | The live readout stops being live for anything done outside the tool. |
| **More machinery:** export, ingest, drift reconcile, conflict UI between working copy and published file | Several extra sessions before v1, for no builder-visible feature |

### A.4 When it would win

- IT cannot provide durable storage and backups for `mh2_seq.db` (R-H4 falls through).
- Sign-off must legally or organizationally happen in a document.
- People without tool access must *edit* sequences, not only read them.

If any of these become true, the cheaper step is an **R-S1 one-way export**
(derived, never re-imported). Moving the authority is the more expensive step.
Only if that is insufficient should this appendix be reconsidered as a
design.
