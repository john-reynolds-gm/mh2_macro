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


def test_build_stem_names_maps_id_to_name_and_collapses_whitespace():
    con = fresh_db()
    con.execute("INSERT INTO stems (stem_id, name) VALUES (?,?)", ("COU", "Counting"))
    con.execute("INSERT INTO stems (stem_id, name) VALUES (?,?)",
                ("WHO", "Whole Numbers and \nBase Ten Structure"))
    names = node_lookup.build_stem_names(con)
    assert names == {"COU": "Counting", "WHO": "Whole Numbers and Base Ten Structure"}


def test_nodes_for_stem_orders_by_concept_skill_then_seq():
    con = fresh_db()
    con.execute("INSERT INTO stems (stem_id, name) VALUES ('COU', 'Counting')")
    _add_node(con, "COU-0001", "COU::key-b", "second in skill A", stem_id="COU", seq=2)
    _add_node(con, "COU-0002", "COU::key-a", "first in skill A", stem_id="COU", seq=1)
    _add_node(con, "COU-0003", "COU::key-c", "only one in skill B", stem_id="COU", seq=3)
    con.execute("UPDATE nodes SET concept_skill = 'Skill A' WHERE node_id IN ('COU-0001','COU-0002')")
    con.execute("UPDATE nodes SET concept_skill = 'Skill B' WHERE node_id = 'COU-0003'")

    nodes = node_lookup.nodes_for_stem(con, "COU")
    assert [n.source_key for n in nodes] == ["COU::key-a", "COU::key-b", "COU::key-c"]
    assert [n.concept_skill for n in nodes] == ["Skill A", "Skill A", "Skill B"]


def test_nodes_for_stem_excludes_other_stems():
    con = fresh_db()
    _add_node(con, "COU-0001", "COU::a", "a", stem_id="COU", seq=1)
    _add_node(con, "ORD-0001", "ORD::b", "b", stem_id="ORD", seq=1)
    nodes = node_lookup.nodes_for_stem(con, "COU")
    assert [n.source_key for n in nodes] == ["COU::a"]


def test_nodes_for_stem_null_concept_skill_falls_back_to_ungrouped():
    con = fresh_db()
    _add_node(con, "SUB-0001", "SUB::a", "a", stem_id="SUB", seq=1)
    nodes = node_lookup.nodes_for_stem(con, "SUB")
    assert nodes[0].concept_skill == "Ungrouped"


def test_nodes_for_stem_collapses_whitespace_in_text_and_skill():
    con = fresh_db()
    _add_node(con, "COU-0001", "COU::a", "  messy   text\nhere ", stem_id="COU", seq=1)
    con.execute("UPDATE nodes SET concept_skill = ' Skill  A \n' WHERE node_id = 'COU-0001'")
    nodes = node_lookup.nodes_for_stem(con, "COU")
    assert nodes[0].node_text == "messy text here"
    assert nodes[0].concept_skill == "Skill A"
