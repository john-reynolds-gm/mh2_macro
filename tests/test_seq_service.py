"""seq_service tests (contract §8.2): end to end on temp copies.

Implementer R's mh2.seq_read / mh2.grade_kind may not exist yet.  When they
are not importable a small FAKE stand-in (defined below, injected into
sys.modules only for this test process) answers with contract-shaped data
read from the same mh2.db tables.  Once R's real modules exist they are used
instead and the same assertions run against them (integration)."""
import difflib
import hashlib
import importlib.util
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import types
from pathlib import Path


# Integrator: temp dirs (real-DB copies are ~150 MB each) are removed at exit.
import atexit as _atexit  # noqa: E402
_TMPDIRS = []


def _mkdtemp(**kw):
    d = tempfile.mkdtemp(**kw)
    _TMPDIRS.append(d)
    return d


_atexit.register(lambda: [shutil.rmtree(d, ignore_errors=True) for d in _TMPDIRS])

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

# ------------------------------------------------------------------ fake R


def _build_fake():
    OWED = ("core", "span", "unconfirmed", "unknown")
    BRIDGE = ("off_grade", "state_extension", "leaf", "no_grade")
    MARKER_RE = re.compile(r"\s*\(\s*[A-Z][a-z]+\s*[-–—]?\s*done\s*\)\s*$", re.I)

    sr = types.ModuleType("mh2.seq_read")
    gk = types.ModuleType("mh2.grade_kind")
    sr.__fake__ = True

    def connect_ro(path):
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con

    def normalize_grade(con, token):
        t = str(token).strip().lower().replace("grade", "").replace("g", "").strip()
        t = {"k": "K", "pk": "PK", "a1": "A1"}.get(t, t)
        if t not in ("PK", "K", "1", "2", "3", "4", "5", "6", "7", "8", "A1"):
            raise ValueError(f"unknown grade {token!r}")
        return t

    def display_label(label):
        return " ".join(MARKER_RE.sub("", label or "").split())

    def ladders_last_read(con):
        return con.execute("SELECT MAX(ts) FROM ingest_log").fetchone()[0]

    def _kind(con, node_id, grade):
        r = con.execute("SELECT resolution FROM node_grade_ruling WHERE node_id=?",
                        (node_id,)).fetchone()
        if r and r[0] == "leaf":
            return "leaf"
        if r and r[0] == "no_grade_field":
            return "no_grade"
        if con.execute("SELECT 1 FROM node_grade WHERE node_id=? AND grade=?",
                       (node_id, grade)).fetchone():
            k = con.execute("SELECT kind FROM node_grade_kind WHERE node_id=? AND grade=?",
                            (node_id, grade)).fetchone()
            return k[0] if k else "unknown"
        return "off_grade"

    def _hint(con, node_id):
        for (v,) in con.execute("SELECT value FROM node_fields WHERE node_id=? AND"
                                " field='additional_notes' ORDER BY ordinal", (node_id,)):
            m = re.search(r"G(\w+): (?:Likely )?(\d+(?:\.\d+)?) instructional periods?", v)
            if m:
                return {"text": v, "value": float(m.group(2)), "unit": "period",
                        "qualifier": "exact", "low": float(m.group(2)),
                        "high": float(m.group(2)), "basis": "grade_named", "n_estimates": 1}
        return None

    def node_facts(con, grade, source_keys=None):
        out = {}
        q = ("SELECT n.*, s.name AS stem_name FROM nodes n"
             " LEFT JOIN stems s ON s.stem_id = n.stem_id")
        rows = con.execute(q).fetchall()
        want = None if source_keys is None else set(source_keys)
        for r in rows:
            if want is not None and r["source_key"] not in want:
                continue
            state = _kind(con, r["node_id"], grade)
            out[r["source_key"]] = {
                "source_key": r["source_key"], "node_id": r["node_id"],
                "node_text": r["node_text"], "stem_id": r["stem_id"],
                "stem_name": r["stem_name"], "concept_skill": r["concept_skill"],
                "concept_skill_display": display_label(r["concept_skill"]),
                "cs_id": f"{r['stem_id']}:" + hashlib.sha1(
                    (r["concept_skill"] or "").encode()).hexdigest()[:8],
                "seq": r["seq"], "source_file": r["source_file"], "state": state,
                "in_grade": state in ("core", "span", "unconfirmed", "unknown", "leaf")
                and state != "leaf" or state == "leaf" and bool(con.execute(
                    "SELECT 1 FROM node_grade WHERE node_id=? AND grade=?",
                    (r["node_id"], grade)).fetchone()),
                "owed": state in OWED, "requires_confirm": state in BRIDGE,
                "predecessor_keys": [], "period_hint": _hint(con, r["node_id"])}
        for k, f in out.items():
            preds = con.execute(
                "SELECT source_key, node_id FROM nodes WHERE stem_id=? AND"
                " concept_skill IS ? AND seq < ? ORDER BY seq",
                (f["stem_id"], f["concept_skill"], f["seq"])).fetchall()
            f["predecessor_keys"] = [p["source_key"] for p in preds
                                     if _kind(con, p["node_id"], grade) in OWED]
        return out

    def ordering_badges(positions, facts):
        out = {}
        for p, pos in positions.items():
            f = facts.get(p)
            if not f:
                continue
            before, unplaced = [], []
            for q in f["predecessor_keys"]:
                if q in positions:
                    if positions[q] > pos:
                        before.append(q)
                else:
                    unplaced.append(q)
            bl = []

            def nid(k):
                return facts[k]["node_id"] if k in facts else k
            if before:
                q = before[0]
                bl.append({"code": "before_predecessor", "tier": "structural",
                           "label": f"before {nid(q)}",
                           "detail": f"Placed before its ladder predecessor {nid(q)} "
                                     f"(M{positions[q][0]} · slot {positions[q][1]}). Warning only.",
                           "refs": before, "partners": []})
            if unplaced:
                q = unplaced[0]
                bl.append({"code": "predecessor_unplaced", "tier": "info",
                           "label": f"{nid(q)} unplaced",
                           "detail": f"Its ladder predecessor {nid(q)} is not placed in this sequence",
                           "refs": unplaced, "partners": []})
            if bl:
                out[p] = bl
        return out

    def suggest_successors(con, *, source_key_seen, node_text_seen, stem_id_seen,
                           concept_skill_seen, exclude_keys):
        suffix = source_key_seen.split(":", 1)[1]
        rows = con.execute("SELECT n.*, s.name AS stem_name FROM nodes n LEFT JOIN stems s"
                           " ON s.stem_id=n.stem_id ORDER BY n.seq").fetchall()
        res, seen = [], set()

        def add(r, reason, ratio):
            if r["source_key"] in exclude_keys or r["source_key"] in seen or len(res) >= 3:
                return
            seen.add(r["source_key"])
            res.append({"source_key": r["source_key"], "node_id": r["node_id"],
                        "node_text": r["node_text"], "stem_id": r["stem_id"],
                        "stem_name": r["stem_name"],
                        "concept_skill_display": display_label(r["concept_skill"]),
                        "reason": reason, "ratio": ratio})
        for r in rows:
            if r["source_key"].split(":", 1)[1] == suffix and r["source_key"] != source_key_seen:
                add(r, "same_text_other_stem", 1.0)

        def ratio(r):
            return round(difflib.SequenceMatcher(None, r["node_text"].casefold(),
                                                 node_text_seen.casefold()).ratio() + 1e-9, 3)
        cand = [(ratio(r), r) for r in rows if r["stem_id"] == stem_id_seen]
        for ra, r in sorted((c for c in cand if display_label(c[1]["concept_skill"]) ==
                             display_label(concept_skill_seen) and c[0] >= 0.6),
                            key=lambda c: -c[0]):
            add(r, "same_cs_similar", ra)
        for ra, r in sorted((c for c in cand if c[0] >= 0.8), key=lambda c: -c[0]):
            add(r, "same_stem_similar", ra)
        return res

    def list_grades(con):
        out = []
        for r in con.execute("SELECT grade, ord, band FROM grade_order ORDER BY ord"):
            n = con.execute("SELECT COUNT(*) FROM node_grade WHERE grade=?", (r["grade"],)).fetchone()[0]
            out.append({"grade": r["grade"], "label": r["grade"], "short": r["grade"],
                        "ord": r["ord"], "band": r["band"], "in_grade_nodes": n,
                        "owed_nodes": n, "chips": n, "sequence": None})
        return out

    def build_slice(con, grade, overlay=None):
        overlay = overlay or {}
        nodes = []
        for k, f in node_facts(con, grade).items():
            if f["in_grade"]:
                nodes.append({"source_key": k, "node_id": f["node_id"], "state": f["state"],
                              "placed_elsewhere": overlay.get(k, []),
                              "badges": ([{"code": "placed_elsewhere", "tier": "info",
                                           "label": "also", "detail": None, "refs": [],
                                           "partners": []}] if overlay.get(k) else [])})
        return {"grade": grade, "kind_source": "grade_type", "nodes": nodes,
                "ladders_last_read": ladders_last_read(con)}

    def node_drawer(con, source_key, grade, overlay=None):
        f = node_facts(con, grade, [source_key]).get(source_key)
        if f is None:
            raise KeyError(source_key)
        return {"grade": grade, "source_key": source_key, "node_id": f["node_id"],
                "placed_elsewhere": (overlay or {}).get(source_key, [])}

    def build_compare(con, keys, grade):
        if not keys or len(keys) > 4 or len(set(keys)) != len(keys):
            raise ValueError("compare takes 1 to 4 distinct keys")
        facts = node_facts(con, grade, keys)
        for k in keys:
            if k not in facts:
                raise KeyError(k)
        return {"grade": grade, "columns": [facts[k] for k in keys]}

    for name, fn in list(locals().items()):
        if name in ("connect_ro", "normalize_grade", "display_label", "ladders_last_read",
                    "node_facts", "ordering_badges", "suggest_successors", "list_grades",
                    "build_slice", "node_drawer", "build_compare"):
            setattr(sr, name, fn)
    gk.kind_source = lambda con: "grade_type"
    return sr, gk


def ensure_read_modules():
    """'real' when R's modules are importable, else inject the fake and return 'fake'."""
    if importlib.util.find_spec("mh2.seq_read") is not None and \
            importlib.util.find_spec("mh2.grade_kind") is not None:
        return "real"
    if "mh2.seq_read" not in sys.modules or not getattr(sys.modules["mh2.seq_read"], "__fake__", False):
        import mh2
        sr, gk = _build_fake()
        sys.modules["mh2.seq_read"] = sr
        sys.modules["mh2.grade_kind"] = gk
        mh2.seq_read, mh2.grade_kind = sr, gk
    return "fake"


READ_MODE = ensure_read_modules()

from mh2 import seq_service as V  # noqa: E402
from mh2 import seq_store as S  # noqa: E402

# --------------------------------------------------------------- test data

N = {  # node_id -> (stem, seq, text, concept_skill, grades, kind per grade)
    "WHO-0010": ("WHO", 10, "Compare whole numbers by using place value.",
                 "Compare and order numbers by using place value.", {"1": "span", "2": "span", "3": "span", "4": "span"}),
    "WHO-0011": ("WHO", 11, "Order whole numbers by using place value.",
                 "Compare and order numbers by using place value.", {"1": "span", "2": "span", "3": "span", "4": "span"}),
    "WHO-0012": ("WHO", 12, "Compare decimal numbers by using place value.",
                 "Compare and order numbers by using place value.", {"4": "core", "5": "core"}),
    "COM-0012": ("COM", 12, "Compare multi-digit numbers using place value relationships.",
                 "Compare and order multi-digit numbers by using a number line. (Amy done)", {"2": "core"}),
    "COM-0013": ("COM", 13, "Order multi-digit numbers on a number line.",
                 "Compare and order multi-digit numbers by using a number line. (Amy done)", {"2": "core"}),
    "TIM-0010": ("TIM", 10, "Tell and write time to the nearest minute.",
                 "Tell and write time to the nearest minute.", {"2": "unconfirmed", "3": "unconfirmed"}),
    "NEW-0001": ("TIM", 11, "Tell time with a number line.",
                 "Tell and write time to the nearest minute.", {"2": None}),   # None = no kind row -> unknown
    "WHO-0020": ("WHO", 20, "Round whole numbers to the nearest ten.",
                 "Round.", {"3": "core"}),
}


def skey(text, stem):
    norm = re.sub(r"[^a-z0-9]", "", text.lower())
    return f"{stem}:" + hashlib.sha1(norm.encode()).hexdigest()[:16]


K = {nid: skey(v[2], v[0]) for nid, v in N.items()}


def make_mh2(dirpath):
    path = Path(dirpath) / "mh2.db"
    con = sqlite3.connect(path)
    con.executescript(config.SCHEMA.read_text())
    for g, o in zip(("PK", "K", "1", "2", "3", "4", "5", "6", "7", "8", "A1"), range(11)):
        con.execute("INSERT OR IGNORE INTO grade_order (grade, ord, band) VALUES (?,?,?)", (g, o, None))
    for sid, name, dom in (("WHO", "Whole Numbers and Base Ten Structure", "Number Systems and Structures"),
                           ("COM", "Comparing", "Number Systems and Structures"),
                           ("TIM", "Time", "Measurement and Data")):
        con.execute("INSERT INTO stems (stem_id, name, domain) VALUES (?,?,?)", (sid, name, dom))
    for nid, (stem, seq, text, cs, grades) in N.items():
        con.execute("INSERT INTO nodes (node_id, stem_id, seq, source_key, node_text, concept_skill,"
                    " source_file) VALUES (?,?,?,?,?,?,?)",
                    (nid, stem, seq, K[nid], text, cs, f"MH2_{stem}.docx"))
        con.execute("INSERT INTO node_grade_ruling (node_id, raw_value, resolution, is_leaf)"
                    " VALUES (?,?,?,0)", (nid, ", ".join(grades), "ruled"))
        for g, kind in grades.items():
            con.execute("INSERT INTO node_grade (node_id, grade) VALUES (?,?)", (nid, g))
            if kind:
                con.execute("INSERT INTO node_grade_kind (node_id, grade, kind, basis) VALUES (?,?,?,?)",
                            (nid, g, kind, "test"))
    con.execute("INSERT INTO node_fields (node_id, field, ordinal, value) VALUES"
                " ('WHO-0010','additional_notes',0,'G2: Likely 2 instructional periods (heavy fluency practice)')")
    con.execute("INSERT INTO ingest_log (run_id, ts, source_file, action) VALUES ('r','2026-09-09 15:11:11','x','ok')")
    con.commit()
    con.close()
    return path


def make_env():
    d = _mkdtemp(prefix="seqsvc_")
    mh2 = make_mh2(d)
    return V.Ctx(mh2, Path(d) / "mh2_seq.db"), mh2


def mh2_edit(path, sql, params=()):
    con = sqlite3.connect(path)
    con.execute(sql, params)
    con.commit()
    con.close()


def expect(exc, fn, code=None):
    try:
        fn()
    except exc as e:
        if code is not None:
            assert e.code == code, (e.code, code)
        return e
    raise AssertionError(f"expected {exc.__name__} {code}")


def rev(res):
    return res["sequence"]["sequence"]["rev"]


def build_5_6(ctx, writer="amy"):
    """The §5.6 script: M1 holds WHO-0011; M2 has a co-placed slot WHO-0010 +
    COM-0012, then a bridge WHO-0012."""
    r = V.create_sequence(ctx, writer, "2", "Grade 2 sequence", None)
    sid = r["result"]["sequence_id"]
    r = V.create_module(ctx, writer, sid, rev(r), "M1 Place value", None)
    m1 = r["result"]["module_id"]
    r = V.create_module(ctx, writer, sid, rev(r), "M2 Comparing numbers", None)
    m2 = r["result"]["module_id"]
    r = V.place(ctx, writer, sid, rev(r), m1, K["WHO-0011"])
    p_11 = r["result"]
    r = V.place(ctx, writer, sid, rev(r), m2, K["WHO-0010"])
    p_10 = r["result"]
    r = V.place(ctx, writer, sid, rev(r), m2, K["COM-0012"], slot_id=p_10["slot_id"])
    p_com = r["result"]
    r = V.place(ctx, writer, sid, rev(r), m2, K["WHO-0012"], confirm_off_grade=True)
    p_12 = r["result"]
    return dict(sid=sid, m1=m1, m2=m2, p_11=p_11, p_10=p_10, p_com=p_com, p_12=p_12, view=r["sequence"])


# ------------------------------------------------------------------- tests

def test_end_to_end_5_6():
    ctx, _ = make_env()
    e = build_5_6(ctx)
    v = e["view"]
    assert v["grade"] == "2" and v["ladders_last_read"] == "2026-09-09 15:11:11"
    assert v["sequence"]["owner"] == "amy" and v["sequence"]["created_by"] == "amy"
    assert [m["title"] for m in v["modules"]] == ["M1 Place value", "M2 Comparing numbers"]
    assert [s["position"] for s in v["modules"][1]["slots"]] == [1, 2]
    assert [p["node_id"] for p in v["modules"][1]["slots"][0]["placements"]] == ["WHO-0010", "COM-0012"]
    # placed_index positions
    pi = v["placed_index"]
    assert pi[K["WHO-0011"]] == {"placement_id": e["p_11"]["placement_id"], "module_id": e["m1"],
                                 "module_title": "M1 Place value", "module_position": 1,
                                 "slot_id": e["p_11"]["slot_id"], "slot_position": 1,
                                 "status": "ok", "is_bridge": False}
    assert (pi[K["WHO-0010"]]["module_position"], pi[K["WHO-0010"]]["slot_position"]) == (2, 1)
    assert pi[K["COM-0012"]]["slot_id"] == pi[K["WHO-0010"]]["slot_id"]
    assert (pi[K["WHO-0012"]]["module_position"], pi[K["WHO-0012"]]["slot_position"]) == (2, 2)
    assert pi[K["WHO-0012"]]["is_bridge"] is True
    # before_predecessor on WHO-0011 (its predecessor WHO-0010 sits later)
    pl11 = v["modules"][0]["slots"][0]["placements"][0]
    codes = [b["code"] for b in pl11["badges"]]
    assert "before_predecessor" in codes, codes
    bp = [b for b in pl11["badges"] if b["code"] == "before_predecessor"][0]
    assert bp["tier"] == "structural" and bp["refs"] == [K["WHO-0010"]]
    assert bp["label"] == "before WHO-0010"
    assert K["WHO-0011"] in v["slice_badges"]
    assert {b["code"] for b in v["slice_badges"][K["WHO-0011"]]} == {"before_predecessor"}
    # co-placed nodes never produce an ordering badge; WHO-0010 has no predecessor; COM-0012 has none
    pl10 = v["modules"][1]["slots"][0]["placements"][0]
    assert pl10["badges"] == [] and pl10["status"] == "ok"
    # bridge badge
    pl12 = v["modules"][1]["slots"][1]["placements"][0]
    assert pl12["is_bridge"] is True and pl12["grade_kind_seen"] == "off_grade"
    b = [x for x in pl12["badges"] if x["code"] == "bridge"][0]
    assert b["label"] == "bridge · off_grade" and b["tier"] == "structural"
    assert b["detail"] == "Placed outside this grade's inventory on purpose"
    assert K["WHO-0012"] in v["slice_badges"]
    # PlacementView key set is complete and stable
    assert set(pl11) == {
        "placement_id", "rev", "order_in_slot", "source_key", "node_id", "node_id_seen", "node_text",
        "node_text_seen", "stem_id", "stem_name", "concept_skill_display", "ladder_file_seen",
        "grade_kind_seen", "state_now", "status", "relabelled", "relabel", "is_bridge", "calibration",
        "period_estimate", "period_hint", "period_hint_seen", "differentiation_note", "placed_by",
        "placed_at", "updated_by", "updated_at", "placed_elsewhere", "badges"}
    assert set(v) == {"grade", "ladders_last_read", "sequence", "modules", "placed_index",
                      "slice_badges", "guardrail", "attention"}
    assert set(v["modules"][0]) == {"module_id", "title", "note", "order_key", "position",
                                    "guardrail", "slots"}
    assert v["attention"] == []
    # whole flow is JSON-able and the same view comes back from get_sequence
    json.dumps(v)
    assert V.get_sequence(ctx, "2") == v
    assert V.get_sequence(ctx, "g2") == v          # grade token normalised


def test_attributes_guardrail_and_period_hint_seen():
    ctx, _ = make_env()
    e = build_5_6(ctx)
    pid10 = e["p_10"]["placement_id"]
    res = V.update_placement(ctx, "bob", pid10, 1, {"calibration": "deep", "period_estimate": 1.5})
    assert res["result"] == {"placement_id": pid10}
    before_rev = rev(res)
    pl = res["sequence"]["modules"][1]["slots"][0]["placements"][0]
    assert pl["calibration"] == "deep" and pl["period_estimate"] == 1.5 and pl["updated_by"] == "bob"
    assert pl["rev"] == 2
    assert pl["period_hint_seen"] == "G2: Likely 2 instructional periods (heavy fluency practice)"
    assert pl["period_hint"]["value"] == 2.0
    # COM-0012: calibrate + time; WHO-0011: functional no time; bridge: illuminating
    pc = e["p_com"]["placement_id"]
    V.update_placement(ctx, "bob", pc, 1, {"calibration": "deep", "period_estimate": 0.5})
    V.update_placement(ctx, "bob", e["p_11"]["placement_id"], 1, {"calibration": "functional"})
    res = V.update_placement(ctx, "bob", e["p_12"]["placement_id"], 1, {"calibration": "illuminating"})
    assert rev(res) == before_rev           # attribute writes never bump the sequence rev
    from mh2 import seq_guardrail
    v = V.get_sequence(ctx, "2")
    flat = [p for m in v["modules"] for s in m["slots"] for p in s["placements"]]
    want = seq_guardrail.compute([{"calibration": p["calibration"], "period_estimate": p["period_estimate"]}
                                  for p in flat])
    assert v["guardrail"] == want
    assert want["counts"] == {"deep": 2, "functional": 1, "illuminating": 1, "unset": 0}
    assert want["n_timed"] == 2 and want["time_coverage"] == 0.5 and want["show_time_targets"] is True
    g = V.get_guardrail(ctx, "2")
    assert g["sequence"] == want and g["sequence_id"] == e["sid"]
    m2 = [m for m in v["modules"] if m["module_id"] == e["m2"]][0]
    assert [x for x in g["modules"] if x["module_id"] == e["m2"]][0]["guardrail"] == m2["guardrail"]
    assert m2["guardrail"]["n"] == 3
    # clearing
    res = V.update_placement(ctx, "bob", pid10, 2, {"period_estimate": None, "calibration": None})
    pl = res["sequence"]["modules"][1]["slots"][0]["placements"][0]
    assert pl["period_estimate"] is None and pl["calibration"] is None


def test_off_grade_422_then_confirm():
    ctx, _ = make_env()
    r = V.create_sequence(ctx, "amy", "2", "G2", None)
    sid = r["result"]["sequence_id"]
    r = V.create_module(ctx, "amy", sid, rev(r), "M1")
    m1 = r["result"]["module_id"]
    e = expect(S.NeedsConfirm, lambda: V.place(ctx, "amy", sid, rev(r), m1, K["WHO-0012"]))
    assert e.status == 422 and e.extra == {"source_keys": [K["WHO-0012"]],
                                           "states": {K["WHO-0012"]: "off_grade"}}
    r2 = V.place(ctx, "amy", sid, rev(r), m1, K["WHO-0012"], confirm_off_grade=True)
    assert r2["sequence"]["modules"][0]["slots"][0]["placements"][0]["is_bridge"] is True
    # group: the offender list covers every node that needs a confirm
    e = expect(S.NeedsConfirm, lambda: V.create_slot_group(
        ctx, "amy", sid, rev(r2), m1, [K["WHO-0020"], K["COM-0012"]], False, None))
    assert e.extra["source_keys"] == [K["WHO-0020"]]
    # 1..4 keys
    expect(S.Invalid, lambda: V.create_slot_group(ctx, "amy", sid, rev(r2), m1, [], False, None), "invalid")
    expect(S.Invalid, lambda: V.create_slot_group(ctx, "amy", sid, rev(r2), m1, list(K.values())[:5],
                                                  False, None), "invalid")


def test_unknown_node_and_unknown_grade():
    ctx, _ = make_env()
    r = V.create_sequence(ctx, "amy", "2", "G2", None)
    sid = r["result"]["sequence_id"]
    r = V.create_module(ctx, "amy", sid, rev(r), "M1")
    expect(S.NotFound, lambda: V.place(ctx, "amy", sid, rev(r), r["result"]["module_id"], "WHO:nope"),
           "not_found")
    expect(S.NotFound, lambda: V.get_sequence(ctx, "13"))
    expect(S.NotFound, lambda: V.get_slice(ctx, "zz"))
    expect(S.NotFound, lambda: V.get_node(ctx, "WHO:nope", "2"))
    expect(S.NotFound, lambda: V.get_compare(ctx, ["WHO:nope"], "2"))
    expect(S.Invalid, lambda: V.get_compare(ctx, [], "2"), "invalid")
    expect(S.Invalid, lambda: V.get_compare(ctx, list(K.values())[:5], "2"), "invalid")
    expect(S.NotFound, lambda: V.get_events(ctx, 999, 10, None))
    expect(S.Invalid, lambda: V.get_events(ctx, sid, 0, None), "invalid")
    expect(S.NotFound, lambda: V.create_sequence(ctx, "amy", "13", "x", None))
    expect(S.NotFound, lambda: V.update_module(ctx, "amy", 9999, 1, {"title": "x"}))


def test_stale_rev_carries_current_sequence_view():
    ctx, _ = make_env()
    e = build_5_6(ctx)
    cur = rev(V.get_sequence(ctx, "2") and {"sequence": V.get_sequence(ctx, "2")})
    err = expect(S.StaleRevision, lambda: V.create_module(ctx, "bob", e["sid"], cur - 1, "late"))
    assert err.status == 409 and err.code == "stale_revision"
    assert err.extra["current_rev"] == cur
    assert err.extra["sequence"]["sequence"]["rev"] == cur
    assert err.extra["sequence"] == V.get_sequence(ctx, "2")
    json.dumps(err.extra)
    # attribute write: stale PLACEMENT rev also carries the sequence view
    err = expect(S.StaleRevision, lambda: V.update_placement(
        ctx, "bob", e["p_10"]["placement_id"], 99, {"calibration": "deep"}))
    assert err.extra["current_rev"] == 1 and err.extra["sequence"]["sequence"]["rev"] == cur
    # every structural service write carries it
    pid = e["p_10"]["placement_id"]
    sid = e["sid"]
    for fn in (lambda r: V.update_sequence(ctx, "b", sid, r, {"title": "zz"}),
               lambda r: V.update_module(ctx, "b", e["m1"], r, {"title": "zz"}),
               lambda r: V.move_module(ctx, "b", e["m1"], r, "down"),
               lambda r: V.remove_module(ctx, "b", e["m1"], r),
               lambda r: V.update_slot(ctx, "b", e["p_10"]["slot_id"], r, "x"),
               lambda r: V.move_slot(ctx, "b", e["p_10"]["slot_id"], r, "down", None),
               lambda r: V.merge_slot(ctx, "b", e["p_10"]["slot_id"], r, e["p_11"]["slot_id"]),
               lambda r: V.create_slot_group(ctx, "b", sid, r, e["m1"], [K["COM-0013"]], False, None),
               lambda r: V.place(ctx, "b", sid, r, e["m1"], K["COM-0013"]),
               lambda r: V.co_place(ctx, "b", pid, r, e["p_11"]["slot_id"]),
               lambda r: V.ungroup(ctx, "b", pid, r),
               lambda r: V.remove_placement(ctx, "b", pid, r, None),
               lambda r: V.reattach(ctx, "b", pid, r, K["COM-0013"]),
               lambda r: V.acknowledge(ctx, "b", pid, r)):
        err = expect(S.StaleRevision, lambda: fn(cur - 1))
        assert err.extra["sequence"]["sequence"]["rev"] == cur


def test_reword_orphans_then_reattach_preserves_identity():
    ctx, mh2 = make_env()
    e = build_5_6(ctx)
    pid = e["p_11"]["placement_id"]
    V.update_placement(ctx, "amy", pid, 1, {"calibration": "deep", "period_estimate": 1.0})
    # simulate a reword in Word: new text -> new source_key (same node_id seq), old key gone
    new_text = "Order whole numbers using place value."
    new_key = skey(new_text, "WHO")
    mh2_edit(mh2, "UPDATE nodes SET node_text=?, source_key=? WHERE node_id='WHO-0011'", (new_text, new_key))
    v = V.get_sequence(ctx, "2")
    pl = v["modules"][0]["slots"][0]["placements"][0]
    assert pl["status"] == "orphaned" and pl["state_now"] is None and pl["node_id"] is None
    assert pl["node_text"] == pl["node_text_seen"] == N["WHO-0011"][2]
    assert pl["node_id_seen"] == "WHO-0011" and pl["relabelled"] is False
    assert pl["badges"][0]["code"] == "orphaned"
    assert pl["badges"][0]["detail"] == ("Node no longer in the ladder as placed; was: "
                                         f"{N['WHO-0011'][2]} (MH2_WHO.docx)")
    assert pl["is_bridge"] is False
    # position retained; still in placed_index
    assert v["placed_index"][K["WHO-0011"]]["status"] == "orphaned"
    assert len(v["attention"]) == 1
    a = v["attention"][0]
    assert a["actions"] == ["reattach", "remove"] and a["status"] == "orphaned"
    assert a["module_position"] == 1 and a["slot_position"] == 1 and a["rev"] == pl["rev"]
    assert a["grade_kind_seen"] == "span" and a["state_now"] is None and a["stem_id_seen"] == "WHO"
    assert a["suggestions"], "expected a successor suggestion"
    assert a["suggestions"][0]["source_key"] == new_key
    assert a["suggestions"][0]["reason"] == "same_cs_similar" and a["suggestions"][0]["ratio"] >= 0.9
    assert V.get_attention(ctx, "2")["items"] == v["attention"]
    assert V.get_attention(ctx, "2")["sequence_id"] == e["sid"]
    # reattach to the suggestion: same placement row, calibration kept, attention cleared
    before_rev = v["sequence"]["rev"]
    r = V.reattach(ctx, "amy", pid, before_rev, new_key)
    assert r["result"] == {"placement_id": pid}
    pl = r["sequence"]["modules"][0]["slots"][0]["placements"][0]
    assert pl["placement_id"] == pid and pl["status"] == "ok" and pl["source_key"] == new_key
    assert pl["calibration"] == "deep" and pl["period_estimate"] == 1.0
    assert pl["node_text_seen"] == new_text and pl["node_id"] == "WHO-0011"
    assert r["sequence"]["attention"] == []
    assert S.get_placement(S.connect(ctx.seq_path), pid)["reattached_from"] == K["WHO-0011"]
    # reattach onto an already-placed key is refused
    err = expect(S.Conflict, lambda: V.reattach(ctx, "amy", pid, rev(r), K["WHO-0010"]),
                 "reattach_target_placed")
    assert err.status == 409


def test_deleted_node_orphans_without_suggestion():
    ctx, mh2 = make_env()
    e = build_5_6(ctx)
    con = sqlite3.connect(mh2)
    con.execute("PRAGMA foreign_keys=OFF")
    con.execute("DELETE FROM nodes WHERE node_id='WHO-0010'")
    con.execute("DELETE FROM node_grade WHERE node_id='WHO-0010'")
    con.commit()
    con.close()
    v = V.get_sequence(ctx, "2")
    att = {a["source_key"]: a for a in v["attention"]}
    assert att[K["WHO-0010"]]["status"] == "orphaned"
    # every same-stem sibling is already placed in this sequence, so: no suggestion ("deleted?")
    assert att[K["WHO-0010"]]["suggestions"] == []
    # remove the orphan: attention clears; the co-placed partner is unaffected
    r = V.remove_placement(ctx, "amy", e["p_10"]["placement_id"], v["sequence"]["rev"], "deleted in Word")
    assert [a["source_key"] for a in r["sequence"]["attention"]] == []
    left = r["sequence"]["modules"][1]["slots"][0]["placements"]
    assert [p["node_id"] for p in left] == ["COM-0012"]
    # orphans never count towards predecessor ordering: WHO-0011 is now just "predecessor gone"
    codes = [b["code"] for b in r["sequence"]["modules"][0]["slots"][0]["placements"][0]["badges"]]
    assert "before_predecessor" not in codes


def test_grade_change_states_and_exemptions():
    ctx, mh2 = make_env()
    e = build_5_6(ctx)
    pid11 = e["p_11"]["placement_id"]
    # core/span -> off-grade: WHO-0011 loses G2
    mh2_edit(mh2, "DELETE FROM node_grade WHERE node_id='WHO-0011' AND grade='2'")
    v = V.get_sequence(ctx, "2")
    pl = v["modules"][0]["slots"][0]["placements"][0]
    assert pl["status"] == "grade_changed" and pl["state_now"] == "off_grade"
    b = [x for x in pl["badges"] if x["code"] == "grade_changed"][0]
    assert b["label"] == "was span → now off_grade" and b["tier"] == "structural"
    assert b["detail"] == "The grade ruling changed since this was placed"
    assert pl["is_bridge"] is True and pl["badges"][1]["code"] == "bridge"
    assert v["attention"][0]["actions"] == ["acknowledge", "remove"]
    assert v["attention"][0]["suggestions"] == []
    assert K["WHO-0011"] in v["slice_badges"]
    # acknowledge refreshes grade_kind_seen -> now a deliberate bridge
    r = V.acknowledge(ctx, "amy", pid11, v["sequence"]["rev"])
    pl = r["sequence"]["modules"][0]["slots"][0]["placements"][0]
    assert pl["status"] == "ok" and pl["grade_kind_seen"] == "off_grade" and pl["is_bridge"] is True
    assert r["sequence"]["attention"] == []
    # unconfirmed placement: -> core is silent, -> state_extension raises
    r = V.place(ctx, "amy", e["sid"], rev(r), e["m1"], K["TIM-0010"])
    assert r["sequence"]["placed_index"][K["TIM-0010"]]["status"] == "ok"
    mh2_edit(mh2, "UPDATE node_grade_kind SET kind='core' WHERE node_id='TIM-0010' AND grade='2'")
    assert V.get_sequence(ctx, "2")["placed_index"][K["TIM-0010"]]["status"] == "ok"
    mh2_edit(mh2, "UPDATE node_grade_kind SET kind='state_extension' WHERE node_id='TIM-0010' AND grade='2'")
    v = V.get_sequence(ctx, "2")
    assert v["placed_index"][K["TIM-0010"]]["status"] == "grade_changed"
    assert v["placed_index"][K["TIM-0010"]]["is_bridge"] is True
    # unknown -> core is silent
    r = V.place(ctx, "amy", e["sid"], v["sequence"]["rev"], e["m1"], K["NEW-0001"])
    pl = [p for s in r["sequence"]["modules"][0]["slots"] for p in s["placements"]
          if p["source_key"] == K["NEW-0001"]][0]
    assert pl["grade_kind_seen"] == "unknown" and pl["status"] == "ok"
    mh2_edit(mh2, "INSERT INTO node_grade_kind (node_id, grade, kind, basis) VALUES ('NEW-0001','2','core','t')")
    assert V.get_sequence(ctx, "2")["placed_index"][K["NEW-0001"]]["status"] == "ok"


def test_relabelled_is_info_not_attention():
    ctx, mh2 = make_env()
    e = build_5_6(ctx)
    mh2_edit(mh2, "UPDATE nodes SET concept_skill='Compare numbers.' WHERE node_id='WHO-0010'")
    mh2_edit(mh2, "UPDATE nodes SET source_file='MH2_WHO_renamed.docx' WHERE node_id='COM-0012'")
    v = V.get_sequence(ctx, "2")
    pls = {p["node_id"]: p for m in v["modules"] for s in m["slots"] for p in s["placements"]}
    a = pls["WHO-0010"]
    assert a["status"] == "ok" and a["relabelled"] is True
    assert a["relabel"]["concept_skill_seen"] == "Compare and order numbers by using place value."
    assert a["relabel"]["concept_skill_now"] == "Compare numbers."
    b = [x for x in a["badges"] if x["code"] == "relabelled"][0]
    assert b["tier"] == "info" and b["label"] == "heading changed"
    assert b["detail"] == 'Concept/skill was "Compare and order numbers by using place value."'
    c = pls["COM-0012"]
    assert c["relabelled"] is True
    assert [x for x in c["badges"] if x["code"] == "relabelled"][0]["detail"] == "Ladder file was MH2_COM.docx"
    assert v["attention"] == []
    # acknowledge refreshes the snapshots
    r = V.acknowledge(ctx, "amy", e["p_10"]["placement_id"], v["sequence"]["rev"])
    pl = [p for m in r["sequence"]["modules"] for s in m["slots"] for p in s["placements"]
          if p["node_id"] == "WHO-0010"][0]
    assert pl["relabelled"] is False


def test_placed_elsewhere_overlay():
    ctx, _ = make_env()
    g3 = V.create_sequence(ctx, "cat", "3", "Grade 3 sequence", None)
    g3m = V.create_module(ctx, "cat", g3["result"]["sequence_id"], rev(g3), "M1 Round", None)
    # WHO-0010 is span 1-4: place it in G3's sequence
    r3 = V.place(ctx, "cat", g3["result"]["sequence_id"], rev(g3m), g3m["result"]["module_id"], K["WHO-0010"])
    assert r3["result"]["placed_elsewhere"] == []
    e = build_5_6(ctx)
    # the G2 slice (and drawer) show the G3 placement; the G3 slice shows nothing for it
    want = [{"grade": "3", "sequence_id": g3["result"]["sequence_id"], "sequence_title": "Grade 3 sequence",
             "module_id": g3m["result"]["module_id"], "module_title": "M1 Round",
             "placement_id": r3["result"]["placement_id"]}]
    sl = V.get_slice(ctx, "2")
    node = [n for n in sl["nodes"] if n["source_key"] == K["WHO-0010"]][0] if READ_MODE == "fake" else None
    if READ_MODE == "fake":
        assert node["placed_elsewhere"] == want
    else:
        found = [n for st in (x for d in sl["super_stems"] for x in d["stems"])
                 for c in st["concept_skills"] for n in c["nodes"] if n["source_key"] == K["WHO-0010"]][0]
        assert found["placed_elsewhere"] == want
    dr = V.get_node(ctx, K["WHO-0010"], "2")
    assert dr["placed_elsewhere"] == want
    # and the G2 sequence view's own placements carry it
    v = V.get_sequence(ctx, "2")
    pl = [p for m in v["modules"] for s in m["slots"] for p in s["placements"] if p["node_id"] == "WHO-0010"][0]
    assert pl["placed_elsewhere"] == want
    # the place result reports the cross-grade placement too (the prompt's data)
    res = V.create_slot_group(ctx, "amy", e["sid"], v["sequence"]["rev"], e["m1"], [K["COM-0013"]], False, None)
    assert res["result"]["skipped"] == [] and len(res["result"]["placement_ids"]) == 1
    sl3 = V.get_slice(ctx, "3")
    assert json.dumps(sl3)


def test_slot_operations_through_service():
    ctx, _ = make_env()
    e = build_5_6(ctx)
    sid = e["sid"]
    r = V.get_sequence(ctx, "2")
    # merge M1's slot into the co-placed slot of M2
    r1 = V.merge_slot(ctx, "amy", e["p_11"]["slot_id"], r["sequence"]["rev"], e["p_10"]["slot_id"])
    assert r1["result"] == {"slot_id": e["p_10"]["slot_id"]}
    m1 = r1["sequence"]["modules"][0]
    assert m1["slots"] == []
    assert len(r1["sequence"]["modules"][1]["slots"][0]["placements"]) == 3
    # ungroup one, then move it across the boundary to M1 with to_module_id
    pid = e["p_11"]["placement_id"]
    pv = [p for s in r1["sequence"]["modules"][1]["slots"] for p in s["placements"] if p["placement_id"] == pid][0]
    r2 = V.ungroup(ctx, "amy", pid, rev(r1))
    new_slot = r2["result"]["slot_id"]
    r3 = V.move_slot(ctx, "amy", new_slot, rev(r2), None, e["m1"])
    assert [s["slot_id"] for s in r3["sequence"]["modules"][0]["slots"]] == [new_slot]
    r4 = V.update_slot(ctx, "amy", new_slot, rev(r3), "Topic A")
    assert r4["sequence"]["modules"][0]["slots"][0]["label"] == "Topic A"
    # module operations
    r5 = V.move_module(ctx, "amy", e["m1"], rev(r4), "down")
    assert [m["module_id"] for m in r5["sequence"]["modules"]] == [e["m2"], e["m1"]]
    err = expect(S.Conflict, lambda: V.remove_module(ctx, "amy", e["m1"], rev(r5)), "module_not_empty")
    err = expect(S.Invalid, lambda: V.move_module(ctx, "amy", e["m1"], rev(r5), "down"), "at_edge")
    r6 = V.update_module(ctx, "amy", e["m1"], rev(r5), {"title": "M1b", "note": "n"})
    assert r6["sequence"]["modules"][1]["title"] == "M1b"
    r7 = V.update_module(ctx, "amy", e["m1"], rev(r6), {"note": None})
    assert r7["sequence"]["modules"][1]["note"] is None
    r8 = V.co_place(ctx, "amy", pid, rev(r7), e["p_10"]["slot_id"])
    assert r8["result"] == {"slot_id": e["p_10"]["slot_id"]}
    assert r8["sequence"]["modules"][1]["slots"] == []       # module M1 emptied its slot
    r9 = V.remove_module(ctx, "amy", e["m1"], rev(r8))
    assert [m["module_id"] for m in r9["sequence"]["modules"]] == [e["m2"]]
    r10 = V.update_sequence(ctx, "amy", sid, rev(r9), {"title": "Renamed", "owner": "bob", "note": "x"})
    sq = r10["sequence"]["sequence"]
    assert (sq["title"], sq["owner"], sq["note"]) == ("Renamed", "bob", "x")
    r11 = V.update_sequence(ctx, "amy", sid, rev(r10), {"note": None})
    assert r11["sequence"]["sequence"]["note"] is None
    expect(S.Invalid, lambda: V.update_sequence(ctx, "amy", sid, rev(r11), {"title": None}), "invalid")
    expect(S.Conflict, lambda: V.create_sequence(ctx, "amy", "2", "again", None), "sequence_exists")


def test_events_paging_and_attribution():
    ctx, _ = make_env()
    e = build_5_6(ctx, writer="amy")
    V.update_placement(ctx, "bob", e["p_10"]["placement_id"], 1, {"calibration": "deep", "differentiation_note": "q"})
    allev = V.get_events(ctx, e["sid"], 200, None)
    assert allev["next_before"] is None
    n = len(allev["events"])
    assert n >= 9 and allev["events"][0]["actor"] == "bob" and allev["events"][-1]["action"] == "sequence_create"
    p1 = V.get_events(ctx, e["sid"], 4, None)
    assert len(p1["events"]) == 4 and p1["next_before"] == p1["events"][-1]["event_id"]
    p2 = V.get_events(ctx, e["sid"], 4, p1["next_before"])
    assert p2["events"][0]["event_id"] < p1["next_before"]
    assert V.get_events(ctx, e["sid"], 10000, None)["events"] == allev["events"]   # clamped to 200
    assert set(allev["events"][0]) == {"event_id", "action", "actor", "at", "module_id", "slot_id",
                                       "placement_id", "before", "after"}
    json.dumps(allev)


def test_views_crud_and_identity():
    ctx, _ = make_env()
    v = V.create_view(ctx, "amy", "2", "Lens", {"hidden_stems": ["TIM"], "context": False})
    assert v["grade"] == "2" and v["state"]["hidden_stems"] == ["TIM"]
    assert V.list_views(ctx, "amy", "2") == {"views": [v]}
    assert V.list_views(ctx, "amy", None) == {"views": [v]}
    assert V.list_views(ctx, "bob", "2") == {"views": []}
    expect(S.Conflict, lambda: V.create_view(ctx, "amy", "2", "Lens", {}), "view_name_exists")
    expect(S.NotFound, lambda: V.update_view(ctx, "bob", v["view_id"], {"name": "x"}))
    u = V.update_view(ctx, "amy", v["view_id"], {"name": "Lens2"})
    assert u["name"] == "Lens2" and u["state"] == v["state"]
    assert V.delete_view(ctx, "amy", v["view_id"]) == {"deleted": v["view_id"]}
    expect(S.NotFound, lambda: V.delete_view(ctx, "amy", v["view_id"]))
    expect(S.NotFound, lambda: V.create_view(ctx, "amy", "99", "x", {}))


def test_empty_grade_views_and_grades():
    ctx, _ = make_env()
    v = V.get_sequence(ctx, "4")
    assert v["sequence"] is None and v["modules"] == [] and v["placed_index"] == {} \
        and v["slice_badges"] == {} and v["attention"] == []
    from mh2 import seq_guardrail
    assert v["guardrail"] == seq_guardrail.compute([])
    assert V.get_guardrail(ctx, "4") == {"sequence_id": None, "sequence": seq_guardrail.compute([]),
                                         "modules": []}
    assert V.get_attention(ctx, "4") == {"sequence_id": None, "items": []}
    build = V.create_sequence(ctx, "amy", "2", "Grade 2 sequence", None)
    g = V.grades(ctx)
    assert set(g) == {"grades", "kind_source", "ladders_last_read"}
    by = {x["grade"]: x for x in g["grades"]}
    assert by["2"]["sequence"]["sequence_id"] == build["result"]["sequence_id"]
    assert set(by["2"]["sequence"]) == {"sequence_id", "grade", "title", "owner", "rev", "n_modules",
                                        "n_placements", "updated_at"}
    assert by["3"]["sequence"] is None
    assert g["ladders_last_read"] == "2026-09-09 15:11:11"
    json.dumps(g)


def test_every_return_value_is_json_serialisable():
    ctx, mh2 = make_env()
    e = build_5_6(ctx)
    outs = [V.grades(ctx), V.get_slice(ctx, "2"), V.get_node(ctx, K["WHO-0010"], "2"),
            V.get_compare(ctx, [K["WHO-0010"], K["COM-0012"]], "2"), V.get_sequence(ctx, "2"),
            V.get_guardrail(ctx, "2"), V.get_attention(ctx, "2"),
            V.get_events(ctx, e["sid"], 50, None), V.list_views(ctx, "amy", None)]
    for o in outs:
        json.dumps(o)
    mh2_edit(mh2, "UPDATE nodes SET source_key='WHO:deadbeefdeadbeef' WHERE node_id='WHO-0011'")
    json.dumps(V.get_sequence(ctx, "2"))


def test_service_has_no_fastapi_or_raw_sql():
    src = (ROOT / "mh2" / "seq_service.py").read_text()
    assert "fastapi" not in src.lower().replace("no fastapi import", "")
    assert "sqlite3" not in src and "execute(" not in src
    assert "mh2.coverage" not in src


def _main():
    print(f"[read modules: {READ_MODE}]")
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok", name)
            except Exception as ex:  # noqa: BLE001
                import traceback
                failed += 1
                print("FAIL", name, repr(ex))
                traceback.print_exc()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
