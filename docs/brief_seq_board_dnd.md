# Brief: builder layout, the board, drag-and-drop, and undo (step 3)

Status: agreed with John 2026-10-08. Not started.
Builds on: `3a0b47a` (O8/O9 autofill plus the walkthrough quick fixes: save status,
"Adding to" picker, Nodes/Sequence tabs, compact rows).
Decisions (John, 2026-10-08):
- The sandbox works on the **real sequence**, with **Undo**. There are no scratch
  copies; option B is deferred.
- The sandbox layout is a **board of module columns**.
- Drag-and-drop is built in **plain JS** (O6 stands). No library, no CDN, nothing
  vendored.

## Why

From the first writer walkthrough (2026-10-01): the writer wanted the sequence panel
bigger while adding details and shuffling, and the slice back when they needed more
nodes. On a big screen they saw one card at a time; on a laptop they couldn't find the
panel. The quick fixes covered the laptop and card density. This brief covers the
rest:
- a resizable panel,
- a full-screen board for tinkering,
- drag-and-drop everywhere,
- an Undo that makes tinkering safe.

## Phasing

The work splits into three sessions. Each one leaves the app shippable, with tests
green.

| Phase | Scope | Model |
|---|---|---|
| **1** | Backend positional moves (§2) and the pure undo logic (§6.1), with tests. No visible UI change. | Sonnet |
| **2** | Resizable divider (§3), the board view (§4), and the Undo button (§6.2) for today's button, chevron and select actions. | Sonnet |
| **3** | Drag-and-drop in list, board and slice (§5). Drags push onto the same undo stack. | Opus. The interaction state is the riskiest part. |

## 1. Today, for reference

- **Layout.** `.main` is a CSS grid. The rail is 380 px, or 340 px at 1280–1599 px
  wide and 320 px at 1024–1279 px. Below 1024 px it shows as Nodes/Sequence tabs
  (`renderTabs`). The drawer is a third column.
- **Rail.** `renderRail` → `renderModule` → `renderSlot` (expanded) or
  `renderCompactSlot`. The density toggle is `ui.railDensity`.
- **Writes.** All go through `write()` / `seqWrite()`, using the sequence `rev` for
  layout operations and the placement `rev` for attributes. The server returns the
  full `SequenceView`, and the client re-renders regions with `put()`.
- **Render guard.** `pointerDown` defers re-render for up to 1.5 s so a click isn't
  lost when the DOM is swapped. A drag lasts longer than that. §5.6 replaces this guard.
- **Move API.**
  - W5 moves a module by `direction` only.
  - W8 moves a slot by `direction`, or by `to_module_id`, which appends at the end.
  - W9 merges slots.
  - W11 places, with `after_slot_id` or `slot_id`.
  - W13 co-places; when the source slot empties, it is dropped and its label lost.
  - W14 ungroups to a new slot right after the original.
  - The event action `'move'` already exists in the schema, unused. Use it in §2;
    no migration is needed.

## 2. Backend: positional moves (phase 1)

Drag-and-drop needs "put this exactly there". Use a **0-based `to_index`** among the
target container's active siblings, counted **excluding the item being moved**. The
client computes it from the `SequenceView` it holds. The sequence `rev` check makes
a stale index a 409, not a misplacement.

| Route | Change |
|---|---|
| W5 `POST /modules/{id}/move` | Accept `{expected_rev, to_index}` as an alternative to `direction`. Exactly one is allowed. |
| W8 `POST /slots/{id}/move` | Accept `{expected_rev, to_module_id, to_index}`. `to_module_id` without `to_index` keeps today's append-at-end. `direction` is unchanged. |
| W11 `POST /placements` | Accept `to_index` as an alternative to `after_slot_id`. It creates the new slot at that index. |
| **W18 (new)** `POST /placements/{id}/move` | `{expected_rev, to_module_id, to_index}`. Moves one placement into a **new slot** at that position. If the source slot is left empty, it is dropped. Logs action `'move'` with before `{slot_id, module_id, slot_label}` and after `{slot_id, module_id, to_index}`. Returns `{slot_id}`. |
| W13 co-place, W9 merge | The event `before` gains `slot_label` and the source slot's `to_index`, when a slot is dropped, so undo can rebuild it. |

Implementation notes:
- In `seq_store`, translate `to_index` to `_alloc_key(where="start")` or
  `("after", sibling[to_index-1])`.
- Validate `0 ≤ to_index ≤ n`; anything else is 422 `invalid`.
- A move to its current position is a no-op that still returns 200. It doesn't bump
  `rev` or log an event.
- Contract §4 and §3.5 get the new shapes.
- DemoApi mirrors all of this.
- Tests go in `test_seq_store`, `test_seq_service`, `test_seq_api_routes`, and the JS
  DemoApi parity tests. Cover:
  - to the start, the middle and the end;
  - across modules;
  - the no-op case;
  - an out-of-range index;
  - a stale rev;
  - a source slot dropped and its label kept in the event.

## 3. Resizable divider, list mode (phase 2)

- Put a vertical handle between the rail and the slice when the window is at least
  1024 px wide. It is `role="separator"`, `aria-orientation="vertical"`, with
  `aria-valuenow` set to the width in px, and it is focusable.
- Pointer drag resizes. ←/→ change the width by 20 px; Home and End jump to min and
  max. A double-click resets to the default for that breakpoint.
- Min 300 px. Max is the main width minus 360 px, so the slice never goes below 360.
  Store the width in `mh2seq-rail-width` (try/catch, same as the other `mh2seq-*` keys).
- Drive it with a CSS custom property (`--rail-w`) on `.main`. The breakpoint rules
  use `var(--rail-w, <default>)`.
- When the drawer is open and slice plus drawer don't fit, the drawer **overlays** on
  the right instead of squeezing the slice.
- No re-render during a resize. Set only the CSS variable.

## 4. The board (phase 2)

**Entering and leaving.** An **"Open board ⤢"** button sits in the rail header and in
the Sequence tab. On the board, **"← Back to list"** and Esc (when nothing else is open)
exit. Add the board state to the URL view state as `bd=1`, so reload and back/forward
keep it. Saved filters may store it. Below 1024 px the board button is hidden; the
list and tabs remain.

**Layout:**

```
┌ header: grade · Adding to · save status · Undo ─────────────────────────────┐
│ ┌ Nodes ┐ ┌ M1 Counting ┐ ┌ M2 Fractions ┐ ┌ M3 … ┐ ┌ + Module ┐  →scroll   │
│ │filters│ │ ▮▮▯ 40/45/15 │ │ ▮▯▯         │ │      │ │          │           │
│ │search │ │ 1 NS-…0003  │ │ 1 NS-…0010  │ │      │ │          │           │
│ │chip   │ │ 2 NS-…0004  │ │ 2 …         │ │      │ │          │           │
│ │chip   │ │   + CO-0002 │ │             │ │      │ │          │           │
│ └───────┘ └─────────────┘ └─────────────┘ └──────┘ └──────────┘           │
└──────────────────────────────────────────────────────────────────────────────┘
```

- **Node picker** on the left, 300 px and collapsible (`◀`). Collapsed by default under
  1440 px wide. It holds:
  - a search box that matches node ID or text;
  - a stem filter (reuse the toolbar filters' state);
  - an **Unplaced only** toggle, on by default;
  - compact chips with "+ Place" (to the "Adding to" module).
  Clicking a chip opens the drawer as an overlay.
- **Module columns**, 280 px each, in a horizontally scrolling area. Each has:
  - a header with the title (inline-editable, same as the rail), the position, ‹ ›
    buttons that call the W5 direction moves, the module's mini guardrail (count and
    time bars) and the "Adding to" radio;
  - a body of compact slot rows, scrolling vertically inside the column.
  Co-placed slots render as one bordered group. A row expands in place on click or
  Enter (same as `renderCompactSlot` → `renderSlot`).
- **"+ Module" column** at the end; it runs the existing `add-module` action.
- **The drawer** overlays the board on the right and doesn't take a column.
- **Target sizes:**
  - 1920 × 1080: picker plus 5 columns visible, with at least 12 rows per column.
  - 1440 × 900: picker collapsed, plus 4 columns.
  - 1280 × 800: 4 columns, picker collapsed.
- **Reuse** `renderModule`, `renderSlot`, `renderCompactSlot` and their actions. The
  board is a different container, not a second copy of the row code. Wherever a
  render function branches on board versus list, take a `ctx.board` flag.
- Before drag-and-drop exists (end of phase 2), everything on the board works with
  the existing chevrons, "Move to module" and "Co-place with…" selects.

## 5. Drag-and-drop (phase 3)

### 5.1 Mechanism

Use **Pointer Events** (`pointerdown`/`move`/`up`, `setPointerCapture`), not the
HTML5 drag API. One code path covers mouse, pen and touch, and the drag image is
under our control.
- A drag starts after 5 px of movement, so clicks still work.
- On touch, a drag starts after a 300 ms long-press, so scrolling still works.
- The dragged item follows the pointer as a lightweight clone of its compact row. The
  source dims in place.
- Esc or a `pointercancel` aborts the drag with no write.
- Auto-scroll when the pointer is within 40 px of the edge of any scroll container:
  board horizontal, column vertical, rail vertical.

### 5.2 What can be dragged, and where it can drop

| Drag source | Drop between slots (insertion line) | Drop onto a slot (highlight) | Drop onto a module header or empty column |
|---|---|---|---|
| **Node chip** (slice or picker, unplaced) | Place a new slot there (W11 `to_index`) | Co-place (W11 `slot_id`) | Place at the end of that module |
| **Node chip, already placed** | Moves its existing placement (W18) | Co-place its placement (W13) | W18 at the end |
| **Slot** (grip on the row) | Move the slot (W8 `to_module_id` + `to_index`) | Merge (W9) | W8 at the end |
| **Placement inside a co-placed slot** | Move it out to a new slot (W18) | Co-place into that slot (W13) | W18 at the end |
| **Module** (column header, or header in list mode) | Reorder modules (W5 `to_index`) | none | none |

- **Hit zones.** Over a slot row, the top 25 % or bottom 25 % means insert before or
  after; the middle 50 % means co-place or merge.
- **Labels.** Show a text label on the drop indicator, for example "Insert as slot 3
  in M2" or "Co-place with slot 3", so the result is never a surprise.
- **Off-grade or bridge chips** go through the existing confirm prompt after the
  drop, the same as "+ Place" does now.
- **No drag-to-remove.** Remove stays a button with its confirmation.
- **Drag works in all three containers:** list rail, board columns and slice. Slice to
  rail works in list mode; picker to column works on the board.

### 5.3 Keyboard and accessibility

- Every drag has a non-drag equivalent that already exists: chevrons, "Move to
  module…", "Co-place with…", "+ Place" and the module ‹ ›. Keep all of them.
- After each drop, an `aria-live` announcement says what happened: "Moved NS-FRAC-0012
  to Module 2, slot 3."
- Drag handles have `aria-label`s, and the grip isn't the only focusable element in a
  row.

### 5.4 Saving

- On drop, show the item at its new position immediately with a "saving" style. Then
  call the write.
- On success, render the server's `SequenceView`, as every write does now.
- On error, put the layout back as it was, keep the error notice and the save-status
  text, and leave the undo stack untouched.
- On 409, reload the view and **clear the undo stack** (see §6), with the toast
  "The sequence changed elsewhere, so undo history was cleared."

### 5.5 Mixed with the drawer and compare

- Dragging doesn't open the drawer.
- Dropping a chip that is in the compare tray leaves the tray as it was.

### 5.6 Render guard

Replace the 1.5 s `pointerDown` heuristic with an explicit `ui.dragging` flag. While
dragging:
- region re-renders are deferred, and one runs on drop or cancel;
- the existing click-guard behaviour stays for plain clicks.

## 6. Undo

### 6.1 Logic (phase 1, pure and tested in node)

- `inverseOf(op, viewBefore)` returns a list of primitive write calls that restore the
  layout from before `op`. It needs no network and no DOM, and lives in the pure-logic
  section of `app.js`.
- **Undoable:** place, slot move, module move, placement move (W18), co-place, ungroup
  and merge. The table below lists each inverse.
- **Not undoable in v1:** remove (its confirm text gains "This can't be undone"),
  attribute edits (calibration, periods, note), module create, rename and remove, and
  reattach or acknowledge.

| Op | Inverse |
|---|---|
| place (new slot) | remove that placement |
| place (co-place into slot) | remove that placement |
| slot move | W8 back to its old module and index |
| module move | W5 `to_index` back to its old index |
| placement move (W18) | If its old slot still exists, W13 into it. Otherwise W18 to the old module and index, then W7 to restore the old slot label. |
| co-place | Same as above, based on the old slot |
| ungroup | W13 back into the original slot |
| merge (k placements) | 1. W18 the first moved placement to the source slot's old module and index. 2. W13 the rest into that new slot. 3. W7 to restore the label. |

### 6.2 UI (phase 2 wires the button to the existing actions; phase 3 adds drags)

- An **"↶ Undo"** button sits in the header, next to the save status. Its tooltip names
  the action, for example "Undo: move slot 3 to M2".
- **Ctrl+Z / Cmd+Z** triggers it, except when focus is in a text input, a textarea or a
  select.
- The stack holds at most 30 entries. It is scoped to the session and the grade, and
  cleared on grade switch, reload or 409.
- A multi-step inverse runs as consecutive writes. If a step fails, reload, clear the
  stack, and show the toast "Couldn't fully undo; the sequence has been reloaded."
- Undo writes are ordinary writes. They appear in the history panel as normal events
  (move, co_place, and so on). No server-side undo concept is needed.
- No redo in v1.

## 7. Tests

- **Phase 1:** the backend tests in §2. In `tests/js/seq_app_test.js`, cover
  `inverseOf` for every row of the §6.1 table. Edge cases:
  - a co-place that dropped a labelled source slot;
  - a 3-placement merge;
  - a move to index 0;
  - a move into an empty module.
  Each case must restore the exact slot order, the slot membership and the labels
  (compare the normalised view).
- **Phase 2:**
  - divider clamping and storage that throws;
  - `bd=1` round-trips in `encodeState`/`decodeState`;
  - the board renders the same rows as the list for a fixture sequence;
  - the picker filter;
  - Undo through the buttons.
- **Phase 3:**
  - a pure `dropTarget(pointerY, rowRect)` gives the before, co-place and after zones;
  - `toIndex` excludes the dragged item;
  - e2e: simulate pointer sequences through DemoApi for each row of the §5.2 table,
    plus Esc cancel and 409 during a drop.
- **Every phase:** full `pytest -q`; the JS suites; rebuild the three demos and run
  `demo_bundle_check.js` on each; `test_seq_frontend.py` still green, with one inline
  script in the bundle.
- **Screenshots** with each phase's summary:
  - list mode at 1440 and 1280, with the divider at its default and at its max;
  - the board at 1920, 1440 and 1280 with a G2 sequence of 3 modules and 15 or more
    placements;
  - mid-drag frames for an insertion line and for a co-place highlight.

## 8. Acceptance (end of phase 3, G2 demo at 1440 × 900)

1. Open the board. Four module columns are visible, and the picker expands with ◀.
2. Drag 5 unplaced chips from the picker into M1 and M2, at specific positions. The
   positions match the drop labels.
3. Drag one chip onto a slot. It co-places.
4. Drag that co-placed node back out. It gets its own slot where it was dropped.
5. Drag a slot from M1 to the top of M2. Drag M3's header to first position.
6. Press Undo 4 times. The layout matches the state after step 2 exactly: slot order,
   membership and labels.
7. Reload. The board reopens with the same layout. The undo stack is empty.
8. Back to list mode: widen the rail to 600 px, reload, and the width is kept. Drag
   a chip from the slice into the rail and it places.
9. Keyboard only: reorder a slot with the chevrons and undo with Cmd+Z.

Report anything that felt unclear or fiddly. That feeds the next writer walkthrough.

## Out of scope

- Scratch or what-if sequences (option B), redo, and undo of removals or attribute edits.
- Multi-select drag, cross-grade drag, and drag-to-remove.
- The board below 1024 px wide, or board-specific touch layouts (touch drag on list
  mode is in scope).
- Any change to the guardrail math, badges or reconcile.
- Splitting `app.js` into several files. Keep one classic script: the demo bundler and
  `demo_bundle_check.js` expect exactly one inline script. Give the board, drag-and-drop
  and undo code a clearly marked section each.

## Working rules

- Don't commit, merge or push. Leave the changes in the working tree for John to
  review and commit. In a Cowork sandbox, run no git command that writes at all.
- Plain JS, no build step, no CDN, nothing vendored (O6). Don't edit `review_api.py`,
  `mh2/ingest_*.py` or `DEFERRED.md`.
- Rules live in Python (R-S10): the guardrail and badge logic stay server-side.
  `inverseOf`, `dropTarget` and `toIndex` are UI mechanics, so they can live in JS.
