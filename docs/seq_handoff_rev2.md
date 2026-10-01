# MH2 Grade Sequencing Tool — handoff rev 2 (2026-10-01)

Paste this as the first message of a new chat. Recommended model: **Opus** for
reviewing rulings and design changes; **Sonnet** (Claude Code) for fixes and
applying rulings.

## 1. Where things stand

- **Branch `seq/v1-prototype`** in `~/mh2_macro` holds a working v1 prototype. It
  contains `seq/grade_type`, `seq/analysis` and `seq/prototype` too. **Not merged
  to `main`. Not pushed** (pushing `main` triggers the Fly deploy).
- **Verified on John's Mac (step 2 complete):** rebuild OK; pytest **490 passed + 2
  test-only failures, now fixed** (they pinned the ingest timestamp of the
  sandbox's DB). FastAPI ran for real on Python 3.14 (`test_seq_api_fastapi.py`
  passed). G2 counts reproduced on a fresh rebuild: 35 in-grade / 33 owed / 22
  context / 57 chips; kinds core 13, span 14, unconfirmed 6, leaf 2. App ran via
  `uvicorn seq_api:app --port 8001` on a data copy.
- **Browser check notes:** _(John: add anything that looked off in step 2e, or
  "none")._

### Uncommitted on the branch right now

| File | What | Action |
|---|---|---|
| `tests/test_seq_read.py` | Timestamp-pin fix (2 asserts + `import re`) | Commit: `seq tests: don't pin the ingest timestamp to one build` |
| `data/build/mh2_seq.db`, `data/reports/review_reconcile.txt` | Gap-audit reconcile from the rebuild: proposals #2, #3 (K.CC.A.1) → `landed_elsewhere`. Correct behaviour, not sequencer work. No placement tables in the real DB. | Commit separately: `Rebuild reconcile: K.CC.A.1 proposals landed elsewhere` |
| `data/reports/rebuild_log.tsv` (untracked), two deleted `~$…SAMPLE` Office lock files | Noise | Ignore, or commit the lock-file deletions |
| `docs/seq_handoff_rev2.md` | This file | Commit with the test fix |

## 2. What the prototype is

FastAPI app (`seq_api.py`, separate from `review_api.py`, same Basic-auth pattern)
+ plain JS frontend (`seq_static/`, no build step) + placement tables in
`mh2_seq.db` keyed on `source_key`. Also an offline single-file demo
(`docs/seq_demo/seq_demo_g{2,4,6}.html`, built by `scripts/export_seq_demo.py`).

Features: grade slice with progression strips and kind badges; filters
(context, unplaced-only, pairings, flagged) and saved views; right drawer
(Goal band separated from node fields); compare bottom sheet (≤4, URL-encoded,
co-place action); module builder (modules → slots → placements, chevrons, move,
co-place, remove, calibration, period estimate, cross-grade differentiation
note); live guardrail (25/50/25 count, 40/45/15 time, "N of M counted");
needs-attention queue (orphaned / grade_changed); optimistic revisions (409);
append-only `placement_event` log.

## 3. Key docs on the branch (read in this order, only as needed)

1. `docs/seq_v1_README.md` — overview, how to run, visual-risk list (12 items).
2. `docs/seq_v1_contract.md` §1.1–1.2 — **every provisional decision** (O1–O13,
   gates, assumptions). This is the review list.
3. `docs/seq_orientation_rev1.md` — design reference; §2.4 has the DEFERRED
   §6 / R-H3 amendment text (app DB owns placements).
4. `docs/seq_data_model_rev1.md`, `docs/seq_build_plan_rev1.md` — only if
   changing schema or planning sessions.
5. `docs/seq_analysis_rev1.md`, `docs/review/*.csv` — period-estimate density,
   cross-stem link evidence.

## 4. Rulings John made

- `mh2_seq.db` owns placements (narrow exception to "no third source of truth";
  Word stays authoritative for nodes/tags/grades). Amendment text drafted, **not
  yet pasted** into DEFERRED.md.
- Calibration level is placement-scoped.
- UI stack: accepted plain JS by default (provisional O6).

## 5. Next steps (step 3 onward)

1. **Commit** the uncommitted items in §1.
2. **Rule on provisional decisions** in contract §1.1–1.2. Hardest looks: O1
   (ownership/editing), O8/O9 (time units: days/lessons → periods; per-grade vs
   total estimates), O10 (Flagged vs Pairings).
3. **Fill `docs/review/grade_split_review.csv`** (30 rows), then add a
   `ccss_default_grades` column to the grade worksheet — the only route by which
   state-conditional grades become core vs state_extension.
4. **Paste the DEFERRED amendment** if agreed.
5. **Merge `seq/v1-prototype` → `main`** once green; don't push.
6. **Walk one grade builder through the demo** before building more.

## 6. Known gaps and cautions

- Visual layout only partly checked (one narrow-width screenshot looked right).
- Not done by design: stated-link capture fix in `ingest_ladders.py` (O11),
  `student_facing_example` storage (O5), DEFERRED amendment (O4), wiring the
  sequencer into the audit app, multi-worker uvicorn.
- `reviewed_by`/`proposed_by` in the audit are still client-supplied strings;
  the sequencer binds `placed_by` to the authenticated user.
- Data notes: ladders split 10 PK–5 / 7 6–A1; DONE markers remain on 19
  concept/skill headings (COU, EST) — display-stripped only; 32 of 88
  `node_links` are noise; `compute_lesson_overlap.py` and the Pacing doc are not
  in the repo.
- **Working rule:** Claude's sandbox must not run git commands that write to
  `~/mh2_macro` (it left stale lock files on 2026-09-30). Claude prepares
  changes; John runs git.
