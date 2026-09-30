# Sequencer — start here (unattended run, 2026-09-30 afternoon)

> **Reconciled with branches 2026-09-30.** The three design docs were checked
> against the real branches. Kind vocabulary, the `node_grade_kind` adapter and
> S1b now match `seq/grade_type`. Analysis and prototype findings are folded in
> as rulings O7–O13. Figures were re-verified (§1, §5).

Nothing was merged or pushed. `main` is untouched.

## 1. Merge (about 5 minutes, on your Mac)

The sandbox had no pytest and only Python 3.10, so your full suite has not run.
All three branches merge cleanly together (checked in a scratch clone). Their
71 new or changed tests pass there when run directly, without pytest.

```sh
cd ~/mh2_macro
rm -f .git/objects/maintenance.lock .git/objects/*/tmp_obj_*   # sandbox leftovers, harmless
git switch main
for b in seq/grade_type seq/analysis seq/prototype; do
  git merge --no-ff --no-edit "$b" || break
  .venv/bin/python -m pytest -q || { echo "tests failed after $b"; break; }
done
.venv/bin/python scripts/rebuild.py        # grade_type adds columns + node_grade_kind
git add docs/seq_*.md && git commit -m "Sequencer design docs rev1"
```

If `rebuild.py` passes, check: `SELECT COUNT(*) FROM node_grade` = 535, and
`SELECT kind, COUNT(*) FROM node_grade_kind GROUP BY 1` = core 155 / span 213 /
unconfirmed 143, with no state_extension rows until you add `ccss_default_grades`.
That is 511 of the 535 `node_grade` rows. The other 24 are leaf 13, out_of_band 9
and blank `ruling_type` 2, and have no kind row by design. (These were reproduced
on a copy of the 2026-09-09 DB with the branch loader.) Do not push until you're
happy: pushing `main` triggers the Fly deploy workflow.

## 2. Read, in this order

| # | File | What it is |
|---|---|---|
| 1 | `docs/seq_orientation_rev1.md` | The single design reference. Supersedes `seq_orientation_rev0.md` and `seq_design_brief_rev0.md`. |
| 2 | `docs/seq_data_model_rev1.md` | Proposed DDL for `mh2_seq.db`, the placement reconcile contract, route shapes. Appendix A: the rejected Word-is-truth alternative. |
| 3 | `docs/seq_build_plan_rev1.md` | Sonnet sessions S1–S13 plus S1b (the kind adapter check after the merge) to v1, with gates. Appendix A: the S1 brief, ready to paste. |
| 4 | `docs/seq_analysis_rev1.md` (branch `seq/analysis`) | Period-estimate density and cross-stem link analysis. |
| 5 | `docs/prototype/seq_prototype_g2.html` (branch `seq/prototype`) | Static G2 slice prototype. **Layout not yet eyeballed.** Open it in a browser first. |

## 3. What each branch does

- **`seq/grade_type`**: adds `ruling_type`, `needs_writer_review` and `states_mentioned` to `node_grade_ruling`. Adds a new `node_grade_kind` table (core / span / state_extension / unconfirmed) for the sequencer only; the audit does not read it (DEFERRED §6). `node_grade` is unchanged. Produces `docs/review/grade_split_review.csv` for you to rule on.
- **`seq/analysis`**: adds `scripts/report_period_estimates.py` (a parser plus a coverage report) and `scripts/report_cross_stem_links.py` (stated links, shared CCSS codes, shared EM2 lessons). Includes tests, and CSVs in `docs/review/`.
- **`seq/prototype`**: adds `scripts/render_seq_prototype.py --grade N`, a read-only HTML slice in the style of `render_static.py`. It has progression strips, the node drawer, badges, toggles and a 4-column compare sheet.

## 4. Decisions waiting on you

1. **Fill `john_ruling`** in `grade_split_review.csv` (30 rows), then add a `ccss_default_grades` column to the worksheet. This is the only route by which state-conditional grades become core or state_extension.
2. **Paste the DEFERRED §6 / R-H3 amendment** from `seq_orientation_rev1.md`. It records that `mh2_seq.db` owns placements, as a narrow exception.
3. **UI stack.** The recommendation is plain JavaScript with no build step, following the audit's pattern (Gate B in the build plan).
4. **Time axis.** Periods are usable only for G1–G5, with an "N of M counted" caveat. 6–A1 counts in days or lessons. You need to rule on what a day or a lesson is worth (O8), and whether an estimate on a multi-grade node applies per grade or in total (O9).
5. **Ownership, modules vs. topics, grade-builder logins.** These are O1–O3 in the orientation doc; each has a recommended default.
6. **New since the branches (orientation §12, each with a default):** O7, the 6 out_of_band nodes plus 1 blank-type node that get no kind row and read as "kind pending"; O10, shared-CCSS badges moved out of Flagged into a Pairings toggle, because 22 of 35 G2 nodes carry one; O11, authorizing the stated-link capture fix in `ingest_ladders.py` as a pipeline change; O12, keeping the concept/skill drawer band Goal-only; O13, how to show goal gaps.

## 5. Figure corrections found

- The ladders split 10 PK–5 / 7 6–A1, not 11 / 6: the `MH2_GA1_` Inequalities ladder is in band 6_9.
- DONE markers are gone from node text but remain on 19 concept/skill headings in COU and EST, affecting 32 nodes. They come from the Word files.
- Of the 88 `node_links`, only 56 are real stated links; 19 more mentions are missed by `ASSOC_RE` in `ingest_ladders.py`.
- `docs/prototype/README.md` question 4 gives "mathematical_models (39 c/s), leaves_to_include (31), standards_notes (21), strategies (21), additional_notes (14)". Those are sums over the 11 grade slices, not distinct concept/skills. Distinct, out of 81 multi-node C/S, they are 19 / 12 / 10 / 11 / 7 (orientation §4.5). Its other figures reproduce.
- Until you fill `ccss_default_grades`, `TIM-0010` / `TIM-0011` are `unconfirmed` in G2, not state extensions. The G2 owed inventory is 33 today, and 31 if the review CSV's proposals are ruled as they stand.
- Student-Facing Example is parsed but never stored. The brief's drawer row for it has nothing behind it.
- `compute_lesson_overlap.py` and the Pacing doc are not in the repo.
