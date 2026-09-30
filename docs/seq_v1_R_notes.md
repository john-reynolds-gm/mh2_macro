# Sequencer v1, R (read side): implementer notes

## Built

- `mh2/grade_kind.py`: the contract 3.1 adapter (constants, `kind_source`,
  `build_grade_kinds`, `ruling_by_node`, `kind_for`). Never reads
  `ruling_type` to derive a kind.
- `mh2/seq_read.py`: every function in contract 3.2, with the 3.3 shapes.
  Slice query logic is adapted from `scripts/render_seq_prototype.py`
  (copied, script untouched). `parse_period_notes` is imported from
  `scripts.report_period_estimates`; stem names from
  `mh2.node_lookup.build_stem_names`.
- Tests: `tests/test_grade_kind.py`, `tests/test_seq_read.py` (real data, temp
  copy), `tests/test_seq_read_synth.py` (in-memory DBs from `config.SCHEMA`).

## Deviations from the contract

1. **`NodeFacts` has one extra key, `predecessor_node_ids`** (parallel to
   `predecessor_keys`). Reason: `ordering_badges` must label
   "WHO-0010 unplaced" for a predecessor that is not placed, and W calls
   `node_facts` only for placed keys, so the predecessor's `node_id` is not
   otherwise available to a pure function. Additive; nothing in the contract
   depends on the key set of NodeFacts. W may ignore it.
2. `ordering_badges` returns an entry (possibly `[]`) for every key in
   `positions`, so `ob[key]` (contract 3.9 step 6) never raises.

## Choices where the contract is silent

- `node_text` is whitespace-collapsed in slice, drawer, facts and compare (the
  prototype did the same). `concept_skill` in NodeFacts is the raw label
  (`""` if NULL) so W can compare it with `concept_skill_seen`. `goal` is not
  collapsed.
- Drawer `grade_states` follows the contract literally (every GRADES member
  whose state is not `off_grade`). For a leaf or no_grade_field node, `kind_for`
  returns that state for every grade, so all 11 grades are listed.
- Drawer `lessons` skips refs matching `OTHER_PRODUCT` (same filter as shared
  lessons); `product_refs` lists every row unfiltered.
- Drawer `links` reads `node_links` rows with `link_type = 'stated'`; `stem_id`
  is set when the text equals a stem name (case-insensitive).
- Partner `nodes` are ordered by `(seq, node_id)`, partners by `stem_id`;
  the "(n)" in the shared_code detail is the number of shared codes.
- Multiple predecessors: one badge whose label/detail lists all of them
  (`before N-1, N-2`; `N-1, N-2 unplaced`).
- `normalize_grade` also accepts `pre-k`, `prek`, `kindergarten`, `algebra 1`.
- `placed_elsewhere` refs are sorted by grade order then `placement_id`.

## Measured figures (data copy, `MAX(ingest_log.ts) = '2026-09-09 15:11:11'`, 511 kind rows)

All figures in contract 5.1, 5.2 and 8.1 reproduced; no discrepancy.

- `node_grade` rows 535; kinds core 155, span 213, unconfirmed 143, unknown 24;
  `kind_for` splits the 24 into leaf 13 and unknown 11; no `node_grade` row
  reads `off_grade`. Predicate: `build_grade_kinds` over all `node_grade` rows,
  then `kind_for`.
- Per grade, `in_grade_nodes` = rows of `node_grade` with that grade; `owed_nodes`
  = of those, state in (core, span, unconfirmed, unknown); `chips` = in-grade
  nodes plus nodes of the same `(stem_id, concept_skill)` that are not in-grade.

  | grade | PK | K | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | A1 |
  |---|---|---|---|---|---|---|---|---|---|---|---|
  | in_grade | 24 | 45 | 37 | 35 | 37 | 61 | 54 | 81 | 77 | 37 | 47 |
  | owed | 24 | 44 | 36 | 33 | 36 | 61 | 52 | 79 | 75 | 35 | 47 |
  | chips | 27 | 57 | 59 | 57 | 70 | 82 | 79 | 106 | 107 | 67 | 80 |

- G2 counts: in_grade 35, excl_leaf 33, owed 33, leaf 2, concept_skills 24
  (22 excluding leaf-only), context 22 (21 off_grade + 1 no_grade), stems 7,
  chips 57. In-grade states core 13, span 14, unconfirmed 6, leaf 2
  (COU-0024, MUL-0004).
- Cross-stem shared-code node pairs derived from slice partners over all grades:
  180, equal as a set to `docs/review/cross_stem_links_shared_ccss.csv`.
- Period hints: WHO-0010@G2 2.0 `grade_named`; TIM-0010@G2 None
  `ungraded_multi_grade`; COM-0012@G2 1.0 `single_grade_node`; no day or lesson
  hint carries a value in any grade.
- Compare COM-0012 + WHO-0010 @G2: `shared_ccss == ["2.NBT.A.4"]`,
  `shared_lessons == ["G2-M1-L35"]`.
- Speed: `build_slice` for G2 about 15 ms; all tests run in a few seconds.
