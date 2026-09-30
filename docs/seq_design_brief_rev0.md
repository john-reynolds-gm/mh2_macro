# MH2 Grade Sequencing Tool — Design Handoff (rev 0, recovered from claude.ai chat)

Saved verbatim into the repo on 2026-09-30. Written before the gap-audit work
landed; several data-layer assumptions are superseded — see
`seq_orientation_rev1.md` for the reconciliation. Figures here are from the
10-ladder corpus and are historical.

---

## 1. Purpose

Writers need to build **grades** — organized into modules and/or topics — out of the existing stem ladders. This is not a top-to-bottom read of each stem. Building a grade means:

- pulling nodes from many stems into one grade's sequence
- ordering them into modules
- sometimes co-teaching two nodes from *different* stems in the same slot

The concrete pain point driving this: to do this work today, a writer opens several Word documents and cross-references them by hand.

**Root cause.** Ladders are organized *vertically* — one stem, all grades. Building a grade is *horizontal* — one grade, all stems. The writer is performing that transposition in their head. The tool's core job is to do the transpose for them.

**v1 is deliberately manual.** No LLM. Nail the representation and interaction first; automation layers on afterward.

---

## 2. Relationship to the standards tool

**Decided: reuse, do not fork.**

- Same node table, same content-hash node IDs, same `ingest_ladders.py` output, same SQLite DB.
- The sequencing tool adds new tables (`sequence`, `module`, `placement`) with foreign keys to existing `node_id`.
- Rationale: the parser work (including the comment-anchor heading bug) and the node-hash identity model are hard-won. A second parser against the same Word docs is pure drift risk.
- **Do not rewrite the standards tool.** It stays in Streamlit.

---

## 3. Stack decision

**React (frontend) + FastAPI (backend) + SQLite.**

Streamlit was ruled out for this tool specifically. The standards tool is queue-shaped — one item, a verdict, next item — which fits Streamlit's rerun model fine. This tool is browse-shaped: ~110 elements on screen, each individually clickable, with constantly changing state that must stay visible. Every click triggers a full script rerun, full repaint, and lost scroll/expander state. That is an execution-model problem, not a styling problem.

React chosen over HTMX or NiceGUI because Python is no longer a hard constraint, UX quality is the priority, and Claude Code is strongest on React by a wide margin.

**Critical mitigation — keep the frontend dumb:**

- All parsing, consistency checks, ordering rules, grade normalization, and DB access stay in **Python** behind a small API.
- React only renders and posts.
- When something breaks, it is far more likely to be in Python that John can read than in TSX he cannot.

---

## 4. Data model

### 4.1 Three-tier attribute scope

The single most important schema rule. Anything that can vary by grade lives on the placement, not the node.

| Scope | Holds | Examples |
|---|---|---|
| **Concept/skill** | Shared across the whole progression | `Goal` (confirmed by John: applies to all nodes in the C/S; do not surface per-node) |
| **Node** | Intrinsic to the content | Node text, standards, misconceptions, models, strategies |
| **Placement** | Anything that can differ by grade | Module, order index, calibration level, period estimate, scope/range note |

### 4.2 Placement

- A placement joins one `node_id` to one `(grade, module)` with an order index.
- **Grouping, not merging.** Co-placement groups 1+ nodes into one teaching slot. Nodes stay independent entities. Non-destructive and reversible. True composite authoring (writer-authored blend text with provenance) is deferred.

### 4.3 Ownership

**Node ownership and placement ownership are separate questions.** Separating them dissolves the conflict.

- **Nodes** are owned by the stem writer — unchanged from the standards tool model.
- **Placements** are owned by the grade builder.
- Because the tables are disjoint by role, two writers building G2 and G3 never write the same rows. Append-only merges keep working.

**Node reuse across grades is permitted and expected** — 70 of 137 node cells are multi-grade (see §6). A node placed in G2 M3 and G3 M1 is two independent placement rows, not a conflict.

Residual risk is *accidental* duplication — nobody notices a node was taught twice with no differentiation. Handle it as with shared CCSS codes: **surface, don't block.** When placing a node already placed in another grade, show where it's placed and prompt the writer to record what differs. That note lives on the placement and is exactly where the "same concept, different number range" case belongs.

### 4.4 Bridges (cross-grade modules)

No schema change needed. A bridge is simply a G2 module containing a G1-tagged node. Requirements:

- Filter the browser to the current grade **by default**, one click to widen.
- Badge off-grade nodes visibly in the sequence rail so a bridge is always deliberate.

Build in v1 — it's nearly free.

### 4.5 Known failure mode — orphaned placements

Node identity is a content hash. A stem writer rewording a node makes it a *new* node and orphans every placement referencing it, potentially **across several grades at once**. Larger blast radius than in the standards tool, where one reword orphaned one alignment set.

Needs an **orphaned-placement queue** showing affected grades and modules so a grade builder can re-attach rather than silently lose work.

---

## 5. Interface design

### 5.1 Grade slice (primary view)

Not a search-and-browse file picker. A **fixed inventory** of everything a grade owes.

- Group by super-stem → stem → concept/skill → nodes.
- **Show only concept/skills containing an in-grade node.** For G2 this collapses 62 concept/skills to 21.
- Each concept/skill renders as a **full progression strip** in ladder column order: in-grade nodes solid, adjacent-grade nodes dimmed as context.

Why the strip rather than a flat filtered list:

- Prerequisite context is visible without navigating — makes ordering warnings legible instead of mysterious.
- Bridges become one click on an already-visible dimmed node.
- Multi-grade nodes appear solid in each slice with no data duplication.
- Unmapped grades stay visible instead of silently vanishing (important while normalization is in flight).

### 5.2 View controls

Tiles should be **readable, not minimal**. Density is managed by user-controlled filtering, not by shrinking.

- Per-stem and per-concept/skill show/hide toggles.
- Drag to reorder stems.
- Toggles: context grades on/off, unplaced only, pairing links on/off.
- **Saved, named views per writer.** If someone spends ten minutes building a workable G2 layout, losing it on refresh means they stop doing it. Cheap now, awkward to retrofit.

### 5.3 Node drawer (read one node)

- Click a node → **non-modal drawer from the right**. Slice stays live and clickable behind it.
- Must **visually separate concept/skill-scoped content from node-specific content**, or writers will read a `Goal` as belonging to the one node they clicked.
- **Collapse empty attribute rows by default** (see fill rates in §6).

### 5.4 Comparison table (decide whether nodes belong together)

Rejected: stacked drawers. Panels scroll independently and nothing aligns, so the writer does the comparison in their head again — the Word-docs problem in smaller windows.

**Chosen: a synthetic ladder table.** The ladder is already nodes-as-columns, attributes-as-rows; comparison rebuilds that shape across stems.

- Renders as a **bottom sheet growing out of the compare tray** — not a new page, not a modal.
- The slice stays visible and **live** above it: clicking a fourth node in the slice adds a column without closing anything.
- Drawer enters from the right, comparison from the bottom — different edges, both can be open at once.
- **Cap at four columns** before horizontal scroll; past that it stops being scannable.
- **Encode selected node IDs in the URL** so a writer can paste a link saying "look at these two together." Trivial with React Router now, annoying later.
- Shared-standard rows self-highlight — the pairing evidence is visible in place.
- Primary action in the table: **"Co-place in a module."** The decision and the action are in the same place.

---

## 6. Empirical findings from the ladder documents

All figures from the **10 PK–5 ladders currently in the project**. The Stem Masterlist lists ~21 PK–5 stems, so a complete corpus is roughly double.

### Corpus size

- 137 node cells across 62 concept/skills.
- **G2 slice: 21 of 62 concept/skills, 31 in-grade nodes + 21 context nodes = 52 chips.** Full corpus ≈ 44 rows, ~110 chips.
- Concept/skills per stem vary enormously: Counting has 14 (only 4 touch G2), Base Ten has 6 (4 touch G2). No fixed per-stem layout will work.
- Strip widths are small — mostly 1–4, max 6. The horizontal strip holds; vertical count is what grows.

### Grade field is uncontrolled free text — **the blocking problem**

- **56 distinct values across 137 cells.** `G2`, `2`, `Grade 1`, `Kindergarten`, `PK/K`, `GK, G1` all coexist.
- Some cells aren't grades: `**Range** – PK - Within 10 GK – Within 19`, `GK Size of groups (small, medium, large)`.
- Comment anchors leak: `G2[^c18]`, `1, 2[^c20]`.
- **70 of 137 cells are multi-grade**; only 62 are single-grade; 5 parse to no grade at all.
- **20 cells are state-conditional.**

### Cross-stem linkage already exists as human-authored data

- **33 CCSS codes appear in 2+ different stems.** Examples: `2.NBT.A.1` in Counting and Base Ten; `1.OA.C.6` in Counting and two Addition/Subtraction concept/skills; `4.NBT.A.2` in Comparing and Ordering plus three Base Ten concept/skills.
  - **Decided (John): these are pairings to consider, not conflicts to resolve.** Badge as a link, not a warning.
- **38 hand-written cross-stem references in prose**, pattern: "Associated concepts from other stems: Whole Numbers and Base Ten Structure." Writers are already recording these links where nobody can query them. One parser turns them into a real table.

### Attribute rows — 15 exist per concept/skill, fill rates split sharply

| Fill | Rows |
|---|---|
| Always populated | Considerations (61/61), Additional Notes (61/61), Existing Product Reference (61/61), Knowledge Graph Node (58/58), Required Skills/Cases (58/58), Specifications (58/59), Goal (49/49) |
| Mostly | Misconceptions (55/61), Mathematical Models (47/61), Strategies (43/61) |
| Sparse | Leaves to Include (22/59), Terminology (13/53), **Student-Facing Example (10/59)** |

A fixed drawer layout would be roughly a third empty. Hence collapse-empty-by-default.

### Parsing issues to fix

- Label variants needing normalization: `Goal` / `Goals` / `Goal****s`, `Notes related to ****Standards****, Grade, or Leaf`, `Leaves ****to Include`.
- **A Base Ten concept/skill parses with an empty title while carrying 4 G2 nodes** — the comment-anchor heading bug, still live in this data. Use `ingest_ladders.full_text()`, not python-docx `.text`.
- Inline workflow markers embedded in node text: `(Megan—DONE)`, `(Stella- DONE)`.

### Pacing framework is a live constraint, not background reading

`Math_Pacing_Prioritization.docx` (v2.0, June 2026) assigns every target one of three levels — **Deep / Functional / Illuminating** (teacher-facing: Know it / Use it / See it) — with year-level guardrails of roughly **25/50/25 by target count** and **40/45/15 by instructional time**. The document is explicit that these are diagnostics, not quotas, and that time allocation is more meaningful than raw node counts.

Two consequences:

1. **Calibration level is placement-scoped, not node-scoped.** The document states directly that a calibration level is not a permanent property of a mathematical idea — the same target can carry different expectations depending on grain size and time horizon. Getting this wrong would be expensive to unwind.
2. **The module builder needs a live guardrail readout** — distribution by level and by estimated instructional periods as the writer places nodes. Period estimates already exist in the ladders (e.g. "G1 – likely 10 instructional periods"), so the time axis is partially populated today.

This is the highest-value non-LLM feature in the tool: it turns an invisible year-level judgment into a live number.

---

## 7. Next work item — typed grade normalization (blocking)

Nothing renders until this is done. Same shape as the 86-category → 54-stem mapping: **John owns it, writers review.**

Output is **not** a flat string→grade lookup. It must be typed, because the 70 multi-grade cells are three different phenomena:

| Type | Meaning | Real examples |
|---|---|---|
| **Span** | Genuinely taught across both grades | `PK, GK`, `1, 2`, `GK, G1` |
| **State-conditional** | One default grade + state overrides. **Not** a multi-grade node — this is the leaves concept the standards tool already models. 20 cells. | `4 (3 for some states)`, `6 (5 for FL, TX, OK)`, `4, 5 (G3 for TX and AR)`, `2 (possibly just leaf for Texas)` |
| **Range prose** | Could be either, or loose drafting — needs writer judgment | `K-5`, `1, 2, 3, 4`, `2-5 (G1 for FL and AR)` |

Proposed mapping schema, one row per distinct raw string:

```
raw_value | default_grades[] | state_overrides{} | span_type | needs_review
```

Controlled vocabulary: `PK, GK, G1, G2, G3, G4, G5`.

Notes:

- Strip comment anchors (`[^c\d+]`) before matching.
- Cells that parse to no grade (5 of them) need writer resolution, not a default.
- For v1, resolve to the **CCSS-default grade** and badge state exceptions rather than building state-switching into the slice.
- `node → grade` is **many-to-many** in the schema. Do not put grade as a column on `node`.

---

## 8. Open questions

1. Are `K-5` / `1, 2, 3, 4` genuine spans or loose drafting? Writer judgment required during normalization.
2. Should the 38 prose cross-references be parsed in v1 or v2? They are cheap and high-signal.
3. Hosting — still pending IT/digital team input. ~5–10 users, distributed copies for now. Same open question as the standards tool.
4. Do period estimates exist consistently enough across ladders to drive the guardrail readout, or is the time axis too sparse for v1?

---

## 9. Consistency checking — tiered

A writer specifically asked for cross-stem inconsistency detection, since many writers work on the project simultaneously. **v1 needs none of the expensive tiers.**

| Tier | Check | Cost |
|---|---|---|
| Structural | Unparseable grade; node placed before its ladder predecessor; unplaced at grade close | Free |
| Shared-code | Same CCSS code across stems; same code at conflicting grades | Free, 33 hits today |
| Declared link | Prose cross-references extracted to a real table | One parser, 38 hits |
| Textual | Near-duplicate node wording across stems | Cheap string work |
| Semantic | Terminology drift, conceptual overlap | LLM — defer |

Surface these as **badges inline in the slice plus a "Flagged" filter — not a separate report page.** The writer's complaint is about context-switching; a separate page reintroduces it.

Ordering warnings should **warn, not block** — writers will have legitimate reasons to invert a sequence.

---

## 10. v1 scope

1. Grade-slice inventory view with progression strips
2. View controls — stem/concept-skill show-hide, reorder, saved named views
3. Node drawer (non-modal, right) with scope separation and empty-row collapse
4. Comparison table (bottom sheet, max 4 columns, URL-encoded selection)
5. Module builder — ordered placements, co-placement grouping, remove
6. Calibration level set per placement + live guardrail readout
7. Structural and shared-code consistency badges
8. Cross-grade placement prompt with differentiation note

**Deferred:** true node merging with authored blend text; LLM synthesis and pairing suggestions; semantic inconsistency detection; state-switching in the slice; drag-and-drop reorder (chevrons are adequate until proven otherwise); EM2 lesson-overlap scoring.

---

## 11. Deferred automation — what's already available when v1 is solid

Do not build these yet. Logged so they aren't rediscovered.

- **EM2 lesson-overlap pairing.** `compute_lesson_overlap.py` already computes Jaccard similarity over shared `lesson_id` sets. `lesson_metadata.csv` (1,378 EM2 lessons) plus `all_states.csv` / `CCSS_alignment_guide.csv` link standards to lessons. So "two nodes point at the same EM2 lesson" is derivable **today from structured tags** — no need to scrape the free-text EM2 mentions in ladder prose (`EM2 G1 M2—Whiteboard Exchange: Commutative Property`).
- Shared-CCSS-code pairing (§6) is stronger and cheaper than model similarity, and it is already human-authored.

---

## 12. Model recommendation

- **Design and architecture decisions (this thread): Opus.**
- **Implementation in Claude Code: Sonnet.**
- Keep them strictly separate, as with the standards tool. Scope each Claude Code session to a single view. No full rebuilds without asking; checkpoint after each view.
