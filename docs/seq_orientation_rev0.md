# Grade Sequencing Tool — orientation rev 0 (2026-09-30)

Written to restart the sequencing phase. Paste as the first message of a new
design chat. Figures below were measured against the live repo on
2026-09-30 (`data/build/mh2.db` dated 2026-09-09); predicates are given.

---

## 0. Provenance — read first

**No sequencing design document exists in the repo.** No code, schema, brief,
or handoff for it. The five local Cowork sessions were all searched: they
mention sequencing only as "later, same hosting pattern." The earlier design
conversations (likely in claude.ai chats) are not accessible from here. What
survives is the project memory summary, the grade-normalization worksheet,
and the rulings scattered through `DEFERRED.md` and the handoffs. Anything
decided in those chats that isn't captured below needs to be re-stated.

## 1. What we know was decided

From project memory and `DEFERRED.md`:

- **Purpose.** "Grade builders" place ladder nodes into grade-level modules
  and sequences. It solves the transposition problem: ladders are vertical
  (one stem, many grades), grades are horizontal (one grade, many stems).
- **`node_grade` is read as a placement, not a range.** The audit reads it
  as a containment bound; sequencing reads it as "which grade slice shows
  this node." Closed ruling (DEFERRED §6): *do not unify.*
- **Hosting follows the gap-audit pattern** (DEFERRED §8): same app shape,
  one instance and one worker, ladders read-only from SharePoint.
- **React is a candidate** for the sequencing UI (drag-and-drop ordering is
  a much heavier interaction than the audit's review buttons). Not decided.
- **Phased rollout: PK–5 before 6–9.**

## 2. What the gap-audit work already gives this tool

| Built for the audit | What it means for sequencing |
|---|---|
| Ladder ingest → `nodes` | The things being placed. **353 nodes across 17 stems** (`SELECT COUNT(*), COUNT(DISTINCT stem_id) FROM nodes`). |
| Grade ruling → `node_grade`, `node_grade_ruling` | The worksheet README says *"Both tools are blocked until this is done."* It is done: **306 ruled, 26 leaf, 21 no_grade_field** (`GROUP BY resolution`). |
| `mh2_seq.db` (durable, writer-owned) | Natural home for placement tables. Right now it holds only review tables. |
| `source_key` + snapshot anchoring (R3) | Placements **must not** key on `node_id` (positional, renumbers on rebuild). Same contract as `tag_review`. |
| `reconcile_review.py` pattern | Needs a sibling: what happens to a placement when its node is reworded or deleted in Word. |
| FastAPI + auth + container/Fly config | Reusable as is. Open gap: `reviewed_by` isn't yet tied to the logged-in user. That matters more here, because placements are contested authorship. |
| Leaves (26 leaf nodes, 1,493 `leaves` rows) | Leaves are outside the core sequence by definition. We still need to decide whether state variants of a sequence are in scope. |

## 3. Measured facts that shape the design

- **147 of the 306 ruled nodes carry more than one grade**
  (`SELECT node_id FROM node_grade GROUP BY 1 HAVING COUNT(*)>1`). Every one
  of these needs a placement decision. This is where the grade builder does
  the most work.
- **Worksheet ruling types** (`Counter(ruling_type)` on the Worksheet tab):
  span 33, single 20, state_conditional 16, range_prose 12, leaf 11,
  out_of_band 2, alternative 1, unparsed 1, blank 2. Each type probably
  needs its own placement default (e.g. `alternative` means undecided).
- **Coverage is partial: 17 of 42 stems have ladders** (`stems` table).
  A full grade sequence can't be built yet. The tool has to work with
  undrafted stems as placeholders.
- **Prerequisite data is thin.** `node_links` has 88 rows, all
  `link_type='stated'`, all free text, with **no `related_node_id`
  resolved**. Automatic dependency-ordering checks have nothing to run on yet.
- **Existing product placement is available.** `product_refs` holds 779
  node→lesson refs, and `lesson_metadata.csv` gives grade/module/lesson for
  the current product. This could seed a starting sequence or act as a
  comparison layer.

## 4. New input since the earlier chats: Math Pacing Prioritization (v2.0, 6/25/2026)

It names the **node** as MH2's calibration grain. It sets **Deep / Functional
/ Illuminating** levels with year-level guardrails: roughly 25/50/25% of
targets and **40/45/15% of instructional time**. For the sequencing tool
this suggests:

- a per-node calibration level, which may differ by grade ("a level is not an
  eternal property"), and
- per-grade and per-module time diagnostics, shown as guardrails, never
  enforced as quotas.

Whether calibration is in scope for this tool or is a separate layer is an
open question (§5).

## 5. Open questions that gate design

1. **Source of truth.** Gap audit: "judgments in the app, curriculum
   changes in Word." A grade sequence has no Word home today. Is the tool's
   placement data *the* authoritative sequence (the first time an app DB is
   authoritative)? Or is it exported to a document that becomes the truth?
   This decision changes most of the others.
2. **Users and ownership.** Who are the grade builders (vs. stem writers)?
   One owner per grade, or shared editing? Is there sign-off?
3. **Output shape.** Grade → module → ordered nodes? Are lessons in scope?
   Is time (days or minutes) per node or module?
4. **Multi-grade nodes.** Place one node in several grades (spiral), or
   require one home grade plus "revisit" references?
5. **State variants.** One core sequence plus state overlays, or separate
   sequences per state?
6. **Calibration.** Are Deep/Functional/Illuminating levels and time
   budgets in v1?
7. **Stability.** When a ladder changes, what happens to placed nodes:
   flag, auto-carry, or drop?
8. **UI stack.** React vs. plain JS. Decide after the interaction model,
   not before.

## 6. Proposed Phase 0 (design only, no implementation)

1. Rule on §5 Q1–Q3 (the ones that set the data model).
2. Write `seq_data_model_rev1.md`: placement tables in `mh2_seq.db`, keyed on
   `source_key`, plus the reconcile contract.
3. Paper-prototype one grade (suggest G3 or G6: many multi-grade nodes,
   drafted ladders) using real nodes. Validate with a grade builder.
4. Only then decide the UI stack and write the first scoped Sonnet brief.

## 7. Do not touch (inherited)

Everything in handoff rev 12 §5 and DEFERRED §6. In particular: don't read
`node_grade` as containment in this tool, and don't make the audit read it
as placement.
