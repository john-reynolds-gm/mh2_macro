# Sequencing analysis rev 1 — time axis and cross-stem pairing

Self-contained. Answers design brief rev 0 §8 Q2 and Q4 from the data as it
stands in `mh2.db`. Written unattended while John was away: nothing here was
ruled on, every choice below is a default he can overturn.

**Provenance.** Run on branch `seq/analysis` against a private copy of
`data/build/mh2.db` (dated 2026-09-09), 353 nodes across 17 stems. Read-only;
nothing was written to `mh2.db` or to the original data folder.
`docs/seq_design_brief_rev0.md` is not in this branch (it is untracked in the
main checkout); its §6, §8, §9 and §11 were read from there. The sandbox had
no pytest and no network, so the two test files were run with a small
hand-rolled runner; results are in §4.

Full per-figure predicates are in `docs/review/period_estimates_summary.md` and
`docs/review/cross_stem_links_summary.md`. The headline predicates are repeated
below so this page stands alone.

---

## 1. The time axis (brief §8 Q4)

**Short answer: not dense enough for a live total in v1. Usable with a visible
"N of M nodes counted" caveat for grades 1–5; not usable for PK, K, or any of
6–A1.**

### 1.1 What the ladders actually say

Estimates live only in `node_fields` where `field='additional_notes'`
(`SELECT COUNT(DISTINCT node_id) ... lower(value) LIKE '%instructional period%'`
gives 196 nodes, matching the brief's premise). They come in three conventions,
and the third was not in the brief:

1. inline phrase — "Likely 1-2 instructional periods", optionally `G4:` prefixed;
2. header plus value rows — "Instructional Period" then "1-2" or "PK: 1" (42 nodes);
3. **a different unit.** The 6–9 ladders say "1 day" / "2 instructional days"
   (Equations, Integers and Rationals, Inequalities, Probability, Irrational)
   or "1/2 lesson" / "1-2 lessons" (Coordinate System). Nothing in the repo
   says a day or a lesson equals an instructional period, so the parser
   records the unit and never converts.

### 1.2 Numbers

Denominator: in-grade nodes = nodes with at least one `node_grade` row and
`node_grade_ruling.is_leaf = 0` → **306**.

| Figure | Value | Predicate |
|---|---|---|
| Nodes with any time cue | 301 of 353 | `parse_period_notes(additional_notes)` non-empty |
| In-grade nodes with a usable estimate, any unit | 245 of 306 (80%) | `usable(e)`: exact, range, or part_of with an upper bound |
| ... in periods only | 159 of 306 (52%) | same, `e.unit == 'period'` |
| Unparsed rows | 3 of 376 | `qualifier == 'unparsed'` |

Per grade, in periods. "Node-level" = the node has any usable estimate.
"Strict" = the estimate names that grade, or is un-graded on a node that sits in
exactly one grade. Strict is what a per-grade readout can use.

| Grade | PK | K | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | A1 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Node-level | 96% | 93% | 92% | 91% | 92% | 90% | 77% | 24% | 13% | 11% | 11% |
| Strict | 29% | 48% | 61% | 70% | 56% | 61% | 60% | 11% | 0% | 0% | 2% |

The gap between the two rows is the real finding. Most PK–5 nodes have an
estimate, but many sit in two grades and carry one un-graded figure, so the
per-grade time a grade builder would sum is unknown for them. Even if days and
lessons were ruled equal to periods, strict coverage in 6–A1 would be 38%,
20%, 17%, 15%. The 6–9 gap is mostly a units problem for Equations, Integers,
Probability and Coordinate System (90–100% covered in days or lessons) and a
missing-data problem for Inequalities (27%) and Irrational (38%).

### 1.3 What a readout must handle

- Bounds, not points: 93 usable rows are ranges, open-ended ("1 or more") or
  part_of (an upper bound only).
- `part_of` and `embedded` mean the time lives inside other nodes' periods;
  adding them as whole periods overcounts.
- Group estimates ("2+ days for the first 6 nodes altogether") are deliberately
  unusable per node.
- Fluency is "not taught in a fixed number of periods" (11 rows, `not_fixed`).

---

## 2. Cross-stem pairing (brief §8 Q2, §9, §11)

### 2.1 Three signals

| Signal | Count | Predicate |
|---|---|---|
| Stated links in `node_links` | 88 rows, **56 real mentions** | `link_type='stated'`; noise removed by `noise_reason()` |
| Mentions the ingest missed | 19 | `extract_mentions()` minus `node_links` |
| CCSS-shared node pairs, different stems | **180** (30 codes in 2+ stems) | `node_standards_parsed`, `state IS NULL`, shared `standard_code`, `stem_id` differs |
| EM2-lesson-shared node pairs, different stems | **30** | `product_refs.lesson_id IS NOT NULL`, TX/Bluebonnet/Maryland refs excluded, stems differ |
| CCSS and lesson together | 13 | `cset & lset` |
| Any 2+ signals (stated counted at stem level) | 39 (8 with all three) | `n_signals >= 2` in `cross_stem_pairs_combined.csv` |

The 88 stated rows are mostly not links. The ingest's `ASSOC_RE` takes
everything after the "Associated Concepts from Other Stems" heading, so period
estimates, fluency lists, lesson references and quotations arrive in
`related_text`: 32 of the 88 are noise. It also misses `Related concepts:`,
`Related to:`, `Associated concepts:` and headerless prose ("overlap with Base
Ten structure"), which add 19 mentions. Resolution to a stem: 35 high, 15
medium, 13 low confidence, 2 same-ladder, 10 unresolved ("Rounding",
"Parallel/perpendicular", "Modeling" name concepts, not stems). Most-named
target: Whole Numbers and Base Ten Structure (29).

The stated signal is stem-level, so it is reported beside the node-pair
signals rather than merged into them. 7 stem pairs are supported by a stated
link and a computed signal; 7 by a stated link alone (for example ADD/WHO,
COU/TIM, EST/FRA); 4 more point at stems with no drafted ladder.

### 2.2 Grade 2

22 node pairs share grade 2 and at least one signal; 9 have two or more. The
strongest two are each supported by CCSS, lesson and stated link:

- `COM-0012` / `WHO-0010` — "Compare multi-digit numbers using place value
  relations" / "Compare whole numbers by using place value." (1 code, 1 lesson,
  Jaccard 0.33)
- `EST-0009` / `MUL-0001` — estimation by decomposing and recomposing /
  equal groups by repeated addition (1 code, 1 lesson, Jaccard 0.17)

Most G2 pairs are Counting ↔ Whole Numbers (6 pairs) on a shared code. That is a
genuine pairing, but it also shows the CCSS signal fans out: one code can pair a
Counting node (`COU-0022`) with four Whole Numbers nodes.

### 2.3 Stated-link parsing in v1?

Do the small version. Fix the capture (stop at the first non-concept line, add
the two missing headers), resolve to stem only, and show it as a stem-level
badge and as corroboration on pairs CCSS or lesson overlap already found.
Defer concept and node resolution: the candidates are overlap guesses, and a
wrong pairing displayed as "the writer said so" misattributes authorship. The
stated links add 7 stem pairs nothing else supports, which is worth having,
but they are not a pairing engine.

---

## 3. Implications for the sequencer

1. **Guardrail readout.** Ship level (Deep / Functional / Illuminating)
   distribution by node count first; it needs no time data. Add time as a
   second panel that always shows "time known for N of M placed nodes" and
   carries low–high bounds. Do not render a time percentage against 40/45/15
   for PK, K or 6–A1. This is a coverage rule, not a quota.
2. **Period estimate belongs on the placement** (brief §4.1), and the data
   agrees: multi-grade nodes carry one figure for several grades. Seed the
   placement's estimate from the node's figure only when the node has one
   grade or the text names the grade; otherwise leave it blank for the grade
   builder. The parsed `grade` field does this.
3. **Units need a ruling before any 6–9 time readout.** What is a day, or a
   lesson, in instructional periods? Until then the readout is PK–5 only.
4. **Comparison table evidence.** Lead with the 13 pairs found by CCSS and lesson
   together (8 of them also stated-linked), then the 24 CCSS-plus-stated pairs. Sort by number of
   signals, then shared-grade, then Jaccard. Shared-grade matters: 167 of 180
   CCSS pairs share a grade; 4 of 30 lesson pairs do not.
5. **Fan-out control.** A single shared code can generate many pairs
   (Equations ↔ Inequalities alone produce 32). Group by stem pair in the UI
   and show the code, not a pair per node.
6. **Undrafted stems.** Stated links to Spatial thinking, Ordering and
   Multiplication/Division (Fractions) can only appear as placeholders.

### Open rulings this raises

- Does a day or a lesson convert to an instructional period, and at what rate?
- Does an un-graded estimate on a multi-grade node apply per grade or in total?
- Is "Part of 1 instructional period" a cost of zero, of one, or of a fraction?
  The parser keeps it as an upper bound and does not decide.
- Should the ingest capture for stated links be fixed in `mh2/ingest_ladders.py`
  (a change to the pipeline, not made here)?

---

## 4. Verification

Both test files were executed with a throwaway runner (import the module, call
each `test_` function), because the sandbox has no pytest:

- `tests/test_period_estimates.py`: 34 passed, 0 failed.
- `tests/test_cross_stem_links.py`: 9 passed, 0 failed.

`python3 -m py_compile` passes on all three scripts and both test files. Tests
cover the parsing rules, not the database joins; the joins were checked by
reading the output rows, not by assertion. Not run under pytest, and not run
against the main data folder.

## 5. Files

- `scripts/report_period_estimates.py` — parser (`parse_period_notes`, pure) and report.
- `scripts/report_cross_stem_links.py` — three-signal report.
- `tests/test_period_estimates.py`, `tests/test_cross_stem_links.py`.
- `docs/review/` — copies of the report CSVs and both summaries, so they travel with the branch.
  `data/reports/` is not ignored in `.gitignore` (only `coverage.html` is), but regenerating there
  would churn tracked files, so the copies live here.
