"""Throwaway-but-kept fixture generator for the F (frontend) tests and demo.

It is NOT the real read layer (that is mh2/seq_read.py, owned by R). It is a
small, independent re-implementation of the contract's section 3.2 / 5 shapes,
written by F so the frontend can be built and demoed before R merges. The
integrator regenerates the real demo from mh2.seq_read; this only produces a
"demo data built from fixture" payload (format mh2-seq-demo/1).

    MH2_DATA_DIR=... python3 tests/js/fixtures/gen_fixture_from_db.py --grade 2 \
        --out tests/js/fixtures/g2_payload.json

Shapes follow docs/seq_v1_contract.md sections 3.3, 5.1-5.5 exactly.
Read-only on mh2.db (mode=ro).
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib  # noqa: F401  (kept for parity with R's imports; unused here)
import hashlib
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from scripts.report_period_estimates import parse_period_notes  # noqa: E402

FIELD_ORDER = [
    ("specifications", "Specifications"), ("required_skills_cases", "Required skills / cases"),
    ("standards_notes", "Standards notes"), ("considerations", "Considerations"),
    ("misconceptions", "Misconceptions"), ("mathematical_models", "Mathematical models"),
    ("strategies", "Strategies"), ("terminology", "Terminology"),
    ("leaves_to_include", "Leaves to include"), ("intervention_notes", "Intervention notes"),
    ("product_reference", "Existing product reference"), ("additional_notes", "Additional notes")]
MARKER_RE = re.compile(r"\s*\(\s*[A-Z][a-z]+\s*[-–—]?\s*done\s*\)\s*$", re.I)
OTHER_PRODUCT = re.compile(r"TX\b|Bluebonnet|Maryland|Catalyst", re.I)
GRADES = ["PK", "K", "1", "2", "3", "4", "5", "6", "7", "8", "A1"]
GRADE_LABELS = {"PK": "Pre-K", "K": "Kindergarten", "A1": "Algebra 1",
                **{str(i): f"Grade {i}" for i in range(1, 9)}}
GRADE_SHORT = {"PK": "PK", "K": "K", "A1": "A1", **{str(i): f"G{i}" for i in range(1, 9)}}
OWED = ("core", "span", "unconfirmed", "unknown")
BRIDGE = ("off_grade", "state_extension", "leaf", "no_grade")


def ws(s):
    return re.sub(r"\s+", " ", s or "").strip()


def display_label(s):
    return ws(MARKER_RE.sub("", s or ""))


def cs_id(stem_id, cs):
    return f"{stem_id}:{hashlib.sha1((cs or '').encode('utf-8')).hexdigest()[:8]}"


class Db:
    def __init__(self, con):
        con.row_factory = sqlite3.Row
        self.con = con
        self.stems = {r["stem_id"]: dict(r) for r in con.execute("SELECT * FROM stems")}
        self.stem_name = {k: ws(v["name"]) for k, v in self.stems.items()}
        self.nodes = [dict(r) for r in con.execute("SELECT * FROM nodes ORDER BY stem_id, seq")]
        self.by_id = {n["node_id"]: n for n in self.nodes}
        self.by_key = {n["source_key"]: n for n in self.nodes}
        self.grades = defaultdict(list)
        for r in con.execute("SELECT node_id, grade FROM node_grade"):
            self.grades[r["node_id"]].append(r["grade"])
        for k in self.grades:
            self.grades[k].sort(key=GRADES.index)
        self.kinds = {(r["node_id"], r["grade"]): r["kind"]
                      for r in con.execute("SELECT node_id, grade, kind FROM node_grade_kind")}
        self.kind_source = "grade_type" if self.kinds else "fallback"
        self.ruling = {r["node_id"]: dict(r) for r in con.execute("SELECT * FROM node_grade_ruling")}
        self.fields = defaultdict(lambda: defaultdict(list))
        for r in con.execute("SELECT node_id, field, value FROM node_fields ORDER BY node_id, field, ordinal"):
            if (r["value"] or "").strip():
                self.fields[r["node_id"]][r["field"]].append(r["value"])
        self.std = defaultdict(list)
        for r in con.execute("SELECT * FROM node_standards_parsed ORDER BY node_id, standard_code"):
            self.std[r["node_id"]].append(dict(r))
        self.refs = defaultdict(list)
        for r in con.execute("SELECT rowid, * FROM product_refs ORDER BY rowid"):
            self.refs[r["node_id"]].append(dict(r))
        self.links = defaultdict(list)
        for r in con.execute("SELECT * FROM node_links WHERE link_type='stated' ORDER BY rowid"):
            self.links[r["node_id"]].append(r["related_text"])
        # shared CCSS: code -> {node_id}
        self.code_nodes = defaultdict(set)
        for nid, L in self.std.items():
            for s in L:
                if s["state"] is None:
                    self.code_nodes[s["standard_code"]].add(nid)
        self.lesson_nodes = defaultdict(set)
        for nid, L in self.refs.items():
            for r in L:
                if r["lesson_id"] and not OTHER_PRODUCT.search(r["raw_ref"] or ""):
                    self.lesson_nodes[r["lesson_id"]].add(nid)
        self.ladders_last_read = con.execute("SELECT MAX(ts) FROM ingest_log").fetchone()[0]
        self.stem_by_name = {v: k for k, v in self.stem_name.items()}

    # ---- kinds
    def state(self, nid, grade):
        rl = self.ruling.get(nid) or {}
        if rl.get("resolution") == "leaf":
            return "leaf"
        if rl.get("resolution") == "no_grade_field":
            return "no_grade"
        if grade in self.grades.get(nid, []):
            return self.kinds.get((nid, grade), "unknown")
        return "off_grade"

    def other_grades(self, nid, grade):
        return [g for g in self.grades.get(nid, []) if g != grade]

    # ---- partners (shared ccss with a node in a different stem)
    def partners(self, n, grade):
        sid = n["stem_id"]
        per = defaultdict(lambda: {"codes": set(), "nodes": set()})
        for s in self.std.get(n["node_id"], []):
            if s["state"] is not None:
                continue
            for other in self.code_nodes[s["standard_code"]]:
                o = self.by_id[other]
                if o["stem_id"] != sid:
                    per[o["stem_id"]]["codes"].add(s["standard_code"])
                    per[o["stem_id"]]["nodes"].add(other)
        out = []
        for psid in sorted(per):
            nodes = sorted(per[psid]["nodes"], key=lambda i: self.by_id[i]["seq"])
            out.append({"stem_id": psid, "stem_name": self.stem_name.get(psid, psid),
                        "codes": sorted(per[psid]["codes"]),
                        "nodes": [{"source_key": self.by_id[i]["source_key"], "node_id": i,
                                   "in_grade": grade in self.grades.get(i, [])} for i in nodes]})
        return out

    def badges(self, n, grade, state):
        nid = n["node_id"]
        rl = self.ruling.get(nid) or {}
        B = []

        def b(code, tier, label, detail, partners=None):
            B.append({"code": code, "tier": tier, "label": label, "detail": detail,
                      "refs": [], "partners": partners or []})
        if state == "span":
            b("span", "info", "span", "Also " + ", ".join(GRADE_SHORT[g] for g in self.other_grades(nid, grade)))
        elif state == "unconfirmed":
            d = "Grade ruling not final: " + str(rl.get("raw_value"))
            if rl.get("states_mentioned"):
                d += "; states: " + rl["states_mentioned"]
            b("unconfirmed", "info", "unconfirmed", d)
        elif state == "unknown":
            b("kind_pending", "info", "kind pending", "No kind recorded for this grade")
        elif state == "state_extension":
            b("state_ext", "info", "state ext", rl.get("states_mentioned") or rl.get("notes"))
        elif state == "leaf":
            b("leaf", "info", "leaf", rl.get("raw_value"))
        elif state == "no_grade":
            b("no_grade", "info", "no grade", "The ladder cell has no grade")
        P = self.partners(n, grade)
        if P:
            b("shared_code", "pairing", "pairs: " + ", ".join(p["stem_id"] for p in P),
              "Shares CCSS codes with " + ", ".join(f"{p['stem_name']} ({len(p['codes'])})" for p in P), P)
        return B

    def period_hint(self, nid, grade):
        lines = [v for v in self.fields[nid].get("additional_notes", [])] if nid in self.fields else []
        ests = parse_period_notes(lines)
        kept = [e for e in ests if e.grade == grade]
        basis = "grade_named"
        ng = self.grades.get(nid, [])
        if not kept and len(ng) == 1:
            kept = [e for e in ests if e.grade is None]
            basis = "single_grade_node"
        if not kept and len(ng) > 1:
            kept = [e for e in ests if e.grade is None]
            basis = "ungraded_multi_grade"
        if not kept:
            return None
        k0 = kept[0]
        value = None
        if (len(kept) == 1 and basis != "ungraded_multi_grade" and k0.qualifier == "exact"
                and k0.unit == "period" and "group_total" not in (k0.note or "")):
            value = k0.low
        return {"text": " · ".join(e.snippet for e in kept), "value": value, "unit": k0.unit,
                "qualifier": k0.qualifier, "low": k0.low, "high": k0.high, "basis": basis,
                "n_estimates": len(kept)}

    # ---- slice
    def build_slice(self, grade):
        cs_groups = defaultdict(list)
        for n in self.nodes:
            cs_groups[(n["stem_id"], n["concept_skill"])].append(n)
        included = {k: v for k, v in cs_groups.items()
                    if any(grade in self.grades.get(n["node_id"], []) for n in v)}
        by_stem = defaultdict(list)
        counts = defaultdict(int)
        for (sid, cs), nodes in included.items():
            nodes = sorted(nodes, key=lambda n: n["seq"])
            goals = sorted({n["goal"] for n in nodes if (n["goal"] or "").strip()})
            assert len(goals) <= 1, (sid, cs, goals)
            sn = []
            for n in nodes:
                nid = n["node_id"]
                st = self.state(nid, grade)
                ing = grade in self.grades.get(nid, [])
                rl = self.ruling.get(nid) or {}
                sn.append({"source_key": n["source_key"], "node_id": nid, "seq": n["seq"],
                           "node_text": ws(n["node_text"]), "grades": self.grades.get(nid, []),
                           "state": st, "in_grade": ing, "owed": st in OWED,
                           "requires_confirm": st in BRIDGE, "resolution": rl.get("resolution"),
                           "grade_raw": rl.get("raw_value"), "states_mentioned": rl.get("states_mentioned"),
                           "badges": self.badges(n, grade, st), "placed_elsewhere": [],
                           "period_hint": self.period_hint(nid, grade)})
            ing_n = sum(1 for x in sn if x["in_grade"])
            ing_nl = sum(1 for x in sn if x["in_grade"] and x["state"] != "leaf")
            by_stem[sid].append((min(n["seq"] for n in nodes), {
                "cs_id": cs_id(sid, cs), "label": cs, "label_display": display_label(cs),
                "goal": goals[0] if goals else None, "goal_missing": not goals,
                "in_grade_count": ing_n, "in_grade_count_excl_leaf": ing_nl, "nodes": sn}))
            counts["cs"] += 1
            counts["cs_nl"] += 1 if ing_nl else 0
            for x in sn:
                counts["chips"] += 1
                if x["in_grade"]:
                    counts["in"] += 1
                    counts["in_nl"] += 1 if x["state"] != "leaf" else 0
                    counts["owed"] += 1 if x["owed"] else 0
                    counts["leaf"] += 1 if x["state"] == "leaf" else 0
                else:
                    counts["ctx"] += 1
        dom = defaultdict(list)
        for sid, L in by_stem.items():
            L.sort(key=lambda t: t[0])
            dom[self.stems[sid]["domain"] or "(no domain)"].append(
                {"stem_id": sid, "stem_name": self.stem_name[sid], "concept_skills": [c for _, c in L]})
        supers = []
        for d in sorted(dom, key=lambda d: (d == "(no domain)", d.casefold())):
            supers.append({"domain": d, "stems": sorted(dom[d], key=lambda s: s["stem_name"].casefold())})
        return {"grade": grade, "grade_label": GRADE_LABELS[grade], "kind_source": self.kind_source,
                "ladders_last_read": self.ladders_last_read,
                "counts": {"in_grade_nodes": counts["in"], "in_grade_nodes_excl_leaf": counts["in_nl"],
                           "owed_nodes": counts["owed"], "leaf_in_grade": counts["leaf"],
                           "concept_skills": counts["cs"], "concept_skills_excl_leaf": counts["cs_nl"],
                           "context_nodes": counts["ctx"], "stems": len(by_stem), "chips": counts["chips"]},
                "super_stems": supers}

    def grades_info(self):
        out = []
        for i, g in enumerate(GRADES):
            c = self.build_slice(g)["counts"]
            out.append({"grade": g, "label": GRADE_LABELS[g], "short": GRADE_SHORT[g], "ord": i,
                        "band": None, "in_grade_nodes": c["in_grade_nodes"],
                        "owed_nodes": c["owed_nodes"], "chips": c["chips"], "sequence": None})
        return {"grades": out, "kind_source": self.kind_source, "ladders_last_read": self.ladders_last_read}

    def field_defs(self):
        known = {k for k, _ in FIELD_ORDER}
        present = {f for d in self.fields.values() for f in d}
        defs = [{"key": k, "label": l} for k, l in FIELD_ORDER]
        defs += [{"key": k, "label": k.replace("_", " ").capitalize()} for k in sorted(present - known)]
        return defs

    # ---- drawer
    def drawer(self, n, grade, slice_index):
        nid = n["node_id"]
        st = self.state(nid, grade)
        rl = self.ruling.get(nid) or {}
        sib = sorted([m for m in self.nodes if m["stem_id"] == n["stem_id"]
                      and m["concept_skill"] == n["concept_skill"]], key=lambda m: m["seq"])
        fd = self.field_defs()
        fields = [{"key": d["key"], "label": d["label"], "values": self.fields[nid][d["key"]]}
                  for d in fd if nid in self.fields and self.fields[nid].get(d["key"])]
        empty = [d for d in fd if not (nid in self.fields and self.fields[nid].get(d["key"]))]
        stds, state_codes = [], []
        for s in self.std.get(nid, []):
            if s["state"] is None:
                shared = any(self.by_id[o]["stem_id"] != n["stem_id"] for o in self.code_nodes[s["standard_code"]])
                stds.append({"code": s["standard_code"], "relation": s["relation"],
                             "annotation": s["annotation"], "shared": shared})
            else:
                state_codes.append({"code": s["standard_code"], "state": s["state"],
                                    "relation": s["relation"], "annotation": s["annotation"]})
        seen, lessons = set(), []
        for r in self.refs.get(nid, []):
            lid = r["lesson_id"]
            if not lid or lid in seen or OTHER_PRODUCT.search(r["raw_ref"] or ""):
                continue
            seen.add(lid)
            per = defaultdict(list)
            for o in sorted(self.lesson_nodes[lid], key=lambda i: self.by_id[i]["seq"]):
                if self.by_id[o]["stem_id"] != n["stem_id"]:
                    per[self.by_id[o]["stem_id"]].append(o)
            lessons.append({"lesson_id": lid, "product": r["product"], "raw_ref": r["raw_ref"],
                            "shared_with": [{"stem_id": s, "stem_name": self.stem_name.get(s, s),
                                             "nodes": [{"source_key": self.by_id[o]["source_key"], "node_id": o,
                                                        "in_grade": grade in self.grades.get(o, [])} for o in L]}
                                            for s, L in sorted(per.items())]})
        goals = sorted({m["goal"] for m in sib if (m["goal"] or "").strip()})
        return {
            "grade": grade, "source_key": n["source_key"], "node_id": nid, "node_text": ws(n["node_text"]),
            "seq": n["seq"], "stem_id": n["stem_id"], "stem_name": self.stem_name[n["stem_id"]],
            "domain": self.stems[n["stem_id"]]["domain"], "source_file": n["source_file"],
            "state": st, "in_grade": grade in self.grades.get(nid, []), "owed": st in OWED,
            "requires_confirm": st in BRIDGE,
            "cs": {"cs_id": cs_id(n["stem_id"], n["concept_skill"]), "label": n["concept_skill"],
                   "label_display": display_label(n["concept_skill"]),
                   "goal": goals[0] if goals else None, "goal_missing": not goals},
            "strip": [{"source_key": m["source_key"], "node_id": m["node_id"], "seq": m["seq"],
                       "state": self.state(m["node_id"], grade),
                       "in_grade": grade in self.grades.get(m["node_id"], []),
                       "is_self": m["node_id"] == nid} for m in sib],
            "ruling": {"raw_value": rl.get("raw_value"), "resolution": rl.get("resolution"),
                       "ruling_type": rl.get("ruling_type"), "states_mentioned": rl.get("states_mentioned"),
                       "notes": rl.get("notes"), "needs_writer_review": rl.get("needs_writer_review")},
            "grade_states": [{"grade": g, "short": GRADE_SHORT[g], "state": self.state(nid, g)}
                             for g in GRADES if g in self.grades.get(nid, [])],
            "fields": fields, "fields_empty": empty, "standards": stds, "state_codes": state_codes,
            "pairings": self.partners(n, grade), "lessons": lessons,
            "product_refs": [{"product": r["product"], "raw_ref": r["raw_ref"], "lesson_id": r["lesson_id"]}
                             for r in self.refs.get(nid, [])],
            "links": [{"text": t, "stem_id": self.stem_by_name.get(ws(t)), "label": "as captured"}
                      for t in self.links.get(nid, [])],
            "period_hint": self.period_hint(nid, grade), "placed_elsewhere": [],
            "badges": slice_index[n["source_key"]]["badges"]}

    def compare_column(self, n, grade, dr):
        nid = n["node_id"]
        st = dr["state"]
        ph = dr["period_hint"]
        return {"source_key": n["source_key"], "node_id": nid, "node_text": dr["node_text"],
                "stem_id": n["stem_id"], "stem_name": dr["stem_name"],
                "cs_label_display": dr["cs"]["label_display"], "goal": dr["cs"]["goal"],
                "grades": self.grades.get(nid, []), "state": st, "in_grade": dr["in_grade"],
                "grade_raw": dr["ruling"]["raw_value"], "period_hint_text": ph["text"] if ph else None,
                "ccss": sorted({s["code"] for s in dr["standards"]}),
                "lessons": sorted({l["lesson_id"] for l in dr["lessons"]}),
                "state_codes": sorted({s["code"] for s in dr["state_codes"]}),
                "fields": {f["key"]: f["values"] for f in dr["fields"]}}


def empty_guardrail():
    """Contract 5.8, G0: compute([])."""
    z3 = {"deep": 0, "functional": 0, "illuminating": 0}
    return {"n": 0, "n_calibrated": 0, "n_timed": 0,
            "counts": {**z3, "unset": 0},
            "count_share": {k: None for k in z3},
            "periods": {"deep": 0.0, "functional": 0.0, "illuminating": 0.0, "unset": 0.0},
            "total_periods": 0.0, "time_share": {k: None for k in z3}, "time_coverage": None,
            "show_time_targets": False,
            "count_target": {"deep": 0.25, "functional": 0.5, "illuminating": 0.25},
            "time_target": {"deep": 0.4, "functional": 0.45, "illuminating": 0.15},
            "time_mark_min_coverage": 0.5}


def build_payload(con, grade):
    db = Db(con)
    sl = db.build_slice(grade)
    idx = {n["source_key"]: n for s in sl["super_stems"] for st in s["stems"]
           for c in st["concept_skills"] for n in c["nodes"]}
    drawers = {k: db.drawer(db.by_key[k], grade, idx) for k in idx}
    cols = {k: db.compare_column(db.by_key[k], grade, drawers[k]) for k in idx}
    gi = db.grades_info()
    return {"format": "mh2-seq-demo/1",
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "grade": grade, "source": "fixture",
            "whoami": {"user": "demo", "auth_enabled": False, "source": "demo"},
            "grades": gi, "slice": sl, "drawers": drawers, "compare_columns": cols,
            "field_defs": db.field_defs(),
            "sequence": {"grade": grade, "ladders_last_read": db.ladders_last_read, "sequence": None,
                         "modules": [], "placed_index": {}, "slice_badges": {},
                         "guardrail": empty_guardrail(), "attention": []},
            "views": []}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--grade", default="2")
    ap.add_argument("--db", default=str(config.DB))
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    p = build_payload(con, a.grade)
    Path(a.out).write_text(json.dumps(p, ensure_ascii=False, indent=None), encoding="utf-8")
    print("wrote", a.out, len(json.dumps(p)) // 1024, "KB", p["slice"]["counts"])


if __name__ == "__main__":
    main()
