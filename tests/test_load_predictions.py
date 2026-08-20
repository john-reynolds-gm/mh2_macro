"""
Path C ingestion tests (§3 Path C, §3.5.1 — step 8).

Four things here are load-bearing, and all four are traps the real files set:

  direction      `ccss_id` holds the TARGET in every direction, including the
                 three files where the target is a state code. Reading the pair
                 off the field name puts Texas in the ccss_code column.
  the PK         the same pair arrives from two runs whose scores do not share
                 a range. Without `direction` in the key, one arbitrary
                 measurement survives and nothing records which.
  the label      three spellings of `state` coexist and two Virginia anchors
                 disagree with their own. The code prefix is the source.
  same-state     the state->state run pairs FL with FL. That is not what
                 §3.5.1 asked for.

Run with: python tests/test_load_predictions.py
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

from mh2.load_predictions import (  # noqa: E402
    CCSS_TO_STATE, STATE_TO_CCSS, STATE_TO_STATE, load_all, load_file,
    model_version, state_of,
)


def write_jsonl(directory: Path, name: str, objects: list) -> Path:
    path = directory / name
    path.write_text("\n".join(json.dumps(o) for o in objects) + "\n")
    return path


def anchor(anchor_id, targets, state="fl", tier="Weak"):
    """One anchor object shaped like the real files."""
    return {
        "anchor_id": anchor_id, "anchor_text": "t", "anchor_grade": "K",
        "anchor_strand": None, "state": state, "tier": tier,
        "predictions": [
            {"rank": i, "ccss_id": code, "ccss_text": "x",
             "bi_score": score, "bi_rank": i}
            for i, (code, score) in enumerate(targets, start=1)],
    }


def tmpdir():
    return Path(tempfile.mkdtemp())


def counts():
    from collections import Counter
    return Counter()


# ----------------------------------------------------------------- direction

def test_state_to_ccss_puts_each_code_in_its_own_column():
    d = tmpdir()
    path = write_jsonl(d, "f.jsonl",
                       [anchor("FL.K.NSO.1.1", [("K.CC.A.3", 0.6)])])
    ccss_rows, state_rows = load_file(path, STATE_TO_CCSS, counts())
    assert state_rows == []
    state, state_code, ccss_code, direction = ccss_rows[0][:4]
    assert (state, state_code, ccss_code) == ("FL", "FL.K.NSO.1.1", "K.CC.A.3")
    assert direction == STATE_TO_CCSS


def test_ccss_to_state_reverses_the_pair():
    """
    `ccss_id` here holds 'TX.1.5D'. Trusting the field name would file a TEKS
    code as a CCSS one, and the generator would look for it under the wrong key
    forever.
    """
    d = tmpdir()
    path = write_jsonl(d, "f.jsonl",
                       [anchor("1.G.A.1", [("TX.1.5D", 0.23)], state="ccss")])
    ccss_rows, _state = load_file(path, CCSS_TO_STATE, counts())
    state, state_code, ccss_code, direction = ccss_rows[0][:4]
    assert (state, state_code, ccss_code) == ("TX", "TX.1.5D", "1.G.A.1")
    assert direction == CCSS_TO_STATE


def test_a_pair_contradicting_its_declared_direction_is_counted():
    d = tmpdir()
    path = write_jsonl(d, "f.jsonl",
                       [anchor("K.CC.A.3", [("K.CC.A.1", 0.5)], state="ccss")])
    stats = counts()
    ccss_rows, _state = load_file(path, STATE_TO_CCSS, stats)
    assert ccss_rows == []
    assert any("does not match the declared direction" in k for k in stats)


# ------------------------------------------------------------- the state label

def test_state_comes_from_the_code_not_the_label():
    """'Virginia ' with a trailing space, and 'fl' in lower case. Neither is used."""
    d = tmpdir()
    path = write_jsonl(d, "f.jsonl", [
        anchor("VA.K.NS.1", [("K.CC.A.3", 0.6)], state="Virginia "),
        anchor("FL.K.NSO.1.1", [("K.CC.A.3", 0.6)], state="fl"),
    ])
    ccss_rows, _state = load_file(path, STATE_TO_CCSS, counts())
    assert {r[0] for r in ccss_rows} == {"VA", "FL"}


def test_a_label_disagreeing_with_its_code_is_reported():
    d = tmpdir()
    path = write_jsonl(d, "f.jsonl",
                       [anchor("VA.K.NS.1", [("K.CC.A.3", 0.6)], state="TX")])
    stats = counts()
    load_file(path, STATE_TO_CCSS, stats)
    assert any("state label != code prefix" in k for k in stats)


def test_state_of_reads_a_ccss_code_as_no_state():
    assert state_of("K.CC.A.3") is None
    assert state_of("TX.1.5D") == "TX"


# --------------------------------------------------------------- state->state

def test_same_state_pairs_are_dropped_and_counted():
    """'FL.K.NSO.1.1 -> FL.K.GR.1.2' is in the real file, 379 anchors' worth."""
    d = tmpdir()
    path = write_jsonl(d, "f.jsonl", [anchor("FL.K.NSO.1.1", [
        ("FL.K.GR.1.2", 0.25), ("TX.K.2A", 0.24)])])
    stats = counts()
    ccss_rows, state_rows = load_file(path, STATE_TO_STATE, stats)
    assert ccss_rows == []
    assert [r[3] for r in state_rows] == ["TX.K.2A"]
    assert any("same-state pair dropped" in k for k in stats)


def test_state_to_state_never_lands_in_model_predictions():
    """Both sides are state codes. There is no ccss_code to key on."""
    d = tmpdir()
    write_jsonl(d, "big_three_to_big_three_k25.jsonl",
                [anchor("FL.K.NSO.1.1", [("TX.K.2A", 0.25)])])
    con = sqlite3.connect(":memory:")
    con.executescript(config.SCHEMA.read_text())
    load_all(con, d)
    assert con.execute("SELECT COUNT(*) FROM model_predictions").fetchone()[0] == 0
    assert con.execute(
        "SELECT COUNT(*) FROM model_state_predictions").fetchone()[0] == 1


# ------------------------------------------------------------------ the key

def test_both_directions_of_one_pair_survive():
    """
    The whole reason `direction` is in the primary key. These two rows are one
    pair measured twice, at 0.62 and 0.19 — scores from runs whose ranges do
    not overlap. Keeping one at random would discard a real measurement and
    leave no way to tell which.
    """
    d = tmpdir()
    write_jsonl(d, "state_to_ccss_k50.jsonl",
                [anchor("TX.1.5D", [("1.G.A.1", 0.62)], state="tx")])
    write_jsonl(d, "ccss_to_tx_k100.jsonl",
                [anchor("1.G.A.1", [("TX.1.5D", 0.19)], state="ccss")])
    con = sqlite3.connect(":memory:")
    con.executescript(config.SCHEMA.read_text())
    load_all(con, d)
    rows = con.execute(
        "SELECT direction, score FROM model_predictions"
        " WHERE state_code='TX.1.5D' AND ccss_code='1.G.A.1'"
        " ORDER BY direction").fetchall()
    assert rows == [(CCSS_TO_STATE, 0.19), (STATE_TO_CCSS, 0.62)]


def test_model_version_records_the_absence_and_tracks_content():
    """
    §6 puts model_version in the key and the offline run did not stamp one.
    The recorded value must say so, and must change when the file does.
    """
    d = tmpdir()
    a = write_jsonl(d, "state_to_ccss_k50.jsonl",
                    [anchor("FL.K.NSO.1.1", [("K.CC.A.3", 0.6)])])
    first = model_version(a)
    assert first.startswith("unstamped:state_to_ccss_k50@")
    write_jsonl(d, "state_to_ccss_k50.jsonl",
                [anchor("FL.K.NSO.1.1", [("K.CC.A.3", 0.7)])])
    assert model_version(a) != first


def test_a_target_repeated_within_an_anchor_keeps_the_best_rank():
    d = tmpdir()
    path = write_jsonl(d, "f.jsonl", [anchor("FL.K.NSO.1.1", [
        ("K.CC.A.1", 0.4), ("K.CC.A.3", 0.6), ("K.CC.A.1", 0.2)])])
    stats = counts()
    ccss_rows, _state = load_file(path, STATE_TO_CCSS, stats)
    ranks = {r[2]: r[4] for r in ccss_rows}
    assert ranks["K.CC.A.1"] == 1, "rank 1 beats the rank-3 restatement"
    assert any("target repeated within an anchor" in k for k in stats)


def test_the_tier_rides_along_verbatim():
    """
    Carried, not recomputed. The thresholds behind it were calibrated for one
    direction and applied to all six, so recomputing would launder that.
    """
    d = tmpdir()
    path = write_jsonl(d, "f.jsonl", [anchor(
        "FL.K.NSO.1.1", [("K.CC.A.3", 0.6)], tier="No Match")])
    ccss_rows, _state = load_file(path, STATE_TO_CCSS, counts())
    assert ccss_rows[0][6] == "No Match"


def test_a_missing_file_is_reported_not_ignored():
    con = sqlite3.connect(":memory:")
    con.executescript(config.SCHEMA.read_text())
    stats, per_file = load_all(con, tmpdir())
    assert per_file == {}
    assert any(k.startswith("MISSING:") for k in stats)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("all passed")
