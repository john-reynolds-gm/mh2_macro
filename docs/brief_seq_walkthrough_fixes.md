# Brief: walkthrough quick fixes (first writer demo, 2026-10-01)

Status: agreed with John 2026-10-01. Not started.
Scope: frontend only (`seq_static/app.js`, `app.css`, `index.html`, plus the DemoApi
inside `app.js`). No schema, API or Python changes.
Independent of `docs/brief_seq_o8_o9_autofill.md`. Either can land first. If both are
in flight, the only shared code is `renderPlacement` / `renderPeriods`; merge with care.
Recommended model: Sonnet (Claude Code).

## What the writer said

> "My biggest stumble is how do I save what I've done so far and switch to building a
> different module or building for a different grade? I saved a view, I think, but I
> don't know what that really does for me and I don't know how to switch to something
> new and come back to this module later."

> "…I wish I could make the left panel larger while I work on adding the details…
> Even when I pull it onto my big screen I can only see 1 card at a time in the left
> panel. On my laptop screen I don't even see (and can't figure out how to get to) the
> left panel at all."

## Diagnosis

Nothing is missing functionally. **Every write is already saved** (`write()` in `app.js`
is the single choke point, and the server or DemoApi persists it). The UI never says so.
- **"Saved views"** stores filters and layout only. The writer took it for "save my
  work".
- **Switching module** depends on a "Place into this module" radio inside each module
  header in the rail. It's easy to miss, and invisible when the rail is hidden.
- **Below 1280 px** the rail is `display: none` behind a small "Sequence" button at
  the right end of the header (`app.css` `@media (max-width: 1279px)`).
- **Rail width** is fixed (380 px, or 340 px at 1280–1599), and each placement card
  shows text, calibration, periods, note and tools, so one card fills the viewport.

## Fixes

### A. Save status indicator

- Add a status element to the header row, next to the grade picker, with
  `aria-live="polite"`.
- It is driven by `write()`:
  - **Saving…** while `busy`.
  - **✓ All changes saved · 2:41 pm** on success (local time, `h:mm a`).
  - **Not saved: \<short reason\>**, styled as a warning, on error. The existing notice
    still shows the detail.
  - On 409, show **"Someone else changed this; reloaded"** once the view refreshes.
- In the demo the success text is **✓ Saved in this browser · 2:41 pm**.
- Before any write in the session: **All changes save automatically**, muted.
- Put formatting in a pure function (`saveStatusText(state, now, isDemo)`) so it can be
  tested in node.

### B. Rename "Saved views" to "Saved filters"

- Change the label only: the select placeholder, the "Save as…" dialog title,
  aria-labels and the toast text. Keep the API path, table name (`saved_view`) and JS
  identifiers unchanged.
- Add a tooltip and a one-line helper in the save dialog: **"Remembers which filters
  and stems are shown. Your sequence itself saves automatically."**

### C. "Adding to" module picker

- Add a header or toolbar control that's always visible, at every width: **Adding to:
  [M2 · Fractions intro ▾]**. The options are every module, plus **+ New module** at the
  end, which runs the existing `add-module` action.
- It is bound to `ui.targetModuleId`. Keep the rail's radio buttons in sync: both
  write the same state. The rail radio can stay.
- **"+ Place" on a chip** names the target in its tooltip and aria-label: "Place in
  M2 · Fractions intro".
- **Remember the target per grade.** Store `mh2seq-target:<grade>` in localStorage,
  wrapped in try/catch like the existing `mh2seq-user`, with silent fallback when
  storage is blocked. `loadGrade` → `fixTarget` restores it when the module still
  exists, and otherwise falls back to the current rule (last module).
- **Switching grades** is already the header grade dropdown. Add a tooltip: "Each grade
  has its own sequence. Your work in this grade is saved."
- After a successful place, the existing notice gains a link: **"Placed in M2 · Show
  in sequence"**. The link opens the rail (on narrow screens) and scrolls to the new
  placement.

### D. The sequence on laptop screens

- **1024–1279 px:** show the rail at 320 px instead of hiding it. With the drawer
  open, the drawer overlays rather than adding a third column.
- **Below 1024 px:** replace the small header button with a **two-tab switch at the top
  of the content area: "Nodes" | "Sequence (n)"**, where n is the number of active
  placements. It's full width, sticky, and can't be missed. The tab state lives in
  `ui.railOpen` (rename the internal name only if that's trivial).
- After placing from the Nodes tab, the "Show in sequence" link (C) switches tabs.
- Check with screenshots at **1440, 1280, 1100 and 800 px** wide, both with a sequence
  of 10+ placements and with an empty one. Attach them to the summary.

### E. Compact placement cards

- Default the rail to a **compact row** per placement, about 2 lines:
  `slot# · 0003 · STEM · first ~60 chars of text…`, then on the right a calibration
  letter (D/F/I), periods, and badge dots.
- Click or Enter on a row expands it to the full card (today's `renderPlacement`).
  More than one row can be open at a time.
- The rail header gets **Expand all / Collapse all**. Remember the choice in localStorage
  (`mh2seq-rail-density`), with the same try/catch rule as above.
- Chevrons, "Move to module", co-place and remove stay on the expanded card. In compact
  rows, **only the ▲ ▼ chevrons** stay visible, so reordering still works collapsed.
- Keyboard: rows are focusable, Enter toggles, and focus is kept across re-render
  (reuse `fk()`).
- **Target:** at least **8 compact rows visible** in a 900 px-tall window at 1440 px
  wide.

### F. Demo footer wording

The footer currently says "edits are kept in this browser". Make it **"Your edits are
saved in this browser on this computer only. Another browser or computer starts
fresh."**

## Out of scope (step 3, designed separately)

- A resizable rail divider, the full-screen "sandbox" builder, and drag-and-drop. The
  writer's "make the left panel larger" request is the main input to that design. Don't
  build a partial version here.
- Any change to saved-view semantics (for example, saving the target module in a view).

## Tests

- `tests/js/seq_app_test.js`:
  - `saveStatusText` for every state.
  - Target-module restore: stored id exists, stored id deleted, storage throws.
  - Compact or expanded row rendering: the compact row contains the node number and
    chevrons; Enter toggles.
  - Labels: no user-visible "Saved views" string remains (grep the rendered header).
- `tests/js/seq_e2e_test.js`: a status walk-through.
  1. Place, and the status shows saved.
  2. Change the target via the header picker; the rail radio agrees.
  3. Switch grade and back; the target is restored.
  4. A stale rev produces 409 and the reload status text.
- `tests/test_seq_frontend.py`: still green. No http URLs, one inline script in the
  bundle.
- Rebuild the three demos and run `node tests/js/demo_bundle_check.js` on each.

## Acceptance

Replay the writer's stumble in the G2 demo at 1280 × 800:

1. Start a sequence, add M1 and M2, and place 3 nodes into M1. The status says saved.
2. Switch "Adding to" to M2, place 2 nodes, and the notice links to them.
3. Switch to G4, then back to G2. M2 is still the target, and all 5 placements are there.
4. At 800 px wide, the "Sequence (5)" tab is visible without scrolling, and switching
   to it shows the 5 placements compactly.
5. At 1440 × 900, at least 8 compact rows are visible.

Report anything from this replay that still felt unclear. That feeds step 3.

## Working rules

- Don't commit, merge or push. Leave the changes in the working tree for John to
  review and commit. In a Cowork sandbox, run no git command that writes at all.
- Plain JS, no build step, no CDN (O6). Don't edit `review_api.py` or `DEFERRED.md`.
