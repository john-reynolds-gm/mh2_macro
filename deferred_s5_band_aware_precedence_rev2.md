# DEFERRED.md — §5 addition, rev 2

**Supersedes rev 1 in full.** Rev 1 contained a measurement that was wrong, and
the wrong measurement was its stated rationale. Do not merge the two; replace.

---

## §5.x Band-aware precedence in stem attribution

**Status:** Closed. Adopted and implemented.

**Ruling.** A same-band candidate beats a cross-band candidate regardless of
route. Within the same band, `concept_standards` still beats the category
route. The cross-band loser is written to
`data/reports/stem_attribution_band_flips.txt` and never silently dropped.

### What it replaces

B.2's Call 1 band-scoped the category route only. That was sufficient while
`concept_standards` was written from the PK–5 workbook alone, which made the
category route the only route capable of a cross-band hit. Loading the G6
workbook removed that accident.

### What it fixes

6–9 ladders cross-reference grade-5 standards as prerequisite content, chiefly
via `RP_COORDINATE_SYSTEM`, `EE_GENERAL_EXPRESSIONS`,
`EE_ONE_VARIABLE_EQUATIONS_DEG_1`, `RP_PERCENTS`, and `GM_CONSTRUCTIONS`. A
prerequisite reference is not an ownership claim: the G6 Coordinate System
section citing grade-5 geometry does not mean Coordinate System owns grade-5
content. Under §1 the stem roster is the unit of work, so without this ruling
those standards move out of the band whose writers are supposed to audit them.

### Measured population — 12 flips

Predicate: standards whose `stem_ids` or `ladder_status` change under the rule,
printed by the Step 1 session from `build_stem_attribution` before and after.
All twelve are `band=PK5`.

| Standard | Demoted candidate | Retained | Status change |
|---|---|---|---|
| `5.G.A.1` | `RP_COORDINATE_SYSTEM` | `SPA` | drafted → undrafted |
| `5.G.A.2` | `RP_COORDINATE_SYSTEM` | `SPA` | drafted → undrafted |
| `5.OA.B.3` | `RP_COORDINATE_SYSTEM` | `STR` | drafted → undrafted |
| `FL.5.AR.2.2` | `EE_GENERAL_EXPRESSIONS` | `EXP` | drafted → undrafted |
| `FL.5.GR.4.1` | `RP_COORDINATE_SYSTEM` | `SPA` | drafted → undrafted |
| `FL.5.GR.4.2` | `RP_COORDINATE_SYSTEM` | `SPA` | drafted → undrafted |
| `OK.5.A.2.2` | `EE_ONE_VARIABLE_EQUATIONS_DEG_1` | `EXP` | drafted → undrafted |
| `TX.5.8B` | `RP_COORDINATE_SYSTEM` | `SPA` | drafted → undrafted |
| `TX.5.8C` | `RP_COORDINATE_SYSTEM` | `SPA` | drafted → undrafted |
| `IN.5.G.1` | `GM_CONSTRUCTIONS` | `IDE` | none (undrafted → undrafted) |
| `OK.5.GM.1.1` | `GM_CONSTRUCTIONS` | `IDE` | none (undrafted → undrafted) |
| `IN.5.NS.4` | `RP_PERCENTS` | `FRA` (category) | undrafted → **drafted** |

### Rationale, corrected

**Eleven of the twelve have a same-band alternative inside `concept_standards`
itself.** The winning route already held a PK–5 stem alongside the 6–9 one;
the ruling drops the cross-band ID from a multi-candidate list. That is noise
removal, not arbitration between routes, and it is the strongest possible case
for the change — the same-band owner is unambiguous and already present.

**One, `IN.5.NS.4`, demotes across routes**, from a cross-band
`concept_standards` candidate to a same-band category candidate. This is the
case the precedence form was designed for, and it is a single row.

### Why precedence rather than band-scoping `concept_standards`

Outright band-scoping would also drop cross-band hits where no same-band
alternative exists, turning attributed rows into unattributed ones. The
precedence form only demotes when there is something to demote to.

That population is non-empty and identified: `TX.5.8A`, `TX.5.4F`,
`VA.5.PFA.2.b`, `VA.5.PFA.2.d` hold cross-band candidates with no alternative
and are correctly untouched. Under band-scoping they would have gone
unattributed for no gain.

### Reconciliation with earlier figures — recorded, not resolved

The 12 is **not** a subset of the 14 flips the G6 session reported, nor of the 5
returned by a narrower predicate during design. `TX.5.8A` appears in both
earlier figures and does not flip.

Sufficient candidate explanation: the 14 were measured before §2.1 reversed the
inert-stem call, which added category-route candidates for stems lacking a
`stems` row and therefore changed which standards have same-band alternatives
available. Plausible, unverified, and not worth verifying. Recorded as a
discrepancy per the standing principle — do not reverse-engineer a predicate
that reproduces 14.

### Correction to rev 1 — the reason this section was rewritten

Rev 1 justified the ruling with a measurement stating that 0 of 592 standards
holding a `6_9` concept hit also held a `PK5` one, and concluded that the rule
could only ever demote across routes to a category candidate. **That
measurement was invalid and its conclusion was the reverse of the truth** — 11
of 12 demote within `concept_standards`.

The error: band was derived by joining `concepts.stem_id` to
`stem_map.stem_id`. `concepts.stem_id` carries the PK–5 short codes, and all 18
of them (`ADD`, `ANG`, `COM`, `COM2`, `COU`, `EST`, `EXP`, `FRA`, `IDE`, `MUL`,
`MUL2`, `ORD`, `PER`, `SPA`, `STR`, `SUB`, `TIM`, `WHO`) are absent from
`stem_map.stem_id`. They are reachable only through `stem_map.workbook_stem_id`.
Every PK–5 concept therefore resolved to `band=NULL` and no same-band pair could
be returned.

**Rule to carry forward: band for anything keyed on `concepts.stem_id` or
`nodes.stem_id` resolves through `stem_map.workbook_stem_id`, never through
`stem_map.stem_id`.** The two-namespace problem is not confined to the drafted
predicate; it reaches any query that asks a band question about a stem.

### Report path

The band-flip list writes to
`data/reports/stem_attribution_band_flips.txt`, distinct from
`data/reports/stem_attribution_conflicts.txt`, which
`scripts/report_stem_attribution.py` owns and rewrites band-blind. Sharing the
path was a silent-overwrite hazard: plausible conflict output would appear with
the band flips gone.

### Dependency

`DEFERRED.md` §1.1 — deferring the conflict report to v2 — holds because this
ruling is adopted. Its rationale is that a conflict is invisible to the writer,
and these flips demonstrably were not.

### Downstream consequence, open for Step 4

Nine of the twelve move `drafted` → `undrafted`. Colour is unaffected; tags live
in `node_standards` and do not move. The render will therefore show **Green rows
inside undrafted stems** — `5.G.A.1` reads Green, tagged in the Coordinate
System ladder, filed under `SS_SPATIAL`, which has no ladder. Correct, and
potentially unreadable without the tag expansion. Goes to Step 4 question 2
alongside `NSS_ORD`. Count pending.

---

## §5.y Roster membership in `stems.csv` — proposed, awaiting confirmation

Not yet a ruling. Written down so the decision is visible rather than implicit.

`PS_STATISTICAL_VARIABILITY` was removed from `stems.csv` on the grounds that
the stem is not planned for drafting. Verified costless: the string appears zero
times in any cell of either stem/leaf workbook, so nothing resolves to it and no
unresolved-name report is produced. `stem_map` is correct at 54 rows, 32 `6_9`
and 22 `PK5`.

**The criterion needs settling, because after §2.1 the two candidate criteria
diverge.** Undrafted is now a meaningful state — owner known, ladder missing.
Deleting a stem removes it from that population entirely: a standard whose only
route resolved there would read *unattributed* rather than *undrafted*, which is
the distinction the tick-column work exists to preserve.

Sixteen 6–9 stems currently read attributed-and-undrafted with zero nodes,
including most of the Functions roster, the polynomial and exponential
Expressions stems, and `RP_PERCENTS`. Several are Algebra 1 content unlikely to
be drafted soon. Under a planned-drafting criterion they would go too, and the
6–9 undrafted bucket would largely empty again.

**Proposed criterion:** roster membership, with deletion permitted only when the
stem name appears nowhere in either workbook — the test that made this removal
safe. Applies without needing to measure consequences case by case.

**Do not reintroduce `PS_STATISTICAL_VARIABILITY` from a backup.** Its absence
is deliberate.
