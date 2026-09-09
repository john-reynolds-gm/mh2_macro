"""
load_standards.py tests — the gaps-sheet welded-code fix (rev 4 §8).

'WI.PK.B.EL.5 not numbered' is a writer's placeholder for a code the source
never numbered. Left alone, normalize_code's whitespace strip welds the prose
onto the code ('WI.PK.B.EL.5notnumbered'), producing a standard no ladder tag
can ever resolve to -- rev 2 flagged the resulting duplicate rows as a data
defect. The fix strips the trailing prose before normalize_code ever runs.

Run with: python -m pytest tests/ -q   (or: python tests/test_load_standards.py)
"""
import csv
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

import config  # noqa: E402

from mh2.load_standards import load_gaps_sheet, load_stem_inventory  # noqa: E402
from mh2.normalize import strip_trailing_prose  # noqa: E402


def test_strip_trailing_prose_removes_not_numbered():
    assert strip_trailing_prose("WI.PK.B.EL.5 not numbered") == "WI.PK.B.EL.5"
    assert strip_trailing_prose("PA.PK.2.4.PK.A.1 not numbered") == "PA.PK.2.4.PK.A.1"
    assert strip_trailing_prose("OR.PK.OA 1 not numbered") == "OR.PK.OA 1"


def test_strip_trailing_prose_leaves_ordinary_codes_alone():
    assert strip_trailing_prose("AL.1.DA.16.b") == "AL.1.DA.16.b"


def _write_gaps_sheet(rows) -> Path:
    tmpdir = Path(tempfile.mkdtemp())
    path = tmpdir / "gaps.xlsx"
    df = pd.DataFrame(rows, columns=[
        "State", "Category (new list)", "Grade", "Tagged to a Stem",
        "Standard Code", "Standard Text", "EM2 Alignment Notes",
    ])
    with pd.ExcelWriter(path) as xw:
        df.to_excel(xw, sheet_name="Other State Standards Gaps", index=False)
    return path


def _fresh_db():
    con = sqlite3.connect(":memory:")
    con.executescript(config.SCHEMA.read_text())
    return con


def test_load_gaps_sheet_welds_are_unwelded_at_load():
    """
    The real sheet carries 'WI.PK.B.EL.5 not numbered' TWICE -- rev 2's
    'duplicate' finding. Stripped and normalized, both rows resolve to the
    SAME clean code, which is exactly what first-writer-wins is for.
    """
    row = {
        "State": "Wisconsin", "Category (new list)": "Money: identification",
        "Grade": "PK", "Tagged to a Stem": "Yes",
        "Standard Code": "WI.PK.B.EL.5 not numbered",
        "Standard Text": "Identifies coins and understands their value.",
        "EM2 Alignment Notes": None,
    }
    path = _write_gaps_sheet([row, row])

    con = _fresh_db()
    cur = con.cursor()
    n = load_gaps_sheet(cur, path, set())

    assert n == 2
    standards = cur.execute("SELECT standard_id FROM standards").fetchall()
    assert standards == [("WI.PK.B.EL.5",)]
    leaves = cur.execute("SELECT standard_id FROM leaves").fetchall()
    assert leaves == [("WI.PK.B.EL.5",), ("WI.PK.B.EL.5",)]


def _write_g6_sheet(sheet_name: str, rows: list[dict]) -> Path:
    tmpdir = Path(tempfile.mkdtemp())
    path = tmpdir / "g6.xlsx"
    df = pd.DataFrame(rows, columns=[
        "Stem", "Concept/Skill", "Details", "CCSSM Standard",
        "Big Three Standard", "Other Standard", "Grade/Leaf", "Notes",
    ])
    with pd.ExcelWriter(path) as xw:
        df.to_excel(xw, sheet_name=sheet_name, index=False)
    return path


def _write_stems_csv(rows: list[dict]) -> Path:
    tmpdir = Path(tempfile.mkdtemp())
    path = tmpdir / "stems.csv"
    fields = ("stem_id", "band", "stem_group", "masterlist_name",
              "workbook_sheet", "workbook_stem", "ladder_file", "ladder_drafted")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def test_load_stem_inventory_g6_resolves_against_stems_csv():
    """
    The 6-9 path must RESOLVE the canonical stem_id from stems.csv, never
    derive one from the workbook's stem name (rev 4 handoff §1's "resolve,
    never derive"). This is the opposite of the PK-5 path's mint-a-3-letter-
    code behavior.
    """
    stems_csv = _write_stems_csv([{
        "stem_id": "EE_POLYNOMIAL_EQUATIONS", "band": "6_9", "stem_group": "",
        "masterlist_name": "Polynomial Equations", "workbook_sheet": "",
        "workbook_stem": "Polynomial Equations", "ladder_file": "", "ladder_drafted": "0",
    }])
    sheet_path = _write_g6_sheet("Expressions and Equations", [{
        "Stem": "Polynomial Equations", "Concept/Skill": "Solve by factoring",
        "Details": None, "CCSSM Standard": "6.EE.A.1",
        "Big Three Standard": None, "Other Standard": None,
        "Grade/Leaf": None, "Notes": None,
    }])

    orig_stems_csv = config.STEMS_CSV
    config.STEMS_CSV = stems_csv
    try:
        con = _fresh_db()
        cur = con.cursor()
        nc, nt, _unparsed = load_stem_inventory(
            cur, sheet_path, ["Expressions and Equations"], "6_9")
    finally:
        config.STEMS_CSV = orig_stems_csv

    assert nc == 1
    assert nt == 1
    stem_rows = cur.execute("SELECT stem_id, name, band FROM stems").fetchall()
    assert stem_rows == [("EE_POLYNOMIAL_EQUATIONS", "Polynomial Equations", "6_9")]
    concept_stem = cur.execute("SELECT stem_id FROM concepts").fetchone()[0]
    assert concept_stem == "EE_POLYNOMIAL_EQUATIONS"


def test_load_stem_inventory_g6_unresolved_name_is_skipped_not_derived():
    """
    A workbook stem name with no match in stems.csv must be skipped (never
    minted an ID) and reported, and the build must not raise.
    """
    stems_csv = _write_stems_csv([{
        "stem_id": "EE_POLYNOMIAL_EQUATIONS", "band": "6_9", "stem_group": "",
        "masterlist_name": "Polynomial Equations", "workbook_sheet": "",
        "workbook_stem": "Polynomial Equations", "ladder_file": "", "ladder_drafted": "0",
    }])
    sheet_path = _write_g6_sheet("Expressions and Equations", [{
        "Stem": "Brand New Stem Nobody Curated", "Concept/Skill": "Some new skill",
        "Details": None, "CCSSM Standard": None,
        "Big Three Standard": None, "Other Standard": None,
        "Grade/Leaf": None, "Notes": None,
    }])

    orig_stems_csv = config.STEMS_CSV
    orig_reports = config.REPORTS
    config.STEMS_CSV = stems_csv
    config.REPORTS = Path(tempfile.mkdtemp())
    try:
        con = _fresh_db()
        cur = con.cursor()
        nc, nt, _unparsed = load_stem_inventory(
            cur, sheet_path, ["Expressions and Equations"], "6_9")
        report = config.REPORTS / "stem_resolution_unmatched.txt"
        assert report.exists()
        assert "Brand New Stem Nobody Curated" in report.read_text()
    finally:
        config.STEMS_CSV = orig_stems_csv
        config.REPORTS = orig_reports

    assert nc == 0
    assert nt == 0
    assert cur.execute("SELECT COUNT(*) FROM stems").fetchone()[0] == 0
    assert cur.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == 0


def test_load_stem_inventory_pk5_still_derives():
    """PK-5 path is unchanged: mints a code from the stem name, never resolves."""
    sheet_path = _write_g6_sheet("Measurement and Data", [{
        "Stem": "Angles", "Concept/Skill": "Identify angle types",
        "Details": None, "CCSSM Standard": "4.MD.C.5",
        "Big Three Standard": None, "Other Standard": None,
        "Grade/Leaf": None, "Notes": None,
    }])

    con = _fresh_db()
    cur = con.cursor()
    nc, nt, _unparsed = load_stem_inventory(
        cur, sheet_path, ["Measurement and Data"], "PK5")

    assert nc == 1
    assert nt == 1
    stem_id, band = cur.execute("SELECT stem_id, band FROM stems").fetchone()
    assert stem_id == "ANG"
    assert band is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("all passed")
