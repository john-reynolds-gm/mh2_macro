"""
seq_service.py -- composition layer: R's reads over mh2.db + W's store over
mh2_seq.db.  One function per route (docs/seq_v1_contract.md §3.9, §4).

No FastAPI import (so the sandbox can run it end to end) and no SQL (all SQL
lives in seq_store; mh2.db is reached only through mh2.seq_read).  Every
function opens its own connections and closes them in `finally`.  It raises
seq_store.SeqError subclasses only: ValueError/KeyError from R become
NotFound / Invalid('invalid').  A StaleRevision from any write gets
extra["sequence"] = the current SequenceView before it is re-raised.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from mh2 import grade_kind, seq_guardrail, seq_read, seq_reconcile, seq_store
from mh2.seq_store import (Conflict, Invalid, NeedsConfirm, NotFound,  # noqa: F401
                           SeqError, StaleRevision)

MAX_EVENTS = 200
_SLICE_BADGE_CODES = ("bridge", "grade_changed", "before_predecessor",
                      "predecessor_unplaced")


@dataclass(frozen=True)
class Ctx:
    mh2_path: Path
    seq_path: Path

    @classmethod
    def from_config(cls) -> "Ctx":
        import config
        return cls(Path(config.DB), Path(config.SEQ_DB))


# ---------------------------------------------------------------- plumbing

@contextmanager
def _cons(ctx: Ctx):
    seq = seq_store.connect(ctx.seq_path)
    mh2 = None
    try:
        seq_store.ensure_schema(seq)
        mh2 = seq_read.connect_ro(ctx.mh2_path)
        yield mh2, seq
    finally:
        if mh2 is not None:
            mh2.close()
        seq.close()


def _grade(mh2, token) -> str:
    try:
        return seq_read.normalize_grade(mh2, token)
    except ValueError:
        raise NotFound(f"unknown grade {token!r}")


def _facts_one(mh2, grade, source_key):
    return seq_read.node_facts(mh2, grade, [source_key]).get(source_key)


def _snap(mh2, grade, source_key) -> dict:
    f = _facts_one(mh2, grade, source_key)
    if f is None:
        raise NotFound(f"node {source_key} is not in the ladders")
    return {"source_key": f["source_key"], "node_id": f["node_id"],
            "node_text": f["node_text"], "source_file": f["source_file"],
            "stem_id": f["stem_id"], "concept_skill": f["concept_skill"],
            "state": f["state"], "requires_confirm": bool(f["requires_confirm"]),
            "period_hint": f["period_hint"]}


def _badge(code, tier, label, detail=None, refs=None):
    return {"code": code, "tier": tier, "label": label, "detail": detail,
            "refs": list(refs or []), "partners": []}


# -------------------------------------------------------- the sequence view

def _empty_view(mh2, grade) -> dict:
    return {"grade": grade, "ladders_last_read": seq_read.ladders_last_read(mh2),
            "sequence": None, "modules": [], "placed_index": {}, "slice_badges": {},
            "guardrail": seq_guardrail.compute([]), "attention": []}


def _gr_input(placements):
    return [{"calibration": p["calibration"], "period_estimate": p["period_estimate"],
             "estimate_source": p["estimate_source"]}
            for p in placements]


def build_sequence_view(seq_con, mh2_con, sequence_id, grade) -> dict:
    """Contract §3.9 'How build_sequence_view is built'."""
    if sequence_id is None:
        return _empty_view(mh2_con, grade)
    tree = seq_store.load_tree(seq_con, sequence_id)
    seq = tree["sequence"]
    flat = [(m, s, p) for m in tree["modules"] for s in m["slots"] for p in s["placements"]]
    keys = [p["source_key"] for _, _, p in flat]
    facts = seq_read.node_facts(mh2_con, grade, keys) if keys else {}
    status = {p["placement_id"]: seq_reconcile.derive_status(p, facts.get(p["source_key"]))
              for _, _, p in flat}
    positions = {p["source_key"]: (m["position"], s["position"])
                 for m, s, p in flat if status[p["placement_id"]]["status"] != "orphaned"}
    ob = seq_read.ordering_badges(positions, facts) if positions else {}
    overlay = seq_store.placement_index(seq_con, exclude_grade=grade)
    active_keys = set(keys)

    placed_index, slice_badges, attention = {}, {}, []
    module_views = []
    for m in tree["modules"]:
        slot_views = []
        for s in m["slots"]:
            pviews = []
            for p in s["placements"]:
                key = p["source_key"]
                st = status[p["placement_id"]]
                f = facts.get(key)
                orphan = st["status"] == "orphaned"
                is_bridge = (not orphan) and bool(f and f["requires_confirm"])
                badges = []
                if orphan:
                    badges.append(_badge(
                        "orphaned", "structural", "orphaned",
                        "Node no longer in the ladder as placed; was: "
                        f"{p['node_text_seen']} ({p['ladder_file_seen']})"))
                if st["status"] == "grade_changed":
                    badges.append(_badge(
                        "grade_changed", "structural",
                        f"was {p['grade_kind_seen']} → now {st['state_now']}",
                        "The grade ruling changed since this was placed"))
                if is_bridge:
                    badges.append(_badge(
                        "bridge", "structural", f"bridge · {st['state_now']}",
                        "Placed outside this grade's inventory on purpose"))
                if st["relabelled"]:
                    r = st["relabel"]
                    bits = []
                    if r["concept_skill_seen"] != r["concept_skill_now"]:
                        bits.append(f"Concept/skill was \"{r['concept_skill_seen']}\"")
                    if r["ladder_file_seen"] != r["ladder_file_now"]:
                        bits.append(f"Ladder file was {r['ladder_file_seen']}")
                    badges.append(_badge("relabelled", "info", "heading changed",
                                         "; ".join(bits)))
                badges.extend(ob.get(key, []))
                cs_seen = p["concept_skill_seen"]
                if f:
                    cs_disp = f["concept_skill_display"]
                elif cs_seen is not None:
                    cs_disp = seq_read.display_label(cs_seen)
                else:
                    cs_disp = None
                pv = {
                    "placement_id": p["placement_id"], "rev": p["rev"],
                    "order_in_slot": p["order_in_slot"], "source_key": key,
                    "node_id": f["node_id"] if f else None,
                    "node_id_seen": p["node_id_seen"],
                    "node_text": f["node_text"] if f else p["node_text_seen"],
                    "node_text_seen": p["node_text_seen"],
                    "stem_id": f["stem_id"] if f else p["stem_id_seen"],
                    "stem_name": f["stem_name"] if f else None,
                    "concept_skill_display": cs_disp,
                    "ladder_file_seen": p["ladder_file_seen"],
                    "grade_kind_seen": p["grade_kind_seen"],
                    "state_now": st["state_now"], "status": st["status"],
                    "relabelled": st["relabelled"], "relabel": st["relabel"],
                    "is_bridge": is_bridge, "calibration": p["calibration"],
                    "period_estimate": p["period_estimate"],
                    "estimate_source": p["estimate_source"],
                    "period_hint": f["period_hint"] if f else None,
                    "period_hint_seen": p["period_hint_seen"],
                    "differentiation_note": p["differentiation_note"],
                    "placed_by": p["placed_by"], "placed_at": p["placed_at"],
                    "updated_by": p["updated_by"], "updated_at": p["updated_at"],
                    "placed_elsewhere": overlay.get(key, []), "badges": badges,
                }
                pviews.append(pv)
                placed_index[key] = {
                    "placement_id": p["placement_id"], "module_id": m["module_id"],
                    "module_title": m["title"], "module_position": m["position"],
                    "slot_id": s["slot_id"], "slot_position": s["position"],
                    "status": st["status"], "is_bridge": is_bridge}
                sb = [b for b in badges if b["code"] in _SLICE_BADGE_CODES]
                if sb:
                    slice_badges[key] = sb
                if st["status"] in ("orphaned", "grade_changed"):
                    sugg = []
                    if orphan:
                        sugg = seq_read.suggest_successors(
                            mh2_con, source_key_seen=key,
                            node_text_seen=p["node_text_seen"],
                            stem_id_seen=p["stem_id_seen"],
                            concept_skill_seen=cs_seen, exclude_keys=active_keys)
                    attention.append({
                        "placement_id": p["placement_id"], "rev": p["rev"],
                        "module_id": m["module_id"], "module_title": m["title"],
                        "module_position": m["position"], "slot_id": s["slot_id"],
                        "slot_position": s["position"], "status": st["status"],
                        "source_key": key, "node_text_seen": p["node_text_seen"],
                        "ladder_file_seen": p["ladder_file_seen"],
                        "stem_id_seen": p["stem_id_seen"],
                        "grade_kind_seen": p["grade_kind_seen"],
                        "state_now": st["state_now"], "suggestions": sugg,
                        "actions": ["reattach", "remove"] if orphan
                        else ["acknowledge", "remove"]})
            slot_views.append({"slot_id": s["slot_id"], "label": s["label"],
                               "order_key": s["order_key"], "position": s["position"],
                               "placements": pviews})
        mod_pl = [p for s in m["slots"] for p in s["placements"]]
        module_views.append({
            "module_id": m["module_id"], "title": m["title"], "note": m["note"],
            "order_key": m["order_key"], "position": m["position"],
            "guardrail": seq_guardrail.compute(_gr_input(mod_pl)), "slots": slot_views})
    attention.sort(key=lambda a: (a["module_position"], a["slot_position"]))
    return {
        "grade": grade, "ladders_last_read": seq_read.ladders_last_read(mh2_con),
        "sequence": {k: seq[k] for k in ("sequence_id", "grade", "title", "owner", "note",
                                         "rev", "created_by", "created_at")},
        "modules": module_views, "placed_index": placed_index,
        "slice_badges": slice_badges,
        "guardrail": seq_guardrail.compute(_gr_input([p for _, _, p in flat])),
        "attention": attention}


def _view_for(seq, mh2, sequence_id) -> dict:
    grade = seq_store.get_sequence(seq, sequence_id)["grade"]
    return build_sequence_view(seq, mh2, sequence_id, grade)


def _write(ctx, kind, row_id, op) -> dict:
    """Run `op(seq_con, mh2_con, grade) -> result dict` and wrap the answer.
    `kind/row_id` locate the owning sequence ('sequence' | 'module' | 'slot' |
    'placement')."""
    with _cons(ctx) as (mh2, seq):
        sid = seq_store.sequence_id_of(seq, kind, row_id)
        grade = seq_store.get_sequence(seq, sid)["grade"]
        try:
            out = op(seq, mh2, grade)
        except seq_store.StaleRevision as e:
            e.extra["sequence"] = build_sequence_view(seq, mh2, sid, grade)
            raise
        return {"sequence": build_sequence_view(seq, mh2, sid, grade), "result": out}


# --------------------------------------------------------------------- reads

def grades(ctx: Ctx) -> dict:
    with _cons(ctx) as (mh2, seq):
        summaries = seq_store.sequence_summaries(seq)
        infos = seq_read.list_grades(mh2)
        for g in infos:
            g["sequence"] = summaries.get(g["grade"])
        return {"grades": infos, "kind_source": grade_kind.kind_source(mh2),
                "ladders_last_read": seq_read.ladders_last_read(mh2)}


def get_slice(ctx: Ctx, grade: str) -> dict:
    with _cons(ctx) as (mh2, seq):
        g = _grade(mh2, grade)
        overlay = seq_store.placement_index(seq, exclude_grade=g)
        return seq_read.build_slice(mh2, g, overlay=overlay)


def get_node(ctx: Ctx, source_key: str, grade: str) -> dict:
    with _cons(ctx) as (mh2, seq):
        g = _grade(mh2, grade)
        overlay = seq_store.placement_index(seq, exclude_grade=g)
        try:
            return seq_read.node_drawer(mh2, source_key, g, overlay=overlay)
        except KeyError:
            raise NotFound(f"node {source_key} not found")


def get_compare(ctx: Ctx, keys: list, grade: str) -> dict:
    with _cons(ctx) as (mh2, seq):
        g = _grade(mh2, grade)
        try:
            return seq_read.build_compare(mh2, list(keys), g)
        except KeyError as e:
            raise NotFound(f"node {e.args[0] if e.args else ''} not found")
        except ValueError as e:
            raise Invalid("invalid", str(e))


def get_sequence(ctx: Ctx, grade: str) -> dict:
    with _cons(ctx) as (mh2, seq):
        g = _grade(mh2, grade)
        return build_sequence_view(seq, mh2, seq_store.active_sequence_id(seq, g), g)


def get_guardrail(ctx: Ctx, grade: str) -> dict:
    view = get_sequence(ctx, grade)
    seq = view["sequence"]
    return {"sequence_id": seq["sequence_id"] if seq else None,
            "sequence": view["guardrail"],
            "modules": [{"module_id": m["module_id"], "title": m["title"],
                         "guardrail": m["guardrail"]} for m in view["modules"]]}


def get_attention(ctx: Ctx, grade: str) -> dict:
    view = get_sequence(ctx, grade)
    seq = view["sequence"]
    return {"sequence_id": seq["sequence_id"] if seq else None, "items": view["attention"]}


def get_events(ctx: Ctx, sequence_id: int, limit: int = 50, before=None) -> dict:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise Invalid("invalid", "limit must be a positive integer")
    limit = min(limit, MAX_EVENTS)
    with _cons(ctx) as (mh2, seq):
        seq_store.get_sequence(seq, sequence_id)  # NotFound
        rows = seq_store.list_events(seq, sequence_id, limit + 1, before)
        more = len(rows) > limit
        rows = rows[:limit]
        return {"events": rows, "next_before": rows[-1]["event_id"] if more else None}


# ------------------------------------------------------------------- writes

def create_sequence(ctx: Ctx, writer: str, grade: str, title: str, note=None) -> dict:
    with _cons(ctx) as (mh2, seq):
        g = _grade(mh2, grade)
        sid = seq_store.create_sequence(seq, writer, g, title, note)
        return {"sequence": build_sequence_view(seq, mh2, sid, g),
                "result": {"sequence_id": sid}}


def update_sequence(ctx: Ctx, writer: str, sequence_id: int, expected_rev: int,
                    fields: dict) -> dict:
    kw = {k: fields[k] for k in ("title", "owner", "note", "archived") if k in fields}
    if kw.get("title", "") is None:
        raise Invalid("invalid", "title must not be empty")

    def op(seq, mh2, grade):
        seq_store.update_sequence(seq, writer, sequence_id, expected_rev, **kw)
        return {}
    return _write(ctx, "sequence", sequence_id, op)


def create_module(ctx: Ctx, writer: str, sequence_id: int, expected_rev: int,
                  title: str, note=None) -> dict:
    def op(seq, mh2, grade):
        return {"module_id": seq_store.create_module(seq, writer, sequence_id,
                                                     expected_rev, title, note)}
    return _write(ctx, "sequence", sequence_id, op)


def update_module(ctx: Ctx, writer: str, module_id: int, expected_rev: int,
                  fields: dict) -> dict:
    kw = {k: fields[k] for k in ("title", "note") if k in fields}
    if kw.get("title", "") is None:
        raise Invalid("invalid", "title must not be empty")

    def op(seq, mh2, grade):
        seq_store.update_module(seq, writer, module_id, expected_rev, **kw)
        return {}
    return _write(ctx, "module", module_id, op)


def move_module(ctx: Ctx, writer: str, module_id: int, expected_rev: int,
                direction: str) -> dict:
    def op(seq, mh2, grade):
        seq_store.move_module(seq, writer, module_id, expected_rev, direction)
        return {}
    return _write(ctx, "module", module_id, op)


def remove_module(ctx: Ctx, writer: str, module_id: int, expected_rev: int) -> dict:
    def op(seq, mh2, grade):
        seq_store.remove_module(seq, writer, module_id, expected_rev)
        return {}
    return _write(ctx, "module", module_id, op)


def update_slot(ctx: Ctx, writer: str, slot_id: int, expected_rev: int, label) -> dict:
    def op(seq, mh2, grade):
        seq_store.update_slot(seq, writer, slot_id, expected_rev, label)
        return {}
    return _write(ctx, "slot", slot_id, op)


def move_slot(ctx: Ctx, writer: str, slot_id: int, expected_rev: int,
              direction=None, to_module_id=None) -> dict:
    def op(seq, mh2, grade):
        seq_store.move_slot(seq, writer, slot_id, expected_rev, direction=direction,
                            to_module_id=to_module_id)
        return {}
    return _write(ctx, "slot", slot_id, op)


def merge_slot(ctx: Ctx, writer: str, slot_id: int, expected_rev: int,
               into_slot_id: int) -> dict:
    def op(seq, mh2, grade):
        seq_store.merge_slot(seq, writer, slot_id, expected_rev, into_slot_id)
        return {"slot_id": into_slot_id}
    return _write(ctx, "slot", slot_id, op)


def create_slot_group(ctx: Ctx, writer: str, sequence_id: int, expected_rev: int,
                      module_id: int, source_keys: list, confirm_off_grade: bool = False,
                      differentiation_notes: dict | None = None) -> dict:
    if not 1 <= len(source_keys) <= 4:
        raise Invalid("invalid", "a slot group takes 1 to 4 nodes")

    def op(seq, mh2, grade):
        snaps = [_snap(mh2, grade, k) for k in source_keys]
        return seq_store.place_group(
            seq, writer, sequence_id, expected_rev, snaps, module_id=module_id,
            confirm_off_grade=confirm_off_grade,
            differentiation_notes=differentiation_notes)
    return _write(ctx, "sequence", sequence_id, op)


def place(ctx: Ctx, writer: str, sequence_id: int, expected_rev: int, module_id: int,
          source_key: str, slot_id=None, after_slot_id=None, differentiation_note=None,
          confirm_off_grade: bool = False) -> dict:
    def op(seq, mh2, grade):
        snap = _snap(mh2, grade, source_key)
        res = seq_store.place(
            seq, writer, sequence_id, expected_rev, snap, module_id=module_id,
            slot_id=slot_id, after_slot_id=after_slot_id,
            differentiation_note=differentiation_note,
            confirm_off_grade=confirm_off_grade)
        overlay = seq_store.placement_index(seq, exclude_grade=grade)
        return {**res, "placed_elsewhere": overlay.get(source_key, [])}
    return _write(ctx, "sequence", sequence_id, op)


def update_placement(ctx: Ctx, writer: str, placement_id: int, expected_rev: int,
                     changes: dict) -> dict:
    keys = ("calibration", "period_estimate", "differentiation_note")
    ch = {k: changes[k] for k in keys if k in changes}
    if "confirm_estimate" in changes:
        if changes["confirm_estimate"] is not True or ch:
            raise Invalid("invalid", "confirm_estimate must be true and sent on its own")

        def confirm(seq, mh2, grade):
            seq_store.confirm_estimate(seq, writer, placement_id, expected_rev)
            return {"placement_id": placement_id}
        return _write(ctx, "placement", placement_id, confirm)

    def op(seq, mh2, grade):
        hint_text = None
        if "period_estimate" in ch:
            p = seq_store.get_placement(seq, placement_id)
            f = _facts_one(mh2, grade, p["source_key"])
            hint = f["period_hint"] if f else None
            hint_text = hint["text"] if hint else None
        seq_store.set_attributes(seq, writer, placement_id, expected_rev, ch,
                                 period_hint_seen=hint_text)
        return {"placement_id": placement_id}
    return _write(ctx, "placement", placement_id, op)


def co_place(ctx: Ctx, writer: str, placement_id: int, expected_rev: int,
             target_slot_id: int) -> dict:
    def op(seq, mh2, grade):
        seq_store.co_place(seq, writer, placement_id, expected_rev, target_slot_id)
        return {"slot_id": target_slot_id}
    return _write(ctx, "placement", placement_id, op)


def ungroup(ctx: Ctx, writer: str, placement_id: int, expected_rev: int) -> dict:
    def op(seq, mh2, grade):
        return {"slot_id": seq_store.ungroup(seq, writer, placement_id, expected_rev)}
    return _write(ctx, "placement", placement_id, op)


def remove_placement(ctx: Ctx, writer: str, placement_id: int, expected_rev: int,
                     reason=None) -> dict:
    def op(seq, mh2, grade):
        seq_store.remove_placement(seq, writer, placement_id, expected_rev, reason)
        return {}
    return _write(ctx, "placement", placement_id, op)


def reattach(ctx: Ctx, writer: str, placement_id: int, expected_rev: int,
             source_key: str) -> dict:
    def op(seq, mh2, grade):
        seq_store.get_placement(seq, placement_id)
        snap = _snap(mh2, grade, source_key)
        seq_store.reattach(seq, writer, placement_id, expected_rev, snap)
        return {"placement_id": placement_id}
    return _write(ctx, "placement", placement_id, op)


def acknowledge(ctx: Ctx, writer: str, placement_id: int, expected_rev: int) -> dict:
    def op(seq, mh2, grade):
        p = seq_store.get_placement(seq, placement_id)
        snap = _snap(mh2, grade, p["source_key"])  # NotFound if the node is gone
        seq_store.acknowledge(seq, writer, placement_id, expected_rev, snap)
        return {"placement_id": placement_id}
    return _write(ctx, "placement", placement_id, op)


# --------------------------------------------------------------------- views

def list_views(ctx: Ctx, owner: str, grade=None) -> dict:
    with _cons(ctx) as (mh2, seq):
        g = _grade(mh2, grade) if grade is not None else None
        return {"views": seq_store.list_views(seq, owner, g)}


def create_view(ctx: Ctx, owner: str, grade: str, name: str, state: dict) -> dict:
    with _cons(ctx) as (mh2, seq):
        return seq_store.create_view(seq, owner, _grade(mh2, grade), name, state)


def update_view(ctx: Ctx, owner: str, view_id: int, fields: dict) -> dict:
    kw = {k: fields[k] for k in ("name", "state") if k in fields}
    with _cons(ctx) as (mh2, seq):
        return seq_store.update_view(seq, owner, view_id, **kw)


def delete_view(ctx: Ctx, owner: str, view_id: int) -> dict:
    with _cons(ctx) as (mh2, seq):
        seq_store.delete_view(seq, owner, view_id)
        return {"deleted": view_id}
