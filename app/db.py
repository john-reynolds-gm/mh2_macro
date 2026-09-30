"""
db.py — Review page data access.

Every query here is read-heavy and cheap (the CA/TX/FL suggested-candidate
pool is under 1,000 rows). Nothing is precomputed into a separate table:
status is always derived live at query time from `node_standards_parsed`
and from `candidate_ruling`, per the build brief — never from a workbook's
static column.

The one write path, `write_ruling()`, records the reviewer's judgment in
`mh2_seq.db` — the durable review database. It writes NOTHING to `mh2.db`,
which `scripts/rebuild.py` destroys and rebuilds from data/source on every
run; a ruling stored there would be silently lost at the next rebuild.
(This docstring previously claimed `node_standards` was "authoritative,
never touched by a rebuild". That was false from the moment rebuild.py
started unlinking the database, and the write path has been moved rather
than the claim repaired.)

The tags themselves are not written anywhere: they live in the Word
ladders, which stay the single source of truth (§6). An accepted candidate
raises a `tag_proposal` — an instruction to a human to go edit that ladder.
"""

import csv
import json
import sqlite3
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from mh2 import node_lookup, review_store  # noqa: E402

PRIORITY_STATES = ["CA", "TX", "FL"]


@st.cache_resource
def get_connection():
    con = sqlite3.connect(str(config.DB), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


@st.cache_resource
def get_seq_connection():
    """mh2_seq.db — durable review state, the database a rebuild never
    destroys. Schema is ensured here so the tool works on a checkout that
    has not run a rebuild yet, same as review_api.py's `_seq_con()`."""
    con = sqlite3.connect(str(config.SEQ_DB), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    review_store.ensure_schema(con, config.SCHEMA_SEQ)
    return con


def _rows(con, sql, params=()):
    return [dict(r) for r in con.execute(sql, params).fetchall()]


# ------------------------------------------------------------- sidebar tree

@st.cache_data
def domain_tree(_con, version: int):
    """domain -> stem list, each carrying node counts and a ruled/total tally
    over the CA/TX/FL suggested-candidate pool (the thing a reviewer actually
    works through one at a time — see `suggested_pool` docstring)."""
    con = _con
    stems = _rows(con, """
        SELECT s.stem_id, s.name, s.domain, sm.stem_group, sm.band
        FROM stems s
        LEFT JOIN stem_map sm ON sm.stem_id = s.stem_id
    """)
    node_counts = dict(con.execute(
        "SELECT stem_id, COUNT(*) FROM nodes GROUP BY stem_id").fetchall())
    ruled_totals = _ruled_totals_by_stem(con)

    domains = {}
    for row in stems:
        domain = row["domain"] or row["stem_group"] or "Other"
        ruled, total = ruled_totals.get(row["stem_id"], (0, 0))
        domains.setdefault(domain, []).append({
            "stem_id": row["stem_id"],
            "name": (row["name"] or "").replace("\n", " "),
            "n_nodes": node_counts.get(row["stem_id"], 0),
            "ruled": ruled,
            "total": total,
        })
    return domains


def _ruled_totals_by_stem(con):
    """stem_id -> (ruled, total) over the suggested-candidate pool.

    The tally is assembled in Python rather than by SQL `GROUP BY` because
    `ruled` now comes from `candidate_ruling` in the other database. Rulings
    are keyed on `source_key`, so node_ids are translated through
    `node_lookup` — the one sanctioned place that query is issued.
    """
    rows = con.execute("""
        SELECT n.stem_id, c.node_id, c.standard_code
        FROM candidates c
        JOIN nodes n ON n.node_id = c.node_id
        LEFT JOIN node_standards_parsed nsp
               ON nsp.node_id = c.node_id AND nsp.standard_code = c.standard_code
              AND nsp.state = c.state
        WHERE c.auto_surface = 1 AND c.state IN ('CA','TX','FL')
          AND nsp.node_id IS NULL
    """).fetchall()
    nodes = node_lookup.build_node_lookup(con)
    ruled_keys = set(review_store.list_candidate_rulings(get_seq_connection()))

    totals: dict[str, list[int]] = {}
    for r in rows:
        tally = totals.setdefault(r["stem_id"], [0, 0])
        tally[1] += 1
        info = nodes.get(r["node_id"])
        if info and (info.source_key, r["standard_code"]) in ruled_keys:
            tally[0] += 1
    return {stem_id: (ruled, total) for stem_id, (ruled, total) in totals.items()}


@st.cache_data
def concept_skills_for_stem(_con, stem_id: str, version: int):
    """Ordered concept/skills for a stem, each with a ruled/total tally."""
    con = _con
    cs_list = _rows(con, """
        SELECT DISTINCT concept_skill, MIN(seq) AS first_seq
        FROM nodes WHERE stem_id = ? GROUP BY concept_skill
        ORDER BY first_seq
    """, (stem_id,))
    pool = suggested_pool(con, version, stem_id=stem_id)
    totals = {}
    for row in pool:
        key = row["concept_skill"]
        t = totals.setdefault(key, [0, 0])
        t[1] += 1
        if row["ruled"]:
            t[0] += 1
    for cs in cs_list:
        ruled, total = totals.get(cs["concept_skill"], (0, 0))
        cs["ruled"], cs["total"] = ruled, total
    return cs_list


@st.cache_data
def nodes_for_concept_skill(_con, stem_id: str, concept_skill: str, version: int):
    con = _con
    nodes = _rows(con, """
        SELECT node_id, seq, node_text, goal, grade_or_leaf
        FROM nodes WHERE stem_id = ? AND concept_skill = ?
        ORDER BY seq
    """, (stem_id, concept_skill))
    ccss_by_node = _ccss_anchors_by_node(con, [n["node_id"] for n in nodes])
    pool = suggested_pool(con, version, stem_id=stem_id, concept_skill=concept_skill)
    totals = {}
    for row in pool:
        t = totals.setdefault(row["node_id"], [0, 0])
        t[1] += 1
        if row["ruled"]:
            t[0] += 1
    for n in nodes:
        n["anchors"] = ccss_by_node.get(n["node_id"], [])
        n["ruled"], n["total"] = totals.get(n["node_id"], (0, 0))
    return nodes


def _ccss_anchors_by_node(con, node_ids):
    if not node_ids:
        return {}
    q = ",".join("?" * len(node_ids))
    rows = con.execute(f"""
        SELECT node_id, standard_code FROM node_standards_parsed
        WHERE state IS NULL AND node_id IN ({q})
        ORDER BY standard_code
    """, node_ids).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["node_id"], []).append(r["standard_code"])
    return out


# ---------------------------------------------------------------- one node

@st.cache_data
def get_node(_con, node_id: str, version: int):
    con = _con
    row = con.execute(
        "SELECT * FROM nodes WHERE node_id = ?", (node_id,)).fetchone()
    if not row:
        return None
    node = dict(row)
    node["fields"] = _rows(con, """
        SELECT field, ordinal, value FROM node_fields
        WHERE node_id = ? ORDER BY field, ordinal
    """, (node_id,))
    node["anchors"] = [r["standard_code"] for r in con.execute(
        "SELECT standard_code FROM node_standards_parsed"
        " WHERE node_id = ? AND state IS NULL ORDER BY standard_code",
        (node_id,)).fetchall()]
    node["has_absence_assertion"] = con.execute(
        "SELECT 1 FROM node_absence_assertions WHERE node_id = ? LIMIT 1",
        (node_id,)).fetchone() is not None
    return node


def field_text(node: dict, field: str) -> str:
    vals = [f["value"] for f in node["fields"] if f["field"] == field]
    return "\n".join(vals)


# ------------------------------------------------------ the suggested pool

@st.cache_data
def suggested_pool(_con, version: int, stem_id: str = None,
                    concept_skill: str = None, node_id: str = None,
                    states=None):
    """Candidates a reviewer works one at a time: auto_surface=1, restricted
    to `states` (default CA/TX/FL), excluding any (node, state, code) already
    present as a ladder-authored tag in `node_standards_parsed` — those are
    the 'From the ladder' chips, already trusted, not up for review.

    Each row carries the live ruling (None / 'accepted' / 'rejected') read
    from `candidate_ruling` in mh2_seq.db and attached below — not joined,
    because it is a different database — and never cached separately.
    """
    con = _con
    states = states or PRIORITY_STATES
    where = ["c.auto_surface = 1", f"c.state IN ({','.join('?' * len(states))})"]
    params = list(states)
    if node_id:
        where.append("c.node_id = ?")
        params.append(node_id)
    elif concept_skill is not None and stem_id:
        where.append("n.stem_id = ? AND n.concept_skill = ?")
        params += [stem_id, concept_skill]
    elif stem_id:
        where.append("n.stem_id = ?")
        params.append(stem_id)

    sql = f"""
        SELECT c.node_id, n.concept_skill, c.state, c.standard_code,
               c.strength, c.combined_score, c.grade_offset, c.evidence_json,
               c.browse_rank, std.text AS standard_text, std.grade,
               mr.relevance AS rerank_relevance, mr.rerank_rank
        FROM candidates c
        JOIN nodes n ON n.node_id = c.node_id
        LEFT JOIN node_standards_parsed nsp
               ON nsp.node_id = c.node_id AND nsp.standard_code = c.standard_code
              AND nsp.state = c.state
        LEFT JOIN standards std ON std.standard_id = c.standard_code
        LEFT JOIN model_reranks mr
               ON mr.node_id = c.node_id AND mr.state = c.state
              AND mr.standard_code = c.standard_code
        WHERE {' AND '.join(where)} AND nsp.node_id IS NULL
        ORDER BY c.node_id, c.state,
                 CASE WHEN mr.rerank_rank IS NOT NULL THEN mr.rerank_rank ELSE 999 END,
                 c.combined_score DESC
    """
    rows = _rows(con, sql, params)
    nodes = node_lookup.build_node_lookup(con)
    rulings = review_store.list_candidate_rulings(get_seq_connection())
    for r in rows:
        info = nodes.get(r["node_id"])
        ruling = rulings.get((info.source_key, r["standard_code"])) if info else None
        r["ruled_status"] = ruling["ruling"] if ruling else None
        r["reviewed_by"] = ruling["ruled_by"] if ruling else None
        r["reviewed_at"] = ruling["ruled_at"] if ruling else None
        r["ruled"] = ruling is not None
    return rows


def from_ladder_chips(con, node_id: str, state: str):
    return _rows(con, """
        SELECT standard_code, relation, annotation
        FROM node_standards_parsed
        WHERE node_id = ? AND state = ?
        ORDER BY standard_code
    """, (node_id, state))


def other_states_summary(con, node_id: str, version: int):
    rows = suggested_pool(con, version, node_id=node_id,
                           states=_non_priority_states(con))
    states = {r["state"] for r in rows}
    strong = sum(1 for r in rows if r["strength"] == "strong")
    moderate = sum(1 for r in rows if r["strength"] == "moderate")
    weak = sum(1 for r in rows if r["strength"] == "weak")
    return {"rows": rows, "n_states": len(states), "total": len(rows),
            "strong": strong, "moderate": moderate, "weak": weak,
            "moderate_or_weak": moderate + weak}


_ALL_STATES_CACHE = None


def _non_priority_states(con):
    global _ALL_STATES_CACHE
    if _ALL_STATES_CACHE is None:
        _ALL_STATES_CACHE = [r[0] for r in con.execute(
            "SELECT DISTINCT state FROM candidates").fetchall()]
    return [s for s in _ALL_STATES_CACHE if s not in PRIORITY_STATES]


# --------------------------------------------------------------- rationale

_PATH_LABELS = {
    "path0": "CA code inheritance",
    "pathA": "lesson overlap",
    "pathB": "crosswalk (Learnosity)",
    "pathC": "model similarity",
}


def explain(row: dict) -> str:
    try:
        ev = json.loads(row["evidence_json"])
    except (TypeError, ValueError, KeyError):
        ev = {}
    paths = ev.get("paths", {})
    bits = []
    for key, label in _PATH_LABELS.items():
        p = paths.get(key)
        if p:
            bits.append(f"{label} ({p.get('score', 0):.2f})")
    if not bits:
        bits.append(f"combined score {row.get('combined_score', 0):.2f}")
    offset = row.get("grade_offset")
    if offset:
        bits.append(f"grade offset {offset:+d}")
    if row.get("rerank_relevance"):
        bits.append(f"AI rerank: {row['rerank_relevance']}")
    return "; ".join(bits)


def pane_header_tag(rows_for_state: list) -> tuple:
    """(label, css-class) for a state pane header, from the best strength
    present among its suggested candidates."""
    strengths = {r["strength"] for r in rows_for_state}
    if "strong" in strengths:
        return "code match", "det"
    if "moderate" in strengths:
        return "lesson overlap / crosswalk", "det"
    if "weak" in strengths:
        return "model only", "mod"
    return "no suggestions", "mod"


# ------------------------------------------------------------------ write

def write_ruling(con, node_id: str, standard_id: str, ruling: str, reviewer: str):
    """Record a ruling on a suggested candidate. `ruling` is 'accepted' or
    'rejected'. Writes to mh2_seq.db only — `con` is the mh2.db connection,
    read here solely to translate node_id into the `source_key` the ruling
    is keyed on.

    'accepted' additionally raises a `tag_proposal`: the ruling records the
    judgment, the proposal is the instruction to add the tag to the Word
    ladder, which is the only place a tag actually exists (§6). A second
    accept does not raise a duplicate — the open proposal already says it.

    Both branches store what the reviewer was looking at (node id, node
    text, ladder file). A rejection needs it most: it raises no proposal to
    carry an anchor of its own, and it is the ruling that goes on quietly
    suppressing a suggestion.
    """
    assert ruling in ("accepted", "rejected")
    info = node_lookup.build_node_lookup(con).get(node_id)
    if info is None:
        raise ValueError(f"no such node_id in mh2.db: {node_id!r}")

    seq = get_seq_connection()
    review_store.set_candidate_ruling(
        seq, info.source_key, standard_id, ruling, reviewer,
        node_id_seen=node_id, node_text_seen=info.node_text,
        ladder_file_seen=info.source_file)
    if ruling == "accepted" and not review_store.open_proposal_exists(
            seq, standard_id, info.source_key):
        review_store.create_tag_proposal(
            seq, standard_id=standard_id, source_key=info.source_key,
            node_text_seen=info.node_text, proposed_by=reviewer,
            node_id_seen=node_id, ladder_file=info.source_file)


def known_reviewers(con):
    """Reviewer names seen in `candidate_ruling`. `con` (mh2.db) is unused —
    kept so app/review.py's call site does not change."""
    return review_store.candidate_ruling_reviewers(get_seq_connection())


# ------------------------------------------------------------- gap pool
#
# The 1,493-row / 37-state "Other State Standards Gaps" sheet, loaded into
# `leaves` (origin='gaps_sheet') by mh2/load_standards.py. These rows have no
# node_id — they're hand-curated state standards not yet routed to a stem,
# not per-node candidates — so this is browse-only, no ruling write path.

def _normalize_gap_category(cat: str) -> str:
    """Same fold as mh2/load_standards.py's load_gaps_sheet: category text is
    case-inconsistent in the source, so only the part before the colon keeps
    its case. Must match exactly or categories silently split into two."""
    cat = (cat or "").strip()
    if ":" in cat:
        head, tail = cat.split(":", 1)
        return f"{head.strip()}: {tail.strip().lower()}"
    return cat


def _norm_stem_name(name: str) -> str:
    return " ".join((name or "").split()).casefold()


@st.cache_data
def gap_category_targets(version: int):
    """category -> hand-maintained routing targets, read live from
    `config.CATEGORY_TO_STEMS_CSV` (John's first-pass draft, expected to be
    revised without a code change). Each target cell is "band | domain | stem
    name"; a category may route to several targets."""
    targets = {}
    with open(config.CATEGORY_TO_STEMS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cat = _normalize_gap_category(row.get("category", ""))
            cells = []
            for key, val in row.items():
                if key == "category" or not val or not val.strip():
                    continue
                parts = [p.strip() for p in val.split("|")]
                if len(parts) != 3:
                    continue
                band_raw, domain, stem_name = parts
                band = "PK5" if band_raw.upper().startswith("PK") else "6_9"
                cells.append({"band": band, "domain": domain, "stem_name": stem_name})
            targets[cat] = cells
    return targets


def _stem_lookup(con):
    """(band, domain, stem name) -> drafted stem, for resolving gap-pool
    routing targets against actual stems. Most targets won't resolve — they
    name stems nobody has drafted a ladder for yet — and that's expected."""
    rows = _rows(con, """
        SELECT s.stem_id, s.name, COALESCE(s.domain, sm.stem_group) AS domain,
               COALESCE(sm.band, 'PK5') AS band
        FROM stems s LEFT JOIN stem_map sm ON sm.stem_id = s.stem_id
    """)
    node_counts = dict(con.execute(
        "SELECT stem_id, COUNT(*) FROM nodes GROUP BY stem_id").fetchall())
    return {
        (r["band"], _norm_stem_name(r["domain"]), _norm_stem_name(r["name"])):
            {"stem_id": r["stem_id"], "n_nodes": node_counts.get(r["stem_id"], 0)}
        for r in rows
    }


@st.cache_data
def gap_pool_categories(_con, version: int):
    """One row per gap-pool category: counts by status, distinct states, and
    resolved routing targets — the sidebar's unit of navigation."""
    con = _con
    counts = _rows(con, """
        SELECT category, status, COUNT(*) AS n
        FROM leaves WHERE origin = 'gaps_sheet'
        GROUP BY category, status
    """)
    state_counts = {r["category"]: r["n_states"] for r in _rows(con, """
        SELECT category, COUNT(DISTINCT jurisdiction) AS n_states
        FROM leaves WHERE origin = 'gaps_sheet' GROUP BY category
    """)}

    by_cat = {}
    for r in counts:
        c = by_cat.setdefault(r["category"], {
            "category": r["category"], "proposed": 0, "covered": 0})
        c[r["status"] or "proposed"] = r["n"]

    targets_by_cat = gap_category_targets(version)
    stems = _stem_lookup(con)
    out = []
    for cat, c in by_cat.items():
        targets = []
        for t in targets_by_cat.get(cat, []):
            match = stems.get((t["band"], _norm_stem_name(t["domain"]),
                                _norm_stem_name(t["stem_name"])))
            targets.append({
                **t,
                "stem_id": match["stem_id"] if match else None,
                "n_nodes": match["n_nodes"] if match else 0,
            })
        out.append({
            "category": cat,
            "total": c["proposed"] + c["covered"],
            "proposed": c["proposed"],
            "covered": c["covered"],
            "n_states": state_counts.get(cat, 0),
            "targets": targets,
        })
    out.sort(key=lambda c: c["category"])
    return out


@st.cache_data
def gap_pool_states(_con, version: int):
    return [r[0] for r in _con.execute(
        "SELECT DISTINCT jurisdiction FROM leaves WHERE origin = 'gaps_sheet'"
        " ORDER BY jurisdiction").fetchall()]


@st.cache_data
def gap_categories_for_stem(_con, stem_id: str, version: int):
    """Reverse of the routing in `gap_pool_categories`: which gap-pool
    categories name THIS stem as a target. Used by the Review page's Gap
    pool preview panel — a category can route to several stems and a stem
    can be named by several categories, so this returns a list, not one."""
    con = _con
    if not stem_id:
        return []
    return [c for c in gap_pool_categories(con, version)
            if any(t["stem_id"] == stem_id for t in c["targets"])]


@st.cache_data
def gap_pool_unhomed_total(_con, version: int):
    """Pool-wide count of gap-pool rows still 'proposed' — i.e. not yet
    matched to a stem's coverage. The number the preview panel reports as
    'remain unhomed pool-wide', so a reviewer sees the backlog isn't
    specific to whatever stem they're currently looking at."""
    con = _con
    return con.execute(
        "SELECT COUNT(*) FROM leaves WHERE origin = 'gaps_sheet'"
        " AND status = 'proposed'").fetchone()[0]


@st.cache_data
def gap_pool_rows(_con, category: str, version: int,
                   states: tuple = (), status: str = None):
    con = _con
    where = ["origin = 'gaps_sheet'", "category = ?"]
    params = [category]
    if states:
        where.append(f"jurisdiction IN ({','.join('?' * len(states))})")
        params += list(states)
    if status:
        where.append("status = ?")
        params.append(status)
    return _rows(con, f"""
        SELECT l.jurisdiction AS state, l.standard_id, l.status,
               l.description AS notes, std.text AS standard_text, std.grade
        FROM leaves l
        LEFT JOIN standards std ON std.standard_id = l.standard_id
        WHERE {' AND '.join(where)}
        ORDER BY l.jurisdiction, l.standard_id
    """, params)
