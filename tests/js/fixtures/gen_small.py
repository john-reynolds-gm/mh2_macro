"""Writes tests/js/fixtures/demo_small.json: a tiny hand-specified DemoPayload
(2 stems, 3 concept/skills, 8 nodes incl. span, core, unconfirmed, off_grade,
leaf, no_grade) in the contract section 5 shapes. Deterministic; no database.

    python3 tests/js/fixtures/gen_small.py
"""
import json
from pathlib import Path

GRADE = "2"
SHORT = {"1": "G1", "2": "G2", "3": "G3", "4": "G4", "5": "G5"}


def key(n):
    return "T:" + format(abs(hash(n)) % (16 ** 12), "012x") if False else "K:" + n.replace("-", "").lower()


def badge(code, tier, label, detail, partners=None):
    return {"code": code, "tier": tier, "label": label, "detail": detail, "refs": [], "partners": partners or []}


NODES = [
    # (node_id, stem, cs, seq, text, grades, state, raw, extra)
    ("WHO-0010", "WHO", "c1", 10, "Compare whole numbers by using place value.", ["1", "2", "3", "4"], "span", "1, 2, 3, 4", {}),
    ("WHO-0011", "WHO", "c1", 11, "Order whole numbers by using place value.", ["1", "2", "3", "4"], "span", "1, 2, 3, 4", {"elsewhere": True}),
    ("WHO-0012", "WHO", "c1", 12, "Compare decimal numbers by using place value.", ["4", "5"], "off_grade", "4, 5", {}),
    ("WHO-0020", "WHO", "c2", 20, "Count to 1,000 by ones, tens and hundreds.", ["2"], "core", "G2", {}),
    ("WHO-0021", "WHO", "c2", 21, "Count with a leaf-only routine.", ["2"], "leaf", "leaf", {}),
    ("TIM-0010", "TIM", "c3", 10, "Tell and write time to the nearest five minutes.", ["2", "3"], "unconfirmed", "3 (2 for some states)", {}),
    ("TIM-0011", "TIM", "c3", 11, "Solve problems involving <b>time</b> intervals.", ["2"], "core", "G2", {}),
    ("TIM-0012", "TIM", "c3", 12, "Tell time in context (no grade stated).", [], "no_grade", None, {}),
]
CS = {
    "c1": ("WHO", "WHO:7046b922", "Compare and order numbers by using place value.",
           "Identify relationships between quantities\nCompare multiple quantities to sequence them"),
    "c2": ("WHO", "WHO:aa11bb22", "Count to 1,000.", None),
    "c3": ("TIM", "TIM:c22eed5f", "Tell and write time to the nearest minute.", "Read analog and digital clocks"),
}
STEMS = {"WHO": ("Whole Numbers and Base Ten Structure", "Number Systems and Structures"),
         "TIM": ("Time", "Measurement and Data")}
PARTNER = [{"stem_id": "COM", "stem_name": "Comparing", "codes": ["2.NBT.A.4"],
            "nodes": [{"source_key": "K:com0012", "node_id": "COM-0012", "in_grade": True}]}]
ELSEWHERE = [{"grade": "3", "sequence_id": 2, "sequence_title": "Grade 3 sequence", "module_id": 7,
              "module_title": "M1 Place value", "placement_id": 31}]
HINT_2 = {"text": "G2: Likely 2 instructional periods", "value": 2.0, "unit": "period", "qualifier": "exact",
          "low": 2.0, "high": 2.0, "basis": "grade_named", "n_estimates": 1}
# O9: an un-graded estimate on a multi-grade node fills the same number in each grade.
HINT_TIM = {"text": "Likely 1 instructional period", "value": 1.0, "unit": "period", "qualifier": "exact",
            "low": 1.0, "high": 1.0, "basis": "ungraded_multi_grade", "n_estimates": 1}
OWED = ("core", "span", "unconfirmed", "unknown")
BRIDGE = ("off_grade", "state_extension", "leaf", "no_grade")


def slice_node(n):
    nid, stem, cs, seq, text, grades, state, raw, extra = n
    ing = GRADE in grades
    b = []
    if state == "span":
        b.append(badge("span", "info", "span", "Also " + ", ".join(SHORT[g] for g in grades if g != GRADE)))
    if state == "unconfirmed":
        b.append(badge("unconfirmed", "info", "unconfirmed", "Grade ruling not final: " + raw))
    if state == "leaf":
        b.append(badge("leaf", "info", "leaf", "leaf"))
    if state == "no_grade":
        b.append(badge("no_grade", "info", "no grade", "The ladder cell has no grade"))
    if extra.get("elsewhere"):
        b.append(badge("placed_elsewhere", "info", "also G3", "Placed in G3 · M1 Place value"))
    if nid == "WHO-0010":
        b.append(badge("shared_code", "pairing", "pairs: COM", "Shares CCSS codes with Comparing (1)", PARTNER))
    hint = HINT_2 if nid == "WHO-0010" else (HINT_TIM if nid == "TIM-0010" else None)
    return {"source_key": key(nid), "node_id": nid, "seq": seq, "node_text": text, "grades": grades, "state": state,
            "in_grade": ing, "owed": state in OWED, "requires_confirm": state in BRIDGE,
            "resolution": "leaf" if state == "leaf" else ("no_grade_field" if state == "no_grade" else "ruled"),
            "grade_raw": raw, "states_mentioned": None, "badges": b,
            "placed_elsewhere": ELSEWHERE if extra.get("elsewhere") else [], "period_hint": hint}


def build():
    by_cs = {}
    for n in NODES:
        by_cs.setdefault(n[2], []).append(n)
    supers = {}
    for c, nodes in by_cs.items():
        stem, csid, label, goal = CS[c]
        sn = [slice_node(n) for n in nodes]
        cs = {"cs_id": csid, "label": label, "label_display": label, "goal": goal, "goal_missing": goal is None,
              "in_grade_count": sum(1 for x in sn if x["in_grade"]),
              "in_grade_count_excl_leaf": sum(1 for x in sn if x["in_grade"] and x["state"] != "leaf"), "nodes": sn}
        dom = STEMS[stem][1]
        supers.setdefault(dom, {}).setdefault(stem, []).append(cs)
    super_stems = []
    for dom in sorted(supers):
        stems = [{"stem_id": s, "stem_name": STEMS[s][0], "concept_skills": supers[dom][s]} for s in sorted(supers[dom], key=lambda s: STEMS[s][0].casefold())]
        super_stems.append({"domain": dom, "stems": stems})
    allnodes = [x for ss in super_stems for st in ss["stems"] for c in st["concept_skills"] for x in c["nodes"]]
    ing = [x for x in allnodes if x["in_grade"]]
    sl = {"grade": GRADE, "grade_label": "Grade 2", "kind_source": "grade_type", "ladders_last_read": "2026-09-09 15:11:11",
          "counts": {"in_grade_nodes": len(ing), "in_grade_nodes_excl_leaf": sum(1 for x in ing if x["state"] != "leaf"),
                     "owed_nodes": sum(1 for x in ing if x["owed"]), "leaf_in_grade": sum(1 for x in ing if x["state"] == "leaf"),
                     "concept_skills": 3, "concept_skills_excl_leaf": 3, "context_nodes": len(allnodes) - len(ing),
                     "stems": 2, "chips": len(allnodes)},
          "super_stems": super_stems}
    idx = {x["source_key"]: x for x in allnodes}
    drawers, cols = {}, {}
    for n in NODES:
        nid, stem, c, seq, text, grades, state, raw, extra = n
        sx = idx[key(nid)]
        stem_id, csid, label, goal = CS[c]
        strip = [{"source_key": key(m[0]), "node_id": m[0], "seq": m[3], "state": m[6], "in_grade": GRADE in m[5], "is_self": m[0] == nid} for m in by_cs[c]]
        fields = [{"key": "specifications", "label": "Specifications", "values": ["Use symbols: >, <, =", "Within 1,000"]}]
        if nid == "WHO-0010":
            fields.append({"key": "additional_notes", "label": "Additional notes", "values": ["G2: Likely 2 instructional periods"]})
        drawers[key(nid)] = {
            "grade": GRADE, "source_key": key(nid), "node_id": nid, "node_text": text, "seq": seq, "stem_id": stem_id,
            "stem_name": STEMS[stem_id][0], "domain": STEMS[stem_id][1], "source_file": "MH2_PK5_" + STEMS[stem_id][0] + ".docx",
            "state": state, "in_grade": sx["in_grade"], "owed": sx["owed"], "requires_confirm": sx["requires_confirm"],
            "cs": {"cs_id": csid, "label": label, "label_display": label, "goal": goal, "goal_missing": goal is None},
            "strip": strip,
            "ruling": {"raw_value": raw, "resolution": sx["resolution"], "ruling_type": "span" if state == "span" else state,
                       "states_mentioned": None, "notes": None, "needs_writer_review": 0},
            "grade_states": [{"grade": g, "short": SHORT[g], "state": state if g == GRADE else "span"} for g in grades],
            "fields": fields, "fields_empty": [{"key": "strategies", "label": "Strategies"}, {"key": "terminology", "label": "Terminology"}],
            "standards": [{"code": "2.NBT.A.4", "relation": "aligned", "annotation": None, "shared": nid == "WHO-0010"}] if state != "no_grade" else [],
            "state_codes": [{"code": "CA.2.NBT.4", "state": "CA", "relation": "aligned", "annotation": None}],
            "pairings": PARTNER if nid == "WHO-0010" else [],
            "lessons": [{"lesson_id": "G2-M1-L35", "product": "EM2", "raw_ref": "EM2 G2 M1 L35",
                         "shared_with": [{"stem_id": "COM", "stem_name": "Comparing", "nodes": PARTNER[0]["nodes"]}]}] if nid == "WHO-0010" else [],
            "product_refs": [{"product": "EM2", "raw_ref": "EM2 G2 M1 L35", "lesson_id": "G2-M1-L35"}] if nid == "WHO-0010" else [],
            "links": [{"text": "Time", "stem_id": "TIM", "label": "as captured"}, {"text": "Likely 1 instructional period", "stem_id": None, "label": "as captured"}] if nid == "WHO-0010" else [],
            "period_hint": sx["period_hint"], "placed_elsewhere": sx["placed_elsewhere"], "badges": sx["badges"]}
        cols[key(nid)] = {
            "source_key": key(nid), "node_id": nid, "node_text": text, "stem_id": stem_id, "stem_name": STEMS[stem_id][0],
            "cs_label_display": label, "goal": goal, "grades": grades, "state": state, "in_grade": sx["in_grade"],
            "grade_raw": raw, "period_hint_text": sx["period_hint"]["text"] if sx["period_hint"] else None,
            "ccss": ["2.NBT.A.4"] if state != "no_grade" and stem_id == "WHO" else [],
            "lessons": ["G2-M1-L35"] if nid in ("WHO-0010", "WHO-0011") else [],
            "state_codes": ["CA.2.NBT.4"], "fields": {"specifications": ["Use symbols: >, <, =", "Within 1,000"]}}
    z3 = {"deep": 0, "functional": 0, "illuminating": 0}
    gr = {"n": 0, "n_calibrated": 0, "n_timed": 0, "counts": {**z3, "unset": 0}, "count_share": {k: None for k in z3},
          "periods": {**{k: 0.0 for k in z3}, "unset": 0.0}, "total_periods": 0.0, "time_share": {k: None for k in z3},
          "time_coverage": None, "show_time_targets": False,
          "count_target": {"deep": 0.25, "functional": 0.5, "illuminating": 0.25},
          "time_target": {"deep": 0.4, "functional": 0.45, "illuminating": 0.15}, "time_mark_min_coverage": 0.5}
    grades = [{"grade": g, "label": "Grade " + g if g.isdigit() else g, "short": SHORT.get(g, g), "ord": i, "band": None,
               "in_grade_nodes": 0, "owed_nodes": 0, "chips": 0, "sequence": None} for i, g in enumerate(["1", "2", "3"])]
    grades[1].update(in_grade_nodes=len(ing), owed_nodes=sl["counts"]["owed_nodes"], chips=len(allnodes))
    return {"format": "mh2-seq-demo/1", "generated_at": "2026-09-30T12:00:00+00:00", "grade": GRADE, "source": "fixture",
            "whoami": {"user": "demo", "auth_enabled": False, "source": "demo"},
            "grades": {"grades": grades, "kind_source": "grade_type", "ladders_last_read": "2026-09-09 15:11:11"},
            "slice": sl, "drawers": drawers, "compare_columns": cols,
            "field_defs": [{"key": "specifications", "label": "Specifications"}, {"key": "strategies", "label": "Strategies"},
                           {"key": "additional_notes", "label": "Additional notes"}],
            "sequence": {"grade": GRADE, "ladders_last_read": "2026-09-09 15:11:11", "sequence": None, "modules": [],
                         "placed_index": {}, "slice_badges": {}, "guardrail": gr, "attention": []},
            "views": []}


if __name__ == "__main__":
    out = Path(__file__).with_name("demo_small.json")
    out.write_text(json.dumps(build(), indent=1, ensure_ascii=False), encoding="utf-8")
    print("wrote", out)
