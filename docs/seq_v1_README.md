# Grade Sequencing Tool, v1 prototype

Branch `seq/v1-prototype`, 2026-09-30. It is built on the contract in
`docs/seq_v1_contract.md` and was assembled from three parallel parts:
read side (R), write side and API (W), and frontend (F). This page covers
what the prototype does, how to run it, what is provisional or unverified,
and what to do next.

## 1. What it does

For a single grade, the tool shows every concept/skill that has at least one
node in that grade, laid out as ladder progressions. You build a sequence by
placing those nodes into modules. Each placement gets a calibration (Deep,
Functional or Illuminating) and a period estimate. A readout shows how the
sequence balances against reference marks.

### Screens

| Region | What you see | What you can do |
|---|---|---|
| **Header** | The grade picker, e.g. "G2 · 33 owed · 4 placed". A stamp for when the ladders were read and where the kinds came from. Who you are signed in as, plus a "Your name" box when auth is off. A Demo badge in the offline file. | Switch grade. Set the name your edits are attributed to (only when auth is off). |
| **Toolbar** | Toggles: Context, Leaves, State ext, Unplaced only, Pairings, Flagged. A Stems menu, saved views, Reset view, and "shown n of N chips". | Filter the slice. Hide or show stems and concept/skills, and expand or collapse all of them. Save, rename, delete and apply named views. Every lens setting is kept in the URL. |
| **Slice** (middle) | Super-stem headings (sticky), then stems, then concept/skill strips. Each strip has its goal (clamped to 2 lines, with more/less). Chips wrap and are joined by arrows. Other-grade chips are dimmed and show grade pills. | Click a chip to open the drawer. "+ Place" puts it in the target module. "compare" adds it to compare (up to 4). Stems have up/down, collapse and hide. |
| **Rail** (left) | The Balance panel (sticky), with tabs for the whole sequence and for this module. It has a counts bar and a time bar with reference ticks, and the note "Reference marks, not quotas". Below it: Needs attention, then modules, slots and placements, then History and Download CSV. | Start the sequence. Add, rename, reorder and remove (empty) modules. Choose the target module. Per slot: move up/down (this crosses module boundaries at the ends), move to a module, merge into the slot above, and label it. Per placement: calibration, periods (autofilled from the ladder hint on place and marked "from ladder" until edited or confirmed with "Looks right"; "Use n" re-applies the hint), a differentiation note, co-place with another slot, ungroup, remove (second click confirms), and open in drawer. |
| **Drawer** (right) | The concept/skill band (goal only), then the node band: kind in this grade, grade pills, and grade as written. Then the placement box, "also placed in", the progression mini-map with prev/next, the period hint, node fields, CCSS codes (shared codes marked), state codes (collapsed), pairings, shared EM2 lessons, stated links ("as captured") and product refs. | Place into a module. Jump along the progression. Open partner nodes. A stated link that names a stem scrolls to that stem. |
| **Compare** (bottom) | A tray, "Compare 2/4 · Open · Clear". The sheet has up to 4 columns with sticky row labels and headers, and highlights shared CCSS codes and lessons. | Hide empty rows, or put shared rows first. Resize the sheet by dragging or with the arrow keys. "Co-place in a module" places the unplaced ones together in one slot. |

### Rules the page enforces

- **Off-grade nodes.** Placing a node that is outside this grade's inventory asks you to confirm it as a bridge. It is then badged "bridge".
- **Nodes already placed in another grade.** The page shows where, and offers an optional "How is it different in Grade N?" note. It never blocks the placement.
- **Ordering warnings** come from the server. "before WHO-0010" means a node is placed before its ladder predecessor. "WHO-0010 unplaced" means the predecessor is not placed at all. Both are warnings only.
- **Concurrent edits.** If someone else changed the sequence, your structural change is refused (409). The page then shows the latest version with the notice "Someone else changed this sequence…". Calibration, period and note edits use a per-placement revision, so they do not collide with structural edits.
- **Ladder changes.** When a node is reworded or deleted in the ladders, its placement becomes an orphan under **Needs attention**, with suggested successors and a Re-attach button. When a grade ruling changes, the placement is flagged "grade changed" and you can Keep (acknowledge) it or Remove it. Placements are never deleted automatically.
- **Keyboard.** Every control is a native element. Alt+↑ and Alt+↓ on a focused slot or module card move it. Esc closes the topmost layer: an inline edit, then the view form, the place prompt, the Stems menu, the drawer, the sheet, and finally the narrow-screen rail.

## 2. How to run it

### a. The offline demo (no server)

Double-click `docs/seq_demo/seq_demo_g2.html`, or `seq_demo_g4.html` or
`seq_demo_g6.html`. Each is one self-contained file built from `mh2.db`; the
footer says "data built from mh2.db". Everything works except the
server-only parts:

- no ordering warnings (the rail footnote says "Ordering warnings need the server");
- no successor suggestions for orphans.

Your edits are kept in that browser's localStorage. **Reset demo** clears
them, and nothing is ever sent anywhere.

To rebuild a demo, for example after a rebuild of `mh2.db`:

```sh
.venv/bin/python scripts/export_seq_demo.py --grade 2 --out docs/seq_demo/seq_demo_g2.html
```

`--grade` takes PK, K, 1–8 or A1. Add `--with-sequence` to embed your current
placements as the demo's starting state.

### b. The real app (FastAPI, on your Mac)

`seq_api.py` is its own app, separate from `review_api.py`. It needs
`node_grade_kind` in `mh2.db`, which `scripts/rebuild.py` creates on this
branch because it includes `seq/grade_type`. Without that table every node
reads "kind pending".

The first call creates the placement tables inside `data/build/mh2_seq.db`,
next to the audit's review tables. It does not touch the review tables, and
`rebuild.py` never deletes that file. For a first try, point it at a copy:

```sh
cd ~/mh2_macro
mkdir -p ~/mh2_seq_trial/build
cp data/build/mh2.db data/build/mh2_seq.db ~/mh2_seq_trial/build/
MH2_DATA_DIR=~/mh2_seq_trial .venv/bin/uvicorn seq_api:app --reload --port 8001
```

Then open **http://127.0.0.1:8001/seq/**. `/seq` without the slash redirects
there. The API is under `/api/seq/…`; FastAPI's own docs are at `/docs`.

To use your real data, leave out `MH2_DATA_DIR`.

### c. Auth

- **`MH2_AUTH_USERS` unset (auth off):** anyone can use it.
  - Edits are attributed to the name typed in the header's "Your name" box. It is stored in the browser and sent as the `X-MH2-User` header.
  - If the box is empty, edits are attributed to `local`.
- **`MH2_AUTH_USERS="a:pw1,b:pw2"` (auth on):** every route asks for HTTP Basic credentials: the page, the two assets and the API.
  - The browser shows its own login prompt.
  - Edits are attributed to the Basic user. A name sent in `X-MH2-User`, or a `placed_by` field in the request body, is ignored.
  - These are the same credentials and the same rules as the audit (O3).
- **Ownership.** `owner` is advisory. Anyone signed in can edit any sequence. When the sequence's owner is not you, a banner says "You are editing X's sequence". Every write goes in the History with the writer's name.

### d. Tests

On your Mac, from the repo root:

```sh
.venv/bin/python -m pytest -q                    # the whole suite, seq tests included
.venv/bin/python -m pytest -q tests/test_seq_*.py tests/test_grade_kind.py   # sequencer only
node tests/js/seq_app_test.js                    # frontend logic + DemoApi + stub-DOM smoke (needs node)
node tests/js/seq_e2e_test.js                    # real HttpApi -> HTTP -> seq_service, vs DemoApi (needs node)
```

- **pytest.** `tests/test_seq_api_fastapi.py` is the first test that runs `seq_api.py` under real FastAPI, via TestClient. It SKIPs in the sandbox, so it has never run anywhere. Watch it.
- **Direct runs.** Every `tests/test_seq_*.py` also runs directly with `python tests/test_seq_x.py`, printing `ok` / `SKIP` / `FAIL`.
- **Data.** Tests that need real data copy `mh2.db` / `mh2_seq.db` into a temp folder, about 150 MB per copy, and remove it at exit. They never write the real files.
- **Node.** node is optional. Without it, the node-driven tests SKIP.
- **HTTP harness.** `tests/seq_http_harness.py` serves `seq_api.py`'s own route functions over Python's stdlib `http.server`, so you can try the API without FastAPI: `MH2_DATA_DIR=~/mh2_seq_trial python tests/seq_http_harness.py --port 8765`, then open http://127.0.0.1:8765/seq/. It imports `seq_api.py` with small stand-ins for fastapi and pydantic, so its route table cannot drift from the real one.

Results in the sandbox (Python 3.10, no pytest or FastAPI, data copy with
`MAX(ingest_log.ts) = 2026-09-09 15:11:11`):

| File | ok | skip | fail |
|---|---|---|---|
| test_grade_kind.py | 10 | 0 | 0 |
| test_seq_read.py | 18 | 0 | 0 |
| test_seq_read_synth.py | 23 | 0 | 0 |
| test_seq_store.py | 17 | 0 | 0 |
| test_seq_reconcile.py | 12 | 0 | 0 |
| test_seq_guardrail.py | 9 | 0 | 0 |
| test_seq_auth.py | 6 | 0 | 0 |
| test_seq_api_routes.py | 9 | 0 | 0 |
| test_seq_service.py | 16 | 0 | 0 |
| test_seq_frontend.py | 11 | 0 | 0 |
| test_seq_integration.py | 7 | 0 | 0 |
| test_seq_api_fastapi.py | 0 | 3 | 0 (no FastAPI here) |
| tests/js/seq_app_test.js | 39 | – | 0 |
| tests/js/seq_e2e_test.js | 14 checks | – | 0 |

W's service and reconcile tests now run against R's real read modules. The
pre-existing tests that run without FastAPI (18 files, 292 tests, through a
small pytest stand-in) all pass. `test_load_predictions.py` cannot be
imported on 3.10 because `mh2/load_predictions.py` uses 3.12 f-string
syntax. It is unchanged from `main` and is not a regression.

## 3. What is provisional

Every ruling the prototype runs on is **Provisional (Claude, 2026-09-30),
confirm**. They are listed in the contract's decision tables:
[§1.1 Open rulings and gates](seq_v1_contract.md#11-open-rulings-and-gates)
(O1–O13, Gates A/B/K/P) and
[§1.2 Other assumptions](seq_v1_contract.md#12-other-assumption-confirm-items-from-the-design-docs-decided).
The ones you will notice first:

- **O1.** Ownership is advisory; anyone signed in can edit.
- **O2.** Modules only, no topics.
- **O8/O9.** One day = one lesson = one period. Placing a node saves the ladder's number straight away, marked "from ladder" until the writer edits it or clicks "Looks right". A multi-grade estimate fills the same number in each grade; "part of N" fills N − 0.5; group totals and fluency notes stay blank.
- **O10.** Shared-code badges appear under the Pairings toggle, not under Flagged.
- **Q1.** Stems start collapsed when a grade has more than 100 chips (G6, G7).
- **Guardrail targets.** Count targets are 25/50/25 and time targets 40/45/15. The time marks appear only at 50% or more period coverage.
- **O4.** The DEFERRED §6 amendment is still yours to paste.

## 4. What is unverified

- **FastAPI on Python 3.14.** `seq_api.py` has never been imported under real FastAPI or pydantic v2. What has been checked:
  - it compiles;
  - the ast route test passes (33 routes);
  - the harness runs its exact route functions, bodies and dependencies with stand-ins;
  - `tests/test_seq_api_fastapi.py` has been checked against the harness but still needs its real run.

  The most likely first-run issue would be in the binding of a body model or a `Depends` parameter. It would show up as a 422 or a 500 on the first write.
- **Visual layout.** No browser was available, so the CSS has been reviewed by reading only. See §6.
- **Multi-worker writes.** Revisions are checked inside `BEGIN IMMEDIATE`. Eight simultaneous writes through the threaded harness gave one 200 and seven 409s, with no corruption. Several uvicorn workers have not been tried.

## 5. Known limitations

- There is one active sequence per grade. The demo files hold one grade each.
- The demo has no ordering warnings and no successor suggestions (the documented degradations).
- **Re-attach** offers only the server's suggestions; there is no "pick any node" control. You cannot place into a specific slot or after a specific slot straight from a chip. Use "Co-place with…" and the chevrons after placing.
- Archiving a sequence and changing its owner exist in the API but not in the page.
- Time is in periods, and a day or lesson counts as one period (O8). Grades 7–A1 still have fewer autofilled estimates, because their notes are often missing or give no single number.
- Stated links are shown as captured. Some are noise, per O11, because the capture fix is a pipeline change.
- `student_facing_example` is not shown, because it is not stored (O5).
- `seq_api` is not mounted into `review_api` yet. That would be `app.include_router(seq_api.router)`, a one-line change to a do-not-touch file, and needs your go-ahead.

## 6. Visual risks to check in a real browser

These come from reading the render code and the CSS, not from seeing them:

1. **1280–1599 px with the drawer open.** The columns are now 340 / slice / 380, which should fit two 220 px chips per row. Check that it does, and that a scrollbar does not push it down to one.
2. **Heights of the sticky stacks.**
   - In the rail, the Balance panel (bars plus legends) is about 200 px tall. On a 13" laptop it may leave little room for modules.
   - In the slice, the super-stem headings stick while stems scroll under them.
3. **Menu layering.** The Stems menu panel should float above the sticky headings (header z 6 > headings z 3).
4. **Narrow widths (under 1280 px).** The rail and the drawer both become full-height fixed overlays over the header. Check they can be closed (the Sequence button or Esc; ✕ Close) and that they do not trap each other at about 800 px.
5. **Compare sheet.**
   - Columns are now capped at about 320 px by capping the cell content. Check the sticky corner, the row labels and the header when you scroll both ways.
   - Check that long state-code lists wrap.
6. **Line clamps.** The goal, the chip text and the placement text use `-webkit-line-clamp`.
7. **Chip rows.** Chips vary in height within a row, so wrapped strips may look ragged.
8. **Rail density.** Each placement row packs a lot into about 340 px: the segmented calibration control, periods, the hint with "Use n", the note, and the tools.
9. **Balance ticks.** The ticks are positioned by percentage and centred with −1 px. Check they line up with the segment ends.
10. **Contrast** was calculated, not measured. Check the dimmed context chips and the yellow text on yellow.
11. **Native select menus.** After picking a value from a native select (grade, Move to module, Co-place with…), check the page re-renders at once. There is a guard for this: the page defers renders while the mouse is down, and a menu can swallow the mouseup.
12. **Mouse resizing** of the sheet grip. Only the keyboard path is tested.

## 7. Recommended next steps to pilot

1. **Run the full suite on your Mac.** Merge or fetch the branch (see `docs/seq_v1_START_HERE.md`), rebuild, run `pytest -q`, and read the result of `test_seq_api_fastapi.py`.
2. **Click through G2 on a data copy, following contract §9.6:**
   - start a sequence, add 2 modules, and place 4 nodes, one of them a bridge;
   - co-place two nodes, set calibration and periods, and watch the Balance panel change;
   - open two windows and check that the second one's structural write gets the 409 notice;
   - turn auth on and check the 401s and the attribution;
   - run `python -m mh2.seq_reconcile`: it writes `data/reports/placement_reconcile.txt` and no rows.
3. **Look at the §6 visual risks** at 1280, 1440 and 1920 px, and at 1024 px.
4. **Rule on the provisional decisions** in §3, especially O1 (ownership), O8/O9 (time units) and the guardrail targets. Paste the DEFERRED §6 amendment (O4).
5. **Walk a grade builder through the G2 demo file** (Gate B). The walkthrough questions Q1–Q5 are answered provisionally in the contract.
6. **Decide how it is served for the pilot.** One option is to mount it in `review_api`: one line, a do-not-touch file, and deployed with the audit on Fly. The other is to run it as a second app. Until you decide, keep it local.

## 8. Integration notes (what changed at integration)

The integrator made these changes on top of R, W and F:

- `tests/test_seq_{read,reconcile,service,store}.py`, `tests/test_grade_kind.py`: temp copies of the real DB (about 150 MB each) are now deleted at exit. The leak had filled the sandbox disk and failed `test_grade_kind.py`.
- `seq_static/app.css`:
  - two chips per row with the drawer open at 1280–1599 px;
  - compare columns no longer grow to the width of a full sentence (the max-content table ignored `td` max-width);
  - scroll margins under the sticky headers;
  - centred ticks.
- `seq_static/app.js`:
  - one Esc no longer closes two layers (the region listener and the document listener both ran);
  - the "don't re-render while the mouse is down" guard can no longer freeze rendering after a native select;
  - Esc closes the narrow-screen rail;
  - no "Open in drawer" on an orphan (it would 404);
  - DemoApi's `module_not_empty` error carries `n_placements`, as the server's does.
- `tests/js/seq_app_test.js`: the Esc chain assertion now includes the rail, and there is one new test for the two fixes above.
- New:
  - `tests/seq_http_harness.py`;
  - `tests/js/seq_e2e_test.js`;
  - `tests/test_seq_integration.py` (guardrail parity over 200 random lists, compare parity over 20 random G2 sets, export round trip, route table, auth through the harness);
  - `tests/test_seq_api_fastapi.py`;
  - demo bundles for G2 (now from mh2.db), G4 and G6.

The implementers' reported deviations were checked against each other:

- **R** added `NodeFacts.predecessor_node_ids`. W ignores it, and it is used for the "WHO-0010 unplaced" labels. Fine.
- **W** added `seq_store.sequence_id_of`. It is additive and internal.
- **W** did not write a shared guardrail JSON file. W's Python table and F's JS fixture both match contract §5.8. Integration also checks Python against JS on 200 random lists and on the e2e final state.
- **F** added a payload key `source`. It is footer only; the round-trip test ignores extra keys.
- **F** added `encodeUrlQuery` / `decodeGrade`. Fine.
- **F** placement actions send the *sequence* revision; only the attribute PATCH sends the placement revision. This matches W's store:
  - co-place, ungroup, remove, reattach and acknowledge all check `grade_sequence.rev`;
  - `set_attributes` checks `placement.rev`;
  - the e2e scenario runs every one of them over HTTP with no spurious 409s, and gets the intended 409s for stale revisions.
- **F** puts the drawer in a third grid column at ≥1280 px. Kept, with narrower columns from 1280 to 1599 px (see above).

All do-not-touch files are byte-identical to the contract commit `4cb3f99`.
Compared with `main`, the only differences outside the sequencer's own files
come from `seq/grade_type`, `seq/analysis` and `seq/prototype`, which are
already in this branch.
