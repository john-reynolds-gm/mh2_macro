"""
rebuild.py hardening tests -- the pre-Azure safeguards (DEFERRED.md §8,
R-H5.1-R-H5.5). Numbers below are the brief's §7 acceptance criteria.

  #1  a rebuild that fails partway leaves the pre-existing mh2.db
      byte-identical and leaves mh2_seq.db's rows intact
  #2  a swap replaces mh2.db and the swapped-in file passes integrity_check
  #3  a second rebuild while the lock is held exits non-zero naming the
      lock file, and does not touch mh2.db
  #4  with MH2_DATA_DIR set, a run reads and writes entirely inside it
  #5  pre-flight names every missing input in one message and exits before
      the schema is created
  #6  rebuild_log.tsv gains exactly one line per run, failing step named

The integration tests here drive the real `scripts/rebuild.py` in a
subprocess against a temporary MH2_DATA_DIR whose declared inputs are
present but empty. That is deliberate: the run clears pre-flight and then
fails inside the first loader, which is precisely the "fails partway"
shape #1 is about, and it takes about a second rather than the twelve
minutes a real rebuild needs. A genuine end-to-end rebuild is not run here
-- see the note on #2 below.
"""
import fcntl
import hashlib
import os
import subprocess
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import config  # noqa: E402
import rebuild  # noqa: E402
from mh2 import review_store  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_reconcile_review import _seq_db  # noqa: E402

# Derived from rebuild.py's own list rather than restated, so a new required
# input cannot be added there without these tests staking it out too.
RELATIVE_INPUTS = [p.relative_to(config.DATA) for p in rebuild.REQUIRED_INPUTS]


def _data_tree(root: Path, omit=()) -> Path:
    """A complete MH2_DATA_DIR: every required input present but empty.
    `omit` is a set of basenames to leave absent."""
    for rel in RELATIVE_INPUTS:
        if rel.name in omit:
            continue
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    for sub in ("build", "reports", "source/ladders"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def _run_rebuild(data_dir: Path, actor="tester"):
    env = dict(os.environ, MH2_DATA_DIR=str(data_dir), MH2_REBUILD_ACTOR=actor)
    return subprocess.run(
        [sys.executable, str(REPO / "scripts" / "rebuild.py")],
        capture_output=True, text=True, cwd=str(REPO), env=env, timeout=300)


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _log_lines(data_dir: Path) -> list[str]:
    path = data_dir / "reports" / rebuild.REBUILD_LOG_NAME
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


# ------------------------------------------------------------------- #1, #6

def test_failed_rebuild_leaves_live_db_byte_identical(tmp_path):
    """#1: the live database is not the build target, so a failure cannot
    reach it. Predicate: md5 of data/build/mh2.db before == after."""
    data = _data_tree(tmp_path / "data")
    live = data / "build" / "mh2.db"
    live.write_bytes(b"pretend this is the live database")
    before = _md5(live)

    seq = _seq_db(data / "build" / "mh2_seq.db")
    review_store.set_standard_review(seq, "K.CC.A.1", "confirmed", "Green", "jane")
    seq.close()
    seq_before = _md5(data / "build" / "mh2_seq.db")

    proc = _run_rebuild(data)
    assert proc.returncode != 0, proc.stdout[-2000:]

    assert _md5(live) == before, "a failed rebuild modified the live database"
    assert _md5(data / "build" / "mh2_seq.db") == seq_before
    # And the judgment itself is still readable, not merely the same bytes.
    seq = review_store.connect(data / "build" / "mh2_seq.db")
    assert review_store.get_standard_review(seq, "K.CC.A.1")["reviewed_by"] == "jane"
    seq.close()


def test_failed_rebuild_logs_one_line_naming_the_failing_step(tmp_path):
    """#6, failure case."""
    data = _data_tree(tmp_path / "data")
    proc = _run_rebuild(data, actor="debbie")
    assert proc.returncode != 0

    lines = _log_lines(data)
    assert len(lines) == 2, lines            # header + exactly one run
    assert lines[0].split("\t") == ["timestamp_utc", "actor", "outcome",
                                    "seconds", "ladders"]
    fields = lines[1].split("\t")
    assert fields[1] == "debbie"
    assert fields[2].startswith("failed:"), fields[2]
    assert fields[2] != "failed:unexpected-error", (
        "the failing step should be named, not swallowed as a generic error")


def test_each_run_appends_exactly_one_more_line(tmp_path):
    """#6: append-only, never rewritten."""
    data = _data_tree(tmp_path / "data")
    _run_rebuild(data)
    _run_rebuild(data)
    assert len(_log_lines(data)) == 3        # header + two runs


def test_rebuild_log_records_a_successful_run(tmp_path, monkeypatch):
    """#6, success case. The writer is exercised directly: a real end-to-end
    rebuild is minutes long and needs the true source workbooks."""
    monkeypatch.setattr(config, "REPORTS", tmp_path)
    monkeypatch.setenv("MH2_REBUILD_ACTOR", "katie")
    rebuild.append_rebuild_log("ok", 12.5, 10)

    lines = (tmp_path / rebuild.REBUILD_LOG_NAME).read_text().splitlines()
    assert len(lines) == 2
    fields = lines[1].split("\t")
    assert fields[1] == "katie"
    assert fields[2] == "ok"
    assert fields[3] == "12.5"
    assert fields[4] == "10"


def test_rebuild_actor_falls_back_to_the_os_user(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPORTS", tmp_path)
    monkeypatch.delenv("MH2_REBUILD_ACTOR", raising=False)
    rebuild.append_rebuild_log("ok", 1.0, 0)
    assert (tmp_path / rebuild.REBUILD_LOG_NAME).read_text().splitlines()[1] \
        .split("\t")[1] != ""


# ----------------------------------------------------------------------- #2

def test_swap_replaces_live_db_and_result_passes_integrity_check(tmp_path):
    """#2. The swap is tested on its own rather than through a full rebuild:
    what #2 is really asserting is that the file that lands is intact and is
    the one that was built."""
    live = tmp_path / "mh2.db"
    live.write_bytes(b"the previous database")
    build_db = tmp_path / rebuild.BUILD_DB_NAME
    con = sqlite3.connect(build_db)
    con.execute("CREATE TABLE t (x INTEGER)")
    con.execute("INSERT INTO t VALUES (42)")
    con.commit()
    con.close()

    rebuild.swap_into_place(build_db, live)

    assert not build_db.exists(), "the build file should have been moved, not copied"
    con = sqlite3.connect(live)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert con.execute("SELECT x FROM t").fetchone()[0] == 42
    con.close()


def test_swap_refuses_rather_than_orphan_wal_sidecars(tmp_path):
    """The guard behind #2's comment in swap_into_place: nothing sets WAL
    today, and if that ever changes, moving the .db alone must fail loudly
    instead of silently leaving -wal/-shm behind."""
    live = tmp_path / "mh2.db"
    live.write_bytes(b"the previous database")
    build_db = tmp_path / rebuild.BUILD_DB_NAME
    con = sqlite3.connect(build_db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (x INTEGER)")
    con.commit()

    with pytest.raises(SystemExit):
        rebuild.swap_into_place(build_db, live)
    con.close()
    assert live.read_bytes() == b"the previous database"


# ----------------------------------------------------------------------- #3

def test_second_rebuild_refuses_while_the_lock_is_held(tmp_path):
    """#3."""
    data = _data_tree(tmp_path / "data")
    live = data / "build" / "mh2.db"
    live.write_bytes(b"pretend this is the live database")
    before = _md5(live)

    lock_path = data / "build" / rebuild.LOCK_NAME
    holder = open(lock_path, "w")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        proc = _run_rebuild(data)
    finally:
        holder.close()

    assert proc.returncode != 0
    assert str(lock_path) in proc.stderr, proc.stderr
    assert "already running" in proc.stderr
    assert _md5(live) == before
    # A refused attempt never started, so it is not a run and is not logged.
    assert _log_lines(data) == []


def test_lock_is_released_when_a_run_ends(tmp_path):
    """Otherwise the first failure would wedge every later rebuild."""
    data = _data_tree(tmp_path / "data")
    first = _run_rebuild(data)
    assert first.returncode != 0
    second = _run_rebuild(data)
    assert "already running" not in second.stderr


# ----------------------------------------------------------------------- #4

def _snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): p.stat().st_mtime_ns
            for p in root.rglob("*") if p.is_file()}


def test_data_dir_relocation_writes_nothing_under_the_repo(tmp_path):
    """#4."""
    repo_data = REPO / "data"
    before = _snapshot(repo_data)

    data = _data_tree(tmp_path / "data")
    _run_rebuild(data)

    assert _snapshot(repo_data) == before, "the run touched the repo's own data/"
    # ... and everything it did write landed in the relocated tree.
    assert (data / "reports" / rebuild.REBUILD_LOG_NAME).exists()
    assert (data / "build" / rebuild.LOCK_NAME).exists()


# ----------------------------------------------------------------------- #5

def test_preflight_names_every_missing_input_in_one_message(tmp_path):
    """#5."""
    omit = {"stems.csv", "all_states.csv", "lesson_metadata.csv"}
    data = _data_tree(tmp_path / "data", omit=omit)

    proc = _run_rebuild(data)

    assert proc.returncode != 0
    for name in omit:
        assert name in proc.stderr, f"{name} not named in:\n{proc.stderr}"
    assert _log_lines(data)[1].split("\t")[2] == "failed:pre-flight"


def test_preflight_exits_before_the_schema_is_created(tmp_path):
    """#5's second half: no database, not even a build file, is left behind."""
    data = _data_tree(tmp_path / "data", omit={"stems.csv"})
    _run_rebuild(data)
    assert not (data / "build" / "mh2.db").exists()
    assert not (data / "build" / rebuild.BUILD_DB_NAME).exists()


def test_preflight_does_not_require_predictions_or_rerank_cache(tmp_path):
    """Both are legitimately absent on a clean checkout and their loaders
    skip cleanly. Requiring them would break a fresh clone."""
    data = _data_tree(tmp_path / "data")
    assert not (data / "source" / "predictions").exists()
    assert not (data / "reranks" / "cache.jsonl").exists()

    proc = _run_rebuild(data)

    assert "predictions" not in proc.stderr.lower()
    assert "cache.jsonl" not in proc.stderr
    assert _log_lines(data)[1].split("\t")[2] != "failed:pre-flight"
