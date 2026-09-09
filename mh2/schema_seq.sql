-- ============================================================================
-- mh2_seq.db — review write-path state (Session C).
--
-- Separate database from mh2.db on purpose: rebuild.py unlinks and rebuilds
-- mh2.db from data/source on every run (scripts/rebuild.py), and this state
-- must survive that. No cross-database foreign keys are possible -- that is
-- what mh2/reconcile_review.py exists for: it is the only writer of these
-- tables and the only place referential drift against mh2.db is detected.
--
-- `color`, not `colour`: coverage.StandardRow already spells it `color`
-- (mh2/coverage.py), so there is no established `colour` spelling anywhere
-- in this codebase to be consistent with -- see DEFERRED.md.
-- ============================================================================

PRAGMA foreign_keys = ON;

-- Standard-level review. The durable record: standard codes do not move.
CREATE TABLE IF NOT EXISTS standard_review (
    standard_id             TEXT PRIMARY KEY,
    outcome                 TEXT NOT NULL
                            CHECK (outcome IN ('confirmed','insufficient')),
    computed_color_at_review TEXT NOT NULL,
    reviewed_by             TEXT NOT NULL,
    reviewed_at             TEXT NOT NULL,
    note                    TEXT
);

-- Writer color override. Separate from standard_review on purpose: a writer
-- may confirm without overriding, or override without confirming, and
-- conflating them loses which they did.
--
-- computed_color_at_set records what the rollup said WHEN the override was
-- written. Without it, a later rebuild cannot distinguish an override that
-- has become redundant from one that still stands.
CREATE TABLE IF NOT EXISTS standard_color_override (
    standard_id           TEXT PRIMARY KEY,
    writer_color          TEXT NOT NULL
                          CHECK (writer_color IN ('Green','Yellow','Red')),
    computed_color_at_set TEXT NOT NULL,
    reason                TEXT NOT NULL,
    set_by                TEXT NOT NULL,
    set_at                TEXT NOT NULL
);

-- Tag-level review: is THIS tag adequate for this standard?
-- Keyed on source_key, not node_id (R3). node_id_seen, node_text_seen and
-- ladder_file_seen are captured at write time for display and for locating
-- the node in a document; none is a key and none is trusted after a rebuild.
CREATE TABLE IF NOT EXISTS tag_review (
    standard_id      TEXT NOT NULL,
    source_key       TEXT NOT NULL,
    outcome          TEXT NOT NULL
                     CHECK (outcome IN ('confirmed','insufficient','wrong_node')),
    node_id_seen     TEXT,
    node_text_seen   TEXT,
    ladder_file_seen TEXT,
    reviewed_by      TEXT NOT NULL,
    reviewed_at      TEXT NOT NULL,
    note             TEXT,
    PRIMARY KEY (standard_id, source_key)
);

-- Proposed tags. This is an instruction to a human editing a Word document,
-- which is why it carries node_text_seen and ladder_file rather than just
-- identifiers.
--
-- Not keyed on (standard_id, source_key): a withdrawn proposal and a later
-- re-proposal of the same pair are two events, and the history matters.
CREATE TABLE IF NOT EXISTS tag_proposal (
    proposal_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    standard_id    TEXT NOT NULL,
    source_key     TEXT NOT NULL,
    node_id_seen   TEXT,
    node_text_seen TEXT NOT NULL,
    ladder_file    TEXT,
    rationale      TEXT,
    proposed_by    TEXT NOT NULL,
    proposed_at    TEXT NOT NULL,
    state          TEXT NOT NULL DEFAULT 'open'
                   CHECK (state IN ('open','landed','landed_elsewhere',
                                    'needs_attention','withdrawn')),
    resolved_at    TEXT,
    resolved_note  TEXT
);
CREATE INDEX IF NOT EXISTS ix_proposal_open ON tag_proposal(state, ladder_file);
CREATE INDEX IF NOT EXISTS ix_tag_review_source ON tag_review(source_key);
