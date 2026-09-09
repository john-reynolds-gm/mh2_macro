# Handoff — MH2 Standards Coverage Audit Tool (rev 4)

Paste as the first message of a new conversation. Self-contained.
**Supersedes rev 3.** Every number was measured against `mh2.db` or the source
workbook, not estimated.

**Attach `mh2.db`.** Nothing here can be re-verified without it.

## Model recommendation

**Opus for design. Sonnet in Claude Code for implementation.** Strictly
separate. One view or one loader per session with an explicit do-not-touch list.
Six clean sessions on that pattern so far.

---

## 1. Where the project stands

A coverage audit answering "is every standard we owe tagged in some ladder
node?" React + FastAPI + SQLite. The standards-alignment Streamlit tool is
parked, not deleted.

**Premise:** the ladders are the tagging source of truth, not the spreadsheet.
`reconcile.py` reports 410 standards tagged in a ladder but not attached to that
stem in the workbook, and 25 filed under one stem in the workbook and another in
the ladder, against `concept_standards` at 1,636 rows.

**Second, sharper measurement:** of 765 PK–5 standards the writers marked as
tagged, **373 have zero tags in any ladder** — 49%. That is the audit's headline
finding and its justification.

Loader corrections are done. The view is fully specified below and **not
written**. §11 is the build order and §4–§7 are directly usable as the brief for
the first implementation session.

---

## 2. Decisions settled

| # | Decision | Outcome |
|---|---|---|
| D1 | Band as a stored column | Yes. On `grade_order`, not `standards`. |
| D2 | Render the writers' claim | Yes for Yes/blank. **`Partially` deferred.** |
| D3 | Undrafted stems | Read Red, explained verbally. No third state. |
| D4 | Off-grade-only rows | **Yellow.** |
| D5 | Loader corrections before the view | Done. |
| D6 | **Read-only or read-write** | **v1 is read-only.** Team decides after the demo. |
| D7 | Claim column label | **"Tagged in sheet."** |
| D8 | Flags rendering | **Per-tag icon in the Nodes cell**, not row-level. |
| D9 | Row detail | **Inline expansion** in v1, not a side drawer. |

Consequences worth carrying:

- **D3 takes the stem-ID namespace mismatch off the v1 critical path.** PK–5
  nodes use short stem IDs (`MUL`, `ADD`, `COU`, `ANG`…) while `stem_map` uses
  long ones (`OE_MULDIV_WHOLE`, `NSS_COUNTING`…), so `nodes ⋈ stem_map` returns
  180 rows, all 6–9. `stem_map` is also stale — `OE_ADDSUB` reads undrafted
  while stem `ADD` has 27 nodes. Must be fixed before v2 `/suggest_nodes`
  (stem-scoped by design) and before the 6–9 run. Not before v1.
- **D4 means Yellow needs a reason list, not a colour enum.** v1 Yellow means
  "off-grade tags only." v2's scope check will also want to mean "someone look at
  this." Store reasons as a list per row so v2 adds a reason rather than
  redefining a colour.
- **D9 is a v1 choice, not a principle.** Inline keeps row grain and tag grain
  visibly the same object. A drawer becomes better once the detail grows, which
  `/suggest_nodes` will do.

---

## 3. Coverage model

Two grains.

- **Row grain (browse unit):** one row per standard, carrying the rolled-up
  colour.
- **Tag grain (evidence unit):** one record per `(standard, node)` pair with
  `node_id`, the node's grade(s), `grade_match`, and a note.

`grade_match` ∈ `on-grade` | `off-grade` | `n/a — leaf` | `unresolved`.

**`grade_match` is containment, not equality.** The ladder grade field is an
umbrella summarizing where consulted states place the content, not a placement.
Test: is the standard's own grade (`standards.grade`) inside the node's declared
range? No state enumeration anywhere.

Rollup:

- **Green** — ≥1 tag with `grade_match` of `on-grade` or `n/a — leaf`
- **Yellow** — ≥1 tag, none `on-grade` or `n/a — leaf`
- **Red** — zero tags

**Colour and flags are independent axes with separate filters.** Colour answers
"is this covered somewhere?" Flags answer "is any individual tag suspect?" A
standard well covered at G2 but also tagged on a node reading
`4, 5 (G3 for TX and AR)` is green with one flagged chip. Off-grade tags never
downgrade a well-covered standard. This removes an argument writers would
otherwise have.

`n/a — leaf` matters: leaf nodes are exactly where Other-Gaps standards belong,
so they must satisfy grade-match rather than fail it. 25 leaf nodes.

**The two tools read `node_grade` differently and that is correct.** The audit
reads it as a range (plausibility bound). The sequencing tool reads it as a
placement (which grade slices display the node). Containment would put one node
in four slices, wrong for placement. Do not unify them.

### Table

Columns: **ID · Text · Grade · Coverage · Nodes · Tagged in sheet**

- `Coverage` is a Green / Yellow / Red chip.
- `Nodes` lists node IDs; a flagged tag carries a flag icon **next to the node
  it concerns** (D8).
- `Tagged in sheet` is a checkmark or dash. Tooltip: "the tagging workbook says
  this is tagged." The column exists precisely because the sheet can be wrong.

Tabs: CCSS / Texas / Florida / CA-non-CCSS / Other gaps.
Filters: Grade · Coverage · Flags · Tagged in sheet. Tab counts shown inline.

### Row expansion (D9)

Node IDs like `ADD-0001` are meaningless to writers. The expansion must situate
them. **Every field needed is already in the DB and populated** — no new
ingestion:

| Shown | Source |
|---|---|
| Stem name and domain | `stems.name`, `stems.domain` via `nodes.stem_id` |
| Concept/skill | `nodes.concept_skill` |
| Node text | `nodes.node_text` |
| Goal | `nodes.goal` |
| Declared grades + raw cell | `node_grade`, `nodes.grade_or_leaf` |
| `grade_match` and reason | computed |
| Specifications, misconceptions, considerations | `node_fields.field` |

Verified example — `ADD-0001` resolves to stem "Addition and Subtraction"
(domain "Operations and Equations"), concept/skill "Compose and decompose whole
numbers.", node text "Compose and decompose whole numbers in multiple ways.",
goal "Represent a set of objects by number in many ways". The node ID becomes a
small monospace label, not the identifying information.

**Gotcha:** `stems.name` contains embedded newlines from workbook headings —
`'Whole Numbers and \nBase Ten Structure'`. Collapse whitespace on display.

---

## 4. Denominator and band, measured

**Tab membership comes from `standard_tag_status.sheet`,** which after the loader
work holds all five sheets (3,859 rows). `standards` supplies grade and text.
`tagging:California-not CCSS` has zero rows in `standards` — all 17 lost
first-writer-wins to `California-All` — so `standards.source` cannot express that
tab. The tag-status loader resolves those 17 the other way, which is what we
want. `California-All` (550 rows) drops out by construction, not by an exclusion
clause.

**CCSS heading predicate:** four or more dot-separated segments with a digit in
the fourth. `K.CC.A.1` and `K.CC.B.4.a` are leaves; `K.CC.A` and `K.CC` are not.
**317 leaves, 339 headings.** 317 is exactly the anchor count in every Path C
prediction run, so the offline runs already used this set. Only the CCSS sheet
has headings. The predicate incidentally excludes all 246 `Grades 9-12` CCSS rows
because HS codes are three-segment (`HSA-REI.B.3`) — **keep the grain predicate
and the band filter separate**; that coincidence is not a design.

**Band** lives on `grade_order` (`PK5` / `6_9` / NULL). NULL is meaningful — it
is how a standard declares itself outside both runs. Do not coalesce it.

| Tab | PK5 | 6_9 | NULL |
|---|---|---|---|
| CCSS (leaves) | 191 | 126 | 0 |
| California-not CCSS | 3 | 2 | 12 |
| Florida | 184 | 159 | 294 |
| Texas | 246 | 189 | 90 |
| Other Gaps | 922 | 569 | 0 |
| **Total** | **1,546** | **1,045** | **396** |

**The PK–5 run is 1,546 standards.**

**Do not hardcode the denominator to these five sheets.** Tabs are filters, so a
full state corpus can load later as data.

Known data facts: CCSS has zero PK rows; PK exists only in Other Gaps. One
Florida duplicate falls in scope (`FL.6.DP.1.6`) — the export is row-order
matched, so the audit shows one row against two sheet rows. Two gaps-sheet codes
are mangled (§8).

---

## 5. Coverage, measured — PK–5

**These figures have alias resolution applied**, which is what §7 specifies for
read time and therefore what the rollup must do. An earlier revision quoted
exact-match-only figures (460 / 52 / 1,034); those are wrong for the view. Seven
rows resolve via alias, all seven land Green, all seven are on-grade.

| Tab | n | Green | Yellow | Red | has ≥1 tag |
|---|---|---|---|---|---|
| CCSS | 191 | 103 | 3 | 85 | 55.5% |
| CA-not-CCSS | 3 | 3 | 0 | 0 | 100% |
| Florida | 184 | 80 | 4 | 100 | 45.7% |
| Texas | 246 | 85 | 7 | 154 | 37.4% |
| Other Gaps | 922 | 196 | 38 | 688 | 25.4% |
| **Total** | **1,546** | **467** | **52** | **1,027** | **33.6%** |

**One definition, applied everywhere.** `eval/coverage_audit.py` currently prints
both — its coverage table is exact-match-only while its content-area block runs
post-resolution, so CCSS reads 89 Red in one and 85 in the other. Both are
correct; the script is inconsistent. The view must resolve once and report one
number.

### 33% is a drafting schedule, not a coverage crisis

**CCSS PK–5 by domain:** CC 10 rows / 100% · NBT 39 / 97% · OA 34 / 62% ·
NF 37 / 38% · G 21 / 29% · MD 50 / 26%.

**Florida PK–5 by domain:** NSO 56 / 89% · FR 21 / 38% · AR 38 / 34% ·
M 23 / 26% · GR 34 / 21% · DP 12 / 0%.

**Other Gaps PK–5 by top-level category** (`Category (new list)`, split on the
colon): Data 155 / 0% · Measurement 105 / 7% · Geometry 87 / 28% ·
Place value 87 / 77% · Operations whole numbers 81 / 32% · Money 80 / 1% ·
Fractions and decimals 79 / 27% · Patterns 66 / 0% · Counting 65 / 86% ·
Time and calendar 56 / 41% · Probability 18 / 0% · Geometric measurement
16 / 0% · Expressions/equations 15 / 20% · Operations with decimals 12 / 50%.

**Texas cannot be grouped this way.** TEKS codes are `TX.3.3A` — grade,
knowledge-and-skills number, breakout letter, with no domain segment. TX groups
only at tab level.

The pattern is identical everywhere: content areas with a drafted ladder are
near-complete; content areas with no ladder are near-zero. `stem_map` shows
`ladder_drafted = 0` for `SS_SPATIAL`, `SS_IDCLASSIFY`, `SS_COMPDECOMP`,
`MD_AREA`, `MD_LENGTH`, `MD_PERIMETER`, `MD_VOLUME`, `MD_DATA`, `SPR_SPR`.

Per D3 this is explained verbally, not modelled. **Say it out loud in the demo
before showing the number**, or the first thing writers see reads as an
indictment of work not yet scheduled.

### The claim axis — the actual work queue

| | has a tag | no tag |
|---|---|---|
| Tagged in sheet = yes | 399 | **366** |
| Tagged in sheet = blank | 120 | 661 |

**366 is the number to put in front of writers.** Three causes were hypothesized:
a stale claim, a reworded node (content-hash IDs, §18), or a mis-keyed code. One
is now measured — **all seven alias-recovered rows were claimed as tagged**, so
mis-keying accounts for exactly 7. The remaining 366 are stale claims or reworded
nodes. The converse cell — 120 with a tag and no claim — validates the premise
from the other direction.

Exact-match-only figures, if a report ever needs reconciling: 392 / 373 / 120 /
661. Per tab, yes / of-those-Red at exact match: CCSS 133 / 47 · CA-not-CCSS
2 / 0 · Florida 124 / 56 · Texas 140 / 68 · Other Gaps 366 / 202.

### Flags are a genuinely separate axis — now measured

**99 rows carry at least one flagged tag** (`off-grade` or `unresolved`), against
52 Yellow. So **47 rows are Green with a flag**: well covered somewhere, and also
tagged on a node whose grade does not support it. Under a rollup where any
off-grade tag downgrades the row, those 47 would read as problems. That is the
argument with writers §3 was written to avoid, and it is a third of the flagged
population.

Tag-grain `grade_match` distribution over PK–5: on-grade 924 · off-grade 133 ·
`n/a — leaf` 60 · unresolved 7.

**Acceptance check in its own right:** rows with ≥1 flagged tag = 99 and
Yellow = 52 must both hold. Their difference is not an error.

### Yellow, characterized

52 rows. Verified example: `TX.1.3C` ("compose 10 with two or more addends…",
grade 1) is tagged only to `ADD-0001`, whose grade cell reads
`Range – / PK - Within 10 / GK – Within 19 / G1+ – Application`, canonicalized to
`PK, K`. Grade 1 falls outside, so → off-grade. **Note this is arguably a ruling
problem, not a tagging problem** — "G1+ – Application" suggests G1 belongs in the
range. Expect some of the 52 to resolve by amending the grade worksheet rather
than by retagging. Others: `K.G.B.4` tagged only to `ANG-0002` (declared G1);
`FL.5.AR.2.2` tagged only to a node declared `6, 7`.

---

## 6. Read-only versus read-write (D6)

**v1 is read-only against the DB.** The team decides after seeing the prototype;
they cannot evaluate the tradeoff in the abstract.

**Nothing needs to change now to keep both paths open.** `node_standards`
already has `status` (`proposed` / `accepted` / `rejected`), `reviewed_by`,
`reviewed_at`, `source`, `caveat`. Read-write is a behaviour change, not a
migration. All 1,986 rows currently sit at `proposed` because they came from the
ladders.

**One naming hazard to write into the spec now.** If the tool ever writes,
`status='proposed'` will mean two different things: "parsed from a ladder,
unreviewed" and "a writer proposed this in the UI." `source` already
distinguishes them (`ladder_doc` vs `human`). **Never read `status` without
`source`.** Free now, painful later.

### The proposals file — a transport, not a source of truth

v1 ships an "add this node" affordance whose only effect is appending a row to
an append-only proposals file. No DB write. The coverage rollup is unaffected.

The file is a **work order**: node, standard, who, when, and the ladder file and
cell to edit. Someone applies it to the `.docx` — by hand or by script — and the
next rebuild picks it up from the ladder, where it belongs.

**It must not become a third source of truth.** The entire finding is that two
sources of truth produced 373 discrepancies. Ladders + workbook + an
authoritative tags CSV means the ladder goes stale, and the ladder is what
writers author from and what the curriculum ships from. The premise dies.

Requirements:

- The UI says "queued for the ladder," not "saved."
- **A queued proposal must never turn a Red row Green.** Otherwise the audit
  starts reporting on intentions.
- Proposals render visually distinct from tags.
- Nothing is wasted either way: if the team picks read-write, these rows become
  `node_standards` inserts with `source='human'`; if read-only, the file is
  already the work order.

---

## 7. Loader corrections — done

Five changes across four checkpointed sessions.

1. **`normalize_grade`** — `s.replace("Grade","")` turned `Grades 9-12` into
   `s 9-12` on 551 rows, and the fallback returned the raw cell, so the function
   had no failure mode. Fixed; unrecognized values surface in a review queue.
   Algebra branch parenthesized (it relied on `and` binding tighter than `or`).
2. **Band on `grade_order`** — column plus rows for `9`, `A2`, `GEO`, HS token.
   Ties out to §4 exactly.
3. **`standard_alias`** — `punct` 3,309 keys / 0 collisions; `nocluster` (CCSS
   jurisdiction only) 656 / 0. Built over the five tagging sheets only, **not**
   over all of `standards`. Resolution: exact → punct → nocluster, returning the
   tier. **Never rewrites `node_standards.standard_id`** — the code the writer
   typed is the evidence that ladder and corpus disagree, and it is what the
   export must show.
4. **Gaps sheet into `standard_tag_status`** — 2,368 → 3,859 rows, zero PK
   collisions. `Partially` still collapses to 0, deliberately.
5. **`rebuild.py` reporting** — band distribution, unrecognized grade tokens,
   alias counts and collisions, claim × coverage, mismatch reports.

**What the alias table is worth: 7 rows.** PK–5 recovery is 6 punct
(`3.NF.A.2.a`, `3.NF.A.2.b`, `3.NF.A.3.a`, `TX.4.3E`, `TX.4.3F`, `TX.5.3H`),
1 nocluster (`4.NBT.B.4`), 1,027 still Red. Keying failure is a rounding error
in this audit.

**Why `nocluster` is jurisdictional, not positional.** In CCSS the middle letter
is a cluster designator and the number is unique within the **domain** — there is
no `4.NBT.A.4` competing with `4.NBT.B.4`. Zero collisions across all 656 CCSS
standards. In state schemes the same-looking letter usually is not a cluster and
numbers restart inside each letter group: `LA.1.AR.A.1`, `.B.1`, `.C.1` are three
different standards. So the tier is gated on `jurisdiction = 'CCSS'`, drops the
segment at index 2 only, never the last segment. Two rounds of positional tuning
could not converge because position was standing in for a jurisdiction test.

---

## 8. Two open code items, small

Recommendation: one short session before the rollup. Leaving them means
explaining four wrong rows in the first demo.

**`expand_ranges` already works and is not reached from the ladder path.** Its
docstring scopes it to `lesson_metadata`. Against the eight range-shorthand
ladder codes, five expand cleanly into 16 real member standards, all in the
corpus:

```
1.NBT.B.2-2.c     -> 1.NBT.B.2, .2.a, .2.b, .2.c     4/4 resolve
2.NBT.A.1-1.b     -> 2.NBT.A.1, .1.a, .1.b           3/3
MD.4.NOS.C.7.a-d  -> MD.4.NOS.C.7.a .. .d            4/4
TX.1.2E-1.2G      -> TX.1.2E, TX.1.2F, TX.1.2G       3/3
TX.3.3F-G         -> TX.3.3F, TX.3.3G                2/2
```

The other three (`CA.7-12.A`, `CA.9-12.A`, `GA.PK.CD-MA`) are grade-span and
strand references, correctly left alone. Different in kind from aliasing: this
produces **real tags**, because the writer did tag those standards and the parser
did not unpack the shorthand. Net PK–5 effect is 2 Reds (`TX.3.3G`,
`MD.4.NOS.C.7.b`). One call site plus a test.

**Double-counting is not a risk.** `node_standards` has
`PRIMARY KEY (node_id, standard_id)` with `INSERT OR IGNORE`, so an expanded
member that already exists is dropped; and alias resolution only fires for
standards with no exact tag, so once expansion creates the exact tag the alias
path is never consulted. The two mechanisms cannot both count one pair.

**What does need checking is whether expansion replaces the range row or sits
beside it.** If `MD.4.NOS.C.7.a-d` survives after its members are inserted, there
is a tag pointing at a code absent from `standards` — invisible in the view,
since rows come from the denominator, but permanently stuck in the mismatch
report. Capture a baseline first
(`python3 eval/coverage_audit.py > eval/before_session_a.txt`), then:

```sql
-- unresolvable range-shaped tags: 8 before, expect exactly 3 after
SELECT ns.standard_id, COUNT(DISTINCT ns.node_id) AS nodes
FROM node_standards ns LEFT JOIN standards s ON s.standard_id = ns.standard_id
WHERE s.standard_id IS NULL AND ns.standard_id LIKE '%-%'
GROUP BY 1 ORDER BY 1;

-- must be empty; confirms nothing recreated the table without the PK
SELECT node_id, standard_id, COUNT(*) FROM node_standards
GROUP BY 1,2 HAVING COUNT(*) > 1;

-- siblings from one cell must land on the same node
SELECT standard_id, GROUP_CONCAT(node_id) FROM node_standards
WHERE standard_id IN ('MD.4.NOS.C.7.a','MD.4.NOS.C.7.b','MD.4.NOS.C.7.c',
                      'MD.4.NOS.C.7.d','TX.1.2E','TX.1.2F','TX.1.2G',
                      'TX.3.3F','TX.3.3G')
GROUP BY 1 ORDER BY 1;
```

The three surviving rows should be `CA.7-12.A`, `CA.9-12.A`, `GA.PK.CD-MA`.

**Invariants after Session A.** Grade rulings cannot create tags; range expansion
can. So: Red 1,027 → **1,025** (exactly `TX.3.3G` and `MD.4.NOS.C.7.b`),
Green 467 → **469**, Yellow unchanged at 52, sum still 1,546, and the
range-shorthand line in the mismatch report 8 → 3. **If Red drops by more than
two, report it rather than accepting it** — expansion is reaching codes that were
not measured.

**Two gaps-sheet standards are unreachable by construction.** The corpus holds
`WI.PK.B.EL.5notnumbered` and `PA.PK.2.4.PK.A.1notnumbered` — `normalize_code`
strips whitespace, so `WI.PK.B.EL.5 not numbered` welded into one token. These
are the two "duplicate" gaps codes rev 2 flagged; they are mangled, not
duplicated. Decision needed: strip trailing prose at load, or exclude unnumbered
placeholders from the denominator.

---

## 9. The mismatch reports

59 distinct ladder-written codes have no exact match in `standards`. 26 resolve
via alias, 33 do not. Both print to `data/reports/ladder_code_mismatches.txt` on
every rebuild.

The 33: range shorthand 8 (code, §8) · strand/domain-level reference 9 (nobody;
different grain) · genuine typo 6 (writers) · PK state code absent from corpus 6
(nobody) · CA code in no sheet 2 (nobody) · blocked by the welded-code defect 2
(code, §8).

Typos for the writer report: `7.NA.A.2` (likely `7.NS.A.2`), `7.NS.A.A.1.b`
(doubled cluster), `NA.A.2`, `RN.B.3` (likely `N-RN.B.3`), `GR.1.1`,
`VS.2.NS.1.g` (likely `VA.2.NS.1.g`).

Strand-level, not errors: `GA.8.PAR`, `GA.PK.CD`, `SC.PK.MTE`, `SD.PK.CD`,
`PA.M03.A`, `PA.M03.D`, `PA.M04.D`, `VA.7`, `CA.7-12.A`. Own heading so nobody
tries to fix them.

**Separately: five ladder codes resolve *exactly* to standards whose only source
is `all_states.csv`** — `NE.K.N.2.a`, `.b`, `.c`, `.f`, `VA.3.CE.2`. Nebraska and
Virginia are in the gaps sheet; those codes are not. Writers tagged beyond the
curated gap list. Not an error, arguably good work, but outside the denominator.
Belongs in the view as a finding type ("tagged to a standard we don't owe").

---

## 10. Process, learned the hard way

Three acceptance figures in the loader briefs were wrong, all the same way: a
number computed in an ad-hoc script whose predicate did not match the prose
written around it.

- **74 alias recoveries** — the diagnostic unioned node lists across colliding
  alias keys, so a lookup for `3.NF.A.3` returned nodes tagged to `.3.a`–`.d`.
  True figure 7.
- **12 mismatch rows** — that was the CCSS-nocluster subset, not "every code with
  no exact match." True figures 26 / 33.
- **171 claim-yes-and-Red** — the four-workbook-tab subset, before the gaps claim
  was consolidated. True figure 373.

Two standing rules:

1. **Acceptance criteria carry the literal predicate that produced the number, or
   carry no number at all** — just expected shape plus "report what you measure."
   A wrong target invites a session to tune until it matches, which is exactly
   how a positional heuristic got built and reverted.
2. **Every brief includes:** "if you cannot reproduce a stated figure, report the
   discrepancy rather than searching for a filter that hits it." That behaviour
   caught both the 74 and the 12.

**`eval/coverage_audit.py` is fixed.** The over-matching aggressive tier is gone;
it now reads the real `standard_alias` table and reports recoveries by tier, or
says so and reports zero if the table is absent. It also resolves the DB path
from `argv[1]` → `$MH2_DB` → `mh2.config.DB` → a short candidate list, prints
which file it opened, and connects **read-only via URI mode**. The original bug
was a hardcoded relative `mh2.db`, which `sqlite3.connect` silently *creates*
rather than erroring on, so the script built a blank database and died on the
first query. Read-only mode makes a wrong path fail loudly.

Its one remaining wart is the split definition described in §5 — the coverage
table is exact-match-only, the content-area block is post-resolution.

**The DB lives at `data/build/mh2.db`**, and `mh2.config` imports cleanly from
`eval/`. Useful for Session C.

Related, and the reason the alias table survived: **the collision rule caught the
bug.** Without it, 2,364 keys would have silently resolved to whichever sibling
won the insert and nothing would have looked wrong. Keep refuse-ambiguity-and-
report anywhere the pipeline resolves one key to one row.

---

## 11. Build order

1. ~~Grade normalization ruling, loaded into `node_grade`~~
2. ~~`rebuild.py` baseline row counts~~
3. ~~Loader corrections (grade fallback, band, alias, gaps claim, reporting)~~
4. ~~Design the coverage view~~ — this document
5. **Session A** — `expand_ranges` wiring + welded gaps codes (§8)
6. **Session B** — coverage rollup as a query layer, no UI. Real counts first.
7. **Session C** — FastAPI, read-only endpoints
8. **Session D** — React table, filters, inline expansion
9. **Session E** — proposals file and the add-node affordance (§6)
10. Demo, then the team decides D6
11. v2, in this order: category→stems hints (free, human-authored),
    `/suggest_nodes`, row-level scope check

---

## 12. Still open

**Design, decidable during the build:**

1. Whether the audit surfaces **cross-stem relocations as a distinct finding
   type.** Rev 2 decided yes (25 rows, the main mechanism by which the workbook
   goes stale); needs UI. Without it, a relocated standard reads Red while
   sitting tagged under another stem.
2. Whether to surface `FL.6.DP.1.6` and the two welded gaps codes, or absorb them
   silently.
3. How "tagged to a standard we don't owe" appears (§9).

**Elsewhere:**

4. **The 21 blank-grade nodes are a ladder-completeness question, not a tool
   problem.** 9 in `MH2_6A1_NumberSystem_IrrationalRealNumbers` (over half that
   ladder's 17 nodes), 5 in `MH2_6A1_NumberSystem_Integers and Rationals`, 3 in
   `MH2_PK5_Measurement and Data_Time`, the rest in
   `MH2_6A1_One-Variable Equations`. One message to one writer in one band. They
   render `unresolved` until then, which is the honest answer.
5. ~~Worksheet row 84~~ — **resolved, no action.** The two added rows landed; a
   rebuild confirms `ruled` 306 / `leaf` 26 / `unresolved` 0 /
   `no_grade_field` 21, `node_grade` at 535. Neither affected PK–5 coverage
   (both were `7, 8`, i.e. 6–9 band), which is why §5 did not move.

   Row 84 (`4, 5 Leaf for G3 TX / G5 content is leaf for G4 FL, G3 TX?`) is
   **deliberately `is_leaf` FALSE with `default_grades = 3, 4, 5`.** It is main
   content at G4–G5 and a leaf only for G3 TX and possibly G4 FL. Because
   grade-match is containment, widening the range to include G3 makes a G3 TX
   standard read on-grade without claiming the node is a leaf — which
   `is_leaf` TRUE would have, at every grade. The narrower, more accurate
   encoding. Rev 2 flagged this as a defect by inferring from the raw text; it
   was a decision. **Do not "fix" it.** Known tradeoff: a CCSS G3 standard tagged
   there also reads on-grade, so the bound is weaker than the truth for
   non-TX jurisdictions. Acceptable — grade-match is a plausibility bound, not a
   placement claim, and the prose note carries the nuance for the writer.
6. **Revisit `ADD-0001`-style rulings** once Yellow is visible (§5).
7. `node_standards` 1,986 vs `node_standards_parsed` 1,954. PK is
   `(node_id, standard_code)` with `INSERT OR IGNORE`, so a code in two cells of
   one node keeps the first and drops the second annotation. 32 rows. Check
   whether any dropped row carried `partial`/`exceeds`.
8. Four `stem_map` rows have no workbook stem (MD_LENGTH, MD_AREA, MD_VOLUME,
   MD_DATA). Inert until those ladders exist.
9. Whether `mh2_category_to_stems.csv` covers 6–9 categories. If not, the 6–9 run
   loses its cheapest Red feature.
10. **6–9 domain-grouping source** (equivalent to the PK–5 workbook). John
    supplying.
11. Two audit runs, same structure: **PK–5 first, then 6–9.**
12. **Budget.** Baseline was $295.57 of $400 before a significant amount of
    Claude Code work, plus six sessions since. No current figure. Check Claude
    Platform spend.

---

## 13. v2, for context

**Scope check, replacing the old Yellow.** `node_standards_parsed` relation
breakdown is aligned 1,933 / exceeds 12 / partial 9 — writers are not authoring
scope distinctions, so there is no cheap seed. v2 gets a row-level "check scope"
button: one API call comparing the state standard's text against the CCSS
standard it maps to, returning the delta in prose ("gap is complex solutions"),
cached on the code pair, presentation-only. The delta text was always the
deliverable; the colour was never the point. Free in the meantime: 613 rows carry
tagging-step notes in `standard_tag_status.note_tagging` — render as a chip, no
model.

**Red suggestions: stem-scoped API call, not retrieval.** A writer can tell which
stem an untagged standard belongs in; the annoying part is finding the node. So
`POST /suggest_nodes` with `{standard_code, stem_id}`: the server assembles the
standard's text and grade plus every node in that stem and asks for a ranked
short list with one-line reasons. Stems run 9–42 nodes, so it fits one prompt.
**Suggestions never write** — the writer clicks one, creating a
`node_standards` row with `status='proposed'`, `source='human'`, `reviewed_by`.
Cache on `(standard_code, stem_id, stem_content_hash)`. Optional.

This inverts retrieval-over-a-corpus into ranking-within-a-known-set, which is
why the direction asymmetry stops mattering: the reach table gives TX 45% and FL
58% at top-10 for `ccss_to_state`, against 98% and 100% the other way.
Structural — a state standard's top-10 CCSS list is short and focused, while a
CCSS standard's top-10 state list picks ten out of a whole state corpus.

**Requires** the stem-ID namespace fix (§2) and resolves D6.

---

## 14. `all_states.csv` has no consumer in this tool

Traced every reader. Two roles, neither load-bearing.

1. **Corpus text in `standards`** — 14,945 rows, 79% of the table, and **none
   appear in any tab.** Loader order is tagging sheets → gaps → all_states,
   first-writer-wins, and `load_gaps_sheet` inserts gap rows with their own text
   and grade before all_states runs. Every code in the denominator already has
   text from the tagging workbook. These exist for the full-state-audit tail only.
   The alias work excludes them — an alias derived from an out-of-tab standard
   could resolve a ladder code to a row the audit never displays.
2. **`standard_lessons` for Path A** — 84,209 alignment-guide-derived lesson
   pairings feeding Jaccard overlap in `candidates.py`. That is candidate
   generation, i.e. v2 Red, which `/suggest_nodes` replaces.

`all_states_debug.csv` is therefore not blocking. Point `config.ALL_STATES_CSV`
at it whenever convenient (one line, strictly better file) and don't spend time
on its remaining issues. `gap_overlap_by_state.py` already reads the debug file.

---

## 15. Codebase notes

- **`rebuild.py` deletes the DB** (`config.DB.unlink()`). Back up before running
  if any review decisions live in `node_standards.status`.
- **`parse_node_standards.py`** turns the `Notes related to Standards, Grade, or
  Leaf` cell into codes with state attribution and a `relation`, plus absence
  assertions and labelled residual prose. 329 cells, 1,954 codes, 8 absence
  assertions, 469 residual fragments. Includes sibling-shorthand recovery
  (`TX.3.3A` + `B`; `VA.3.NS.2.a` + `and 2.b`).
- **`reconcile.py`** — Tier 1 exact set arithmetic between workbook-tagged and
  ladder-tagged per stem; Tier 2 cross-stem relocations. Compares
  `concept_standards`, not `standard_tag_status`, so it is not the gap audit, but
  the pattern transfers.
- **Ladder comments: leave them alone.** On the `.docx` path, comment anchors
  live outside `Paragraph.text`, so `read_docx` never sees them — confirmed by 93
  distinct grade strings in the DB, none containing `[^c`. The canonicalizer
  keeps its anchor-strip rule since new ladders arrive with new comments.
  `source_key` collapses everything outside `[a-z0-9]`, so node IDs are safe.
- **When the grade worksheet is regenerated**, generate it through
  `ingest_ladders.read_docx`, not a markdown export. The worksheet's `count`
  column is informational and **must not be an acceptance target** — it came from
  a markdown extraction while the pipeline reads via `read_docx`, and the two
  split multi-line and anchored cells differently. Totals agree at 332;
  per-raw-value attribution does not.
- **Grade ruling conventions:** `state_overrides` is empty by design (state
  extensions folded into `default_grades`, prose kept in `notes`); no override
  behaviour to implement in either tool. `ruling_type` and `needs_writer_review`
  are stale pre-consultation artifacts, ignored everywhere. Grade 9 always maps
  to `A1`. `is_leaf` wins unconditionally — five leaf rows also carry grades,
  which are annotation, and a leaf node must never fail grade-match.
- **Leaf-ness and grades interact in two opposite, both-intentional ways.** Where
  `is_leaf` is TRUE, grades are annotation and leaf wins (five rows). Where a
  node is core content at some grades and a leaf only for particular states, the
  ruling stays `is_leaf` FALSE and the leaf grades are folded into
  `default_grades` instead, with the prose kept in `notes` — see §12.5. Both
  patterns are correct; neither is a defect. A raw grade cell naming a leaf does
  not by itself imply `is_leaf` TRUE.
- **Span-width distribution**, cell-weighted over ruled nodes: width 1 = 163,
  2 = 102, 3 = 15, 4 = 18, 5 = 4, 6 = 3. **87% at two grades or narrower**, so
  the containment bound actually excludes things. The wide tail is 6–9 open-ended
  prose (`6+`, "Any time after these skills are introduced…"), all ruled
  `6, 7, 8, A1`, where grade-match is a no-op on ~13 cells. Acceptable and honest.
- **Path C, context only.** CA is quarantined as a target state in every
  direction — its target corpus in all three runs is the 14-code CA-not-CCSS set,
  so no `*→CA` prediction can be right except by coincidence. CA as an *anchor*
  is kept. TX and FL proceed. Not needed for v1.

---

## 16. Output

Every sheet in the tagging workbook has an empty **`Notes from post-ladder check
step`** column, created for this audit. Export target is predetermined:
per-sheet, row-order-matched, paste-in-place, filling that column plus a coverage
column. The tool is the source of truth; the export is a convenience. The
proposals file (§6) is a second, separate export shape.

---

## 17. Hosting

Not blocking. Run locally and screen-share for the first round of writer
feedback. When needed, for 5–10 users:

- **Cloudflare Tunnel + Cloudflare Access** — best low-effort option. App stays
  on a machine John controls, no inbound ports, email-OTP or SSO with a small
  allowlist. Often easier for IT to approve *because* nothing is exposed. Verify
  current free-tier seat limits.
- **Tailscale** — simplest, but device-bound.
- **Render / Railway / Fly** — easy deploy, but John would be hand-rolling auth,
  which is the part to avoid.

What matters: don't write your own auth, keep the DB restorable, make sure
someone besides John can get in.

---

## 18. Principles carried forward

- **AI-generated rationale is presentation-only.** Never contributes to scoring
  or evidence paths. Architectural constraint, not a preference.
- **Independence of evidence paths is enforced explicitly.**
  `scored_alignments.csv` was produced by auditing `learnosity_combined.csv` with
  the Path C model, so it cannot corroborate either.
- **Absence assertions are CCSS-scoped** and never suppress state candidate
  generation. A node CCSS omits is exactly where a state standard is most likely
  to exist — that is the definition of a leaf.
- **Shared CCSS codes across stems are pairings to consider, not conflicts.**
- **Node IDs are content-hash based**, so a reworded node surfaces as deleted +
  new rather than silently carrying approved alignments forward. Deliberate, and
  one of the three plausible causes of a claim-yes-and-Red row.
- **Dispositions are durable**, keyed to `(standard_code, node_id)` with the
  node's content hash and the standard's text hash, since the latter does not
  self-invalidate.
- **Never read `status` without `source`** (§6).
- **The tool must not become a third source of truth** (§6).
- **Refuse ambiguity and report it.** Never resolve one key to one row by picking
  a winner.
- **Resolution without reporting hides the defect.** An alias that silently
  resolves `3.NF.A.2a` forever means the ladder never gets corrected and the
  export keeps showing the writer their own misspelling. Every recovery mechanism
  emits a list.
- **Validate against project files, not handoff summaries.** Rev 3 got the
  three figures in §10 wrong. Rev 2 had the denominator, the in-scope rule, and
  the keying-failure volume wrong. Rev 1 had the Yellow volume, the CCSS heading
  count, the denominator, and the span-width table wrong.
