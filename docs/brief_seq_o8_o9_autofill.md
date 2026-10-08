# Brief: period-estimate autofill (rulings O8 + O9)

Status: agreed with John 2026-10-01. Not started.
Authority: `docs/seq_rulings_rev1.md` (O8, O9). This brief turns those rulings into
code. Where the brief and the rulings disagree, the rulings win; report the
disagreement rather than resolving it.
Branch: `seq/v1-prototype`, or a new `seq/autofill` cut from it. Recommended model:
Sonnet (Claude Code).

## What changes for the writer

Today a placement's period estimate starts blank. The writer reads the ladder hint and
types a number, or clicks "Use N" when the hint is a single exact period figure.

After this change, **placing a node saves an estimate straight away**, taken from the
ladder note. The placement card and the drawer mark it **"from ladder"** until the writer
edits or confirms it. The guardrail's time split works from the first placement on.

## The autofill rule (replaces data model §5.4 rule 2–3 / contract §3.2.1)

Input: the `Estimate`s that `_period_hint_from` already keeps for this grade. Steps 1–2 of
the kept-estimate selection are unchanged, except that a multi-grade node's un-graded
estimates are now used for the value as well as the text.

`value` is set as follows. Units are ignored: **day = lesson = period, 1:1** (O8).

| Kept estimate | `value` | Example |
|---|---|---|
| `exact` | `low` | "2 instructional days" → 2; "1/2 lesson" → 0.5 |
| `range`, closed | `low` | "1-2 periods" → 1; "4-5" → 4 |
| `range`, open-ended (`'open_ended' in note`) | `low` | "1+" → 1; "1 or more" → 1 |
| `part_of` with `high` | `high − 0.5` | "part of 1 period" → 0.5; "part of 2" → 1.5 |
| `part_of` with no `high`, or `'group_total' in note` | `None` | "2+ days for the first 6 nodes altogether" |
| `multiple`, `not_fixed`, `embedded`, `unparsed` | `None` | fluency rows |
| More than one kept estimate | `None` | ambiguous; text only |
| `part_of` with `high ≤ 0.5` | `None` | no corpus case; do not invent one |

The multi-grade case (`basis == "ungraded_multi_grade"`) now sets `value` by the same
table (O9: "the same number in every grade"). Keep `basis` in the payload so the UI can
say why.

Measured on the current `mh2.db` (from `docs/review/period_estimates.csv`): `part_of`
gives 27 rows "part of 1" → 0.5, 4 rows "part of 2" → 1.5, and 7 rows of unbounded
group totals that stay blank. 62 rows are ranges and 11 are open-ended. **Report the
before/after count of in-grade nodes with a non-null `value`, per grade PK–A1**, using
the predicate `period_hint(con, node_id, grade)["value"] is not None` over in-grade,
non-leaf nodes.

## Saving on placement

1. **Schema.** Add to `placement` in `mh2/schema_placement.sql`:
   `estimate_source TEXT CHECK (estimate_source IS NULL OR estimate_source IN ('ladder','builder'))`.
   `NULL` means no estimate.
2. **Migration.** `seq_store.ensure_schema` currently returns early when every table
   exists, so a new column in the `.sql` never reaches an existing database. Add a
   column check: if `estimate_source` is missing from `PRAGMA table_info(placement)`,
   run `ALTER TABLE placement ADD COLUMN …` with the same CHECK. Keep it idempotent and
   cheap on the fast path. Existing rows with a non-null `period_estimate` become
   `'builder'`, since a person typed them. Test the migration against a database built
   from the old schema.
3. **Place.** `seq_service._snap` gains `period_hint` (the hint dict, or None). In
   `seq_store._insert_placement`, when `snap["period_hint"]` has a non-null `value`, write
   `period_estimate = value`, `period_hint_seen = hint["text"]` and
   `estimate_source = 'ladder'`. The `place` event's `after` records
   `period_estimate` and `estimate_source`. This applies to `place` and `place_group`.
   `co_place`, `move` and other reorders do not touch estimates.
4. **Edit.** `set_attributes`, on any change to `period_estimate`, including setting
   it to the same number and including clearing it to null, sets
   `estimate_source = 'builder'` (or NULL when cleared). Record it in the event's
   `before` and `after`.
5. **Confirm without changing.** Add an explicit confirm action: a PATCH with
   `{"confirm_estimate": true}` that flips `'ladder'` to `'builder'` and logs a
   `confirm_period` event. A writer who agrees with the ladder can then clear the
   marker without retyping the number. Add it to the contract §4 route table.
6. **A removed and re-placed node** gets a new placement row, so it autofills again.
   That is intended. The old row's history stays in `placement_event`.

## UI (`seq_static/app.js`, `app.css`)

- **The placement card's periods line** (`renderPeriods`): when
  `estimate_source == 'ladder'`, show the number with a small **"from ladder"** pill and
  a **"Looks right"** button, which calls the confirm action. The tooltip shows
  `period_hint_seen`. When it's `'builder'`, show the number plain.
- **The drawer's period hint section**: add a line saying what the autofill chose and
  why, using `basis`. For example: "Autofilled 2 (same estimate used in each grade the
  node spans)" or "Autofilled 0.5 ('part of 1 period')".
- **The "Use N" button** stays. Clicking it is an edit, so the source becomes `builder`.
- **The guardrail footer**: next to "N of M counted", add "(K from ladder)" when K > 0.
- **DemoApi** mirrors all of the above: `place`, `placeGroup`, the PATCH, the confirm
  action and the guardrail count. It must not reimplement the value table in JS; the
  value arrives precomputed in `period_hint.value` from the export (R-S10, "rules live
  in Python").

## Docs to update

- `docs/seq_v1_contract.md`: §3.2.1 (the period hint rule), §3.3 (the placement
  shape gains `estimate_source`), §3.4 and §3.5 (schema, `set_attributes`, confirm), §4
  (route), §5 (the worked examples that show a placement), and the §1.1 O8/O9 rows
  (replace "superseded" with the new text).
- `docs/seq_data_model_rev1.md`: §5.4, and the `placement` DDL comment on
  `period_estimate`, which now reads "periods; day/lesson are 1:1 (O8)".
- `docs/seq_build_plan_rev1.md`: S8 check 3 is superseded. Add one line pointing here.
- `docs/seq_v1_README.md`: remove "No conversion" wording wherever it appears.

## Tests

- `tests/test_seq_read_synth.py::test_period_hint_branches`: extend it with one case
  per row of the value table, including day and lesson units, open-ended, `part_of`
  1 and 2, group total, and multi-grade un-graded. These are pure and need no database
  beyond `hint_db`.
- `tests/test_seq_read.py::test_period_hints`: update the expected values for the
  fixture nodes. Don't pin counts from John's live `mh2.db` (see the 2026-10-01
  timestamp-pin fix).
- `tests/test_seq_store.py`: autofill on `place` and `place_group`; edit to builder;
  clear to NULL; confirm (and confirm when the source is already builder, or with no
  estimate, gives 400 `invalid`); event payloads; migration from the old schema.
- `tests/test_seq_service.py`, `tests/test_seq_api_routes.py`: the confirm route and a
  409 on a stale rev.
- `tests/test_seq_guardrail.py`: ladder-sourced estimates count toward "N of M".
- `tests/js/seq_app_test.js`, `tests/js/seq_e2e_test.js`: DemoApi parity for place,
  edit and confirm; the "from ladder" pill renders and clears.
- Rebuild the three demos (`scripts/export_seq_demo.py --grade 2|4|6`) and run
  `node tests/js/demo_bundle_check.js` on each.

## Acceptance

1. Full `pytest -q` and the JS suites are green.
2. The per-grade before/after count of non-null `value` is reported (see above).
3. On a fresh G2 sequence in the demo: place WHO-0010, and the card shows an estimate
   with "from ladder". Click "Looks right" and the pill clears. Edit to 3 and the
   source stays builder. The history panel shows `place`, `confirm_period` and
   `set_period`, in that order.
4. Run the app against the existing data copy that already has placements. It
   migrates without error, and old estimates show as builder.

## Out of scope

- Converting any unit other than day, lesson and period.
- Splitting a multi-grade estimate across grades (O9 option C was rejected).
- Changing `scripts/report_period_estimates.py`. Its parser is reused as is. If a row
  parses wrongly, report it; don't fix it here.

## Working rules

- Don't commit, merge or push. Leave the changes in the working tree for John to
  review and commit. In a Cowork sandbox, run no git command that writes at all
  (stale lock files, 2026-09-30).
- Don't edit `review_api.py`, `mh2/ingest_*.py`, or `DEFERRED.md`.
