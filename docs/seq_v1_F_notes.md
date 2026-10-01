# Sequencer v1 frontend (Implementer F): notes

Branch `seq/v1-F`. Files: `seq_static/{index.html,app.js,app.css}`, `scripts/export_seq_demo.py`,
`tests/js/seq_app_test.js` (+ `demo_bundle_check.js`, `fixtures/`), `tests/test_seq_frontend.py`,
`docs/seq_demo/seq_demo_g2.html`, this file. Nothing else was touched.

## Try it

- Demo, no server: double-click `docs/seq_demo/seq_demo_g2.html`. It is built from a fixture
  (footer says "demo data built from fixture"). The integrator regenerates it from `mh2.db`:
  `python scripts/export_seq_demo.py --grade 2 --out docs/seq_demo/seq_demo_g2.html`
  (footer then says "from mh2.db"). `--fixture <json>` builds from a ready payload instead.
- Real server (after W merges): `uvicorn seq_api:app`, open `/seq/`.
- Tests: `node tests/js/seq_app_test.js` (38 tests); `python3 tests/test_seq_frontend.py`.
- Fixture generator (my stand-in for R, kept so the fixture is reproducible):
  `MH2_DATA_DIR=... python3 tests/js/fixtures/gen_fixture_from_db.py --grade 2 --out tests/js/fixtures/g2_payload.json`.
  Its G2 counts match contract 5.1/5.2 exactly (35/33/33/2/24/22/22/7/57; all 11 grades' owed and chips match 5.1).

## Screens and interactions

1. **Header.** Grade picker ("G2 · 33 owed · 4 placed"), ladder/kind stamp, "Signed in as", a
   "Your name" box when auth is off (stored in `localStorage["mh2seq-user"]`, sent as `X-MH2-User`),
   Demo badge + Reset demo. Under 1280 px a "Sequence" button shows the rail as an overlay.
2. **Toolbar.** Context / Leaves / State ext / Unplaced only / Pairings / Flagged toggles
   (`aria-pressed`), a Stems menu (show/hide stems and concept/skills, expand all / collapse all),
   saved views (select + Save as / Rename / Delete, inline, no `prompt()`), Reset view, and
   "shown n of N chips". Every lens setting is in the URL (contract 5.9) via `history.replaceState`.
3. **Slice.** Super-stem sticky headings; stems with "n in grade · m owed unplaced", move up/down,
   collapse, hide; concept/skill strips with a 2-line clamped goal (more/less) or italic "No goal in
   ladder"; chips 220 x >=84 px that wrap, joined by arrows. Stems start collapsed when `chips > 100`
   (G6, G7). Chip = body button (opens drawer) + "+ Place" + "compare" toggle (disabled at 4).
4. **Drawer** (right, non-modal, third grid column at >=1280 px so no chip is covered, overlay below):
   concept/skill band (goal only), node band (kind, grade pills, "Grade as written", ruling detail when
   unconfirmed), placement box (place into module / "In this sequence: M2 · slot 1" + Show in rail),
   also-placed-in, progression mini-map with prev/next, period hint (verbatim), node fields with
   "Show all fields (n empty)", CCSS with shared marker, State codes (collapsed), pairings and shared
   EM2 lessons (partner nodes are links when in this slice), stated links "as captured" (a chip whose
   text is a stem name un-hides, expands and scrolls to that stem), product refs.
5. **Compare.** Tray ("Compare 2/4 · Open · Clear") and a bottom sheet: sticky row labels and column
   header, shared rows highlighted with a "shared" tag, flag cells as a check mark, "Hide empty rows"
   (on) and "Shared rows first" (off), resizable (drag the grip or use arrow keys), remove per column,
   and the primary **Co-place in a module** (-> `createSlotGroup`; already-placed keys are listed as
   skipped; bridge / cross-grade prompts come first).
6. **Rail.** Start-the-sequence button; sticky **Balance** panel (Sequence | This module tabs; counts bar
   and time bar with reference ticks, time ticks only when `show_time_targets`, "Reference marks, not
   quotas", teacher labels Know it / Use it / See it); **Needs attention** (orphan: was/now suggestions with
   reason and similarity + Re-attach; grade_changed: Keep (acknowledge); both: Remove); module cards
   (rename by Edit or double-click, up/down, "Place into this module" radio, D/F/I summary, Remove when
   empty, + Add module); slots (label, up/down crossing module edges, Move to module, Merge into slot
   above); placement rows (badges, status chip, segmented calibration, periods input, hint + "Use n" or the
   reason it cannot be used, note editor, Ungroup, Co-place with..., Remove with a second-click confirm,
   Open in drawer); the place prompt (cross-grade note and/or bridge confirm) above the target module;
   History (50 events, More); "Download CSV".
7. **Errors.** 409 `stale_revision`: the returned SequenceView replaces the view and the polite notice
   bar says "Someone else changed this sequence. Your change was not applied, and you are now seeing the
   latest." (no retry). Other errors are toasts (`role="alert"`). Successful writes announce in the notice bar.
8. **Keyboard.** Every control is a native element. Alt+Up/Down on a focused slot or module card = the
   chevrons; focus stays on the moved item (`data-focus-key`). Esc closes, in order: inline edit, view
   form, place prompt, Stems menu, drawer, sheet. Enter commits inline edits.

## Wired vs placeholder

Wired (all through the same `act(name, data)` table, in demo and HTTP modes): everything above.
In HTTP mode none of it has been run against the real server (W is not in this clone); HttpApi is
covered by request-building tests against a fake `fetch`.

Not in the UI (API methods exist and are tested): `place` with `slot_id` / `after_slot_id` (co-place and
insert-after are done afterwards with "Co-place with..." and the chevrons), `updateSequence` owner/archive.
Orphan re-attach offers only the server's suggestions (the demo never has any; there is no manual
"pick a node" control because the spec has none).
DemoApi degradations (contract 6.2): no `before_predecessor` / `predecessor_unplaced` badges (footnote
"Ordering warnings need the server"), no successor suggestions, `relabelled` never set; status stays "ok"
except for placements seeded from a server sequence.

## Contract ambiguities and choices

1. `encodeState(state, slice)` does not write `grade` (so "defaults write an empty query" holds). The page
   uses `encodeUrlQuery(grade, state, slice)` and `decodeGrade(query)`.
2. DemoPayload gets one additive key, `source` ("fixture" | "mh2.db"), used only for the footer.
   Nothing else reads it. The integrator's round-trip should ignore it.
3. Expected revisions: every placement action except the attribute PATCH (calibration / periods / note) sends
   the **sequence** rev (W13-W17); only `updatePlacement` sends the placement rev (contract section 4).
4. `SequenceView.sequence` carries exactly the contract's keys (no `updated_at`); the demo keeps
   `updated_at` internally for `GradeInfo.sequence`.
5. In a placement row, a status chip ("orphaned" / "grade changed") stands in for the orphaned and
   grade_changed badges so they are not shown twice; all other badges show as returned.
6. The shared guardrail table lives in W's files, which I do not have, so the six cases of 5.8 are in
   `tests/js/fixtures/guardrail_cases.json` (same numbers as the contract).
7. Extras beyond the spec, all small: two-step Remove on placements, "Co-place with..." select,
   Expand/Collapse all stems, Download CSV (pure `sequenceToCsv`, formula-safe), sheet resize grip,
   flash highlight of newly placed rows, `Enter` commits inline edits.
8. Inline edits (module title, slot label, sequence title) save on change or Enter. Leaving a field
   unchanged keeps the editor open until Esc; I did not re-render on blur because swapping the DOM
   between mousedown and mouseup drops the click (the controller also defers any re-render while a mouse
   button is down).
9. JS file is ASCII-only (non-ASCII characters are `\u` escapes) so the wrong charset header cannot corrupt it.

## Known visual risks (no browser was available; layout/CSS never seen rendered)

- Three-column grid at 1280 px: rail 380 + drawer 420 leaves ~480 px for chips (2 per row). Check it.
- Sticky stack: toolbar/header (z 6), rail Balance panel (z 4), super-stem headings (z 3), drawer header
  (z 2) - overlaps unverified, especially the Stems menu panel (z 20) over sticky headings.
- `-webkit-line-clamp` (goal, chip text, placement text) relies on WebKit-compatible support; fine in
  Chrome/Safari/Firefox current, unchecked here.
- Chip heights differ per strip row (min 84 px, content-driven); wrapped strips may look ragged.
- Compare sheet: `min(var(--sheet-h), 80vh)` height and sticky corner cell over sticky header.
- Balance bar ticks are positioned with percentage `left`; check alignment with segment ends.
- Rail placement rows are dense (segmented control + periods + hint + note + tools in ~340 px);
  the Alt-arrow hint lives in `aria-label` and tooltips only.
- Contrast was chosen by calculation (text on tinted backgrounds >= 4.5:1), not measured.
- Drag-resize of the sheet uses pointer events; untested outside the keyboard path.
