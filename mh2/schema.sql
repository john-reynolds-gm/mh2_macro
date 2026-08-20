-- ============================================================================
-- MH2 Stems & Leaves — core schema
--
-- Design notes:
--   * node_id is DB-assigned and opaque. Nodes are matched across re-ingests
--     by source_key (stem + normalized node text), NOT by position or slug.
--     Editing node wording creates a new source_key, so re-ingest reports it
--     as "changed" rather than silently orphaning review decisions.
--   * node_fields is entity-attribute-value on purpose. The ladders already
--     disagree on field names (Goal/Goals, Knowledge Graph Node/Rough Lesson
--     Objectives) and new stems will invent more. Normalization happens at
--     ingest; the schema never needs to change.
--   * node_standards.relation carries the leaf signal:
--       aligned  - node fully covers the standard
--       partial  - node covers part of it; more nodes needed
--       exceeds  - standard demands something the node does NOT teach
--                  => this row IS a candidate leaf
--   * candidates is regenerable output from the matching pipeline. Never
--     authoritative. node_standards is authoritative.
--
-- Portability: plain SQL, no SQLite-only syntax beyond AUTOINCREMENT.
-- Swap to Postgres by changing INTEGER PRIMARY KEY AUTOINCREMENT -> SERIAL.
-- ============================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- structure

CREATE TABLE IF NOT EXISTS stems (
    stem_id      TEXT PRIMARY KEY,      -- short code, e.g. 'CNT'
    name         TEXT NOT NULL,         -- 'Counting'
    domain       TEXT,                  -- 'Number Systems and Structures'
    band         TEXT,                  -- 'PK-2', '3-5', ...
    status       TEXT DEFAULT 'draft'
);

-- The recorded ladder <-> workbook stem mapping, loaded verbatim from
-- stems.csv. This is hand-maintained DATA, not something to infer: three naming
-- surfaces disagree and none of the disagreements is recoverable from string
-- similarity.
--
-- masterlist_name repeats by design (NSS_COM and NSS_ORD are both 'Comparing
-- and Ordering'), and so does ladder_file (one ladder, two workbook stems).
-- Any code assuming one ladder maps to one stem is wrong, which is why the
-- primary key is stem_id alone and nothing else is unique.
--
-- stem_id is an OPAQUE key. The slugs are provisional and may be replaced with
-- the team's own stem codes -- join on it, never pattern-match it.
CREATE TABLE IF NOT EXISTS stem_map (
    stem_id         TEXT PRIMARY KEY,
    band            TEXT NOT NULL,         -- PK5 | 6_9
    stem_group      TEXT,
    masterlist_name TEXT,
    workbook_sheet  TEXT,
    workbook_stem   TEXT,                  -- verbatim, embedded newline kept
    ladder_file     TEXT,                  -- as written in stems.csv
    ladder_drafted  INTEGER NOT NULL,
    workbook_stem_id TEXT,                 -- resolved against stems(stem_id)
    ladder_path     TEXT                   -- resolved file on disk
);

-- Concept/Skill rows from H2_Stem_and_Leaf_Spreadsheet.
-- This is the team's PRE-ladder inventory and current hand-tagging surface.
CREATE TABLE IF NOT EXISTS concepts (
    concept_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    stem_id      TEXT REFERENCES stems(stem_id),
    stem_name    TEXT,                  -- as written in the workbook
    text         TEXT NOT NULL,
    details      TEXT,
    grade_leaf   TEXT,
    source_sheet TEXT
);

-- Nodes from the Learning Ladder docs.
CREATE TABLE IF NOT EXISTS nodes (
    node_id      TEXT PRIMARY KEY,      -- opaque, e.g. 'CNT-0007'
    stem_id      TEXT REFERENCES stems(stem_id),
    seq          INTEGER,               -- document order, display only
    source_key   TEXT UNIQUE NOT NULL,  -- stem_id + normalized node text
    node_text    TEXT NOT NULL,
    concept_skill TEXT,                 -- heading the node sits under
    goal         TEXT,
    grade_or_leaf TEXT,
    status       TEXT DEFAULT 'draft',
    source_file  TEXT,
    first_seen   TEXT DEFAULT CURRENT_TIMESTAMP,
    last_seen    TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS node_fields (
    node_id      TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
    field        TEXT NOT NULL,         -- 'specifications', 'misconceptions', ...
    ordinal      INTEGER NOT NULL,
    value        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_node_fields ON node_fields(node_id, field);

-- ---------------------------------------------------------------- standards

CREATE TABLE IF NOT EXISTS standards (
    standard_id  TEXT PRIMARY KEY,      -- normalized code: 'TX.K.2A', 'K.CC.A.1'
    jurisdiction TEXT NOT NULL,         -- 'CCSS', 'TX', 'CA', ...
    grade        TEXT,
    text         TEXT,
    is_ccss      INTEGER DEFAULT 0,
    is_big_three INTEGER DEFAULT 0,
    source       TEXT
);
CREATE INDEX IF NOT EXISTS ix_std_juris ON standards(jurisdiction, grade);

-- State standard -> CCSS crosswalk. Path B: boosts a candidate, never
-- originates one. `source` is IN the primary key on purpose -- the same pair
-- arriving from both scored_alignments and learnosity is two rows, and that
-- agreement is the corroboration signal, not a duplicate to collapse.
-- Path B edges. LEARNOSITY ONLY -- one row per edge.
--
-- Rev 3 had `source` in the primary key and unioned two files into this table.
-- That was wrong: scored_alignments is an AUDIT of learnosity, not a second
-- edge source. It was produced by combining lesson overlap (Path A's method),
-- the Path C model, and standards text, then asking a model to rule on each
-- learnosity tag. Unioning recorded one claim as two rows and let a
-- row-counting boost treat an audit of a claim as independent corroboration of
-- it. The audit is joined on (state_code, ccss_code) and attached as columns.
-- Measured, not assumed: the audit's anchor codes overlap all_states at 84%
-- and learnosity at 3.9%, and that 3.9% is entirely Massachusetts, the one
-- state whose own prefix collides with the item bank's 'MA' (Mathematics) one.
-- So the audit is NOT about these edges and carries no columns here. It audits
-- the all_states tag set -- see alignment_audit below.
CREATE TABLE IF NOT EXISTS crosswalk (
    state            TEXT NOT NULL,
    state_code       TEXT NOT NULL,      -- shortened: joins the rest of the project
    ccss_code        TEXT NOT NULL,
    -- The item-bank tag path exactly as learnosity wrote it, e.g.
    -- 'IN.2023.MATH.1.NS.1' where state_code is 'IN.1.NS.1'. Kept because it is
    -- the only key back to the Learnosity items, and because it makes the
    -- shortening auditable rather than a silent rewrite.
    source_code      TEXT,
    -- Level of the STATE side of the edge: Standard, Expectation, Benchmark,
    -- Cluster, Domain, Heading... Not every edge joins two peers. Learnosity
    -- pairs a CCSS standard with a state Cluster 55 times and a state Heading
    -- twice, and those are edges between different levels of the tree. Without
    -- this column they look identical to real standard-to-standard edges and
    -- the generator cannot down-weight them.
    granularity      TEXT,
    PRIMARY KEY (state_code, ccss_code)
);

-- scored_alignments' verdicts. The audited subject is an all_states
-- (state code -> CCSS code) tag, which is PATH A's state side, not a Path B
-- edge. Rev 4 assumed it audited learnosity; the codes say otherwise.
--
-- This attaches to Path A candidates as a flag. It can never CORROBORATE Path A
-- (§4 independence) -- the audit was produced by running lesson overlap, which
-- is Path A's own method, so agreement here is a method agreeing with itself.
-- Use it to demote a tag the auditor rejected, never to promote one it liked.
CREATE TABLE IF NOT EXISTS alignment_audit (
    state      TEXT NOT NULL,
    state_code TEXT NOT NULL,
    ccss_code  TEXT NOT NULL,          -- the tag being ruled on
    verdict    TEXT,                   -- confirmed | likely_correct | likely_incorrect | uncertain
    confidence REAL,                   -- effectively binary; use presence, not magnitude
    rationale  TEXT,
    PRIMARY KEY (state_code, ccss_code)
);

-- Where the audit proposed a DIFFERENT CCSS code than the tag it was ruling on.
-- Only those rows: a row that restates the code it just confirmed suggests
-- nothing. That is ~641 of 9,076.
--
-- These are Path C evidence and yield `weak`. They must never corroborate Path
-- A or Path C (§4 independence) -- the audit was built from both.
CREATE TABLE IF NOT EXISTS model_suggestions (
    state      TEXT NOT NULL,
    state_code TEXT NOT NULL,
    ccss_code  TEXT NOT NULL,          -- the RECOMMENDED code, not the audited one
    rationale  TEXT,
    PRIMARY KEY (state_code, ccss_code)
);

-- Path A: which lessons a standard is tagged to, on both sides of the join.
CREATE TABLE IF NOT EXISTS standard_lessons (
    standard_code TEXT NOT NULL,
    lesson_id     TEXT NOT NULL,
    source        TEXT NOT NULL,          -- guide | metadata | all_states
    PRIMARY KEY (standard_code, lesson_id, source)
);
CREATE INDEX IF NOT EXISTS ix_sl_lesson ON standard_lessons(lesson_id);

-- The tagging workbook's work queue: has this standard been tagged to a stem?
CREATE TABLE IF NOT EXISTS standard_tag_status (
    standard_code   TEXT PRIMARY KEY,
    sheet           TEXT NOT NULL,
    tagged_to_stem  INTEGER NOT NULL,     -- 1 = Yes, 0 = blank
    note_tagging    TEXT,
    note_postladder TEXT
);

-- Offline model output, ingested at step 8 from data/source/predictions/*.jsonl.
--
-- `direction` is in the primary key, and that is a change from the spec's §6.
-- Measured reason: the SAME (state_code, ccss_code) pair arrives from two runs
-- pointing opposite ways -- state_to_ccss_k50 and ccss_to_{ca,fl,tx} -- and
-- they are not the same measurement. Their scores do not even share a range:
--
--     file                    median top-1 bi_score    tier spread
--     state_to_ccss_k50               0.604            Strong 91 / Moderate 726 / Weak 1081 / No Match 384
--     ccss_to_ca_k50                  0.121            No Match 317  (100%)
--     ccss_to_fl_k50                  0.159            No Match 317  (100%)
--     ccss_to_tx_k100                 0.190            No Match 317  (100%)
--     big_three_to_big_three_k25      0.273            No Match 918  (100%)
--
-- The tier thresholds were calibrated for the state->CCSS direction and were
-- then applied to every other run unchanged. Do NOT read those 100% `No Match`
-- columns as "the model found nothing": ccss_to_tx reaches 99% of Texas' truth
-- codes. Without `direction` in the key, one arbitrary row per pair survives
-- and there is no way to tell which run it came from.
--
-- `tier` is the model's own label. It sits on the ANCHOR in the source file,
-- not on the individual prediction, so every row for one anchor repeats it.
-- Carried verbatim, not recomputed -- see the calibration note above before
-- using it as a filter.
--
-- `model_version` is part of the key per §6, and the offline run did NOT stamp
-- one. Rather than invent a plausible-looking version, the loader records
-- 'unstamped:<file>@<content hash>': honest about the gap, stable across
-- reloads, and it changes the moment the file does.
CREATE TABLE IF NOT EXISTS model_predictions (
    state         TEXT NOT NULL,        -- from the CODE prefix, not the label
    state_code    TEXT NOT NULL,
    ccss_code     TEXT NOT NULL,
    direction     TEXT NOT NULL,        -- state_to_ccss | ccss_to_state
    rank          INTEGER NOT NULL,
    score         REAL NOT NULL,
    tier          TEXT,                 -- Strong | Moderate | Weak | No Match
    model_version TEXT NOT NULL,
    PRIMARY KEY (direction, state_code, ccss_code, model_version)
);
CREATE INDEX IF NOT EXISTS ix_mp_ccss ON model_predictions(ccss_code, state);

-- §3.5.1: state -> state predictions, for the 28 nodes that carry state codes
-- an author wrote by hand but no CCSS code at all. The spec says this run "does
-- not exist yet"; it does now, in two files:
--
--     big_three_to_big_three_k25    918 Big Three anchors -> Big Three targets
--     state_gaps_to_big_three_k50 1,364 other-state anchors -> Big Three targets
--
-- Kept OUT of model_predictions on purpose: both sides are state codes, so
-- there is no ccss_code to key on and forcing one in would mean lying about
-- which column holds what. Nothing reads this table yet -- wiring it into the
-- generator is the §3.5.1 line item, not step 8.
--
-- Same-state pairs (FL.K.NSO.1.1 -> FL.K.GR.1.2) are dropped at load. A state
-- standard corresponding to another standard in its own state answers a
-- question nobody asked here.
CREATE TABLE IF NOT EXISTS model_state_predictions (
    anchor_state  TEXT NOT NULL,
    anchor_code   TEXT NOT NULL,
    state         TEXT NOT NULL,
    state_code    TEXT NOT NULL,
    direction     TEXT NOT NULL,        -- state_to_state
    rank          INTEGER NOT NULL,
    score         REAL NOT NULL,
    tier          TEXT,
    model_version TEXT NOT NULL,
    PRIMARY KEY (anchor_code, state_code, model_version)
);
CREATE INDEX IF NOT EXISTS ix_msp_anchor ON model_state_predictions(anchor_code);

-- Reranker output (mh2/rerank.py), loaded from the durable cache at
-- data/reranks/cache.jsonl -- see mh2/load_reranks.py. Rebuilding never calls
-- the API: this table is populated from whatever is already cached, and stays
-- empty for a (node, state) pair nobody has paid to rerank yet.
--
-- Only the top RERANK_DEPTH-capped, top-10-truncated reranked order is kept.
-- This is the "browse" list step 7's writer-facing design puts behind a
-- collapsible menu, distinct from `candidates.auto_surface` -- a code can
-- have a browse_rank and still fail the SURFACE_THRESHOLD cut, and that is
-- the whole point of having both.
CREATE TABLE IF NOT EXISTS model_reranks (
    node_id        TEXT NOT NULL,
    state          TEXT NOT NULL,
    standard_code  TEXT NOT NULL,
    rerank_rank    INTEGER NOT NULL,     -- 1-based position after reranking
    relevance      TEXT,                 -- aligned | partial | unrelated
    model_version  TEXT NOT NULL,
    PRIMARY KEY (node_id, state, standard_code)
);
CREATE INDEX IF NOT EXISTS ix_reranks_node ON model_reranks(node_id, state);

-- ------------------------------------------------------------ the alignment

-- Hand tags carried over from the stem/leaf workbook, keyed to CONCEPTS.
-- These get promoted into node_standards once concepts are matched to nodes.
CREATE TABLE IF NOT EXISTS concept_standards (
    concept_id   INTEGER NOT NULL REFERENCES concepts(concept_id) ON DELETE CASCADE,
    standard_id  TEXT NOT NULL,
    bucket       TEXT,                  -- 'ccssm' | 'big_three' | 'other'
    annotation   TEXT,                  -- inline '--added MF 5/29' style notes
    PRIMARY KEY (concept_id, standard_id)
);

-- Authoritative node -> standard alignment.
CREATE TABLE IF NOT EXISTS node_standards (
    node_id      TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
    standard_id  TEXT NOT NULL,
    relation     TEXT DEFAULT 'aligned' CHECK (relation IN ('aligned','partial','exceeds')),
    caveat       TEXT,                  -- 'requires counting by tens from any multiple of five'
    source       TEXT,                  -- 'ladder_doc' | 'stem_workbook' | 'model' | 'human'
    confidence   REAL,
    status       TEXT DEFAULT 'proposed' CHECK (status IN ('proposed','accepted','rejected')),
    reviewed_by  TEXT,
    reviewed_at  TEXT,
    PRIMARY KEY (node_id, standard_id)
);
CREATE INDEX IF NOT EXISTS ix_ns_std ON node_standards(standard_id);

-- Node-level hand tags, parsed out of the ladders' 'Notes related to
-- Standards, Grade, or Leaf' cell. Layer 1: regenerated on every rebuild.
--
-- No uniqueness constraint beyond the primary key, and none wanted. One
-- standard may legitimately sit on several nodes within a concept/skill --
-- that is normal authoring, not a data error, and deduping it would destroy
-- the node assignment the author made.
CREATE TABLE IF NOT EXISTS node_standards_parsed (
    node_id        TEXT NOT NULL,
    standard_code  TEXT NOT NULL,
    state          TEXT,                   -- NULL for CCSS
    relation       TEXT,                   -- aligned | partial | exceeds
    annotation     TEXT,
    source_cell    TEXT NOT NULL,
    PRIMARY KEY (node_id, standard_code)
);
-- 'absent' is NOT a relation value here. An absence assertion carries no
-- standard_code, so it cannot be a row in this table at all.

-- 'No CCSSM for ordinal numbers' -- the author asserting that a FRAMEWORK does
-- not cover this node. It carries no code, so it cannot live above, and no
-- sentinel code may be invented to force it there.
--
-- Scopes to the named framework ONLY. NEVER used to filter or suppress state
-- candidate generation: a node CCSS omits is exactly where a state standard is
-- most likely to exist and most valuable to surface. That is the definition of
-- a leaf.
CREATE TABLE IF NOT EXISTS node_absence_assertions (
    node_id     TEXT NOT NULL,
    framework   TEXT NOT NULL,             -- CCSS | unknown
    note        TEXT NOT NULL,             -- verbatim author text
    source_cell TEXT NOT NULL,
    PRIMARY KEY (node_id, framework, note)
);

-- Regenerable pipeline output. Evidence for review, never truth. Written at
-- step 6; empty until then.
--
-- No blended score: every path's score is carried independently, and
-- `strength` is derived from how many agree. No uniqueness constraint beyond
-- the surrogate key either -- a (concept_skill, standard_code) pair appearing
-- on three nodes is three rows, because one standard may legitimately sit on
-- several nodes.
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id     INTEGER PRIMARY KEY,
    run_id           INTEGER NOT NULL,
    node_id          TEXT,
    concept_skill_id TEXT NOT NULL,
    state            TEXT NOT NULL,
    standard_code    TEXT NOT NULL,
    anchor_ccss_code TEXT,                -- NULL for the §3.5.1 state-anchored rows
    -- §3.5.1: the 28 nodes with no CCSS code still carry state codes an author
    -- wrote by hand, and those are the anchor. Declared here so the two kinds
    -- of row are one table; nothing writes it until the state->state model run
    -- exists.
    anchor_state_code TEXT,
    score_ca_code    REAL,
    score_overlap    REAL,
    score_crosswalk  REAL,
    score_model      REAL,
    grade_offset     INTEGER,
    combined_score   REAL NOT NULL,
    strength         TEXT NOT NULL,       -- strong | moderate | weak
    node_confidence  REAL,
    -- Step 7's precision-first cut (rev 5, John, 2026-08-17). 1 whenever a
    -- deterministic path (strong/moderate) supports the row; for TX/FL's
    -- weak, Path-C-only rows, 1 iff combined_score clears SURFACE_THRESHOLD.
    -- This is what gets shown to a writer outright.
    auto_surface     INTEGER NOT NULL,
    -- This row's 1-based position in the reranked top-10 for this
    -- (node, state), or NULL if it has none -- either because reranking
    -- hasn't been run for this node yet, or the reranker didn't place it in
    -- its top 10. The writer-facing "show me more" collapsible menu.
    browse_rank      INTEGER,
    evidence_json    TEXT NOT NULL,
    generated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cand_cs ON candidates(concept_skill_id, state);
CREATE INDEX IF NOT EXISTS idx_cand_node ON candidates(node_id, state);

-- Leaves: state-specific requirements that hang off a stem.
CREATE TABLE IF NOT EXISTS leaves (
    leaf_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    stem_id      TEXT REFERENCES stems(stem_id),
    node_id      TEXT REFERENCES nodes(node_id),   -- nullable: orphan leaves exist
    jurisdiction TEXT NOT NULL,
    standard_id  TEXT,
    category     TEXT,                  -- from the gaps sheet taxonomy
    description  TEXT,
    origin       TEXT,                  -- 'gaps_sheet' | 'exceeds_review' | 'uncovered'
    status       TEXT DEFAULT 'proposed'
);

-- Existing product references (EM2 / TX BB lesson citations on a node).
CREATE TABLE IF NOT EXISTS product_refs (
    node_id      TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
    product      TEXT,                  -- 'EM2', 'TX BB'
    raw_ref      TEXT NOT NULL,
    lesson_id    TEXT
);

-- Cross-stem links: 'Associated Concepts from Other Stems' + computed overlap.
CREATE TABLE IF NOT EXISTS node_links (
    node_id      TEXT NOT NULL,
    related_text TEXT,                  -- as written, before resolution
    related_node_id TEXT,               -- resolved, nullable
    link_type    TEXT,                  -- 'stated' | 'shared_standard' | 'similarity'
    score        REAL
);

-- Ingest audit trail.
CREATE TABLE IF NOT EXISTS ingest_log (
    run_id       TEXT,
    ts           TEXT DEFAULT CURRENT_TIMESTAMP,
    source_file  TEXT,
    action       TEXT,
    detail       TEXT
);
