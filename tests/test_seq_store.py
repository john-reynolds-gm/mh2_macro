"""seq_store tests (contract §8.2): lifecycle, concurrency, order keys,
events, validation, saved views, signature / source rules."""
import inspect
import json
import re
import shutil
import sqlite3
import sys
import tempfile
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
from mh2 import seq_store as S  # noqa: E402


def fresh():
    d = _mkdtemp(prefix="seqstore_")
    con = S.connect(Path(d) / "mh2_seq.db")
    S.ensure_schema(con)
    return con


def snap(key, state="core", requires=False, node_id=None, stem="WHO", cs="C/S one"):
    return {"source_key": key, "node_id": node_id or key.upper(), "node_text": f"text of {key}",
            "source_file": "ladder.docx", "stem_id": stem, "concept_skill": cs,
            "state": state, "requires_confirm": requires}


def seq_rev(con, sid):
    return S.get_sequence(con, sid)["rev"]


def dump(con):
    out = {}
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"
                            " AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
        out[t] = [tuple(r) for r in con.execute(f"SELECT * FROM {t} ORDER BY 1")]
    return out


def n_events(con, sid=None):
    if sid is None:
        return con.execute("SELECT COUNT(*) FROM placement_event").fetchone()[0]
    return con.execute("SELECT COUNT(*) FROM placement_event WHERE sequence_id=?",
                       (sid,)).fetchone()[0]


def setup_seq(con, grade="2", modules=2):
    sid = S.create_sequence(con, "amy", grade, f"Grade {grade} sequence")
    mids = [S.create_module(con, "amy", sid, seq_rev(con, sid), f"M{i}") for i in range(1, modules + 1)]
    return sid, mids


def expect(exc, fn, code=None):
    try:
        fn()
    except exc as e:
        if code is not None:
            assert e.code == code, (e.code, code)
        return e
    raise AssertionError(f"expected {exc.__name__} {code}")


# ------------------------------------------------------------------- schema

REVIEW_TABLES = ("standard_review", "standard_color_override", "standard_status",
                 "tag_review", "tag_proposal", "candidate_ruling")


def test_ensure_schema_seven_tables_and_review_tables_untouched():
    con = fresh()
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'"
                                       " AND name NOT LIKE 'sqlite_%'")}
    assert names == set(S._TABLES) and len(names) == 7, names
    # against a temp copy of the real review DB
    src = Path(config.SEQ_DB)
    if not src.exists():
        print("SKIP real-copy half: no", src)
        return
    d = _mkdtemp()
    cp = Path(d) / "mh2_seq.db"
    shutil.copy(src, cp)
    raw = sqlite3.connect(cp)
    have = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    tables = [t for t in REVIEW_TABLES if t in have]
    assert len(tables) == 6, have
    before = {t: raw.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    raw.close()
    con2 = S.connect(cp)
    S.ensure_schema(con2)
    S.ensure_schema(con2)   # idempotent, fast path
    after = {t: con2.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    assert before == after
    assert {r[0] for r in con2.execute("SELECT name FROM sqlite_master WHERE type='table'"
                                       " AND name NOT LIKE 'sqlite_%'")} >= set(S._TABLES)


# ---------------------------------------------------------------- lifecycle

def test_full_lifecycle():
    con = fresh()
    sid = S.create_sequence(con, "amy", "2", "Grade 2 sequence", note="n")
    assert S.active_sequence_id(con, "2") == sid
    row = S.get_sequence(con, sid)
    assert row["owner"] == "amy" and row["created_by"] == "amy" and row["rev"] == 1
    expect(S.Conflict, lambda: S.create_sequence(con, "amy", "2", "Another"), "sequence_exists")
    m1 = S.create_module(con, "amy", sid, 1, "M1")
    m2 = S.create_module(con, "amy", sid, 2, "M2")
    assert seq_rev(con, sid) == 3
    # place: solo, solo, bridge
    r1 = S.place(con, "amy", sid, 3, snap("WHO:a"), module_id=m1)
    r2 = S.place(con, "bob", sid, 4, snap("WHO:b"), module_id=m1, differentiation_note="within 100")
    r3 = S.place(con, "bob", sid, 5, snap("COM:c"), module_id=m2)
    assert r1["slot_id"] != r2["slot_id"]
    p2 = S.get_placement(con, r2["placement_id"])
    assert p2["placed_by"] == "bob" and p2["differentiation_note"] == "within 100"
    assert p2["grade_kind_seen"] == "core" and p2["node_text_seen"] == "text of WHO:b"
    # co-place: b joins a's slot; b's old slot drops
    S.co_place(con, "amy", r2["placement_id"], 6, r1["slot_id"])
    tree = S.load_tree(con, sid)
    assert [len(s["placements"]) for s in tree["modules"][0]["slots"]] == [2]
    assert tree["modules"][0]["slots"][0]["placements"][1]["source_key"] == "WHO:b"
    # ungroup: b back to its own slot right after
    new_slot = S.ungroup(con, "amy", r2["placement_id"], 7)
    tree = S.load_tree(con, sid)
    slots = tree["modules"][0]["slots"]
    assert [s["slot_id"] for s in slots] == [r1["slot_id"], new_slot]
    expect(S.Invalid, lambda: S.ungroup(con, "amy", r2["placement_id"], 8), "already_alone")
    # merge: the new slot into a's slot
    S.merge_slot(con, "amy", new_slot, 8, r1["slot_id"])
    tree = S.load_tree(con, sid)
    assert len(tree["modules"][0]["slots"]) == 1
    assert [p["source_key"] for p in tree["modules"][0]["slots"][0]["placements"]] == ["WHO:a", "WHO:b"]
    # move slot across a module boundary (down from last slot of M1 -> start of M2)
    S.move_slot(con, "amy", r1["slot_id"], 9, direction="down")
    tree = S.load_tree(con, sid)
    assert tree["modules"][0]["slots"] == []
    assert [s["slot_id"] for s in tree["modules"][1]["slots"]] == [r1["slot_id"], r3["slot_id"]]
    # ... and back up: first slot of M2 goes to the end of M1
    S.move_slot(con, "amy", r1["slot_id"], 10, direction="up")
    tree = S.load_tree(con, sid)
    assert [s["slot_id"] for s in tree["modules"][0]["slots"]] == [r1["slot_id"]]
    # move to an explicit module (appended at the end)
    S.move_slot(con, "amy", r1["slot_id"], 11, to_module_id=m2)
    tree = S.load_tree(con, sid)
    assert [s["slot_id"] for s in tree["modules"][1]["slots"]] == [r3["slot_id"], r1["slot_id"]]
    # swap within module
    S.move_slot(con, "amy", r1["slot_id"], 12, direction="up")
    tree = S.load_tree(con, sid)
    assert [s["slot_id"] for s in tree["modules"][1]["slots"]] == [r1["slot_id"], r3["slot_id"]]
    # slot label, module rename/move
    S.update_slot(con, "amy", r1["slot_id"], 13, "Co-taught: compare")
    S.update_module(con, "amy", m1, 14, title="M1 renamed", note="nn")
    S.move_module(con, "amy", m1, 15, "down")
    assert [m["module_id"] for m in S.load_tree(con, sid)["modules"]] == [m2, m1]
    # attributes (placement rev, sequence rev unchanged)
    pid = r3["placement_id"]
    rev_before = seq_rev(con, sid)
    S.set_attributes(con, "cat", pid, S.get_placement(con, pid)["rev"],
                     {"calibration": "deep", "period_estimate": 1.5, "differentiation_note": "x"},
                     period_hint_seen="G2: 1 period")
    assert seq_rev(con, sid) == rev_before
    p = S.get_placement(con, pid)
    assert (p["calibration"], p["period_estimate"], p["period_hint_seen"], p["updated_by"]) == \
        ("deep", 1.5, "G2: 1 period", "cat")
    S.set_attributes(con, "cat", pid, p["rev"], {"calibration": None})
    assert S.get_placement(con, pid)["calibration"] is None
    # remove a placement; its slot drops
    rev = seq_rev(con, sid)
    S.remove_placement(con, "amy", pid, rev, reason="moved to G3")
    p = S.get_placement(con, pid)
    assert p["removed_at"] and p["removed_by"] == "amy" and p["removed_reason"] == "moved to G3"
    tree = S.load_tree(con, sid)
    assert all(p["placement_id"] != pid for m in tree["modules"] for s in m["slots"] for p in s["placements"])
    # remove empty module / non-empty module
    e = expect(S.Conflict, lambda: S.remove_module(con, "amy", m2, seq_rev(con, sid)), "module_not_empty")
    assert e.extra["n_placements"] == 2
    S.remove_module(con, "amy", m1, seq_rev(con, sid))
    # a removed node can be placed again (a new row)
    r = S.place(con, "amy", sid, seq_rev(con, sid), snap("COM:c"), module_id=m2)
    assert r["placement_id"] != pid
    # history is newest-first and paged
    ev = S.list_events(con, sid, limit=1000)
    assert ev[0]["event_id"] > ev[-1]["event_id"]
    assert ev[-1]["action"] == "sequence_create" and ev[-1]["actor"] == "amy"
    page1 = S.list_events(con, sid, limit=5)
    page2 = S.list_events(con, sid, limit=5, before_event_id=page1[-1]["event_id"])
    assert page1[-1]["event_id"] > page2[0]["event_id"]
    actions = {e["action"] for e in ev}
    assert {"place", "co_place", "ungroup", "slot_merge", "slot_move", "slot_update",
            "module_update", "module_move", "set_calibration", "set_period", "set_note",
            "remove", "module_remove", "module_create"} <= actions, actions
    # sequence update + archive + summaries
    S.update_sequence(con, "zed", sid, seq_rev(con, sid), title="Renamed", owner="zed", note=None)
    sm = S.sequence_summaries(con)["2"]
    assert sm["title"] == "Renamed" and sm["owner"] == "zed" and sm["n_modules"] == 1
    assert sm["n_placements"] == 3 and sm["updated_at"]
    S.update_sequence(con, "zed", sid, seq_rev(con, sid), archived=True)
    assert S.active_sequence_id(con, "2") is None and "2" not in S.sequence_summaries(con)
    sid2 = S.create_sequence(con, "amy", "2", "New one")     # allowed once the first is archived
    expect(S.Conflict, lambda: S.update_sequence(con, "amy", sid, seq_rev(con, sid), archived=False),
           "sequence_exists")
    assert sid2 != sid


def test_duplicate_active_placement_and_cross_sequence():
    con = fresh()
    sid, (m1, m2) = setup_seq(con)
    r = S.place(con, "amy", sid, seq_rev(con, sid), snap("WHO:a"), module_id=m1)
    e = expect(S.Conflict, lambda: S.place(con, "amy", sid, seq_rev(con, sid), snap("WHO:a"),
                                           module_id=m2), "already_placed")
    assert e.extra["placement_id"] == r["placement_id"]
    # same key in another sequence succeeds
    sid3 = S.create_sequence(con, "amy", "3", "G3")
    m3 = S.create_module(con, "amy", sid3, seq_rev(con, sid3), "M1")
    S.place(con, "amy", sid3, seq_rev(con, sid3), snap("WHO:a"), module_id=m3)
    idx = S.placement_index(con)
    assert sorted(x["grade"] for x in idx["WHO:a"]) == ["2", "3"]
    idx2 = S.placement_index(con, exclude_grade="2")
    assert [x["grade"] for x in idx2["WHO:a"]] == ["3"]
    assert set(idx2["WHO:a"][0]) == {"grade", "sequence_id", "sequence_title", "module_id",
                                     "module_title", "placement_id"}
    # the partial unique index itself
    expect(sqlite3.IntegrityError, lambda: con.execute(
        "INSERT INTO placement (sequence_id, slot_id, source_key, node_text_seen,"
        " grade_kind_seen, placed_by, placed_at) VALUES (?,?,?,?,?,?,?)",
        (sid, r["slot_id"], "WHO:a", "t", "core", "x", "now")))
    # cross-sequence targets
    p = S.get_placement(con, r["placement_id"])
    other_slot = S.place(con, "amy", sid3, seq_rev(con, sid3), snap("WHO:z"), module_id=m3)["slot_id"]
    expect(S.Invalid, lambda: S.co_place(con, "amy", p["placement_id"], seq_rev(con, sid), other_slot),
           "cross_sequence")
    expect(S.Invalid, lambda: S.move_slot(con, "amy", p["slot_id"], seq_rev(con, sid), to_module_id=m3),
           "cross_sequence")
    expect(S.Invalid, lambda: S.place(con, "amy", sid, seq_rev(con, sid), snap("WHO:q"), module_id=m3),
           "cross_sequence")
    expect(S.Invalid, lambda: S.merge_slot(con, "amy", p["slot_id"], seq_rev(con, sid), other_slot),
           "cross_sequence")


def test_place_group_and_skips():
    con = fresh()
    sid, (m1, m2) = setup_seq(con)
    solo = S.place(con, "amy", sid, seq_rev(con, sid), snap("WHO:a"), module_id=m1)
    n0 = n_events(con)
    out = S.place_group(con, "amy", sid, seq_rev(con, sid),
                        [snap("WHO:a"), snap("WHO:b"), snap("COM:c")], module_id=m2,
                        differentiation_notes={"WHO:b": "b note"})
    assert out["skipped"] == [{"source_key": "WHO:a", "reason": "already_placed",
                               "placement_id": solo["placement_id"]}]
    assert len(out["placement_ids"]) == 2
    assert n_events(con) - n0 == 2           # one place event per placement
    tree = S.load_tree(con, sid)
    assert [p["source_key"] for p in tree["modules"][1]["slots"][0]["placements"]] == ["WHO:b", "COM:c"]
    assert tree["modules"][1]["slots"][0]["placements"][0]["differentiation_note"] == "b note"
    # everything already placed: nothing written, reported
    rev = seq_rev(con, sid)
    n1 = n_events(con)
    out = S.place_group(con, "amy", sid, rev, [snap("WHO:a")], module_id=m2)
    assert out["slot_id"] is None and out["placement_ids"] == [] and len(out["skipped"]) == 1
    assert seq_rev(con, sid) == rev and n_events(con) == n1
    # off-grade: all offenders listed, nothing written
    before = dump(con)
    e = expect(S.NeedsConfirm, lambda: S.place_group(
        con, "amy", sid, rev, [snap("X:1", "off_grade", True), snap("X:2"),
                               snap("X:3", "leaf", True)], module_id=m2))
    assert e.status == 422 and e.code == "confirm_off_grade_required"
    assert e.extra["source_keys"] == ["X:1", "X:3"]
    assert e.extra["states"] == {"X:1": "off_grade", "X:3": "leaf"}
    assert dump(con) == before
    out = S.place_group(con, "amy", sid, rev, [snap("X:1", "off_grade", True)], module_id=m2,
                        confirm_off_grade=True)
    assert len(out["placement_ids"]) == 1


def test_gap_exhaustion_renumbers_once_and_keeps_order():
    con = fresh()
    sid, (m1,) = setup_seq(con, modules=1)
    a = S.place(con, "amy", sid, seq_rev(con, sid), snap("K:a"), module_id=m1)
    S.place(con, "amy", sid, seq_rev(con, sid), snap("K:b"), module_id=m1)
    renum0 = con.execute("SELECT COUNT(*) FROM placement_event WHERE action='renumber'").fetchone()[0]
    assert renum0 == 0
    inserted = []
    for i in range(11):
        r = S.place(con, "amy", sid, seq_rev(con, sid), snap(f"K:n{i}"), module_id=m1,
                    after_slot_id=a["slot_id"])
        inserted.append(r["slot_id"])
        n_ren = con.execute("SELECT COUNT(*) FROM placement_event WHERE action='renumber'").fetchone()[0]
        assert n_ren == (0 if i < 10 else 1), (i, n_ren)
    slots = S.load_tree(con, sid)["modules"][0]["slots"]
    keys = [s["order_key"] for s in slots]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    ids = [s["slot_id"] for s in slots]
    assert ids == [a["slot_id"]] + list(reversed(inserted)) + [ids[-1]]
    ev = con.execute("SELECT * FROM placement_event WHERE action='renumber'").fetchone()
    assert ev["module_id"] == m1 and json.loads(ev["after_json"])["kind"] == "slot"
    # only that container was renumbered
    assert json.loads(ev["after_json"])["container_id"] == m1


def test_front_insert_uses_half_step_and_module_start_crossing():
    con = fresh()
    sid, (m1, m2) = setup_seq(con)
    r = S.place(con, "amy", sid, seq_rev(con, sid), snap("K:a"), module_id=m2)
    r0 = S.place(con, "amy", sid, seq_rev(con, sid), snap("K:b"), module_id=m1)
    S.move_slot(con, "amy", r0["slot_id"], seq_rev(con, sid), direction="down")
    slots = S.load_tree(con, sid)["modules"][1]["slots"]
    assert [s["slot_id"] for s in slots] == [r0["slot_id"], r["slot_id"]]
    assert slots[0]["order_key"] == S.ORDER_STEP // 2


# -------------------------------------------------------------- concurrency

def test_stale_rev_raises_and_changes_nothing():
    con = fresh()
    sid, (m1, m2) = setup_seq(con)
    r = S.place(con, "amy", sid, seq_rev(con, sid), snap("WHO:a"), module_id=m1)
    pid = r["placement_id"]
    cur = seq_rev(con, sid)
    calls = {
        "place": lambda: S.place(con, "x", sid, cur - 1, snap("WHO:b"), module_id=m1),
        "create_module": lambda: S.create_module(con, "x", sid, cur - 1, "zzz"),
        "update_module": lambda: S.update_module(con, "x", m1, cur - 1, title="zzz"),
        "move_module": lambda: S.move_module(con, "x", m1, cur - 1, "down"),
        "remove_module": lambda: S.remove_module(con, "x", m2, cur - 1),
        "update_slot": lambda: S.update_slot(con, "x", r["slot_id"], cur - 1, "zz"),
        "move_slot": lambda: S.move_slot(con, "x", r["slot_id"], cur - 1, direction="down"),
        "co_place": lambda: S.co_place(con, "x", pid, cur - 1, r["slot_id"]),
        "ungroup": lambda: S.ungroup(con, "x", pid, cur - 1),
        "remove": lambda: S.remove_placement(con, "x", pid, cur - 1),
        "reattach": lambda: S.reattach(con, "x", pid, cur - 1, snap("WHO:zz")),
        "acknowledge": lambda: S.acknowledge(con, "x", pid, cur - 1, snap("WHO:a")),
        "update_sequence": lambda: S.update_sequence(con, "x", sid, cur - 1, title="zzz"),
        "place_group": lambda: S.place_group(con, "x", sid, cur + 5, [snap("WHO:b")], module_id=m1),
    }
    before = dump(con)
    for name, fn in calls.items():
        e = expect(S.StaleRevision, fn)
        assert e.status == 409 and e.code == "stale_revision", name
        assert e.extra["current_rev"] == cur, name
        assert dump(con) == before, f"{name} changed rows"
    # attribute writes check the PLACEMENT rev, and ignore the sequence rev
    prev = S.get_placement(con, pid)["rev"]
    e = expect(S.StaleRevision, lambda: S.set_attributes(con, "x", pid, prev + 3, {"calibration": "deep"}))
    assert e.extra["current_rev"] == prev
    assert dump(con) == before
    # existence comes before revision
    expect(S.NotFound, lambda: S.update_module(con, "x", 9999, 1, title="q"))
    expect(S.NotFound, lambda: S.place(con, "x", 9999, 1, snap("WHO:a"), module_id=m1))


def test_second_writer_with_old_rev_loses():
    con = fresh()
    con2 = S.connect(con.execute("PRAGMA database_list").fetchone()[2])
    sid, (m1,) = setup_seq(con, modules=1)
    rev = seq_rev(con, sid)
    S.place(con, "amy", sid, rev, snap("WHO:a"), module_id=m1)
    expect(S.StaleRevision, lambda: S.place(con2, "bob", sid, rev, snap("WHO:b"), module_id=m1))


def test_rollback_on_failure_leaves_no_partial_write():
    con = fresh()
    sid, (m1,) = setup_seq(con, modules=1)
    S.place(con, "amy", sid, seq_rev(con, sid), snap("WHO:a"), module_id=m1)
    before = dump(con)
    # a bad snapshot state fails mid-transaction (after the rev check)
    bad = snap("WHO:b", state="bogus")
    expect(S.Invalid, lambda: S.place(con, "amy", sid, seq_rev(con, sid), bad, module_id=m1))
    assert dump(con) == before
    assert not con.in_transaction


# ------------------------------------------------------------------- events

def test_event_counts_match_mutations():
    con = fresh()
    c = {"n": 0}

    def ev(k=1):
        c["n"] += k
        assert n_events(con) == c["n"], (n_events(con), c["n"])

    sid = S.create_sequence(con, "a", "4", "G4"); ev()
    m = S.create_module(con, "a", sid, 1, "M1"); ev()
    r1 = S.place(con, "a", sid, 2, snap("K:1"), module_id=m); ev()
    r2 = S.place(con, "a", sid, 3, snap("K:2"), module_id=m); ev()
    S.set_attributes(con, "a", r1["placement_id"], 1, {"calibration": "deep"}); ev()
    # multi-attribute PATCH: one event per changed attribute (this is the only multi-event call)
    S.set_attributes(con, "a", r1["placement_id"], 2,
                     {"calibration": "functional", "period_estimate": 2, "differentiation_note": "n"}); ev(3)
    # unchanged attribute dropped, one changed
    S.set_attributes(con, "a", r1["placement_id"], 3, {"calibration": "functional", "period_estimate": 3}); ev()
    # nothing changed -> no event, no rev bump
    S.set_attributes(con, "a", r1["placement_id"], 4, {"calibration": "functional"}); ev(0)
    assert S.get_placement(con, r1["placement_id"])["rev"] == 4
    S.co_place(con, "a", r2["placement_id"], 4, r1["slot_id"]); ev()
    S.ungroup(con, "a", r2["placement_id"], 5); ev()
    S.update_slot(con, "a", r1["slot_id"], 6, "lbl"); ev()
    S.remove_placement(con, "a", r2["placement_id"], 7); ev()
    S.update_module(con, "a", m, 8, title="M1b"); ev()
    S.update_sequence(con, "a", sid, 9, note="hi"); ev()
    # every sequence-level event carries actor and timestamp, stamped by the store
    for e in S.list_events(con, sid, limit=100):
        assert e["actor"] == "a" and re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$", e["at"])
    # before/after hold changed fields only
    e = [x for x in S.list_events(con, sid, 100) if x["action"] == "set_calibration"][0]
    assert set(e["before"]) == {"calibration"} and set(e["after"]) == {"calibration"}


# ----------------------------------------------------------- store rules

def test_needs_confirm_single_place():
    con = fresh()
    sid, (m1,) = setup_seq(con, modules=1)
    s = snap("WHO:o", "off_grade", True)
    before = dump(con)
    e = expect(S.NeedsConfirm, lambda: S.place(con, "a", sid, seq_rev(con, sid), s, module_id=m1))
    assert e.extra == {"source_keys": ["WHO:o"], "states": {"WHO:o": "off_grade"}}
    assert dump(con) == before
    r = S.place(con, "a", sid, seq_rev(con, sid), s, module_id=m1, confirm_off_grade=True)
    assert S.get_placement(con, r["placement_id"])["grade_kind_seen"] == "off_grade"


def test_module_not_empty_and_at_edge_and_validation():
    con = fresh()
    sid, (m1, m2) = setup_seq(con)
    r = S.place(con, "a", sid, seq_rev(con, sid), snap("WHO:a"), module_id=m1)
    expect(S.Conflict, lambda: S.remove_module(con, "a", m1, seq_rev(con, sid)), "module_not_empty")
    # edges
    expect(S.Invalid, lambda: S.move_module(con, "a", m1, seq_rev(con, sid), "up"), "at_edge")
    expect(S.Invalid, lambda: S.move_module(con, "a", m2, seq_rev(con, sid), "down"), "at_edge")
    expect(S.Invalid, lambda: S.move_slot(con, "a", r["slot_id"], seq_rev(con, sid), direction="up"), "at_edge")
    r2 = S.place(con, "a", sid, seq_rev(con, sid), snap("WHO:b"), module_id=m2)
    expect(S.Invalid, lambda: S.move_slot(con, "a", r2["slot_id"], seq_rev(con, sid), direction="down"), "at_edge")
    # validation
    rev = lambda: seq_rev(con, sid)  # noqa: E731
    expect(S.Invalid, lambda: S.move_module(con, "a", m1, rev(), "sideways"), "invalid")
    expect(S.Invalid, lambda: S.move_slot(con, "a", r["slot_id"], rev()), "invalid")
    expect(S.Invalid, lambda: S.move_slot(con, "a", r["slot_id"], rev(), direction="up", to_module_id=m2), "invalid")
    expect(S.Invalid, lambda: S.create_module(con, "a", sid, rev(), "  "), "invalid")
    expect(S.Invalid, lambda: S.create_sequence(con, "a", "3", ""), "invalid")
    expect(S.Invalid, lambda: S.create_sequence(con, "a", "9", "x"), "invalid")
    pid, prev = r["placement_id"], S.get_placement(con, r["placement_id"])["rev"]
    expect(S.Invalid, lambda: S.set_attributes(con, "a", pid, prev, {"calibration": "huge"}), "invalid")
    expect(S.Invalid, lambda: S.set_attributes(con, "a", pid, prev, {"period_estimate": -1}), "invalid")
    expect(S.Invalid, lambda: S.set_attributes(con, "a", pid, prev, {"period_estimate": 201}), "invalid")
    expect(S.Invalid, lambda: S.set_attributes(con, "a", pid, prev, {"period_estimate": "2"}), "invalid")
    expect(S.Invalid, lambda: S.set_attributes(con, "a", pid, prev, {"rev": 4}), "invalid")
    expect(S.Invalid, lambda: S.set_attributes(con, "a", pid, prev, {}), "invalid")
    S.set_attributes(con, "a", pid, prev, {"period_estimate": 0})        # zero is allowed
    S.set_attributes(con, "a", pid, prev + 1, {"period_estimate": 200})  # boundary allowed
    expect(S.Invalid, lambda: S.update_slot(con, "a", r["slot_id"], rev(), 5), "invalid")


def test_reattach_and_acknowledge_preserve_identity():
    con = fresh()
    sid, (m1,) = setup_seq(con, modules=1)
    r = S.place(con, "a", sid, seq_rev(con, sid), snap("WHO:old", node_id="WHO-0011", cs="Label (Amy done)"),
                module_id=m1)
    S.place(con, "a", sid, seq_rev(con, sid), snap("WHO:other"), module_id=m1)
    pid = r["placement_id"]
    S.set_attributes(con, "a", pid, 1, {"calibration": "deep", "period_estimate": 2})
    expect(S.Conflict, lambda: S.reattach(con, "b", pid, seq_rev(con, sid), snap("WHO:other")),
           "reattach_target_placed")
    new = snap("WHO:new", state="span", node_id="WHO-0012", cs="Label")
    S.reattach(con, "b", pid, seq_rev(con, sid), new)
    p = S.get_placement(con, pid)
    assert p["source_key"] == "WHO:new" and p["reattached_from"] == "WHO:old"
    assert p["node_id_seen"] == "WHO-0012" and p["grade_kind_seen"] == "span"
    assert p["concept_skill_seen"] == "Label" and p["updated_by"] == "b"
    assert p["calibration"] == "deep" and p["period_estimate"] == 2 and p["placed_by"] == "a"
    ev = [e for e in S.list_events(con, sid, 100) if e["action"] == "reattach"][0]
    assert ev["before"]["source_key"] == "WHO:old" and ev["after"]["source_key"] == "WHO:new"
    # acknowledge refreshes the snapshots only
    S.acknowledge(con, "c", pid, seq_rev(con, sid), snap("WHO:new", state="off_grade", node_id="WHO-0099", cs="Label"))
    p = S.get_placement(con, pid)
    assert p["grade_kind_seen"] == "off_grade" and p["node_id_seen"] == "WHO-0099"
    assert p["source_key"] == "WHO:new" and p["reattached_from"] == "WHO:old"
    expect(S.Invalid, lambda: S.acknowledge(con, "c", pid, seq_rev(con, sid), snap("WHO:zzz")), "invalid")


# -------------------------------------------------------------------- views

def test_saved_views_owner_scoped():
    con = fresh()
    v = S.create_view(con, "amy", "2", "Mine", {"hidden_stems": ["WHO"], "context": True})
    assert set(v) == {"view_id", "grade", "name", "state", "created_at", "updated_at"}
    assert v["state"] == {"hidden_stems": ["WHO"], "context": True}
    expect(S.Conflict, lambda: S.create_view(con, "amy", "2", "Mine", {}), "view_name_exists")
    S.create_view(con, "amy", "3", "Mine", {})            # same name, other grade: fine
    bv = S.create_view(con, "bob", "2", "Mine", {"x": 1})  # same name, other owner: fine
    assert [x["name"] for x in S.list_views(con, "amy", "2")] == ["Mine"]
    assert len(S.list_views(con, "amy")) == 2
    assert [x["view_id"] for x in S.list_views(con, "bob")] == [bv["view_id"]]
    expect(S.NotFound, lambda: S.update_view(con, "bob", v["view_id"], name="hax"))
    expect(S.NotFound, lambda: S.delete_view(con, "bob", v["view_id"]))
    expect(S.NotFound, lambda: S.update_view(con, "amy", 99999, name="x"))
    u = S.update_view(con, "amy", v["view_id"], state={"a": 1})
    assert u["state"] == {"a": 1} and u["name"] == "Mine"
    u = S.update_view(con, "amy", v["view_id"], name="Renamed")
    assert u["name"] == "Renamed" and u["state"] == {"a": 1}
    S.create_view(con, "amy", "2", "Other", {})
    expect(S.Conflict, lambda: S.update_view(con, "amy", v["view_id"], name="Other"), "view_name_exists")
    expect(S.Invalid, lambda: S.create_view(con, "amy", "2", " ", {}), "invalid")
    expect(S.Invalid, lambda: S.create_view(con, "amy", "2", "n", []), "invalid")
    S.delete_view(con, "amy", v["view_id"])
    assert all(x["view_id"] != v["view_id"] for x in S.list_views(con, "amy"))


# ------------------------------------------------ period autofill (O8 / O9)

def hint(value, text="Likely 2 instructional days"):
    return {"text": text, "value": value, "unit": "day", "qualifier": "exact",
            "low": value, "high": value, "basis": "single_grade_node", "n_estimates": 1}


def events_for(con, pid):
    return [(e["action"], e["before"], e["after"])
            for e in reversed(S.list_events(con, S.get_placement(con, pid)["sequence_id"]))
            if e["placement_id"] == pid]


def test_autofill_on_place_and_place_group():
    con = fresh()
    sid, (m1, _m2) = setup_seq(con)
    r = S.place(con, "a", sid, seq_rev(con, sid), dict(snap("k1"), period_hint=hint(2.0)), module_id=m1)
    p = S.get_placement(con, r["placement_id"])
    assert (p["period_estimate"], p["estimate_source"], p["period_hint_seen"]) == \
        (2.0, "ladder", "Likely 2 instructional days")
    (act, before, after), = events_for(con, r["placement_id"])
    assert act == "place" and before is None
    assert after["period_estimate"] == 2.0 and after["estimate_source"] == "ladder"
    # no hint, or a hint with no value: blank, source NULL
    r2 = S.place(con, "a", sid, seq_rev(con, sid), snap("k2"), module_id=m1)
    r3 = S.place(con, "a", sid, seq_rev(con, sid), dict(snap("k3"), period_hint=hint(None)), module_id=m1)
    for pid in (r2["placement_id"], r3["placement_id"]):
        p = S.get_placement(con, pid)
        assert (p["period_estimate"], p["estimate_source"], p["period_hint_seen"]) == (None, None, None)
        assert events_for(con, pid)[0][2]["estimate_source"] is None
    g = S.place_group(con, "a", sid, seq_rev(con, sid),
                      [dict(snap("k4"), period_hint=hint(0.5, "part of 1 period")), snap("k5")],
                      module_id=m1)
    p4, p5 = (S.get_placement(con, pid) for pid in g["placement_ids"])
    assert (p4["period_estimate"], p4["estimate_source"]) == (0.5, "ladder")
    assert (p5["period_estimate"], p5["estimate_source"]) == (None, None)
    after = events_for(con, p4["placement_id"])[0][2]
    assert after["group"] is True and after["period_estimate"] == 0.5 and after["estimate_source"] == "ladder"


def test_autofill_untouched_by_co_place_and_moves_and_re_place_autofills_again():
    con = fresh()
    sid, (m1, m2) = setup_seq(con)
    a = S.place(con, "a", sid, seq_rev(con, sid), dict(snap("k1"), period_hint=hint(2.0)), module_id=m1)
    b = S.place(con, "a", sid, seq_rev(con, sid), snap("k2"), module_id=m1)
    S.co_place(con, "a", a["placement_id"], seq_rev(con, sid), b["slot_id"])
    S.move_slot(con, "a", b["slot_id"], seq_rev(con, sid), to_module_id=m2)
    p = S.get_placement(con, a["placement_id"])
    assert (p["period_estimate"], p["estimate_source"]) == (2.0, "ladder")
    S.set_attributes(con, "a", a["placement_id"], p["rev"], {"period_estimate": 4})
    S.remove_placement(con, "a", a["placement_id"], seq_rev(con, sid))
    c = S.place(con, "a", sid, seq_rev(con, sid), dict(snap("k1"), period_hint=hint(2.0)), module_id=m1)
    assert c["placement_id"] != a["placement_id"]
    p = S.get_placement(con, c["placement_id"])
    assert (p["period_estimate"], p["estimate_source"]) == (2.0, "ladder")
    assert S.get_placement(con, a["placement_id"])["estimate_source"] == "builder"   # history kept


def test_edit_makes_builder_same_number_counts_and_clear_is_null():
    con = fresh()
    sid, (m1, _) = setup_seq(con)
    pid = S.place(con, "a", sid, seq_rev(con, sid), dict(snap("k1"), period_hint=hint(2.0)),
                  module_id=m1)["placement_id"]
    # the same number as the ladder's is still an edit
    S.set_attributes(con, "b", pid, 1, {"period_estimate": 2}, period_hint_seen="Likely 2 instructional days")
    p = S.get_placement(con, pid)
    assert (p["period_estimate"], p["estimate_source"], p["rev"]) == (2.0, "builder", 2)
    act, before, after = events_for(con, pid)[-1]
    assert act == "set_period"
    assert before == {"period_estimate": 2.0, "period_hint_seen": "Likely 2 instructional days",
                      "estimate_source": "ladder"}
    assert after == {"period_estimate": 2.0, "period_hint_seen": "Likely 2 instructional days",
                     "estimate_source": "builder"}
    # same number again once it is the builder's: no change, no event
    n = n_events(con)
    S.set_attributes(con, "b", pid, 2, {"period_estimate": 2})
    assert n_events(con) == n and S.get_placement(con, pid)["rev"] == 2
    S.set_attributes(con, "b", pid, 2, {"period_estimate": 3})
    assert S.get_placement(con, pid)["estimate_source"] == "builder"
    # clearing: NULL source
    S.set_attributes(con, "b", pid, 3, {"period_estimate": None})
    p = S.get_placement(con, pid)
    assert (p["period_estimate"], p["estimate_source"]) == (None, None)
    assert events_for(con, pid)[-1][2]["estimate_source"] is None
    # clearing a ladder estimate straight away
    q = S.place(con, "a", sid, seq_rev(con, sid), dict(snap("k2"), period_hint=hint(1.0)),
                module_id=m1)["placement_id"]
    S.set_attributes(con, "b", q, 1, {"period_estimate": None})
    p = S.get_placement(con, q)
    assert (p["period_estimate"], p["estimate_source"]) == (None, None)
    assert events_for(con, q)[-1][1]["estimate_source"] == "ladder"
    # a calibration-only edit leaves the source alone
    r = S.place(con, "a", sid, seq_rev(con, sid), dict(snap("k3"), period_hint=hint(1.0)),
                module_id=m1)["placement_id"]
    S.set_attributes(con, "b", r, 1, {"calibration": "deep"})
    assert S.get_placement(con, r)["estimate_source"] == "ladder"


def test_confirm_estimate():
    con = fresh()
    sid, (m1, _) = setup_seq(con)
    pid = S.place(con, "a", sid, seq_rev(con, sid), dict(snap("k1"), period_hint=hint(0.5)),
                  module_id=m1)["placement_id"]
    srev = seq_rev(con, sid)
    expect(S.StaleRevision, lambda: S.confirm_estimate(con, "b", pid, 7))
    S.confirm_estimate(con, "b", pid, 1)
    p = S.get_placement(con, pid)
    assert (p["period_estimate"], p["estimate_source"], p["rev"], p["updated_by"]) == (0.5, "builder", 2, "b")
    assert seq_rev(con, sid) == srev                       # a placement write, like set_attributes
    assert events_for(con, pid)[-1] == ("confirm_period",
                                        {"period_estimate": 0.5, "estimate_source": "ladder"},
                                        {"period_estimate": 0.5, "estimate_source": "builder"})
    # already the builder's, or no estimate at all: invalid, nothing written
    n = n_events(con)
    e = expect(S.Invalid, lambda: S.confirm_estimate(con, "b", pid, 2), "invalid")
    assert e.status == 422
    blank = S.place(con, "a", sid, seq_rev(con, sid), snap("k2"), module_id=m1)["placement_id"]
    n = n_events(con)
    expect(S.Invalid, lambda: S.confirm_estimate(con, "b", blank, 1), "invalid")
    assert n_events(con) == n and S.get_placement(con, blank)["rev"] == 1
    expect(S.NotFound, lambda: S.confirm_estimate(con, "b", 999, 1))
    expect(S.Invalid, lambda: S.confirm_estimate(con, "b", pid, None))


def test_migration_from_v1_schema():
    """A database built from the frozen v1 DDL gains estimate_source ('builder'
    where a person typed an estimate) and accepts confirm_period events; event
    rows and ids survive the placement_event rebuild; a second run is a no-op."""
    d = _mkdtemp(prefix="seqmig_")
    path = Path(d) / "mh2_seq.db"
    con = S.connect(path)
    con.executescript((ROOT / "tests" / "fixtures" / "schema_placement_v1.sql").read_text())
    con.execute("INSERT INTO grade_sequence (grade, title, created_by, created_at, owner)"
                " VALUES ('2', 'G2', 'amy', 'x', 'amy')")
    con.execute("INSERT INTO module (sequence_id, title, order_key, created_by, created_at)"
                " VALUES (1, 'M1', 1024, 'amy', 'x')")
    con.execute("INSERT INTO slot (sequence_id, module_id, order_key, created_by, created_at)"
                " VALUES (1, 1, 1024, 'amy', 'x')")
    for key, est in (("k1", 2.5), ("k2", None)):
        con.execute("INSERT INTO placement (sequence_id, slot_id, source_key, node_text_seen,"
                    " grade_kind_seen, period_estimate, placed_by, placed_at)"
                    " VALUES (1, 1, ?, 't', 'core', ?, 'amy', 'x')", (key, est))
    for i in range(3):
        con.execute("INSERT INTO placement_event (sequence_id, placement_id, action, actor, at,"
                    " after_json) VALUES (1, 1, 'set_period', 'amy', 'x', ?)", (json.dumps({"i": i}),))
    con.execute("DELETE FROM placement_event WHERE event_id = 3")   # AUTOINCREMENT high-water 3
    try:
        con.execute("INSERT INTO placement_event (sequence_id, action, actor, at)"
                    " VALUES (1, 'confirm_period', 'a', 'x')")
        raise AssertionError("v1 schema should reject confirm_period")
    except sqlite3.IntegrityError:
        pass
    before_events = [tuple(r) for r in con.execute("SELECT * FROM placement_event ORDER BY 1")]
    con.close()

    con = S.connect(path)
    S.ensure_schema(con)
    rows = {r["source_key"]: (r["period_estimate"], r["estimate_source"])
            for r in con.execute("SELECT * FROM placement")}
    assert rows == {"k1": (2.5, "builder"), "k2": (None, None)}
    assert [tuple(r) for r in con.execute("SELECT * FROM placement_event ORDER BY 1")] == before_events
    idx = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'"
                                     " AND tbl_name='placement_event'")}
    assert {"ix_event_seq", "ix_event_placement"} <= idx
    assert con.execute("SELECT version FROM placement_schema_meta").fetchone()[0] == 2
    assert not con.execute("SELECT name FROM sqlite_master WHERE name='placement_event_v1'").fetchone()
    con.execute("INSERT INTO placement_event (sequence_id, action, actor, at)"
                " VALUES (1, 'confirm_period', 'a', 'x')")
    assert con.execute("SELECT MAX(event_id) FROM placement_event").fetchone()[0] == 4
    try:
        con.execute("UPDATE placement SET estimate_source='typed'")
        raise AssertionError("estimate_source CHECK missing")
    except sqlite3.IntegrityError:
        pass
    snapshot = dump(con)
    S.ensure_schema(con)                                   # idempotent
    assert dump(con) == snapshot and S._needs_v2(con) is None
    # and the migrated database works end to end
    S.set_attributes(con, "b", 2, 1, {"period_estimate": 1})
    assert S.get_placement(con, 2)["estimate_source"] == "builder"
    con.close()


def test_fresh_schema_is_v2():
    con = fresh()
    assert S._needs_v2(con) is None
    assert con.execute("SELECT version FROM placement_schema_meta").fetchone()[0] == 2


# --------------------------------------------------- signature & source rules

WRITERS = ("create_sequence", "update_sequence", "create_module", "update_module", "move_module",
           "remove_module", "update_slot", "move_slot", "merge_slot", "place", "place_group",
           "set_attributes", "confirm_estimate", "co_place", "ungroup", "remove_placement",
           "reattach", "acknowledge")
OWNERS = ("list_views", "create_view", "update_view", "delete_view")


def test_signatures_no_by_at_and_writer_second():
    n = 0
    for name, fn in inspect.getmembers(S, inspect.isfunction):
        if fn.__module__ != S.__name__:
            continue
        for p in inspect.signature(fn).parameters:
            assert not (p.endswith("_by") or p.endswith("_at")), (name, p)
        n += 1
    assert n > 30
    for name in WRITERS:
        assert list(inspect.signature(getattr(S, name)).parameters)[1] == "writer", name
    for name in OWNERS:
        assert list(inspect.signature(getattr(S, name)).parameters)[1] == "owner", name


def test_delete_from_only_in_delete_view():
    src = (ROOT / "mh2" / "seq_store.py").read_text()
    assert len(re.findall(r"DELETE\s+FROM", src, re.I)) == 1
    assert re.search(r"DELETE\s+FROM", inspect.getsource(S.delete_view), re.I)


def test_no_fastapi_and_no_mh2db_access():
    import ast
    tree = ast.parse((ROOT / "mh2" / "seq_store.py").read_text())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add((node.module or "").split(".")[0])
    assert mods <= {"__future__", "json", "sqlite3", "contextlib", "datetime", "pathlib"}, mods


def _main():
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok", name)
            except Exception as e:  # noqa: BLE001
                import traceback
                failed += 1
                print("FAIL", name, repr(e))
                traceback.print_exc()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
