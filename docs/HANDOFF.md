# Handoff — 6–9 ladders now actually wired in; two ingest bugs found and fixed; run_reranks blocked on credits

Written at the close of a session whose only job, per the last handoff, was to
run `parse_node_standards` + `candidates` against the six newly-ingested
grade 6–9 ladders and re-check diagnostics. That surfaced two real bugs in
the ingest pipeline — not tuning issues — both are now fixed, tested, and
the fixes are durable across a plain `rm -rf data/build && rebuild.py`.

`candidate_generation_spec.md` (rev 5) is still the source of truth for the
design. This file is what's easy to miss in it, plus everything that
happened after rev 5 was written.

## Where the build is

Steps 1–9 done, step 7 (threshold fitting) done — unchanged from last
handoff. This session did not touch scoring, thresholds, or the reranker's
design; it fixed data-correctness bugs in how the 6–9 ladders enter the
system, then re-ran the pipeline.

A wiped `data/build` rebuilds clean and now **actually is safe** for the 6–9
ladders too (see "Bug 2" below for why that wasn't true for most of this
session): `rm -rf data/build && python scripts/rebuild.py`.

Tests are 7 files under `tests/`, run individually — `pytest` is not
installed in the venv, so use `python tests/test_normalize.py` etc. All 7
pass as of this handoff, after both fixes.

| table | rows | note |
|---|---|---|
| `nodes` | 353 | up from 235 pre-ladder-ingestion; includes the 6 new stems |
| `node_standards_parsed` | 1,954 | up from 1,080 — was corrupted mid-session, now clean (see Bug 1) |
| `candidates` | 81,638 | regenerated against the new ladder content |
| `model_reranks` | 1,810 | loaded from the existing 181-subject cache; unaffected by node text, only by node **identity** (see Bug 2) |

## Bug 1 — table-cell paragraph breaks silently dropped, gluing standard codes together

`full_text()` (`mh2/ingest_ladders.py`) only emitted a line break for
explicit `w:br`/`w:cr` runs — never for the boundary between separate `w:p`
paragraphs in the same table cell. A standards cell listing several codes as
separate lines (pressing Enter between them) had no separator at all once
extracted: `"7.SP.C.5"` and `"7.SP.C.7.a"` on their own lines came back as
one glued `"7.SP.C.57.SP.C.7.a"`.

**Scope:** 97 of the 139 CCSS anchor codes attached to the new 6–9 nodes
(70%) were corrupted this way — unreadable, matching nothing downstream.
This is very likely why FL's Path A candidates for the new ladders were
sitting at 50 total before the fix: not missing alignment-guide data (FL's
grade 6–9 `all_states.csv` rows are real, 909 of them), just anchors that
couldn't be looked up. **After the fix: FL Path A jumped from 50 → 543
candidates**, and the diagnostics blind rate — which had spiked hard right
after ladder ingestion — came back down (FL held-out blind 0.274 → ~0.015,
TX 0.185 → ~0.14-0.19).

**Fix:** `full_text()` now emits `"\n"` at each new `w:p` sibling (guarded so
a single-paragraph call, e.g. one already-scoped paragraph element, doesn't
get a spurious leading blank line). This does **not** affect the 10
pre-existing PK–5 ladders' node text or counts — `test_ingest_ladders.py`'s
`test_per_ladder_counts` (which pins exact counts per file) still passes
unchanged.

## Bug 2 — a plain rebuild silently re-broke last session's hand-fixed stems

The previous handoff's "wipe `data/build` and rebuild" recovery path is
documented as always safe. It was NOT safe for the 6 new ladders: doing
exactly that reintroduced the stem-collision bugs the *previous* session had
already fixed by hand (`EXP` swallowing both `ExpressionsGeneral` and
`One-Variable Equations` again; `NUM`/`COO`/`ONE`/`PRO` reappearing).

**Root cause:** `rebuild.py`'s bulk ingest step
(`mh2.ingest_ladders --glob`) resolves each file's stem via
`resolve_stem()`'s fuzzy word-overlap match, `allow_create=True`. The
correct stem choices from last session's one-off
`scripts/ingest_new.py --stem-id ...` runs only ever existed in the
now-deleted database — nowhere durable — so a fresh rebuild had no way to
recover them and fell back to the same collision-prone auto-derivation.

**Fix, and how it's scoped:** `stems.csv` (`data/source/workbooks/`) already
had a `ladder_file` column for exactly this purpose — it's the project's
existing hand-reviewed file↔stem mapping (`load_stems.py`'s own docstring
says so), just never wired into `ingest_ladders.py`, and it already had
rows for all 7 new topics (`PS_PROBABILITY`, `RP_COORDINATE_SYSTEM`,
`EE_GENERAL_EXPRESSIONS`, `NS_INTEGERS_AND_RATIONALS`,
`NS_IRRATIONAL_REAL_NUMBERS`, `EE_ONE_VARIABLE_EQUATIONS_DEG_1`,
`EE_ONE_VARIABLE_INEQUALITIES_DE`) with `ladder_file` blank and
`ladder_drafted=0`. Populated `ladder_file`/`ladder_drafted=1` for those 7
rows, and added `stem_map_stem_of()` to `mh2/ingest_ladders.py`, consulted
**before** `resolve_stem()`'s fuzzy match.

**Important scoping decision, deliberate, do not "simplify" this later:**
`stem_map_stem_of()` only fires for `stem_map.band = '6_9'`. The 10
pre-existing PK–5 ladders *also* already had a `ladder_file` row in
`stems.csv` (dormant, unused before this session) — wiring the lookup in
without the band restriction renames every PK–5 node
(`ANG`→`MD_ANGLES`, `TIM`→`MD_TIME`, `COM`→`NSS_COM`, etc.), which:
- invalidates the entire paid rerank cache (it's keyed on `node_id`) even
  though none of those files' actual content changed, and
- would silently orphan any future node-keyed review data.
This was found, weighed, and **explicitly declined** (John's call) in favor
of scoping to the 7 new 6–9 files only. If a full PK5→stems.csv-code
migration is ever wanted, it needs to be its own deliberate step (with a
plan for re-running the reranker against the new node IDs), not a side
effect of onboarding future ladders.

**Current stem_id per file, for reference:**

| file | stem_id |
|---|---|
| `MH2_6-A1_Probability_ladder.docx` | `PS_PROBABILITY` |
| `MH2_6A1_Coordinate System_Stem.docx` | `RP_COORDINATE_SYSTEM` |
| `MH2_6A1_ExpressionsGeneral_LessonLadder.docx` | `EE_GENERAL_EXPRESSIONS` |
| `MH2_6A1_NumberSystem_Integers and Rationals_LessonLadder_new.docx` | `NS_INTEGERS_AND_RATIONALS` |
| `MH2_6A1_NumberSystem_IrrationalRealNumbers_LessonLadder.docx` | `NS_IRRATIONAL_REAL_NUMBERS` |
| `MH2_6A1_One-Variable Equations.docx` | `EE_ONE_VARIABLE_EQUATIONS_DEG_1` |
| `MH2_GA1_One Variable Inequalities_Stem.docx` | `EE_ONE_VARIABLE_INEQUALITIES_DE` |
| all 10 PK–5 ladders | unchanged (`ANG`, `TIM`, `COM`, `EST`, `FRA`, `WHO`, `COU`, `SUB`, `ADD`, `MUL`) |

Note `stems.csv` picked up an unrelated formatting pass from a linter/editor
this session (whitespace/quoting normalization on rows this session didn't
touch) — harmless, already reflected in the file on disk.

## A side effect worth confirming with John: the `ADD` ladder is now applied

Last handoff flagged `MH2_PK5_OperationsAndEquations_AdditionAndSubtraction.docx`
(`ADD` stem) as "previewed, never applied — worth a decision." Recovering
from Bug 2 required re-running the bulk ingest tool
(`scripts/ingest_new.py --apply`) over every ladder file, which does not
check `stems.csv`'s `ladder_drafted` flag — it applies every `.docx` present
in `data/source/ladders`. **As a side effect, `ADD` is now applied**: 27
nodes exist under the `ADD` stem in the current database. This was not a
deliberate decision this session — it happened because fixing Bug 2 required
re-running that tool over everything. Worth explicit confirmation that this
is what's wanted; if not, those 27 nodes (and their downstream
`node_standards_parsed`/`candidates` rows) would need to come back out.

## The 4 stale garbled `ONE_VAR_INEQ` nodes — resolved, not by choice

Last handoff flagged 4 leftover garbled nodes
(`ONE_VAR_INEQ-0018`–`-0021`) as confirmed-safe-to-delete but not yet
deleted. They no longer exist: this session did a full `data/build` wipe and
clean re-ingest (required to fix Bug 2), and node IDs are DB-assigned
sequentially per stem on ingest — a fresh ingest never recreated them. Not a
deliberate cleanup action, just moot now.

## Current numbers (post-fix, for reference — re-run `eval.diagnostics` before trusting these further out)

Diagnostic top-50 cut, held-out split:

| state | cov_v | cond@50 | raw@50 | blind | list len |
|---|---|---|---|---|---|
| TX | 94.6% | 0.941 | 0.856 | 0.090–0.19 | ~110-120 |
| FL | 99.1% | 0.973 | 0.959 | ~0.014-0.08 | ~100-110 |
| CA | 97.6% | 0.990 | 0.963 | 0.028 | ~2-3 |

Node placement (top-node-only): CA 0.735/0.488 (ceiling 0.677), FL
0.270/0.195 (ceiling 0.592), TX 0.306/0.166 (ceiling 0.558) — down somewhat
from pre-ingestion (TX was 0.392/0.239, FL 0.426/0.296), consistent with a
much larger, harder truth set now that grade 6–9 has real content instead of
being an artifact. Not yet root-caused further; likely just a harder
problem, not a regression — worth a look if it doesn't move once more 6–9
tagging accumulates.

FL Path A: 543 candidates (109 nodes reached, 0.714 auto-surfaced, 0.500
blind for that subset specifically — Path A alone is a weak+moderate
producer here, most of its precision still comes from being combined with
Path C). TX Path A: still 0, confirmed — TX remains absent from
`all_states.csv` entirely, so **Path C is still TX's only route**,
unaffected by anything in this session.

**266 nodes now have a CCSS anchor** (up from 118 pre-ladder-ingestion) —
this is the number that matters for the reranker follow-up, below.

## `run_reranks.py` — blocked on API credits, not code

The anchored-node universe grew from 118 → 266, meaning the rerank browse
list needs a follow-up run of up to (266 × 2 states) − 181 already-cached =
**~351 new calls**. John is blocked on an API-credits/org-billing issue and
needs to loop in someone with admin access before this can run. No code
changes needed here — `mh2/rerank.py`, `scripts/run_reranks.py`, and
`mh2/load_reranks.py` are all unaffected by this session's fixes.
**`data/reranks/cache.jsonl` (181 subjects) is intact and untouched** —
confirmed it still loads correctly (1,810 `model_reranks` rows) after the
stem fix.

## What's actually next

1. **Confirm the `ADD` ladder question above** — keep it applied, or back
   it out.
2. **Once API credits are restored: run `scripts/run_reranks.py`** for the
   ~351 new (node, state) pairs, then `mh2.load_reranks`.
3. Re-run `eval.diagnostics` periodically as more 6–9 content/tagging
   accumulates — the placement-metric dip above isn't understood yet, just
   flagged as plausible-not-alarming.
4. Decide whether the PK5→stems.csv-code stem migration (declined this
   session, see Bug 2) is ever wanted, and if so, plan the rerank-cache
   re-key as part of it, not as an afterthought.

## Live defects and traps — carried forward from before, unchanged

1. **The generator emits ~48 nodes per standard for TX, ~55 for FL.** §4.2.3
   says top-node-only until the multi-node threshold is fit. Still a spec
   violation; must be fixed before anything renders in a browser.
2. **§4.2's node-text scorer was never built.** `node_confidence` is still
   `1/len(siblings)`. Blocks the multi-node threshold.
3. **CA is quarantined as a Path C target**, in every direction, unchanged.
4. **Run 3b is short 134 leaf codes**, unchanged.
5. **Tokenizer truncates a hyphenated segment followed by digits**
   (`GA.PK.CD-MA3.4d` → `GA.PK.CD-MA`), patched only in
   `scripts/export_anchor_standards.py`, unchanged.
6. **`normalize_code` glues trailing prose onto codes** — 15 rows across IN,
   OR, PA, RI, WI, unchanged.

## The rev 5 policy change — do not undo it

**Presenting writers with bad suggestions is worse than presenting them with
nothing** (John). Step 7 fits for **precision**; a blind row is an accepted
cost, reported but not optimized against. Do not loosen
`SURFACE_THRESHOLD` to chase the (now much-improved, but still real for TX)
blind rate — that's a reversal of a deliberate ruling and needs John.

Also still true: **do not use 0.8 as a score threshold** — TX/FL's Path C
distributions top out below it.

## Conventions that took work to get right (carried forward, plus one new one)

- `data/source` is read-only. `data/build` is disposable. **`data/reranks`
  is neither** — paid API output, durable, never wiped by a `data/build`
  reset.
- Word leaves `~$*.docx` lock files in `data/source/ladders`; use
  `load_stems.ladder_paths()`, never a bare `*.docx` glob.
- `python-docx`'s `.text` silently drops Word equation-editor math **and**
  silently glues multi-paragraph cells together. Always use
  `mh2.ingest_ladders.full_text()`, never `.text`, anywhere a cell might
  contain more than one paragraph or an equation.
- A ladder file's stem_id is settled the moment it has any node
  (`bound_stem_of()`) — never re-derived from filename text after that. For
  a file with no nodes yet: `stems.csv`'s `ladder_file` column (via
  `stem_map`, `band='6_9'` only) is now consulted before the filename-based
  fuzzy match. Inventing a new stem from filename letters when nothing
  matches is refused, not guessed.
- **A plain `rebuild.py` is only as safe as the data backing it.** It being
  "the documented safe default" doesn't make it immune to silently reverting
  hand-made decisions that were never captured in `data/source` — always
  check whether a fix from a prior session lived in the database (gone on
  wipe) or in `data/source`/`stems.csv` (durable) before trusting a rebuild
  to reproduce it.
- Reach is keyed on `(grain, key, state)`, never node id alone.
- Never blend path scores. Never hard-filter on grade. `strength` derives
  from the set of distinct independent paths, not a count of rows.
- `candidate_reviews` is layer 3, append-only, and nothing in steps 1–10
  writes to it.
- The Path 0 gate aborts ingestion on failure by design. `--strict` restores
  a hard abort instead of the CA quarantine.

## Open questions for John

- Confirm/reject the `ADD` ladder being applied (see above) — it happened as
  a side effect of fixing Bug 2, not a deliberate choice.
- Whether `SURFACE_THRESHOLD = 0.70` holds once measured against a full
  round of 6–9 content, or needs a separate cut for that grade band —
  unchanged from last handoff, still not re-measured.
- Whether/when to do the PK5→stems.csv stem-code migration, given it
  requires a rerank-cache re-key.
- Same open items as before, still open: ~100 scope notes needing a
  `partial` vs `exceeds` ruling; whether a TX alignment guide or FL K–5
  guide exists anywhere; whether to strip the 700 backwards-read CA rows
  from `model_predictions`.
