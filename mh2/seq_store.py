"""
seq_store.py -- the ONLY module that issues SQL against the placement tables
(grade_sequence, module, slot, placement, placement_event, saved_view).

Contract: docs/seq_v1_contract.md §3.5 (binding); data model:
docs/seq_data_model_rev1.md.  Conventions follow mh2/review_store.py:

* `writer` (or `owner` for saved views) is ALWAYS the second positional
  argument.  No function accepts a `*_by` or `*_at` value; the store stamps
  both itself, so a route can never pass a client-supplied identity or time.
* Every mutating function (except the saved-view ones) runs in one
  BEGIN IMMEDIATE ... COMMIT, with ROLLBACK on any exception.
* Optimistic concurrency: structural writes check `grade_sequence.rev ==
  expected_rev` and bump it; `set_attributes` checks `placement.rev`.
* Soft delete only (removed_at/removed_by); the one hard delete is a saved
  view's (see delete_view).  Every change appends one `placement_event` row
  (the multi-attribute PATCH is the single documented exception).
* Order keys are sparse integers, step ORDER_STEP; a container is renumbered
  only when an insert finds a gap under 2 (one `renumber` event).

No FastAPI import, no mh2.db access.  Nothing here knows about node facts:
`seq_service` passes a NodeSnap built from mh2.seq_read.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ORDER_STEP = 1024
CALIBRATIONS = ("deep", "functional", "illuminating")
UNSET = object()  # sentinel for "field not supplied" in update functions

GRADES = ("PK", "K", "1", "2", "3", "4", "5", "6", "7", "8", "A1")
_KINDS = ("core", "span", "state_extension", "unconfirmed",
          "off_grade", "leaf", "no_grade", "unknown")
_PERIOD_MAX = 200


# ------------------------------------------------------------------- errors

class SeqError(Exception):
    status = 500
    code = "error"

    def __init__(self, code=None, message=None, extra=None):
        if code is not None:
            self.code = code
        self.message = message or self.code.replace("_", " ")
        self.extra = dict(extra or {})
        super().__init__(f"{self.code}: {self.message}")


class NotFound(SeqError):
    status = 404
    code = "not_found"

    def __init__(self, message=None, extra=None):
        super().__init__(None, message or "not found", extra)


class Conflict(SeqError):
    status = 409


class StaleRevision(Conflict):
    code = "stale_revision"

    def __init__(self, current_rev, message=None, extra=None):
        ex = dict(extra or {})
        ex["current_rev"] = current_rev
        super().__init__(None, message or
                         "This was changed by someone else; reload and retry", ex)


class Invalid(SeqError):
    status = 422


class NeedsConfirm(Invalid):
    code = "confirm_off_grade_required"

    def __init__(self, source_keys, states, message=None):
        super().__init__(None, message or
                         "Placing outside this grade's inventory needs confirmation",
                         {"source_keys": list(source_keys), "states": dict(states)})


# ------------------------------------------------------------------ plumbing

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


_TABLES = ("placement_schema_meta", "grade_sequence", "module", "slot", "placement",
           "placement_event", "saved_view")


_SCHEMA = Path(__file__).with_name("schema_placement.sql")


def ensure_schema(con: sqlite3.Connection) -> None:
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not all(t in have for t in _TABLES):
        con.executescript(_SCHEMA.read_text())
    if _needs_v2(con):  # fast path: two catalogue reads, no write lock
        _migrate_v2(con)


def _needs_v2(con):
    """(add estimate_source, rebuild placement_event) still to do on this database."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(placement)")}
    ev = con.execute("SELECT sql FROM sqlite_master WHERE type='table'"
                     " AND name='placement_event'").fetchone()[0]
    need = ("estimate_source" not in cols, "'confirm_period'" not in ev)
    return need if any(need) else None


def _migrate_v2(con):
    """v1 -> v2 (rulings O8/O9).  CREATE IF NOT EXISTS never alters a table, so:
    add placement.estimate_source (an existing estimate was typed by a person:
    'builder'), and rebuild placement_event so its action CHECK admits
    'confirm_period' (SQLite cannot alter a CHECK).  Event rows, ids and the
    AUTOINCREMENT high-water mark are kept.  Idempotent: re-checked under the
    write lock in case another process migrated first."""
    sql = _SCHEMA.read_text()
    start = sql.index("CREATE TABLE IF NOT EXISTS placement_event (")
    ev_table = sql[start:sql.index("\n);", start) + 3]
    ev_indexes = [ln for ln in sql.splitlines()
                  if ln.startswith("CREATE INDEX") and " ON placement_event(" in ln]
    with _tx(con):
        need = _needs_v2(con)
        if need is None:
            return
        add_col, rebuild_events = need
        if add_col:
            con.execute("ALTER TABLE placement ADD COLUMN estimate_source TEXT CHECK"
                        " (estimate_source IS NULL OR estimate_source IN ('ladder','builder'))")
            con.execute("UPDATE placement SET estimate_source='builder'"
                        " WHERE period_estimate IS NOT NULL")
        if rebuild_events:
            con.execute("ALTER TABLE placement_event RENAME TO placement_event_v1")
            con.execute(ev_table)
            con.execute("INSERT INTO placement_event SELECT * FROM placement_event_v1")
            con.execute("UPDATE sqlite_sequence SET seq = MAX(seq, (SELECT seq FROM sqlite_sequence"
                        " WHERE name='placement_event_v1')) WHERE name='placement_event'")
            con.execute("DROP TABLE placement_event_v1")
            for stmt in ev_indexes:
                con.execute(stmt)
        con.execute("UPDATE placement_schema_meta SET version=2"
                    " WHERE component='placement' AND version < 2")


def sequence_id_of(con, kind, row_id) -> int:
    """Owning sequence of a module / slot / placement / sequence id, removed
    rows included (the service uses it to attach the current view to a 409)."""
    if kind == "sequence":
        return _seq_row(con, row_id)["sequence_id"]
    table, pk = {"module": ("module", "module_id"), "slot": ("slot", "slot_id"),
                 "placement": ("placement", "placement_id")}[kind]
    row = con.execute(f"SELECT sequence_id FROM {table} WHERE {pk}=?", (row_id,)).fetchone()
    if row is None:
        raise NotFound(f"{kind} not found")
    return row[0]


@contextmanager
def _tx(con):
    con.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")


def _one(con, sql, params=()):
    row = con.execute(sql, params).fetchone()
    return dict(row) if row else None


def _event(con, sequence_id, action, actor, now, *, module_id=None, slot_id=None,
           placement_id=None, before=None, after=None):
    con.execute(
        "INSERT INTO placement_event (sequence_id, module_id, slot_id, placement_id,"
        " action, actor, at, before_json, after_json) VALUES (?,?,?,?,?,?,?,?,?)",
        (sequence_id, module_id, slot_id, placement_id, action, actor, now,
         None if before is None else json.dumps(before, sort_keys=True),
         None if after is None else json.dumps(after, sort_keys=True)))


def _require_text(value, what):
    if not isinstance(value, str) or not value.strip():
        raise Invalid("invalid", f"{what} must not be empty")
    return value.strip()


def _opt_text(value):
    """None or a string; blank strings clear to None."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise Invalid("invalid", "expected text")
    value = value.strip()
    return value or None


def _check_expected(expected_rev):
    if isinstance(expected_rev, bool) or not isinstance(expected_rev, int):
        raise Invalid("invalid", "expected_rev must be an integer")


def _seq_row(con, sequence_id):
    row = _one(con, "SELECT * FROM grade_sequence WHERE sequence_id=?", (sequence_id,))
    if row is None:
        raise NotFound("sequence not found")
    return row


def _check_seq_rev(con, sequence_id, expected_rev):
    _check_expected(expected_rev)
    row = _seq_row(con, sequence_id)
    if row["rev"] != expected_rev:
        raise StaleRevision(row["rev"])
    return row


def _bump_seq(con, sequence_id):
    con.execute("UPDATE grade_sequence SET rev = rev + 1 WHERE sequence_id=?",
                (sequence_id,))


def _module_row(con, module_id):
    row = _one(con, "SELECT * FROM module WHERE module_id=? AND removed_at IS NULL",
               (module_id,))
    if row is None:
        raise NotFound("module not found")
    return row


def _slot_row(con, slot_id):
    row = _one(con, "SELECT * FROM slot WHERE slot_id=? AND removed_at IS NULL",
               (slot_id,))
    if row is None:
        raise NotFound("slot not found")
    return row


def _placement_row(con, placement_id):
    row = _one(con, "SELECT * FROM placement WHERE placement_id=? AND removed_at IS NULL",
               (placement_id,))
    if row is None:
        raise NotFound("placement not found")
    return row


def _same_seq(row, sequence_id, what):
    if row["sequence_id"] != sequence_id:
        raise Invalid("cross_sequence", f"{what} belongs to another sequence")


# ---------------------------------------------------------------- order keys

_KIND = {
    "module": ("module", "module_id", "order_key", "sequence_id"),
    "slot": ("slot", "slot_id", "order_key", "module_id"),
    "placement": ("placement", "placement_id", "order_in_slot", "slot_id"),
}


def _siblings(con, kind, container_id):
    table, pk, key, cont = _KIND[kind]
    return [(r[0], r[1]) for r in con.execute(
        f"SELECT {pk}, {key} FROM {table} WHERE {cont}=? AND removed_at IS NULL"
        f" ORDER BY {key}, {pk}", (container_id,))]


def _renumber(con, kind, container_id, sequence_id, writer, now):
    table, pk, key, cont = _KIND[kind]
    sibs = _siblings(con, kind, container_id)
    before, after = {}, {}
    for i, (pid, old) in enumerate(sibs, start=1):
        new = ORDER_STEP * i
        before[str(pid)] = old
        after[str(pid)] = new
        con.execute(f"UPDATE {table} SET {key}=?, rev=rev+1 WHERE {pk}=?", (new, pid))
    _event(con, sequence_id, "renumber", writer, now,
           module_id=container_id if kind == "slot" else None,
           slot_id=container_id if kind == "placement" else None,
           before={"kind": kind, "container_id": container_id, "keys": before},
           after={"kind": kind, "container_id": container_id, "keys": after})


def _alloc_key(con, kind, container_id, sequence_id, writer, now, *,
               where="end", after_pk=None):
    """New order key in a container: 'end' (max+STEP), 'start', or ('after', pk).
    Renumbers that container (and logs one event) only when the gap is < 2."""
    for attempt in (0, 1):
        sibs = _siblings(con, kind, container_id)
        if where == "end" or (where == "after" and after_pk is None):
            return (sibs[-1][1] if sibs else 0) + ORDER_STEP
        if where == "start":
            lo, hi = 0, (sibs[0][1] if sibs else None)
        else:
            idx = [p for p, _ in sibs].index(after_pk)
            lo = sibs[idx][1]
            hi = sibs[idx + 1][1] if idx + 1 < len(sibs) else None
        if hi is None:
            return lo + ORDER_STEP
        if hi - lo >= 2:
            return (lo + hi) // 2
        if attempt == 0:
            _renumber(con, kind, container_id, sequence_id, writer, now)
    raise AssertionError("renumber did not open a gap")  # pragma: no cover


def _swap_with_neighbour(con, kind, container_id, pk_value, direction):
    """Swap the order key with the adjacent active sibling; returns the
    neighbour's pk, or None at the edge."""
    table, pk, key, _ = _KIND[kind]
    sibs = _siblings(con, kind, container_id)
    ids = [p for p, _ in sibs]
    i = ids.index(pk_value)
    j = i - 1 if direction == "up" else i + 1
    if j < 0 or j >= len(sibs):
        return None
    a, b = sibs[i], sibs[j]
    con.execute(f"UPDATE {table} SET {key}=?, rev=rev+1 WHERE {pk}=?", (b[1], a[0]))
    con.execute(f"UPDATE {table} SET {key}=?, rev=rev+1 WHERE {pk}=?", (a[1], b[0]))
    return b[0]


def _check_direction(direction):
    if direction not in ("up", "down"):
        raise Invalid("invalid", "direction must be 'up' or 'down'")


def _drop_slot_if_empty(con, slot_id, writer, now):
    """Soft-remove a slot left with no active placement. Returns True if removed."""
    n = con.execute("SELECT COUNT(*) FROM placement WHERE slot_id=? AND removed_at IS NULL",
                    (slot_id,)).fetchone()[0]
    if n == 0:
        cur = con.execute(
            "UPDATE slot SET removed_at=?, removed_by=?, rev=rev+1"
            " WHERE slot_id=? AND removed_at IS NULL", (now, writer, slot_id))
        return cur.rowcount > 0
    return False


# ----------------------------------------------------------------- sequences

def create_sequence(con, writer, grade, title, note=None) -> int:
    if grade not in GRADES:
        raise Invalid("invalid", f"unknown grade {grade!r}")
    title = _require_text(title, "title")
    note = _opt_text(note)
    with _tx(con):
        existing = active_sequence_id(con, grade)
        if existing is not None:
            raise Conflict("sequence_exists", "This grade already has an active sequence",
                           {"sequence_id": existing})
        now = _now()
        cur = con.execute(
            "INSERT INTO grade_sequence (grade, title, owner, note, created_by, created_at)"
            " VALUES (?,?,?,?,?,?)", (grade, title, writer, note, writer, now))
        sid = cur.lastrowid
        _event(con, sid, "sequence_create", writer, now,
               after={"grade": grade, "title": title, "owner": writer, "note": note})
    return sid


def get_sequence(con, sequence_id) -> dict:
    return _seq_row(con, sequence_id)


def active_sequence_id(con, grade):
    row = con.execute("SELECT MIN(sequence_id) FROM grade_sequence"
                      " WHERE grade=? AND archived_at IS NULL", (grade,)).fetchone()
    return row[0]


def update_sequence(con, writer, sequence_id, expected_rev, *, title=UNSET, owner=UNSET,
                    note=UNSET, archived=UNSET) -> None:
    with _tx(con):
        cur = _check_seq_rev(con, sequence_id, expected_rev)
        now = _now()
        sets, before, after = {}, {}, {}
        if title is not UNSET:
            title = _require_text(title, "title")
            if title != cur["title"]:
                sets["title"] = title
        if owner is not UNSET:
            owner = _opt_text(owner)
            if owner != cur["owner"]:
                sets["owner"] = owner
        if note is not UNSET:
            note = _opt_text(note)
            if note != cur["note"]:
                sets["note"] = note
        archiving = None
        if archived is not UNSET and archived is not None:
            want = bool(archived)
            if want != (cur["archived_at"] is not None):
                archiving = want
        if title is UNSET and owner is UNSET and note is UNSET and archived is UNSET:
            raise Invalid("invalid", "no fields supplied")
        if not sets and archiving is None:
            return  # nothing changed: no bump, no event
        for k, v in sets.items():
            before[k] = cur[k]
            after[k] = v
        if archiving is True:
            sets["archived_at"], sets["archived_by"] = now, writer
            before["archived"], after["archived"] = False, True
        elif archiving is False:
            other = active_sequence_id(con, cur["grade"])
            if other is not None and other != sequence_id:
                raise Conflict("sequence_exists",
                               "This grade already has an active sequence",
                               {"sequence_id": other})
            sets["archived_at"], sets["archived_by"] = None, None
            before["archived"], after["archived"] = True, False
        assign = ", ".join(f"{k}=?" for k in sets)
        con.execute(f"UPDATE grade_sequence SET {assign}, rev = rev + 1"
                    " WHERE sequence_id=?", (*sets.values(), sequence_id))
        action = "sequence_archive" if archiving is True else "sequence_update"
        _event(con, sequence_id, action, writer, now, before=before, after=after)


def sequence_summaries(con) -> dict:
    out = {}
    for r in con.execute(
            "SELECT s.sequence_id, s.grade, s.title, s.owner, s.rev, s.created_at,"
            " (SELECT COUNT(*) FROM module m WHERE m.sequence_id=s.sequence_id"
            "   AND m.removed_at IS NULL) AS n_modules,"
            " (SELECT COUNT(*) FROM placement p WHERE p.sequence_id=s.sequence_id"
            "   AND p.removed_at IS NULL) AS n_placements,"
            " (SELECT MAX(e.at) FROM placement_event e WHERE e.sequence_id=s.sequence_id)"
            "   AS last_at"
            " FROM grade_sequence s WHERE s.archived_at IS NULL"
            " ORDER BY s.sequence_id DESC"):
        # descending iteration so the LOWEST id per grade wins the final write
        out[r["grade"]] = {
            "sequence_id": r["sequence_id"], "grade": r["grade"], "title": r["title"],
            "owner": r["owner"], "rev": r["rev"], "n_modules": r["n_modules"],
            "n_placements": r["n_placements"],
            "updated_at": r["last_at"] or r["created_at"]}
    return out


# ------------------------------------------------------------------- modules

def create_module(con, writer, sequence_id, expected_rev, title, note=None) -> int:
    title = _require_text(title, "title")
    note = _opt_text(note)
    with _tx(con):
        _check_seq_rev(con, sequence_id, expected_rev)
        now = _now()
        key = _alloc_key(con, "module", sequence_id, sequence_id, writer, now)
        cur = con.execute(
            "INSERT INTO module (sequence_id, title, order_key, note, created_by,"
            " created_at) VALUES (?,?,?,?,?,?)",
            (sequence_id, title, key, note, writer, now))
        mid = cur.lastrowid
        _bump_seq(con, sequence_id)
        _event(con, sequence_id, "module_create", writer, now, module_id=mid,
               after={"title": title, "note": note, "order_key": key})
    return mid


def update_module(con, writer, module_id, expected_rev, *, title=UNSET, note=UNSET) -> None:
    with _tx(con):
        mod = _module_row(con, module_id)
        sid = mod["sequence_id"]
        _check_seq_rev(con, sid, expected_rev)
        if title is UNSET and note is UNSET:
            raise Invalid("invalid", "no fields supplied")
        now = _now()
        sets, before, after = {}, {}, {}
        if title is not UNSET:
            title = _require_text(title, "title")
            if title != mod["title"]:
                sets["title"] = title
        if note is not UNSET:
            note = _opt_text(note)
            if note != mod["note"]:
                sets["note"] = note
        if not sets:
            return
        for k, v in sets.items():
            before[k], after[k] = mod[k], v
        assign = ", ".join(f"{k}=?" for k in sets)
        con.execute(f"UPDATE module SET {assign}, rev = rev + 1 WHERE module_id=?",
                    (*sets.values(), module_id))
        _bump_seq(con, sid)
        _event(con, sid, "module_update", writer, now, module_id=module_id,
               before=before, after=after)


def move_module(con, writer, module_id, expected_rev, direction) -> None:
    _check_direction(direction)
    with _tx(con):
        mod = _module_row(con, module_id)
        sid = mod["sequence_id"]
        _check_seq_rev(con, sid, expected_rev)
        now = _now()
        nb = _swap_with_neighbour(con, "module", sid, module_id, direction)
        if nb is None:
            raise Invalid("at_edge", f"module is already {'first' if direction == 'up' else 'last'}")
        _bump_seq(con, sid)
        _event(con, sid, "module_move", writer, now, module_id=module_id,
               before={"order_key": mod["order_key"]},
               after={"direction": direction, "swapped_with": nb})


def remove_module(con, writer, module_id, expected_rev) -> None:
    with _tx(con):
        mod = _module_row(con, module_id)
        sid = mod["sequence_id"]
        _check_seq_rev(con, sid, expected_rev)
        n = con.execute(
            "SELECT COUNT(*) FROM placement p JOIN slot s ON s.slot_id=p.slot_id"
            " WHERE s.module_id=? AND p.removed_at IS NULL AND s.removed_at IS NULL",
            (module_id,)).fetchone()[0]
        if n:
            raise Conflict("module_not_empty", "Remove or move this module's placements first",
                           {"n_placements": n})
        now = _now()
        con.execute("UPDATE module SET removed_at=?, removed_by=?, rev=rev+1"
                    " WHERE module_id=?", (now, writer, module_id))
        _bump_seq(con, sid)
        _event(con, sid, "module_remove", writer, now, module_id=module_id,
               before={"title": mod["title"]})


# --------------------------------------------------------------------- slots

def update_slot(con, writer, slot_id, expected_rev, label) -> None:
    label = _opt_text(label)
    with _tx(con):
        slot = _slot_row(con, slot_id)
        sid = slot["sequence_id"]
        _check_seq_rev(con, sid, expected_rev)
        now = _now()
        con.execute("UPDATE slot SET label=?, rev=rev+1 WHERE slot_id=?", (label, slot_id))
        _bump_seq(con, sid)
        _event(con, sid, "slot_update", writer, now, module_id=slot["module_id"],
               slot_id=slot_id, before={"label": slot["label"]}, after={"label": label})


def move_slot(con, writer, slot_id, expected_rev, *, direction=None, to_module_id=None) -> None:
    if (direction is None) == (to_module_id is None):
        raise Invalid("invalid", "give exactly one of direction / to_module_id")
    if direction is not None:
        _check_direction(direction)
    with _tx(con):
        slot = _slot_row(con, slot_id)
        sid = slot["sequence_id"]
        _check_seq_rev(con, sid, expected_rev)
        now = _now()
        old_module = slot["module_id"]
        if direction is not None:
            nb = _swap_with_neighbour(con, "slot", old_module, slot_id, direction)
            if nb is not None:
                after = {"direction": direction, "swapped_with": nb,
                         "module_id": old_module}
            else:
                mods = [p for p, _ in _siblings(con, "module", sid)]
                i = mods.index(old_module)
                j = i - 1 if direction == "up" else i + 1
                if j < 0 or j >= len(mods):
                    raise Invalid("at_edge", "slot is already at the "
                                  + ("start" if direction == "up" else "end")
                                  + " of the sequence")
                target = mods[j]
                where = "end" if direction == "up" else "start"
                key = _alloc_key(con, "slot", target, sid, writer, now, where=where)
                con.execute("UPDATE slot SET module_id=?, order_key=?, rev=rev+1"
                            " WHERE slot_id=?", (target, key, slot_id))
                after = {"direction": direction, "module_id": target, "order_key": key}
        else:
            target_row = _module_row(con, to_module_id)
            _same_seq(target_row, sid, "target module")
            key = _alloc_key(con, "slot", to_module_id, sid, writer, now, where="end")
            con.execute("UPDATE slot SET module_id=?, order_key=?, rev=rev+1"
                        " WHERE slot_id=?", (to_module_id, key, slot_id))
            after = {"module_id": to_module_id, "order_key": key}
        _bump_seq(con, sid)
        _event(con, sid, "slot_move", writer, now, module_id=after["module_id"],
               slot_id=slot_id,
               before={"module_id": old_module, "order_key": slot["order_key"]},
               after=after)


def merge_slot(con, writer, slot_id, expected_rev, into_slot_id) -> None:
    with _tx(con):
        src = _slot_row(con, slot_id)
        dst = _slot_row(con, into_slot_id)
        sid = src["sequence_id"]
        _check_seq_rev(con, sid, expected_rev)
        _same_seq(dst, sid, "target slot")
        if slot_id == into_slot_id:
            raise Invalid("invalid", "cannot merge a slot into itself")
        now = _now()
        moved = [r[0] for r in con.execute(
            "SELECT placement_id FROM placement WHERE slot_id=? AND removed_at IS NULL"
            " ORDER BY order_in_slot, placement_id", (slot_id,))]
        for pid in moved:
            key = _alloc_key(con, "placement", into_slot_id, sid, writer, now, where="end")
            con.execute("UPDATE placement SET slot_id=?, order_in_slot=?, updated_by=?,"
                        " updated_at=?, rev=rev+1 WHERE placement_id=?",
                        (into_slot_id, key, writer, now, pid))
        con.execute("UPDATE slot SET removed_at=?, removed_by=?, rev=rev+1 WHERE slot_id=?",
                    (now, writer, slot_id))
        con.execute("UPDATE slot SET rev=rev+1 WHERE slot_id=?", (into_slot_id,))
        _bump_seq(con, sid)
        _event(con, sid, "slot_merge", writer, now, module_id=dst["module_id"],
               slot_id=into_slot_id,
               before={"slot_id": slot_id, "module_id": src["module_id"]},
               after={"into_slot_id": into_slot_id, "placement_ids": moved})


# ---------------------------------------------------------------- placements

def _check_snap(snap):
    for k in ("source_key", "node_text", "state"):
        if not snap.get(k):
            raise Invalid("invalid", f"snapshot is missing {k}")
    if snap["state"] not in _KINDS:
        raise Invalid("invalid", f"unknown state {snap['state']!r}")


def _active_placement_for(con, sequence_id, source_key):
    return _one(con, "SELECT placement_id FROM placement WHERE sequence_id=?"
                " AND source_key=? AND removed_at IS NULL", (sequence_id, source_key))


def _autofill(snap):
    """(period_estimate, period_hint_seen, estimate_source) for a new placement:
    the ladder hint's precomputed value (seq_read, rulings O8/O9), else blank."""
    hint = snap.get("period_hint")
    if hint and hint.get("value") is not None:
        return float(hint["value"]), hint["text"], "ladder"
    return None, None, None


def _insert_placement(con, writer, now, sequence_id, slot_id, order_in_slot, snap, note):
    est, seen, source = _autofill(snap)
    try:
        cur = con.execute(
            "INSERT INTO placement (sequence_id, slot_id, order_in_slot, source_key,"
            " node_id_seen, node_text_seen, ladder_file_seen, stem_id_seen,"
            " concept_skill_seen, grade_kind_seen, period_estimate, period_hint_seen,"
            " estimate_source, differentiation_note, placed_by,"
            " placed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sequence_id, slot_id, order_in_slot, snap["source_key"], snap.get("node_id"),
             snap["node_text"], snap.get("source_file"), snap.get("stem_id"),
             snap.get("concept_skill"), snap["state"], est, seen, source, note, writer, now))
    except sqlite3.IntegrityError:
        dup = _active_placement_for(con, sequence_id, snap["source_key"])
        raise Conflict("already_placed", "This node is already placed in the sequence",
                       {"placement_id": dup["placement_id"] if dup else None})
    return cur.lastrowid


def _place_after(snap, note, **extra):
    """The `place` event's after_json."""
    est, _seen, source = _autofill(snap)
    return {"source_key": snap["source_key"], "grade_kind_seen": snap["state"],
            "differentiation_note": note, **extra,
            "period_estimate": est, "estimate_source": source,
            "confirmed_off_grade": bool(snap.get("requires_confirm"))}


def _new_slot(con, writer, now, sequence_id, module_id, key):
    cur = con.execute(
        "INSERT INTO slot (sequence_id, module_id, order_key, created_by, created_at)"
        " VALUES (?,?,?,?,?)", (sequence_id, module_id, key, writer, now))
    return cur.lastrowid


def place(con, writer, sequence_id, expected_rev, snap, *, module_id,
          slot_id=None, after_slot_id=None, differentiation_note=None,
          confirm_off_grade=False) -> dict:
    with _tx(con):
        _check_seq_rev(con, sequence_id, expected_rev)
        _check_snap(snap)
        note = _opt_text(differentiation_note)
        if slot_id is not None:
            slot = _slot_row(con, slot_id)
            _same_seq(slot, sequence_id, "slot")
            module_id = slot["module_id"]
        else:
            mod = _module_row(con, module_id)
            _same_seq(mod, sequence_id, "module")
        if slot_id is None and after_slot_id is not None:
            after_slot = _slot_row(con, after_slot_id)
            _same_seq(after_slot, sequence_id, "slot")
            if after_slot["module_id"] != module_id:
                raise Invalid("invalid", "after_slot_id is not in that module")
        dup = _active_placement_for(con, sequence_id, snap["source_key"])
        if dup:
            raise Conflict("already_placed", "This node is already placed in the sequence",
                           {"placement_id": dup["placement_id"]})
        if snap.get("requires_confirm") and not confirm_off_grade:
            raise NeedsConfirm([snap["source_key"]], {snap["source_key"]: snap["state"]})
        now = _now()
        if slot_id is None:
            if after_slot_id is None:
                key = _alloc_key(con, "slot", module_id, sequence_id, writer, now)
            else:
                key = _alloc_key(con, "slot", module_id, sequence_id, writer, now,
                                 where="after", after_pk=after_slot_id)
            slot_id = _new_slot(con, writer, now, sequence_id, module_id, key)
            order = ORDER_STEP
        else:
            order = _alloc_key(con, "placement", slot_id, sequence_id, writer, now)
        pid = _insert_placement(con, writer, now, sequence_id, slot_id, order, snap, note)
        _bump_seq(con, sequence_id)
        _event(con, sequence_id, "place", writer, now, module_id=module_id,
               slot_id=slot_id, placement_id=pid,
               after=_place_after(snap, note))
    return {"placement_id": pid, "slot_id": slot_id}


def place_group(con, writer, sequence_id, expected_rev, snaps, *, module_id,
                confirm_off_grade=False, differentiation_notes=None) -> dict:
    notes = differentiation_notes or {}
    with _tx(con):
        _check_seq_rev(con, sequence_id, expected_rev)
        if not snaps:
            raise Invalid("invalid", "no nodes supplied")
        mod = _module_row(con, module_id)
        _same_seq(mod, sequence_id, "module")
        seen, todo, skipped = set(), [], []
        for snap in snaps:
            _check_snap(snap)
            if snap["source_key"] in seen:
                raise Invalid("invalid", "duplicate node in group")
            seen.add(snap["source_key"])
            dup = _active_placement_for(con, sequence_id, snap["source_key"])
            if dup:
                skipped.append({"source_key": snap["source_key"], "reason": "already_placed",
                                "placement_id": dup["placement_id"]})
            else:
                todo.append(snap)
        need = [s for s in todo if s.get("requires_confirm")]
        if need and not confirm_off_grade:
            raise NeedsConfirm([s["source_key"] for s in need],
                               {s["source_key"]: s["state"] for s in need})
        if not todo:
            return {"slot_id": None, "placement_ids": [], "skipped": skipped}
        now = _now()
        key = _alloc_key(con, "slot", module_id, sequence_id, writer, now)
        slot_id = _new_slot(con, writer, now, sequence_id, module_id, key)
        pids = []
        for i, snap in enumerate(todo, start=1):
            note = _opt_text(notes.get(snap["source_key"]))
            pid = _insert_placement(con, writer, now, sequence_id, slot_id,
                                    ORDER_STEP * i, snap, note)
            pids.append(pid)
            _event(con, sequence_id, "place", writer, now, module_id=module_id,
                   slot_id=slot_id, placement_id=pid,
                   after=_place_after(snap, note, group=True))
        _bump_seq(con, sequence_id)
    return {"slot_id": slot_id, "placement_ids": pids, "skipped": skipped}


_ATTR_ACTION = {"calibration": "set_calibration", "period_estimate": "set_period",
                "differentiation_note": "set_note"}


def set_attributes(con, writer, placement_id, expected_rev, changes, period_hint_seen=None) -> None:
    if not isinstance(changes, dict) or not changes:
        raise Invalid("invalid", "no changes supplied")
    bad = set(changes) - set(_ATTR_ACTION)
    if bad:
        raise Invalid("invalid", f"cannot set {sorted(bad)}")
    clean = {}
    for k, v in changes.items():
        if k == "calibration":
            if v is not None and v not in CALIBRATIONS:
                raise Invalid("invalid", "calibration must be deep, functional, illuminating or null")
        elif k == "period_estimate":
            if v is not None:
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    raise Invalid("invalid", "period_estimate must be a number")
                if v != v or v < 0 or v > _PERIOD_MAX:
                    raise Invalid("invalid", f"period_estimate must be between 0 and {_PERIOD_MAX}")
                v = float(v)
        else:
            v = _opt_text(v)
        clean[k] = v
    _check_expected(expected_rev)
    with _tx(con):
        row = _one(con, "SELECT * FROM placement WHERE placement_id=? AND removed_at IS NULL",
                   (placement_id,))
        if row is None:
            raise NotFound("placement not found")
        if row["rev"] != expected_rev:
            raise StaleRevision(row["rev"])
        # Retyping a ladder-autofilled number is still an edit: it makes it the builder's.
        changed = {k: v for k, v in clean.items()
                   if v != row[k] or (k == "period_estimate" and v is not None
                                      and row["estimate_source"] == "ladder")}
        if not changed:
            return  # nothing changed: no bump, no event
        now = _now()
        sets = dict(changed)
        source = None
        if "period_estimate" in changed:
            source = None if changed["period_estimate"] is None else "builder"
            sets["period_hint_seen"] = period_hint_seen
            sets["estimate_source"] = source
        assign = ", ".join(f"{k}=?" for k in sets)
        con.execute(f"UPDATE placement SET {assign}, updated_by=?, updated_at=?,"
                    " rev = rev + 1 WHERE placement_id=?",
                    (*sets.values(), writer, now, placement_id))
        slot = _one(con, "SELECT module_id FROM slot WHERE slot_id=?", (row["slot_id"],))
        for k in ("calibration", "period_estimate", "differentiation_note"):
            if k not in changed:
                continue
            before, after = {k: row[k]}, {k: changed[k]}
            if k == "period_estimate":
                before["period_hint_seen"] = row["period_hint_seen"]
                after["period_hint_seen"] = period_hint_seen
                before["estimate_source"] = row["estimate_source"]
                after["estimate_source"] = source
            _event(con, row["sequence_id"], _ATTR_ACTION[k], writer, now,
                   module_id=slot["module_id"] if slot else None,
                   slot_id=row["slot_id"], placement_id=placement_id,
                   before=before, after=after)


def confirm_estimate(con, writer, placement_id, expected_rev) -> None:
    """The writer agrees with a ladder-autofilled estimate: 'ladder' -> 'builder',
    number unchanged, one `confirm_period` event.  Invalid unless the source is
    'ladder' (already the builder's, or no estimate)."""
    _check_expected(expected_rev)
    with _tx(con):
        row = _one(con, "SELECT * FROM placement WHERE placement_id=? AND removed_at IS NULL",
                   (placement_id,))
        if row is None:
            raise NotFound("placement not found")
        if row["rev"] != expected_rev:
            raise StaleRevision(row["rev"])
        if row["estimate_source"] != "ladder":
            raise Invalid("invalid", "There is no ladder estimate to confirm")
        now = _now()
        con.execute("UPDATE placement SET estimate_source='builder', updated_by=?, updated_at=?,"
                    " rev = rev + 1 WHERE placement_id=?", (writer, now, placement_id))
        slot = _one(con, "SELECT module_id FROM slot WHERE slot_id=?", (row["slot_id"],))
        _event(con, row["sequence_id"], "confirm_period", writer, now,
               module_id=slot["module_id"] if slot else None,
               slot_id=row["slot_id"], placement_id=placement_id,
               before={"period_estimate": row["period_estimate"], "estimate_source": "ladder"},
               after={"period_estimate": row["period_estimate"], "estimate_source": "builder"})


def co_place(con, writer, placement_id, expected_rev, target_slot_id) -> None:
    with _tx(con):
        p = _placement_row(con, placement_id)
        sid = p["sequence_id"]
        _check_seq_rev(con, sid, expected_rev)
        target = _slot_row(con, target_slot_id)
        _same_seq(target, sid, "target slot")
        if target_slot_id == p["slot_id"]:
            raise Invalid("invalid", "already in that slot")
        now = _now()
        key = _alloc_key(con, "placement", target_slot_id, sid, writer, now)
        con.execute("UPDATE placement SET slot_id=?, order_in_slot=?, updated_by=?,"
                    " updated_at=?, rev=rev+1 WHERE placement_id=?",
                    (target_slot_id, key, writer, now, placement_id))
        dropped = _drop_slot_if_empty(con, p["slot_id"], writer, now)
        _bump_seq(con, sid)
        _event(con, sid, "co_place", writer, now, module_id=target["module_id"],
               slot_id=target_slot_id, placement_id=placement_id,
               before={"slot_id": p["slot_id"]},
               after={"slot_id": target_slot_id, "source_slot_removed": dropped})


def ungroup(con, writer, placement_id, expected_rev) -> int:
    with _tx(con):
        p = _placement_row(con, placement_id)
        sid = p["sequence_id"]
        _check_seq_rev(con, sid, expected_rev)
        n = con.execute("SELECT COUNT(*) FROM placement WHERE slot_id=?"
                        " AND removed_at IS NULL", (p["slot_id"],)).fetchone()[0]
        if n < 2:
            raise Invalid("already_alone", "This node is already alone in its slot")
        slot = _slot_row(con, p["slot_id"])
        now = _now()
        key = _alloc_key(con, "slot", slot["module_id"], sid, writer, now,
                         where="after", after_pk=slot["slot_id"])
        new_slot = _new_slot(con, writer, now, sid, slot["module_id"], key)
        con.execute("UPDATE placement SET slot_id=?, order_in_slot=?, updated_by=?,"
                    " updated_at=?, rev=rev+1 WHERE placement_id=?",
                    (new_slot, ORDER_STEP, writer, now, placement_id))
        _bump_seq(con, sid)
        _event(con, sid, "ungroup", writer, now, module_id=slot["module_id"],
               slot_id=new_slot, placement_id=placement_id,
               before={"slot_id": p["slot_id"]}, after={"slot_id": new_slot})
    return new_slot


def remove_placement(con, writer, placement_id, expected_rev, reason=None) -> None:
    reason = _opt_text(reason)
    with _tx(con):
        p = _placement_row(con, placement_id)
        sid = p["sequence_id"]
        _check_seq_rev(con, sid, expected_rev)
        now = _now()
        con.execute("UPDATE placement SET removed_at=?, removed_by=?, removed_reason=?,"
                    " rev=rev+1 WHERE placement_id=?", (now, writer, reason, placement_id))
        dropped = _drop_slot_if_empty(con, p["slot_id"], writer, now)
        slot = _one(con, "SELECT module_id FROM slot WHERE slot_id=?", (p["slot_id"],))
        _bump_seq(con, sid)
        _event(con, sid, "remove", writer, now, module_id=slot["module_id"] if slot else None,
               slot_id=p["slot_id"], placement_id=placement_id,
               before={"source_key": p["source_key"], "slot_id": p["slot_id"]},
               after={"removed_reason": reason, "slot_removed": dropped})


_SEEN_COLS = (("node_id_seen", "node_id"), ("node_text_seen", "node_text"),
              ("ladder_file_seen", "source_file"), ("stem_id_seen", "stem_id"),
              ("concept_skill_seen", "concept_skill"), ("grade_kind_seen", "state"))


def _refresh_seen(con, placement_id, p, snap, writer, now, *, new_key=None):
    sets = {col: snap.get(src) for col, src in _SEEN_COLS}
    if sets["node_text_seen"] is None:
        raise Invalid("invalid", "snapshot is missing node_text")
    if sets["grade_kind_seen"] not in _KINDS:
        raise Invalid("invalid", "snapshot is missing a valid state")
    before = {c: p[c] for c in sets if p[c] != sets[c]}
    after = {c: v for c, v in sets.items() if c in before}
    if new_key is not None:
        sets["source_key"] = new_key
        sets["reattached_from"] = p["source_key"]
        before["source_key"], after["source_key"] = p["source_key"], new_key
    assign = ", ".join(f"{k}=?" for k in sets)
    con.execute(f"UPDATE placement SET {assign}, updated_by=?, updated_at=?, rev=rev+1"
                " WHERE placement_id=?", (*sets.values(), writer, now, placement_id))
    return before, after


def reattach(con, writer, placement_id, expected_rev, snap) -> None:
    with _tx(con):
        p = _placement_row(con, placement_id)
        sid = p["sequence_id"]
        _check_seq_rev(con, sid, expected_rev)
        _check_snap(snap)
        dup = _active_placement_for(con, sid, snap["source_key"])
        if dup:
            raise Conflict("reattach_target_placed",
                           "That node is already placed in this sequence",
                           {"placement_id": dup["placement_id"]})
        now = _now()
        before, after = _refresh_seen(con, placement_id, p, snap, writer, now,
                                      new_key=snap["source_key"])
        slot = _one(con, "SELECT module_id FROM slot WHERE slot_id=?", (p["slot_id"],))
        _bump_seq(con, sid)
        _event(con, sid, "reattach", writer, now, module_id=slot["module_id"] if slot else None,
               slot_id=p["slot_id"], placement_id=placement_id, before=before, after=after)


def acknowledge(con, writer, placement_id, expected_rev, snap) -> None:
    with _tx(con):
        p = _placement_row(con, placement_id)
        sid = p["sequence_id"]
        _check_seq_rev(con, sid, expected_rev)
        _check_snap(snap)
        if snap["source_key"] != p["source_key"]:
            raise Invalid("invalid", "snapshot is for a different node")
        now = _now()
        before, after = _refresh_seen(con, placement_id, p, snap, writer, now)
        slot = _one(con, "SELECT module_id FROM slot WHERE slot_id=?", (p["slot_id"],))
        _bump_seq(con, sid)
        _event(con, sid, "acknowledge", writer, now, module_id=slot["module_id"] if slot else None,
               slot_id=p["slot_id"], placement_id=placement_id, before=before, after=after)


# --------------------------------------------------------------------- reads

def get_placement(con, placement_id) -> dict:
    row = _one(con, "SELECT * FROM placement WHERE placement_id=?", (placement_id,))
    if row is None:
        raise NotFound("placement not found")
    return row


def load_tree(con, sequence_id) -> dict:
    seq = _seq_row(con, sequence_id)
    modules = []
    slots_by_mod, pl_by_slot = {}, {}
    for r in con.execute(
            "SELECT * FROM placement WHERE sequence_id=? AND removed_at IS NULL"
            " ORDER BY order_in_slot, placement_id", (sequence_id,)):
        pl_by_slot.setdefault(r["slot_id"], []).append(dict(r))
    for r in con.execute(
            "SELECT * FROM slot WHERE sequence_id=? AND removed_at IS NULL"
            " ORDER BY order_key, slot_id", (sequence_id,)):
        slots_by_mod.setdefault(r["module_id"], []).append(dict(r))
    for mpos, m in enumerate(con.execute(
            "SELECT * FROM module WHERE sequence_id=? AND removed_at IS NULL"
            " ORDER BY order_key, module_id", (sequence_id,)), start=1):
        mod = dict(m)
        mod["position"] = mpos
        mod["slots"] = []
        for spos, s in enumerate(slots_by_mod.get(m["module_id"], []), start=1):
            s["position"] = spos
            s["placements"] = pl_by_slot.get(s["slot_id"], [])
            mod["slots"].append(s)
        modules.append(mod)
    return {"sequence": seq, "modules": modules}


def placement_index(con, *, exclude_grade=None) -> dict:
    out: dict = {}
    for r in con.execute(
            "SELECT p.source_key, p.placement_id, s.sequence_id, s.grade,"
            " s.title AS sequence_title, m.module_id, m.title AS module_title"
            " FROM placement p"
            " JOIN grade_sequence s ON s.sequence_id = p.sequence_id"
            " JOIN slot sl ON sl.slot_id = p.slot_id"
            " JOIN module m ON m.module_id = sl.module_id"
            " WHERE p.removed_at IS NULL AND s.archived_at IS NULL"
            " AND (? IS NULL OR s.grade <> ?)"
            " ORDER BY s.grade, s.sequence_id, m.order_key, sl.order_key, p.order_in_slot",
            (exclude_grade, exclude_grade)):
        out.setdefault(r["source_key"], []).append({
            "grade": r["grade"], "sequence_id": r["sequence_id"],
            "sequence_title": r["sequence_title"], "module_id": r["module_id"],
            "module_title": r["module_title"], "placement_id": r["placement_id"]})
    return out


def list_events(con, sequence_id, limit=50, before_event_id=None) -> list:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise Invalid("invalid", "limit must be a positive integer")
    rows = con.execute(
        "SELECT * FROM placement_event WHERE sequence_id=?"
        " AND (? IS NULL OR event_id < ?) ORDER BY event_id DESC LIMIT ?",
        (sequence_id, before_event_id, before_event_id, limit))
    return [{
        "event_id": r["event_id"], "action": r["action"], "actor": r["actor"],
        "at": r["at"], "module_id": r["module_id"], "slot_id": r["slot_id"],
        "placement_id": r["placement_id"],
        "before": json.loads(r["before_json"]) if r["before_json"] else None,
        "after": json.loads(r["after_json"]) if r["after_json"] else None,
    } for r in rows]


# --------------------------------------------------------------- saved views

def _view(row) -> dict:
    return {"view_id": row["view_id"], "grade": row["grade"], "name": row["name"],
            "state": json.loads(row["state_json"]), "created_at": row["created_at"],
            "updated_at": row["updated_at"]}


def _own_view(con, owner, view_id):
    row = con.execute("SELECT * FROM saved_view WHERE view_id=? AND owner=?",
                      (view_id, owner)).fetchone()
    if row is None:
        raise NotFound("view not found")
    return row


def list_views(con, owner, grade=None) -> list:
    rows = con.execute(
        "SELECT * FROM saved_view WHERE owner=? AND (? IS NULL OR grade=?)"
        " ORDER BY grade, name COLLATE NOCASE, view_id", (owner, grade, grade))
    return [_view(r) for r in rows]


def _check_state(state):
    if not isinstance(state, dict):
        raise Invalid("invalid", "state must be an object")


def create_view(con, owner, grade, name, state) -> dict:
    if grade not in GRADES:
        raise Invalid("invalid", f"unknown grade {grade!r}")
    name = _require_text(name, "name")
    _check_state(state)
    now = _now()
    with _tx(con):
        try:
            cur = con.execute(
                "INSERT INTO saved_view (owner, grade, name, state_json, created_at,"
                " updated_at) VALUES (?,?,?,?,?,?)",
                (owner, grade, name, json.dumps(state, sort_keys=True), now, now))
        except sqlite3.IntegrityError:
            raise Conflict("view_name_exists", "You already have a view with that name")
        return _view(con.execute("SELECT * FROM saved_view WHERE view_id=?",
                                 (cur.lastrowid,)).fetchone())


def update_view(con, owner, view_id, *, name=UNSET, state=UNSET) -> dict:
    with _tx(con):
        row = _own_view(con, owner, view_id)
        sets = {}
        if name is not UNSET:
            sets["name"] = _require_text(name, "name")
        if state is not UNSET:
            _check_state(state)
            sets["state_json"] = json.dumps(state, sort_keys=True)
        if sets:
            sets["updated_at"] = _now()
            assign = ", ".join(f"{k}=?" for k in sets)
            try:
                con.execute(f"UPDATE saved_view SET {assign} WHERE view_id=?",
                            (*sets.values(), view_id))
            except sqlite3.IntegrityError:
                raise Conflict("view_name_exists", "You already have a view with that name")
        return _view(con.execute("SELECT * FROM saved_view WHERE view_id=?",
                                 (view_id,)).fetchone())


def delete_view(con, owner, view_id) -> None:
    with _tx(con):
        _own_view(con, owner, view_id)
        con.execute("DELETE FROM saved_view WHERE view_id=? AND owner=?", (view_id, owner))
