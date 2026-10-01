# Sequencer v1: rulings sheet (rev 1, 2026-10-01)

One page per decision in `seq_v1_contract.md` §1.1–1.2. Canonical source for the
original questions: `seq_orientation_rev1.md` §12. Fill the **Ruling** column; once
done, the contract entries change from "Provisional" to "Ruled (John, date)".

**All items ruled 2026-10-01.** Code changes still pending: O8/O9 hint and autosave (see O9).

## Walkthrough note (step 2e browser check)

Node IDs overflowed chips on long stem IDs (for example `EE_ONE_VARIABLE_EQUATIONS_DEG_1-0003`). The stem prefix repeated the stem heading above the strip, so chips now show only the number (`0003`). The full ID is in the tooltip and screen-reader label. Fixed 2026-10-01 in `seq_static/app.js` (`chipId`) and `app.css`; demos rebuilt.

---

## The four hard ones

### O1 Ownership: **Ruled: accept** (2026-10-01)

- **Question:** One owner per grade sequence, or shared editing? Is there a sign-off step?
- **v1 behaviour:** `owner` is advisory. Anyone logged in can edit. Non-owners see a
  "You are editing *owner*'s sequence" banner. No sign-off or lock state. Every
  write is attributed to the logged-in user in `placement_event`.
- **Revisit if:** a sequence needs formal approval (a lead signs off a grade), or
  sign-off must happen in a Word document (orientation §2.5).

### O8 Time units: **Ruled: 1 lesson = 1 day = 1 period** (2026-10-01)

- **Question:** The 6–9 ladders give estimates in days or lessons, not instructional
  periods. Can they be converted?
- **John's convention:** one lesson is that day's math block. In K–5 that block is
  part of the school day; in 6–A1 it is the math period. So day, lesson and period
  are the same unit, 1:1.
- **Change this triggers (Sonnet session):** data model §5.4 rule 3 stops treating
  `unit = day/lesson` as text only. An exact day/lesson estimate becomes a prefill
  hint at the same number ("1/2 lesson" → 0.5). It is still never saved without the
  builder confirming it. Update `seq_read` hint logic and its tests, the contract
  §1.1 O8 row, and the "No conversion" wording in the README.
- **Effect:** better time hints for Equations, Integers, Probability and Coordinate
  System. Strict per-grade coverage in 6 / 7 / 8 / A1 rises to about 38 / 20 / 17 / 15 %
  (`seq_analysis_rev1.md` §1.2). Grades 7–A1 stay low because of O9 and missing data
  (Inequalities, Irrational), not units.

### O9 Estimate on a node that spans grades: **Ruled: option B, per grade** (2026-10-01)

**John's ruling:**

- **Multi-grade, un-prefixed estimate:** prefill the same number in every grade the
  node is tagged to. "2 days" on a G1+G2 node → 2 in G1 and 2 in G2. The writers may not
  have meant per grade, but they are detail-oriented, will reconcile as they work,
  and the number is easy to change. A prefilled number beats a blank box.
- **"Part of N" (≤ N):** prefill N − 0.5. "Part of 1" → 0.5, "part of 2" → 1.5. Corpus:
  27 rows "part of 1", 4 rows "part of 2". The 7 `part_of` rows with no bound are group
  totals ("2+ days for the first 6 nodes altogether") and stay blank.
- **Still blank:** `multiple`, `not_fixed` (fluency), `embedded`, `unparsed`, group totals.
- **Change this triggers (Sonnet session, together with O8):** data model §5.4 rules 1–3
  and `seq_read`'s hint logic plus tests. Contract §1.1 O9 row. Orientation §12 note.

Original analysis, kept for the record:

**The problem in one example.** Node X appears in grades 1 *and* 2. Its ladder note
says "Likely 2 instructional periods", with no "G1:"/"G2:" prefix. Building the
G2 sequence, how many periods does X get?

- **Per grade:** 2 periods in G1 and 2 more in G2 (4 total across the two years).
- **Total:** 2 periods across both grades, so perhaps 1 in each.

The ladder doesn't say which. It matters because the guardrail sums placement
estimates to get the 40/45/15 time split. Guess "per grade" when the writer meant
"total" and that node's time doubles.

This is common. Most PK–5 nodes have an estimate, but in PK and K most of them sit in
two grades with one un-prefixed figure. That's why strict per-grade coverage is only
29 % (PK) and 48 % (K), against 90 %+ "some estimate exists".

**Second part: "part of".** Some notes say "part of 1 period", meaning the idea is
taught inside another node's period. Counting it as a whole period double-counts.
v1 shows it as "≤ 1" and leaves the number blank.

| Option | What the builder sees | Risk |
|---|---|---|
| **A. Accept (rec.)** | The note as text; the period box is blank; the builder types a number | More typing; time coverage stays low until builders fill it in |
| B. Assume per grade | The box is prefilled with the same number in every grade (still needs confirming) | Overcounts if writers meant "total" |
| C. Assume total, split evenly | Prefill = number ÷ grades | Undercounts if writers meant "per grade"; produces fractions |

**Why A:** the tool shouldn't guess what the writer meant. The builder knows the
grade, and the "N of M counted" caveat keeps gaps visible. **Choose B instead** if
you know the stem writers meant "per grade" when they left off the prefix.

- **Ruling:** B, with "part of N" → N − 0.5 (see above).
- **Ranges and open-ended (ruled 2026-10-01):** prefill the low end. "1-2" → 1,
  "2-3" → 2, "1+" → 1.
- **Saving (ruled 2026-10-01):** the ladder value is **saved on placement**, not just
  shown in the box. This reverses data model §5.4 ("written only when the builder
  saves it") and build plan S8 check 3 ("`period_estimate` stays NULL until the
  builder saves it"). Implementation notes for the Sonnet session:
  - Add `placement.estimate_source TEXT CHECK (estimate_source IN ('ladder','builder'))`
    to `mh2/schema_placement.sql`, an additive column. Set it to `'ladder'` on create
    when a hint exists, and to `'builder'` on any PATCH of `period_estimate`, including
    a builder accepting the same number.
  - `period_hint_seen` keeps the ladder text on create. The create `placement_event`
    records the autofilled value.
  - The drawer and builder show a "from ladder" marker until `estimate_source = 'builder'`.
  - The guardrail counts ladder-sourced estimates. Optionally, it can show
    "N from ladder, M confirmed" next to "N of M counted".
  - Re-placing a removed node autofills again only if it has no builder-sourced value.

### O10 Flagged filter vs Pairings toggle: **Ruled: accept (option A)** (2026-10-01)

**What badges are.** The slice marks nodes with small badges. Some mark **problems**,
such as a node placed before its ladder predecessor, or placed in a grade it doesn't
belong to. The **Flagged** filter is meant to show only nodes with a problem, as a
to-do list.

**What a shared-code badge is.** It marks a node that shares a CCSS code with a node in
a *different* stem. Example: in G2, Counting and Whole Numbers share codes on 6 node
pairs. That isn't a problem. It's a **pairing opportunity**: the builder may want those
nodes in the same module, or near each other.

**Why it can't go in Flagged.** 22 of the 35 G2 nodes carry a shared-code badge. If those
counted as flags, the Flagged filter would show nearly the whole grade and stop
working as a to-do list. Narrowing to partners in the same grade barely helps (20 of 35).

| Option | Behaviour | Trade-off |
|---|---|---|
| **A. Accept (rec.)** | Flagged = problems only. A separate **Pairings** toggle shows shared-code badges, grouped by partner stem | Two controls instead of one |
| B. Fold into Flagged | One filter for everything | Flagged shows about 2/3 of the grade; useless as a to-do list |
| C. Drop the badge | No pairing signal in v1 | Loses the main cross-stem hint the brief asked for |

- **Ruling:** A.

---

## Everything else in §1.1: **Ruled: accept as a group** (2026-10-01)

| # | v1 decision (short) | Ruling |
|---|---|---|
| O2 | Modules only; a slot label covers an informal "Topic A" | Accept |
| O3 | Same per-writer logins as the audit app; no roles | Accept |
| O4 | DEFERRED §6 amendment not applied by the code work; John pastes it (orientation §2.4) before pilot | **Applied 2026-10-01** (DEFERRED §6 exception + §8 R-H3 note) |
| O5 | `student_facing_example` deferred (not stored in `mh2.db`) | Accept |
| O6 | Plain JS, no build step (already accepted provisionally) | Accept |
| O7 | 11 in-grade rows with no kind: badge "kind pending", counted as owed | Accept |
| O11 | Stated-link capture fix not done; links shown "as captured" | Accept |
| O12 | Concept/skill band shows Goal only | Accept |
| O13 | Partly-filled goal: show the one goal; no goal: "No goal in ladder" | Accept |
| Gate B | Passed without a walkthrough. **Recheck at step 5's walkthrough:** stems collapse above 100 chips; strips wrap rather than scroll | Accept |

## §1.2 assumptions: **Ruled: accept as a group** (2026-10-01)

| Item | v1 decision | Ruling |
|---|---|---|
| Placements per node | One active placement per node per sequence (same-year revisits later) | Accept |
| Estimate scope | Period estimate per placement, not per slot | Accept |
| Super-stem | `stems.domain` | Accept |
| `grade_changed` | Silent for unknown→core/span/unconfirmed and unconfirmed→core/span; everything else raises | Accept |
| Time-mark threshold | 40/45/15 marks drawn only when ≥ 50 % of placements have an estimate | Accept |
| Sequences per grade | One active sequence per grade (API refuses a second) | Accept |
| Owed-and-unplaced | A filter ("Unplaced only"), not a badge | Accept |
| `predecessor_unplaced` | Info, not a problem flag | Accept |
| Chevrons at module edges | Up from first slot → end of previous module; down from last → start of next | Accept |
