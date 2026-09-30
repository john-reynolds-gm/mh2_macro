"""
Pure-function tests for scripts/report_cross_stem_links.py: the text helpers
that decide what counts as a stated cross-stem mention. No database.

Run with: python -m pytest tests/test_cross_stem_links.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.report_cross_stem_links import (  # noqa: E402
    extract_mentions, jaccard, noise_reason, norm, split_group, stem_key,
)


def test_norm_handles_ampersand_punctuation_and_parens():
    assert norm("Integers & Rational Numbers") == "integers and rational numbers"
    assert norm("Whole Numbers and \nBase Ten Structure") == "whole numbers and base ten structure"
    assert norm("Multiplication and Division (Whole Numbers)") == "multiplication and division"


def test_split_group():
    assert split_group("Measurement and Data (Time, Money)") == \
        ("Measurement and Data", ["Time", "Money"])
    assert split_group("Counting") == ("Counting", [])


def test_stem_key_joins_inflections():
    assert stem_key("classification") == stem_key("classify")
    assert stem_key("rounding") == stem_key("round")
    assert stem_key("shapes") == stem_key("shape")


def test_noise_reasons():
    assert noise_reason("Likely 1 instructional period") == "time_estimate"
    assert noise_reason("GK: 1-2") == "time_estimate"
    assert noise_reason("GK M5 L7") is not None
    assert noise_reason("Fluency Ideas") == "section_heading"
    assert noise_reason("“By fluent we mean students can recall facts”") == "quotation"
    assert noise_reason("Whole Numbers and Base Ten Structure") is None
    assert noise_reason("Lays the foundation for counting on in Addition and Subtraction") is None


def test_run_stops_at_first_noise_line():
    lines = ["Fluency Ideas", "x (EM2 G1 M1 L1)", "Associated Concepts from Other Stems:",
             "Spatial Thinking", "Instructional Period", "1-2"]
    assert extract_mentions(lines) == [("run_item", "Spatial Thinking")]


def test_header_with_inline_text():
    lines = ["Associated concepts from other stems: Whole Numbers and Base Ten Structure",
             "“A quotation that follows”"]
    assert extract_mentions(lines) == [("assoc_header", "Whole Numbers and Base Ten Structure")]


def test_alternate_headers_are_found():
    assert extract_mentions(["Related concepts:", "Comparing numbers", "Launch Ideas:"]) == \
        [("run_item", "Comparing numbers")]
    assert extract_mentions(["Related to:", "Counting", "Estimating"]) == \
        [("run_item", "Counting"), ("run_item", "Estimating")]
    assert extract_mentions(["Associated concepts: Modeling"]) == [("assoc_short_header", "Modeling")]


def test_prose_cue_without_header():
    got = extract_mentions(["We will see an overlap with Base Ten structure with teen numbers."])
    assert [k for k, _ in got] == ["prose_cue"]
    assert extract_mentions(["Fluency Ideas", "Sprint: Count by Fives"]) == []


def test_jaccard():
    assert jaccard({"a", "b"}, {"b", "c"}) == 1 / 3
    assert jaccard(set(), set()) == 0.0
