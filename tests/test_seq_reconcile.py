"""seq_reconcile tests (contract §8.2): derive_status on hand-built facts for
every data model §6.3 row expressible without a DB; the report CLI on temp
copies (no rows written); and a real mh2.db copy where node rows are altered
or deleted to simulate a reword / deletion / grade change."""
import hashlib
import json
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import test_seq_service as T  # noqa: E402  (installs the fake read module if R's is absent)
from mh2 import seq_reconcile as R  # noqa: E402
from mh2 import seq_service as V  # noqa: E402


def row(seen="core", cs="C/S", file="a.docx"):
    return {"grade_kind_seen": seen, "concept_skill_seen": cs, "ladder_file_seen": file}


def facts(state="core", cs="C/S", file="a.docx"):
    return {"state": state, "concept_skill": cs, "source_file": file}


def test_orphan():
    s = R.derive_status(row(), None)
    assert s == {"status": "orphaned", "state_now": None, "relabelled": False, "relabel": None}


def test_ok_unchanged():
    s = R.derive_status(row("span"), facts("span"))
    assert s["status"] == "ok" and s["state_now"] == "span" and s["relabelled"] is False


def test_core_to_off_grade_is_grade_changed():
    assert R.derive_status(row("core"), facts("off_grade"))["status"] == "grade_changed"


def test_other_non_exempt_transitions_raise():
    for seen, now in (("core", "span"), ("span", "core"), ("core", "leaf"), ("span", "no_grade"),
                      ("unconfirmed", "state_extension"), ("unconfirmed", "off_grade"),
                      ("unconfirmed", "leaf"), ("unknown", "off_grade"), ("unknown", "leaf"),
                      ("unknown", "state_extension"), ("state_extension", "core"),
                      ("core", "unconfirmed"), ("core", "unknown"), ("off_grade", "core")):
        assert R.derive_status(row(seen), facts(now))["status"] == "grade_changed", (seen, now)


def test_exempt_transitions_are_silent():
    assert R.EXEMPT_TRANSITIONS == {("unknown", "core"), ("unknown", "span"), ("unknown", "unconfirmed"),
                                    ("unconfirmed", "core"), ("unconfirmed", "span")}
    for seen, now in sorted(R.EXEMPT_TRANSITIONS):
        assert R.derive_status(row(seen), facts(now))["status"] == "ok", (seen, now)


def test_unconfirmed_to_span_ok_and_to_state_extension_raises():
    assert R.derive_status(row("unconfirmed"), facts("span"))["status"] == "ok"
    assert R.derive_status(row("unconfirmed"), facts("state_extension"))["status"] == "grade_changed"


def test_relabel_on_concept_skill_and_on_file():
    s = R.derive_status(row(cs="Old"), facts(cs="New"))
    assert s["status"] == "ok" and s["relabelled"] is True
    assert s["relabel"] == {"concept_skill_seen": "Old", "concept_skill_now": "New",
                            "ladder_file_seen": "a.docx", "ladder_file_now": "a.docx"}
    s = R.derive_status(row(file="a.docx"), facts(file="b.docx"))
    assert s["relabelled"] is True and s["relabel"]["ladder_file_now"] == "b.docx"
    # relabel co-occurs with grade_changed, but never with orphaned
    s = R.derive_status(row("core", cs="Old"), facts("leaf", cs="New"))
    assert s["status"] == "grade_changed" and s["relabelled"] is True
    assert R.derive_status(row(cs="Old"), None)["relabelled"] is False
    # nothing captured -> nothing to compare
    assert R.derive_status(row(cs=None, file=None), facts())["relabelled"] is False


def test_node_id_change_alone_is_not_a_state():
    f = facts("span")
    f["node_id"] = "WHO-0099"
    r = row("span")
    r["node_id_seen"] = "WHO-0011"
    assert R.derive_status(r, f)["status"] == "ok"


def _table_hashes(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out = {}
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"
                            " AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
        rows = [tuple(r) for r in con.execute(f"SELECT * FROM {t} ORDER BY 1")]
        out[t] = hashlib.sha1(json.dumps(rows, default=str).encode()).hexdigest()
    con.close()
    return out


def test_cli_on_temp_copies_writes_report_and_no_rows():
    ctx, mh2 = T.make_env()
    e = T.build_5_6(ctx)
    T.mh2_edit(mh2, "UPDATE nodes SET node_text=?, source_key=? WHERE node_id='WHO-0011'",
               ("Order whole numbers using place value.", T.skey("Order whole numbers using place value.", "WHO")))
    T.mh2_edit(mh2, "DELETE FROM node_grade WHERE node_id='WHO-0012' AND grade='4'")   # unrelated: noop for G2
    T.mh2_edit(mh2, "UPDATE node_grade_kind SET kind='state_extension' WHERE node_id='COM-0012'")
    T.mh2_edit(mh2, "UPDATE nodes SET concept_skill='Renamed heading' WHERE node_id='WHO-0010'")
    before = _table_hashes(ctx.seq_path)
    out = Path(_mkdtemp()) / "placement_reconcile.txt"
    assert R.main(["--db", str(ctx.mh2_path), "--seq-db", str(ctx.seq_path), "--out", str(out)]) == 0
    assert _table_hashes(ctx.seq_path) == before, "the CLI must write no rows"
    text = out.read_text()
    assert "== orphaned ==" in text and "== grade_changed ==" in text and "== relabelled ==" in text
    assert "WHO-0011" in text and "Order whole numbers by using place value." in text
    assert "COM-0012 core -> state_extension" in text
    assert "top suggestion" in text
    assert "stem WHO: 1 of 3 placements orphaned" in text
    assert "orphaned: 1    grade_changed: 1    relabelled: 1" in text
    # --out as a directory
    d = Path(_mkdtemp())
    assert R.main(["--db", str(ctx.mh2_path), "--seq-db", str(ctx.seq_path), "--out", str(d)]) == 0
    assert (d / "placement_reconcile.txt").exists()
    # the database files are opened read-only: a read-only open cannot have been written
    src = (ROOT / "mh2" / "seq_reconcile.py").read_text()
    assert "mode=ro" in src and "INSERT" not in src and "UPDATE" not in src and "DELETE" not in src


def test_cli_without_placement_tables():
    ctx, mh2 = T.make_env()
    sqlite3.connect(ctx.seq_path).close()          # empty file
    out = Path(_mkdtemp()) / "r.txt"
    assert R.main(["--db", str(mh2), "--seq-db", str(ctx.seq_path), "--out", str(out)]) == 0
    assert "no placement tables" in out.read_text()


def test_stem_with_every_placement_orphaned_is_headlined():
    ctx, mh2 = T.make_env()
    r = V.create_sequence(ctx, "amy", "2", "G2", None)
    sid = r["result"]["sequence_id"]
    r = V.create_module(ctx, "amy", sid, T.rev(r), "M1", None)
    m = r["result"]["module_id"]
    r = V.place(ctx, "amy", sid, T.rev(r), m, T.K["COM-0012"])
    r = V.place(ctx, "amy", sid, T.rev(r), m, T.K["COM-0013"])
    con = sqlite3.connect(mh2)
    con.execute("PRAGMA foreign_keys=OFF")
    con.execute("DELETE FROM nodes WHERE stem_id='COM'")
    con.commit()
    con.close()
    out = Path(_mkdtemp()) / "r.txt"
    R.main(["--db", str(mh2), "--seq-db", str(ctx.seq_path), "--out", str(out)])
    assert "stem COM: 2 of 2 placements orphaned  -- check ingest before acting" in out.read_text()


def test_real_mh2_copy_reword_delete_grade_change():
    src = Path(config.DB)
    if not src.exists():
        print("SKIP real-copy: no", src)
        return
    d = Path(_mkdtemp(prefix="seqreal_"))
    mh2 = d / "mh2.db"
    shutil.copy(src, mh2)
    ctx = V.Ctx(mh2, d / "mh2_seq.db")
    con = sqlite3.connect(mh2)
    keys = {nid: k for nid, k in con.execute(
        "SELECT node_id, source_key FROM nodes WHERE node_id IN ('NS-BASE10-0010','NS-BASE10-0011','NS-COMP-ORDER-0012','MD-TIME-0010')")}
    con.close()
    assert len(keys) == 4, keys
    r = V.create_sequence(ctx, "amy", "2", "Grade 2 sequence", None)
    sid = r["result"]["sequence_id"]
    r = V.create_module(ctx, "amy", sid, T.rev(r), "M1", None)
    m = r["result"]["module_id"]
    for nid in ("NS-BASE10-0011", "NS-BASE10-0010", "NS-COMP-ORDER-0012", "MD-TIME-0010"):
        r = V.place(ctx, "amy", sid, T.rev(r), m, keys[nid])
    v = r["sequence"]
    assert v["attention"] == [] and len(v["placed_index"]) == 4
    # reword NS-BASE10-0011 (new text + new key), delete NS-COMP-ORDER-0012 outright, MD-TIME-0010 loses G2
    con = sqlite3.connect(mh2)
    con.execute("PRAGMA foreign_keys=OFF")
    con.execute("UPDATE nodes SET node_text=node_text || ' today', source_key='NS-BASE10:0123456789abcdef'"
                " WHERE node_id='NS-BASE10-0011'")
    con.execute("DELETE FROM nodes WHERE node_id='NS-COMP-ORDER-0012'")
    con.execute("DELETE FROM node_grade WHERE node_id='MD-TIME-0010' AND grade='2'")
    con.commit()
    con.close()
    v = V.get_sequence(ctx, "2")
    status = {k: p["status"] for k, p in v["placed_index"].items()}
    assert status[keys["NS-BASE10-0011"]] == "orphaned" and status[keys["NS-COMP-ORDER-0012"]] == "orphaned"
    assert status[keys["MD-TIME-0010"]] == "grade_changed" and status[keys["NS-BASE10-0010"]] == "ok"
    assert len(v["attention"]) == 3
    who = [a for a in v["attention"] if a["source_key"] == keys["NS-BASE10-0011"]][0]
    assert who["suggestions"] and who["suggestions"][0]["source_key"] == "NS-BASE10:0123456789abcdef"
    json.dumps(v)
    before = _table_hashes(ctx.seq_path)
    out = d / "rep.txt"
    assert R.main(["--db", str(mh2), "--seq-db", str(ctx.seq_path), "--out", str(out)]) == 0
    assert _table_hashes(ctx.seq_path) == before
    text = out.read_text()
    assert "orphaned: 2    grade_changed: 1" in text and "MD-TIME-0010 unconfirmed -> off_grade" in text


def _main():
    print(f"[read modules: {T.READ_MODE}]")
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
