# Brief — ladder edits export ("Download latest ladder edits")

Status: agreed with John 2026-09-30, implemented the same day.
Authority: `DEFERRED.md` §6 (ladders are the tagging source of truth; the
tool must not become a third), §7 (`GET /api/worklist` has no consuming UI),
§8 R-H1 (ladders are never written back), R-H3.

## The workflow this serves

1. Writers review coverage in the app. The lead (Debbie) enters every
   suggested change herself, so **anything entered in the app counts as
   signed off** — there is no separate approval state.
2. One writer, the ladder keeper, owns editing the Word ladders.
3. The keeper clicks **Download latest ladder edits** and gets an Excel
   workbook listing every standard code to add to or remove from a node.
4. The keeper edits the ladders. The next rebuild reads them, and anything
   that landed drops off the next download. The keeper never reports back
   to the app.

Excel was chosen over Word after comparing samples
(`data/reports/export_samples/`): one row per edit, sortable, filterable,
with a Done column for the keeper.

## What counts as an edit

Only tag additions and removals on a named node:

- **Add** — a `tag_proposal` in state `open` or `needs_attention`, unless
  the node it names now carries the standard. That last case has landed
  but not been reconciled yet, for example after `--skip-review-reconcile`;
  `GET /api/standards/{id}` drops it the same way.
- **Remove** — a `tag_review` with outcome `incorrect_tag` whose node still
  carries the standard, resolved through the same alias path the rollup
  uses (`coverage.build_tags_by_standard`). The code is shown as the ladder
  spells it (`Tag.raw_code`), since that is what the keeper will search
  the cell for.

Out of scope: `partial_coverage` tag reviews, standard-level reviews,
overrides and notes. None of them names an edit to a node.

## The removals check

Until now nothing could tell whether an `incorrect_tag` had been acted on.
`mh2/ladder_edits.removal_state()` classifies each one against the current
`mh2.db`:

| state | predicate |
|---|---|
| `removed` | node's `source_key` present, standard no longer tagged on that node |
| `still_tagged` | node present and still tagged → exported as a Remove row |
| `needs_attention` | `source_key` absent from `nodes` (node reworded or deleted) → exported, flagged |

The state is **derived, not stored**. `reconcile_review.py` reports all
three in `review_reconcile.txt` but, per §5.2, still never modifies a
`tag_review` row. Consequence, accepted: if a removed tag is later typed
back into the ladder, the old `incorrect_tag` judgment resurfaces it as a
Remove row. That is the judgment still standing, not a bug.

## Surface

- `GET /api/export/ladder-edits.xlsx`, behind the same auth dependency
  as every route. Filename `ladder_edits_<YYYY-MM-DD>.xlsx`.
- A **Download latest ladder edits** link in the writer bar, which
  appears only when the page is served (the D1 `ONLINE` gate), same as
  every write control.
- Each download is a full snapshot of what's outstanding, not a delta, so
  nothing is stored per download.
- The header gives the date the ladders were last read into the tool
  (`MAX(ingest_log.ts)` in `mh2.db`, which moves with the database when a
  rebuild swaps it in). An edit made after that date still appears until
  the next rebuild.

## Deferred

- **"Applied, waiting for rebuild" mark** — see `DEFERRED.md` §9.
