# Grade Sequencing Tool: v1 prototype contract (2026-09-30)

**This document is binding** for the three parallel implementers of the v1
prototype on branch `seq/v1-prototype`:

- **R** (read side)
- **W** (write side and API)
- **F** (frontend and demo bundle)

Each works in a separate clone. Where this contract and the rev 1 design docs
disagree, this contract wins for v1. The design docs are
`docs/seq_orientation_rev1.md`, `docs/seq_data_model_rev1.md` and
`docs/seq_build_plan_rev1.md`. Every deviation from them is listed in §1.3.

If something is ambiguous, pick the reading that keeps the three parts
compatible. Record it in your final report under "Contract ambiguities". Do
not edit this file and do not edit another implementer's file (§2).

Figures quoted here come from the data copy with `MAX(ingest_log.ts) =
'2026-09-09 15:11:11'`, with `node_grade_kind` loaded (511 rows). If your data
differs, re-measure with the same predicate and report both values. Do not
force the number.

---

## 0. Architecture in one picture

```
 mh2.db (ro) ──► mh2/grade_kind.py ─┐
                                   ├─► mh2/seq_read.py  (R: pure reads, badges, compare, hints)
                                   │
 mh2_seq.db ──► mh2/seq_store.py (W: all SQL on placement tables, revs, events)
                mh2/seq_reconcile.py (W: derived status, report CLI)
                mh2/seq_guardrail.py (W: pure arithmetic)
                mh2/seq_auth.py      (W: pure auth helpers)
                         │
                mh2/seq_service.py   (W: composes R + W; one function per route;
                         │                 opens/closes both DBs; no FastAPI import)
                seq_api.py           (W: FastAPI app + router; routes are one-liners
                         │                 over seq_service; static page routes)
                seq_static/          (F: index.html, app.js, app.css)
                         │    HttpApi ──► /api/seq/*
                         │    DemoApi ──► embedded JSON (single offline file)
                scripts/export_seq_demo.py (F: builds seq_demo_<grade>.html from R's functions)
```

**Where the overlay join happens.** The overlay answers "where else is this
node placed". R never opens `mh2_seq.db`. W never queries `mh2.db` directly;
it reaches it only through R's functions, which `seq_service` calls. The join
is done in `seq_service` in plain Python. W's `seq_store.placement_index()`
returns a `dict[source_key, list[PlacedRef]]`. That dict is passed into R's
`build_slice(..., overlay=...)` / `node_drawer(..., overlay=...)`, and R
attaches it to nodes. There is no cross-DB SQL, no `ATTACH`, and no FK.

**Why `seq_service.py` exists** (it is not in the data model): FastAPI cannot
be imported in the sandbox. Every route is therefore a one-line call into a
plain Python function that the tests can run end to end. It also makes "no
SQL in routes" structural: `seq_api.py` imports neither `sqlite3` nor any
`*_store`.

---

## 1. Decisions

### 1.1 Open rulings and gates

Every entry is **Provisional (Claude, 2026-09-30), confirm**.

| # | Decision for v1 | One-line reason |
|---|---|---|
| **O1** Ownership | `grade_sequence.owner` is advisory. It is set to the creator on create and editable by PATCH. Anyone authenticated can edit. When `whoami.user != owner`, the UI shows a non-blocking "You are editing *owner*'s sequence" banner. There is no sign-off state. Every write is attributed in `placement_event`. | Recommended default. History is kept, so shared editing is recoverable. |
| **O2** Modules vs topics | Modules only. `slot.label` covers an informal "Topic A". | Recommended default. Topics can be added later as an additive table. |
| **O3** Grade-builder logins | Same `MH2_AUTH_USERS` per-writer Basic credentials as the audit. No roles. | Recommended default. Nothing new to provision for a prototype. |
| **O4** DEFERRED §6 / R-H3 amendment | **Not applied by this work.** DEFERRED.md is an existing file and is not edited. The amendment text in orientation §2.4 remains John's to paste before pilot. | Implementers may not edit existing docs, and the prototype writes only to its own tables. |
| **O5** `student_facing_example` | Deferred. Not shown. | It is not stored in `mh2.db` (orientation §10 D5), and ingest is do-not-touch. |
| **O6** UI stack | Plain JavaScript, no build step, no framework, no CDN, no npm: `seq_static/index.html` + `app.js` (one classic script) + `app.css`. | John reads Python, not TSX. It matches the audit's pattern. The Node toolchain is unavailable here and unwanted on the corporate Mac. |
| **O7** In-grade rows with no kind row (out_of_band 9, blank 2) | Read state `unknown`, badge "kind pending", counted as owed, and exempt from `grade_changed` when a row later appears. No change to the grade_type branch. | Recommended default. It degrades gracefully. |
| **O8** Time units | No conversion. `period_estimate` is always in instructional periods. Day/lesson hints are shown verbatim and never prefilled. | Recommended default. Nothing in the repo defines a day or a lesson in periods. |
| **O9** Un-graded estimate on a multi-grade node; `part_of` | Shown as text, with no prefill (`value = null`). `part_of` is shown as "≤ high". The builder types the number. | Recommended default. The tool does not decide per-grade versus total. |
| **O10** Flagged vs Pairings | Shared-code badges have `tier: "pairing"` and are shown only when the **Pairings** toggle is on. **Flagged** filters on `tier: "structural"` only. | 22 of 35 G2 nodes carry a shared code. Flagged would pass almost everything. |
| **O11** Stated-link capture fix | Not done. `node_links` is shown as captured, with the label "as captured". | It is a pipeline change, and ingest is do-not-touch. |
| **O12** C/S band beyond Goal | Goal only. | Recommended default. Identical fields look like merged Word cells. |
| **O13** Goal gaps | Partly-filled C/S: show the single non-empty goal, with no marker. No goal at all: the C/S band shows "No goal in ladder" and is not collapsed (`goal_missing: true`). | Recommended default. |
| **Gate A** | Passed on defaults O1/O2. O4 is noted as outstanding for John. | Only the prototype's own tables are written. |
| **Gate B** | Passed on O6 = plain JS **without** the grade-builder walkthrough. The walkthrough questions are decided as follows. **Q1:** stems start collapsed when `slice.counts.chips > 100` (G6, G7). **Q2:** strips **wrap** and never scroll horizontally. **Q3** = O10. **Q4** = O12. **Q5** = O13. | This is a prototype for that walkthrough. The builder reacts to it instead of a paper mock. |
| **Gate K** | Satisfied on the data copy (`kind_source = 'grade_type'`, 511 kind rows). R still implements and tests the `fallback` path. | Production `mh2.db` may predate the rebuild. |
| **Gate P** | Out of scope. | Pilot. |

### 1.2 Other "Assumption (confirm)" items from the design docs, decided

All are **Provisional (Claude, 2026-09-30), confirm**.

| Item | Decision | Reason |
|---|---|---|
| One active placement per node per sequence | Kept, enforced by the partial unique index. | Same-year revisits are a later feature. |
| Period estimate per placement, not per slot | Kept. | Keeps time-by-level exact. |
| Super-stem = `stems.domain` | Kept. | `stem_map` does not join PK–5 stems. |
| `grade_changed` exemptions | `unknown → core/span/unconfirmed` and `unconfirmed → core/span` are silent. Every other change of read state raises. | Data model §6.2. |
| `TIME_MARK_MIN_COVERAGE` | 0.5 | `seq/analysis` tiering. |
| One active sequence per grade | The API refuses a second active sequence for a grade (409 `sequence_exists`). The schema still allows more. | v1 UI shows one sequence per grade. |
| Owed-and-unplaced | **Not a badge.** It is the "Unplaced only" filter. | As a structural badge it would flag the entire slice on an empty sequence (the build plan S10 concern). |
| `predecessor_unplaced` | `tier: "info"`, not structural. | Frequent while a sequence is being built. It is informative, not a problem. |
| Chevron at a module boundary | Moving a slot "up" from the first position moves it to the **end of the previous module**. Moving "down" from the last position moves it to the **start of the next module**. | Chevrons alone can then move anything anywhere, which makes keyboard-only use workable. |

### 1.3 Deviations from the rev 1 design docs (deliberate)

1. **Auth is not moved out of `review_api.py`.** The build plan's S3 "verbatim move" edits `review_api.py`, which is do-not-touch for this work. W instead writes `mh2/seq_auth.py` with a byte-for-byte copy of `_configured_writers`' body under the name `configured_writers`. A drift test compares the two function bodies via `ast` (§8.2). This is the alternative the build plan names.
2. **`seq_api.py` is its own FastAPI app** (`seq_api:app`, run with `uvicorn seq_api:app`). It is constructed exactly like the audit, `FastAPI(title=..., dependencies=[Depends(require_writer)])`, and it also exposes `router`. It is **not** included into `review_api.app` in v1, because that is a one-line edit to a do-not-touch file. Including it later is `app.include_router(seq_api.router)` and needs John's say-so. Because of the app-level gate, every route on `seq_api.app`, including the static page, is authenticated.
3. **Schema file:** a new `mh2/schema_placement.sql` (data model §2), **not** an append to `mh2/schema_seq.sql`. The review schema's reconcile contract is closed, and a separate file keeps `review_store.ensure_schema` and `seq_store.ensure_schema` independent. `mh2/schema_seq.sql` is left untouched. Tables still live in the same file, `config.SEQ_DB`. `config.py` is **not** edited. `seq_store` locates the schema file with `Path(__file__).with_name("schema_placement.sql")`.
4. **Module names:**
   - `mh2/seq_read.py` replaces `grade_slice.py`. It also holds what the data model split into `placement_status` successors and `seq_checks` badges, because those are reads over `mh2.db`.
   - `mh2/seq_reconcile.py` replaces `placement_status.py` + `reconcile_placements.py`.
   - `mh2/seq_guardrail.py` replaces `guardrail.py`.
5. **Route paths** are flattened, with the grade in the query string (`/api/seq/slice?grade=2`, not `/grades/2/slice`), so that HttpApi and DemoApi share one path table. The full list is in §4.
6. **Page URL** is `/seq/` (trailing slash), with relative asset URLs. `/seq` redirects there.
7. **Dev identity:** when auth is off, the writer name is the `X-MH2-User` request header if it is present and valid, otherwise `"local"`. When auth is on, the header is ignored.

---

## 2. File ownership

**Nobody edits a file owned by someone else. Nobody edits any pre-existing
repo file.** The only exception is `.gitignore`, which nobody needs to touch.
Each implementer may add new `tests/test_seq_*.py` files of their own, using
the prefixes below.

| File | Owner | Notes |
|---|---|---|
| `mh2/grade_kind.py` | R | Data model §5.2 adapter, plus the state constants (§3.1). |
| `mh2/seq_read.py` | R | Every read over `mh2.db` for the sequencer. |
| `tests/test_grade_kind.py`, `tests/test_seq_read*.py` | R | |
| `mh2/schema_placement.sql` | W | Data model §3 DDL, with the §3.4 additions. |
| `mh2/seq_store.py` | W | The only SQL against the placement tables. |
| `mh2/seq_reconcile.py` | W | Derived status (pure) and the report CLI. |
| `mh2/seq_guardrail.py` | W | Pure. |
| `mh2/seq_auth.py` | W | Pure, with no FastAPI import. |
| `mh2/seq_service.py` | W | Composition; imports `mh2.seq_read`. |
| `seq_api.py` | W | FastAPI app + router + static routes. |
| `tests/test_seq_store.py`, `tests/test_seq_reconcile.py`, `tests/test_seq_guardrail.py`, `tests/test_seq_auth.py`, `tests/test_seq_api_routes.py`, `tests/test_seq_service.py` | W | |
| `seq_static/index.html`, `seq_static/app.js`, `seq_static/app.css` | F | |
| `scripts/export_seq_demo.py` | F | |
| `tests/js/seq_app_test.js`, `tests/js/fixtures/*.json`, `tests/test_seq_frontend.py` | F | |
| `docs/seq_demo/seq_demo_g2.html` | F | A generated sample bundle, committed so John can double-click it. |
| `tests/test_seq_integration.py` | Integrator | §9 |
| `docs/seq_v1_contract.md` | Architect | Read-only for R/W/F. |

**Do-not-touch** (byte-identical at the end; the integrator checks this with
`git diff --stat <base>..HEAD`):

- `review_api.py`: the whole file, including the `FastAPI(... dependencies=[Depends(require_writer)])` line and the auth-off-when-unset behaviour.
- `config.py`, `entrypoint.sh`, `Dockerfile`, `fly.toml`, `requirements*.txt`.
- `app/` (Streamlit).
- `mh2/coverage.py`, `mh2/schema.sql`, `mh2/schema_seq.sql`, `mh2/review_store.py`, `mh2/reconcile_review.py`, `mh2/ladder_edits.py`, `mh2/node_lookup.py`, `mh2/ingest_ladders.py`, and every `mh2/load_*.py`.
- `mh2/grade_split_review.py`, `mh2/normalize.py`, `mh2/parse_node_standards.py`, `mh2/reconcile.py`, `mh2/candidates.py`, `mh2/rerank.py`.
- `scripts/rebuild.py`, `scripts/render_static.py`, `scripts/render_seq_prototype.py`, `scripts/report_period_estimates.py`, `scripts/report_cross_stem_links.py`, and every other existing script.
- `docs/**` except the files listed above; in particular `docs/prototype/*`, `docs/review/*`, the design docs, `DEFERRED.md`, and `HOSTING.md`.
- Every existing file in `tests/`.
- `data/**`. No implementer writes to the real `mh2.db` or `mh2_seq.db`. Tests use temp copies. Do not run `scripts/rebuild.py`.

**Allowed imports of existing code** (read-only use):

- `config`
- `mh2.node_lookup` (`build_stem_names`)
- `scripts.report_period_estimates` (`parse_period_notes`, `usable`, `Estimate`). It is importable as a namespace package from the repo root, as `tests/test_period_estimates.py` does.

Nobody imports `mh2.coverage`, `review_api`, `app.*`, or `scripts.render_*`.

**Python compatibility:**

- Code must run on **3.10** (sandbox) and **3.14** (production).
- Every module starts with `from __future__ import annotations`.
- Do not use `typing.Self`, `NotRequired`, `match` on 3.11+ features, `tomllib`, or `ExceptionGroup`.
- `TypedDict` from `typing` is fine.

---

## 3. Python API

The field lists below are exact. JSON produced from them uses the same key
names. "list" means JSON array, and "str|None" means string or `null`. Keys
are **always present**; an absent value is `null` or `[]`, never a missing key.

### 3.1 R: `mh2/grade_kind.py`

```python
STORED_KINDS  = ("core", "span", "state_extension", "unconfirmed")
READ_STATES   = STORED_KINDS + ("off_grade", "leaf", "no_grade", "unknown")
OWED_STATES   = ("core", "span", "unconfirmed", "unknown")        # counted in the inventory
BRIDGE_STATES = ("off_grade", "state_extension", "leaf", "no_grade")  # placing needs confirm_off_grade
GRADES        = ("PK", "K", "1", "2", "3", "4", "5", "6", "7", "8", "A1")  # = grade_sequence CHECK; excludes 'OUT'

def kind_source(con) -> str
    """'grade_type' iff sqlite_master has table node_grade_kind AND it has >= 1 row; else 'fallback'."""
def build_grade_kinds(con) -> dict[tuple[str, str], str]
    """(node_id, grade) -> kind for EVERY node_grade row (key set == node_grade).
    node_grade_kind.kind when a row exists, else 'unknown'. Fallback: all 'unknown'."""
def ruling_by_node(con) -> dict[str, dict]
    """node_id -> {raw_value, resolution, is_leaf, notes, ruling_type, states_mentioned,
    needs_writer_review}. Missing columns (pre-grade_type DB) -> None."""
def kind_for(node_id: str, grade: str, kinds: dict, ruling_by_node: dict) -> str
    """leaf (resolution=='leaf', wins) > no_grade (resolution=='no_grade_field')
    > kinds[(node_id, grade)] > 'off_grade'."""
```

Never re-derive a kind from `ruling_type`. Never import `mh2.load_grade_ruling`.

### 3.2 R: `mh2/seq_read.py`

All functions take an open **read-only** connection to `mh2.db`. They never
open `mh2_seq.db` and never write. They are pure apart from reading `con`.
Grades are validated with `normalize_grade`. An unknown grade raises
`ValueError(f"unknown grade {token!r}")`. An unknown `source_key` raises
`KeyError(source_key)`.

**Constants:**

```python
FIELD_ORDER = [  # (key, label) -- same order/labels as scripts/render_seq_prototype.py (copied, not imported)
  ("specifications","Specifications"), ("required_skills_cases","Required skills / cases"),
  ("standards_notes","Standards notes"), ("considerations","Considerations"),
  ("misconceptions","Misconceptions"), ("mathematical_models","Mathematical models"),
  ("strategies","Strategies"), ("terminology","Terminology"),
  ("leaves_to_include","Leaves to include"), ("intervention_notes","Intervention notes"),
  ("product_reference","Existing product reference"), ("additional_notes","Additional notes")]
MARKER_RE = re.compile(r"\s*\(\s*[A-Z][a-z]+\s*[-–—]?\s*done\s*\)\s*$", re.I)
COMPARE_MAX = 4
GRADE_LABELS = {"PK":"Pre-K","K":"Kindergarten","1":"Grade 1",...,"8":"Grade 8","A1":"Algebra 1"}
GRADE_SHORT  = {"PK":"PK","K":"K","1":"G1",...,"8":"G8","A1":"A1"}
```

**Functions:**

```python
def connect_ro(path) -> sqlite3.Connection
    """sqlite3.connect(f"file:{path}?mode=ro", uri=True); row_factory = sqlite3.Row."""

def normalize_grade(con, token: str) -> str
    """Accepts '2', 'g2', 'G2', 'Grade 2', 'k', 'pk', 'a1'. Returns a GRADES member or raises ValueError."""

def display_label(label: str) -> str
    """MARKER_RE stripped, whitespace collapsed. Display only; the raw label is used for grouping."""

def cs_id(stem_id: str, concept_skill: str) -> str
    """f"{stem_id}:{sha1(concept_skill.encode('utf-8')).hexdigest()[:8]}" over the RAW label.
    Stable URL/view-state id for a concept/skill."""

def ladders_last_read(con) -> str | None          # SELECT MAX(ts) FROM ingest_log

def list_grades(con) -> list[GradeInfo]
def build_slice(con, grade: str, overlay: dict[str, list[PlacedRef]] | None = None) -> Slice
def node_drawer(con, source_key: str, grade: str,
                overlay: dict[str, list[PlacedRef]] | None = None) -> Drawer
def compare_column(con, source_key: str, grade: str) -> CompareColumn
def compare_field_defs(con) -> list[FieldDef]
    """FIELD_ORDER, then any other node_fields.field present in the DB, sorted,
    label = key.replace('_',' ').capitalize()."""
def assemble_compare(columns: list[CompareColumn], field_defs: list[FieldDef], grade: str) -> Compare
    """PURE (no con). Algorithm in §5.5; mirrored verbatim in JS (DemoApi)."""
def build_compare(con, keys: list[str], grade: str) -> Compare
    """ValueError if len(keys)==0, len(keys) > COMPARE_MAX, or duplicates; KeyError on an
    unknown key (first unknown). = assemble_compare([compare_column(k) ...], compare_field_defs(con), grade)."""

def node_facts(con, grade: str, source_keys: Iterable[str] | None = None) -> dict[str, NodeFacts]
    """Facts for the given keys (all nodes if None). Unknown keys are simply absent
    (absence == orphaned for the caller)."""
def ordering_badges(positions: dict[str, tuple[int, int]],
                    facts: dict[str, NodeFacts]) -> dict[str, list[Badge]]
    """PURE. positions = source_key -> (module_position, slot_position), 1-based, for the
    sequence's active, non-orphaned placements. Output is keyed by placed source_key.
    For each placed P and each Q in facts[P]['predecessor_keys']:
      Q placed and positions[Q] > positions[P]  -> 'before_predecessor' (structural)
      Q not placed                              -> 'predecessor_unplaced' (info)
    Co-placed nodes (equal positions) never produce a badge. One badge per code per P,
    refs = the Q keys in seq order."""
def suggest_successors(con, *, source_key_seen: str, node_text_seen: str,
                       stem_id_seen: str | None, concept_skill_seen: str | None,
                       exclude_keys: set[str]) -> list[Suggestion]
    """Data model §6.4, at most 3, in rule order, de-duplicated, never including exclude_keys:
      1 'same_text_other_stem': a node whose source_key hash suffix (part after the first ':')
        equals source_key_seen's suffix -- that IS 'same normalized text' by construction of
        source_key(); ratio=1.0.
      2 'same_cs_similar': same stem_id, display_label(concept_skill)==display_label(concept_skill_seen),
        ratio >= 0.6.
      3 'same_stem_similar': same stem_id, ratio >= 0.8.
    ratio = difflib.SequenceMatcher(None, a.casefold(), b.casefold()).ratio() on node_text,
    rounded half-up to 3 dp. Within a rule: ratio desc, then node seq."""
def period_hint(con, node_id: str, grade: str) -> PeriodHint | None      # §3.2.1
```

**Semantics shared by slice, drawer and facts:**

- *in_grade*: the node has a `node_grade` row for the grade. This is the placement reading. Never read it as containment.
- *state*: `kind_for(node_id, grade, …)`.
- *owed*: `state in OWED_STATES`.
- *requires_confirm*: `state in BRIDGE_STATES`.
- *Slice C/S inclusion*: a `(stem_id, concept_skill)` pair (raw) is included iff it has at least one in-grade node, **leaves included**. The server always returns everything. Show and hide happen in the page.
- *Strip*: every node of an included C/S, ordered by `nodes.seq`.
- *Ordering*:
  - `super_stems` by `domain` (casefold; a NULL domain becomes `"(no domain)"` and sorts last);
  - stems by `stem_name` (casefold);
  - C/S within a stem by `MIN(seq)`;
  - nodes by `seq`.
- *Stem name*: `node_lookup.build_stem_names` (whitespace-collapsed).
- *Goal*: the single distinct non-empty `nodes.goal` in the C/S, whitespace-**preserved** (goals contain `\n`). If there is more than one distinct non-empty goal, raise `ValueError` (0 on real data). If there is none, `goal = None` and `goal_missing = True`.
- *Predecessors* (`NodeFacts.predecessor_keys`): the source_keys of nodes in the same `(stem_id, concept_skill)` with lower `seq` whose state for this grade is in `OWED_STATES`, in seq order.
- *Shared code*: a CCSS code (`node_standards_parsed.state IS NULL`) that the node shares with a node in a **different stem**, anywhere in the DB. It is grouped by partner stem (§3.3 `Partner`).
- *Shared lesson* (drawer and compare only; never a slice badge): `product_refs.lesson_id IS NOT NULL`, shared with a node in a different stem. Exclude refs whose `raw_ref` matches `r"TX\b|Bluebonnet|Maryland|Catalyst"` (case-insensitive), the same filter as `scripts/report_cross_stem_links.OTHER_PRODUCT`. Copy the regex; do not import.

#### 3.2.1 Period hint (data model §5.4, exact)

1. Parse `node_fields` rows with `field='additional_notes'`, in `ordinal` order, with `parse_period_notes()`.
2. Choose the kept estimates and the basis:
   - `kept` = the estimates with `e.grade == grade`, giving `basis = "grade_named"`.
   - If there are none and the node has exactly one `node_grade` grade, `kept` = those with `e.grade is None`, giving `basis = "single_grade_node"`.
   - If there are still none and the node has more than one grade, `kept` = the un-graded ones, giving `basis = "ungraded_multi_grade"`.
   - If `kept` is empty, return `None`.
3. Set `value = kept[0].low` only when all of these hold:
   - `len(kept) == 1`
   - `basis != "ungraded_multi_grade"`
   - `qualifier == "exact"`
   - `unit == "period"`
   - `"group_total" not in note`

   In every other case `value = None`.
4. `text` = `" · ".join(e.snippet for e in kept)`. `unit`, `qualifier`, `low` and `high` come from `kept[0]`. `n_estimates` = `len(kept)`.

### 3.3 Shared shapes (TypedDict-style)

```
PlacedRef   = {grade, sequence_id, sequence_title, module_id, module_title, placement_id}
Badge       = {code, tier, label, detail: str|None, refs: [source_key], partners: [Partner]}
              tier ∈ "structural" | "pairing" | "info"
Partner     = {stem_id, stem_name, codes: [str], nodes: [{source_key, node_id, in_grade: bool}]}
PeriodHint  = {text, value: float|None, unit: str|None, qualifier, low: float|None,
               high: float|None, basis, n_estimates: int}
GradeInfo   = {grade, label, short, ord, band: str|None, in_grade_nodes, owed_nodes, chips,
               sequence: SequenceSummary|None}      # R fills sequence=None; seq_service fills it
SliceNode   = {source_key, node_id, seq, node_text, grades: [grade], state, in_grade, owed,
               requires_confirm, resolution, grade_raw: str|None, states_mentioned: str|None,
               badges: [Badge], placed_elsewhere: [PlacedRef], period_hint: PeriodHint|None}
SliceCS     = {cs_id, label, label_display, goal: str|None, goal_missing: bool,
               in_grade_count, in_grade_count_excl_leaf, nodes: [SliceNode]}
SliceStem   = {stem_id, stem_name, concept_skills: [SliceCS]}
Slice       = {grade, grade_label, kind_source, ladders_last_read,
               counts: {in_grade_nodes, in_grade_nodes_excl_leaf, owed_nodes, leaf_in_grade,
                        concept_skills, concept_skills_excl_leaf, context_nodes, stems, chips},
               super_stems: [{domain, stems: [SliceStem]}]}
NodeFacts   = {source_key, node_id, node_text, stem_id, stem_name, concept_skill,
               concept_skill_display, cs_id, seq, source_file, state, in_grade, owed,
               requires_confirm, predecessor_keys: [source_key], period_hint: PeriodHint|None}
Suggestion  = {source_key, node_id, node_text, stem_id, stem_name, concept_skill_display,
               reason, ratio: float}
FieldDef    = {key, label}
```

**Slice badge codes** (from R). A chip gets them in this order:

| code | tier | when | label | detail |
|---|---|---|---|---|
| `span` | info | state = span | `span` | `"Also G1, G3, G4"` (other `node_grade` grades, short labels) |
| `unconfirmed` | info | state = unconfirmed | `unconfirmed` | `"Grade ruling not final: <raw_value>"` + `"; states: <states_mentioned>"` if set |
| `kind_pending` | info | state = unknown | `kind pending` | `"No kind recorded for this grade"` |
| `state_ext` | info | state = state_extension | `state ext` | `states_mentioned` or `notes` or `null` |
| `leaf` | info | state = leaf | `leaf` | `raw_value` |
| `no_grade` | info | state = no_grade | `no grade` | `"The ladder cell has no grade"` |
| `placed_elsewhere` | info | overlay non-empty | `"also G1, G3"` | `"Placed in G1 · <module_title>; G3 · <module_title>"` |
| `shared_code` | pairing | ≥1 partner stem | `"pairs: COM, EST"` (stem_ids) | `"Shares CCSS codes with Comparing (4), Estimating (1)"` |

`refs` is `[]` for all of these. `partners` is filled only for `shared_code`.
Off-grade chips get **no** kind badge; they are styled as context with grade
pills.

### 3.4 W: `mh2/schema_placement.sql`

This is data model §3 verbatim, with these changes (all additive):

1. The `placement_event.action` CHECK list gains `'slot_update'`, `'slot_move'` and `'slot_merge'`. `'move'` stays in the list but is not used; slot moves log `slot_move`.
2. No other column or table changes. `placement_schema_meta` version = 1.
3. The file header comment states that it is applied by `mh2/seq_store.ensure_schema` only, and that `mh2/schema_seq.sql` / `review_store` never read it.

### 3.5 W: `mh2/seq_store.py`

```python
ORDER_STEP = 1024
CALIBRATIONS = ("deep", "functional", "illuminating")
UNSET = object()     # sentinel for "field not supplied" in update functions

class SeqError(Exception):
    status: int; code: str; message: str; extra: dict
class NotFound(SeqError):        status = 404; code = "not_found"
class Conflict(SeqError):        status = 409  # code set per case
class StaleRevision(Conflict):   code = "stale_revision"   # extra = {"current_rev": int}
class Invalid(SeqError):         status = 422  # code set per case
class NeedsConfirm(Invalid):     code = "confirm_off_grade_required"  # extra = {"source_keys": [...], "states": {key: state}}

NodeSnap = {source_key, node_id, node_text, source_file, stem_id, concept_skill, state, requires_confirm}
           # built by seq_service from R's NodeFacts; the store copies it into *_seen columns

def connect(path) -> sqlite3.Connection        # isolation_level=None, row_factory=Row, foreign_keys=ON
def ensure_schema(con) -> None                 # runs schema_placement.sql (IF NOT EXISTS)

# sequences
def create_sequence(con, writer, grade, title, note=None) -> int      # Conflict 'sequence_exists' (extra sequence_id)
def get_sequence(con, sequence_id) -> dict                             # NotFound
def active_sequence_id(con, grade) -> int | None                       # lowest id, archived_at IS NULL
def update_sequence(con, writer, sequence_id, expected_rev, *, title=UNSET, owner=UNSET,
                    note=UNSET, archived=UNSET) -> None
def sequence_summaries(con) -> dict[str, SequenceSummary]             # grade -> active sequence summary
# modules
def create_module(con, writer, sequence_id, expected_rev, title, note=None) -> int   # appended
def update_module(con, writer, module_id, expected_rev, *, title=UNSET, note=UNSET) -> None
def move_module(con, writer, module_id, expected_rev, direction) -> None             # Invalid 'at_edge'
def remove_module(con, writer, module_id, expected_rev) -> None                      # Conflict 'module_not_empty'
# slots
def update_slot(con, writer, slot_id, expected_rev, label) -> None
def move_slot(con, writer, slot_id, expected_rev, *, direction=None, to_module_id=None) -> None
def merge_slot(con, writer, slot_id, expected_rev, into_slot_id) -> None
# placements
def place(con, writer, sequence_id, expected_rev, snap: NodeSnap, *, module_id,
          slot_id=None, after_slot_id=None, differentiation_note=None,
          confirm_off_grade=False) -> dict        # {placement_id, slot_id}
def place_group(con, writer, sequence_id, expected_rev, snaps: list[NodeSnap], *, module_id,
                confirm_off_grade=False, differentiation_notes: dict | None = None) -> dict
                # {slot_id, placement_ids, skipped: [{source_key, reason:'already_placed', placement_id}]}
def set_attributes(con, writer, placement_id, expected_rev, changes: dict,
                   period_hint_seen: str | None = None) -> None
                # changes ⊆ {calibration, period_estimate, differentiation_note}; value None clears
def co_place(con, writer, placement_id, expected_rev, target_slot_id) -> None
def ungroup(con, writer, placement_id, expected_rev) -> int   # new slot_id; Invalid 'already_alone'
def remove_placement(con, writer, placement_id, expected_rev, reason=None) -> None
def reattach(con, writer, placement_id, expected_rev, snap: NodeSnap) -> None
                # Conflict 'reattach_target_placed' if snap.source_key is active in this sequence
def acknowledge(con, writer, placement_id, expected_rev, snap: NodeSnap) -> None
# reads
def get_placement(con, placement_id) -> dict       # NotFound
def load_tree(con, sequence_id) -> SequenceTree
    # {sequence: row, modules: [{**module_row, position, slots: [{**slot_row, position,
    #  placements: [placement_row, ...]}]}]}  active rows only, ordered by order keys
def placement_index(con, *, exclude_grade: str | None = None) -> dict[str, list[PlacedRef]]
    # every active placement in every non-archived sequence, except sequences of exclude_grade
def list_events(con, sequence_id, limit=50, before_event_id=None) -> list[Event]
# saved views (owner-scoped; NotFound if the view is not the owner's)
def list_views(con, owner, grade=None) -> list[View]
def create_view(con, owner, grade, name, state: dict) -> View     # Conflict 'view_name_exists'
def update_view(con, owner, view_id, *, name=UNSET, state=UNSET) -> View
def delete_view(con, owner, view_id) -> None
```

**Rules:**

- **`writer` is always the second positional argument.** No function accepts a `*_by` or `*_at` parameter. Timestamps are `datetime.now(timezone.utc).isoformat(timespec="seconds")`.
- **Revisions.** Every mutating function except the saved-view functions runs in one `BEGIN IMMEDIATE … COMMIT`, with `ROLLBACK` on any exception.
  - `set_attributes` checks `placement.rev == expected_rev`.
  - Every other function checks `grade_sequence.rev == expected_rev` for the sequence that owns the row, then bumps `grade_sequence.rev` by 1.
  - Every updated row also gets `rev = rev + 1`.
  - A mismatch raises `StaleRevision` and changes nothing.
- **Check order:**
  1. existence (`NotFound`);
  2. revision (`StaleRevision`);
  3. business rules (`Conflict` / `Invalid` / `NeedsConfirm`).
- **Events.** Exactly one `placement_event` row per call, with changed fields only in `before_json`/`after_json`. `place_group` writes one `place` event per placement. A renumber adds one `renumber` event.
  - Action names: `sequence_create`/`sequence_update`/`sequence_archive`, `module_create`/`module_update`/`module_move`/`module_remove`, `slot_update`/`slot_move`/`slot_merge`, `place`, `co_place`, `ungroup`, `remove`, `set_calibration`/`set_period`/`set_note`, `reattach`, `acknowledge`, `renumber`.
  - When one PATCH changes several attributes, it writes one event per changed attribute. This is the only multi-event call.
- **Order keys:** data model §4 (append = max + 1024; swap on chevron; insert-between = `(a+b)//2`; renumber only that container, `1024*i`, when the gap is under 2).
- **Slot semantics:**
  - `place` without `slot_id` creates a slot of one, appended to the module or inserted after `after_slot_id`. With `slot_id` it appends into that slot (co-place at place time).
  - `move_slot(direction)` swaps with the adjacent slot in the module. At a boundary it crosses into the neighbouring module (§1.2). At the first slot of the first module ("up"), or the last slot of the last module ("down"), it raises `Invalid('at_edge')`.
  - `move_slot(to_module_id)` appends at the end of that module.
  - `merge_slot` appends all of the source slot's active placements, in order, to `into_slot_id`, then soft-removes the source slot.
  - `co_place` moves one placement to the end of the target slot.
  - `ungroup` puts the placement into a new slot inserted immediately after its current slot.
  - Any slot left with zero active placements is soft-removed in the same transaction.
  - A target in another sequence raises `Invalid('cross_sequence')`.
- **Soft delete only.** `DELETE FROM` appears only in `delete_view`.
- **Off-grade:** if `snap["requires_confirm"]` and not `confirm_off_grade`, raise `NeedsConfirm`. `place_group` checks all snaps first and lists every one that needs confirmation.
- **`already_placed`:** `place` raises `Conflict('already_placed', extra={placement_id})`. `place_group` skips those keys and reports them.
- **`period_hint_seen`** is written only when `period_estimate` is in `changes`. `seq_service` passes the current hint text.
- **Validation (`Invalid('invalid')`):** `calibration` ∉ `CALIBRATIONS` ∪ {None}; `period_estimate` < 0 or > 200; empty `title`/`name`; `direction` ∉ {up, down}; both or neither of `direction`/`to_module_id`.

```
SequenceSummary = {sequence_id, grade, title, owner, rev, n_modules, n_placements, updated_at}
View            = {view_id, grade, name, state: dict, created_at, updated_at}
Event           = {event_id, action, actor, at, module_id, slot_id, placement_id,
                   before: dict|None, after: dict|None}
```

### 3.6 W: `mh2/seq_reconcile.py`

```python
EXEMPT_TRANSITIONS = {("unknown","core"), ("unknown","span"), ("unknown","unconfirmed"),
                      ("unconfirmed","core"), ("unconfirmed","span")}

def derive_status(placement_row: dict, facts: NodeFacts | None) -> StatusInfo
    """PURE. facts None -> 'orphaned'. Else state_now = facts['state'];
    state_now != grade_kind_seen and (seen, now) not in EXEMPT_TRANSITIONS -> 'grade_changed';
    else 'ok'. relabelled (only when not orphaned) = concept_skill != concept_skill_seen
    or source_file != ladder_file_seen."""
StatusInfo = {status, state_now: str|None, relabelled: bool,
              relabel: {concept_skill_seen, concept_skill_now, ladder_file_seen, ladder_file_now}|None}

def main(argv=None) -> int
    """python -m mh2.seq_reconcile [--db PATH] [--seq-db PATH] [--out PATH]
    Writes data/reports/placement_reconcile.txt (data model §6.7 sections). Writes NO rows.
    Opens mh2.db via seq_read.connect_ro and mh2_seq.db read-only (mode=ro)."""
```

### 3.7 W: `mh2/seq_guardrail.py`

```python
COUNT_TARGET = {"deep": 0.25, "functional": 0.50, "illuminating": 0.25}
TIME_TARGET  = {"deep": 0.40, "functional": 0.45, "illuminating": 0.15}
TIME_MARK_MIN_COVERAGE = 0.5
def round_half_up(x: float, dp: int) -> float     # math.floor(x * 10**dp + 0.5) / 10**dp  (x >= 0)
def compute(placements: Iterable[dict]) -> Guardrail   # each: {calibration, period_estimate}
```

The formula is in §5.8. It is **authoritative**; the JS mirror must match it.
There is no pass/fail key: `set(result) & {"pass","fail","ok","status"} == set()`.

### 3.8 W: `mh2/seq_auth.py` (no FastAPI import)

```python
DEV_HEADER = "X-MH2-User"
DEV_NAME_RE = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")
def configured_writers() -> dict[str, str]      # body byte-identical to review_api._configured_writers
def auth_enabled() -> bool                      # bool(configured_writers())
def check_basic(username: str | None, password: str | None) -> str | None
    """Auth on: the username if it matches (secrets.compare_digest), else None. Auth off: 'local'."""
def resolve_writer(basic_username: str | None, dev_header: str | None) -> tuple[str, str]
    """(user, source). Auth on: (basic_username, 'basic') -- the caller has already verified it.
    Auth off: (dev_header, 'dev_header') if DEV_NAME_RE matches, else ('local', 'default')."""
```

In `seq_api.py`:

- `require_writer` wraps `check_basic` exactly the way `review_api.require_writer` behaves: a 401 with `WWW-Authenticate: Basic` when auth is on and the credentials fail.
- `current_writer(request, credentials) -> str` returns `resolve_writer(...)[0]`.
- Every mutating route has `writer: str = Depends(current_writer)`.

### 3.9 W: `mh2/seq_service.py` (the route layer's only dependency)

```python
@dataclass(frozen=True)
class Ctx:
    mh2_path: Path
    seq_path: Path
    @classmethod
    def from_config(cls) -> "Ctx": ...   # config.DB, config.SEQ_DB (resolved at call time)
```

Every function opens its own connections: mh2 via `seq_read.connect_ro`, seq
via `seq_store.connect` + `ensure_schema`. It closes them in `finally`.
Service functions raise `seq_store.SeqError` subclasses only. `ValueError` and
`KeyError` from R are translated: unknown grade or key becomes `NotFound`, and
bad compare input becomes `Invalid('invalid')`. **A `StaleRevision` raised by
any write gets `extra["sequence"] = build_sequence_view(...)`, the current
view, before re-raising.**

| Function | Returns | Route |
|---|---|---|
| `grades(ctx)` | `{grades: [GradeInfo], kind_source, ladders_last_read}` | GET /grades |
| `get_slice(ctx, grade)` | `Slice` (overlay = `placement_index(exclude_grade=grade)`) | GET /slice |
| `get_node(ctx, source_key, grade)` | `Drawer` (same overlay) | GET /node/{key} |
| `get_compare(ctx, keys, grade)` | `Compare` | GET /compare |
| `get_sequence(ctx, grade)` | `SequenceView` (`sequence: null` if none) | GET /sequence |
| `get_guardrail(ctx, grade)` | `{sequence_id, sequence: Guardrail, modules: [{module_id, title, guardrail}]}` | GET /guardrail |
| `get_attention(ctx, grade)` | `{sequence_id, items: [AttentionItem]}` | GET /attention |
| `get_events(ctx, sequence_id, limit, before)` | `{events: [Event], next_before: int\|None}` | GET /sequences/{id}/events |
| `create_sequence(ctx, writer, grade, title, note)` | `WriteResult` | POST /sequences |
| `update_sequence(ctx, writer, sequence_id, expected_rev, fields: dict)` | `WriteResult` | PATCH /sequences/{id} |
| `create_module(ctx, writer, sequence_id, expected_rev, title, note)` | `WriteResult` | POST /sequences/{id}/modules |
| `update_module(ctx, writer, module_id, expected_rev, fields)` | `WriteResult` | PATCH /modules/{id} |
| `move_module(ctx, writer, module_id, expected_rev, direction)` | `WriteResult` | POST /modules/{id}/move |
| `remove_module(ctx, writer, module_id, expected_rev)` | `WriteResult` | DELETE /modules/{id} |
| `update_slot(ctx, writer, slot_id, expected_rev, label)` | `WriteResult` | PATCH /slots/{id} |
| `move_slot(ctx, writer, slot_id, expected_rev, direction, to_module_id)` | `WriteResult` | POST /slots/{id}/move |
| `merge_slot(ctx, writer, slot_id, expected_rev, into_slot_id)` | `WriteResult` | POST /slots/{id}/merge |
| `create_slot_group(ctx, writer, sequence_id, expected_rev, module_id, source_keys, confirm_off_grade, differentiation_notes)` | `WriteResult` | POST /slots |
| `place(ctx, writer, sequence_id, expected_rev, module_id, source_key, slot_id, after_slot_id, differentiation_note, confirm_off_grade)` | `WriteResult` | POST /placements |
| `update_placement(ctx, writer, placement_id, expected_rev, changes: dict)` | `WriteResult` | PATCH /placements/{id} |
| `co_place(ctx, writer, placement_id, expected_rev, target_slot_id)` | `WriteResult` | POST /placements/{id}/co-place |
| `ungroup(ctx, writer, placement_id, expected_rev)` | `WriteResult` | POST /placements/{id}/ungroup |
| `remove_placement(ctx, writer, placement_id, expected_rev, reason)` | `WriteResult` | DELETE /placements/{id} |
| `reattach(ctx, writer, placement_id, expected_rev, source_key)` | `WriteResult` | POST /placements/{id}/reattach |
| `acknowledge(ctx, writer, placement_id, expected_rev)` | `WriteResult` | POST /placements/{id}/acknowledge |
| `list_views(ctx, owner, grade)` | `{views: [View]}` | GET /views |
| `create_view(ctx, owner, grade, name, state)` | `View` | POST /views |
| `update_view(ctx, owner, view_id, fields)` | `View` | PUT /views/{id} |
| `delete_view(ctx, owner, view_id)` | `{deleted: view_id}` | DELETE /views/{id} |
| `build_sequence_view(seq_con, mh2_con, sequence_id \| None, grade)` | `SequenceView` | (internal) |

`WriteResult = {sequence: SequenceView, result: dict}`. The `result` keys for
each route are in §4.

**How `build_sequence_view` is built** (the only place R and W data meet for a sequence):

1. `tree = seq_store.load_tree(...)`.
2. `facts = seq_read.node_facts(mh2, grade, keys_of_active_placements)`.
3. For each placement, compute `derive_status(p, facts.get(key))`.
4. `positions` = the non-orphaned placements' `(module.position, slot.position)`. Then `ob = seq_read.ordering_badges(positions, facts)`.
5. `overlay = seq_store.placement_index(exclude_grade=grade)` supplies `placed_elsewhere`.
6. Placement badges, in this order:
   - `orphaned`
   - `grade_changed`
   - `bridge`, when `status != orphaned` and `facts.requires_confirm`
   - `relabelled`
   - `ob[key]`
7. For each attention item (orphaned or grade_changed), call `suggest_successors` for orphans, with `exclude_keys` = the active keys in this sequence.
8. Guardrail = `seq_guardrail.compute` over the whole sequence and over each module.

In step 3, `place`, `reattach` and `acknowledge` build their `NodeSnap` from
`node_facts(mh2, grade, [source_key])`. A missing key raises `NotFound`.

---

## 4. HTTP API

- **Prefix:** `/api/seq`. All JSON. `seq_api.app` has the app-level auth gate.
- **Node identity in URLs** is the `source_key`. F always builds paths and query values with `encodeURIComponent(source_key)`, so `TIM:fe32bbacca33e365` becomes `TIM%3Afe32bbacca33e365`. The server receives it decoded. Source keys contain no `/` or `,` (verified: 0 rows), so a plain `{source_key}` path param and comma-splitting of `keys=` are safe.
- **Bodies are Pydantic models** with `model_config = ConfigDict(extra="ignore")`. A client-sent `placed_by`, `*_by` or `*_at` is silently dropped, and the stored value is always the authenticated writer.
- **"Absent vs null" in PATCH:** use `body.model_dump(exclude_unset=True)`. A key sent as `null` clears the field. An absent key is unchanged.
- **Error body** (every non-2xx the service raises): `{"detail": {"error": <code>, "message": <human text>, ...extra}}`. FastAPI's own validation errors return `{"detail": [ ... ]}`, a list. F treats a list `detail` as `{error: "invalid", message: "Invalid request"}`.
- **Status codes:**
  - 401: auth (gate).
  - 404 `not_found`.
  - 409: `stale_revision` (extra `current_rev`, `sequence`: SequenceView), `already_placed` (`placement_id`), `sequence_exists` (`sequence_id`), `module_not_empty`, `view_name_exists`, `reattach_target_placed`.
  - 422: `confirm_off_grade_required` (`source_keys`, `states`), `at_edge`, `already_alone`, `cross_sequence`, `invalid`.

| # | Method | Path | Query / body | 200 response | Service fn |
|---|---|---|---|---|---|
| R0 | GET | `/whoami` | — | `{user, auth_enabled, source}`, where source ∈ basic, dev_header, default | (seq_api, via seq_auth) |
| R1 | GET | `/grades` | — | `{grades, kind_source, ladders_last_read}` | `grades` |
| R2 | GET | `/slice` | `grade` | `Slice` | `get_slice` |
| R3 | GET | `/node/{source_key}` | `grade` | `Drawer` | `get_node` |
| R4 | GET | `/compare` | `grade`, `keys` (1–4, comma-joined) | `Compare` | `get_compare` |
| R5 | GET | `/sequence` | `grade` | `SequenceView` | `get_sequence` |
| R6 | GET | `/guardrail` | `grade` | `{sequence_id, sequence, modules}` | `get_guardrail` |
| R7 | GET | `/attention` | `grade` | `{sequence_id, items}` | `get_attention` |
| R8 | GET | `/sequences/{sequence_id}/events` | `limit`=50 (≤200), `before` | `{events, next_before}` | `get_events` |
| W1 | POST | `/sequences` | `{grade, title, note?}` | WriteResult, `result {sequence_id}` | `create_sequence` |
| W2 | PATCH | `/sequences/{sequence_id}` | `{expected_rev, title?, owner?, note?, archived?}` | WriteResult `{}` | `update_sequence` |
| W3 | POST | `/sequences/{sequence_id}/modules` | `{expected_rev, title, note?}` | WriteResult `{module_id}` | `create_module` |
| W4 | PATCH | `/modules/{module_id}` | `{expected_rev, title?, note?}` | WriteResult `{}` | `update_module` |
| W5 | POST | `/modules/{module_id}/move` | `{expected_rev, direction}` | WriteResult `{}` | `move_module` |
| W6 | DELETE | `/modules/{module_id}` | query `expected_rev` | WriteResult `{}` | `remove_module` |
| W7 | PATCH | `/slots/{slot_id}` | `{expected_rev, label}` (null clears) | WriteResult `{}` | `update_slot` |
| W8 | POST | `/slots/{slot_id}/move` | `{expected_rev, direction?, to_module_id?}` (exactly one) | WriteResult `{}` | `move_slot` |
| W9 | POST | `/slots/{slot_id}/merge` | `{expected_rev, into_slot_id}` | WriteResult `{slot_id}` | `merge_slot` |
| W10 | POST | `/slots` | `{expected_rev, sequence_id, module_id, source_keys[1..4], confirm_off_grade=false, differentiation_notes?: {key: note}}` | WriteResult `{slot_id, placement_ids, skipped}` | `create_slot_group` |
| W11 | POST | `/placements` | `{expected_rev, sequence_id, module_id, source_key, slot_id?, after_slot_id?, differentiation_note?, confirm_off_grade=false}` | WriteResult `{placement_id, slot_id, placed_elsewhere}` | `place` |
| W12 | PATCH | `/placements/{placement_id}` | `{expected_rev` (**placement** rev)`, calibration?, period_estimate?, differentiation_note?}` | WriteResult `{placement_id}` | `update_placement` |
| W13 | POST | `/placements/{placement_id}/co-place` | `{expected_rev, target_slot_id}` | WriteResult `{slot_id}` | `co_place` |
| W14 | POST | `/placements/{placement_id}/ungroup` | `{expected_rev}` | WriteResult `{slot_id}` | `ungroup` |
| W15 | DELETE | `/placements/{placement_id}` | query `expected_rev`, `reason?` | WriteResult `{}` | `remove_placement` |
| W16 | POST | `/placements/{placement_id}/reattach` | `{expected_rev, source_key}` | WriteResult `{placement_id}` | `reattach` |
| W17 | POST | `/placements/{placement_id}/acknowledge` | `{expected_rev}` | WriteResult `{placement_id}` | `acknowledge` |
| V1 | GET | `/views` | `grade?` | `{views}` | `list_views` |
| V2 | POST | `/views` | `{grade, name, state}` | `View` | `create_view` |
| V3 | PUT | `/views/{view_id}` | `{name?, state?}` | `View` | `update_view` |
| V4 | DELETE | `/views/{view_id}` | — | `{deleted}` | `delete_view` |
| P1 | GET | `/seq` | — | 307 redirect to `seq/` (relative) | (static) |
| P2 | GET | `/seq/` | — | `seq_static/index.html` | (static) |
| P3 | GET | `/seq/assets/{name}` | name ∈ {`app.js`, `app.css`} else 404 | file | (static) |

**Revisions.**

- `expected_rev` is `sequence.rev` for every write except W12, which uses `placement.rev`, and W1 and V1–V4, which have none.
- W12 does not change `sequence.rev`.
- Every write returns the full, fresh `SequenceView`, so F never has to guess.

**Static serving** uses explicit `FileResponse` routes. **Never
`app.mount(StaticFiles)`**, because mounted sub-apps skip the auth gate. Paths
resolve from `Path(__file__).parent / "seq_static"`.

**Route function names in `seq_api.py`** are the service function names
prefixed with `r_`, for example `r_get_slice`. Each route body is exactly one
statement: `return _run(seq_service.<fn>, CTX, ...)`. Here `_run` translates
`SeqError` into `HTTPException(e.status, {"error": e.code, "message":
e.message, **e.extra})`. `whoami` and the three static routes are the only
exceptions.

---

## 5. JSON shapes and worked examples

The examples are real G2 data, trimmed with `…`. Values marked *illustrative*
depend on placements.

### 5.1 `GET /api/seq/grades`

```json
{"kind_source": "grade_type", "ladders_last_read": "2026-09-09 15:11:11",
 "grades": [
  {"grade": "PK", "label": "Pre-K", "short": "PK", "ord": 0, "band": null,
   "in_grade_nodes": 24, "owed_nodes": 24, "chips": 27, "sequence": null},
  {"grade": "2", "label": "Grade 2", "short": "G2", "ord": 3, "band": null,
   "in_grade_nodes": 35, "owed_nodes": 33, "chips": 57,
   "sequence": {"sequence_id": 1, "grade": "2", "title": "Grade 2 sequence", "owner": "local",
                "rev": 14, "n_modules": 2, "n_placements": 4, "updated_at": "2026-09-30T18:02:11+00:00"}},
  "…"]}
```

**Per-grade figures:** `chips` = PK 27, K 57, 1 59, 2 57, 3 70, 4 82, 5 79, 6
106, 7 107, 8 67, A1 80. `in_grade_nodes` = 24, 45, 37, 35, 37, 61, 54, 81, 77,
37, 47. `owed_nodes` = 24, 44, 36, 33, 36, 61, 52, 79, 75, 35, 47 (measured
by the architect's scratch implementation of §3.2; R re-measures and reports
any difference).

### 5.2 `GET /api/seq/slice?grade=2`

```json
{"grade": "2", "grade_label": "Grade 2", "kind_source": "grade_type",
 "ladders_last_read": "2026-09-09 15:11:11",
 "counts": {"in_grade_nodes": 35, "in_grade_nodes_excl_leaf": 33, "owed_nodes": 33,
            "leaf_in_grade": 2, "concept_skills": 24, "concept_skills_excl_leaf": 22,
            "context_nodes": 22, "stems": 7, "chips": 57},
 "super_stems": [
  {"domain": "Number Systems and Structures",
   "stems": [
    {"stem_id": "WHO", "stem_name": "Whole Numbers and Base Ten Structure",
     "concept_skills": [
      {"cs_id": "WHO:7046b922",
       "label": "Compare and order numbers by using place value.",
       "label_display": "Compare and order numbers by using place value.",
       "goal": "Identify relationships between quantities (less than, greater than, or equal to)\nCompare multiple quantities to sequence them",
       "goal_missing": false, "in_grade_count": 2, "in_grade_count_excl_leaf": 2,
       "nodes": [
        {"source_key": "WHO:d65c6bed74a5ba1c", "node_id": "WHO-0010", "seq": 10,
         "node_text": "Compare whole numbers by using place value.",
         "grades": ["1","2","3","4"], "state": "span", "in_grade": true, "owed": true,
         "requires_confirm": false, "resolution": "ruled", "grade_raw": "1, 2, 3, 4",
         "states_mentioned": null,
         "badges": [
          {"code": "span", "tier": "info", "label": "span", "detail": "Also G1, G3, G4",
           "refs": [], "partners": []},
          {"code": "shared_code", "tier": "pairing", "label": "pairs: COM, EST",
           "detail": "Shares CCSS codes with Comparing (4), Estimating (1)", "refs": [],
           "partners": [
            {"stem_id": "COM", "stem_name": "Comparing",
             "codes": ["1.NBT.B.3", "2.NBT.A.4", "4.NBT.A.2", "K.CC.C.7"],
             "nodes": [{"source_key": "COM:03eff9b3fa1ec3aa", "node_id": "COM-0008", "in_grade": false},
                       {"source_key": "COM:fd33c1c6e7cba4fb", "node_id": "COM-0009", "in_grade": false},
                       {"source_key": "COM:8367f2788953e5d3", "node_id": "COM-0011", "in_grade": false},
                       {"source_key": "COM:58ec03661c5f2d1b", "node_id": "COM-0012", "in_grade": true},
                       {"source_key": "COM:86f92edc405ced41", "node_id": "COM-0013", "in_grade": false}]},
            {"stem_id": "EST", "stem_name": "Estimating", "codes": ["K.CC.C.7"],
             "nodes": [{"source_key": "EST:86d045ca0eb5e640", "node_id": "EST-0003", "in_grade": false}]}]}],
         "placed_elsewhere": [],
         "period_hint": {"text": "G2: Likely 2 instructional periods (heavy fluency practice)",
                         "value": 2.0, "unit": "period", "qualifier": "exact", "low": 2.0,
                         "high": 2.0, "basis": "grade_named", "n_estimates": 1}},
        {"source_key": "WHO:dc726b29e8f61f18", "node_id": "WHO-0011", "seq": 11,
         "node_text": "Order whole numbers by using place value.", "grades": ["1","2","3","4"],
         "state": "span", "in_grade": true, "owed": true, "requires_confirm": false, "…": "…"},
        {"source_key": "WHO:fdbd77e5556b420d", "node_id": "WHO-0012", "seq": 12,
         "node_text": "Compare decimal numbers by using place value.", "grades": ["4","5"],
         "state": "off_grade", "in_grade": false, "owed": false, "requires_confirm": true,
         "resolution": "ruled", "grade_raw": "4, 5", "states_mentioned": null,
         "badges": [ {"code": "shared_code", "…": "…"} ], "placed_elsewhere": [],
         "period_hint": null},
        {"source_key": "WHO:5a975d82b5ef07dd", "node_id": "WHO-0013", "seq": 13, "…": "…"}]}]}]},
  {"domain": "Measurement and Data", "stems": [
    {"stem_id": "TIM", "stem_name": "Time", "concept_skills": [
      {"cs_id": "TIM:c22eed5f", "label": "Tell and write time to the nearest minute.", "…": "…",
       "nodes": [
        {"source_key": "TIM:fe32bbacca33e365", "node_id": "TIM-0010", "grades": ["2","3"],
         "state": "unconfirmed", "in_grade": true, "owed": true, "grade_raw": "3 (2 for some states)",
         "badges": [{"code": "unconfirmed", "tier": "info", "label": "unconfirmed",
                     "detail": "Grade ruling not final: 3 (2 for some states)", "refs": [], "partners": []}],
         "period_hint": {"text": "Likely 1 instructional period", "value": null, "unit": "period",
                         "qualifier": "exact", "low": 1.0, "high": 1.0,
                         "basis": "ungraded_multi_grade", "n_estimates": 1}, "…": "…"}]}]}]}]}
```

- Context chips are 21 `off_grade` + 1 `no_grade` = 22.
- In-grade states for G2: core 13, span 14, unconfirmed 6, leaf 2.
- The two leaves are `COU-0024` and `MUL-0004`.
- The `placed_elsewhere` entries use the `PlacedRef` shape. An example (*illustrative*): `{"grade":"3","sequence_id":2,"sequence_title":"Grade 3 sequence","module_id":7,"module_title":"M1 Place value","placement_id":31}`.

### 5.3 `GET /api/seq/node/WHO%3Ad65c6bed74a5ba1c?grade=2` (Drawer)

```
Drawer = {grade, source_key, node_id, node_text, seq, stem_id, stem_name, domain, source_file,
          state, in_grade, owed, requires_confirm,
          cs: {cs_id, label, label_display, goal, goal_missing},
          strip: [{source_key, node_id, seq, state, in_grade, is_self}],
          ruling: {raw_value, resolution, ruling_type, states_mentioned, notes, needs_writer_review},
          grade_states: [{grade, short, state}],          # every GRADES member where state != 'off_grade'
          fields: [{key, label, values: [str]}],          # non-empty only, compare_field_defs order
          fields_empty: [FieldDef],                       # for "show all"
          standards: [{code, relation, annotation, shared: bool}],   # CCSS only (state IS NULL)
          state_codes: [{code, state, relation, annotation}],        # state IS NOT NULL
          pairings: [Partner],                            # same as the shared_code badge's partners
          lessons: [{lesson_id, product, raw_ref, shared_with: [{stem_id, stem_name,
                     nodes: [{source_key, node_id, in_grade}]}]}],
          product_refs: [{product, raw_ref, lesson_id}],  # all rows, in rowid order
          links: [{text, stem_id: str|None, label: "as captured"}],  # stem_id when text == a stem name
          period_hint: PeriodHint|None,
          placed_elsewhere: [PlacedRef],
          badges: [Badge]}
```

```json
{"grade": "2", "source_key": "WHO:d65c6bed74a5ba1c", "node_id": "WHO-0010",
 "node_text": "Compare whole numbers by using place value.", "seq": 10,
 "stem_id": "WHO", "stem_name": "Whole Numbers and Base Ten Structure",
 "domain": "Number Systems and Structures",
 "source_file": "MH2_PK5_NumberSystemsandStructures_Whole Numbers and Base Ten Structure.docx",
 "state": "span", "in_grade": true, "owed": true, "requires_confirm": false,
 "cs": {"cs_id": "WHO:7046b922", "label": "Compare and order numbers by using place value.",
        "label_display": "Compare and order numbers by using place value.",
        "goal": "Identify relationships between quantities (less than, greater than, or equal to)\nCompare multiple quantities to sequence them",
        "goal_missing": false},
 "strip": [{"source_key": "WHO:d65c6bed74a5ba1c", "node_id": "WHO-0010", "seq": 10, "state": "span", "in_grade": true, "is_self": true},
           {"source_key": "WHO:dc726b29e8f61f18", "node_id": "WHO-0011", "seq": 11, "state": "span", "in_grade": true, "is_self": false},
           {"source_key": "WHO:fdbd77e5556b420d", "node_id": "WHO-0012", "seq": 12, "state": "off_grade", "in_grade": false, "is_self": false},
           {"source_key": "WHO:5a975d82b5ef07dd", "node_id": "WHO-0013", "seq": 13, "state": "off_grade", "in_grade": false, "is_self": false}],
 "ruling": {"raw_value": "1, 2, 3, 4", "resolution": "ruled", "ruling_type": "span",
            "states_mentioned": null, "notes": null, "needs_writer_review": 0},
 "grade_states": [{"grade": "1", "short": "G1", "state": "span"}, {"grade": "2", "short": "G2", "state": "span"},
                  {"grade": "3", "short": "G3", "state": "span"}, {"grade": "4", "short": "G4", "state": "span"}],
 "fields": [{"key": "specifications", "label": "Specifications",
             "values": ["Some place value charts ok, but don’t rely heavily on place value charts", "Use symbols: >, <, =", "…"]},
            {"key": "misconceptions", "label": "Misconceptions", "values": ["Comparing by only considering the digit in the greatest place value …"]},
            {"key": "additional_notes", "label": "Additional notes",
             "values": ["G1: Likely 3 instructional periods", "G2: Likely 2 instructional periods (heavy fluency practice)",
                        "G3: Likely part of 1 instructional period", "G4: Likely part of 1 instructional period"]}, "…"],
 "fields_empty": [{"key": "strategies", "label": "Strategies"}, {"key": "terminology", "label": "Terminology"},
                  {"key": "leaves_to_include", "label": "Leaves to include"}],
 "standards": [{"code": "1.NBT.B.3", "relation": "aligned", "annotation": null, "shared": true},
               {"code": "2.NBT.A.4", "relation": "aligned", "annotation": null, "shared": true},
               {"code": "4.NBT.A.2", "relation": "aligned", "annotation": null, "shared": true},
               {"code": "K.CC.C.7", "relation": "aligned", "annotation": null, "shared": true}],
 "state_codes": [{"code": "AK.1.CC.5", "state": "AK", "relation": "aligned", "annotation": null}, "… (42 rows)"],
 "pairings": ["(the two Partner objects of §5.2)"],
 "lessons": [{"lesson_id": "G2-M1-L35", "product": "EM2", "raw_ref": "EM2 G2 M1 L35, L36, L37",
              "shared_with": [{"stem_id": "COM", "stem_name": "Comparing",
                               "nodes": [{"source_key": "COM:58ec03661c5f2d1b", "node_id": "COM-0012", "in_grade": true}]}]},
             {"lesson_id": "G1-M5-L7", "product": "EM2", "raw_ref": "EM2 G1 M5 L7, L8", "shared_with": []}, "…"],
 "product_refs": [{"product": "EM2", "raw_ref": "EM2 G1 M5 L7, L8", "lesson_id": "G1-M5-L7"}, "… (7 rows)"],
 "links": [],
 "period_hint": {"text": "G2: Likely 2 instructional periods (heavy fluency practice)", "value": 2.0,
                 "unit": "period", "qualifier": "exact", "low": 2.0, "high": 2.0,
                 "basis": "grade_named", "n_estimates": 1},
 "placed_elsewhere": [],
 "badges": ["(same list as the slice chip)"]}
```

Notes:

- `lessons` lists distinct `lesson_id`s (first `raw_ref` per id), and `shared_with` may be empty.
- For `COM-0012`, `links` would be `[{"text": "Whole Numbers and Base Ten Structure", "stem_id": "WHO", "label": "as captured"}, {"text": "Ordering", "stem_id": null, …}, {"text": "Likely 1 instructional period", "stem_id": null, …}]`. The third one is noise, shown as captured (O11).

### 5.4 CompareColumn (R) and the demo payload

```
CompareColumn = {source_key, node_id, node_text, stem_id, stem_name, cs_label_display,
                 goal: str|None, grades: [grade], state, in_grade, grade_raw: str|None,
                 period_hint_text: str|None,
                 ccss: [code] (sorted, distinct), lessons: [lesson_id] (sorted, distinct, filtered as §3.2),
                 state_codes: [code] (sorted),
                 fields: {key: [values]}}      # non-empty only
```

### 5.5 `GET /api/seq/compare?grade=2&keys=COM%3A58ec03661c5f2d1b,WHO%3Ad65c6bed74a5ba1c`

```
Compare = {grade, columns: [CompareColumn], field_defs: [FieldDef],
           rows: [{key, label, group, kind, cells: [...], shared: bool, empty: bool}],
           shared_ccss: [code], shared_lessons: [lesson_id]}
```

**`assemble_compare(columns, field_defs, grade)`** is exact and mirrored in JS.
Let `C = columns`. The rows are appended in this order:

1. **Node rows.** `group="node"`, `kind="text"`, `shared=false`. The fixed list `NODE_ROWS`:

   | key | label | cell per column (always a list of strings) |
   |---|---|---|
   | `stem` | Stem | `[c.stem_name]` |
   | `concept_skill` | Concept/skill | `[c.cs_label_display]` |
   | `goal` | Goal | `[c.goal]` if goal else `[]` |
   | `grades` | Grades | `c.grades` |
   | `state` | Kind in this grade | `[c.state]` |
   | `grade_raw` | Grade (as written) | `[c.grade_raw]` if set else `[]` |
   | `period_hint` | Period hint | `[c.period_hint_text]` if set else `[]` |

2. **CCSS rows.** `group="ccss"`, `kind="flag"`. For each `code` in `sorted(set().union(*[c.ccss]))`:
   - `key="ccss:"+code`, `label=code`;
   - `cells=[code in c.ccss for c in C]`;
   - `shared = sum(cells) >= 2`.
3. **Lesson rows.** `group="lesson"`, `kind="flag"`. The same as CCSS rows, over `c.lessons`, with `key="lesson:"+id`.
4. **Field rows.** `group="field"`, `kind="text"`, `shared=false`. For each `FieldDef` in `field_defs`: `cells=[c.fields.get(key, []) for c in C]`.
5. **State codes row.** `key="state_codes"`, `label="State codes"`, `group="state"`, `kind="text"`, `cells=[c.state_codes for c in C]`, `shared=false`.

For every row, `empty` = every cell is `[]` or `false`. `shared_ccss` and
`shared_lessons` are the codes and ids of the shared rows, in row order.
Sorting is plain string sort (Python `sorted`, JS `.sort()` with no
comparator; codes are ASCII).

```json
{"grade": "2",
 "columns": [{"source_key": "COM:58ec03661c5f2d1b", "node_id": "COM-0012",
              "node_text": "Compare multi-digit numbers using place value relationships.",
              "stem_id": "COM", "stem_name": "Comparing",
              "cs_label_display": "Compare and order multi-digit numbers by using a number line.",
              "goal": "Apply place value understanding to compare numbers", "grades": ["2"],
              "state": "core", "in_grade": true, "grade_raw": "G2",
              "period_hint_text": "Instructional Period: | Likely 1 instructional period",
              "ccss": ["2.NBT.A.4"], "lessons": ["G2-M1-L35"],
              "state_codes": ["CA.2.NBT.4", "FL.2.NSO.1.3", "TX.2.2D"], "fields": {"…": "…"}},
             {"source_key": "WHO:d65c6bed74a5ba1c", "node_id": "WHO-0010", "…": "…",
              "ccss": ["1.NBT.B.3", "2.NBT.A.4", "4.NBT.A.2", "K.CC.C.7"],
              "lessons": ["G1-M5-L7", "G2-M1-L35", "G4-M1-L9"]}],
 "field_defs": [{"key": "specifications", "label": "Specifications"}, "…"],
 "rows": [
  {"key": "stem", "label": "Stem", "group": "node", "kind": "text",
   "cells": [["Comparing"], ["Whole Numbers and Base Ten Structure"]], "shared": false, "empty": false},
  "…",
  {"key": "ccss:1.NBT.B.3", "label": "1.NBT.B.3", "group": "ccss", "kind": "flag", "cells": [false, true], "shared": false, "empty": false},
  {"key": "ccss:2.NBT.A.4", "label": "2.NBT.A.4", "group": "ccss", "kind": "flag", "cells": [true, true], "shared": true, "empty": false},
  "…",
  {"key": "lesson:G2-M1-L35", "label": "G2-M1-L35", "group": "lesson", "kind": "flag", "cells": [true, true], "shared": true, "empty": false},
  "…",
  {"key": "strategies", "label": "Strategies", "group": "field", "kind": "text", "cells": [[], []], "shared": false, "empty": true},
  {"key": "state_codes", "label": "State codes", "group": "state", "kind": "text", "cells": [["CA.2.NBT.4", "…"], ["AK.1.CC.5", "…"]], "shared": false, "empty": false}],
 "shared_ccss": ["2.NBT.A.4"], "shared_lessons": ["G2-M1-L35"]}
```

### 5.6 `GET /api/seq/sequence?grade=2` (SequenceView)

```
SequenceView = {grade, ladders_last_read,
                sequence: {sequence_id, grade, title, owner, note, rev, created_by, created_at}|None,
                modules: [ModuleView], placed_index: {source_key: PlacedHere},
                slice_badges: {source_key: [Badge]}, guardrail: Guardrail,
                attention: [AttentionItem]}
ModuleView   = {module_id, title, note, order_key, position, guardrail: Guardrail, slots: [SlotView]}
SlotView     = {slot_id, label, order_key, position, placements: [PlacementView]}
PlacementView= {placement_id, rev, order_in_slot, source_key, node_id: str|None, node_id_seen,
                node_text,            # current text, or node_text_seen if orphaned
                node_text_seen, stem_id, stem_name, concept_skill_display, ladder_file_seen,
                grade_kind_seen, state_now: str|None, status, relabelled, relabel,
                is_bridge, calibration, period_estimate, period_hint: PeriodHint|None,
                period_hint_seen, differentiation_note, placed_by, placed_at, updated_by,
                updated_at, placed_elsewhere: [PlacedRef], badges: [Badge]}
PlacedHere   = {placement_id, module_id, module_title, module_position, slot_id, slot_position,
                status, is_bridge}
AttentionItem= {placement_id, rev, module_id, module_title, module_position, slot_id, slot_position,
                status, source_key, node_text_seen, ladder_file_seen, stem_id_seen,
                grade_kind_seen, state_now, suggestions: [Suggestion], actions: [str]}
```

- **Placement badge codes** (W, from `derive_status` and R's `ordering_badges`):

  | code | tier | label | detail |
  |---|---|---|---|
  | `orphaned` | structural | `orphaned` | `"Node no longer in the ladder as placed; was: <node_text_seen> (<ladder_file_seen>)"` |
  | `grade_changed` | structural | `"was <seen> → now <now>"` | `"The grade ruling changed since this was placed"` |
  | `bridge` | structural | `"bridge · <state>"` | `"Placed outside this grade's inventory on purpose"` |
  | `relabelled` | info | `heading changed` | `"Concept/skill was \"<seen>\""` or `"Ladder file was <seen>"` |
  | `before_predecessor` | structural | `"before WHO-0010"` | `"Placed before its ladder predecessor WHO-0010 (M2 · slot 1). Warning only."` |
  | `predecessor_unplaced` | info | `"WHO-0010 unplaced"` | `"Its ladder predecessor WHO-0010 is not placed in this sequence"` |

- `slice_badges[key]` = the placement's badges whose code ∈ {`bridge`, `grade_changed`, `before_predecessor`, `predecessor_unplaced`}. The page shows them on the chip.
- `attention` includes `orphaned` and `grade_changed` placements only, ordered by module position, then slot position.
- `actions` is `["reattach","remove"]` for orphans and `["acknowledge","remove"]` for grade_changed.
- `placed_index` covers active placements, orphans included.
- When `sequence` is null, `modules` is `[]`, the dicts are `{}`, `attention` is `[]`, and `guardrail = compute([])`.

**Worked example** (*illustrative* placements over real nodes). M1 holds
WHO-0011 alone. M2 has a co-placed slot WHO-0010 + COM-0012, then a bridge
WHO-0012.

```json
{"grade": "2", "ladders_last_read": "2026-09-09 15:11:11",
 "sequence": {"sequence_id": 1, "grade": "2", "title": "Grade 2 sequence", "owner": "local",
              "note": null, "rev": 9, "created_by": "local", "created_at": "2026-09-30T17:40:02+00:00"},
 "modules": [
  {"module_id": 1, "title": "M1 Place value", "note": null, "order_key": 1024, "position": 1,
   "guardrail": {"…": "compute over M1"},
   "slots": [
    {"slot_id": 1, "label": null, "order_key": 1024, "position": 1, "placements": [
     {"placement_id": 1, "rev": 3, "order_in_slot": 1024, "source_key": "WHO:dc726b29e8f61f18",
      "node_id": "WHO-0011", "node_id_seen": "WHO-0011",
      "node_text": "Order whole numbers by using place value.",
      "node_text_seen": "Order whole numbers by using place value.",
      "stem_id": "WHO", "stem_name": "Whole Numbers and Base Ten Structure",
      "concept_skill_display": "Compare and order numbers by using place value.",
      "ladder_file_seen": "MH2_PK5_…_Whole Numbers and Base Ten Structure.docx",
      "grade_kind_seen": "span", "state_now": "span", "status": "ok", "relabelled": false,
      "relabel": null, "is_bridge": false, "calibration": "functional", "period_estimate": 1.0,
      "period_hint": {"text": "G2: 1 instructional period", "value": 1.0, "unit": "period",
                      "qualifier": "exact", "low": 1.0, "high": 1.0, "basis": "grade_named", "n_estimates": 1},
      "period_hint_seen": "G2: 1 instructional period", "differentiation_note": "Within 1,000 here; G3 extends to 10,000",
      "placed_by": "local", "placed_at": "2026-09-30T17:41:10+00:00", "updated_by": "local",
      "updated_at": "2026-09-30T17:44:00+00:00", "placed_elsewhere": [],
      "badges": [{"code": "before_predecessor", "tier": "structural", "label": "before WHO-0010",
                  "detail": "Placed before its ladder predecessor WHO-0010 (M2 · slot 1). Warning only.",
                  "refs": ["WHO:d65c6bed74a5ba1c"], "partners": []}]}]}]},
  {"module_id": 2, "title": "M2 Comparing numbers", "note": null, "order_key": 2048, "position": 2,
   "guardrail": {"…": "…"},
   "slots": [
    {"slot_id": 2, "label": "Co-taught: compare", "order_key": 1024, "position": 1, "placements": [
     {"placement_id": 2, "source_key": "WHO:d65c6bed74a5ba1c", "node_id": "WHO-0010",
      "calibration": "deep", "period_estimate": 1.5, "status": "ok", "is_bridge": false, "badges": [], "…": "…"},
     {"placement_id": 3, "source_key": "COM:58ec03661c5f2d1b", "node_id": "COM-0012",
      "calibration": "deep", "period_estimate": 0.5, "status": "ok", "is_bridge": false, "badges": [], "…": "…"}]},
    {"slot_id": 3, "label": null, "order_key": 2048, "position": 2, "placements": [
     {"placement_id": 4, "source_key": "WHO:fdbd77e5556b420d", "node_id": "WHO-0012",
      "grade_kind_seen": "off_grade", "state_now": "off_grade", "status": "ok", "is_bridge": true,
      "calibration": "illuminating", "period_estimate": null, "period_hint": null,
      "badges": [{"code": "bridge", "tier": "structural", "label": "bridge · off_grade",
                  "detail": "Placed outside this grade's inventory on purpose", "refs": [], "partners": []}],
      "…": "…"}]}]}],
 "placed_index": {
  "WHO:dc726b29e8f61f18": {"placement_id": 1, "module_id": 1, "module_title": "M1 Place value", "module_position": 1,
                           "slot_id": 1, "slot_position": 1, "status": "ok", "is_bridge": false},
  "WHO:d65c6bed74a5ba1c": {"placement_id": 2, "module_id": 2, "module_title": "M2 Comparing numbers", "module_position": 2,
                           "slot_id": 2, "slot_position": 1, "status": "ok", "is_bridge": false},
  "COM:58ec03661c5f2d1b": {"placement_id": 3, "…": "…"}, "WHO:fdbd77e5556b420d": {"placement_id": 4, "…": "…"}},
 "slice_badges": {"WHO:dc726b29e8f61f18": ["(before_predecessor badge)"], "WHO:fdbd77e5556b420d": ["(bridge badge)"]},
 "guardrail": {"n": 4, "n_calibrated": 4, "n_timed": 3,
   "counts": {"deep": 2, "functional": 1, "illuminating": 1, "unset": 0},
   "count_share": {"deep": 0.5, "functional": 0.25, "illuminating": 0.25},
   "periods": {"deep": 2.0, "functional": 1.0, "illuminating": 0.0, "unset": 0.0},
   "total_periods": 3.0, "time_share": {"deep": 0.6667, "functional": 0.3333, "illuminating": 0.0},
   "time_coverage": 0.75, "show_time_targets": true,
   "count_target": {"deep": 0.25, "functional": 0.5, "illuminating": 0.25},
   "time_target": {"deep": 0.4, "functional": 0.45, "illuminating": 0.15},
   "time_mark_min_coverage": 0.5},
 "attention": []}
```

**AttentionItem example** (*illustrative*). This is the orphan left after
WHO-0011 was reworded in Word:

```json
{"placement_id": 1, "rev": 3, "module_id": 1, "module_title": "M1 Place value", "module_position": 1,
 "slot_id": 1, "slot_position": 1, "status": "orphaned", "source_key": "WHO:dc726b29e8f61f18",
 "node_text_seen": "Order whole numbers by using place value.",
 "ladder_file_seen": "MH2_PK5_…_Whole Numbers and Base Ten Structure.docx", "stem_id_seen": "WHO",
 "grade_kind_seen": "span", "state_now": null,
 "suggestions": [{"source_key": "WHO:0a1b2c3d4e5f6071", "node_id": "WHO-0011",
                  "node_text": "Order whole numbers using place value.", "stem_id": "WHO",
                  "stem_name": "Whole Numbers and Base Ten Structure",
                  "concept_skill_display": "Compare and order numbers by using place value.",
                  "reason": "same_cs_similar", "ratio": 0.962}],
 "actions": ["reattach", "remove"]}
```

### 5.7 Guardrail endpoint (`GET /api/seq/guardrail?grade=2`)

```json
{"sequence_id": 1, "sequence": {"…Guardrail…": "…"},
 "modules": [{"module_id": 1, "title": "M1 Place value", "guardrail": {"…": "…"}}, "…"]}
```

`sequence_id` is null, `sequence` is `compute([])` and `modules` is `[]` when
there is no sequence.

### 5.8 Guardrail formula (authoritative; JS mirrors it)

Input: the active placements, each `{calibration ∈ deep|functional|illuminating|null, period_estimate: float|null}`. Orphans are included.

```
L = ["deep", "functional", "illuminating"]
n            = count
counts[l]    = count with calibration == l ; counts.unset = count with calibration null
n_calibrated = n - counts.unset
count_share[l] = R4(counts[l] / n_calibrated) if n_calibrated > 0 else null
periods[l]   = R2(sum period_estimate where calibration == l and estimate not null)
periods.unset= R2(sum period_estimate where calibration null and estimate not null)
n_timed      = count with period_estimate not null
total_periods= R2(sum of all non-null estimates)
T            = raw (unrounded) sum over L of the periods[l] sums
time_share[l]= R4(raw periods[l] / T) if T > 0 else null
time_coverage= R4(n_timed / n) if n > 0 else null
show_time_targets = (n > 0) and (n_timed / n >= 0.5)     # unrounded comparison
count_target, time_target, time_mark_min_coverage = constants
R2/R4 = round_half_up(x, 2/4); JS: Math.floor(x * 10**d + 0.5) / 10**d
```

Float sums are accumulated in the order of the input list.

**Shared fixture table.** W's Python test and F's JS test both assert exactly these outputs:

| # | input `[(calibration, period_estimate)]` | expected (keys not listed are as the formula gives) |
|---|---|---|
| G0 | `[]` | n 0; counts all 0; count_share all null; periods all 0.0; total 0.0; time_share all null; time_coverage null; show false |
| G1 | `[(deep,2),(functional,1),(functional,null),(null,1.5)]` | n 4; counts d1 f2 i0 u1; n_cal 3; count_share d .3333 f .6667 i 0.0; periods d 2.0 f 1.0 i 0.0 u 1.5; total 4.5; n_timed 3; time_share d .6667 f .3333 i 0.0; coverage .75; show true |
| G2 | `[(null,null),(null,null)]` | n 2; counts u2; count_share all null; time_share all null; n_timed 0; coverage 0.0; show false |
| G3 | `[(deep,1),(deep,null)]` | count_share d 1.0 f 0.0 i 0.0; coverage 0.5; show **true** (boundary) |
| G4 | `[(illuminating,0.25),(illuminating,0.125)]` | periods i 0.38 (0.375 half-up); total 0.38; time_share i 1.0; coverage 1.0 |
| G5 | `[(deep,1),(functional,1),(illuminating,1)]` | count_share d .3333 f .3333 i .3333; time_share same; total 3.0 |

### 5.9 Saved view state object (also the URL query)

```
ViewState = {hidden_stems: [stem_id], hidden_cs: [cs_id], collapsed_stems: [stem_id] | null,
             stem_order: [stem_id], context: true, leaves: false, state_ext: true,
             unplaced_only: false, pairings: false, flagged: false,
             node: source_key|null, compare: [source_key], sheet: false}
```

URL query keys (only non-defaults are written; lists are comma-joined, each
item `encodeURIComponent`'d):

| Query key | Field | Value format |
|---|---|---|
| `grade` | (grade) | |
| `hs` | `hidden_stems` | |
| `hc` | `hidden_cs` | |
| `cl` | `collapsed_stems` | Absent means the default rule (all collapsed when `chips > 100`, else none). Present, even empty, means an explicit list. |
| `so` | `stem_order` | |
| `ctx` | `context` | `ctx=0` |
| `lv` | `leaves` | `lv=1` |
| `sx` | `state_ext` | `sx=0` |
| `up` | `unplaced_only` | `up=1` |
| `pr` | `pairings` | `pr=1` |
| `fl` | `flagged` | `fl=1` |
| `node` | `node` | |
| `compare` | `compare` | Max 4. Extras beyond 4 are dropped on load. |
| `sheet` | `sheet` | `sheet=1` |

Two rules:

- `saved_view.state_json` = the ViewState **without** `node`, `compare` and `sheet`. Views save a lens, not a selection.
- Unknown stem ids and cs ids on load are ignored silently.

---

## 6. DemoApi (offline single file)

**Python is authoritative.** DemoApi exists so the tool can be previewed and
demoed with no server. Where it cannot reproduce server logic cheaply, it
degrades in the documented ways below. It never invents a different shape.

### 6.1 Demo payload (built by `scripts/export_seq_demo.py`)

```
DemoPayload = {format: "mh2-seq-demo/1", generated_at, grade,
               whoami: {user: "demo", auth_enabled: false, source: "demo"},
               grades: {grades: [GradeInfo], kind_source, ladders_last_read},   # = GET /grades
               slice: Slice,                                  # = GET /slice?grade=g, overlay {} unless --with-sequence
               drawers: {source_key: Drawer},                 # every node in the slice (in-grade + context)
               compare_columns: {source_key: CompareColumn},  # same key set as drawers
               field_defs: [FieldDef],                        # = compare_field_defs(con)
               sequence: SequenceView,                        # empty view unless --with-sequence
               views: []}
```

- **CLI:** `python scripts/export_seq_demo.py --grade 2 [--out PATH] [--db PATH] [--with-sequence --seq-db PATH]`.
  - The default output is `<config.REPORTS>/seq_demo_<slug>.html`, where slug is `g2`, `pk`, `k` or `a1` (as in `render_seq_prototype.grade_slug`, reimplemented, not imported).
  - It uses `seq_read.connect_ro`, `list_grades`, `build_slice`, `node_drawer`, `compare_column` and `compare_field_defs`.
  - `--with-sequence` additionally calls `seq_service.get_sequence` and `get_slice` over a `Ctx` with the given paths. It fails with a clear message if `mh2.seq_service` is not importable.
- **Bundling:**
  1. Read `seq_static/index.html`.
  2. Replace the exact line `<link rel="stylesheet" href="assets/app.css">` with `<style>…app.css…</style>`.
  3. Replace `<script src="assets/app.js"></script>` with `<script>…app.js…</script>`.
  4. Insert `<script id="seq-demo-data" type="application/json">…</script>` before the app script.
- **JSON embedding:** `json.dumps(payload, ensure_ascii=False)`, then replace `</` with `<\/`, U+2028 and U+2029 with their `\u` escapes, and `://` with `:\/\/`. All of these are valid JSON escapes, so no literal `http://` can appear.
- If `index.html` does not contain the two exact tags, the script exits non-zero.
- **Boot:** `app.js` checks `document.getElementById("seq-demo-data")`. If it is present, it uses `DemoApi(JSON.parse(el.textContent), safeStorage())`. Otherwise it uses `HttpApi(new URL("../api/seq/", location.href))`.

### 6.2 DemoApi behaviour

DemoApi implements every method of HttpApi (§7.1) with **the same response
shapes and the same error objects** (`ApiError{status, error, message,
detail}`).

- **Reads.**
  - `grades()` returns `payload.grades`.
  - `slice(g)` returns `payload.slice` if `g === payload.grade`, else 404 `not_found` ("Demo contains Grade 2 only").
  - `node(key)` returns `payload.drawers[key]`, else 404.
  - `compare(keys)` checks 1–4 distinct keys (else 422 `invalid`) and that every key is in `compare_columns` (else 404), then returns `assembleCompare(cols, payload.field_defs, grade)`.
  - `whoami()` returns `payload.whoami`.
  - `guardrail()` and `attention()` return the corresponding parts of the current view.
  - `events()` returns the in-memory event log (the same Event shape, `before`/`after` null).
- **State.** It keeps `{seq: {sequence, modules[], slots[], placements[]}, views[], next_id, events[]}`, with order keys, and rebuilds a `SequenceView` after every write with `demoBuildView()`:
  - `position`s are 1-based over active rows sorted by `order_key`.
  - `PlacementView` fields come from the slice node. `period_hint` comes from `SliceNode.period_hint`, `placed_elsewhere` from `SliceNode.placed_elsewhere`, and `concept_skill_display`/`stem_name` from the drawer.
  - `status` is `"ok"` and `relabelled` is false, except for placements carried in from a seeded `sequence`, which keep their status until reattached, acknowledged or removed.
  - `is_bridge` = `SliceNode.requires_confirm`. The `bridge` badge is generated exactly as in §5.6.
  - `before_predecessor` and `predecessor_unplaced` are **not computed** in demo mode. The page shows a footnote, "Ordering warnings need the server".
  - `guardrail` = the JS `computeGuardrail` (§5.8) over the whole sequence and over each module.
- **Writes.**
  - The same rules as §3.5: the rev check (409 `stale_revision` with `sequence` = the current view), `already_placed`, `confirm_off_grade_required` (using `requires_confirm` from the slice/drawer node), `module_not_empty`, `at_edge`, `already_alone`, order-key arithmetic, soft remove, and boundary-crossing slot moves.
  - Every write returns `{sequence: view, result}` with the §4 result keys.
- **`reattach`** in demo mode accepts only a key present in `drawers`, and sets status `ok`. **`acknowledge`** sets status `ok`.
- **Persistence.** `localStorage` key `mh2seq-demo:<grade>:<generated_at>`, value = the JSON state. Every access is wrapped in `try/catch`. If the storage throws or is null, the demo works in memory only and shows "Changes will not be kept after reload". A **Reset demo** button clears the key and reloads the seed.
- **Identity.** `placed_by` and `actor` are always `"demo"`.

---

## 7. Frontend spec (F)

### 7.1 JS structure (`seq_static/app.js`, one classic script)

Sections, in order:

1. `esc`
2. constants
3. pure logic
4. API adapters
5. render functions
6. controller
7. boot

The file ends with:

```js
if (typeof module !== "undefined" && module.exports) {
  module.exports = { esc, encodeState, decodeState, defaultState, computeGuardrail, assembleCompare,
                     visibleSlice, DemoApi, HttpApi, ApiError, renderSlice, renderDrawer,
                     renderRail, renderSheet, renderGuardrail, renderAttention, renderToolbar };
} else { document.addEventListener("DOMContentLoaded", boot); }
```

**Pure logic:**

- `encodeState(state, slice) → query string` and `decodeState(query, slice) → state` (§5.9).
- `defaultState(slice)`.
- `computeGuardrail(placements)` (§5.8).
- `assembleCompare(columns, fieldDefs, grade)` (§5.5).
- `visibleSlice(slice, state, seqView) → {super_stems: [...], counts: {shown_chips, shown_cs}}`. This applies show/hide only, with these rules:
  - `context=false` hides chips with state `off_grade` or `no_grade`.
  - `leaves=false` hides `leaf` chips, and hides a C/S whose `in_grade_count_excl_leaf == 0`.
  - `state_ext=false` hides `state_extension` chips.
  - `unplaced_only` keeps C/S with at least one `owed` chip whose key is not in `seqView.placed_index`.
  - `flagged` keeps C/S with at least one chip carrying a `structural` badge, taken from `node.badges` ∪ `seqView.slice_badges[key]`.
  - Filters act at **strip** level. A kept strip shows all its chips that are not hidden by the context, leaves or state_ext toggles. The progression stays readable.
  - `hidden_stems`/`hidden_cs` remove the item. `stem_order` reorders stems within their super-stem. Unknown ids are ignored.

  No kind or grade derivation happens in JS. It reads `state`, `owed`, `in_grade`, `requires_confirm` and `badges` only.

**API adapters.** `HttpApi(base, fetchFn = fetch)` and `DemoApi(payload, storage)` have identical async methods:

- `whoami()`, `grades()`, `slice(grade)`, `node(key, grade)`, `compare(keys, grade)`
- `sequence(grade)`, `guardrail(grade)`, `attention(grade)`, `events(sequenceId, limit, before)`
- `createSequence(grade, title)`, `updateSequence(id, rev, fields)`
- `createModule(seqId, rev, title)`, `updateModule(id, rev, fields)`, `moveModule(id, rev, dir)`, `removeModule(id, rev)`
- `updateSlot(id, rev, label)`, `moveSlot(id, rev, {direction} | {to_module_id})`, `mergeSlot(id, rev, intoId)`
- `createSlotGroup(seqId, rev, moduleId, keys, confirm, notes)`
- `place(seqId, rev, moduleId, key, {slot_id, after_slot_id, differentiation_note, confirm_off_grade})`
- `updatePlacement(id, placementRev, changes)`, `coPlace(id, rev, slotId)`, `ungroup(id, rev)`, `removePlacement(id, rev, reason)`
- `reattach(id, rev, key)`, `acknowledge(id, rev)`
- `listViews(grade)`, `createView(grade, name, state)`, `updateView(id, fields)`, `deleteView(id)`

HttpApi specifics:

- Non-2xx responses throw `ApiError(status, detail.error, detail.message, detail)`.
- It sends `X-MH2-User` when `localStorage["mh2seq-user"]` is set (wrapped in try/catch).
- It sends `credentials: "same-origin"`.

**Render functions** are pure `(state, data) → HTML string`. All text goes
through `esc()`. Interactive elements carry `data-action="…"` and `data-id`.
The controller uses **one delegated `click`/`change`/`keydown` listener per
region root**. After a write, the controller replaces the view with
`result.sequence` and re-renders the rail, the guardrail, attention, and the
slice's chip badges. It preserves `scrollTop` of the slice and rail
containers, and restores focus to the element with the same
`data-focus-key`.

### 7.2 Layout

Desktop width is 1280 px or more. At narrower widths the rail collapses into
a toggle and the drawer overlays the slice.

```
┌ header: "MH2 Grade Sequencing" · grade picker (from /grades; shows "G2 · 33 owed · 4 placed")
│         · kind_source/ladders-last-read stamp · whoami (+ "your name" field when auth off) · demo badge
├ toolbar: view toggles (Context · Leaves · State ext · Unplaced only · Pairings · Flagged)
│          · saved views [select] [Save as…] [Rename] [Delete] · "Reset view" · counts "shown 41 of 57 chips"
├──────────────┬──────────────────────────────────────────────┬──────────────┐
│ RAIL (380px) │ SLICE (main, scrolls)                         │ DRAWER       │
│  guardrail   │  super-stem heading                           │ (420px,      │
│  readout     │   stem header [▲▼ reorder][collapse][hide]    │  non-modal,  │
│  needs-      │    C/S strip: label · goal (1 line, expand)   │  slides over │
│  attention   │     [chip]→[chip]→[chip] (wraps)              │  the slice's │
│  modules →   │                                               │  right edge) │
│   slots →    │                                               │              │
│   placements │                                               │              │
├──────────────┴──────────────────────────────────────────────┴──────────────┤
│ COMPARE TRAY / SHEET (bottom; tray = "Compare 2/4 [Open]"; sheet ≤ 45vh, resizable) │
└────────────────────────────────────────────────────────────────────────────────┘
```

**Slice.**

- Super-stem headings are sticky subheadings.
- A stem header shows the name and "n in grade · m owed unplaced". It has chevrons (move the stem up/down within its super-stem, which writes `stem_order`), a collapse toggle, and a hide control.
- A C/S strip shows `label_display`, then the goal in the muted colour with `white-space: pre-line`, clamped to 2 lines with an expand control. When `goal_missing`, it shows "No goal in ladder" in italic.
- Chips wrap (Gate B Q2) and are joined by a thin → connector.

**Chip** (fixed width 220 px, min-height 84 px). It shows:

- `node_id` (small, muted) and the node text (clamped to 3 lines, full text in `title`).
- Grade pills (`grades`, short labels; the current grade is emphasised).
- Kind badges.
- A **placed-here** pill "M2 · 1" when the key is in `placed_index`, in the accent colour. A bridge placement adds a bridge pill.
- A `placed_elsewhere` pill.
- `slice_badges` (warning style).
- A `shared_code` badge only when Pairings is on.

Chip styles:

- In-grade owed chips are solid.
- `off_grade`/`no_grade` chips are dimmed (`opacity .55`, dashed border) with grade pills.
- `state_extension` chips are dimmed with a violet badge.
- `leaf` chips have a dotted border.
- The chip for the open drawer node gets an accent outline.

Chip actions, which are real `<button>`s:

- the chip body opens the drawer;
- **"+ Place"**, shown when not placed here, places into the rail's target module;
- **"⊕ compare"** toggles compare, disabled at 4 unless already selected.

### 7.3 Rail (module builder)

- **No sequence:** a "Start the Grade 2 sequence" button → `createSequence(grade, "Grade 2 sequence")`, then `createModule(…, "Module 1")`.
- **Guardrail readout** (sticky at the top of the rail), for the sequence and for the selected target module (tabs "Sequence | This module"):
  - **Counts bar:** three segments (Deep, Functional, Illuminating) sized by `count_share`, with the `count_target` drawn as thin tick marks above the bar. The caption is "Calibrated n_calibrated of n placements · unset u".
  - **Time bar:** segments sized by `time_share`. The `time_target` ticks are drawn **only if `show_time_targets`**. The caption is "Periods known for n_timed of n placements · total total_periods periods" and, when the marks are hidden, "(reference marks shown at ≥ 50% coverage)".
  - The subtitle is always "Reference marks, not quotas". There are no red/green verdict colours and no pass/fail wording.
- **Needs attention** (collapsible, count badge, hidden when 0). Items are grouped by module. For each item:
  - **Orphan:** the `node_text_seen` and `ladder_file_seen`; the suggestions side by side ("was" versus "now" text, reason and ratio), each with a **Re-attach** button; and **Remove**.
  - **grade_changed:** "was X → now Y", with **Keep (acknowledge)** and **Remove**.
- **Module list.** Each module card has:
  - its title (editable inline on double-click or with an Edit button) and position "M1";
  - ▲/▼ chevrons, disabled at the edges;
  - a "Place into this module" radio, which sets the **target module**. The default is the last module, and it is kept in the controller state, not in the URL;
  - a module guardrail mini-summary ("D 2 · F 1 · I 1 · unset 0 · 3.0 periods");
  - **Remove module**, enabled only when empty;
  - "+ Add module" at the bottom.
- **Slot** (a bordered group; a slot of one looks like a single card). It has:
  - the position number;
  - its label (optional, edit inline);
  - ▲/▼ chevrons, which cross module boundaries at the ends (§1.2), with a tooltip saying so;
  - "Move to module ▾" (a select);
  - "Merge into slot above", disabled on the first slot of the first module; it merges into the previous slot in rail order.
- **Placement row**, inside a slot. It shows:
  - `node_id` · `stem_name`, then the node text (2-line clamp);
  - badges;
  - a status chip when not ok;
  - **Calibration:** a segmented control `Deep | Functional | Illuminating | —`, with the tooltip "Know it / Use it / See it";
  - **Periods:** a number input (min 0, step 0.25, empty = unknown) with the saved value, saved on change or blur;
  - the hint text next to it: `period_hint.text`, with a "Use 2" button only when `period_hint.value != null`. Clicking the button fills the input and saves. When `unit != "period"` or `basis == "ungraded_multi_grade"` or `qualifier == "part_of"`, the text shows the reason ("in days; not converted", "estimate covers several grades", "≤ 1");
  - **Note:** a button showing "Add note" or the first line of `differentiation_note`, opening an inline textarea;
  - "Ungroup" (co-placed slots only);
  - "Remove";
  - "Open in drawer".
- **Cross-grade prompt:** placing a node whose `placed_elsewhere` is non-empty opens an inline panel in the rail, above the target module. It reads "Also placed in G1 · M3 Place value; G3 · M1 …". It has a textarea "How is it different in Grade 2? (optional)" and buttons **Place with note**, **Place without note** and **Cancel**. It never blocks.
- **Off-grade confirm:** when `requires_confirm` is true, the same panel shows "WHO-0012 is not in Grade 2's inventory (off-grade; its grades: G4, G5). Place it as a bridge?" with **Place as bridge** and **Cancel**, and sends `confirm_off_grade: true`. When both conditions apply, the note field and the bridge confirmation appear in one panel.
- **409 `stale_revision`:** replace the view with `detail.sequence`, re-render, and show the notice "Someone else changed this sequence. Your change was not applied, and you are now seeing the latest." in an `aria-live="polite"` notice bar. There is no automatic retry.
- **History** (collapsed `<details>` at the bottom): the last 50 events, as "actor · action · time", with a "More" button (`before`).

### 7.4 Drawer

- Non-modal. `role="complementary"`, `aria-label="Node details"`. It opens and changes on chip click, closes on the ✕ button or Esc, and leaves the slice interactive.
- Contents, top to bottom:
  - **C/S band** (tinted background): stem name, `label_display`, and Goal (pre-line) or "No goal in ladder". Goal only, per O12.
  - **Node band:** `node_id`, text, kind in this grade (badge), `grade_states` pills, and ruling `raw_value` ("Grade as written"). When `unconfirmed`, it adds `ruling_type` and `states_mentioned`.
  - **Placement box:**
    - if the node is placed here: "In this sequence: M2 · slot 1" with "Show in rail";
    - otherwise: a "Place in [module ▾]" button, which follows the same prompt rules;
    - the `placed_elsewhere` list.
  - The strip mini-map (the `strip` chips, the current one outlined, clickable) with "‹ prev · next ›" buttons.
  - Period hint.
  - Fields: non-empty `fields`, each a labelled list. A "Show all fields" toggle adds `fields_empty` as "—".
  - Standards: CCSS codes, with a shared marker. "State codes (n)" is collapsed.
  - **Pairings:** grouped by stem, "Comparing: 1.NBT.B.3, 2.NBT.A.4 … → COM-0012 (in grade), COM-0011 …". Each partner node is clickable and opens its drawer when it is in this slice; otherwise it is plain text.
  - **Shared EM2 lessons:** the same pattern.
  - **Stated links (as captured):** chips. A chip with a `stem_id` is a link that un-hides, expands and scrolls to that stem.
  - Product refs.
- Buttons: "⊕ Compare", "Place".

### 7.5 Compare sheet

- **Tray:** a fixed bottom bar that appears when `compare` is non-empty: "Compare 2/4 · [Open] [Clear]".
- **Sheet:** at most 4 columns, and never more. The table has sticky row labels and a sticky column header (`node_id`, stem, text, state, a placed-here pill, and a remove ✕).
  - Rows come from `Compare.rows`. `shared` rows are highlighted (accent-soft background plus a "shared" tag). Flag cells show ✓ or blank.
  - Toggles: "Hide empty rows" (default on) and "Shared rows first" (default off).
  - It can be open together with the drawer. `sheet=1` in the URL.
- **Primary action:** "Co-place in module [▾]" → `createSlotGroup` with the unplaced keys. Already-placed keys are listed in the confirm text as "skipped: already placed in M2". Keys with `requires_confirm` trigger the bridge confirm first.

### 7.6 Saved views

- A toolbar select listing `listViews(grade)`.
- **Save as…** prompts for a name → `createView`. A 409 shows "A view with that name exists".
- Selecting a view applies its `state` (§5.9 rules) and updates the URL.
- **Rename** and **Delete** act on the selected view.
- In demo mode views are stored in the demo state.

### 7.7 Visual language

- Copy the `:root` token block **verbatim** from `scripts/render_static.py`'s `_PAGE_TEMPLATE`: `--bg`, `--panel`, `--border`, `--border-strong`, `--text`, `--muted`, `--muted-2`, `--accent*`, `--green-*`, `--yellow-*`, `--red-*`, `--chip-bg`, `--radius-*` and `--shadow-*`. Do not import it.
- Use the same body font stack, the 14 px/1.5 base, sticky white header with `--shadow-sm`, uppercase 10.5 px group labels, pill chips, and rounded panels with `--shadow-md`.
- Add only these tokens:
  - `--teal-bg #e3f4f4; --teal-fg #11706f; --teal-bd #a9dada` (pairings)
  - `--violet-bg #f1edfc; --violet-fg #5b3fc4; --violet-bd #d3c8f5` (state ext)
  - `--deep #3457d5; --functional #1e7a37; --illuminating #c98a00` (calibration segments)
- Badge colours:

  | Badge | Colour |
  |---|---|
  | span | chip-bg |
  | unconfirmed | yellow |
  | kind pending | chip-bg, dashed |
  | state ext | violet |
  | leaf | chip-bg, dotted |
  | no grade | chip-bg, italic |
  | pairing | teal |
  | bridge | yellow-bd outline |
  | before_predecessor | yellow |
  | orphaned | red |
  | grade_changed | yellow |
  | predecessor_unplaced / relabelled | muted text |

  A badge always carries its text; colour is never the only signal.
- **Readable, not minimal:** labels are words, not only icons. Every icon button has visible text or an `aria-label` plus `title`.

### 7.8 Accessibility and keyboard

- Every control is a native `<button>`, `<input>` or `<select>`. Focus rings are `box-shadow: 0 0 0 3px var(--accent-soft)`, as in the audit.
- Chevron buttons have `aria-label="Move slot up"` and similar. On a focused slot or module card, **Alt+↑ / Alt+↓** do the same as the chevrons. Focus stays on the moved item after re-render (`data-focus-key`).
- Esc closes the topmost of the prompt panel, the drawer and the sheet.
- The notice bar is `aria-live="polite"`. The drawer heading receives focus on open, and focus returns to the chip on close.
- Colour contrast is at least 4.5:1 for text. Dimmed context chips keep full-contrast text inside a reduced-opacity border and background: apply opacity to the background, not the text.
- `prefers-reduced-motion`: no slide animations.

### 7.9 Hard rules for F

- There are no `http://` or `https://` strings in `seq_static/*`. That includes SVG `xmlns`: use CSS or Unicode glyphs, or inline SVG without `xmlns`, which is fine inside HTML.
- Nothing is loaded from the network except the same-origin `/api/seq/*`.
- `index.html` contains exactly the two tags that the exporter replaces (§6.1), and uses the relative URLs `assets/app.css` and `assets/app.js`.
- JS never derives a kind, a grade membership, a status, or a badge that the server provides. The only exceptions are the DemoApi degradations in §6.2.

---

## 8. Testing plan (runnable in this sandbox)

Common conventions:

- Tests are plain `def test_*()` functions with **no fixtures and no parameters**.
- Each file ends with a `if __name__ == "__main__":` runner that calls every `test_*`, prints `ok name` / `SKIP name: reason` / `FAIL`, and exits non-zero on failure. They are also pytest-compatible.
- A test needing real data copies `config.DB` / `config.SEQ_DB` (which follow `MH2_DATA_DIR`) into a `tempfile.mkdtemp()` directory. If the source is missing, it prints SKIP and returns.
- Run with `MH2_DATA_DIR=/sessions/compassionate-keen-fermat/work/data/v1 python3 tests/test_x.py`.
- **A test must never open the real DB files for writing.**
- A cross-implementer dependency (such as W's service tests needing `mh2.seq_read`) is guarded with `try: import …; except ImportError: SKIP`. It becomes live at integration.

### 8.1 R

**`tests/test_grade_kind.py`** (synthetic DBs from `config.SCHEMA`, plus the real copy):

- Both `kind_source` paths.
- A `node_grade` row with no kind row reads `unknown`, never `off_grade`.
- Precedence is leaf > no_grade > kind > off_grade.
- On real data:
  - the key set equals `node_grade` (535);
  - the Counter is core 155, span 213, unconfirmed 143, unknown 24;
  - `kind_for` splits the 24 into leaf 13 and unknown 11;
  - no `node_grade` row reads `off_grade`.

**`tests/test_seq_read.py`**, real copy unless noted:

1. For every grade, the set of in-grade `source_key`s equals `SELECT n.source_key FROM nodes n JOIN node_grade g USING(node_id) WHERE g.grade=?`.
2. `chips` per grade matches §5.1, and `counts.chips == in_grade_nodes + context_nodes`.
3. The G2 counts are as in §5.2, and the G2 in-grade states are core 13, span 14, unconfirmed 6, leaf 2.
4. Within each strip `seq` is strictly increasing, and no node appears twice in a slice.
5. No `label_display` matches `MARKER_RE`. 19 distinct raw labels do.
6. `build_slice` and `json.dumps` succeed for every grade.
7. For every G2 slice node, the drawer's field-key set equals `SELECT DISTINCT field FROM node_fields WHERE node_id=? AND TRIM(value)<>''`.
8. The cross-stem shared-code node-pair set derived from slice/drawer partners over all grades equals `docs/review/cross_stem_links_shared_ccss.csv` as a set of `(node_a, node_b)` pairs (180).
9. Period hint:
   - WHO-0010@G2 has value 2.0 and basis `grade_named`;
   - TIM-0010@G2 has value None and basis `ungraded_multi_grade`;
   - COM-0012@G2 has value 1.0 and basis `single_grade_node`;
   - no hint with unit day or lesson has a value (all grades).
10. `build_compare`:
    - 5 keys raise `ValueError`;
    - an unknown key raises `KeyError`;
    - the COM-0012/WHO-0010 example gives `shared_ccss == ["2.NBT.A.4"]` and `shared_lessons == ["G2-M1-L35"]`.
11. `assemble_compare` on hand-built columns, which pins the row order and `empty`/`shared`. R does not write F's fixture files. F builds its own from §5.5, and the integrator's parity test (§9) ties the two implementations together.
12. `ordering_badges` pure cases: inversion, co-placed tie (no badge), unplaced predecessor.
13. `suggest_successors` on synthetic DBs: rule 1 (the same hash suffix in another stem ranks first), rule 2 threshold 0.6, rule 3 threshold 0.8, `exclude_keys` respected, at most 3.
14. The overlay passes through: given `overlay={key: [PlacedRef]}`, the slice node gets `placed_elsewhere` and a `placed_elsewhere` badge.
15. Static checks:
    - `grep "mh2.coverage"` finds nothing;
    - `"mode=ro"` is present;
    - `"SEQ_DB"` and `"mh2_seq"` are absent from R's files.

### 8.2 W

**`tests/test_seq_store.py`** (temp copy of the real `mh2_seq.db`, plus a fresh temp file):

1. `ensure_schema` creates the 7 tables. The 6 review tables' row counts are unchanged.
2. The whole lifecycle: create sequence → modules → place → co-place → ungroup → merge → move slot across a module boundary → remove.
3. The same key twice in one sequence raises `already_placed`; the same key in two sequences succeeds.
4. 11 inserts at the same point produce exactly one `renumber` event, and order is preserved.
5. A stale rev raises `StaleRevision` and leaves the row hash unchanged.
6. The event count equals the number of mutations (with the multi-attribute PATCH rule).
7. `NeedsConfirm` is raised when `requires_confirm` is set without confirmation.
8. `remove_module` on a non-empty module raises `module_not_empty`.
9. `at_edge`.
10. Saved views are owner-scoped (B cannot see or update A's) and `view_name_exists` is enforced.
11. Checks via `inspect.signature`: no function has a parameter named `*_by` or `*_at`, and `writer`/`owner` is the second parameter.
12. `DELETE FROM` appears only inside `delete_view` (source grep).

**`tests/test_seq_reconcile.py`:**

- `derive_status` on hand-built `NodeFacts` for every data model §6.3 row that is expressible without a DB:
  - an orphan;
  - `core → off_grade` gives grade_changed;
  - `unknown → core` is ok;
  - `unconfirmed → span` is ok;
  - `unconfirmed → state_extension` gives grade_changed;
  - a relabel on concept_skill or file.
- The CLI (skip if `seq_read` is missing): run it on temp copies. The hash of every placement-table row is identical before and after, and the report file exists.

**`tests/test_seq_guardrail.py`:** the §5.8 fixture table G0–G5 exactly, plus the no-pass/fail-key assertion.

**`tests/test_seq_auth.py`:**

- Parse `review_api.py` and `mh2/seq_auth.py` with `ast`. `ast.dump` of the body of `_configured_writers` equals that of `configured_writers` (the drift test).
- `check_basic` and `resolve_writer` cases under a monkeypatched `os.environ` (set and restore by hand): auth off with no header, auth off with a valid header, auth off with an invalid header (`"a b"`), and auth on with good and bad credentials.

**`tests/test_seq_api_routes.py`** (FastAPI is not importable, so everything is static):

1. `py_compile.compile("seq_api.py", doraise=True)`.
2. `ast.parse`. Collect every function decorated `@router.<get|post|patch|put|delete>("<path>")`. The set of `(METHOD, path)` equals the §4 table exactly: 33 routes (R0–R8, W1–W17, V1–V4, P1–P3). Paths in the decorator include the prefix (`"/api/seq/slice"`, `"/seq/"`). The router is created with no prefix, so the static and API routes share it.
3. Every R1–V4 route body is a single `return _run(seq_service.<fn>, CTX, …)`, where `<fn>` matches the §4 mapping and the function name is `r_<fn>`. `whoami` is `r_whoami`, and the static routes are `r_page_redirect`, `r_page` and `r_asset`.
4. Every W* and V* route has a parameter `writer` (V: `owner`) whose default is `Depends(current_writer)`.
5. The source contains no `sqlite3`, `execute(`, `seq_store`, `seq_read`, `app.mount` or `StaticFiles`.
6. `FastAPI(` appears once, with `dependencies=[Depends(require_writer)]`.
7. No Pydantic model field name ends in `_by` or `_at`, and every model sets `extra="ignore"`.
8. The static route whitelist is exactly `{"app.js","app.css"}`.

**`tests/test_seq_service.py`** (skip if `mh2.seq_read` is not importable; temp copies of both DBs):

1. The end-to-end flow of §5.6 on real nodes WHO-0011, WHO-0010, COM-0012 and WHO-0012. Check the `placed_index` positions and the `before_predecessor` on WHO-0011.
2. The off-grade 422, and success with confirm.
3. Stale rev: 409 carries `extra["sequence"]` with the current rev.
4. Guardrail numbers match `seq_guardrail.compute`.
5. Simulate a reword by updating `nodes.node_text` and `source_key` in the **temp** mh2 copy. The attention list contains that placement with a suggestion. `reattach` clears it and preserves `placement_id` and calibration.
6. Every return value is `json.dumps`-able.
7. The slice's `placed_elsewhere` shows a G3 placement when viewing G2.

### 8.3 F

**`tests/js/seq_app_test.js`** (`node tests/js/seq_app_test.js`; plain `assert`, no packages): `require("../../seq_static/app.js")`, then:

- encodeState/decodeState round-trip (the defaults write an empty query; `cl` presence semantics; compare capped at 4; URL-encoded colons).
- `computeGuardrail` matches the §5.8 table G0–G5 exactly.
- `assembleCompare` on `tests/js/fixtures/compare_case1.json`, which F hand-writes from §5.5, gives the expected rows.
- `visibleSlice` toggles on a small fixture slice.
- DemoApi, with a fixture payload (`tests/js/fixtures/demo_small.json`: 2 stems, 3 C/S, 8 nodes including off_grade, leaf and span, written from §5 shapes):
  - create sequence and module;
  - place; `already_placed` 409; off-grade 422, then confirm;
  - co-place, ungroup, merge;
  - slot move across a module and `at_edge`;
  - remove; module_not_empty;
  - stale rev 409 carrying `sequence`;
  - attribute PATCH does not bump the sequence rev;
  - views CRUD;
  - persistence with a fake storage object, and with a storage whose methods throw (still works, and reports not-kept).
- Render functions return strings. Node text `<script>x</script>` is escaped. The chip for a placed node contains the "M1 · 1" pill. The rail contains chevron buttons with `aria-label`s. The guardrail HTML omits time ticks when `show_time_targets` is false.

**`tests/test_seq_frontend.py`:**

1. If `shutil.which("node")`, run the JS test and assert exit 0; else SKIP.
2. `seq_static/*` contain no `http://` or `https://`.
3. `index.html` contains the two exact replaceable tags.
4. `node --check seq_static/app.js`.
5. Skip if `mh2.seq_read` is missing: build `seq_demo_g2.html` into a temp dir with `export_seq_demo.main([...])`, then assert:
   - no `http://`/`https://`, no `<script src`, no `<link`;
   - the embedded JSON parses, `format == "mh2-seq-demo/1"`, `slice == seq_read.build_slice(con,"2")` (the overlay is empty), and the drawer key set equals every slice node key;
   - file size under 3 MB for G2.
   Then extract the inline script and run it under node with a 20-line stub (`module` defined) to confirm `DemoApi` loads the embedded payload and `slice("2")` returns it.

The committed `docs/seq_demo/seq_demo_g2.html` is regenerated by F at the end
and passes the same checks.

---

## 9. Integration (after R, W and F merge)

The integrator works on `seq/v1-prototype` with the three branches merged.

1. **Diff hygiene.** `git diff --stat <contract-commit>..HEAD` lists only files in the §2 ownership table. `md5sum` of every do-not-touch file equals its value at the contract commit.
2. **All new tests.** Run each `tests/test_seq_*.py` and `tests/test_grade_kind.py` directly with `MH2_DATA_DIR=…/data/v1`, and `node tests/js/seq_app_test.js`. The SKIPs that were guarded on missing modules must now be `ok`. Run the pre-existing test files the same way and compare with their pre-merge results: only pre-existing failures may remain.
3. **Write `tests/test_seq_integration.py`:**
   - **Parity (guardrail).** 200 random placement lists (fixed seed; calibrations and `None`; estimates in quarter-periods 0–5 and `None`). `seq_guardrail.compute` equals the JS `computeGuardrail`, run via `node -e` with the lists as JSON.
   - **Parity (compare).** For 20 random G2 key sets of size 1–4, `seq_read.build_compare` equals the JS `assembleCompare(columns, field_defs)` fed `compare_column` outputs.
   - **Shape parity.** A DemoApi-built `SequenceView` after the §5.6 script has the same key sets, recursively, as `seq_service` after the same script. Values may differ only in timestamps, ids and the documented demo degradations.
   - **Round trip.** Everything `export_seq_demo` embeds equals what the service's GET functions return for the same DBs.
4. **Static checks.**
   - `python3 -m py_compile` on every new `.py`;
   - `grep -nE "execute\(|sqlite3" seq_api.py` is empty;
   - `grep -rn "mh2.coverage" mh2/seq_* mh2/grade_kind.py seq_api.py` is empty;
   - `grep -rn "https\?://" seq_static docs/seq_demo` is empty.
5. **Demo sanity.** Regenerate the demos for G2 and G7 (the largest) and report their sizes. Open-by-code check: extract the payload and confirm the chip count equals `counts.chips`.
6. **On John's Mac only** (Python 3.14 + FastAPI; not possible here), a written checklist:
   1. `uvicorn seq_api:app --port 8001`. Open `/seq/` and click through G2: start a sequence, add 2 modules, place 4 nodes including one bridge, co-place, set calibration and periods, and watch the guardrail change.
   2. Two browser windows: the second structural write gets the 409 notice.
   3. `MH2_AUTH_USERS="a:x,b:y"`: `/seq/`, `/seq/assets/app.js` and `/api/seq/slice?grade=2` return 401 without credentials. `placed_by` is `a` after a write, even if the body sends `placed_by: "evil"`.
   4. `python -m mh2.seq_reconcile` writes its report, and the placement table hashes are unchanged.
   5. The existing `pytest -q` suite passes, count unchanged.
7. **Report** in the handoff style: route list, test counts (ok / skip / fail per file), demo sizes, the "Contract ambiguities" sections from R, W and F with the resolution taken, and the provisional rulings (§1) listed for John to confirm.

---

## 10. Sanity pass: what F renders, and where it comes from

| F region / element | Field(s) | Produced by | Route |
|---|---|---|---|
| Grade picker, owed count, placed count | `GradeInfo.label/short/owed_nodes/chips`, `sequence.n_placements` | R `list_grades` + W `sequence_summaries` | R1 |
| Stamp | `kind_source`, `ladders_last_read` | R | R1/R2 |
| Super-stem / stem / C/S headings | `domain`, `stem_name`, `label_display`, `goal`, `goal_missing` | R `build_slice` | R2 |
| Chip body, pills | `node_id`, `node_text`, `grades`, `state`, `in_grade` | R | R2 |
| Kind badges, pairing badge | `SliceNode.badges` | R | R2 |
| Placed-elsewhere pill; cross-grade prompt | `SliceNode.placed_elsewhere` | R (overlay from W `placement_index`) | R2 |
| Placed-here pill; unplaced filter | `SequenceView.placed_index` | W `build_sequence_view` | R5 / every write |
| Ordering / bridge warnings on chips; Flagged | `SequenceView.slice_badges` + `SliceNode.badges` (tier) | W (+ R `ordering_badges`) | R5 / writes |
| Off-grade confirm | `SliceNode.requires_confirm` / `Drawer.requires_confirm` | R | R2/R3 |
| Drawer, all bands | `Drawer.*` | R `node_drawer` | R3 |
| Compare sheet | `Compare.columns/rows/shared_*` | R `build_compare` / JS `assembleCompare` | R4 |
| Rail tree | `SequenceView.modules[].slots[].placements[]` | W | R5 / writes |
| Period hint + "Use n" | `PlacementView.period_hint` (`value`, `unit`, `basis`, `qualifier`) | R `period_hint` via `node_facts` | R5 |
| Guardrail readout | `SequenceView.guardrail`, `ModuleView.guardrail` | W `seq_guardrail.compute` (JS mirror in demo) | R5/R6 / writes |
| Needs attention | `SequenceView.attention` | W `derive_status` + R `suggest_successors` | R5/R7 |
| History | `Event` | W `list_events` | R8 |
| Saved views | `View.state` = ViewState minus selection | W | V1–V4 |
| Whoami, name field | `{user, auth_enabled, source}` | W `seq_auth` | R0 |

Every F field above appears in a §3/§5 shape. Every shape is produced by a
named R or W function and returned by a §4 route. DemoApi serves the same
shapes from the §6.1 payload.
