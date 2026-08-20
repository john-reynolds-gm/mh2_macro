"""
Candidate generation tests (§3, §4 — step 6).

Three things here are load-bearing and everything else is arithmetic:

  independence   a model_suggestions row descends from lesson overlap and the
                 Path C model, so Path A agreeing with one is lesson overlap
                 agreeing with a copy of itself. It must never reach `strong`.
                 That is the rev-3 defect §4 was written to fix, and it is one
                 dict key away from coming back.
  demote-only    the audit ran Path A's own method. A rejecting verdict lowers
                 a candidate; a confirming one changes nothing.
  no filtering   grade is a prior. A three-grade offset is demoted and still
                 emitted; a filter there would discard a third of the FL and TX
                 human tags.

Run with: python tests/test_candidates.py
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

from mh2.candidates import (  # noqa: E402
    CA_CANON_ONLY_SCORE, GRADE_DECAY, GRADE_OUT_OF_WINDOW, MODERATE,
    PATH_C_TOP_K, STRONG, SURFACE_THRESHOLD, WEAK, Audit, Path0, PathA, PathB,
    PathC, apply_decay, demote, generate, grade_factor, strength_of,
)


def db(**tables) -> sqlite3.Cursor:
    """A fresh in-memory database on the real schema, seeded row by row."""
    con = sqlite3.connect(":memory:")
    con.executescript(config.SCHEMA.read_text())
    for table, rows in tables.items():
        if not rows:
            continue
        cols = ",".join(rows[0])
        marks = ",".join("?" * len(rows[0]))
        con.executemany(f"INSERT INTO {table} ({cols}) VALUES ({marks})",
                        [tuple(r.values()) for r in rows])
    con.commit()
    return con.cursor()


def std(code, juris, text=None, grade=None):
    return {"standard_id": code, "jurisdiction": juris, "text": text,
            "grade": grade}


# ------------------------------------------------------------- independence

def test_two_independent_paths_are_strong():
    assert strength_of({"pathA": {}, "pathB": {}}, False) == STRONG


def test_path0_exact_is_strong_on_its_own():
    assert strength_of({"path0": {}}, True) == STRONG


def test_path0_without_text_equality_is_not_strong():
    assert strength_of({"path0": {}}, False) == MODERATE


def test_one_deterministic_path_is_moderate():
    for path in ("path0", "pathA", "pathB"):
        assert strength_of({path: {}}, False) == MODERATE, path


def test_model_only_is_weak():
    assert strength_of({"pathC": {}}, False) == WEAK


def test_a_suggestion_cannot_corroborate_path_a():
    """
    The rev-3 defect, pinned. scored_alignments was built from lesson overlap
    plus the Path C model; letting it agree with Path A promotes one source
    counted twice.
    """
    assert strength_of({"pathA": {}, "suggestion": {}}, False) == MODERATE


def test_a_suggestion_cannot_corroborate_the_model():
    assert strength_of({"pathC": {}, "suggestion": {}}, False) == WEAK


def test_a_suggestion_alone_is_weak():
    assert strength_of({"suggestion": {}}, False) == WEAK


def test_demote_bottoms_out_at_weak():
    assert demote(STRONG) == MODERATE
    assert demote(MODERATE) == WEAK
    assert demote(WEAK) == WEAK


# ------------------------------------------------------------- grade prior

def test_same_grade_is_undecayed():
    factor, offset = grade_factor("4.G.A.1", "TX.4.6A")
    assert (factor, offset) == (1.0, 0)


def test_one_grade_off_decays_once():
    factor, offset = grade_factor("4.G.A.1", "TX.5.6A")
    assert offset == 1 and factor == GRADE_DECAY


def test_outside_the_window_is_demoted_not_dropped():
    factor, offset = grade_factor("1.G.A.1", "TX.5.6A")
    assert offset == 4
    assert factor == GRADE_OUT_OF_WINDOW
    assert factor > 0, "a hard grade filter discards a third of the FL/TX tags"


def test_pk_sorts_below_k():
    _factor, offset = grade_factor("K.CC.A.1", "SD.PK.CD-4.g")
    assert offset == -1


def test_the_prior_still_penalises_a_negative_score():
    """
    bi_score is a cosine and ccss_to_ca_k50 reaches -0.035. Multiplying that by
    a decay makes it LARGER: -0.035 * 0.35 = -0.012, which lifts a four-grade-
    off pair above an on-grade one. The prior has to push one way only.
    """
    on_grade = apply_decay(-0.035, 1.0)
    far_off = apply_decay(-0.035, GRADE_OUT_OF_WINDOW)
    assert far_off < on_grade
    assert apply_decay(0.5, GRADE_DECAY) < 0.5, "and still penalises a positive one"


def test_a_negative_scoring_candidate_still_records_its_anchor():
    """A row built with best_anchor = None has no evidence and cannot be shown."""
    cur = node_fixture(model_predictions=[
        {"state": "CA", "state_code": "CA.9-12.F-LE.4.3", "ccss_code": "4.G.A.1",
         "direction": "ccss_to_state", "rank": 1, "score": -0.035,
         "model_version": "v1"}])
    rows, _stats = generate(cur)
    row = next(r for r in rows if r["state"] == "CA")
    assert row["anchor_ccss_code"] == "4.G.A.1"
    assert row["combined_score"] is not None


def test_only_the_top_k_ranks_reach_the_shortlist():
    cur = node_fixture(model_predictions=[
        {"state": "TX", "state_code": f"TX.4.{i}A", "ccss_code": "4.G.A.1",
         "direction": "state_to_ccss", "rank": i, "score": 0.5,
         "model_version": "v1"}
        for i in range(1, PATH_C_TOP_K + 5)])
    rows, _stats = generate(cur)
    assert len(rows) == PATH_C_TOP_K


def test_the_better_ranked_direction_wins_a_duplicated_pair():
    """
    Not the better SCORE: state_to_ccss's median top-1 is 0.604 and
    ccss_to_state's is 0.19, so comparing scores across directions compares
    two different rulers.
    """
    cur = node_fixture(model_predictions=[
        {"state": "TX", "state_code": "TX.4.6A", "ccss_code": "4.G.A.1",
         "direction": "state_to_ccss", "rank": 7, "score": 0.62,
         "model_version": "v1"},
        {"state": "TX", "state_code": "TX.4.6A", "ccss_code": "4.G.A.1",
         "direction": "ccss_to_state", "rank": 2, "score": 0.19,
         "model_version": "v1"}])
    rows, _stats = generate(cur)
    row = next(r for r in rows if r["state"] == "TX")
    assert row["score_model"] == 0.19, "rank 2 beats rank 7"


def test_a_code_with_no_grade_gets_no_prior():
    factor, offset = grade_factor("4.G.A.1", "PA.M03.A-T.1.1.4")
    assert (factor, offset) == (1.0, None)


# ------------------------------------------------------------------ Path 0

def test_path0_requires_code_and_text():
    cur = db(standards=[
        std("1.MD.B.3", "CCSS", "Tell and write time in hours."),
        std("CA.1.MD.3", "CA", "Tell and write time in hours."),
    ])
    edges = Path0(cur).edges("1.MD.B.3")
    assert edges["CA.1.MD.3"][0] == 1.0
    assert edges["CA.1.MD.3"][1]["match"] == "code_and_text"


def test_path0_emits_a_canon_collision_but_never_as_exact():
    """
    1.MD.A.3 and 1.MD.B.3 both canon to 1.MD.3. Text equality is what tells
    them apart -- but the loser is still emitted, at a lower score and never
    as `strong`, because dropping it silently costs real CA coverage.
    """
    cur = db(standards=[
        std("1.MD.A.3", "CCSS", "Order three objects by length."),
        std("CA.1.MD.3", "CA", "Tell and write time in hours."),
    ])
    edges = Path0(cur).edges("1.MD.A.3")
    assert edges["CA.1.MD.3"] == (CA_CANON_ONLY_SCORE, {"match": "canon_only"})
    assert strength_of({"path0": {}}, False) == MODERATE


def test_path0_skips_the_not_ccss_sheet():
    cur = db(
        standards=[std("1.MD.B.3", "CCSS", "Tell and write time in hours."),
                   std("CA.1.MD.3", "CA", "Tell and write time in hours.")],
        standard_tag_status=[{"standard_code": "CA.1.MD.3",
                              "sheet": "California-not CCSS",
                              "tagged_to_stem": 1}])
    assert Path0(cur).edges("1.MD.B.3") == {}


def test_path0_is_california_only():
    cur = db(standards=[
        std("1.MD.B.3", "CCSS", "Tell and write time in hours."),
        std("TX.1.MD.3", "TX", "Tell and write time in hours."),
    ])
    assert Path0(cur).edges("1.MD.B.3") == {}


# ------------------------------------------------------------------ Path A

def lesson(code, lesson_id, source="all_states"):
    return {"standard_code": code, "lesson_id": lesson_id, "source": source}


def test_path_a_scores_jaccard_and_names_the_lessons():
    cur = db(standard_lessons=[
        lesson("4.G.A.1", "L1"), lesson("4.G.A.1", "L2"),
        lesson("SC.4.G.1", "L2"), lesson("SC.4.G.1", "L3"),
    ])
    score, evidence = PathA(cur).edges("4.G.A.1")["SC.4.G.1"]
    assert score == 1 / 3, "one shared lesson over three distinct"
    assert evidence["shared_lessons"] == ["L2"]


def test_path_a_emits_on_a_single_shared_lesson():
    cur = db(standard_lessons=[lesson("4.G.A.1", "L1"),
                               lesson("SC.4.G.1", "L1")])
    assert "SC.4.G.1" in PathA(cur).edges("4.G.A.1")


def test_path_a_never_returns_a_ccss_code():
    cur = db(standard_lessons=[lesson("4.G.A.1", "L1", "guide"),
                               lesson("4.MD.C.5", "L1", "guide")])
    assert PathA(cur).edges("4.G.A.1") == {}


# ------------------------------------------------------------------ Path B

def edge(state, state_code, ccss, granularity="Standard"):
    return {"state": state, "state_code": state_code, "ccss_code": ccss,
            "source_code": state_code, "granularity": granularity}


def test_path_b_down_weights_a_non_peer_edge():
    cur = db(crosswalk=[edge("KS", "KS.1.NS.1", "1.NBT.B.2"),
                        edge("KS", "KS.1.NS", "1.NBT.B.2", "Cluster")])
    edges = PathB(cur).edges("1.NBT.B.2")
    assert edges["KS.1.NS.1"][0] == 1.0
    assert edges["KS.1.NS"][0] < 1.0, "a CCSS standard paired with a state Cluster"


# ------------------------------------------------------------------ Path C

def test_path_c_keeps_suggestions_out_of_its_edges():
    """
    The two sources share a column in `candidates` and must not share a bucket
    here -- edges() is what feeds the independent set.
    """
    cur = db(model_suggestions=[{"state": "SC", "state_code": "SC.4.G.1",
                                 "ccss_code": "4.G.A.1", "rationale": "x"}])
    path_c = PathC(cur)
    assert path_c.edges("4.G.A.1") == {}
    assert "SC.4.G.1" in path_c.suggestions("4.G.A.1")


# ------------------------------------------------------------------- audit

def test_audit_rejects_only_on_a_rejecting_verdict():
    cur = db(alignment_audit=[
        {"state": "SC", "state_code": "SC.4.G.1", "ccss_code": "4.G.A.1",
         "verdict": "likely_incorrect", "confidence": 99.0, "rationale": "no"},
        {"state": "SC", "state_code": "SC.5.G.1", "ccss_code": "4.G.A.1",
         "verdict": "confirmed", "confidence": 99.0, "rationale": "yes"},
    ])
    audit = Audit(cur)
    assert audit.rejects(audit.verdict("SC.4.G.1", "4.G.A.1")[0])
    assert not audit.rejects(audit.verdict("SC.5.G.1", "4.G.A.1")[0])
    assert audit.verdict("SC.9.G.9", "4.G.A.1") is None


# -------------------------------------------------------------- end to end

def node_fixture(**extra):
    """One node, one CCSS anchor, plus whatever path data the test needs."""
    base = dict(
        stems=[{"stem_id": "ANG", "name": "Angles"}],
        nodes=[{"node_id": "N1", "stem_id": "ANG", "source_key": "ANG|n1",
                "node_text": "n1", "concept_skill": "Turns"}],
        node_standards_parsed=[{"node_id": "N1", "standard_code": "4.G.A.1",
                                "state": None, "source_cell": "cell"}],
    )
    base.update(extra)
    return db(**base)


def test_agreement_across_two_paths_is_strong_end_to_end():
    cur = node_fixture(
        standard_lessons=[lesson("4.G.A.1", "L1"), lesson("SC.4.G.1", "L1")],
        crosswalk=[edge("SC", "SC.4.G.1", "4.G.A.1")])
    rows, _stats = generate(cur)
    row = next(r for r in rows if r["standard_code"] == "SC.4.G.1")
    assert row["strength"] == STRONG
    assert row["score_overlap"] is not None and row["score_crosswalk"] is not None
    assert row["node_id"] == "N1" and row["anchor_ccss_code"] == "4.G.A.1"


def test_the_audit_demotes_that_agreement():
    cur = node_fixture(
        standard_lessons=[lesson("4.G.A.1", "L1"), lesson("SC.4.G.1", "L1")],
        crosswalk=[edge("SC", "SC.4.G.1", "4.G.A.1")],
        alignment_audit=[{"state": "SC", "state_code": "SC.4.G.1",
                          "ccss_code": "4.G.A.1", "verdict": "likely_incorrect",
                          "confidence": 99.0, "rationale": "no"}])
    rows, stats = generate(cur)
    row = next(r for r in rows if r["standard_code"] == "SC.4.G.1")
    assert row["strength"] == MODERATE
    assert stats["demoted_by_audit"] == 1


def test_a_confirming_audit_changes_nothing():
    cur = node_fixture(
        standard_lessons=[lesson("4.G.A.1", "L1"), lesson("SC.4.G.1", "L1")],
        alignment_audit=[{"state": "SC", "state_code": "SC.4.G.1",
                          "ccss_code": "4.G.A.1", "verdict": "confirmed",
                          "confidence": 99.0, "rationale": "yes"}])
    rows, stats = generate(cur)
    row = next(r for r in rows if r["standard_code"] == "SC.4.G.1")
    assert row["strength"] == MODERATE, "one path, and the audit cannot promote"
    assert stats.get("demoted_by_audit", 0) == 0


def test_texas_generates_nothing_from_the_deterministic_paths():
    """
    TX is absent from all three deterministic sources, so with Path C empty the
    correct output is zero rows. Step 8 has since loaded Path C and TX now
    generates plenty — this pins the reason it does: the model, and nothing
    else. It is the test that fails the day someone adds a text-similarity
    fallback to fill the gap.
    """
    cur = node_fixture(
        standard_lessons=[lesson("4.G.A.1", "L1"), lesson("SC.4.G.1", "L1")],
        crosswalk=[edge("SC", "SC.4.G.1", "4.G.A.1")])
    rows, _stats = generate(cur)
    assert [r for r in rows if r["state"] == "TX"] == []


def test_path_c_alone_is_weak_and_that_is_how_tx_will_arrive():
    cur = node_fixture(model_predictions=[
        {"state": "TX", "state_code": "TX.4.6A", "ccss_code": "4.G.A.1",
         "direction": "state_to_ccss", "rank": 1, "score": 0.81,
         "model_version": "v1"}])
    rows, _stats = generate(cur)
    row = next(r for r in rows if r["state"] == "TX")
    assert row["strength"] == WEAK
    assert row["score_model"] == 0.81


def test_an_unanchored_node_is_counted_not_dropped():
    cur = db(stems=[{"stem_id": "ANG", "name": "Angles"}],
             nodes=[{"node_id": "N9", "stem_id": "ANG", "source_key": "ANG|n9",
                     "node_text": "n9", "concept_skill": "Turns"}])
    _rows, stats = generate(cur)
    assert stats["nodes_unanchored"] == 1


def test_an_absence_assertion_does_not_suppress_state_candidates():
    """
    §1/§3.5, reversed from rev 2: a node CCSS omits is exactly where a state
    standard is most likely to exist. The assertion scopes to CCSS only.
    """
    cur = node_fixture(
        standard_lessons=[lesson("4.G.A.1", "L1"), lesson("SC.4.G.1", "L1")],
        node_absence_assertions=[{"node_id": "N1", "framework": "CCSS",
                                  "note": "No CCSSM for ordinal numbers",
                                  "source_cell": "cell"}])
    rows, _stats = generate(cur)
    assert any(r["standard_code"] == "SC.4.G.1" for r in rows)


def test_one_anchor_on_two_sibling_nodes_yields_a_row_each():
    """
    §9 Q1: multi-node tagging is legitimate authoring. Both nodes get the
    candidate, and node_confidence records that the anchor was shared.
    """
    cur = db(
        stems=[{"stem_id": "ANG", "name": "Angles"}],
        nodes=[{"node_id": "N1", "stem_id": "ANG", "source_key": "ANG|n1",
                "node_text": "n1", "concept_skill": "Turns"},
               {"node_id": "N2", "stem_id": "ANG", "source_key": "ANG|n2",
                "node_text": "n2", "concept_skill": "Turns"}],
        node_standards_parsed=[
            {"node_id": "N1", "standard_code": "4.G.A.1", "state": None,
             "source_cell": "c"},
            {"node_id": "N2", "standard_code": "4.G.A.1", "state": None,
             "source_cell": "c"}],
        standard_lessons=[lesson("4.G.A.1", "L1"), lesson("SC.4.G.1", "L1")])
    rows, _stats = generate(cur)
    got = [r for r in rows if r["standard_code"] == "SC.4.G.1"]
    assert {r["node_id"] for r in got} == {"N1", "N2"}
    assert all(r["node_confidence"] == 0.5 for r in got)


def test_a_sole_node_keeps_full_confidence():
    cur = node_fixture(standard_lessons=[lesson("4.G.A.1", "L1"),
                                         lesson("SC.4.G.1", "L1")])
    rows, _stats = generate(cur)
    assert all(r["node_confidence"] == 1.0 for r in rows)


def test_states_filter_narrows_without_changing_scores():
    cur = node_fixture(standard_lessons=[
        lesson("4.G.A.1", "L1"), lesson("SC.4.G.1", "L1"),
        lesson("KS.4.G.1", "L1")])
    everything, _ = generate(cur)
    narrowed, _ = generate(cur, {"SC"})
    assert {r["state"] for r in narrowed} == {"SC"}
    sc = next(r for r in everything if r["state"] == "SC")
    assert narrowed[0]["combined_score"] == sc["combined_score"]


# --------------------------------------------------- step 7: surface / browse

def test_weak_below_threshold_is_browse_only():
    cur = node_fixture(model_predictions=[
        {"state": "TX", "state_code": "TX.4.6A", "ccss_code": "4.G.A.1",
         "direction": "state_to_ccss", "rank": 1,
         "score": SURFACE_THRESHOLD - 0.05, "model_version": "v1"}])
    rows, _stats = generate(cur)
    row = next(r for r in rows if r["state"] == "TX")
    assert row["strength"] == WEAK
    assert row["auto_surface"] is False


def test_weak_at_or_above_threshold_is_auto_surfaced():
    cur = node_fixture(model_predictions=[
        {"state": "TX", "state_code": "TX.4.6A", "ccss_code": "4.G.A.1",
         "direction": "state_to_ccss", "rank": 1,
         "score": SURFACE_THRESHOLD, "model_version": "v1"}])
    rows, _stats = generate(cur)
    row = next(r for r in rows if r["state"] == "TX")
    assert row["auto_surface"] is True


def test_moderate_auto_surfaces_regardless_of_score():
    """
    A deterministic path already clears a higher bar than any Path C score --
    SURFACE_THRESHOLD gates weak, Path-C-only rows ONLY (spec §5: "for TX and
    FL only [Path C] exists"), never a row a second path already corroborates.
    """
    cur = node_fixture(crosswalk=[
        edge("SC", "SC.4.G.1", "4.G.A.1", "Cluster")])  # 0.5, well under 0.70
    rows, _stats = generate(cur)
    row = next(r for r in rows if r["standard_code"] == "SC.4.G.1")
    assert row["strength"] == MODERATE
    assert row["combined_score"] < SURFACE_THRESHOLD
    assert row["auto_surface"] is True


def test_browse_rank_reads_from_model_reranks():
    cur = node_fixture(
        model_predictions=[
            {"state": "TX", "state_code": "TX.4.6A", "ccss_code": "4.G.A.1",
             "direction": "state_to_ccss", "rank": 1, "score": 0.3,
             "model_version": "v1"}],
        model_reranks=[
            {"node_id": "N1", "state": "TX", "standard_code": "TX.4.6A",
             "rerank_rank": 3, "relevance": "partial",
             "model_version": "mh2.rerank:test"}])
    rows, _stats = generate(cur)
    row = next(r for r in rows if r["state"] == "TX")
    assert row["browse_rank"] == 3
    assert row["auto_surface"] is False, "below threshold and unaffected by rerank"


def test_browse_rank_is_none_without_a_rerank_row():
    cur = node_fixture(model_predictions=[
        {"state": "TX", "state_code": "TX.4.6A", "ccss_code": "4.G.A.1",
         "direction": "state_to_ccss", "rank": 1, "score": 0.3,
         "model_version": "v1"}])
    rows, _stats = generate(cur)
    row = next(r for r in rows if r["state"] == "TX")
    assert row["browse_rank"] is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("all passed")
