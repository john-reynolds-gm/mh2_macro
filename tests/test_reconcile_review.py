"""
reconcile_review.py tests -- pinned to the brief's §7 acceptance criteria
(numbers below refer to that list):

  #1/#2/#13  mh2.db is opened read-only and mh2_seq.db is the only database
             written, plus the report file.
  #4         a proposal whose standard lands on the proposed node closes
             'landed' and the report/return carries both colors.
  #5         a proposal whose source_key vanishes (node reworded/removed)
             closes 'needs_attention', not silently.
  #6         a proposal resolved through a standard_alias tier still closes
             'landed', not 'open' -- same alias path the rollup itself uses.
  §5.2/§5.3/§5.4  tag_review / standard_review / standard_color_override are
             reported, never modified, on staleness.

Uses real file-backed sqlite databases (not :memory:) because
reconcile_review.py opens mh2.db with `mode=ro`, which requires a URI to an
actual file.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from mh2 import reconcile_review, review_store  # noqa: E402
from mh2.normalize import alias_key  # noqa: E402


def _mh2_db(path):
    con = sqlite3.connect(str(path))
    con.executescript(config.SCHEMA.read_text())
    return con


def _seq_db(path):
    con = review_store.connect(path)
    review_store.ensure_schema(con, config.SCHEMA_SEQ)
    return con


def _standard(con, standard_id, grade, sheet="CCSS"):
    con.execute(
        "INSERT INTO standards (standard_id, jurisdiction, grade, text, source)"
        " VALUES (?,?,?,?,?)", (standard_id, "CCSS", grade, "text", f"tagging:{sheet}"))
    # coverage.build_rows' denominator is standard_tag_status.sheet, not
    # `standards` directly (mh2/coverage.py, _denominator_rows) -- needed by
    # review_api.py's /api/audit, which reconcile_review.py itself never
    # calls (it drives off build_tags_by_standard instead).
    con.execute(
        "INSERT INTO standard_tag_status (standard_code, sheet, tagged_to_stem)"
        " VALUES (?,?,0)", (standard_id, sheet))


def _node(con, node_id, source_key, grades=(), stem_id=None, is_leaf=False):
    if stem_id is not None:
        con.execute("INSERT OR IGNORE INTO stems (stem_id, name) VALUES (?,?)",
                    (stem_id, stem_id))
    for g in grades:
        con.execute("INSERT INTO grade_order (grade, ord, band) SELECT ?, "
                    "(SELECT COALESCE(MAX(ord), 0) + 1 FROM grade_order), NULL"
                    " WHERE NOT EXISTS (SELECT 1 FROM grade_order WHERE grade = ?)",
                    (g, g))
    con.execute(
        "INSERT INTO nodes (node_id, stem_id, source_key, node_text) VALUES (?,?,?,?)",
        (node_id, stem_id, source_key, source_key))
    con.execute(
        "INSERT INTO node_grade_ruling (node_id, resolution, is_leaf)"
        " VALUES (?,?,?)", (node_id, "ruled", 1 if is_leaf else 0))
    con.executemany("INSERT INTO node_grade (node_id, grade) VALUES (?,?)",
                     [(node_id, g) for g in grades])


def _tag(con, node_id, standard_code):
    con.execute("INSERT INTO node_standards (node_id, standard_id) VALUES (?,?)",
                (node_id, standard_code))


def test_landed_proposal_reports_before_and_after_color(tmp_path):
    mh2 = _mh2_db(tmp_path / "mh2.db")
    _standard(mh2, "K.CC.A.1", "K")
    _node(mh2, "COM-0001", "COM::count more", grades=("K",))
    _tag(mh2, "COM-0001", "K.CC.A.1")
    mh2.commit()
    mh2.close()

    seq = _seq_db(tmp_path / "mh2_seq.db")
    pid = review_store.create_tag_proposal(seq, "K.CC.A.1", "COM::count more",
                                            "count more", "jane")
    seq.close()

    reconcile_review.run(tmp_path / "mh2.db", tmp_path / "mh2_seq.db", tmp_path / "out")

    seq = _seq_db(tmp_path / "mh2_seq.db")
    row = review_store.get_tag_proposal(seq, pid)
    assert row["state"] == "landed"
    assert "Red -> Green" in row["resolved_note"]
    report = (tmp_path / "out" / "review_reconcile.txt").read_text()
    assert "landed (closed" in report
    assert "K.CC.A.1" in report


def test_proposal_with_vanished_source_key_needs_attention(tmp_path):
    mh2 = _mh2_db(tmp_path / "mh2.db")
    _standard(mh2, "K.CC.A.1", "K")
    mh2.commit()
    mh2.close()

    seq = _seq_db(tmp_path / "mh2_seq.db")
    pid = review_store.create_tag_proposal(seq, "K.CC.A.1", "COM::a node reworded away",
                                            "old text", "jane")
    seq.close()

    reconcile_review.run(tmp_path / "mh2.db", tmp_path / "mh2_seq.db", tmp_path / "out")

    seq = _seq_db(tmp_path / "mh2_seq.db")
    assert review_store.get_tag_proposal(seq, pid)["state"] == "needs_attention"


def test_proposal_landed_elsewhere_in_same_stem(tmp_path):
    mh2 = _mh2_db(tmp_path / "mh2.db")
    _standard(mh2, "K.CC.A.1", "K")
    _node(mh2, "COM-0001", "COM::already tagged node", grades=("K",), stem_id="COM")
    _node(mh2, "COM-0002", "COM::proposed node", grades=("K",), stem_id="COM")
    _tag(mh2, "COM-0001", "K.CC.A.1")
    mh2.commit()
    mh2.close()

    seq = _seq_db(tmp_path / "mh2_seq.db")
    pid = review_store.create_tag_proposal(seq, "K.CC.A.1", "COM::proposed node",
                                            "proposed node", "jane")
    seq.close()

    reconcile_review.run(tmp_path / "mh2.db", tmp_path / "mh2_seq.db", tmp_path / "out")

    seq = _seq_db(tmp_path / "mh2_seq.db")
    assert review_store.get_tag_proposal(seq, pid)["state"] == "landed_elsewhere"


def test_proposal_stays_open_when_nothing_landed(tmp_path):
    mh2 = _mh2_db(tmp_path / "mh2.db")
    _standard(mh2, "K.CC.A.1", "K")
    _node(mh2, "COM-0001", "COM::still just proposed", grades=("K",), stem_id="COM")
    mh2.commit()
    mh2.close()

    seq = _seq_db(tmp_path / "mh2_seq.db")
    pid = review_store.create_tag_proposal(seq, "K.CC.A.1", "COM::still just proposed",
                                            "still just proposed", "jane")
    seq.close()

    reconcile_review.run(tmp_path / "mh2.db", tmp_path / "mh2_seq.db", tmp_path / "out")

    seq = _seq_db(tmp_path / "mh2_seq.db")
    assert review_store.get_tag_proposal(seq, pid)["state"] == "open"


def test_proposal_lands_through_alias_resolution(tmp_path):
    """#6: the ladder spells the standard differently but resolves to the
    same standard via the punct alias tier -- must still close 'landed'."""
    mh2 = _mh2_db(tmp_path / "mh2.db")
    _standard(mh2, "K.CC.A.1", "K")
    _node(mh2, "COM-0001", "COM::alias node", grades=("K",))
    _tag(mh2, "COM-0001", "K.CC.A1")  # missing dot -- not an exact match
    mh2.execute("INSERT INTO standard_alias (alias_key, tier, standard_id) VALUES (?,?,?)",
                (alias_key("K.CC.A1", "punct"), "punct", "K.CC.A.1"))
    mh2.commit()
    mh2.close()

    seq = _seq_db(tmp_path / "mh2_seq.db")
    # The proposal is recorded against the RESOLVED standard_id -- review_api
    # creates proposals this way (see standard_detail/tags_by_standard).
    pid = review_store.create_tag_proposal(seq, "K.CC.A.1", "COM::alias node",
                                            "alias node", "jane")
    seq.close()

    reconcile_review.run(tmp_path / "mh2.db", tmp_path / "mh2_seq.db", tmp_path / "out")

    seq = _seq_db(tmp_path / "mh2_seq.db")
    assert review_store.get_tag_proposal(seq, pid)["state"] == "landed"


def test_tag_review_reports_vanished_source_key_but_never_modifies(tmp_path):
    mh2 = _mh2_db(tmp_path / "mh2.db")
    _standard(mh2, "K.CC.A.1", "K")
    mh2.commit()
    mh2.close()

    seq = _seq_db(tmp_path / "mh2_seq.db")
    review_store.set_tag_review(seq, "K.CC.A.1", "COM::gone now", "confirmed", "jane",
                                 ladder_file_seen="Comparing.docx")
    seq.close()

    reconcile_review.run(tmp_path / "mh2.db", tmp_path / "mh2_seq.db", tmp_path / "out")

    seq = _seq_db(tmp_path / "mh2_seq.db")
    row = review_store.get_tag_review(seq, "K.CC.A.1", "COM::gone now")
    assert row is not None and row["outcome"] == "confirmed"  # untouched
    report = (tmp_path / "out" / "review_reconcile.txt").read_text()
    assert "COM::gone now" in report


def test_standard_review_staleness_reported_never_modified(tmp_path):
    mh2 = _mh2_db(tmp_path / "mh2.db")
    _standard(mh2, "K.CC.A.1", "K")
    _node(mh2, "COM-0001", "COM::now covered", grades=("K",))
    _tag(mh2, "COM-0001", "K.CC.A.1")
    mh2.commit()
    mh2.close()

    seq = _seq_db(tmp_path / "mh2_seq.db")
    review_store.set_standard_review(seq, "K.CC.A.1", "confirmed", "Red", "jane")
    seq.close()

    reconcile_review.run(tmp_path / "mh2.db", tmp_path / "mh2_seq.db", tmp_path / "out")

    seq = _seq_db(tmp_path / "mh2_seq.db")
    row = review_store.get_standard_review(seq, "K.CC.A.1")
    assert row["computed_color_at_review"] == "Red"  # never modified
    report = (tmp_path / "out" / "review_reconcile.txt").read_text()
    assert "K.CC.A.1" in report and "may be stale" in report


def test_override_becomes_redundant_when_rollup_catches_up(tmp_path):
    mh2 = _mh2_db(tmp_path / "mh2.db")
    _standard(mh2, "K.CC.A.1", "K")
    _node(mh2, "COM-0001", "COM::now green", grades=("K",))
    _tag(mh2, "COM-0001", "K.CC.A.1")
    mh2.commit()
    mh2.close()

    seq = _seq_db(tmp_path / "mh2_seq.db")
    review_store.set_standard_override(seq, "K.CC.A.1", "Green", "Red",
                                        "writer says it's fine", "jane")
    seq.close()

    reconcile_review.run(tmp_path / "mh2.db", tmp_path / "mh2_seq.db", tmp_path / "out")

    report = (tmp_path / "out" / "review_reconcile.txt").read_text()
    assert "redundant" in report and "K.CC.A.1" in report
    seq = _seq_db(tmp_path / "mh2_seq.db")
    # still present, offered for retirement -- not auto-retired
    assert review_store.get_standard_override(seq, "K.CC.A.1") is not None
