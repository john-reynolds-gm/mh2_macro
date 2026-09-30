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
                            CHECK (outcome IN ('confirmed','partial_coverage','not_covered')),
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
                     CHECK (outcome IN ('confirmed','partial_coverage','incorrect_tag')),
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

-- Writer's own workflow status for a standard -- where the ladder-drafting
-- work stands. Separate from standard_review on purpose: standard_review is
-- the reviewer's audit judgment on the tags (confirmed/partial_coverage/
-- not_covered); this is the writer's own progress marker on fixing the
-- standard, and the two are set by different people at different times.
-- No row means "not_yet_reviewed" (the default), same convention as
-- standard_review/standard_color_override above.
CREATE TABLE IF NOT EXISTS standard_status (
    standard_id TEXT PRIMARY KEY,
    status      TEXT NOT NULL
                CHECK (status IN ('review_complete','in_progress',
                                  'not_yet_reviewed')),
    set_by      TEXT NOT NULL,
    set_at      TEXT NOT NULL
);

-- A reviewer's ruling on a MACHINE-GENERATED suggested candidate: "this tag
-- belongs" or "it does not". Not itself a tag -- tags live only in the Word
-- ladders (§6), and an 'accepted' ruling additionally raises a tag_proposal,
-- which is the instruction to go put one there. This table exists because
-- tag_proposal cannot express a rejection: its states are all stages of a
-- proposal's life, so without somewhere to record "looked at, wrong" the
-- review tool re-surfaces the same rejected candidate after every rebuild.
--
-- Keyed on source_key rather than node_id for the same reason tag_review is
-- (R3): node_id is a content hash and decays when a writer edits node text.
-- node_id_seen, node_text_seen and ladder_file_seen are captured at write
-- time for display and for locating the node in a document; none is a key
-- and none is trusted after a rebuild -- same contract as tag_review's.
--
-- They matter most on a 'rejected' row. An 'accepted' one also raises a
-- tag_proposal, which carries its own anchor; a rejection writes nothing
-- else, and it is rejections that persist quietly and suppress a suggestion
-- from resurfacing. Without these, a stale rejection could be reported only
-- as a standard_id and an opaque source_key -- detectable, but with no way
-- for a writer to find the node it refers to.
CREATE TABLE IF NOT EXISTS candidate_ruling (
    source_key       TEXT NOT NULL,
    standard_id      TEXT NOT NULL,
    ruling           TEXT NOT NULL CHECK (ruling IN ('accepted','rejected')),
    node_id_seen     TEXT,
    node_text_seen   TEXT,
    ladder_file_seen TEXT,
    ruled_by         TEXT NOT NULL,
    ruled_at         TEXT NOT NULL,
    PRIMARY KEY (source_key, standard_id)
);
CREATE INDEX IF NOT EXISTS ix_candidate_ruling_standard
    ON candidate_ruling(standard_id);
