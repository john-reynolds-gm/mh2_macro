"""
seq_read.py -- every read over mh2.db for the Grade Sequencing Tool (R side of
docs/seq_v1_contract.md, sections 3.2, 3.3, 5).

What lives here
    * the grade slice (chips grouped domain > stem > concept/skill)
    * the node drawer and the compare sheet
    * facts about nodes that the write side needs (NodeFacts)
    * pure helpers: ordering badges, compare assembly
    * successor suggestions and period hints

Rules
    * Every function takes an open READ-ONLY connection to mh2.db
      (see connect_ro). Nothing here writes, and nothing here opens any other
      database; the "where else is this node placed" overlay arrives as a plain
      dict argument.
    * Every return value is plain dict / list / str / int / float / bool /
      None, so json.dumps works on it directly.
    * Keys are always present; an absent value is None or [], never missing.

The query logic for the slice is adapted from scripts/render_seq_prototype.py
(load_stems, load_nodes, load_node_grades, load_fields, load_standards,
compute_shared_ccss, FIELD_ORDER). That script is do-not-touch, so the logic is
copied and reshaped here instead of imported.
"""
from __future__ import annotations

import difflib
import hashlib
import math
import re
import sqlite3
import sys
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mh2 import grade_kind  # noqa: E402
from mh2.grade_kind import BRIDGE_STATES, GRADES, OWED_STATES  # noqa: E402
from mh2.node_lookup import build_stem_names  # noqa: E402
from scripts.report_period_estimates import parse_period_notes  # noqa: E402

# ============================================================================
# Constants
# ============================================================================

# (key, label) -- same order and labels as scripts/render_seq_prototype.py.
FIELD_ORDER = [
    ("specifications", "Specifications"),
    ("required_skills_cases", "Required skills / cases"),
    ("standards_notes", "Standards notes"),
    ("considerations", "Considerations"),
    ("misconceptions", "Misconceptions"),
    ("mathematical_models", "Mathematical models"),
    ("strategies", "Strategies"),
    ("terminology", "Terminology"),
    ("leaves_to_include", "Leaves to include"),
    ("intervention_notes", "Intervention notes"),
    ("product_reference", "Existing product reference"),
    ("additional_notes", "Additional notes"),
]

# Trailing author marker on a concept/skill heading, e.g. "(Jane - done)".
MARKER_RE = re.compile(r"\s*\(\s*[A-Z][a-z]+\s*[-–—]?\s*done\s*\)\s*$", re.I)

COMPARE_MAX = 4

GRADE_LABELS = {
    "PK": "Pre-K", "K": "Kindergarten",
    "1": "Grade 1", "2": "Grade 2", "3": "Grade 3", "4": "Grade 4",
    "5": "Grade 5", "6": "Grade 6", "7": "Grade 7", "8": "Grade 8",
    "A1": "Algebra 1",
}
GRADE_SHORT = {
    "PK": "PK", "K": "K",
    "1": "G1", "2": "G2", "3": "G3", "4": "G4",
    "5": "G5", "6": "G6", "7": "G7", "8": "G8",
    "A1": "A1",
}

NO_DOMAIN = "(no domain)"
UNTITLED_CS = "(no concept/skill heading)"

# Product references from other products: same filter as
# scripts/report_cross_stem_links.OTHER_PRODUCT (copied, not imported).
OTHER_PRODUCT = re.compile(r"TX\b|Bluebonnet|Maryland|Catalyst", re.I)

# Rows of the compare sheet that describe the node itself (contract 5.5).
NODE_ROWS = [
    ("stem", "Stem"), ("concept_skill", "Concept/skill"), ("goal", "Goal"),
    ("grades", "Grades"), ("state", "Kind in this grade"),
    ("grade_raw", "Grade (as written)"), ("period_hint", "Period hint"),
]

# A predecessor reference is shown as "M2 · slot 1".
_DOT = "·"


# ============================================================================
# Small helpers
# ============================================================================

def connect_ro(path) -> sqlite3.Connection:
    """Open mh2.db read-only, so nothing here can write to it."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def collapse_ws(s) -> str:
    """Collapse runs of whitespace (including newlines) to one space."""
    return re.sub(r"\s+", " ", s or "").strip()


def round_half_up(x: float, dp: int) -> float:
    """Round x (>= 0) half-up to dp decimals (Python's round() is banker's)."""
    return math.floor(x * 10 ** dp + 0.5) / 10 ** dp


def normalize_grade(con, token: str) -> str:
    """'2', 'g2', 'G2', 'Grade 2', 'k', 'pk', 'a1' -> a GRADES member.

    Raises ValueError(f"unknown grade {token!r}") for anything else. `con` is
    accepted for signature stability; the valid set is the GRADES constant
    (the grade_sequence CHECK list), which excludes 'OUT'."""
    if not isinstance(token, str):
        raise ValueError(f"unknown grade {token!r}")
    t = token.strip()
    t = re.sub(r"^(grade|gr|g)\s*", "", t, flags=re.I)
    t = {"PRE-K": "PK", "PREK": "PK", "KINDERGARTEN": "K",
         "ALGEBRA 1": "A1"}.get(t.upper(), t.upper())
    if t in GRADES:
        return t
    raise ValueError(f"unknown grade {token!r}")


def display_label(label: str) -> str:
    """Concept/skill heading for display: author marker removed, whitespace
    collapsed. The RAW label is what grouping and cs_id use."""
    text = MARKER_RE.sub("", label or "")
    return collapse_ws(text)


def cs_id(stem_id: str, concept_skill: str) -> str:
    """Stable id for a concept/skill: '<stem_id>:<8 hex of sha1(raw label)>'."""
    digest = hashlib.sha1((concept_skill or "").encode("utf-8")).hexdigest()
    return f"{stem_id}:{digest[:8]}"


def ladders_last_read(con) -> str | None:
    """Timestamp of the most recent ladder ingest (the page's 'stamp')."""
    row = con.execute("SELECT MAX(ts) FROM ingest_log").fetchone()
    return row[0] if row else None


def _short(grade: str) -> str:
    return GRADE_SHORT.get(grade, grade)


# ============================================================================
# Loading the tables once per call
# ============================================================================

class _Core:
    """Everything the slice, drawer and facts need, loaded once.

    nodes      node_id -> dict(node_id, source_key, stem_id, seq, node_text,
                               concept_skill (raw, '' if NULL), goal,
                               source_file)
    by_key     source_key -> same dict
    stems      stem_id -> {name, domain}
    grades_of  node_id -> node_grade grades, in grade_order
    rulings    node_id -> ruling dict (grade_kind.ruling_by_node)
    kinds      (node_id, grade) -> stored kind or 'unknown'
    cs_nodes   (stem_id, raw concept_skill) -> [node dicts] in seq order
    """

    def __init__(self, con: sqlite3.Connection):
        names = build_stem_names(con)
        self.stems = {}
        for r in con.execute("SELECT stem_id, name, domain FROM stems"):
            domain = collapse_ws(r["domain"]) or NO_DOMAIN
            self.stems[r["stem_id"]] = {
                "name": names.get(r["stem_id"]) or r["stem_id"],
                "domain": domain,
            }
        self.nodes = {}
        self.by_key = {}
        for r in con.execute(
                "SELECT node_id, source_key, stem_id, CAST(seq AS INTEGER) AS seq, "
                "node_text, concept_skill, goal, source_file FROM nodes "
                "ORDER BY stem_id, CAST(seq AS INTEGER), node_id"):
            node = {
                "node_id": r["node_id"], "source_key": r["source_key"],
                "stem_id": r["stem_id"], "seq": r["seq"],
                "node_text": collapse_ws(r["node_text"]),
                "concept_skill": r["concept_skill"] or "",
                "goal": r["goal"], "source_file": r["source_file"],
            }
            self.nodes[node["node_id"]] = node
            self.by_key[node["source_key"]] = node
        self.grades_of = {}
        for r in con.execute(
                "SELECT g.node_id, g.grade FROM node_grade g "
                "JOIN grade_order o ON o.grade = g.grade "
                "ORDER BY g.node_id, o.ord"):
            self.grades_of.setdefault(r["node_id"], []).append(r["grade"])
        self.rulings = grade_kind.ruling_by_node(con)
        self.kinds = grade_kind.build_grade_kinds(con)
        self.cs_nodes = {}
        for node in self.nodes.values():
            self.cs_nodes.setdefault(
                (node["stem_id"], node["concept_skill"]), []).append(node)
        for members in self.cs_nodes.values():
            members.sort(key=lambda n: (n["seq"], n["node_id"]))

    def stem_name(self, stem_id: str) -> str:
        return self.stems.get(stem_id, {}).get("name") or stem_id

    def domain(self, stem_id: str) -> str:
        return self.stems.get(stem_id, {}).get("domain") or NO_DOMAIN

    def state(self, node_id: str, grade: str) -> str:
        """Read state of a node for a grade (grade_kind.kind_for)."""
        return grade_kind.kind_for(node_id, grade, self.kinds, self.rulings)

    def in_grade(self, node_id: str, grade: str) -> bool:
        return grade in self.grades_of.get(node_id, ())

    def ruling(self, node_id: str) -> dict:
        return self.rulings.get(node_id) or {}

    def get(self, source_key: str) -> dict:
        """Node by source_key, or KeyError(source_key)."""
        node = self.by_key.get(source_key)
        if node is None:
            raise KeyError(source_key)
        return node


def _ccss_codes(con) -> dict:
    """node_id -> sorted list of CCSS codes (node_standards_parsed.state IS NULL)."""
    out = {}
    for r in con.execute("SELECT node_id, standard_code FROM node_standards_parsed "
                         "WHERE state IS NULL ORDER BY node_id, standard_code"):
        out.setdefault(r["node_id"], []).append(r["standard_code"])
    return out


def _shared_code_index(core: _Core, ccss: dict) -> dict:
    """node_id -> {partner_stem_id: {"codes": set, "nodes": set(node_id)}}.

    A code is shared when a node in a DIFFERENT stem carries it too, anywhere
    in the DB (adapted from render_seq_prototype.compute_shared_ccss)."""
    code_nodes = {}
    for node_id, codes in ccss.items():
        if node_id not in core.nodes:
            continue
        for code in codes:
            code_nodes.setdefault(code, []).append(node_id)
    out = {}
    for code, node_ids in code_nodes.items():
        for a in node_ids:
            stem_a = core.nodes[a]["stem_id"]
            for b in node_ids:
                stem_b = core.nodes[b]["stem_id"]
                if stem_a == stem_b:
                    continue
                slot = out.setdefault(a, {}).setdefault(
                    stem_b, {"codes": set(), "nodes": set()})
                slot["codes"].add(code)
                slot["nodes"].add(b)
    return out


def _partners(core: _Core, shared_index: dict, node_id: str, grade: str) -> list:
    """Partner objects (contract 3.3) for one node, ordered by stem_id."""
    out = []
    for stem_id, slot in sorted(shared_index.get(node_id, {}).items()):
        members = sorted((core.nodes[n] for n in slot["nodes"]),
                         key=lambda n: (n["seq"], n["node_id"]))
        out.append({
            "stem_id": stem_id,
            "stem_name": core.stem_name(stem_id),
            "codes": sorted(slot["codes"]),
            "nodes": [{"source_key": m["source_key"], "node_id": m["node_id"],
                       "in_grade": core.in_grade(m["node_id"], grade)}
                      for m in members],
        })
    return out


def _lesson_index(con, core: _Core) -> dict:
    """lesson_id -> set(node_id), skipping refs from other products."""
    out = {}
    for r in con.execute("SELECT node_id, raw_ref, lesson_id FROM product_refs "
                         "WHERE lesson_id IS NOT NULL AND lesson_id <> ''"):
        if OTHER_PRODUCT.search(r["raw_ref"] or "") or r["node_id"] not in core.nodes:
            continue
        out.setdefault(r["lesson_id"], set()).add(r["node_id"])
    return out


def _additional_notes(con) -> dict:
    """node_id -> additional_notes paragraphs in ordinal order."""
    out = {}
    for r in con.execute("SELECT node_id, value FROM node_fields "
                         "WHERE field = 'additional_notes' "
                         "ORDER BY node_id, ordinal"):
        out.setdefault(r["node_id"], []).append(r["value"] or "")
    return out


# ============================================================================
# Period hint (contract 3.2.1)
# ============================================================================

def _autofill_value(kept: list):
    """The number a placement is autofilled with (rulings O8, O9), or None.

    Units are ignored: day = lesson = period, 1:1 (O8).  A multi-grade node's
    un-graded estimate counts in every grade it spans (O9).  exact, closed and
    open-ended ranges give their low; "part of N" gives N - 0.5 when N > 0.5;
    everything else, a group total, or more than one kept estimate gives None."""
    if len(kept) != 1:
        return None
    e = kept[0]
    if "group_total" in (e.note or ""):
        return None
    if e.qualifier in ("exact", "range"):
        return None if e.low is None else float(e.low)
    if e.qualifier == "part_of" and e.high is not None and e.high > 0.5:
        return float(e.high) - 0.5
    return None


def _period_hint_from(notes: list, node_grades: list, grade: str):
    """The hint for one node given its notes and node_grade grades, or None."""
    estimates = parse_period_notes(notes) if notes else []
    kept = [e for e in estimates if e.grade == grade]
    basis = "grade_named"
    if not kept:
        ungraded = [e for e in estimates if e.grade is None]
        if len(node_grades) == 1:
            kept, basis = ungraded, "single_grade_node"
        elif len(node_grades) > 1:
            kept, basis = ungraded, "ungraded_multi_grade"
    if not kept:
        return None
    first = kept[0]
    return {
        "text": " · ".join(e.snippet for e in kept),
        "value": _autofill_value(kept),
        "unit": first.unit,
        "qualifier": first.qualifier,
        "low": None if first.low is None else float(first.low),
        "high": None if first.high is None else float(first.high),
        "basis": basis,
        "n_estimates": len(kept),
    }


def period_hint(con, node_id: str, grade: str):
    """PeriodHint for one node in one grade, or None (contract 3.2.1).

    `value` is the autofill number (_autofill_value); day, lesson and period
    are the same unit (O8)."""
    grade = normalize_grade(con, grade)
    notes = [r[0] or "" for r in con.execute(
        "SELECT value FROM node_fields WHERE node_id = ? AND field = 'additional_notes' "
        "ORDER BY ordinal", (node_id,))]
    grades = [r[0] for r in con.execute(
        "SELECT grade FROM node_grade WHERE node_id = ?", (node_id,))]
    return _period_hint_from(notes, grades, grade)


# ============================================================================
# Badges
# ============================================================================

def _badge(code, tier, label, detail=None, refs=None, partners=None) -> dict:
    return {"code": code, "tier": tier, "label": label, "detail": detail,
            "refs": list(refs or []), "partners": list(partners or [])}


def _sorted_refs(overlay_refs: list) -> list:
    """PlacedRefs in grade order, then placement_id."""
    def key(ref):
        g = ref.get("grade")
        return (GRADES.index(g) if g in GRADES else len(GRADES),
                ref.get("placement_id") or 0)
    return sorted(overlay_refs, key=key)


def _chip_badges(core: _Core, node: dict, grade: str, state: str,
                 partners: list, overlay_refs: list) -> list:
    """Slice badges for one chip, in contract order (3.3)."""
    ruling = core.ruling(node["node_id"])
    badges = []
    if state == "span":
        others = [_short(g) for g in core.grades_of.get(node["node_id"], ())
                  if g != grade]
        badges.append(_badge("span", "info", "span",
                             "Also " + ", ".join(others) if others else None))
    elif state == "unconfirmed":
        detail = f"Grade ruling not final: {ruling.get('raw_value')}"
        if ruling.get("states_mentioned"):
            detail += f"; states: {ruling['states_mentioned']}"
        badges.append(_badge("unconfirmed", "info", "unconfirmed", detail))
    elif state == "unknown":
        badges.append(_badge("kind_pending", "info", "kind pending",
                             "No kind recorded for this grade"))
    elif state == "state_extension":
        detail = ruling.get("states_mentioned") or ruling.get("notes") or None
        badges.append(_badge("state_ext", "info", "state ext", detail))
    elif state == "leaf":
        badges.append(_badge("leaf", "info", "leaf", ruling.get("raw_value")))
    elif state == "no_grade":
        badges.append(_badge("no_grade", "info", "no grade",
                             "The ladder cell has no grade"))
    if overlay_refs:
        refs = _sorted_refs(overlay_refs)
        grades_seen = []
        for ref in refs:
            if ref["grade"] not in grades_seen:
                grades_seen.append(ref["grade"])
        label = "also " + ", ".join(_short(g) for g in grades_seen)
        detail = "Placed in " + "; ".join(
            f"{_short(r['grade'])} {_DOT} {r['module_title']}" for r in refs)
        badges.append(_badge("placed_elsewhere", "info", label, detail))
    if partners:
        label = "pairs: " + ", ".join(p["stem_id"] for p in partners)
        detail = "Shares CCSS codes with " + ", ".join(
            f"{p['stem_name']} ({len(p['codes'])})" for p in partners)
        badges.append(_badge("shared_code", "pairing", label, detail,
                             partners=partners))
    return badges


# ============================================================================
# Slice
# ============================================================================

def _included_cs(core: _Core, grade: str) -> list:
    """[(stem_id, raw_cs, members)] for every C/S with >= 1 in-grade node."""
    out = []
    for (stem_id, raw_cs), members in core.cs_nodes.items():
        if any(core.in_grade(m["node_id"], grade) for m in members):
            out.append((stem_id, raw_cs, members))
    return out


def _grade_counts(core: _Core, grade: str) -> dict:
    """The slice counts for a grade (no badges, no hints: cheap)."""
    included = _included_cs(core, grade)
    in_grade = owed = leaf = 0
    context = 0
    cs_excl_leaf = 0
    stems = set()
    for stem_id, _raw, members in included:
        stems.add(stem_id)
        has_non_leaf = False
        for m in members:
            if core.in_grade(m["node_id"], grade):
                in_grade += 1
                state = core.state(m["node_id"], grade)
                if state == "leaf":
                    leaf += 1
                else:
                    has_non_leaf = True
                if state in OWED_STATES:
                    owed += 1
            else:
                context += 1
        if has_non_leaf:
            cs_excl_leaf += 1
    return {
        "in_grade_nodes": in_grade,
        "in_grade_nodes_excl_leaf": in_grade - leaf,
        "owed_nodes": owed,
        "leaf_in_grade": leaf,
        "concept_skills": len(included),
        "concept_skills_excl_leaf": cs_excl_leaf,
        "context_nodes": context,
        "stems": len(stems),
        "chips": in_grade + context,
    }


def list_grades(con) -> list:
    """GradeInfo for every grade in GRADES (contract 5.1). `sequence` is None;
    seq_service fills it in."""
    core = _Core(con)
    order = {r["grade"]: (r["ord"], r["band"]) for r in
             con.execute("SELECT grade, ord, band FROM grade_order")}
    out = []
    for grade in GRADES:
        counts = _grade_counts(core, grade)
        ord_, band = order.get(grade, (len(out), None))
        out.append({
            "grade": grade, "label": GRADE_LABELS[grade], "short": GRADE_SHORT[grade],
            "ord": ord_, "band": band,
            "in_grade_nodes": counts["in_grade_nodes"],
            "owed_nodes": counts["owed_nodes"],
            "chips": counts["chips"],
            "sequence": None,
        })
    return out


def _goal_for(members: list):
    """(goal, goal_missing) for a C/S: the single distinct non-empty goal,
    whitespace preserved. More than one distinct goal is an error."""
    goals = []
    for m in members:
        g = m["goal"]
        if g is not None and g.strip() and g not in goals:
            goals.append(g)
    if len(goals) > 1:
        raise ValueError(
            f"concept/skill {members[0]['stem_id']}:{members[0]['concept_skill']!r} "
            f"has {len(goals)} distinct goals")
    if goals:
        return goals[0], False
    return None, True


def _slice_node(core: _Core, node: dict, grade: str, shared_index: dict,
                overlay: dict, notes: dict) -> dict:
    """One SliceNode (contract 3.3)."""
    node_id = node["node_id"]
    state = core.state(node_id, grade)
    ruling = core.ruling(node_id)
    partners = _partners(core, shared_index, node_id, grade)
    placed = list(overlay.get(node["source_key"], []))
    return {
        "source_key": node["source_key"],
        "node_id": node_id,
        "seq": node["seq"],
        "node_text": node["node_text"],
        "grades": list(core.grades_of.get(node_id, [])),
        "state": state,
        "in_grade": core.in_grade(node_id, grade),
        "owed": state in OWED_STATES,
        "requires_confirm": state in BRIDGE_STATES,
        "resolution": ruling.get("resolution"),
        "grade_raw": ruling.get("raw_value"),
        "states_mentioned": ruling.get("states_mentioned"),
        "badges": _chip_badges(core, node, grade, state, partners, placed),
        "placed_elsewhere": _sorted_refs(placed),
        "period_hint": _period_hint_from(
            notes.get(node_id, []), core.grades_of.get(node_id, []), grade),
    }


def build_slice(con, grade: str, overlay: dict | None = None) -> dict:
    """The whole grade slice (contract 5.2): every C/S with an in-grade node,
    every node of each such C/S (in-grade or context), grouped
    domain > stem > concept/skill, with counts.

    `overlay` = {source_key: [PlacedRef]} from the write side; it only adds
    `placed_elsewhere` and a badge."""
    grade = normalize_grade(con, grade)
    overlay = overlay or {}
    core = _Core(con)
    shared_index = _shared_code_index(core, _ccss_codes(con))
    notes = _additional_notes(con)

    by_stem = {}
    for stem_id, raw_cs, members in _included_cs(core, grade):
        goal, goal_missing = _goal_for(members)
        in_grade_count = sum(1 for m in members if core.in_grade(m["node_id"], grade))
        leaf_in = sum(1 for m in members if core.in_grade(m["node_id"], grade)
                      and core.state(m["node_id"], grade) == "leaf")
        by_stem.setdefault(stem_id, []).append({
            "cs_id": cs_id(stem_id, raw_cs),
            "label": raw_cs,
            "label_display": display_label(raw_cs) or UNTITLED_CS,
            "goal": goal,
            "goal_missing": goal_missing,
            "in_grade_count": in_grade_count,
            "in_grade_count_excl_leaf": in_grade_count - leaf_in,
            "nodes": [_slice_node(core, m, grade, shared_index, overlay, notes)
                      for m in members],
            "_min_seq": members[0]["seq"],
        })

    by_domain = {}
    for stem_id, cs_list in by_stem.items():
        cs_list.sort(key=lambda c: c["_min_seq"])
        for c in cs_list:
            del c["_min_seq"]
        by_domain.setdefault(core.domain(stem_id), []).append({
            "stem_id": stem_id,
            "stem_name": core.stem_name(stem_id),
            "concept_skills": cs_list,
        })

    def domain_key(item):
        return (item[0] == NO_DOMAIN, item[0].casefold())

    super_stems = []
    for domain, stems in sorted(by_domain.items(), key=domain_key):
        stems.sort(key=lambda s: s["stem_name"].casefold())
        super_stems.append({"domain": domain, "stems": stems})

    return {
        "grade": grade,
        "grade_label": GRADE_LABELS[grade],
        "kind_source": grade_kind.kind_source(con),
        "ladders_last_read": ladders_last_read(con),
        "counts": _grade_counts(core, grade),
        "super_stems": super_stems,
    }


# ============================================================================
# Fields (drawer and compare)
# ============================================================================

def compare_field_defs(con) -> list:
    """FIELD_ORDER first, then any other node_fields.field present, sorted,
    labelled key.replace('_', ' ').capitalize()."""
    known = {k for k, _ in FIELD_ORDER}
    defs = [{"key": k, "label": label} for k, label in FIELD_ORDER]
    extra = sorted(r[0] for r in con.execute("SELECT DISTINCT field FROM node_fields")
                   if r[0] not in known)
    defs += [{"key": k, "label": k.replace("_", " ").capitalize()} for k in extra]
    return defs


def _node_fields(con, node_id: str) -> dict:
    """{field: [non-blank values in ordinal order]} for one node."""
    out = {}
    for r in con.execute("SELECT field, value FROM node_fields WHERE node_id = ? "
                         "ORDER BY field, ordinal", (node_id,)):
        value = (r["value"] or "").strip()
        if value:
            out.setdefault(r["field"], []).append(value)
    return out


def _node_standards(con, node_id: str) -> list:
    """node_standards_parsed rows for one node."""
    return [dict(code=r["standard_code"], state=r["state"], relation=r["relation"],
                 annotation=r["annotation"])
            for r in con.execute(
                "SELECT standard_code, state, relation, annotation "
                "FROM node_standards_parsed WHERE node_id = ? "
                "ORDER BY state IS NOT NULL, state, standard_code", (node_id,))]


def _node_lessons(con, node_id: str) -> list:
    """Distinct lesson_ids on a node (other products excluded), first row
    wins: [{lesson_id, product, raw_ref}]."""
    out, seen = [], set()
    for r in con.execute("SELECT product, raw_ref, lesson_id FROM product_refs "
                         "WHERE node_id = ? ORDER BY rowid", (node_id,)):
        lid = r["lesson_id"]
        if not lid or lid in seen or OTHER_PRODUCT.search(r["raw_ref"] or ""):
            continue
        seen.add(lid)
        out.append({"lesson_id": lid, "product": r["product"],
                    "raw_ref": collapse_ws(r["raw_ref"])})
    return out


# ============================================================================
# Drawer
# ============================================================================

def node_drawer(con, source_key: str, grade: str, overlay: dict | None = None) -> dict:
    """Everything the drawer shows for one node (contract 5.3)."""
    grade = normalize_grade(con, grade)
    overlay = overlay or {}
    core = _Core(con)
    node = core.get(source_key)
    node_id, stem_id = node["node_id"], node["stem_id"]
    state = core.state(node_id, grade)
    ruling = core.ruling(node_id)
    shared_index = _shared_code_index(core, _ccss_codes(con))
    partners = _partners(core, shared_index, node_id, grade)
    shared_codes = {c for p in partners for c in p["codes"]}
    members = core.cs_nodes[(stem_id, node["concept_skill"])]
    goal, goal_missing = _goal_for(members)

    field_defs = compare_field_defs(con)
    values = _node_fields(con, node_id)
    standards = _node_standards(con, node_id)

    lesson_index = _lesson_index(con, core)
    lessons = []
    for item in _node_lessons(con, node_id):
        shared_with = {}
        for other_id in sorted(lesson_index.get(item["lesson_id"], ())):
            other = core.nodes[other_id]
            if other["stem_id"] == stem_id:
                continue
            shared_with.setdefault(other["stem_id"], []).append(other)
        lessons.append({
            "lesson_id": item["lesson_id"], "product": item["product"],
            "raw_ref": item["raw_ref"],
            "shared_with": [
                {"stem_id": sid, "stem_name": core.stem_name(sid),
                 "nodes": [{"source_key": o["source_key"], "node_id": o["node_id"],
                            "in_grade": core.in_grade(o["node_id"], grade)}
                           for o in sorted(nodes, key=lambda o: (o["seq"], o["node_id"]))]}
                for sid, nodes in sorted(shared_with.items())],
        })

    stem_by_name = {s["name"].casefold(): sid for sid, s in core.stems.items()}
    links = []
    for r in con.execute("SELECT related_text FROM node_links WHERE node_id = ? "
                         "AND link_type = 'stated' ORDER BY rowid", (node_id,)):
        text = collapse_ws(r["related_text"])
        if text:
            links.append({"text": text, "stem_id": stem_by_name.get(text.casefold()),
                          "label": "as captured"})

    placed = list(overlay.get(source_key, []))
    return {
        "grade": grade,
        "source_key": source_key,
        "node_id": node_id,
        "node_text": node["node_text"],
        "seq": node["seq"],
        "stem_id": stem_id,
        "stem_name": core.stem_name(stem_id),
        "domain": core.domain(stem_id),
        "source_file": node["source_file"],
        "state": state,
        "in_grade": core.in_grade(node_id, grade),
        "owed": state in OWED_STATES,
        "requires_confirm": state in BRIDGE_STATES,
        "cs": {
            "cs_id": cs_id(stem_id, node["concept_skill"]),
            "label": node["concept_skill"],
            "label_display": display_label(node["concept_skill"]) or UNTITLED_CS,
            "goal": goal,
            "goal_missing": goal_missing,
        },
        "strip": [{"source_key": m["source_key"], "node_id": m["node_id"],
                   "seq": m["seq"], "state": core.state(m["node_id"], grade),
                   "in_grade": core.in_grade(m["node_id"], grade),
                   "is_self": m["node_id"] == node_id} for m in members],
        "ruling": {
            "raw_value": ruling.get("raw_value"),
            "resolution": ruling.get("resolution"),
            "ruling_type": ruling.get("ruling_type"),
            "states_mentioned": ruling.get("states_mentioned"),
            "notes": ruling.get("notes"),
            "needs_writer_review": ruling.get("needs_writer_review"),
        },
        "grade_states": [
            {"grade": g, "short": GRADE_SHORT[g], "state": core.state(node_id, g)}
            for g in GRADES if core.state(node_id, g) != "off_grade"],
        "fields": [{"key": d["key"], "label": d["label"], "values": values[d["key"]]}
                   for d in field_defs if d["key"] in values],
        "fields_empty": [d for d in field_defs if d["key"] not in values],
        "standards": [{"code": s["code"], "relation": s["relation"],
                       "annotation": s["annotation"], "shared": s["code"] in shared_codes}
                      for s in standards if s["state"] is None],
        "state_codes": [{"code": s["code"], "state": s["state"],
                         "relation": s["relation"], "annotation": s["annotation"]}
                        for s in standards if s["state"] is not None],
        "pairings": partners,
        "lessons": lessons,
        "product_refs": [
            {"product": r["product"], "raw_ref": collapse_ws(r["raw_ref"]),
             "lesson_id": r["lesson_id"]}
            for r in con.execute("SELECT product, raw_ref, lesson_id FROM product_refs "
                                 "WHERE node_id = ? ORDER BY rowid", (node_id,))],
        "links": links,
        "period_hint": period_hint(con, node_id, grade),
        "placed_elsewhere": _sorted_refs(placed),
        "badges": _chip_badges(core, node, grade, state, partners, placed),
    }


# ============================================================================
# Compare
# ============================================================================

def compare_column(con, source_key: str, grade: str) -> dict:
    """One CompareColumn (contract 5.4)."""
    grade = normalize_grade(con, grade)
    core = _Core(con)
    node = core.get(source_key)
    node_id = node["node_id"]
    ruling = core.ruling(node_id)
    goal, _missing = _goal_for(core.cs_nodes[(node["stem_id"], node["concept_skill"])])
    standards = _node_standards(con, node_id)
    hint = period_hint(con, node_id, grade)
    lesson_ids = sorted({x["lesson_id"] for x in _node_lessons(con, node_id)})
    return {
        "source_key": source_key,
        "node_id": node_id,
        "node_text": node["node_text"],
        "stem_id": node["stem_id"],
        "stem_name": core.stem_name(node["stem_id"]),
        "cs_label_display": display_label(node["concept_skill"]) or UNTITLED_CS,
        "goal": goal,
        "grades": list(core.grades_of.get(node_id, [])),
        "state": core.state(node_id, grade),
        "in_grade": core.in_grade(node_id, grade),
        "grade_raw": ruling.get("raw_value"),
        "period_hint_text": hint["text"] if hint else None,
        "ccss": sorted({s["code"] for s in standards if s["state"] is None}),
        "lessons": lesson_ids,
        "state_codes": sorted({s["code"] for s in standards if s["state"] is not None}),
        "fields": _node_fields(con, node_id),
    }


def _node_row_cell(key: str, c: dict) -> list:
    """Cell (always a list of strings) for one NODE_ROWS key."""
    if key == "stem":
        return [c["stem_name"]]
    if key == "concept_skill":
        return [c["cs_label_display"]]
    if key == "goal":
        return [c["goal"]] if c["goal"] else []
    if key == "grades":
        return list(c["grades"])
    if key == "state":
        return [c["state"]]
    if key == "grade_raw":
        return [c["grade_raw"]] if c["grade_raw"] else []
    if key == "period_hint":
        return [c["period_hint_text"]] if c["period_hint_text"] else []
    raise KeyError(key)


def _is_empty_cell(cell) -> bool:
    return cell == [] or cell is False


def _flag_rows(columns: list, column_key: str, group: str, prefix: str) -> list:
    """CCSS or lesson rows: one row per distinct id, cells are booleans."""
    ids = sorted(set().union(*[set(c[column_key]) for c in columns])) if columns else []
    rows = []
    for ident in ids:
        cells = [ident in c[column_key] for c in columns]
        rows.append({"key": prefix + ident, "label": ident, "group": group,
                     "kind": "flag", "cells": cells, "shared": sum(cells) >= 2})
    return rows


def assemble_compare(columns: list, field_defs: list, grade: str) -> dict:
    """PURE. Build the compare sheet from columns (contract 5.5). The same
    algorithm is mirrored in the page's JavaScript."""
    rows = []
    for key, label in NODE_ROWS:
        rows.append({"key": key, "label": label, "group": "node", "kind": "text",
                     "cells": [_node_row_cell(key, c) for c in columns],
                     "shared": False})
    rows += _flag_rows(columns, "ccss", "ccss", "ccss:")
    rows += _flag_rows(columns, "lessons", "lesson", "lesson:")
    for d in field_defs:
        rows.append({"key": d["key"], "label": d["label"], "group": "field",
                     "kind": "text",
                     "cells": [list(c["fields"].get(d["key"], [])) for c in columns],
                     "shared": False})
    rows.append({"key": "state_codes", "label": "State codes", "group": "state",
                 "kind": "text", "cells": [list(c["state_codes"]) for c in columns],
                 "shared": False})
    for row in rows:
        row["empty"] = all(_is_empty_cell(cell) for cell in row["cells"])
    return {
        "grade": grade,
        "columns": list(columns),
        "field_defs": list(field_defs),
        "rows": rows,
        "shared_ccss": [r["label"] for r in rows if r["group"] == "ccss" and r["shared"]],
        "shared_lessons": [r["label"] for r in rows
                           if r["group"] == "lesson" and r["shared"]],
    }


def build_compare(con, keys: list, grade: str) -> dict:
    """Compare 1-4 nodes. ValueError for 0 keys, more than COMPARE_MAX, or
    duplicates; KeyError(first unknown key) for a key that is not a node."""
    keys = list(keys)
    if not keys:
        raise ValueError("compare needs at least one key")
    if len(keys) > COMPARE_MAX:
        raise ValueError(f"compare takes at most {COMPARE_MAX} keys, got {len(keys)}")
    if len(set(keys)) != len(keys):
        raise ValueError("compare keys must be distinct")
    grade = normalize_grade(con, grade)
    known = {r[0] for r in con.execute("SELECT source_key FROM nodes")}
    for key in keys:
        if key not in known:
            raise KeyError(key)
    columns = [compare_column(con, k, grade) for k in keys]
    return assemble_compare(columns, compare_field_defs(con), grade)


# ============================================================================
# Node facts (for the write side)
# ============================================================================

def node_facts(con, grade: str, source_keys: Iterable | None = None) -> dict:
    """NodeFacts for the given keys (all nodes if None). Unknown keys are
    simply absent from the result; the caller reads absence as 'orphaned'.

    Extra key beyond contract 3.3: `predecessor_node_ids`, parallel to
    `predecessor_keys`, so a badge can name a predecessor that is not itself
    among the requested keys (see docs/seq_v1_R_notes.md)."""
    grade = normalize_grade(con, grade)
    core = _Core(con)
    notes = _additional_notes(con)
    if source_keys is None:
        wanted = list(core.by_key)
    else:
        wanted = [k for k in dict.fromkeys(source_keys) if k in core.by_key]
    out = {}
    for key in wanted:
        node = core.by_key[key]
        node_id = node["node_id"]
        state = core.state(node_id, grade)
        members = core.cs_nodes[(node["stem_id"], node["concept_skill"])]
        preds = [m for m in members if m["seq"] < node["seq"]
                 and core.state(m["node_id"], grade) in OWED_STATES]
        out[key] = {
            "source_key": key,
            "node_id": node_id,
            "node_text": node["node_text"],
            "stem_id": node["stem_id"],
            "stem_name": core.stem_name(node["stem_id"]),
            "concept_skill": node["concept_skill"],
            "concept_skill_display": display_label(node["concept_skill"]) or UNTITLED_CS,
            "cs_id": cs_id(node["stem_id"], node["concept_skill"]),
            "seq": node["seq"],
            "source_file": node["source_file"],
            "state": state,
            "in_grade": core.in_grade(node_id, grade),
            "owed": state in OWED_STATES,
            "requires_confirm": state in BRIDGE_STATES,
            "predecessor_keys": [m["source_key"] for m in preds],
            "predecessor_node_ids": [m["node_id"] for m in preds],
            "period_hint": _period_hint_from(
                notes.get(node_id, []), core.grades_of.get(node_id, []), grade),
        }
    return out


# ============================================================================
# Ordering badges (pure)
# ============================================================================

def _where(pos: tuple) -> str:
    return f"M{pos[0]} {_DOT} slot {pos[1]}"


def ordering_badges(positions: dict, facts: dict) -> dict:
    """PURE. Warn when a placed node sits before an owed ladder predecessor.

    positions = {source_key: (module_position, slot_position)} for the
    sequence's active, non-orphaned placements (1-based). Returns
    {placed source_key: [Badge]} with an entry (possibly []) for every key in
    positions. One badge per code per node; co-placed nodes (equal positions)
    never produce a badge."""
    out = {}
    for key, pos in positions.items():
        fact = facts.get(key)
        early, unplaced = [], []          # (source_key, node_id)
        if fact is not None:
            ids = fact.get("predecessor_node_ids") or []
            for i, q in enumerate(fact["predecessor_keys"]):
                q_id = ids[i] if i < len(ids) else (
                    facts[q]["node_id"] if q in facts else q)
                if q in positions:
                    if tuple(positions[q]) > tuple(pos):
                        early.append((q, q_id))
                else:
                    unplaced.append((q, q_id))
        badges = []
        if early:
            names = ", ".join(n for _k, n in early)
            where = ", ".join(f"{n} ({_where(positions[k])})" for k, n in early)
            plural = "s" if len(early) > 1 else ""
            badges.append(_badge(
                "before_predecessor", "structural", f"before {names}",
                f"Placed before its ladder predecessor{plural} {where}. Warning only.",
                refs=[k for k, _n in early]))
        if unplaced:
            names = ", ".join(n for _k, n in unplaced)
            if len(unplaced) == 1:
                detail = f"Its ladder predecessor {names} is not placed in this sequence"
            else:
                detail = f"Its ladder predecessors {names} are not placed in this sequence"
            badges.append(_badge("predecessor_unplaced", "info", f"{names} unplaced",
                                 detail, refs=[k for k, _n in unplaced]))
        out[key] = badges
    return out


# ============================================================================
# Successor suggestions
# ============================================================================

def _ratio(a: str, b: str) -> float:
    """difflib ratio on casefolded text, rounded half-up to 3 dp."""
    r = difflib.SequenceMatcher(None, a.casefold(), b.casefold()).ratio()
    return round_half_up(r, 3)


def suggest_successors(con, *, source_key_seen: str, node_text_seen: str,
                       stem_id_seen: str | None, concept_skill_seen: str | None,
                       exclude_keys: set) -> list:
    """Up to 3 replacement candidates for an orphaned placement, in rule order
    (data model 6.4). Suggestions are shown, never applied.

    1 same_text_other_stem : same source_key hash suffix (= same normalized text)
    2 same_cs_similar      : same stem, same displayed C/S, ratio >= 0.6
    3 same_stem_similar    : same stem, ratio >= 0.8"""
    core = _Core(con)
    seen_text = collapse_ws(node_text_seen)
    seen_suffix = source_key_seen.partition(":")[2]
    seen_cs = None if concept_skill_seen is None else display_label(concept_skill_seen)
    skip = set(exclude_keys or ()) | {source_key_seen}
    nodes = sorted(core.nodes.values(), key=lambda n: (n["seq"], n["node_id"]))

    def make(node, reason, ratio):
        return {
            "source_key": node["source_key"], "node_id": node["node_id"],
            "node_text": node["node_text"], "stem_id": node["stem_id"],
            "stem_name": core.stem_name(node["stem_id"]),
            "concept_skill_display": display_label(node["concept_skill"]) or UNTITLED_CS,
            "reason": reason, "ratio": ratio,
        }

    found = []
    if seen_suffix:
        for n in nodes:
            if n["source_key"] not in skip and n["source_key"].partition(":")[2] == seen_suffix:
                found.append(make(n, "same_text_other_stem", 1.0))
    if stem_id_seen is not None:
        if seen_cs is not None:
            rule2 = []
            for n in nodes:
                if (n["stem_id"] == stem_id_seen and n["source_key"] not in skip
                        and display_label(n["concept_skill"]) == seen_cs):
                    ratio = _ratio(seen_text, n["node_text"])
                    if ratio >= 0.6:
                        rule2.append((ratio, n))
            rule2.sort(key=lambda t: -t[0])       # stable: ties keep seq order
            found += [make(n, "same_cs_similar", r) for r, n in rule2]
        rule3 = []
        for n in nodes:
            if n["stem_id"] == stem_id_seen and n["source_key"] not in skip:
                ratio = _ratio(seen_text, n["node_text"])
                if ratio >= 0.8:
                    rule3.append((ratio, n))
        rule3.sort(key=lambda t: -t[0])
        found += [make(n, "same_stem_similar", r) for r, n in rule3]

    out, used = [], set()
    for s in found:
        if s["source_key"] in used:
            continue
        used.add(s["source_key"])
        out.append(s)
        if len(out) == 3:
            break
    return out
