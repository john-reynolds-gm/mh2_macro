# Grade Sequencing Tool: paper prototype (read-only)

A static HTML mock of the primary view from `docs/seq_design_brief_rev0.md` section 5,
generated from the real `mh2.db`. Nothing saves, places, or writes. It exists so John
and a grade builder can react to real nodes on a real grade.

## Regenerate

    MH2_DATA_DIR=<data dir> python scripts/render_seq_prototype.py --grade 2
    # any grade_order token: PK K 1..8 A1 (case and "G"/"Grade " prefix tolerated)
    # --out path.html   --db path/to/mh2.db

Default output is `<data>/reports/seq_prototype_<g2|pk|k|a1...>.html`. Single file, inline CSS/JS,
no network. The committed sample is `docs/prototype/seq_prototype_g2.html`.
The DB is opened `mode=ro`. Header counts are each one SQL statement (the predicates are in
an HTML comment at the top of the file and in the tooltips); the script asserts they match its
own Python grouping at build time.

## What is real

- Grade slice: concept/skills (stem_id + `nodes.concept_skill`) with at least one node having
  `node_grade.grade = <grade>`; all nodes of each in ladder order (`nodes.seq`); in-grade solid,
  other-grade dimmed with grade pills; leaf (`resolution='leaf'`) and `no_grade_field` nodes have
  their own look and stay visible even with context grades off.
- Super-stem = `stems.domain`; stem = `stems.name`. (`stem_map.stem_group` is not used: missing for PK-5.)
- Drawer: Goal (from `nodes.goal`) plus any `node_fields` field that is **identical and non-empty on
  every node** of a 2+ node concept/skill (computed) sit in the concept/skill-scoped box; everything
  else is node-specific. Standards, product refs, stated links, grades and ruling raw value are real.
- Shared-CCSS badge: a CCSS code (`node_standards_parsed.state IS NULL`) on nodes in 2+ stems,
  counted over the whole DB. Neutral teal, framed as a pairing.
- Flagged only = concept/skills that contain a shared-CCSS, leaf, or no-grade-field chip.
- Compare: up to 4 chips, URL hash (`#n=<node>&cmp=a,b&sheet=1`), shared CCSS rows highlighted.
- View toggles persist in localStorage (per grade).

## What is placeholder

Module builder panel, "Co-place in a module" (disabled), guardrail bar, calibration/period
estimates. No saved named views, no drag reorder, no orphan queue, no ordering warnings,
no "unplaced only" or "pairing links" toggles (there are no placements yet).

## Not verified visually

Built unattended without a browser. JS was syntax-checked and exercised with a stub DOM (render,
toggles, drawer, compare cap, hash), but layout/CSS has not been eyeballed. Expect small visual fixes.

## Design questions the prototype surfaced

1. **Chips per grade.** Slice sizes (in-grade + context chips): PK 27, K 57, G1 59, G2 57, G3 70,
   G4 82, G5 79, G6 106, G7 107, G8 67, A1 80. The brief's ~52 chips for G2 has grown to 57. 6-7
   are about twice PK. Is a flat scroll of ~100 strips workable, or does 6-7 need stem-level collapse by default?
2. **Strip width.** Max strip is 4 (PK), 6 (K-2), 8 (3-5), 9 (6-A1; Expressions General). At 236px
   a 9-chip strip is ~2,200px and scrolls horizontally. Fine, or narrower chips / wrapping?
3. **Concept/skills per stem** in a slice vary widely; 35 of 116 concept/skills are single-node.
4. **Many fields are "concept/skill-scoped" in practice.** Computed across the DB, fields identical
   on all nodes of a concept/skill: mathematical_models (39 c/s), leaves_to_include (31),
   standards_notes (21), strategies (21), additional_notes (14). This looks like merged ladder cells,
   not a real scoping rule. Should the concept/skill box grow this way, or stay Goal-only per the brief (4.1)?
5. **Goal gaps.** 18 nodes have a blank Goal. In 6 concept/skills the Goal is blank on only
   some nodes (e.g. MUL "Fluently multiply and divide", EE General Expressions "equivalent
   expressions"); 3 concept/skills have none at all (they appear only in G6-A1 slices). The
   brief says Goal applies to the whole progression, so the prototype shows the recorded Goal at
   concept/skill level; should blanks inherit silently?
6. **Spans.** A strip can contain nodes across 5-6 grades (WHO "Represent, compose, and decompose
   numbers": 6 grades; "Use place value to read and write numbers": 6). In G2 that means most of the
   strip is context. 20 of 35 G2 nodes are also in another grade; 23 of 24 PK nodes are.
   What does "solid in each grade" mean for placement: one home grade or repeated?
7. **Unresolved and leaf nodes.** 21 nodes have no grade field and 26 are leaves (only 6 leaves carry
   `node_grade`). They show as context in any slice where their concept/skill appears, but belong to no
   grade. Should they have their own "unassigned" lane?
8. **Shared CCSS is common.** 22 of 35 in-grade G2 nodes carry a shared-CCSS badge; in G6 30 of 81.
   Does the badge still carry signal at that frequency, or should it show only for pairings with
   *in-grade* partners? "Flagged only" is weak at this rate.
9. **Stated links are free text.** 88 `node_links` rows, none resolved to a node, so they appear
   only as drawer text. No pairing-links toggle is possible yet.
10. **Domain grouping.** `stems.domain` and `stem_map.stem_group` disagree for some 6-A1 stems
    (e.g. Coordinate System: Geometry and Measurement vs Ratios and Proportional Relationships).
    Which is the super-stem?
11. **Standards counts.** 87 nodes have no CCSS code at all, so they can never carry the pairing badge.
