# Candidate Tag Generation — Spec

Rev 5. Design artifact for `mh2_macro`, written as the implementation brief for
Claude Code. Nothing here is code; every algorithm is stated precisely enough to
implement without further design decisions.

**Changes from rev 4.** Steps 6 and 8 are built and the first real Path C
numbers exist. One of them inverts a rev-4 policy.

1. **Presentation policy inverted — precision first (§5).** Rev 4 held that a
   modest number is an input to *how* candidates are presented, "not a reason to
   withhold them," and named blind-row rate as the metric to bar on. John's
   ruling supersedes that: **presenting writers with bad suggestions is worse
   than presenting them with nothing.** Precision becomes the objective, and a
   blind row is now an accepted cost rather than the failure to minimize. This
   reverses which way step 7 tunes; it is recorded here so nobody later "fixes"
   the blind rate and quietly undoes it. The numeric bar is still John's to
   write.
2. **Path C's CA leg is quarantined (§3 Path C, §8 step 8).** The offline run's
   CA target corpus is the 14-code `California-not CCSS` set in all three runs,
   so no CCSS→CA prediction can be right except by coincidence. Caught by a
   load-time gate before ingestion. CA is served by Path 0 at 92%; the 14 codes
   are hand-tagged by writers. Not re-run.
3. **The two prediction directions are not interchangeable (§3 Path C).**
   `state_to_ccss` reaches 98% of TX truth codes at the top-10 cut and
   `ccss_to_state` reaches 45%. Measured as candidates, the reverse direction is
   worse than useless — it contributes +0.005 recall and displaces correct
   answers past rank 50. `PathC` takes a per-direction cut; step 7 fits the two
   independently.
4. **§3.5.1 is a model limitation, not a data gap.** State→state scores 0.118
   against hand-written co-occurrence. John's ruling: work around it rather than
   re-run the offline session.
5. **Node placement is unimplemented, not hard (§4.2).** `node_confidence` is
   `1/len(siblings)`; §4.2's text scorer over `node_fields` was never built. It
   scores 0.39 hit rate against a 0.043 random baseline — an accident
   outperforming chance, not a scorer underperforming.
6. **Lesson overlap does not solve placement (§3 Path A).** `product_refs`
   lesson refs are now parsed (295 refs, 115 of 146 nodes). Measured: correct
   placements have positive lesson overlap 68% of the time and wrong ones 70% —
   no discriminative power at node grain, because sibling nodes draw on the same
   module. Useful as a small precise path for the ~36 AG states; nothing for the
   Big Three.

**Changes from rev 3.** Steps 1–5 are built. Two findings from that work change
the design; a third is a defect in rev 3 itself.

1. **Florida has no deterministic coverage.** FL appears in `all_states.csv` only
   for grades 6–9 and is absent from both crosswalk files entirely. Rev 3 assumed
   Path A covered FL. It does not. **FL and TX both route exclusively through Path
   C at PK–5.** §2, §3 Path A, §3 Path C, §5 acceptance bar, §8 build order.
2. **Path B is one source, not two** (§3 Path B, §6). `scored_alignments.csv` was
   produced by auditing `learnosity_combined.csv` using lesson overlap plus the
   Path C model. It descends from Paths A, B, and C simultaneously. Unioning both
   files into `crosswalk` records the same claim twice and treats an audit as an
   independent witness. Restructured as a join: learnosity edges carrying an audit
   verdict.
3. **Defect in rev 3's `strength` rule** (§4). "≥2 independent paths agree" had no
   independence test, so lesson overlap could corroborate a laundered copy of
   itself and earn `strong`. Independence is now defined explicitly.
4. **Eval must gate on reach before scoring** (§5). Rev 3 reported recall of
   `0.000` for CA, TX, FL, and VA, all of which have zero rows in both crosswalk
   files. A zero meaning "no data exists" and a zero meaning "the ranker failed"
   were printed identically, which made the first real harness run
   uninterpretable.
5. **§3.5.1 added** — John's direction for the 28 unanchored nodes: anchor on the
   state codes they already carry.
6. **Labels confirmed clean.** The team wrote the TX and FL ladder codes by
   reading state standards text directly and matching by hand. No path produced
   them. Ground truth is sound and there is no label leakage.

**Changes from rev 2** — all five open questions are now answered by John, and
three of the answers change the spec rather than just confirming it:

1. **Multi-node tags are legitimate** (§9 Q1 → yes). This changes the node
   placement metric in §5, which previously scored against a single correct node.
2. **Absence assertions are CCSS-scoped and must not suppress state candidates**
   (§9 Q4). `relation = 'absent'` is removed from `node_standards_parsed` — the
   assertion carries no standard code and cannot be stored there at all. New
   table `node_absence_assertions` in §6, and a new open problem in §3.5:
   Paths 0, A, and B all require a CCSS anchor, so these nodes are structurally
   invisible to the generator.
3. **Export is node-grain** (§9 Q2). §8 step 10 gains a `Node` column.

Plus: the stem masterlist gaps are filled and `stems.csv` exists (§8 step 3), but
it does **not** retire the hand-maintained mapping — see §7.1.

**Change from rev 1:** node-level tags exist. They are in the ladders'
`Notes related to Standards, Grade, or Leaf` row, unparsed. Rev 1 claimed no
node-level labels existed and pushed everything to concept/skill grain. That was
wrong, and it changes the eval design, the parsing work, and what can be
measured.

---

## 0. Grain

Two hand-tagged surfaces exist, at different grains.

| Surface | Grain | Tag volume |
|---|---|---|
| `H2_Stem_and_Leaf_Spreadsheet` | (stem, concept/skill) | 130 rows; 290 CCSS, 745 Big Three, 589 Other |
| Ladder `Notes related to Standards, Grade, or Leaf` row | (concept/skill, node) | 143 filled cells; 233 CCSS, 388 Big Three, 452 Other across 32 other-state prefixes |

**Generate at node grain. Evaluate at node grain where labels are dense, at
concept/skill grain where they are sparse.**

**Label provenance (confirmed).** The team produced the TX and FL ladder codes by
reading state standards text directly and matching by hand. No crosswalk file, no
lesson-overlap computation, and no model contributed to them. These are the
strongest labels available in the project and none of the four paths can leak into
them. Treat them as ground truth without qualification.

As-built after step 2: 9 ladders → 65 concept/skills, 146 nodes, 1,080 rows in
`node_standards_parsed`. 1,067 of 1,068 code-shaped tokens parse; the holdout is a
source typo (`VS` for `VA`).

Node-level label density by state:

| | Node-level (ladders) | Concept/skill (workbook) | Eval grain |
|---|---|---|---|
| TX | 170 | 272 | node |
| FL | 146 | 235 | node |
| CA | 72 | 231 | concept/skill |

CA is the exception: ladder authors under-record CA because CA is near-verbatim
CCSS and repeating it adds nothing for them. 72 node-level labels is too thin to
tune on, so CA is evaluated against the workbook's 231 and its node placement is
reported as unvalidated.

Two claims are still tracked separately, but both are now measurable for TX and
FL:

1. **Alignment claim** — this state standard belongs in this concept/skill.
2. **Node placement claim** — it belongs on *this* node.

Ladder inventory: 63 concept/skills, 142 nodes across 9 ladders.

---

## 1. Parsing the node standards cells

**This is the gating step for everything else.** The cells are the richest data
in the project and the hardest to read.

Representative cell, from Angles:

```
CCSS.4.G.A.1  CA.4.G.A.1  FL.3.GR.1.1  TX.4.6A  IN.3.G.2 -partially tagged.
Does not include rays in standard   SC.3.MGSR.3.2
```

### Tokenizer rules

Codes are whitespace-separated with no reliable delimiter, and prose is
interleaved. Extract codes by pattern, then treat what remains as annotation.

```
optional  "CCSS."
optional  two-letter state prefix + "."
required  grade: PK | K | 1–12
required  one or more dot-separated alphanumeric segments
```

Disambiguation rule: a leading two-letter token is a **state prefix** if a grade
follows it, and a **CCSS domain** if a grade precedes it. `MD.1.GR.C.5` is
Maryland; `4.MD.C.5` is CCSS Measurement and Data. This matters — `MD`, `NC`,
and `OK` all collide with plausible domain abbreviations.

### Required test cases

TEKS breaks the obvious pattern, and it breaks it silently. A tokenizer that
requires the post-grade segment to begin with a letter returns **zero** TX codes
rather than erroring — I hit exactly this and briefly measured TX as absent from
the ladders. Cover at minimum:

| Input | Expect |
|---|---|
| `TX.4.6A` | state TX, grade 4, `6A` — post-grade segment starts with a digit |
| `TX.K.2A` | state TX, grade K |
| `CCSS.4.G.A.1` and `4.G.A.1` | same CCSS code; strip the optional prefix |
| `MD.1.GR.C.5` | state MD |
| `4.MD.C.5` | CCSS |
| `K.G.B.4 1.G.A.1 2.G.A.1` | three codes, no delimiter |
| `MT.PK.4.14.h` | state MT, grade PK, three segments |
| `MN.PK.M11.7` | state MN |
| `Clocks: MD.1.GR.C.5, TX.2.9G` | label discarded, two codes |
| `IN.3.G.2 -partially tagged. Does not include rays` | one code + relation note |

Reuse `normalize.py`, which already handles this class of mess
(`TX.1.5A-This goes to 120`, `CA.K.CC.3--added MF 5/29`).

### Annotation capture

50 of the 143 cells carry substantial prose after code removal. It is not noise:

- `-partially tagged. Does not include rays in standard` → `relation = partial`
- `("to at least 20")` → `relation = exceeds`, the leaf generator
- `In EM2 this work is noted as being foundational to…` → provenance, keep as
  free text.

Attach residual prose to the nearest preceding code where position allows,
otherwise to the cell. Never discard it.

### Absence assertions are a separate kind of statement

`No CCSSM for ordinal numbers` is not an annotation on a code — **it carries no
code at all.** It is an author asserting that the CCSS framework does not cover
this node. Two consequences, both settled:

1. It cannot live in `node_standards_parsed`, whose primary key requires a
   `standard_code`. Write it to `node_absence_assertions` (§6). Do not invent a
   sentinel code such as `NONE` to force it into the tag table.
2. **It scopes to CCSS only, and must not suppress state candidates.** A node
   CCSS omits is exactly where a state standard is most likely to exist and most
   valuable to surface — that is the definition of a leaf. Rev 2 said the
   assertion "suppresses a future candidate"; that was wrong and is reversed here.

Detection: a cell (or a residual prose fragment) that names a framework and a
negation, with no parseable code attached. Match conservatively — record
`framework` as `CCSS` when the text names CCSS/CCSSM, otherwise `unknown` and let
a human read it. Do not attempt to classify intent beyond that.

**Instrumentation required.** Report the count of absence assertions and the
count of nodes carrying zero CCSS codes as part of step 2's output. That number
decides §3.5 and nothing else can decide it.

### Resolved anomalies

- **Fractions 19 cells / 15 nodes** — not an anomaly. Fractions has 19 nodes and
  19 cells. The node count in rev 2 was wrong.
- **Time's empty `Concept/Skill:` heading** — not a source hole. Time has 9
  concept/skills and needs no author intervention.

### Scope notes — unruled

Roughly 100 residual fragments are parenthetical scope qualifiers: `(to 10,000)`,
`(2-digit x 2-digit)`, `("to at least 20")`. They are captured and labelled as
scope notes, but **nobody has ruled whether each one means `partial` or
`exceeds`** — that is, whether the node covers less than the standard or more.

The leaf generator depends on the direction. Do not guess it in code. This needs a
human pass, and it is listed in §9.1.

---

## 2. Inputs

| File | Role | Verified facts |
|---|---|---|
| Ladder `.docx` × 9 | Node-level tags + node text | 65 concept/skills, 146 nodes, 1,080 parsed tag rows |
| `H2_Stem_and_Leaf_Spreadsheet_K_to_G5.xlsm` | Concept/skill tags | 130 rows; cols: Stem, Concept/Skill, Details, CCSSM Standard, Big Three Standard, Other Standard, Grade/Leaf, Notes |
| `Standards_for_Stems_Tagging_1.xlsx` | Standard text + work queue | Sheets: CCSS, California-not CCSS, Florida, Texas, Other State Standards Gaps, California-All. Every state sheet has `Tagged to a Stem` (Yes/blank) plus two note columns |
| `stems.csv` | stem mapping (§7.1) | 22 PK–5 rows (10 drafted), 32 rows in the 6–9 band |
| `CCSS_alignment_guide.csv` | CCSS→lesson (primary) | 2,175 lesson pairs, 350 standards, same 11-col schema as `all_states.csv` |
| `lesson_metadata.csv` | CCSS→lesson (supplement) | 2,315 pairs, 426 standards; 1,864 overlap with the guide |
| `all_states.csv` | state→lesson | 77,943 rows with both lesson and standard; 36 states; **no CA, no TX; FL only at grades 6–9** |
| `learnosity_combined.csv` | Path B edge source | 23,080 rows, 31 states; **no CA, no TX, no FL, no VA** |
| `scored_alignments.csv` | audit verdicts over learnosity | 9,076 rows, 28 states; **no CA, no TX, no FL, no VA.** 27 of its 28 states also appear in learnosity — it is an audit, not a second source |
| `data/source/predictions/` | model output | To be produced offline |

### Verified state coverage at PK–5

This table is the reason for most of rev 4. Confirmed by direct count against the
files, not inferred:

**Measured** by the §5 precondition report at node grain, as the share of each
state's hand-tagged truth codes present in that path's vocabulary. `—` means the
path has no vocabulary for the state at all. Superseded numbers from earlier revs
are gone; these come from a `SELECT DISTINCT`, not an estimate.

| State | truth codes | Path 0 | Path A (lesson) | Path B (crosswalk) | Path C |
|---|---|---|---|---|---|
| **CA** | 39 | **92%** | — | — | pending |
| **TX** | 80 | — | — | — | pending |
| **FL** | 61 | — | **0%** (6–9 only) | — | pending |
| KS | 6 | — | 0% | **83%** | pending |
| IN | 6 | — | 83% | 83% | pending |
| OK | 14 | — | 79% | — | pending |
| SC | 14 | — | 71% | 0% | pending |
| MN | 12 | — | 58% | — | pending |
| MO | 6 | — | 50% | 50% | pending |
| NE | 12 | — | 42% | — | pending |
| ND | 10 | — | 20% | 40% | pending |
| VA | 36 | — | 28% | — | pending |
| MD | 21 | — | 0% | 0% | pending |

**Two of the three priority states have exactly one path.** Path C is not a
supplement to this design; for TX and FL it is the entire design.

Corrections to rev 4's version of this table, all measured:

- **Path B is not available for SC**, and rev 4's "SC, ND, IN, MO and ~24 others
  — Path B: yes" was wrong. SC's apparent Path B coverage came from
  `scored_alignments`, which is not a Path B source (see §3 Path B).
- **KS gains Path B at 83%** and is the strongest state in the whole set —
  invisible before, because learnosity's codes needed shortening.
- **MD has neither A nor B.** Vocabulary exists for both; none of it is the
  codes the authors used. That is an incomplete crosswalk, not a missing path.
- **Path A carries the long tail** — OK, SC, MN, NE, VA, IN — and Path B is
  thin everywhere except KS, IN and ND.

K–5 untagged backlog per `Tagged to a Stem` (ceilings; includes cluster headers):
CCSS 121, California-All 64, Florida 60, **Texas 106**.

---

## 3. The four paths

All four answer the same question: given a CCSS code, which state standards
correspond to it? Node assignment (§4) happens afterward.

### Path 0 — CA code inheritance

532 of 550 rows in `California-All` are marked `CCSS`, i.e. verbatim CCSS text.
CA codes drop the cluster letter that CCSS carries.

```
canon(code) := strip leading "CA."; remove segment 3 if a single letter
```

`CA.1.MD.3` → `1.MD.3`; `1.MD.B.3` → `1.MD.3`. Build `canon → ccss_code` from
the CCSS sheet, look up each CA code, and require normalized text equality as a
guard against canon collisions. On a hit, CA inherits every tag on the CCSS
standard: `score = 1.0`, `path = ca_code`.

**Measured**: 175 of 228 CA workbook hand tags (77%) are exact transforms of the
CCSS tag on the same row. Excluded: the 17 `California-not CCSS` rows and the one
row noted `Different than CCSS`.

### Path A — lesson overlap

Build `standard_lessons(standard_code, lesson_id, source)` from:

- `CCSS_alignment_guide.csv` where `ref_type == 'lesson'` — **primary** for the
  CCSS side. Same schema as `all_states.csv`, so this is a self-join.
- `lesson_metadata.content_standards_list` — supplement, adds ~139 standards.
  **Expand range notation first**: `3.MD.C.5.a–b`, `1.NBT.B.2.a-b`,
  `1.NBT.B.2.a–c`. Both ASCII hyphen and en-dash occur. Add as `normalize.py`
  test cases.
- `all_states.csv` where `ref_type == 'lesson'` — state side.

```
overlap(ccss, state) = |L(ccss) ∩ L(state)| / |L(ccss) ∪ L(state)|
```

Emit a candidate when the intersection is ≥ 1. Rank by Jaccard, tie-break on
intersection size. Store the intersecting lesson IDs in `evidence_json` — that
is the reviewer-facing justification.

Coverage: ~35 states. **Not CA** (Path 0 handles it), **not TX**, and **not FL at
PK–5** — FL appears in `all_states.csv` only at grades 6–9. Rev 3 claimed FL
coverage here; that was wrong. Path A is useful for the long tail of states and
contributes nothing to any Big Three state below grade 6.

### Path B — one source, with an audit attached

**Path B is a single source of evidence, not two.** Rev 3 treated
`learnosity_combined.csv` and `scored_alignments.csv` as peer inputs unioned into
one table. They are not peers:

- `learnosity_combined.csv` is the team's tagging of Eureka Math 2 assessment
  items. It is the **only** edge source in Path B. Parse
  `CCSS Math Standard: <code>` **and `CCSS Math Child Standard: <code>`** from
  `targetTags`, pair with `newTagValue`. Deterministic, 31 states.
  Child-standard rows are real sub-standards (`3.MD.C.5.a`), 1,442 of them K–5;
  Cluster and Domain rows are not standards and stay out.
- `scored_alignments.csv` is an **audit**, produced by combining lesson overlap
  (Path A's method), the Path C model, and standards text, then asking a model
  to rule on each alignment and suggest alternates. It descends from Paths A, B
  and C at once.

### What the audit actually audits — measured, and not what rev 4 assumed

Rev 4 stated the audit rules on the *learnosity* tags. It does not. Its anchor
codes overlap:

| | overlap |
|---|---|
| `all_states.csv` (Path A's state side) | **84.0%** |
| `learnosity_combined.csv` | **3.9%** |

and that 3.9% is **entirely Massachusetts** — the one state whose own prefix
collides with the item bank's `MA` for *Mathematics*. The two files share no code
vocabulary: the audit writes SC `7.PAFR.2.2`, learnosity writes
`SC.CCRS.MA.9-12.A1.AAPR.1`.

So the audit is a **Path A** artifact. It is stored in `alignment_audit`, keyed
on the tag it rules on, and attaches to Path A candidates as a flag. It carries
no columns on `crosswalk`. The independence conclusion is unchanged and if
anything stronger: it audits Path A's tag set using Path A's own method, so
agreement is a method agreeing with itself. **Use it to demote, never to
promote.**

### Learnosity codes must be shortened before they join anything

Learnosity stores item-bank tag paths, with framework segments between the state
prefix and the grade. Every other surface in the project uses the state's short
code:

```
IN.2023.MATH.1.NS.1   ->  IN.1.NS.1
MO.LS.MA.1.GM.A.1     ->  MO.1.GM.A.1
```

Unshortened, learnosity intersects the ladders' 424 hand-tagged codes at
**exactly zero** and Path B is structurally silent for every state. Shortening is
verified against a file that had no part in producing it: 6,210 of 9,842 codes
then match `all_states`, and ladder overlap goes 0 → 47. The original path is
kept in `crosswalk.source_code`.

Consequences, all binding:

1. **Join, do not union.** One row per learnosity edge, with the audit verdict
   attached as a column. Unioning produces two rows for one claim and lets any
   row-counting boost double-count it.
2. **The audit is a veto and a weight, never an origin.**

   | learnosity edge | audit verdict | treatment |
   |---|---|---|
   | present | `confirmed` / `likely_correct` | keep, higher weight |
   | present | rejecting verdict | drop, or keep at minimum weight and flag |
   | present | no audit row | keep, base weight |
   | absent | row exists (`recommended_ccss_id`) | **not a Path B edge** — route to Path C as a model suggestion |

3. Confidence is effectively binary (median 99, p25 97). Use verdict presence, not
   magnitude.
4. **`scored_alignments` can never corroborate Path A or Path C** (see §4
   independence). It is derived from both.

Path B still boosts and never originates. Coverage excludes CA, TX, FL, and VA
entirely, so Path B is silent on every priority state.

### Path C — model similarity

Offline, in the environment where the fine-tuned SentenceTransformers model
lives. Output contract:

```
data/source/predictions/{state}_ccss_topk.csv
  state, state_code, ccss_code, rank, score, model_version
```

Top-10 per state standard. **The only path available for TX *and* FL at PK–5.**
TX is absent from all three deterministic sources; FL is absent from both
crosswalk files and appears in `all_states.csv` only at grades 6–9.

This makes Path C the critical path, not the final polish. Two consequences for
sequencing (§8):

- **Path C ingestion moves ahead of threshold fitting.** Fitting thresholds on
  Path 0 / A / B output alone would tune against a population that contains no TX
  and no FL subjects — two of the three states the tool exists to serve.
- The offline run should produce, in one session: the state→CCSS top-k for all
  priority states, **and** the state→state run required by §3.5.1.

Expect weaker performance for TX; TEKS diverges structurally from CCSS. FL's
expected performance is now unknown rather than assumed adequate.

---

## 3.5 Unanchored nodes — the structural gap

All four paths above take a CCSS code as input. Paths 0, A, and B take *only*
that; Path C takes state text but still produces a CCSS pairing. **A node with no
CCSS code therefore receives no candidates from any path.** These nodes are
invisible to the generator, and they are the nodes where a leaf is most likely to
be needed.

Two ways to arrive in that state:

- an explicit absence assertion (`node_absence_assertions`), or
- simply no CCSS code in the cell, with no assertion either way.

Three approaches were tabled in rev 3. **All are superseded by the ruling in
§3.5.1**; they are retained as record of what was considered.

| Approach | Cost | Notes |
|---|---|---|
| **Path D — reverse similarity.** Node text as query, state standards as corpus, same fine-tuned model, offline into `data/source/predictions/`. | second offline run | Fits the existing Path C contract |
| **Orphan pool.** State standards untagged in the workbook *and* absent from every crosswalk source, matched against unanchored nodes. | no model run | Doubles as backlog burn-down; TX has 106 untagged K–5 |
| **Manual flag.** Browser marks the node "no CCSS anchor — leaf candidate"; author searches the hand-built `Other State Standards Gaps` sheet. | none | The sheet already holds thousands of rows |

## 3.5.1 Ruling — anchor on the state codes already present

**Count: 28 unanchored nodes** (step 2a). John's direction, adopted:

Many nodes with no CCSS code still carry *state* codes the authors wrote by hand.
Those hand-written state codes are the anchor. Instead of "given a CCSS code, find
corresponding state codes," the question inverts to **"given the state codes an
author already put on this node, find state codes in other states that correspond
to them."**

Why this is the right answer:

- It reuses the labels that are already the project's strongest evidence, rather
  than inventing a new signal.
- It needs no CCSS pivot, which is precisely what these nodes lack.
- It composes with the existing structure — same candidate rows, same strength
  vocabulary, with `anchor_ccss_code` NULL and a new `anchor_state_code`.

What it requires: **a state→state model run**, which does not exist yet. The
current predictions contract is state→CCSS. So this is not implementable until the
offline session produces it — bundle it with the Path C run per §3 Path C.

Until then, unanchored nodes render in the browser as "no CCSS anchor — state
anchor pending," never as an empty result. The three approaches tabled above are
superseded by this ruling and retained only as record.

Interim behaviour: unanchored nodes render in the browser with an explicit "no
CCSS anchor" state. They must never render as an empty result indistinguishable
from "the generator found nothing" — that is the blind-row failure mode in §5
wearing a different hat.

---

## 4. Combination and node assignment

### No blended score

One candidate row per `(node, state_standard)` carrying each path's score
independently, plus a derived `strength`:

| strength | condition |
|---|---|
| `strong` | Path 0 exact code+text match, **or** ≥2 **independent** paths agree |
| `moderate` | one deterministic path (overlap above threshold, or crosswalk edge) |
| `weak` | model only |

### Independence is defined, not assumed

Rev 3 said "≥2 independent paths agree" without defining independence. That was a
defect: `scored_alignments.csv` was built from lesson overlap plus the Path C
model, so Path A agreeing with a `scored_alignments`-derived edge is **lesson
overlap agreeing with a copy of itself**, promoted to `strong` on one source
counted twice.

Two paths count as agreeing **only if neither derives from the other**:

| | Path 0 | Path A | Path B (learnosity) | Path C |
|---|---|---|---|---|
| **Path 0** | — | independent | independent | independent |
| **Path A** | independent | — | independent | independent |
| **Path B (learnosity)** | independent | independent | — | independent |
| **Path C** | independent | independent | independent | — |

Learnosity edges are independent of everything: they are human tagging of
assessment items, produced before and outside this pipeline.

**`scored_alignments` is not a path and cannot appear in this matrix.** It is a
verdict attached to a learnosity edge (§3 Path B) and a source of Path C
suggestions. An edge whose only support is a `scored_alignments` row is **Path C
evidence, and `weak`** — not corroboration of anything.

Implementation: `strength` derives from the set of *distinct independent paths* with
a non-null score, not from a count of supporting rows. Two rows from one path is
one path.

Every TX and FL candidate at PK–5 is `weak` by construction, because Path C is
their only path. The UI must show this prominently — it is now the majority case
for two of three priority states, not an edge case.

### Grade is a prior, not a filter

Offset between each state hand tag's grade and its paired CCSS tag's grade
(workbook):

| | same | ±1 | ±2 | worse |
|---|---|---|---|---|
| CA | 197 | 18 | 9 | 2 |
| FL | 147 | 50 | 14 | 10 |
| TX | 177 | 54 | 20 | 6 |

A same-grade filter discards 33% of FL and 31% of TX human tags. `3.MD.A.1` is
hand-tagged to both `TX.3.7C` and `TX.4.8C`. Apply a multiplicative decay over a
±2 window, fit on the tuning split. Never hard-filter on grade.

### Node assignment

**One standard may legitimately belong on several nodes within a concept/skill**
(§9 Q1, confirmed by John). This is normal authoring, not a data error. Design
accordingly:

1. One node in the concept/skill → assign to it, `node_confidence = 1.0`.
2. Otherwise score the standard text against each node's `Knowledge Graph Node`
   + `Specifications` + `Required Skills/Cases` text. Emit a candidate row for
   **every node scoring above the multi-node threshold**, not only the top node,
   each carrying its own `node_confidence`. If none clears it, emit the top node
   alone. List the full ranked node list in `evidence_json` either way.
3. The multi-node threshold is fit on the tuning split alongside the other
   thresholds (step 7). Until it is fit, emit top-node-only so step 6 is not
   blocked.

`node_standards_parsed` needs no uniqueness constraint beyond its primary key,
and neither does `candidates` — a `(concept_skill, standard_code)` pair appearing
on three nodes is three rows.

Node placement is measurable for TX and FL against the parsed ladder cells.
Report it separately from the alignment metric — they are different claims and a
generator can be good at one and bad at the other.

---

## 5. Eval harness

**Build this before the generator.** It sets every threshold and decides whether
anything ships.

### Two datasets

**Node-level (primary; TX, FL).** 143 parsed ladder cells. 101 carry a CCSS code
plus at least one Big Three code; 33 carry all three. Input to the generator: the
CCSS codes in the cell. Held out: the state codes in the same cell.

**Concept/skill-level (primary for CA; secondary check for TX and FL).** 117 of
130 workbook rows carry both a CCSSM tag and Big Three tags. Usable
grade-parseable state tags: CA 228, FL 221, TX 257. Node-level candidates are
unioned up to their concept/skill before comparison.

Exclusions: codes that fail `normalize.py`; the 44 concept tags referencing codes
with no standard text (source typos such as `TX.K.2.B`, range notation such as
`VA.5.MG.1.b.i-ii`); the 26 unparsed cell lines.

### Reach gating — compute before scoring, report separately

**This is the largest change in rev 4 to §5.** The first real harness run reported
`r@10 = 0.000` for CA, TX, FL, and VA. All four have zero rows in both crosswalk
files, so Path B could not name a correct answer for any of them under any ranking.
The ranker behaved correctly and the output said it failed. Meanwhile SC scored
0.635 against a 71.4% vocabulary ceiling — near-optimal retrieval, printed as
mediocre.

A zero that means *no data exists* and a zero that means *the ranker failed* must
never print identically. Every subject lands in exactly one bucket:

| Bucket | Condition | Feeds recall? |
|---|---|---|
| **Excluded** | node carries no anchor at all | no — not a test |
| **Path unavailable** | anchor exists; the path has no vocabulary for this state | **no** — emit `path unavailable`, never `0.000` |
| **Unreachable** | vocabulary exists, but no edge from *this subject's* anchors can reach the truth code | counted as a miss in raw recall; excluded from conditional recall |
| **Reachable** | an edge exists | **yes — the only real test of the ranker** |

Report three numbers per (state × path), never one:

1. **Coverage** — share of truth codes reachable. A property of the data.
2. **Conditional retrieval** — recall restricted to reachable subjects. A property
   of the ranker.
3. **Raw recall** — end to end, unconditioned. This is what the acceptance bar is
   written against.

Compute reach at **two levels**, and report the gap:

- *vocabulary reach* — is the truth code present anywhere in this path's output
  space for this state?
- *anchor reach* — is it linked from this subject's specific anchors?

Codes with vocabulary reach but no anchor reach mean the crosswalk is **incomplete**
and fixable. Codes with no vocabulary reach mean a **different path is needed**.
Those imply different work, so the harness must distinguish them.

Practical note: vocabulary reach is a `SELECT DISTINCT` against the source files
and costs nothing. Run it as a precondition report before any scoring. Had it
existed, the Florida finding would have surfaced on day one instead of after a full
harness build.

### Metrics — per state, never blended

- **Alignment**: recall@5, recall@10, MRR, precision@5 — each reported as
  conditional and raw per the bucketing above.
- **Blind-row rate**: share of subjects where no correct standard appears
  anywhere in the candidate list. **Keep reporting it; it is no longer the
  metric to bar on** — see the rev 5 ruling under *Acceptance bar* below, which
  makes precision the objective and a blind row an accepted cost. It stays in
  the harness because it is the honest measure of what the tool declines to
  answer, and that number should be visible even when it is deliberately
  allowed to rise.
- **Node placement** (TX, FL): of correctly surfaced standards, share assigned to
  **any** node the author put them on. Because a standard may sit on several nodes
  (§4), the truth value is a *set* of node IDs, not one node. Scoring against a
  single node would mark a correct assignment wrong whenever the author used more
  than one. Report two numbers:
  - **hit rate** — the generator's node set intersects the author's set.
  - **set agreement** — Jaccard between the two node sets, which catches both
    over-assignment (candidate on five nodes when the author used one) and
    under-assignment.

  Hit rate alone is gameable by assigning every standard to every node; the
  Jaccard is what stops that. Report both or neither.

Report the two grains separately. A generator can score well at concept/skill
grain and scatter standards across the wrong nodes; that difference is the whole
value of having both datasets.

### Splits

No model training happens here, but thresholds and the grade decay are fit: 30%
of subjects for tuning, 70% held out for reporting. Stratify by stem.

**Fit on reachable subjects only.** Unreachable subjects cannot respond to any
threshold — their outcome is fixed regardless of what the tuning changes — so
including them injects noise into the objective. Report on everything.

### Acceptance bar — precision first (rev 5 ruling)

Rev 3's bar was `recall@10 ≥ 0.80 for CA and FL`, which was unsatisfiable: it
required FL to hit 0.80 using paths that have no FL data at PK–5. Rev 4 left the
bar unset pending real Path C numbers. Those numbers now exist (§8 step 8), and
John's ruling on reading them **reverses rev 4's presentation policy**:

> **Presenting the writers with bad suggestions is worse than presenting them
> with nothing.**

Rev 4 said the opposite — that a modest number governs *how* candidates are
presented and is "not a reason to withhold them," and that blind-row rate is the
metric to bar on. **That is superseded.** The consequences, which bind on step 7:

- **Precision is the objective, not recall.** Step 7 fits thresholds to surface
  only what the evidence supports, and shows nothing where it does not.
- **A blind row is now an accepted cost.** It is no longer the failure mode to
  minimize. This is the specific line someone will later try to "fix" by
  loosening a threshold to lift coverage — that is a reversal of a deliberate
  ruling and requires John, not a tuning pass.
- **The three confidence sources**, in John's words: standards with strong
  lesson overlap in the AG; standards sharing the AG's recommended lesson;
  standards with strong model scores. The first two are Path A, the third is
  Path C above a fitted threshold. **For TX and FL only the third exists** —
  Path A gives them nothing (§3 Path A), so a precision-first policy for those
  two states is entirely a Path C threshold question.
- **Do not read a raw score as a probability.** TX's Path C tops out at 0.807
  and FL's at 0.870, with the best-candidate-per-node median at 0.65. A
  threshold that looks like a percentage (`> 0.8`) surfaces almost nothing. Fit
  the cut to a precision target on the tuning split; never pick the number.

What is settled elsewhere:

- **CA** — Path 0 is deterministic and recovers 77% of hand tags.
  `recall@10 ≥ 0.80` remains reasonable and is unaffected by the ruling above.

**The numeric bar is still John's to write**, now against precision rather than
blind rate. Nothing in step 7 sets it.

### Do not use `scored_alignments.csv` as labels

It was produced by combining lesson overlap (Path A), the Path C model, and
standards text, then asking a model to rule on each learnosity tag. It descends
from the methods under test. Corroborating signal and audit verdict only — never
ground truth.

**Ground truth is the ladder cells and the workbook**, written by the team reading
state standards text directly (§0). That provenance is confirmed and clean: no path
contributed to the labels, so the circularity in `scored_alignments` affects
features only, never labels.

### Also settled here

Whether top-5 or top-10 model predictions are needed. If recall@5 saturates, the
existing top-5 run stands and no recompute is required.

---

## 6. Schema additions

Layer 1 — regenerated on every rebuild, no human data:

```sql
CREATE TABLE node_standards_parsed (       -- from the ladder cells
    node_id        TEXT NOT NULL,
    standard_code  TEXT NOT NULL,
    state          TEXT,                   -- NULL for CCSS
    relation       TEXT,                   -- aligned | partial | exceeds
    annotation     TEXT,
    source_cell    TEXT NOT NULL,
    PRIMARY KEY (node_id, standard_code)
);
-- 'absent' was a rev-2 relation value and is gone. An absence assertion has no
-- standard_code, so it cannot be a row here. See node_absence_assertions.

CREATE TABLE node_absence_assertions (     -- 'No CCSSM for ordinal numbers'
    node_id     TEXT NOT NULL,
    framework   TEXT NOT NULL,             -- CCSS | unknown
    note        TEXT NOT NULL,             -- verbatim author text
    source_cell TEXT NOT NULL,
    PRIMARY KEY (node_id, framework, note)
);
-- Scopes to the named framework ONLY. Never used to filter or suppress state
-- candidate generation. Read §1 and §3.5 before writing code against this table.

CREATE TABLE standard_lessons (
    standard_code TEXT NOT NULL,
    lesson_id     TEXT NOT NULL,
    source        TEXT NOT NULL,           -- guide | metadata | all_states
    PRIMARY KEY (standard_code, lesson_id, source)
);

CREATE TABLE crosswalk (                   -- Path B edges: learnosity ONLY
    state           TEXT NOT NULL,
    state_code      TEXT NOT NULL,         -- shortened; joins everything else
    ccss_code       TEXT NOT NULL,
    source_code     TEXT,                  -- the item-bank path, verbatim
    granularity     TEXT,                  -- state side: Standard | Cluster | ...
    PRIMARY KEY (state_code, ccss_code)
);
-- Rev 3 had `source` in the PK and unioned two files. That recorded one claim as
-- two rows and let a row-counting boost double-count it. The union is gone.
-- No audit columns: scored_alignments does not audit these edges (§3 Path B).
-- `granularity` exists because not every edge joins two peers -- learnosity
-- pairs a CCSS standard with a state Cluster 57 times and a Heading twice.

CREATE TABLE alignment_audit (             -- scored_alignments verdicts
    state       TEXT NOT NULL,
    state_code  TEXT NOT NULL,
    ccss_code   TEXT NOT NULL,             -- the tag being ruled on
    verdict     TEXT,
    confidence  REAL,                      -- effectively binary; use presence
    rationale   TEXT,
    PRIMARY KEY (state_code, ccss_code)
);
-- The audited subject is an all_states tag, i.e. PATH A's state side. Attaches
-- to Path A candidates as a flag. Never corroborates: the audit was produced by
-- running lesson overlap, which is Path A's own method. Demote, never promote.

CREATE TABLE model_suggestions (           -- scored_alignments.recommended_ccss_id
    state         TEXT NOT NULL,
    state_code    TEXT NOT NULL,
    ccss_code     TEXT NOT NULL,           -- the RECOMMENDED code
    rationale     TEXT,
    PRIMARY KEY (state_code, ccss_code)
);
-- ONLY rows where the audit recommended a code different from the one it ruled
-- on -- 619 of 9,076, deduping to 436. A row restating a tag it just confirmed
-- suggests nothing. These are Path C evidence and yield `weak`. They must never
-- corroborate Path A or Path C (§4 independence).

CREATE TABLE model_predictions (
    state         TEXT NOT NULL,
    state_code    TEXT NOT NULL,
    ccss_code     TEXT NOT NULL,
    rank          INTEGER NOT NULL,
    score         REAL NOT NULL,
    model_version TEXT NOT NULL,
    PRIMARY KEY (state_code, ccss_code, model_version)
);

CREATE TABLE standard_tag_status (
    standard_code   TEXT PRIMARY KEY,
    sheet           TEXT NOT NULL,
    tagged_to_stem  INTEGER NOT NULL,      -- 1 = Yes, 0 = blank
    note_tagging    TEXT,
    note_postladder TEXT
);

CREATE TABLE candidates (
    candidate_id     INTEGER PRIMARY KEY,
    run_id           INTEGER NOT NULL,
    node_id          TEXT,
    concept_skill_id TEXT NOT NULL,
    state            TEXT NOT NULL,
    standard_code    TEXT NOT NULL,
    anchor_ccss_code TEXT,                 -- NULL for §3.5.1 state-anchored rows
    anchor_state_code TEXT,                -- §3.5.1; NULL for normal CCSS-anchored rows
    score_ca_code    REAL,
    score_overlap    REAL,
    score_crosswalk  REAL,
    score_model      REAL,
    grade_offset     INTEGER,
    combined_score   REAL NOT NULL,
    strength         TEXT NOT NULL,        -- strong | moderate | weak
    node_confidence  REAL,
    evidence_json    TEXT NOT NULL,
    generated_at     TEXT NOT NULL
);
CREATE INDEX idx_cand_cs ON candidates(concept_skill_id, state);
CREATE INDEX idx_cand_node ON candidates(node_id, state);
```

Layer 3 — append-only, survives rebuild, never regenerated:

```sql
CREATE TABLE candidate_reviews (
    review_id     INTEGER PRIMARY KEY,
    subject_key   TEXT NOT NULL,   -- '{node_id|concept_skill_id}|{standard_code}'
    ruling        TEXT NOT NULL,   -- accepted | rejected | deferred | wrong_node
    reviewer      TEXT NOT NULL,
    reviewed_at   TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    note          TEXT,
    superseded_by INTEGER REFERENCES candidate_reviews(review_id)
);
CREATE INDEX idx_review_subject ON candidate_reviews(subject_key);
```

`evidence_hash` covers the normalized standard text plus the node text the
reviewer saw. Text changes → hash mismatch → the review resurfaces as stale with
the prior ruling shown for context.

---

## 7. Parser fixes (prerequisite)

Both cause silent undercounting today.

1. **Strip comment anchors before matching heading labels.** Real headings
   include `Concept[^c3]/Skill[^c4]:` and `C[^c63][^c64][^c65]oncept/Skill:`. In
   the `.docx` path these are `w:commentRangeStart` runs splitting the text.
   Strip first, normalize second. Without this, Fractions reads as 2
   concept/skills instead of 5.
2. **Absorb continuation headings.** Angles and Whole Numbers both have
   `### Concept/Skill:` with empty text followed by a separate heading holding
   the label (`### Make and describe turns)`). If a Concept/Skill label
   normalizes to empty, take the next heading paragraph as its label.

Corrected concept/skill counts after both fixes — Counting 14/14, Subitization
2/2, Whole Numbers & Base Ten 6/6, Mult & Div 9/9 match the workbook exactly;
Angles 7/6, Estimating 9/6, Comparing+Ordering 10/7, Fractions 7/5, Time 12/7
reflect authoring consolidation.

Known source hole: the Time ladder has an empty `Concept/Skill:` heading with no
continuation. Data problem, not a parser problem — needs an author.

## 7.1 `stems.csv` — the mapping is recorded, not inferred

The stem masterlist gaps are filled: Length, Perimeter, Area, Volume, Data,
Spatial Thinking and the Structure/Pattern/Reasoning bullet are all present, and
the 6–9 band is now enumerated.

**This does not retire `LADDER_TO_WORKBOOK_STEMS` by making the mapping
derivable.** It relocates the hand-maintained mapping from Python into a data
file. Three naming surfaces disagree, and none of the disagreements is recoverable
from string similarity:

| | masterlist | workbook |
|---|---|---|
| PK–5 stem count | 21 | 22 |
| Comparing / Ordering | one stem, "Comparing and Ordering" | two stems, "Comparing" and "Ordering" |
| Subitization | "Subitizations" | "Subitization" |
| Mult/Div | "…of Fractions" | "…(Fractions)" |
| Identify/classify | "Identify, Define, and Classify…" | "Identify, define, parts, and classify…" |
| Group label | "Shape and Space" | "Shapes and Space" |

The masterlist agrees with the *ladder* on Comparing and Ordering; the workbook is
the outlier. Note also a double space in the masterlist's
`Identify,  Define, and Classify…`.

Schema — one row per **workbook** stem for PK–5 (the workbook is the tagging
surface), one row per masterlist stem for 6–9 where no workbook rows exist yet:

```
stems.csv
  stem_id         stable slug, e.g. NSS_COM, NSS_ORD, MD_LENGTH
  band            PK5 | 6_9
  stem_group      masterlist group heading
  masterlist_name repeats across NSS_COM and NSS_ORD by design
  workbook_sheet  blank for 6_9
  workbook_stem   blank for 6_9; preserve the embedded newline in
                  'Whole Numbers and \nBase Ten Structure' or match on normalized text
  ladder_file     blank where undrafted; the Comparing and Ordering ladder
                  appears on two rows
  ladder_drafted  1 | 0
```

22 PK–5 rows, 10 drafted; 32 rows in the 6–9 band. Loader must accept a repeated
`masterlist_name` and a repeated `ladder_file`. Any code assuming one ladder maps
to one stem is wrong.

The 12 undrafted PK–5 stems account for the untagged backlog in §2 — over half the
untagged K–5 CCSS standards are MD or G, and those are stems nobody has written
yet rather than tagging that was skipped.

---

## 8. Build order

| # | Deliverable | Depends on |
|---|---|---|
Steps 1–5 are **complete**. Remaining work, resequenced for rev 4:

| # | Deliverable | Depends on | Status |
|---|---|---|---|
| 1 | Parser fixes + tests (§7) | — | ✅ done |
| 2 | Node cell parser → `node_standards_parsed` + `node_absence_assertions` | 1 | ✅ done |
| 2a | Instrumentation report | 2 | ✅ done |
| 3 | `stems.csv` loader, `LADDER_TO_WORKBOOK_STEMS` retired | — | ✅ done |
| 4 | Layer-1 loaders | 1 | ✅ done |
| 5 | `eval/harness.py` | 2, 4 | ✅ done — needs rev-4 rework |
| **5a** | **Reach gating + three-bucket reporting (§5)** | 5 | **next** |
| **4a** | **`crosswalk` restructure: join not union; `model_suggestions` (§3 B, §6)** | 4 | **next** |
| 6 | `mh2/candidates.py` — Paths 0, A, B, with §4 independence | 4a, 5a | |
| **8** | **Path C ingestion — moved ahead of fitting** | 6 | offline run required |
| 7 | Threshold, grade-decay, multi-node threshold fitting | 5a, 6, 8 | |
| 9 | Multi-node assignment + placement metrics (§4, §5) | 2, 6 | |
| 3.5.1 | State-anchored candidates for the 28 unanchored nodes | state→state model run | |
| 10 | Browser + Word/Excel export | 6 | |
| 11 | §11 closure and query tooling | 10 | out of scope for now |

**Why 8 now precedes 7.** TX and FL both route exclusively through Path C at PK–5
(§2 coverage table). Fitting thresholds before Path C exists means fitting on a
population containing zero TX and zero FL subjects — two of the three states the
tool serves. The fit would be tuned to the long-tail crosswalk states nobody
prioritized.

**5a and 4a come before 6** because both change what step 6 reads and what its
output means. Doing them after means re-running and re-reading step 6's numbers.

### The offline session

One trip to the model environment should produce all of:

- state→CCSS top-10 for CA, TX, FL, and the reporting states → `predictions/`
- **state→state** top-k for §3.5.1's 28 unanchored nodes
- a recorded `model_version` for both

Batch these. The session is the bottleneck for steps 7, 8, and 3.5.1 at once.

### Step 2a — instrumentation report

Step 2 must print, not just load. These counts settle open design questions and
are worthless if they live only in the database:

- absence assertions found, by framework
- nodes carrying zero CCSS codes (with and without an assertion) — **decides §3.5**
- standard codes appearing on more than one node within a concept/skill, and the
  distribution of nodes-per-code — confirms §4 quantitatively
- cells whose residual prose failed to classify into
  `partial` / `exceeds` / provenance / absence
- resolution of the Fractions anomaly: 19 filled cells against 15 nodes
- codes rejected by `normalize.py`, verbatim

### Step 10 — export grain

Node-grain export, per §9 Q2. Insert a `Node` column immediately after
`Concept/Skill` and leave the remaining six columns and both note columns
untouched, so the sheet remains ingestible by the existing loader and familiar to
the team.

Consequence of multi-node tags (§4): **a standard code will legitimately repeat
down the sheet across sibling nodes.** This reads as duplication to a reviewer.
The export must carry a header note or a legend cell saying so, or the team will
dedupe it and destroy the node assignment.

---

## 9. Resolved decisions

All five rev-2 open questions are answered. Do not relitigate these; if something
appears to contradict them, ask rather than redesign.

1. **Multi-node tags — yes.** One standard may attach to several nodes within a
   concept/skill. Legitimate authoring. Drives §4 emission and §5 set-based
   placement scoring.
2. **Export grain — node level.** Add a `Node` column; preserve the rest of the
   sheet. §8 step 10.
3. **`Notes related to Standards, Grade, or Leaf` stays the tagging home.** It is
   where the team is actually tagging. No new ladder Standards row for now.
   Revisit only if a ladder template is being redrafted anyway.
4. **Absence assertions are CCSS-scoped and never suppress state candidates.**
   See §1 and §3.5. The rev-2 suppression behaviour is reversed.
5. **`reviewer` is free text.** The UI should seed the input from the distinct
   values already present so `John` / `john` / `JW` do not become three reviewers.
   No enforced roster.

## 9.1 Still open

- **Acceptance bar for TX and FL** — deliberately unset until one real Path C
  number exists. Write it in rev 5. §5.
- **~100 scope notes** (`(to 10,000)`, `(2-digit x 2-digit)`) are captured and
  labelled but unruled: `partial` or `exceeds`? Needs a human pass. The leaf
  generator depends on the direction. §1.
- **`stem_id` slugs in `stems.csv`** are provisional. Join on `stem_id` as an
  opaque key; nothing should hardcode them.
- **`VS` → `VA`** — one source typo, the single unparsed token of 1,068.
- **Tokenizer truncates a hyphenated segment followed by digits.** The ladder
  cell reads `GA.PK.CD-MA3.4d` and `GA.PK.CD-MA7.4a`; both parse as
  `GA.PK.CD-MA` with `3.4d` / `7.4a` falling out as residual prose. So
  `node_standards_parsed` holds a code that does not exist and loses two that
  do. Patched in the §3.5.1 export only (`scripts/export_anchor_standards.py`);
  the parser is unchanged and the same shape will truncate again.
- **`normalize_code` glues trailing prose onto a code.** The gaps sheet writes
  `IN.PK.M1.3 not numbered`; spaces are stripped, so `standards` carries
  `IN.PK.M1.3notnumbered`. **15 rows affected** across IN, OR, PA, RI, WI. The
  text is present, under an unusable key. Worked around in the export only.
- **`MN.PK.M1.1` is not a standard.** `MN.PK.M1.14` is, and its text ("up to at
  least 29") matches the annotation the author wrote in that same cell. Reads as
  a dropped digit. This is an authoring fix in the ladder, not a code change.
- **Whether the 452 other-state ladder codes were hand-read** like the TX and FL
  ones. If yes, they are clean labels for the states where Path B *does* have
  coverage (SC, ND, IN, MO, MD), which would let Path B's boost value be measured
  rather than assumed. Small n, cheap check.
- **§11 scope** — closure checking and natural-language query. Not scheduled.

## 9.2 Resolved in rev 4

6. **Unanchored nodes — anchor on the state codes already present.** §3.5.1.
   Needs a state→state model run to become real.
7. **Path B is one source with an audit attached.** §3 Path B, §6.
8. **Independence is defined explicitly.** §4. `scored_alignments` cannot
   corroborate Path A or Path C.
9. **Labels are clean.** Hand-read from state standards text. §0.

---

## 11. Beyond the candidate generator — recorded, not scheduled

Two ideas from John, kept here so they are not lost. Neither is in scope for the
current build.

### 11.1 The closure problem

Once the team reviews candidates and accepts a set, they have better anchors than
they started with — but **no way to know the accepted set is complete** without
reading the full state standards list, which is the labour this tool exists to
avoid. Accepting good candidates does not prove nothing was missed.

Sketch: after a review pass, take the *approved* alignments for a node and query the
state standards corpus for anything else that plausibly aligns to the same
underlying content. This is a second pass with strictly better input than the
first — the first pass anchors on CCSS codes an author wrote; this one anchors on
alignments an author has explicitly ratified.

This composes naturally with §3.5.1, which is the same inversion applied to nodes
that never had a CCSS anchor. Both replace "anchor on CCSS" with "anchor on
approved state codes." Worth noting they may be one mechanism, not two.

Open: whether closure is best served by the fine-tuned model, an API call over
standards text, or both with disagreement surfaced. The model is cheaper and
already tuned for this exact pairing; a language model reads qualifiers and prose
better. Undecided.

### 11.2 Natural-language query over the standards corpus

Independently useful and much simpler: expose the state standards database to
natural-language search. *"Find standards in grades 3–5 about decomposing
fractions; look for `subdivide` or `partition` as well as `decompose`."*

Value beyond the candidate generator: it serves the 12 undrafted PK–5 stems, where
there are no ladders and therefore no nodes to anchor on, so none of the four paths
apply. It also serves the untagged backlog directly (TX 106 at K–5).

This has no dependency on candidate generation and could be built at any point.
It is the smaller and more immediately useful of the two.