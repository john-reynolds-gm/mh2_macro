# Grade Sequencing Tool — data model rev 1 (2026-09-30)

> **Reconciled with branches 2026-09-30.** §5.2 is rewritten against the real
> `node_grade_kind` table on `seq/grade_type`, and the kind vocabulary is now
> `unconfirmed` (was `range_prose_unconfirmed`) in the DDL too. §5.4 reuses
> `seq/analysis`'s `parse_period_notes()`. §6.2/§6.3 add the
> `unconfirmed → core/span` exemption. §7/§8 add the time-mark threshold and
> the Pairings/Flagged split.

Proposed, not built. The design reference is `docs/seq_orientation_rev1.md`
(rulings R-S1..R-S11 are cited by number). This file covers the DDL for the
new `mh2_seq.db` tables, the placement reconcile contract, the read side
against `mh2.db`, route shapes, and hosting implications. The build plan is
`docs/seq_build_plan_rev1.md`.

---

## 1. Principles (each one follows an existing contract)

| Principle | Precedent |
|---|---|
| Authored rows key on `source_key`, never `node_id` | `tag_review`, `candidate_ruling` (R3) |
| `node_id_seen`, `node_text_seen`, `ladder_file_seen` captured at write time; used only for display, locating, and change detection; never trusted as current | `schema_seq.sql` comments on `tag_review` / `candidate_ruling` |
| No cross-DB foreign keys; drift is found by a reconcile sibling | `reconcile_review.py` |
| One module owns every SQL shape against the new tables; routes contain no SQL | `review_store.py`; DEFERRED §6 "No SQL in routes" |
| `*_by` / `*_at` are stamped by the store, never accepted from a client | `review_store.py` docstring. **Strengthened:** `*_by` comes from the authenticated writer (§11.3), not a request field. |
| History is kept by never deleting authored rows; a re-do is a new row | `tag_proposal` ("a withdrawn proposal and a later re-proposal … are two events") |
| Status that can be derived is derived, not stored | `ladder_edits.removal_state()` (DEFERRED §9) |
| `mh2.db` opened `mode=ro` | `reconcile_review.run`, `review_api._mh2_con` |

---

## 2. Where it lives

- **Same file:** `data/build/mh2_seq.db` (`config.SEQ_DB`).
- **New schema file:** `mh2/schema_placement.sql`, with a new `config.SCHEMA_PLACEMENT`.
  It is kept separate from `mh2/schema_seq.sql` so that placement sessions
  never edit the review schema file, whose reconcile contract is closed.
  `ensure_schema` in the new store runs it with `CREATE … IF NOT EXISTS`, the
  same way `review_store.ensure_schema` does.
- **New store:** `mh2/seq_store.py`. It is the only module that issues SQL
  against these tables.
- **Versioning:** `CREATE TABLE IF NOT EXISTS` never alters an existing table.
  A later column is an explicit `ALTER TABLE … ADD COLUMN` gated on
  `placement_schema_meta.version`. This replaces `PRAGMA user_version`, which is
  per-file and would be shared with the review tables.

---

## 3. DDL (proposed)

```sql
-- ============================================================================
-- mh2/schema_placement.sql — grade sequencing authored state (in mh2_seq.db).
--
-- AUTHORITATIVE for sequences, modules, slots, placements and per-placement
-- attributes ONLY (docs/seq_orientation_rev1.md §2, ruling R-S1). Holds no
-- tags, no node content, no grade rulings. Nothing reads these tables back
-- into mh2.db, and the gap audit never reads them (R-S3).
--
-- Keyed on nodes.source_key (stem_id + ':' + sha1(normalized node_text)[:16]).
-- node_id is positional and renumbers; it is only ever *_seen here.
-- *_seen columns are snapshots for display/locating/change detection and are
-- never trusted as current after a rebuild -- same contract as tag_review.
--
-- Authored rows are never DELETEd. Removal sets removed_at/removed_by; a
-- re-placement is a new row (tag_proposal's history rule). Every change is
-- also appended to placement_event.
--
-- rev: optimistic-concurrency counter. Every UPDATE is
--   ... SET ..., rev = rev + 1 WHERE <pk> = ? AND rev = ?
-- and zero rows affected is a conflict (HTTP 409), never a silent overwrite.
-- ============================================================================

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS placement_schema_meta (
    component TEXT PRIMARY KEY,           -- 'placement'
    version   INTEGER NOT NULL
);
INSERT OR IGNORE INTO placement_schema_meta (component, version) VALUES ('placement', 1);

-- One grade's sequence. v1 UI creates one per grade; the schema allows more
-- (a draft alternative) without a migration.
CREATE TABLE IF NOT EXISTS grade_sequence (
    sequence_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    grade        TEXT NOT NULL
                 CHECK (grade IN ('PK','K','1','2','3','4','5','6','7','8','A1')),
    title        TEXT NOT NULL,
    owner        TEXT,                    -- advisory in v1 (ruling O1)
    note         TEXT,
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    archived_at  TEXT,
    archived_by  TEXT,
    rev          INTEGER NOT NULL DEFAULT 1   -- bumped on EVERY structural change inside it (§11.2)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_sequence_title_active
    ON grade_sequence(grade, title) WHERE archived_at IS NULL;

CREATE TABLE IF NOT EXISTS module (
    module_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    sequence_id  INTEGER NOT NULL REFERENCES grade_sequence(sequence_id),
    title        TEXT NOT NULL,
    order_key    INTEGER NOT NULL,        -- sparse, see §4
    note         TEXT,
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    removed_at   TEXT,
    removed_by   TEXT,
    rev          INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_module_seq ON module(sequence_id, order_key);

-- A teaching slot: one or more co-placed nodes taught together. A solo node
-- is a slot of one. Grouping, not merging: no blend text lives here.
CREATE TABLE IF NOT EXISTS slot (
    slot_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    sequence_id  INTEGER NOT NULL REFERENCES grade_sequence(sequence_id), -- denormalized; set by seq_store only
    module_id    INTEGER NOT NULL REFERENCES module(module_id),
    order_key    INTEGER NOT NULL,        -- sparse, within module
    label        TEXT,                    -- optional ("Topic A", "Co-taught: ...")
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    removed_at   TEXT,                    -- set by seq_store when its last active placement leaves
    removed_by   TEXT,
    rev          INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_slot_module ON slot(module_id, order_key);

CREATE TABLE IF NOT EXISTS placement (
    placement_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    sequence_id         INTEGER NOT NULL REFERENCES grade_sequence(sequence_id), -- denormalized for the unique index
    slot_id             INTEGER NOT NULL REFERENCES slot(slot_id),
    order_in_slot       INTEGER NOT NULL DEFAULT 1024,   -- sparse, within slot

    source_key          TEXT NOT NULL,     -- the key; NOT a FK (other database)

    -- snapshots, captured at place / reattach / acknowledge only
    node_id_seen        TEXT,
    node_text_seen      TEXT NOT NULL,     -- NOT NULL: an orphan must stay findable in Word
    ladder_file_seen    TEXT,
    stem_id_seen        TEXT,
    concept_skill_seen  TEXT,              -- raw label, markers included
    grade_kind_seen     TEXT NOT NULL
                        CHECK (grade_kind_seen IN ('core','span','state_extension',
                                                   'unconfirmed',   -- node_grade_kind vocabulary
                                                   'off_grade','leaf','no_grade','unknown')),

    -- placement-scoped attributes (three-tier rule; R-S2)
    calibration         TEXT CHECK (calibration IN ('deep','functional','illuminating')),
    period_estimate     REAL CHECK (period_estimate IS NULL OR period_estimate >= 0),  -- instructional periods, never days/lessons (ruling O8)
    period_hint_seen    TEXT,              -- ladder prose shown when the estimate was set
    differentiation_note TEXT,             -- also the scope/range note ("within 100 here, within 1000 in G3")

    placed_by           TEXT NOT NULL,
    placed_at           TEXT NOT NULL,
    updated_by          TEXT,
    updated_at          TEXT,
    removed_at          TEXT,
    removed_by          TEXT,
    removed_reason      TEXT,
    reattached_from     TEXT,              -- the previous source_key, set only by an explicit reattach
    rev                 INTEGER NOT NULL DEFAULT 1
);
-- One ACTIVE placement per node per sequence (Assumption (confirm): same-year
-- revisits are a later feature; drop this index to allow them).
CREATE UNIQUE INDEX IF NOT EXISTS ux_placement_active
    ON placement(sequence_id, source_key) WHERE removed_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_placement_source ON placement(source_key);
CREATE INDEX IF NOT EXISTS ix_placement_slot   ON placement(slot_id, order_in_slot);

-- Append-only audit of every authored change. Never updated, never deleted.
-- This is what answers "who moved my node" on a shared sequence -- today's
-- review tables are last-writer-wins with no trace (DEFERRED §8).
CREATE TABLE IF NOT EXISTS placement_event (
    event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    sequence_id  INTEGER NOT NULL,
    module_id    INTEGER,
    slot_id      INTEGER,
    placement_id INTEGER,
    action       TEXT NOT NULL CHECK (action IN (
                   'sequence_create','sequence_update','sequence_archive',
                   'module_create','module_update','module_move','module_remove',
                   'place','move','co_place','ungroup','remove',
                   'set_calibration','set_period','set_note',
                   'reattach','acknowledge','renumber')),
    actor        TEXT NOT NULL,
    at           TEXT NOT NULL,
    before_json  TEXT,                     -- changed fields only
    after_json   TEXT
);
CREATE INDEX IF NOT EXISTS ix_event_seq ON placement_event(sequence_id, event_id);
CREATE INDEX IF NOT EXISTS ix_event_placement ON placement_event(placement_id);

-- Per-writer named view of a grade slice. UI state, not curriculum; lives
-- here only because it must survive a rebuild. state_json is the same object
-- the page writes to its URL query string (orientation §6.2). References to
-- stems / concept-skills that no longer exist are ignored on load, never errors.
CREATE TABLE IF NOT EXISTS saved_view (
    view_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    owner       TEXT NOT NULL,             -- authenticated writer ('local' with auth off)
    grade       TEXT NOT NULL,
    name        TEXT NOT NULL,
    state_json  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (owner, grade, name)
);
```

**What is deliberately absent:**

- No `topic` table. Ruling O2's default is modules only. Adding topics later
  is an additive `topic` table plus a nullable `slot.topic_id`.
- No sign-off state. Ruling O1's default is none in v1. Adding it later is an
  `ALTER TABLE grade_sequence ADD COLUMN status`.
- No state-variant column. State variants are deferred. Adding one later is
  `grade_sequence.variant_of` plus `state`.
- No stored reconcile status (§6.1).

---

## 4. Order keys: how reorders avoid renumber churn

- `module.order_key` (in a sequence), `slot.order_key` (in a module), and
  `placement.order_in_slot` (in a slot) are **sparse integers** with step
  `ORDER_STEP = 1024`.
- **Append:** `max(order_key) + 1024`.
- **Chevron move up/down:** swap `order_key` with the adjacent active sibling.
  That is two row updates in one `BEGIN IMMEDIATE` transaction and one `move`
  event. This is the v1 path, because drag-and-drop is deferred.
- **Insert between a and b** (move to another module at a position, co-place
  split): `(a + b) // 2`.
- **Gap exhausted** (`b - a < 2`): renumber *that one container's* active
  siblings to `1024 * i` in the same transaction, and log one `renumber`
  event. No other container is touched. With 1024 steps, a container needs
  ten consecutive inserts at the same point before this happens.
- **Rejected:** REAL fractional keys (about 50 bisections before precision
  runs out, and noisy values in text dumps) and linked lists (no `ORDER BY`,
  and a corrupt pointer loses the tail).
- Removed rows keep their `order_key`, and ordering reads only active rows.

---

## 5. Read side: what the slice reads from mh2.db

### 5.1 Tables read (all via `mode=ro`)

| mh2.db table | Used for |
|---|---|
| `nodes` (`node_id, source_key, stem_id, seq, node_text, concept_skill, goal, source_file`) | Chips, strip order (`seq`), C/S grouping `(stem_id, concept_skill)`, C/S goal |
| `stems` (`name`, `domain`) | Stem label, super-stem grouping (`domain`; orientation §6.1) |
| `grade_order` (`grade, ord, band`) | Grade ordering and band (PK5 / 6_9) |
| `node_grade` | In-grade membership (the placement reading, R-S3) |
| `node_grade_ruling` (`raw_value, resolution, is_leaf, notes`; on `seq/grade_type` also `ruling_type, needs_writer_review, states_mentioned`) | Leaf / no_grade states, raw grade text in the drawer; `states_mentioned` on the state-ext / unconfirmed badge |
| `node_grade_kind` (`node_id, grade, kind, basis`), from `seq/grade_type` (§5.2) | Per-(node, grade) kind, read only through `mh2/grade_kind.py` |
| `node_fields` | Drawer rows; period hints (`additional_notes`) |
| `node_standards_parsed` (`state IS NULL` = CCSS) | Shared-code badges, compare-table standard rows |
| `node_links` (`stated`) | Drawer chips |
| `product_refs` | Drawer; EM2 lesson refs |
| `ingest_log` (`MAX(ts)`) | "Ladders last read" stamp, the same convention as the ladder-edits export |

**Not read:** `candidates`, `model_*`, `crosswalk`, `leaves` (v1), and
anything through `mh2/coverage.py`. The sequencer does not import
`coverage`. `node_lookup.build_source_key_lookup` may be reused, because it
reads only `nodes`.

### 5.2 Consuming the per-(node, grade) kind from `seq/grade_type`

**As built on `seq/grade_type`** (`mh2/schema.sql`, `mh2/load_grade_ruling.py`):

```sql
CREATE TABLE IF NOT EXISTS node_grade_kind (
    node_id TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
    grade   TEXT NOT NULL REFERENCES grade_order(grade),
    kind    TEXT NOT NULL
            CHECK (kind IN ('core', 'span', 'state_extension', 'unconfirmed')),
    basis   TEXT NOT NULL,          -- 'ruling_type=single', 'worksheet ccss_default_grades', ...
    PRIMARY KEY (node_id, grade)
);
```

- It is a sibling table, not a column. `node_grade` is unchanged, so extension
  grades remain in `node_grade` and the kind is how the sequencer tells them
  apart.
- Rows exist only where `ruling_type` gives a deterministic answer:
  `single` → core; `span` → span; `state_conditional` / `range_prose` /
  `alternative` / `unparsed` → `unconfirmed`, or, once the worksheet carries
  `ccss_default_grades`, core for those grades and state_extension for the rest.
  **No row** for leaf, `out_of_band`, or blank `ruling_type`. On the
  2026-09-09 data that is 511 of 535 `node_grade` rows (orientation §4.2).
- `state_extension` has **0 rows** until John fills `ccss_default_grades`.

**One adapter module, `mh2/grade_kind.py`, is the only code that knows that
shape.** Everything else sees this contract:

```python
STORED_KINDS = ("core", "span", "state_extension", "unconfirmed")   # = node_grade_kind.kind CHECK
READ_STATES  = STORED_KINDS + ("off_grade", "leaf", "no_grade", "unknown")

def kind_source(con) -> str: ...
    # 'grade_type' if sqlite_master has table 'node_grade_kind' AND it has >= 1 row,
    # else 'fallback' (pre-merge or pre-rebuild mh2.db)
def build_grade_kinds(con) -> dict[tuple[str, str], str]:
    """(node_id, grade) -> kind, for EVERY node_grade row (keys == node_grade).
    Value: node_grade_kind.kind when a row exists, else 'unknown'.
    Fallback: every node_grade row -> 'unknown'."""
def kind_for(node_id, grade, kinds, ruling_by_node) -> str:
    """The read state for ANY (node, grade), including grades the node lacks:
       ruling.resolution == 'leaf'            -> 'leaf'      (wins unconditionally,
                                                              as in load_grade_ruling)
       ruling.resolution == 'no_grade_field'  -> 'no_grade'
       (node_id, grade) in kinds              -> kinds[...]   (STORED_KINDS or 'unknown')
       otherwise                              -> 'off_grade'"""
```

- **Why `build_grade_kinds` fills the gaps with `unknown`:** an in-grade row
  with no kind row must never fall through to `off_grade`. That would turn the
  6 `out_of_band` nodes and the blank-type node into bridges in grades
  `node_grade` says they belong to. With leaf taking precedence, the real-data
  `unknown` set is 11 rows (out_of_band 9, blank 2). Ruling O7 records the
  optional branch change that would empty it.
- The adapter reads `node_grade_ruling.states_mentioned` / `notes` for badge
  text. It never reads `ruling_type` to re-derive a kind. That rule lives in
  `load_grade_ruling.derive_kinds` only (DEFERRED §6 "One definition").
- Worksheet-type → kind table and figures: orientation §4.2.
- **How the slice uses kind:**

  | Read state | Slice | Inventory ("owed") | Placing it |
  |---|---|---|---|
  | core, span | Solid chip. Span shows its other grades. | Counted | Normal. A span prompts for a differentiation note if placed elsewhere. |
  | unconfirmed | Solid + "unconfirmed" badge (with `states_mentioned` when set, since state_conditional sits here until ruled) | Counted, flagged | Normal |
  | unknown (fallback, or in-grade with no kind row) | Solid + "kind pending" badge | Counted | Normal. **Transitions from `unknown` never raise `grade_changed` (§6.2).** |
  | state_extension | Dimmed + "state ext" badge with `states_mentioned` / `notes` text | Not counted | Requires `confirm_off_grade` → bridge badge |
  | off_grade | Dimmed context chip (in-strip only) | Not counted | Requires `confirm_off_grade` → bridge badge |
  | leaf | Hidden unless the "leaves" toggle is on; leaf badge | Not counted | Requires `confirm_off_grade` |
  | no_grade | "No grade" context chip | Not counted | Requires `confirm_off_grade` |

### 5.3 Concept/skill label display

The label is shown with a trailing author marker removed (regex
`\s*\(\s*[A-Z][a-z]+\s*[-–—]?\s*done\s*\)\s*$`, case-insensitive). The raw
string is used for grouping and for `concept_skill_seen`. See orientation
§10 D1.

### 5.4 Period hint

`mh2/grade_slice.period_hint(con, node_id, grade)` does **not** parse prose
itself. It calls `parse_period_notes()` and `usable()` from `seq/analysis`'s
`scripts/report_period_estimates.py`. That parser is pure and was written to
"move into mh2/ unchanged". It is importable today as
`scripts.report_period_estimates`, the way the branch's own test imports it.
Moving it to `mh2/period_notes.py` with a re-export from the script is a
later tidy-up (build plan S8). Given the node's `additional_notes` in ordinal
order:

1. Keep the `Estimate`s with `e.grade == grade`. If there are none, and the
   node has exactly one `node_grade` grade, keep those with `e.grade is None`.
   If there are none and the node is multi-grade, keep the un-graded ones for
   **text only** (ruling O9).
2. `hint_value` is set only for one kept estimate that has `qualifier ==
   'exact'`, `unit == 'period'` and `'group_total' not in note`. It is
   `e.low`.
3. Everything else gives `hint_value=None` and the text only: range, open
   ended, `part_of` (shown as "≤ high"), multiple, not_fixed, embedded,
   unparsed, or unit `day` / `lesson` (O8). The payload carries `unit`,
   `qualifier`, `low` and `high` so the page can say which one it is.

The hint is **displayed and offered as a prefill**. It is written to
`placement.period_estimate` only when the builder saves it, and the text
shown at that moment goes to `period_hint_seen`.

---

## 6. The placement reconcile contract

### 6.1 Shape

- **Status is derived at read time and never stored.** `mh2/placement_status.py`
  computes it for every active placement against the current `mh2.db` on every
  request. It has to be derived: `entrypoint.sh` runs
  `rebuild.py --skip-review-reconcile`, so a stored status written by a
  reconcile pass would be stale on the hosted instance by construction.
- **`mh2/reconcile_placements.py`** is a report-only CLI, a sibling of
  `reconcile_review.py`. It writes `data/reports/placement_reconcile.txt` and
  **no rows**. It can be run by hand after any rebuild. Wiring it into
  `rebuild.py` as a tail step, after `reconcile_review` and under its own
  `--skip-placement-reconcile`, is a later, separate change. Because nothing
  depends on it, it is not in v1.
- The only writes that change a placement's anchor or snapshots are **writer
  actions**: `reattach` and `acknowledge`.

### 6.2 States

Computed in this order. The first match wins, and `relabelled` can
co-occur (it is a flag).

| State | Detection | What the grade builder sees | Actions offered |
|---|---|---|---|
| **orphaned** | `source_key` not in `nodes` | A ghost chip in its original module/slot position (position retained), labelled with `node_text_seen` + `ladder_file_seen`, and listed in **Needs attention** with successor suggestions (§6.4) | Re-attach to a suggestion or any node; remove |
| **grade_changed** | Key present; `kind_for(node, sequence.grade)` ≠ `grade_kind_seen`, **excluding** any transition from `unknown` to `core`/`span`/`unconfirmed`, and from `unconfirmed` to `core`/`span` (**Assumption (confirm):** John's `ccss_default_grades` ruling confirming an owed grade is not news to the builder; `unconfirmed → state_extension` is, and does raise) | Badge "was core → now off-grade" (etc.), listed in **Needs attention** | Keep (acknowledge, which refreshes `grade_kind_seen`; if the new state is off-grade it becomes a bridge); remove |
| **relabelled** (flag) | Key present; `concept_skill` ≠ `concept_skill_seen`, or `source_file` ≠ `ladder_file_seen` | Small info dot: "heading changed from …". **Not** in Needs attention. | Acknowledge (refreshes the snapshots) |
| **ok** | Otherwise | Normal | — |

`node_id` ≠ `node_id_seen` alone is **not a state**. `node_id` renumbers, so
the current value is shown and the snapshot is left alone.

### 6.3 Scenarios

| What happened in Word / the worksheet | `source_key` | Resulting state | Notes |
|---|---|---|---|
| Nothing (plain rebuild) | same | ok | `node_id` may still differ; ignored |
| Node reworded (any character change surviving normalization) | new key | **orphaned** | Successor: same stem + same C/S + text similarity |
| Only case or punctuation changed | same (normalization lowercases and strips non-alphanumerics) | ok | By design of `source_key()` |
| Node moved to another concept/skill in the same ladder | same | ok + relabelled | Strip position changes; predecessor badges recompute |
| Author marker removed from a heading | same | ok + relabelled | 19 labels are exposed to this today (orientation §10 D1) |
| Node moved to another stem's ladder, text unchanged | new key (stem prefix) | **orphaned** | Successor: **exact normalized-text match in another stem**, ranked first |
| Node deleted | gone | **orphaned** | No suggestion → "deleted?" |
| Ladder file renamed; `stems.csv` updated so the stem binding holds | same | ok + relabelled (ladder file) | |
| Whole ladder missing or failing to parse | — | Build-beside-then-swap (R-H5.1/5.2) keeps the old `mh2.db`, so nothing changes | If a stem *does* lose all nodes, every placement on it orphans. The report headlines "stem X: n of n placements orphaned — check ingest before acting". |
| Two nodes edited to identical text in one stem | one key survives (`UNIQUE`) | the other's placements **orphaned** | Successor: the survivor |
| Grade worksheet change removes the sequence's grade | same | **grade_changed** (→ off_grade) | Acknowledging turns it into a deliberate bridge |
| John's `ccss_default_grades` ruling makes the grade a state extension (`unconfirmed` → `state_extension`) | same | **grade_changed** (→ state_extension) | Today this would hit G2 placements of `TIM-0010` / `TIM-0011` if the review CSV's proposals are ruled as they stand |
| The same ruling confirms the grade (`unconfirmed` → `core`) | same | ok, **silently** | The `unconfirmed` exemption |
| Node becomes a leaf | same | **grade_changed** (→ leaf) | |
| grade_type branch lands and `mh2.db` is rebuilt (`unknown` → core/span/unconfirmed) | same | ok, **silently** | The `unknown` exemption. `grade_kind_seen` is not rewritten. The 11 out_of_band/blank rows stay `unknown` (O7). |
| New in-grade node appears | — | Not a placement state; the node shows as unplaced in the slice and in the "unplaced" filter | |

### 6.4 Successor suggestions (read time, never applied)

For an orphan, up to three candidates, in this order:

1. The same normalized text in any stem (stem move or collision survivor).
2. The same `stem_id_seen` and the same `concept_skill_seen` (display-normalized):
   nodes **not already actively placed in this sequence**, ranked by
   `difflib.SequenceMatcher` ratio ≥ 0.6.
3. The same stem, any C/S, ratio ≥ 0.8.

The suggestion shows both texts side by side. Re-attach is always an
explicit click.

### 6.5 Never done automatically

- Re-attaching an orphan, even on an exact-text match in another stem.
- Removing, moving, or reordering any placement.
- Changing calibration, period estimate, or notes.
- Rewriting any `*_seen` column outside `place`, `reattach`, and `acknowledge`.
- Treating a grade change as a reason to remove a placement.
- Writing anything to `mh2.db`, Word, or the review tables.

This extends the principle in `ingest_ladders.py`'s docstring: *"silently
transferring review decisions across a reworded node is how you end up with
alignments nobody actually approved."*

### 6.6 Re-attach semantics

`reattach(placement_id, new_source_key, expected_rev)` updates the placement
**in place**, so its `placement_id`, position, calibration, period, and note
carry over. It sets `reattached_from = old source_key`, refreshes all `*_seen`
columns from the new node, and writes a `reattach` event with before/after.
It refuses (409) if the new key already has an active placement in this
sequence. This is the one sanctioned change to a placement's key.

### 6.7 Report (`data/reports/placement_reconcile.txt`)

Sections, following `review_reconcile.txt`'s style: `orphaned` (grouped by
sequence → module, with `ladder_file_seen`, `node_text_seen`, and top
suggestion); `grade_changed` (seen → now); `relabelled`; and a per-stem
orphan-ratio headline.

---

## 7. Guardrail computation (`mh2/guardrail.py`, pure functions)

- Input: the active placements of a module or sequence (calibration, period_estimate).
- Output: counts by level, plus `unset`; periods by level, plus `unknown`;
  `time_coverage = n_with_estimate / n`; and the reference targets
  `COUNT_TARGET = {deep: .25, functional: .50, illuminating: .25}` and
  `TIME_TARGET = {deep: .40, functional: .45, illuminating: .15}`
  (Math Pacing Prioritization v2.0, via brief §6).
- `show_time_targets: bool = time_coverage >= TIME_MARK_MIN_COVERAGE` (0.5).
  This is `seq/analysis`'s tiering rule, where below 50% strict coverage no
  time total is shown, applied to the placements' own estimates. **Assumption
  (confirm)** on the threshold. The page draws the 40/45/15 marks only when it
  is true. The rule lives here and not in JS (R-S10). It is a display gate,
  not a verdict.
- No pass/fail field exists in the output, so the page cannot render one.

## 8. Consistency checks (`mh2/seq_checks.py`)

| Check | Rule |
|---|---|
| Off-grade / bridge | Placement read state ∈ {off_grade, state_extension, leaf, no_grade} |
| Placed before ladder predecessor | For placement P, a node Q in the same `(stem_id, concept_skill)` with `Q.seq < P.seq`, whose read state for this grade is core/span/unconfirmed/unknown, is placed later in the sequence (module order, then slot order). Warn only. |
| Predecessor unplaced | The same Q is not placed in this sequence. Info. |
| Owed and unplaced | An in-grade (core/span/unconfirmed/unknown) node with no active placement in the sequence |
| Shared code (link) | Another node in a *different stem* shares a `node_standards_parsed` code with `state IS NULL`. The badge lists partners and whether they are placed in this sequence. It is **grouped by partner stem**, one entry per stem with its codes, so one code does not fan out (180 node pairs from 30 codes; `seq/analysis` §3.5). **Not a Flagged badge** (ruling O10); it drives the separate Pairings toggle. |
| Shared code at disjoint grades | Partners' `node_grade` sets do not intersect. Info. |

`seq_checks` returns each badge with a `tier` of `structural` or `pairing`.
The Flagged filter is `tier == 'structural'`. `seq/analysis`'s
`scripts/report_cross_stem_links.py` is the reference for the shared-code and
shared-lesson pair sets. S10's acceptance cross-checks `seq_checks` against
its CSVs (`docs/review/cross_stem_links_shared_ccss.csv`: 180 rows).

---

## 9. API routes (shape only; follows `review_api.py` conventions)

Conventions: a new `seq_api.py` defines `router = APIRouter(prefix="/api/seq")`,
included into the **existing** app with one line,
`app.include_router(seq_api.router)`. App-level dependencies apply to included
routers, so the auth gate covers these routes without changing its
construction (rev 12 §5). Routes contain no SQL. They call `mh2/grade_slice.py`,
`mh2/seq_store.py`, `mh2/placement_status.py`, `mh2/guardrail.py`, and
`mh2/seq_checks.py`. Every write takes `writer: str = Depends(current_writer)`
(§11.3), and request bodies have **no** `*_by` field. Every mutating body
carries `expected_rev`.

**Static assets:** the page is served by explicit `GET` routes
(`FileResponse`), the same as `index()`. **Never `app.mount(StaticFiles)`.**
Mounted sub-apps do not run the app-level dependencies, so a mount would
serve the page unauthenticated.

| Method | Path | Purpose |
|---|---|---|
| GET | `/seq`, `/seq/assets/{name}` | Page and whitelisted JS/CSS |
| GET | `/api/seq/grades` | Grades with inventory counts and sequence ids |
| GET | `/api/seq/grades/{grade}/slice` | The slice (`?leaves=0&state_ext=1`), including kind_source and ladders-last-read |
| GET | `/api/seq/nodes/{source_key}` | Drawer payload: node band, C/S band, ruling + kind per grade, placements everywhere, links, refs, period hints |
| GET | `/api/seq/compare?keys=a,b,c,d` | Synthetic ladder table (≤4 enforced server-side), with the shared-standard rows marked |
| GET | `/api/seq/sequences?grade=` | List |
| POST | `/api/seq/sequences` | Create `{grade, title}` |
| GET | `/api/seq/sequences/{id}` | Modules → slots → placements, with derived status, badges, guardrail, `rev` |
| PATCH | `/api/seq/sequences/{id}` | Title, owner, note; archive |
| POST | `/api/seq/sequences/{id}/modules` | Create module (append) |
| PATCH | `/api/seq/modules/{id}` | Title/note |
| POST | `/api/seq/modules/{id}/move` | `{direction: up|down}` |
| DELETE | `/api/seq/modules/{id}` | Soft remove; refuses (409) while it has active placements |
| POST | `/api/seq/placements` | `{sequence_id, module_id, source_key, into_slot_id?, after_slot_id?, differentiation_note?, confirm_off_grade?}`. Returns 409 if already placed in the sequence, and 422 if off-grade without confirm. The response includes `placed_elsewhere` for the cross-grade prompt. |
| PATCH | `/api/seq/placements/{id}` | `{calibration?, period_estimate?, differentiation_note?}` against placement `rev` |
| POST | `/api/seq/placements/{id}/move` | `{direction}` or `{to_module_id, after_slot_id?}` |
| POST | `/api/seq/placements/{id}/co-place` | `{target_slot_id}` |
| POST | `/api/seq/placements/{id}/ungroup` | Split into its own slot after the current one |
| DELETE | `/api/seq/placements/{id}` | Soft remove `{reason?}` |
| POST | `/api/seq/placements/{id}/reattach` | `{source_key}` (§6.6) |
| POST | `/api/seq/placements/{id}/acknowledge` | Refresh snapshots (§6.2) |
| GET | `/api/seq/attention?sequence_id=` | Orphaned + grade_changed, with suggestions |
| GET | `/api/seq/placements?source_key=` | Where a node is placed, across sequences |
| GET | `/api/seq/sequences/{id}/events` | History (newest first, paged) |
| GET/POST/PUT/DELETE | `/api/seq/views` | Saved views for the current writer |
| GET | `/api/seq/sequences/{id}/export.xlsx` | **Deferred.** One-way derived export (orientation §7). |

**Module layout:**

| Module | Owns | DB |
|---|---|---|
| `mh2/grade_kind.py` | The grade_type adapter (§5.2) | mh2.db ro |
| `mh2/grade_slice.py` | Slice, drawer, compare payloads; label display; period hint (via `parse_period_notes`, §5.4) | mh2.db ro |
| `mh2/auth.py` | `configured_writers`, `require_writer`, `current_writer`, moved verbatim from `review_api.py` (build plan S3) | — |
| `mh2/seq_store.py` | Every SQL shape on the placement tables; stamps `*_by`/`*_at`; rev checks; events | mh2_seq.db |
| `mh2/placement_status.py` | Derived status + successors | both (mh2.db ro) |
| `mh2/guardrail.py` | Readout arithmetic | none |
| `mh2/seq_checks.py` | Badges | both (ro) |
| `mh2/reconcile_placements.py` | Report CLI | both (ro) |
| `seq_api.py` | Routes only | — |

---

## 10. What the slice payload looks like (for the S1/S2 contract)

```json
{
  "grade": "2",
  "kind_source": "fallback",
  "ladders_last_read": "2026-09-09 15:11:11",
  "counts": {"in_grade_nodes": 35, "in_grade_nodes_excl_leaf": 33,
             "concept_skills": 24, "context_nodes": 22},
  "super_stems": [
    {"domain": "Measurement and Data",
     "stems": [
       {"stem_id": "TIM", "stem_name": "Time",
        "concept_skills": [
          {"label": "<raw>", "label_display": "<marker stripped>",
           "goal": "<single non-empty goal or null>",
           "nodes": [
             {"source_key": "TIM:…", "node_id": "TIM-0010", "seq": 10,
              "node_text": "…", "grades": ["2","3"],
              "state": "unknown", "in_grade": true,
              "resolution": "ruled", "grade_raw": "3 (2 for some states)"}
           ]}]}]}]
}
```

`counts` values are illustrative (G2, 2026-09-09 build, leaves included by
default in `in_grade_nodes`). Their predicates are in orientation §4.4. With
`kind_source: "grade_type"` (after the merge and a rebuild), `TIM-0010`'s
`state` is `"unconfirmed"`. It becomes `"state_extension"` only after a
`ccss_default_grades` ruling.

---

## 11. Concurrency and hosting

### 11.1 R-H4 still holds, and the sequencer adds nothing that breaks it

It runs as one instance and one worker in the same process as the audit
(an included router, not a second app). SQLite serializes writes, and the
sequencer's writes are small. A structural operation touches at most one
container's siblings (the renumber worst case is under 100 rows). Every
multi-row write runs in `BEGIN IMMEDIATE`, so it takes the write lock up front
and never deadlocks halfway through. The default 5 s busy timeout stands.
Keep the default rollback journal. WAL would add `-wal`/`-shm` sidecars next to
a git-tracked file and complicate backups. Revisit only if lock waits are
observed.

### 11.2 No silent last-writer-wins

The review tables are upsert-last-writer-wins (DEFERRED §8, confirmed). That
is acceptable for a judgment one person owns. It is not acceptable for a
shared sequence two builders may be editing.

- **Structural writes** (place, move, co-place, ungroup, remove, module ops,
  reattach) check and bump `grade_sequence.rev`. Two builders on the same
  sequence therefore serialize. The second gets a 409 with the current
  sequence payload, and the page re-renders. At 5–10 users that is rare and
  cheap.
- **Attribute writes** (calibration, period, note) check and bump
  `placement.rev` only, so two people editing different placements do not
  collide.

### 11.3 Identity is a precondition, not a follow-up

`placed_by`, `updated_by`, `removed_by`, `actor`, and `saved_view.owner` are
stamped from the authenticated username. `require_writer` already *returns*
that name (it returns `"local"` when auth is off). Build plan S3 moves it
verbatim into `mh2/auth.py`, with `review_api.py` importing it under the same
name so the `FastAPI(dependencies=[Depends(require_writer)])` line is
unchanged, and adds `current_writer` as the per-route dependency. When Azure
Easy Auth lands, reading `X-MS-CLIENT-PRINCIPAL-NAME` is a change in that one
file. The audit's own client-supplied `reviewed_by` is **not** changed by
sequencer sessions (rev 12 §4 keeps it as a separate item).

### 11.4 The tracked binary `mh2_seq.db` once placements are high-volume (propose, don't build)

Scale: about 35–80 placements per grade × 11 grades is under 1,000 placements,
plus perhaps 10k events a year. The file stays small. The problem is
**opacity and merge-ability**, not size. FLAG A accepted binary diffs "because
hosting dissolves the concurrent-copy problem." With placements authoritative
(R-S1), that acceptance needs these companions:

1. **After hosting goes live, the server copy is the record.** Stop committing
   `mh2_seq.db` from laptops. The tracked copy becomes a seed. State it in
   HOSTING.md. Danger to name: a deploy **without** the durable mount (a
   missing volume or an unset `MH2_DATA_DIR`) silently serves the stale git
   copy, and writes are lost at the next redeploy. The S13 smoke test must
   write, restart, and read back (HOSTING.md §7 already does this for reviews).
2. **Readable history in git:** `scripts/dump_seq.py` writes
   `con.iterdump()` (stdlib) to `data/seq_export/mh2_seq.sql`. This is
   deterministic and diffable. It runs on demand or nightly on the host and is
   committed by a human. `scripts/load_seq_dump.py` restores into an empty file.
   A JSONL-per-table variant gives friendlier diffs if the SQL dump proves
   noisy.
3. **Local readability at zero risk:** `.gitattributes`
   `data/build/mh2_seq.db diff=sqlite` plus a `textconv` that prints
   `iterdump()`. Then `git diff` and `git log -p` show row changes.
4. **Backups:** `sqlite3.Connection.backup()` (an online, consistent copy) to
   durable storage daily and before every deploy, keeping N copies. This joins
   the existing "backup/snapshot schedule" question already open with IT
   (DEFERRED §8).
5. **Trigger to escalate** to a managed DB (R-H4's stated fallback): IT
   mandates scale-out, or only network storage is durable. This is unchanged.
