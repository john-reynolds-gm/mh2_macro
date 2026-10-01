"""
render_seq_prototype.py -- static, read-only "paper prototype" of the Grade
Sequencing Tool's primary view (docs/seq_design_brief_rev0.md section 5).

One Python script, one HTML file, no server, no network. Python does all the
querying and grouping and embeds the result as JSON; the page's JavaScript only
renders it and keeps UI toggles (localStorage) and the compare selection
(URL hash). Nothing is written back anywhere: this is a prototype for reacting
to, not a working tool.

Usage:
    python scripts/render_seq_prototype.py [--grade 2] [--out path.html] [--db path]

The grade token is any value in grade_order (PK, K, 1..8, A1, ...); case
and a leading "G"/"Grade " are tolerated ("g2", "Grade 2", "a1").
Default output: <REPORTS>/seq_prototype_<slug>.html  (config.REPORTS follows
$MH2_DATA_DIR).

File layout:
    1. data gathering   -- small read-only query functions, each with a docstring
    2. build_payload()  -- groups them into the JSON the page renders
    3. HTML template    -- CSS + JS as plain strings, filled in by render_html()
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

UNTITLED_CS = "(no concept/skill heading)"

# Drawer / compare row order and labels for node_fields.field. Unknown fields
# (a future ladder invents one) are appended after these, title-cased.
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


# ============================================================================
# 1. Data gathering (read-only)
# ============================================================================

def resolve_db(argv_path: str | None) -> Path:
    """argv --db, then $MH2_DB, then config.DB (follows $MH2_DATA_DIR)."""
    if argv_path:
        return Path(argv_path).expanduser()
    if os.environ.get("MH2_DB"):
        return Path(os.environ["MH2_DB"]).expanduser()
    if config.DB.exists():
        return config.DB
    sys.exit("Could not locate mh2.db. Pass --db or set $MH2_DB / $MH2_DATA_DIR.")


def connect_readonly(db: Path) -> sqlite3.Connection:
    """Open the database with mode=ro so this script cannot write to it."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def collapse_ws(s: str | None) -> str:
    """Collapse runs of whitespace (incl. embedded newlines) to one space."""
    return re.sub(r"\s+", " ", s or "").strip()


def normalize_grade_token(con: sqlite3.Connection, token: str) -> str:
    """Map a user-typed grade ('g2', 'Grade 2', 'pk', 'a1') to a grade_order key.
    Exits with the list of valid grades if nothing matches."""
    valid = [r[0] for r in con.execute("SELECT grade FROM grade_order ORDER BY ord")]
    t = token.strip()
    t = re.sub(r"^(grade|gr|g)\s*", "", t, flags=re.I) if t.lower() not in {"geo"} else t
    by_upper = {g.upper(): g for g in valid}
    if t.upper() in by_upper:
        return by_upper[t.upper()]
    sys.exit(f"Unknown grade {token!r}. Valid: {', '.join(valid)}")


def grade_label(g: str) -> str:
    """Short display label for a grade key: 'PK', 'K', 'G2', 'A1'."""
    return g if g in {"PK", "K"} or not g.isdigit() else f"G{g}"


def load_stems(con: sqlite3.Connection) -> dict[str, dict]:
    """stem_id -> {name, domain}. `stems` is the catalog; names are
    whitespace-collapsed (WHO carries an embedded newline). The super-stem
    ("domain") is stems.domain, which is populated for every stem that has
    nodes; stem_map.stem_group is NOT used because it is missing for PK-5
    stems and disagrees with stems.domain for some 6-A1 stems."""
    return {
        r["stem_id"]: {"name": collapse_ws(r["name"]),
                       "domain": collapse_ws(r["domain"]) or "(no domain)"}
        for r in con.execute("SELECT stem_id, name, domain FROM stems")
    }


def load_nodes(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every node, ordered stem -> ladder order (nodes.seq is numeric-ish text
    in some builds, so cast)."""
    return con.execute(
        "SELECT node_id, stem_id, CAST(seq AS INTEGER) AS seq, node_text, "
        "       concept_skill, goal, grade_or_leaf, source_key "
        "FROM nodes ORDER BY stem_id, CAST(seq AS INTEGER), node_id"
    ).fetchall()


def load_node_grades(con: sqlite3.Connection) -> dict[str, list[str]]:
    """node_id -> grades (sorted by grade_order.ord) from node_grade.
    Leaf and no_grade_field nodes mostly have no rows here."""
    out: dict[str, list[str]] = defaultdict(list)
    for r in con.execute(
        "SELECT g.node_id, g.grade FROM node_grade g "
        "JOIN grade_order o ON o.grade = g.grade ORDER BY g.node_id, o.ord"
    ):
        out[r["node_id"]].append(r["grade"])
    return out


def load_rulings(con: sqlite3.Connection) -> dict[str, dict]:
    """node_id -> node_grade_ruling row (raw_value, resolution, is_leaf, notes)."""
    return {r["node_id"]: dict(r) for r in con.execute(
        "SELECT node_id, raw_value, canon_key, resolution, is_leaf, notes "
        "FROM node_grade_ruling")}


def load_fields(con: sqlite3.Connection) -> dict[str, dict[str, list[str]]]:
    """node_id -> {field: [values in ordinal order]}; blank values dropped so
    'empty' means no non-blank value."""
    out: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for r in con.execute(
            "SELECT node_id, field, value FROM node_fields "
            "ORDER BY node_id, field, ordinal"):
        v = (r["value"] or "").strip()
        if v:
            out[r["node_id"]][r["field"]].append(v)
    return out


def load_standards(con: sqlite3.Connection) -> dict[str, list[dict]]:
    """node_id -> [{code, state, relation, annotation}] from
    node_standards_parsed. state is NULL for CCSS codes."""
    out: dict[str, list[dict]] = defaultdict(list)
    for r in con.execute(
            "SELECT node_id, standard_code, state, relation, annotation "
            "FROM node_standards_parsed ORDER BY node_id, state IS NOT NULL, "
            "state, standard_code"):
        out[r["node_id"]].append({
            "code": r["standard_code"], "state": r["state"],
            "relation": r["relation"], "note": r["annotation"]})
    return out


def load_product_refs(con: sqlite3.Connection) -> dict[str, list[dict]]:
    """node_id -> [{product, ref, lesson_id}] from product_refs."""
    out: dict[str, list[dict]] = defaultdict(list)
    for r in con.execute(
            "SELECT node_id, product, raw_ref, lesson_id FROM product_refs "
            "ORDER BY node_id, rowid"):
        out[r["node_id"]].append({"product": r["product"],
                                  "ref": collapse_ws(r["raw_ref"]),
                                  "lesson_id": r["lesson_id"]})
    return out


def load_links(con: sqlite3.Connection) -> dict[str, list[dict]]:
    """node_id -> stated cross-stem links (node_links). related_node_id is
    NULL for every row today, so these are free text only."""
    out: dict[str, list[dict]] = defaultdict(list)
    for r in con.execute(
            "SELECT node_id, related_text, related_node_id, link_type "
            "FROM node_links ORDER BY node_id, rowid"):
        out[r["node_id"]].append({"text": collapse_ws(r["related_text"]),
                                  "related_node_id": r["related_node_id"],
                                  "type": r["link_type"]})
    return out


def compute_shared_ccss(con: sqlite3.Connection) -> dict[str, dict[str, list[str]]]:
    """Shared-CCSS pairings (brief section 6/9): a CCSS code (state IS NULL)
    that appears on nodes in 2+ different stems, across the WHOLE database
    (not just the selected grade, so a pairing with a node in another grade
    still shows). Returns node_id -> {code: [other stem_ids, sorted]}.
    A pairing, not a conflict -- the page badges it neutrally."""
    code_stems: dict[str, set[str]] = defaultdict(set)
    node_codes: dict[str, set[str]] = defaultdict(set)
    for r in con.execute(
            "SELECT p.node_id, p.standard_code, n.stem_id "
            "FROM node_standards_parsed p JOIN nodes n USING(node_id) "
            "WHERE p.state IS NULL"):
        code_stems[r["standard_code"]].add(r["stem_id"])
        node_codes[r["node_id"]].add(r["standard_code"])
    stem_of = {r["node_id"]: r["stem_id"] for r in con.execute(
        "SELECT node_id, stem_id FROM nodes")}
    out: dict[str, dict[str, list[str]]] = {}
    for node_id, codes in node_codes.items():
        mine = stem_of[node_id]
        shared = {c: sorted(code_stems[c] - {mine})
                  for c in sorted(codes) if len(code_stems[c]) >= 2}
        if shared:
            out[node_id] = shared
    return out


def compute_cs_shared_fields(node_ids: list[str],
                             fields: dict[str, dict[str, list[str]]]) -> list[str]:
    """Fields whose non-empty value list is IDENTICAL on every node of a
    concept/skill. Only meaningful with 2+ nodes (with one node everything is
    trivially 'shared'), so returns [] for single-node progressions. Computed,
    not assumed: this is how we find out which attributes the ladders really
    treat as concept/skill-scoped besides Goal."""
    if len(node_ids) < 2:
        return []
    shared = []
    all_fields = {f for n in node_ids for f in fields.get(n, {})}
    for f in sorted(all_fields):
        first = fields.get(node_ids[0], {}).get(f)
        if first and all(fields.get(n, {}).get(f) == first for n in node_ids[1:]):
            shared.append(f)
    return shared


def sql_counts(con: sqlite3.Connection, grade: str) -> dict[str, dict]:
    """The header counts, each computed by ONE direct SQL statement (so the page
    numbers can be checked against sqlite3 by hand). Returns
    name -> {value, sql}. build_payload() re-derives the same numbers from its
    Python grouping and asserts they match."""
    defs = {
        "cs_total": ("SELECT COUNT(*) FROM (SELECT DISTINCT stem_id, "
                     "COALESCE(concept_skill,'') FROM nodes)", ()),
        "cs_shown": ("SELECT COUNT(*) FROM (SELECT DISTINCT n.stem_id, "
                     "COALESCE(n.concept_skill,'') FROM nodes n "
                     "JOIN node_grade g ON g.node_id = n.node_id "
                     "WHERE g.grade = ?)", (grade,)),
        "in_grade": ("SELECT COUNT(DISTINCT node_id) FROM node_grade "
                     "WHERE grade = ?", (grade,)),
        "context": ("SELECT COUNT(*) FROM nodes n WHERE (n.stem_id, "
                    "COALESCE(n.concept_skill,'')) IN (SELECT DISTINCT n2.stem_id, "
                    "COALESCE(n2.concept_skill,'') FROM nodes n2 JOIN node_grade g "
                    "ON g.node_id = n2.node_id WHERE g.grade = ?) AND n.node_id "
                    "NOT IN (SELECT node_id FROM node_grade WHERE grade = ?)",
                    (grade, grade)),
        "stems_shown": ("SELECT COUNT(DISTINCT n.stem_id) FROM nodes n JOIN "
                        "node_grade g ON g.node_id = n.node_id WHERE g.grade = ?",
                        (grade,)),
    }
    out = {}
    for name, (sql, params) in defs.items():
        out[name] = {"value": con.execute(sql, params).fetchone()[0],
                     "sql": sql.replace("?", f"'{grade}'") if params else sql}
    return out


# ============================================================================
# 2. Payload
# ============================================================================

def node_kind(ruling: dict | None, grades: list[str]) -> str:
    """'leaf' | 'unresolved' | 'ruled'. A leaf is judged by the grade ruling
    (resolution='leaf'); 'unresolved' is resolution='no_grade_field' (the
    ladder cell had no grade at all). Everything else is an ordinary ruled
    node. Nodes are never dropped for being either kind."""
    res = (ruling or {}).get("resolution")
    if res == "leaf":
        return "leaf"
    if res == "no_grade_field":
        return "unresolved"
    return "ruled" if grades else "unresolved"


def build_payload(con: sqlite3.Connection, grade: str, db_path: Path) -> dict:
    """Everything the page renders, as plain JSON-able dicts.

    groups: [{domain, stems: [{stem_id, name, cs: [{key, name, goal, goal_varies,
             shared_fields, node_ids, in_grade_n}]}]}]   (only C/S with an in-grade node)
    nodes:  node_id -> everything the chip, drawer and compare table need
    """
    stems = load_stems(con)
    node_rows = load_nodes(con)
    grades = load_node_grades(con)
    rulings = load_rulings(con)
    fields = load_fields(con)
    standards = load_standards(con)
    refs = load_product_refs(con)
    links = load_links(con)
    shared_ccss = compute_shared_ccss(con)
    counts = sql_counts(con, grade)

    # Group nodes into concept/skills keyed (stem_id, heading), ladder order.
    cs_nodes: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for r in node_rows:
        cs_nodes[(r["stem_id"], collapse_ws(r["concept_skill"]) or UNTITLED_CS)].append(r)

    def stem_label(stem_id: str) -> str:
        return stems.get(stem_id, {}).get("name", stem_id)

    nodes_out: dict[str, dict] = {}
    by_stem: dict[str, list[dict]] = defaultdict(list)
    for (stem_id, cs_name), rows in cs_nodes.items():
        ids = [r["node_id"] for r in rows]
        if not any(grade in grades.get(i, []) for i in ids):
            continue  # brief 5.1: only concept/skills with an in-grade node
        goals = [collapse_ws(r["goal"]) for r in rows if collapse_ws(r["goal"])]
        goal = goals[0] if goals else ""
        shared_fields = compute_cs_shared_fields(ids, fields)
        cs_key = f"{stem_id}::{cs_name}"
        for r in rows:
            nid = r["node_id"]
            g = grades.get(nid, [])
            rl = rulings.get(nid)
            nodes_out[nid] = {
                "id": nid, "stem": stem_id, "stem_name": stem_label(stem_id),
                "cs": cs_key, "cs_name": cs_name, "seq": r["seq"],
                "text": collapse_ws(r["node_text"]),
                "grades": g, "in_grade": grade in g,
                "kind": node_kind(rl, g),
                "ruling": ({"raw": rl["raw_value"], "resolution": rl["resolution"],
                            "notes": rl["notes"]} if rl else None),
                "grade_cell": r["grade_or_leaf"],
                "std": standards.get(nid, []),
                "shared": [{"code": c, "stems": [stem_label(s) for s in ss]}
                           for c, ss in shared_ccss.get(nid, {}).items()],
                "fields": dict(fields.get(nid, {})),
                "refs": refs.get(nid, []),
                "links": links.get(nid, []),
            }
        by_stem[stem_id].append({
            "key": cs_key, "name": cs_name, "goal": goal,
            "goal_varies": len(set(goals)) > 1 or len(goals) not in (0, len(rows)),
            "shared_fields": shared_fields, "node_ids": ids,
            "in_grade_n": sum(1 for i in ids if grade in grades.get(i, [])),
        })

    by_domain: dict[str, list[dict]] = defaultdict(list)
    for stem_id, cs_list in by_stem.items():
        info = stems.get(stem_id, {"name": stem_id, "domain": "(no domain)"})
        by_domain[info["domain"]].append({"stem_id": stem_id, "name": info["name"],
                                          "cs": cs_list})
    groups = [{"domain": d, "stems": sorted(s, key=lambda x: x["name"].lower())}
              for d, s in sorted(by_domain.items())]

    # Fields present anywhere, in display order (known first, then unknown).
    present = {f for n in nodes_out.values() for f in n["fields"]}
    labels = dict(FIELD_ORDER)
    field_defs = [{"key": k, "label": l} for k, l in FIELD_ORDER]
    field_defs += [{"key": k, "label": k.replace("_", " ").capitalize()}
                   for k in sorted(present - set(labels))]

    # Python-side recount, cross-checked against the direct SQL counts.
    in_grade_n = sum(1 for n in nodes_out.values() if n["in_grade"])
    py = {"cs_shown": sum(len(s["cs"]) for g_ in groups for s in g_["stems"]),
          "in_grade": in_grade_n, "context": len(nodes_out) - in_grade_n,
          "stems_shown": sum(len(g_["stems"]) for g_ in groups),
          "cs_total": len(cs_nodes)}
    for k, v in py.items():
        assert counts[k]["value"] == v, f"count mismatch {k}: sql={counts[k]['value']} py={v}"

    ctx = [n for n in nodes_out.values() if not n["in_grade"]]
    extra = {
        "context_leaf": sum(1 for n in ctx if n["kind"] == "leaf"),
        "context_unresolved": sum(1 for n in ctx if n["kind"] == "unresolved"),
        "in_grade_multi": sum(1 for n in nodes_out.values()
                              if n["in_grade"] and len(n["grades"]) > 1),
        "in_grade_shared_ccss": sum(1 for n in nodes_out.values()
                                    if n["in_grade"] and n["shared"]),
        "goal_missing_cs": sum(1 for g_ in groups for s in g_["stems"]
                               for c in s["cs"] if not c["goal"]),
        "max_strip": max((len(c["node_ids"]) for g_ in groups for s in g_["stems"]
                          for c in s["cs"]), default=0),
    }
    return {
        "meta": {
            "grade": grade, "grade_label": grade_label(grade),
            "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "db": db_path.name,
            "counts": {k: v["value"] for k, v in counts.items()},
            "predicates": {k: v["sql"] for k, v in counts.items()},
            "extra": extra,
        },
        "groups": groups, "nodes": nodes_out, "field_defs": field_defs,
        "grade_labels": {r["grade"]: grade_label(r["grade"]) for r in
                         con.execute("SELECT grade FROM grade_order")},
    }


# ============================================================================
# 3. HTML
# ============================================================================

def embed_json(data) -> str:
    """json.dumps safe inside a <script> tag ('</' and U+2028/9 escaped)."""
    return (json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def render_html(payload: dict) -> str:
    """Fill the template. Predicates go into an HTML comment as well as the
    page's (i) tooltip, so they survive 'view source'."""
    meta = payload["meta"]
    comment = "\n".join(f"  {k}: {v}" for k, v in meta["predicates"].items())
    # '--' is illegal inside an HTML comment; SQL has none, but be safe.
    comment = comment.replace("--", "- -")
    return (TEMPLATE
            .replace("__TITLE__", f"Grade {meta['grade_label']} sequencing prototype")
            .replace("__CSS__", CSS)
            .replace("__PREDICATES__", comment)
            .replace("__DATA__", embed_json(payload))
            .replace("__JS__", JS))


CSS = r"""
:root {
  color-scheme: light;
  --bg: #f4f5f7; --panel: #ffffff; --border: #e2e4e9; --border-strong: #cbcfd8;
  --text: #1a1d23; --muted: #6b7080; --muted-2: #8a8f9c;
  --accent: #3457d5; --accent-fg: #ffffff; --accent-soft: #eaeefc; --accent-soft-bd: #c3ccf3;
  --green-bg: #e6f4ea; --green-fg: #1e7a37; --green-bd: #b3ddbf;
  --yellow-bg: #fdf1d6; --yellow-fg: #a26a00; --yellow-bd: #efd694;
  --red-bg: #fbe6e4; --red-fg: #ab2f24; --red-bd: #f0bab2;
  --link-bg: #e9f3f3; --link-fg: #25656b; --link-bd: #b7d8da;   /* neutral "pairing" teal */
  --chip-bg: #eef0f3; --radius-sm: 6px; --radius-md: 10px; --radius-lg: 14px;
  --shadow-sm: 0 1px 2px rgba(20,24,33,.05);
  --shadow-md: 0 2px 8px rgba(20,24,33,.06), 0 1px 2px rgba(20,24,33,.04);
  --shadow-lg: 0 8px 24px rgba(20,24,33,.12), 0 2px 6px rgba(20,24,33,.06);
  --drawer-w: 430px; --tray-h: 0px; --sheet-h: 0px;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", "Inter", Helvetica, Arial, sans-serif;
  -webkit-font-smoothing: antialiased; }
button { font: inherit; color: inherit; cursor: pointer; }
.banner { background: #1a1d23; color: #e8eaf0; padding: 6px 24px; font-size: 12px; letter-spacing: .02em; }
.banner b { color: #fff; }
header { padding: 14px 24px 12px; background: var(--panel); border-bottom: 1px solid var(--border); }
header h1 { margin: 0; font-size: 19px; letter-spacing: -.01em; }
header h1 small { font-weight: 500; color: var(--muted); font-size: 13px; margin-left: 8px; }
.counts { display: flex; flex-wrap: wrap; gap: 8px 22px; margin-top: 8px; font-size: 13px; color: var(--muted); }
.counts b { color: var(--text); font-size: 15px; }
.counts .i { cursor: help; color: var(--muted-2); border-bottom: 1px dotted var(--muted-2); }
.toolbar { position: sticky; top: 0; z-index: 20; display: flex; flex-wrap: wrap; align-items: center; gap: 8px 10px;
  padding: 9px 24px; background: rgba(255,255,255,.96); backdrop-filter: blur(4px);
  border-bottom: 1px solid var(--border); font-size: 13px; }
.toolbar .sp { flex: 1; }
.tog { display: inline-flex; align-items: center; gap: 6px; padding: 4px 11px; border: 1px solid var(--border);
  border-radius: 999px; background: var(--panel); user-select: none; font-size: 12.5px; }
.tog:hover { border-color: var(--border-strong); }
.tog.on { background: var(--accent-soft); border-color: var(--accent-soft-bd); color: var(--accent); }
.linkbtn { background: none; border: none; color: var(--accent); font-weight: 600; font-size: 12.5px; padding: 4px 2px; }
.linkbtn:hover { text-decoration: underline; }
details.stempick { position: relative; }
details.stempick summary { list-style: none; }
details.stempick summary::-webkit-details-marker { display: none; }
details.stempick .menu { position: absolute; top: 34px; left: 0; z-index: 30; width: 330px; max-height: 60vh; overflow: auto;
  background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius-md); box-shadow: var(--shadow-lg); padding: 8px 12px; }
.menu label { display: flex; gap: 8px; padding: 3px 0; font-size: 12.5px; align-items: baseline; }
.menu .dom { font-size: 10.5px; font-weight: 650; text-transform: uppercase; letter-spacing: .06em; color: var(--muted-2); margin: 8px 0 2px; }
.menu .n { color: var(--muted-2); margin-left: auto; }
main { padding: 18px 24px 40px; transition: margin-right .15s ease; }
body.drawer-open main { margin-right: var(--drawer-w); }
.legend { display: flex; flex-wrap: wrap; gap: 6px 16px; align-items: center; font-size: 12px; color: var(--muted); margin-bottom: 14px; }
.legend .sw { display: inline-block; width: 26px; height: 14px; border-radius: 4px; vertical-align: -2px; margin-right: 5px; }
.domain { margin-top: 22px; }
.domain > h2 { margin: 0 0 8px; font-size: 11.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; color: var(--muted-2);
  border-bottom: 1px solid var(--border-strong); padding-bottom: 4px; }
.stem { margin-bottom: 14px; background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius-lg); box-shadow: var(--shadow-md); }
.stem > .sh { display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-bottom: 1px solid var(--border); }
.stem.collapsed > .sh { border-bottom: none; }
.stem > .sh h3 { margin: 0; font-size: 15px; }
.stem > .sh .meta { color: var(--muted); font-size: 12px; }
.sh .sp { flex: 1; }
.ibtn { background: none; border: 1px solid transparent; border-radius: var(--radius-sm); padding: 2px 8px; font-size: 12px; color: var(--muted); }
.ibtn:hover { border-color: var(--border); background: #f7f8fb; color: var(--text); }
.cs { padding: 10px 14px 12px; border-bottom: 1px solid var(--border); }
.cs:last-child { border-bottom: none; }
.cs-h { display: flex; align-items: baseline; gap: 8px; margin-bottom: 7px; }
.cs-h h4 { margin: 0; font-size: 13.5px; font-weight: 650; }
.cs-h .meta { font-size: 11.5px; color: var(--muted-2); white-space: nowrap; }
.cs.stub { padding: 6px 14px; color: var(--muted-2); font-size: 12.5px; display: flex; gap: 8px; align-items: center; }
.strip { display: flex; align-items: stretch; gap: 0; overflow-x: auto; padding: 2px 2px 8px; scroll-snap-type: x proximity; }
.arrow { flex: none; align-self: center; width: 16px; text-align: center; color: var(--border-strong); font-size: 13px; }
.gap { flex: none; align-self: center; font-size: 11px; color: var(--muted-2); padding: 4px 8px; border: 1px dashed var(--border-strong); border-radius: 999px; white-space: nowrap; }
.node { position: relative; flex: none; width: 236px; padding: 8px 10px 7px; border: 1px solid var(--border-strong);
  border-radius: var(--radius-md); background: var(--panel); text-align: left; scroll-snap-align: start;
  display: flex; flex-direction: column; gap: 5px; box-shadow: var(--shadow-sm); }
.node:hover { border-color: var(--accent); }
.node .top { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; font-size: 10.5px; color: var(--muted); padding-right: 22px; min-height: 18px; }
.node .txt { font-size: 12.5px; line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 5; -webkit-box-orient: vertical; overflow: hidden; color: #25282f; }
.node .bot { display: flex; flex-wrap: wrap; gap: 4px; margin-top: auto; }
.node.in { border-left: 4px solid var(--accent); }
.node.ctx { background: #f7f8fa; border-style: dashed; border-color: var(--border-strong); opacity: .68; }
.node.ctx:hover { opacity: 1; }
.node.ctx .txt { color: var(--muted); }
.node.leaf { border-style: dotted; border-width: 2px; background: #fbfaf5; }
.node.unres { background: repeating-linear-gradient(135deg, #fff 0 7px, #fdf6e3 7px 14px); border-color: var(--yellow-bd); border-style: dashed; opacity: 1; }
.node.sel { box-shadow: 0 0 0 2px var(--accent-soft-bd); background: var(--accent-soft); }
.node.active { outline: 2px solid var(--accent); outline-offset: 1px; }
.cmpbtn { position: absolute; top: 5px; right: 5px; width: 20px; height: 20px; border-radius: 5px; border: 1px solid var(--border-strong);
  background: var(--panel); font-size: 13px; line-height: 16px; padding: 0; color: var(--muted); }
.cmpbtn:hover { border-color: var(--accent); color: var(--accent); }
.node.sel .cmpbtn { background: var(--accent); color: #fff; border-color: var(--accent); }
.pill { display: inline-block; padding: 0 6px; border-radius: 999px; font-size: 10.5px; line-height: 17px; font-weight: 600;
  background: var(--chip-bg); color: var(--muted); border: 1px solid transparent; white-space: nowrap; }
.pill.g { background: var(--panel); border-color: var(--border-strong); }
.pill.g.here { background: var(--accent); color: #fff; border-color: var(--accent); }
.pill.off { background: var(--yellow-bg); color: var(--yellow-fg); border-color: var(--yellow-bd); }
.pill.link { background: var(--link-bg); color: var(--link-fg); border-color: var(--link-bd); }
.pill.leaf { background: #efe9d3; color: #6b5a1a; }
.pill.unres { background: var(--yellow-bg); color: var(--yellow-fg); border-color: var(--yellow-bd); }
.pill.seq { background: transparent; color: var(--muted-2); padding: 0 2px; font-weight: 500; }
#empty { display: none; padding: 40px; text-align: center; color: var(--muted-2); border: 1px dashed var(--border-strong); border-radius: var(--radius-lg); background: var(--panel); }
/* module builder placeholder */
.mb { margin-top: 28px; padding: 14px; border: 2px dashed var(--border-strong); border-radius: var(--radius-lg); background: rgba(255,255,255,.6); }
.mb h2 { margin: 0 0 2px; font-size: 14px; }
.mb .note { color: var(--muted); font-size: 12.5px; margin-bottom: 10px; }
.mb .cols { display: flex; gap: 10px; overflow-x: auto; }
.mb .col { flex: none; width: 200px; min-height: 92px; border: 1px dashed var(--border-strong); border-radius: var(--radius-md); padding: 8px; color: var(--muted-2); font-size: 12px; background: var(--panel); }
.mb .col b { display: block; color: var(--muted); margin-bottom: 4px; }
.mb .gr { margin-top: 10px; font-size: 12px; color: var(--muted); }
.mb .bar { display: flex; height: 10px; border-radius: 5px; overflow: hidden; margin-top: 4px; max-width: 420px; opacity: .55; }
/* drawer */
#drawer { position: fixed; top: 0; right: 0; bottom: var(--tray-h); width: var(--drawer-w); max-width: 100vw; z-index: 40; background: var(--panel);
  border-left: 1px solid var(--border-strong); box-shadow: var(--shadow-lg); transform: translateX(102%); transition: transform .18s ease; display: flex; flex-direction: column; }
body.drawer-open #drawer { transform: none; }
#drawer .dh { padding: 12px 16px 10px; border-bottom: 1px solid var(--border); display: flex; align-items: flex-start; gap: 10px; }
#drawer .dh .t { flex: 1; min-width: 0; }
#drawer .dh .id { font-weight: 700; font-size: 13px; }
#drawer .dh .sub { color: var(--muted); font-size: 12px; }
#drawer .db { overflow: auto; padding: 0 0 24px; flex: 1; }
.scope { margin: 12px 14px 0; border: 1px solid var(--border); border-radius: var(--radius-md); overflow: hidden; }
.scope > .sl { padding: 6px 12px; font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .07em; }
.scope.cs > .sl { background: #ece9f8; color: #4a3f9b; }
.scope.cs { border-color: #cfc9ee; }
.scope.nd > .sl { background: var(--accent-soft); color: var(--accent); }
.scope > .sb { padding: 10px 12px 12px; }
.scope .sl small { text-transform: none; letter-spacing: 0; font-weight: 500; margin-left: 4px; opacity: .85; }
.fld { margin-top: 10px; }
.fld:first-child { margin-top: 0; }
.fld h5 { margin: 0 0 2px; font-size: 10.5px; font-weight: 650; text-transform: uppercase; letter-spacing: .05em; color: var(--muted-2); }
.fld p, .fld li { margin: 0; font-size: 13px; }
.fld ul { margin: 0; padding-left: 17px; }
.fld.none { color: var(--muted-2); font-style: italic; font-size: 12.5px; }
.codes { display: flex; flex-wrap: wrap; gap: 4px; }
.code { font-size: 11.5px; padding: 1px 7px; border-radius: 5px; background: var(--chip-bg); border: 1px solid var(--border); white-space: nowrap; }
.code.shared { background: var(--link-bg); border-color: var(--link-bd); color: var(--link-fg); font-weight: 600; }
.code.partial, .code.exceeds { border-style: dashed; }
.showempty { margin-top: 12px; }
.kv { display: grid; grid-template-columns: 92px 1fr; gap: 2px 10px; font-size: 12.5px; }
.kv dt { color: var(--muted-2); } .kv dd { margin: 0; }
/* tray + sheet */
#tray { position: fixed; left: 0; right: 0; bottom: 0; z-index: 50; background: #1a1d23; color: #e8eaf0; display: none; align-items: center; gap: 10px; padding: 8px 24px; flex-wrap: wrap; }
body.has-tray #tray { display: flex; }
#tray .tn { background: #2b3040; border-radius: 6px; padding: 2px 4px 2px 9px; font-size: 12px; display: inline-flex; gap: 6px; align-items: center; max-width: 260px; }
#tray .tn span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
#tray .tn button { background: none; border: none; color: #aab; padding: 0 4px; }
#tray .pb { background: var(--accent); color: #fff; border: none; border-radius: 6px; padding: 5px 12px; font-weight: 600; font-size: 12.5px; }
#tray .cp { border: 1px dashed #667; background: transparent; color: #9aa; border-radius: 6px; padding: 5px 10px; font-size: 12.5px; cursor: not-allowed; }
#tray .lk { background: none; border: none; color: #aab; font-size: 12px; text-decoration: underline; }
#sheet { position: fixed; left: 0; right: 0; bottom: var(--tray-h); height: 48vh; z-index: 45; background: var(--panel); border-top: 1px solid var(--border-strong);
  box-shadow: 0 -8px 24px rgba(20,24,33,.14); transform: translateY(102%); transition: transform .18s ease; display: flex; flex-direction: column; }
body.sheet-open #sheet { transform: none; }
body.sheet-open.drawer-open #sheet { right: var(--drawer-w); }
body.sheet-open main { padding-bottom: calc(48vh + 40px); }
#sheet .shh { display: flex; align-items: center; gap: 12px; padding: 8px 18px; border-bottom: 1px solid var(--border); }
#sheet .shh h3 { margin: 0; font-size: 14px; }
#sheet .shb { overflow: auto; flex: 1; }
table.cmp { border-collapse: separate; border-spacing: 0; min-width: 100%; table-layout: fixed; }
table.cmp th, table.cmp td { padding: 7px 12px; border-bottom: 1px solid var(--border); border-right: 1px solid var(--border); vertical-align: top; font-size: 12.5px; text-align: left; }
table.cmp th.rl { position: sticky; left: 0; z-index: 2; width: 150px; background: #fbfbfc; font-size: 10.5px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted-2); font-weight: 650; }
table.cmp thead th { position: sticky; top: 0; z-index: 3; background: #fbfbfc; min-width: 250px; }
table.cmp thead th.rl { z-index: 4; }
table.cmp tr.hl td, table.cmp tr.hl th.rl { background: var(--link-bg); }
table.cmp tr.cs-row td, table.cmp tr.cs-row th.rl { background: #f4f2fc; }
table.cmp ul { margin: 0; padding-left: 16px; }
table.cmp .scopetag { display: block; font-size: 9.5px; color: #4a3f9b; letter-spacing: .04em; }
table.cmp .empty { color: var(--muted-2); }
#toast { position: fixed; left: 50%; top: 70px; transform: translateX(-50%); background: #1a1d23; color: #fff; padding: 7px 14px; border-radius: 8px; font-size: 12.5px; z-index: 90; display: none; }
@media (max-width: 900px) {
  :root { --drawer-w: 100vw; }
  body.drawer-open main { margin-right: 0; }
  body.sheet-open.drawer-open #sheet { right: 0; }
  header, .toolbar, main { padding-left: 14px; padding-right: 14px; }
  .node { width: 210px; }
}
"""

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<!--
  Header counts are each one SQL statement (run read-only against the mh2.db this page was built from):
__PREDICATES__
  In-grade node  = a node with a node_grade row for the selected grade.
  Context node   = any other node in a shown concept/skill (other-grade, leaf without a grade, or no_grade_field).
-->
<style>__CSS__</style>
</head>
<body>
<div class="banner"><b>READ-ONLY PROTOTYPE</b> &mdash; a paper prototype generated from real ladder data for discussion. Nothing here saves, places, or changes any data. Module builder and "Co-place" are not wired.</div>
<header>
  <h1 id="title"></h1>
  <div class="counts" id="counts"></div>
</header>
<div class="toolbar" id="toolbar"></div>
<main id="main">
  <div class="legend" id="legend"></div>
  <div id="slice"></div>
  <div id="empty">Nothing to show with the current view controls.</div>
  <section class="mb" id="mb"></section>
</main>
<aside id="drawer" aria-label="Node detail"></aside>
<section id="sheet" aria-label="Comparison"></section>
<div id="tray"></div>
<div id="toast"></div>
<script id="payload" type="application/json">__DATA__</script>
<script>__JS__</script>
</body>
</html>
"""

JS = r"""
(function () {
'use strict';
const P = JSON.parse(document.getElementById('payload').textContent);
const M = P.meta, N = P.nodes;
const GL = P.grade_labels;
const MAX_CMP = 4;
const LS_KEY = 'seqproto.v1.' + M.grade;

// ---------- state ----------
const prefs = { ctx: true, flagged: false, hiddenStems: [], hiddenCS: [] };
try { Object.assign(prefs, JSON.parse(localStorage.getItem(LS_KEY) || '{}')); } catch (e) {}
function savePrefs() { try { localStorage.setItem(LS_KEY, JSON.stringify(prefs)); } catch (e) {} }
const ui = { node: null, cmp: [], sheet: false };

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
const $ = id => document.getElementById(id);
const gl = g => GL[g] || g;

// ---------- URL hash:  #n=<node>&cmp=a,b,c&sheet=1 ----------
function readHash() {
  const h = new URLSearchParams(location.hash.replace(/^#/, ''));
  ui.node = N[h.get('n')] ? h.get('n') : null;
  ui.cmp = (h.get('cmp') || '').split(',').filter(id => N[id]).slice(0, MAX_CMP);
  ui.sheet = h.get('sheet') === '1' && ui.cmp.length > 0;
}
function writeHash() {
  const parts = [];
  if (ui.node) parts.push('n=' + encodeURIComponent(ui.node));
  if (ui.cmp.length) parts.push('cmp=' + ui.cmp.map(encodeURIComponent).join(','));
  if (ui.sheet && ui.cmp.length) parts.push('sheet=1');
  const s = parts.join('&');
  try { history.replaceState(null, '', s ? '#' + s : location.pathname + location.search); } catch (e) {}
}
function toast(msg) { const t = $('toast'); t.textContent = msg; t.style.display = 'block'; clearTimeout(toast.t); toast.t = setTimeout(() => t.style.display = 'none', 2200); }

// ---------- flags ----------
function flagsOf(n) {
  const f = [];
  if (n.shared.length) f.push('shared');
  if (n.kind === 'leaf') f.push('leaf');
  if (n.kind === 'unresolved') f.push('unresolved');
  return f;
}

// ---------- header / toolbar / legend ----------
function renderHeader() {
  const c = M.counts, x = M.extra;
  $('title').innerHTML = 'Grade ' + esc(M.grade_label) + ' slice <small>primary view &middot; read-only prototype &middot; built ' + esc(M.generated) + ' from ' + esc(M.db) + '</small>';
  const tip = k => esc(M.predicates[k]);
  $('counts').innerHTML =
    '<span><b>' + c.cs_shown + '</b> of ' + c.cs_total + ' concept/skills <span class="i" title="' + tip('cs_shown') + '">shown</span> in <b>' + c.stems_shown + '</b> stems</span>' +
    '<span><b>' + c.in_grade + '</b> in-grade nodes <span class="i" title="' + tip('in_grade') + '">(i)</span></span>' +
    '<span><b>' + c.context + '</b> context nodes <span class="i" title="' + tip('context') + '">(i)</span>' +
      ' &middot; ' + x.context_leaf + ' leaf, ' + x.context_unresolved + ' no grade field</span>' +
    '<span>' + x.in_grade_multi + ' in-grade nodes also in other grades &middot; ' + x.in_grade_shared_ccss + ' with shared-CCSS pairing</span>';
}
function renderToolbar() {
  const stems = [];
  P.groups.forEach(g => g.stems.forEach(s => stems.push([g.domain, s])));
  let menu = '', lastDom = null;
  stems.forEach(([d, s]) => {
    if (d !== lastDom) { menu += '<div class="dom">' + esc(d) + '</div>'; lastDom = d; }
    const nn = s.cs.reduce((a, c) => a + c.in_grade_n, 0);
    menu += '<label><input type="checkbox" data-act="stemcb" data-id="' + esc(s.stem_id) + '"' + (prefs.hiddenStems.includes(s.stem_id) ? '' : ' checked') + '> ' + esc(s.name) + '<span class="n">' + s.cs.length + ' c/s &middot; ' + nn + ' in-grade</span></label>';
  });
  const hiddenN = prefs.hiddenStems.length + prefs.hiddenCS.length;
  $('toolbar').innerHTML =
    '<button class="tog ' + (prefs.ctx ? 'on' : '') + '" data-act="ctx" title="Show the other-grade nodes of each progression as dimmed context">Context grades: ' + (prefs.ctx ? 'on' : 'off') + '</button>' +
    '<button class="tog ' + (prefs.flagged ? 'on' : '') + '" data-act="flagged" title="Only concept/skills with a shared-CCSS pairing, a leaf, or a node with no grade field">Flagged only</button>' +
    '<details class="stempick"><summary class="tog">Stems &#9662;</summary><div class="menu">' + menu + '</div></details>' +
    '<span class="sp"></span>' +
    (hiddenN ? '<span style="color:var(--muted)">' + hiddenN + ' hidden</span>' : '') +
    '<button class="linkbtn" data-act="reset">Reset view</button>';
}
function renderLegend() {
  $('legend').innerHTML =
    '<span><span class="sw" style="background:#fff;border:1px solid var(--border-strong);border-left:4px solid var(--accent)"></span>in this grade</span>' +
    '<span><span class="sw" style="background:#f7f8fa;border:1px dashed var(--border-strong);opacity:.7"></span>other grade (context; bridge if placed here)</span>' +
    '<span><span class="sw" style="background:#fbfaf5;border:2px dotted var(--border-strong)"></span>leaf</span>' +
    '<span><span class="sw" style="background:repeating-linear-gradient(135deg,#fff 0 4px,#fdf6e3 4px 8px);border:1px dashed var(--yellow-bd)"></span>no grade field (unresolved)</span>' +
    '<span><span class="pill link">&#8644; CCSS</span> same CCSS code in another stem &mdash; a pairing to consider, not a conflict</span>' +
    '<span>Strips are in ladder order, left to right. &#10010; adds a node to the compare tray.</span>';
}

// ---------- slice ----------
function pills(n, forGrade) {
  let h = '<span class="pill seq">#' + n.seq + '</span>';
  n.grades.forEach(g => { h += '<span class="pill g' + (g === M.grade ? ' here' : '') + '">' + esc(gl(g)) + '</span>'; });
  if (!n.grades.length) h += '<span class="pill g">no grade</span>';
  return h;
}
function chip(n) {
  const cls = ['node', n.in_grade ? 'in' : 'ctx'];
  if (n.kind === 'leaf') cls.push('leaf');
  if (n.kind === 'unresolved') cls.push('unres');
  if (ui.cmp.includes(n.id)) cls.push('sel');
  if (ui.node === n.id) cls.push('active');
  let bot = '';
  if (!n.in_grade && n.kind === 'ruled') bot += '<span class="pill off" title="Not tagged to ' + esc(gl(M.grade)) + '. Placing it here would make a bridge.">off-grade</span>';
  if (n.kind === 'leaf') bot += '<span class="pill leaf" title="Leaf: outside the core sequence">leaf</span>';
  if (n.kind === 'unresolved') bot += '<span class="pill unres" title="The ladder had no grade for this node">no grade field</span>';
  if (n.shared.length) bot += '<span class="pill link" title="' + esc(n.shared.map(s => s.code + ' also in ' + s.stems.join(', ')).join('\n')) + '">&#8644; ' + n.shared.length + ' CCSS shared</span>';
  return '<div class="' + cls.join(' ') + '" data-id="' + esc(n.id) + '" role="button" tabindex="0">' +
    '<div class="top">' + pills(n) + '</div>' +
    '<div class="txt" title="' + esc(n.text) + '">' + esc(n.text) + '</div>' +
    '<div class="bot">' + bot + '</div>' +
    '<button class="cmpbtn" data-act="cmp" data-id="' + esc(n.id) + '" title="Add/remove in compare tray">' + (ui.cmp.includes(n.id) ? '&#10003;' : '&#10010;') + '</button>' +
    '</div>';
}
function strip(cs) {
  let h = '', hidden = 0, first = true;
  const flush = () => { if (hidden) { h += (first ? '' : '<span class="arrow">&rsaquo;</span>') + '<span class="gap" title="Context grades are off">&middot;&middot;&middot; ' + hidden + ' context hidden</span>'; hidden = 0; first = false; } };
  cs.node_ids.forEach(id => {
    const n = N[id];
    const show = prefs.ctx || n.in_grade || n.kind !== 'ruled';   // leaf / unresolved never vanish
    if (!show) { hidden++; return; }
    flush();
    h += (first ? '' : '<span class="arrow">&rsaquo;</span>') + chip(n); first = false;
  });
  flush();
  return '<div class="strip">' + h + '</div>';
}
function csBlock(cs) {
  if (prefs.hiddenCS.includes(cs.key)) return '<div class="cs stub">' + esc(cs.name) + ' <button class="ibtn" data-act="showcs" data-id="' + esc(cs.key) + '">show</button></div>';
  const nn = cs.node_ids.length;
  return '<div class="cs" data-cs="' + esc(cs.key) + '"><div class="cs-h"><h4>' + esc(cs.name) + '</h4>' +
    '<span class="meta">' + cs.in_grade_n + ' in-grade of ' + nn + (cs.goal ? '' : ' &middot; no Goal recorded') + '</span>' +
    '<span class="sp" style="flex:1"></span><button class="ibtn" data-act="hidecs" data-id="' + esc(cs.key) + '" title="Hide this concept/skill">hide</button></div>' + strip(cs) + '</div>';
}
function csFlagged(cs) { return cs.node_ids.some(id => flagsOf(N[id]).length); }
function renderSlice() {
  let h = '', shownStems = 0;
  P.groups.forEach(g => {
    let inner = '';
    g.stems.forEach(s => {
      const cs = s.cs.filter(c => !prefs.flagged || csFlagged(c));
      if (!cs.length) return;
      shownStems++;
      const inGrade = cs.reduce((a, c) => a + c.in_grade_n, 0);
      if (prefs.hiddenStems.includes(s.stem_id)) {
        inner += '<div class="stem collapsed"><div class="sh"><h3>' + esc(s.name) + '</h3><span class="meta">hidden &middot; ' + cs.length + ' c/s &middot; ' + inGrade + ' in-grade nodes</span><span class="sp"></span><button class="ibtn" data-act="showstem" data-id="' + esc(s.stem_id) + '">show</button></div></div>';
        return;
      }
      inner += '<div class="stem"><div class="sh"><h3>' + esc(s.name) + '</h3><span class="meta">' + cs.length + ' concept/skill' + (cs.length === 1 ? '' : 's') + ' &middot; ' + inGrade + ' in-grade nodes</span><span class="sp"></span><button class="ibtn" data-act="hidestem" data-id="' + esc(s.stem_id) + '">hide stem</button></div>' + cs.map(csBlock).join('') + '</div>';
    });
    if (inner) h += '<section class="domain"><h2>' + esc(g.domain) + '</h2>' + inner + '</section>';
  });
  $('slice').innerHTML = h;
  $('empty').style.display = shownStems ? 'none' : 'block';
}

// ---------- drawer ----------
const FD = P.field_defs;
function fieldHtml(label, vals) {
  if (vals.length === 1) return '<div class="fld"><h5>' + esc(label) + '</h5><p>' + esc(vals[0]) + '</p></div>';
  return '<div class="fld"><h5>' + esc(label) + '</h5><ul>' + vals.map(v => '<li>' + esc(v) + '</li>').join('') + '</ul></div>';
}
function csOf(n) { for (const g of P.groups) for (const s of g.stems) for (const c of s.cs) if (c.key === n.cs) return c; return null; }
function renderDrawer() {
  const d = $('drawer');
  document.body.classList.toggle('drawer-open', !!ui.node);
  if (!ui.node) { d.innerHTML = ''; return; }
  const n = N[ui.node], cs = csOf(n), open = d.dataset.empty === '1';
  const sharedSet = new Set(cs.shared_fields);
  // --- concept/skill scope
  let csBody = '<div class="fld"><h5>Concept/skill</h5><p><b>' + esc(n.cs_name) + '</b> &middot; ' + esc(n.stem_name) + '</p></div>';
  csBody += cs.goal ? '<div class="fld"><h5>Goal</h5><p>' + esc(cs.goal) + '</p>' + (cs.goal_varies ? '<p style="color:var(--yellow-fg);font-size:12px">Note: the Goal cell is blank on some nodes of this concept/skill in the ladder (or differs); the recorded Goal is shown for the whole progression.</p>' : '') + '</div>'
                    : '<div class="fld none">No Goal recorded for this concept/skill.</div>';
  FD.filter(f => sharedSet.has(f.key)).forEach(f => { csBody += fieldHtml(f.label, n.fields[f.key]); });
  const csNote = cs.shared_fields.length ? ' + ' + cs.shared_fields.length + ' field' + (cs.shared_fields.length > 1 ? 's' : '') + ' identical on every node' : '';
  // --- node scope
  let body = '<div class="fld"><h5>Node ' + n.seq + ' of ' + cs.node_ids.length + ' in ladder</h5><p>' + esc(n.text) + '</p></div>';
  let kv = '<dl class="kv"><dt>Grades</dt><dd>' + (n.grades.length ? n.grades.map(g => esc(gl(g))).join(', ') : '<i>none</i>') + '</dd>';
  if (n.ruling) kv += '<dt>Raw grade cell</dt><dd>&ldquo;' + esc(n.ruling.raw || '') + '&rdquo;</dd><dt>Ruling</dt><dd>' + esc(n.ruling.resolution) + (n.ruling.notes ? ' &mdash; ' + esc(n.ruling.notes) : '') + '</dd>';
  kv += '</dl>';
  body += '<div class="fld"><h5>Grade (node_grade + ruling)</h5>' + kv + '</div>';
  const ccss = n.std.filter(s => !s.state), st = n.std.filter(s => s.state);
  const sharedCodes = new Set(n.shared.map(s => s.code));
  const code = s => '<span class="code ' + (sharedCodes.has(s.code) ? 'shared ' : '') + esc(s.relation || '') + '" title="' + esc((s.relation || '') + (s.note ? ' - ' + s.note : '')) + '">' + esc(s.code) + '</span>';
  body += '<div class="fld"><h5>Standards (CCSS)</h5>' + (ccss.length ? '<div class="codes">' + ccss.map(code).join('') + '</div>' : '<span style="color:var(--muted-2)">none parsed</span>') + '</div>';
  if (n.shared.length) body += '<div class="fld"><h5>Shared with other stems</h5><ul>' + n.shared.map(s => '<li><span class="code shared">' + esc(s.code) + '</span> also in ' + esc(s.stems.join(', ')) + '</li>').join('') + '</ul></div>';
  if (st.length) body += '<div class="fld"><h5>State standards (' + st.length + ')</h5><div class="codes">' + st.map(s => '<span class="code ' + esc(s.relation || '') + '" title="' + esc(s.relation || '') + '">' + esc(s.code) + '</span>').join('') + '</div></div>';
  body += '<div class="fld"><h5>Product refs (' + n.refs.length + ')</h5>' + (n.refs.length ? '<ul>' + n.refs.map(r => '<li>' + esc(r.ref) + '</li>').join('') + '</ul>' : '<span style="color:var(--muted-2)">none</span>') + '</div>';
  body += '<div class="fld"><h5>Stated cross-stem links (' + n.links.length + ')</h5>' + (n.links.length ? '<ul>' + n.links.map(l => '<li>' + esc(l.text) + (l.related_node_id ? '' : ' <span style="color:var(--muted-2)">(unresolved text)</span>') + '</li>').join('') + '</ul>' : '<span style="color:var(--muted-2)">none</span>') + '</div>';
  const own = FD.filter(f => !sharedSet.has(f.key));
  const filled = own.filter(f => (n.fields[f.key] || []).length), empty = own.filter(f => !(n.fields[f.key] || []).length);
  filled.forEach(f => { body += fieldHtml(f.label, n.fields[f.key]); });
  if (empty.length) {
    body += '<div class="showempty"><button class="linkbtn" data-act="toggleempty">' + (open ? 'hide' : 'show') + ' empty (' + empty.length + ')</button></div>';
    if (open) empty.forEach(f => { body += '<div class="fld none">' + esc(f.label) + ': empty</div>'; });
  }
  d.innerHTML =
    '<div class="dh"><div class="t"><div class="id">' + esc(n.id) + '</div><div class="sub">' + esc(n.stem_name) + ' &rsaquo; ' + esc(n.cs_name) + '</div></div>' +
    '<button class="tog" data-act="cmp" data-id="' + esc(n.id) + '">' + (ui.cmp.includes(n.id) ? 'In compare &#10003;' : 'Add to compare') + '</button>' +
    '<button class="ibtn" data-act="closedrawer" title="Close">&#10005;</button></div>' +
    '<div class="db"><div class="scope cs"><div class="sl">Concept/skill-scoped <small>applies to all ' + cs.node_ids.length + ' nodes in this progression' + csNote + '</small></div><div class="sb">' + csBody + '</div></div>' +
    '<div class="scope nd"><div class="sl">Node-specific <small>this node only</small></div><div class="sb">' + body + '</div></div></div>';
}

// ---------- compare tray + sheet ----------
function renderTray() {
  const t = $('tray'), has = ui.cmp.length > 0;
  document.body.classList.toggle('has-tray', has);
  document.documentElement.style.setProperty('--tray-h', has ? '46px' : '0px');
  if (!has) { t.innerHTML = ''; ui.sheet = false; renderSheet(); return; }
  t.innerHTML = '<span style="font-size:12px;color:#aab">Compare ' + ui.cmp.length + '/' + MAX_CMP + '</span>' +
    ui.cmp.map(id => '<span class="tn"><span title="' + esc(N[id].text) + '">' + esc(N[id].stem_name + ': ' + N[id].text) + '</span><button data-act="cmp" data-id="' + esc(id) + '" title="Remove">&#10005;</button></span>').join('') +
    '<span style="flex:1"></span>' +
    '<button class="pb" data-act="sheet">' + (ui.sheet ? 'Hide comparison' : 'Compare (' + ui.cmp.length + ')') + '</button>' +
    '<button class="cp" disabled title="Prototype: no placements are written anywhere">Co-place in a module &mdash; prototype, not wired</button>' +
    '<button class="lk" data-act="copylink">copy link</button><button class="lk" data-act="clearcmp">clear</button>';
}
function renderSheet() {
  const s = $('sheet');
  document.body.classList.toggle('sheet-open', ui.sheet && ui.cmp.length > 0);
  if (!(ui.sheet && ui.cmp.length)) { s.innerHTML = ''; return; }
  const ns = ui.cmp.map(id => N[id]);
  // which CCSS codes occur on 2+ of the selected nodes
  const cnt = {};
  ns.forEach(n => new Set(n.std.filter(x => !x.state).map(x => x.code)).forEach(c => cnt[c] = (cnt[c] || 0) + 1));
  const both = Object.keys(cnt).filter(c => cnt[c] > 1).sort();
  const cols = ns.length;
  let head = '<tr><th class="rl"></th>' + ns.map(n => '<th><b>' + esc(n.id) + '</b><br><span style="font-weight:400;text-transform:none;letter-spacing:0">' + esc(n.stem_name) + '</span></th>').join('') + '</tr>';
  const row = (label, cells, cls, tag) => '<tr class="' + (cls || '') + '"><th class="rl">' + esc(label) + (tag ? '<span class="scopetag">' + tag + '</span>' : '') + '</th>' + cells.map(c => '<td>' + (c || '<span class="empty">&mdash;</span>') + '</td>').join('') + '</tr>';
  const list = v => v && v.length ? (v.length === 1 ? esc(v[0]) : '<ul>' + v.map(x => '<li>' + esc(x) + '</li>').join('') + '</ul>') : '';
  let rows = '';
  rows += row('Shared CCSS (this selection)', ns.map(n => n.std.filter(x => !x.state && cnt[x.code] > 1).map(x => '<span class="code shared">' + esc(x.code) + '</span>').join(' ')), both.length ? 'hl' : '');
  rows += row('Concept/skill', ns.map(n => esc(n.cs_name)), 'cs-row', 'CONCEPT/SKILL-SCOPED');
  rows += row('Goal', ns.map(n => esc(csOf(n).goal)), 'cs-row', 'CONCEPT/SKILL-SCOPED');
  rows += row('Node text', ns.map(n => esc(n.text)));
  rows += row('Grades', ns.map(n => n.grades.map(g => esc(gl(g))).join(', ') + (n.kind !== 'ruled' ? ' <span class="pill ' + n.kind + '">' + n.kind + '</span>' : '')));
  rows += row('Ladder position', ns.map(n => n.seq + ' of ' + csOf(n).node_ids.length));
  rows += row('CCSS standards', ns.map(n => n.std.filter(x => !x.state).map(x => '<span class="code ' + (cnt[x.code] > 1 ? 'shared' : '') + '">' + esc(x.code) + '</span>').join(' ')), both.length ? 'hl' : '');
  rows += row('State standards', ns.map(n => { const k = n.std.filter(x => x.state); return k.length ? k.length + ': ' + esc(k.slice(0, 8).map(x => x.code).join(', ')) + (k.length > 8 ? ' &hellip;' : '') : ''; }));
  FD.forEach(f => {
    const vals = ns.map(n => n.fields[f.key] || []);
    if (!vals.some(v => v.length)) return;
    rows += row(f.label, vals.map(list));
  });
  rows += row('Product refs', ns.map(n => n.refs.length ? '<ul>' + n.refs.map(r => '<li>' + esc(r.ref) + '</li>').join('') + '</ul>' : ''));
  rows += row('Stated links', ns.map(n => n.links.length ? '<ul>' + n.links.map(l => '<li>' + esc(l.text) + '</li>').join('') + '</ul>' : ''));
  $('sheet').innerHTML =
    '<div class="shh"><h3>Synthetic ladder &mdash; ' + cols + ' node' + (cols > 1 ? 's' : '') + ' side by side</h3>' +
    '<span style="color:var(--muted);font-size:12px">' + (both.length ? 'Shared CCSS: ' + both.map(esc).join(', ') : 'No shared CCSS code in this selection') + '</span><span style="flex:1"></span>' +
    '<button class="tog" disabled title="Prototype: not wired" style="cursor:not-allowed">Co-place in a module &mdash; prototype, not wired</button>' +
    '<button class="ibtn" data-act="sheet" title="Close">&#10005;</button></div>' +
    '<div class="shb"><table class="cmp"><thead>' + head + '</thead><tbody>' + rows + '</tbody></table></div>';
}

// ---------- module builder placeholder ----------
function renderMB() {
  $('mb').innerHTML = '<h2>Module builder <span class="pill unres">placeholder &mdash; non-functional</span></h2>' +
    '<div class="note">Placements would go here: ordered nodes per module for Grade ' + esc(M.grade_label) + ', with co-placement grouping, a calibration level (Deep / Functional / Illuminating) and period estimate per placement. Calibration and time are placement-scoped, so they would not appear in the drawer above.</div>' +
    '<div class="cols"><div class="col"><b>Module 1</b>drop / place nodes here</div><div class="col"><b>Module 2</b>drop / place nodes here</div><div class="col"><b>Module 3</b>drop / place nodes here</div><div class="col"><b>+ Add module</b>not wired</div></div>' +
    '<div class="gr">Guardrail readout (target 25 / 50 / 25 by count, 40 / 45 / 15 by time) &mdash; empty until placements exist.<div class="bar"><span style="width:25%;background:#3457d5"></span><span style="width:50%;background:#7d94e6"></span><span style="width:25%;background:#c3ccf3"></span></div></div>';
}

// ---------- wiring ----------
function toggleCmp(id) {
  const i = ui.cmp.indexOf(id);
  if (i >= 0) ui.cmp.splice(i, 1);
  else if (ui.cmp.length >= MAX_CMP) { toast('Compare holds at most ' + MAX_CMP + ' nodes. Remove one first.'); return; }
  else ui.cmp.push(id);
  if (!ui.cmp.length) ui.sheet = false;
  syncChips(); renderTray(); renderSheet(); renderDrawer(); writeHash();
}
function syncChips() {
  document.querySelectorAll('.node').forEach(el => {
    const id = el.dataset.id, sel = ui.cmp.includes(id);
    el.classList.toggle('sel', sel);
    el.classList.toggle('active', ui.node === id);
    const b = el.querySelector('.cmpbtn'); if (b) b.innerHTML = sel ? '&#10003;' : '&#10010;';
  });
}
function openNode(id) { ui.node = id; $('drawer').dataset.empty = '0'; syncChips(); renderDrawer(); writeHash(); }
function fullRender() { renderToolbar(); renderSlice(); syncChips(); }
function mutate(list, id, on) { const i = list.indexOf(id); if (on && i < 0) list.push(id); if (!on && i >= 0) list.splice(i, 1); savePrefs(); fullRender(); }

document.addEventListener('click', e => {
  const a = e.target.closest('[data-act]');
  if (a) {
    const act = a.dataset.act, id = a.dataset.id;
    if (act === 'cmp') { e.stopPropagation(); toggleCmp(id); return; }
    if (act === 'ctx') { prefs.ctx = !prefs.ctx; savePrefs(); fullRender(); return; }
    if (act === 'flagged') { prefs.flagged = !prefs.flagged; savePrefs(); fullRender(); return; }
    if (act === 'hidestem') return mutate(prefs.hiddenStems, id, true);
    if (act === 'showstem') return mutate(prefs.hiddenStems, id, false);
    if (act === 'hidecs') return mutate(prefs.hiddenCS, id, true);
    if (act === 'showcs') return mutate(prefs.hiddenCS, id, false);
    if (act === 'reset') { prefs.ctx = true; prefs.flagged = false; prefs.hiddenStems = []; prefs.hiddenCS = []; savePrefs(); fullRender(); return; }
    if (act === 'closedrawer') { ui.node = null; syncChips(); renderDrawer(); writeHash(); return; }
    if (act === 'toggleempty') { const d = $('drawer'); d.dataset.empty = d.dataset.empty === '1' ? '0' : '1'; renderDrawer(); return; }
    if (act === 'sheet') { ui.sheet = !ui.sheet; renderTray(); renderSheet(); writeHash(); return; }
    if (act === 'clearcmp') { ui.cmp = []; ui.sheet = false; syncChips(); renderTray(); renderSheet(); renderDrawer(); writeHash(); return; }
    if (act === 'copylink') { try { navigator.clipboard.writeText(location.href); toast('Link copied'); } catch (x) { toast('Copy the address bar to share this selection'); } return; }
    return;
  }
  const c = e.target.closest('.node');
  if (c) openNode(c.dataset.id);
});
document.addEventListener('change', e => {
  const a = e.target.closest('[data-act="stemcb"]');
  if (a) { mutate(prefs.hiddenStems, a.dataset.id, !a.checked); const d = document.querySelector('details.stempick'); if (d) d.open = true; }
});
document.addEventListener('keydown', e => {
  if ((e.key === 'Enter' || e.key === ' ') && e.target.classList && e.target.classList.contains('node')) { e.preventDefault(); openNode(e.target.dataset.id); }
  if (e.key === 'Escape' && ui.node) { ui.node = null; syncChips(); renderDrawer(); writeHash(); }
});
window.addEventListener('hashchange', () => { readHash(); syncChips(); renderDrawer(); renderTray(); renderSheet(); });

readHash();
renderHeader(); renderLegend(); renderToolbar(); renderSlice(); renderMB();
renderDrawer(); renderTray(); renderSheet(); syncChips();
})();
"""


# ============================================================================
# CLI
# ============================================================================

def grade_slug(grade: str) -> str:
    """File-name slug: digits get a 'g' prefix (2 -> g2), others lowercase (PK -> pk)."""
    return f"g{grade}" if grade.isdigit() else grade.lower()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--grade", default="2", help="grade token, e.g. PK, K, 2, A1 (default 2)")
    ap.add_argument("--out", help="output html path (default <reports>/seq_prototype_<slug>.html)")
    ap.add_argument("--db", help="path to mh2.db (default: $MH2_DB or config.DB)")
    args = ap.parse_args()

    db = resolve_db(args.db)
    con = connect_readonly(db)
    grade = normalize_grade_token(con, args.grade)
    payload = build_payload(con, grade, db)
    con.close()

    out = Path(args.out) if args.out else config.REPORTS / f"seq_prototype_{grade_slug(grade)}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(payload), encoding="utf-8")
    m = payload["meta"]
    c = m["counts"]
    print(f"Wrote {out}  ({out.stat().st_size // 1024} KB)")
    print(f"Grade {grade}: {c['cs_shown']}/{c['cs_total']} concept/skills, {c['stems_shown']} stems, "
          f"{c['in_grade']} in-grade nodes, {c['context']} context nodes")


if __name__ == "__main__":
    main()
