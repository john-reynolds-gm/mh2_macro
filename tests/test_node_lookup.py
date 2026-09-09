"""
node_lookup.py tests -- the one query shape reconcile_review.py and
review_api.py both depend on for source_key/node_text/ladder_file
resolution (Session C brief §1.3).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from mh2 import node_lookup  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_coverage import fresh_db  # noqa: E402


def _add_node(con, node_id, source_key, node_text, source_file=None, stem_id=None, seq=None):
    if stem_id is not None:
        con.execute(
            "INSERT OR IGNORE INTO stems (stem_id, name) VALUES (?,?)",
            (stem_id, stem_id))
    con.execute(
        "INSERT INTO nodes (node_id, stem_id, seq, source_key, node_text, source_file)"
        " VALUES (?,?,?,?,?,?)",
        (node_id, stem_id, seq, source_key, node_text, source_file))


def test_build_node_lookup_keyed_by_node_id():
    con = fresh_db()
    _add_node(con, "COM-0001", "COM::compare two numbers", "compare two numbers",
              source_file="ComparingandOrdering.docx", stem_id="COM", seq=1)
    by_id = node_lookup.build_node_lookup(con)
    assert set(by_id) == {"COM-0001"}
    info = by_id["COM-0001"]
    assert info.source_key == "COM::compare two numbers"
    assert info.node_text == "compare two numbers"
    assert info.source_file == "ComparingandOrdering.docx"
    assert info.stem_id == "COM"
    assert info.seq == 1


def test_build_source_key_lookup_is_one_to_one_reindex():
    con = fresh_db()
    _add_node(con, "COM-0001", "key-a", "text a")
    _add_node(con, "ORD-0001", "key-b", "text b")
    by_key = node_lookup.build_source_key_lookup(con)
    assert set(by_key) == {"key-a", "key-b"}
    assert by_key["key-a"].node_id == "COM-0001"
    assert by_key["key-b"].node_id == "ORD-0001"


def test_missing_node_is_absent_from_both_lookups():
    con = fresh_db()
    _add_node(con, "COM-0001", "key-a", "text a")
    by_id = node_lookup.build_node_lookup(con)
    by_key = node_lookup.build_source_key_lookup(con)
    assert "no-such-node" not in by_id
    assert "no-such-key" not in by_key
