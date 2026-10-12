-- FROZEN COPY of mh2/schema_placement.sql at v1 (commit 4390927), for the v1 -> v2
-- migration test in tests/test_seq_store.py. Do not edit.
-- ============================================================================
-- mh2/schema_placement.sql — grade sequencing authored state (in mh2_seq.db).
--
-- AUTHORITATIVE for sequences, modules, slots, placements and per-placement
-- attributes ONLY (docs/seq_orientation_rev1.md §2, ruling R-S1). Holds no
-- tags, no node content, no grade rulings. Nothing reads these tables back
-- into mh2.db, and the gap audit never reads them (R-S3).
--
-- Applied by mh2/seq_store.ensure_schema ONLY (CREATE ... IF NOT EXISTS).
-- mh2/schema_seq.sql and mh2/review_store never read this file, and
-- scripts/rebuild.py does not apply it. Same database file (config.SEQ_DB).
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
                   'slot_update','slot_move','slot_merge',   -- v1 contract §3.4
                   'place','move','co_place','ungroup','remove',   -- 'move' unused in v1
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
