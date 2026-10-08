"""
load_stems.py tests -- the band-fencing guard in load() (rev 4 handoff §4).

Once the 6-9 loader writes stem_id rows into `stems`, a bare
normalized-name lookup for workbook_stem_id would let a 6-9 stem steal a
PK-5 stem_map row of the same name (e.g. PK-5 MD_AREA's workbook_stem
'Area' vs. the 6-9 GM_AREA stem also named 'Area'). The fix keys the
lookup on (band, normalized name), falling back to the NULL band only when
no same-band match exists -- so PK-5's pre-existing NULL-banded stems keep
resolving exactly as before, and the newly band-tagged 6-9 stems don't leak
into PK-5 matches.

Run with: python -m pytest tests/ -q   (or: python tests/test_load_stems.py)
"""
import csv
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

import pytest  # noqa: E402

from mh2.load_stems import load, read_stems_csv  # noqa: E402


def _fresh_db():
    con = sqlite3.connect(":memory:")
    con.executescript(config.SCHEMA.read_text())
    return con


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


def test_pk5_area_is_not_stolen_by_6_9_area():
    """
    PK-5's MD_AREA names workbook_stem 'Area' and has no matching `stems`
    row of its own (a real gap -- see rev 4 handoff §4 table). A NULL-banded
    lookup with no band qualifier would incorrectly resolve it to the 6-9
    GM_AREA stem, which is also literally named 'Area'. It must stay NULL.
    """
    con = _fresh_db()
    cur = con.cursor()
    cur.execute("INSERT INTO stems (stem_id, name, domain, band) VALUES"
                " ('GM_AREA', 'Area', 'Geometry and Measurement', '6_9')")

    stems_csv = _write_stems_csv([
        {"stem_id": "MD_AREA", "band": "PK5", "stem_group": "Measurement and Data",
         "masterlist_name": "Area", "workbook_sheet": "Measurement and Data",
         "workbook_stem": "Area", "ladder_file": "", "ladder_drafted": "0"},
        {"stem_id": "GM_AREA", "band": "6_9", "stem_group": "Geometry and Measurement",
         "masterlist_name": "Area", "workbook_sheet": "Geometry and Measurement",
         "workbook_stem": "Area", "ladder_file": "", "ladder_drafted": "0"},
    ])
    ladder_dir = Path(tempfile.mkdtemp())

    load(cur, stems_csv, ladder_dir)

    pk5_wb_id = cur.execute(
        "SELECT workbook_stem_id FROM stem_map WHERE stem_id = 'MD_AREA'").fetchone()[0]
    g6_wb_id = cur.execute(
        "SELECT workbook_stem_id FROM stem_map WHERE stem_id = 'GM_AREA'").fetchone()[0]
    assert pk5_wb_id is None
    assert g6_wb_id == "GM_AREA"


def test_pk5_null_band_match_still_works():
    """
    PK-5's existing NULL-band-to-NULL-band matching (the vast majority of
    stem_map rows) must be unaffected by the band-fencing change.
    """
    con = _fresh_db()
    cur = con.cursor()
    cur.execute("INSERT INTO stems (stem_id, name, domain) VALUES"
                " ('ANG', 'Angles', 'Measurement and Data')")

    stems_csv = _write_stems_csv([
        {"stem_id": "MD_ANGLES", "band": "PK5", "stem_group": "Measurement and Data",
         "masterlist_name": "Angles", "workbook_sheet": "Measurement and Data",
         "workbook_stem": "Angles", "ladder_file": "", "ladder_drafted": "0"},
    ])
    ladder_dir = Path(tempfile.mkdtemp())

    load(cur, stems_csv, ladder_dir)

    wb_id = cur.execute(
        "SELECT workbook_stem_id FROM stem_map WHERE stem_id = 'MD_ANGLES'").fetchone()[0]
    assert wb_id == "ANG"


def _row(stem_id, workbook_stem, ladder_file="", band="PK5"):
    return {"stem_id": stem_id, "band": band, "stem_group": "",
            "masterlist_name": "Comparing and Ordering", "workbook_sheet": "",
            "workbook_stem": workbook_stem, "ladder_file": ladder_file,
            "ladder_drafted": "0"}


def test_one_ladder_two_workbook_stems_keeps_both_rows():
    """
    DEFERRED.md §10: NS-COMP-ORDER is one ladder over the 'Comparing' and
    'Ordering' workbook stems. stem_map is keyed (stem_id, workbook_stem), so
    neither row overwrites the other -- coverage's name lookup needs both.
    """
    con = _fresh_db()
    cur = con.cursor()
    cur.execute("INSERT INTO stems (stem_id, name) VALUES"
                " ('NS-COMP-ORDER', 'Comparing and Ordering')")
    load(cur, _write_stems_csv([_row("NS-COMP-ORDER", "Comparing", "c.docx"),
                                _row("NS-COMP-ORDER", "Ordering", "c.docx")]),
         Path(tempfile.mkdtemp()))
    got = sorted(cur.execute("SELECT stem_id, workbook_stem FROM stem_map"))
    assert got == [("NS-COMP-ORDER", "Comparing"), ("NS-COMP-ORDER", "Ordering")]


@pytest.mark.parametrize("rows, why", [
    ([_row("NS-X", "A", "x.docx"), _row("NS-X", "B", "y.docx")], "different band or ladder_file"),
    ([_row("NS-X", "A", "x.docx"), _row("NS-X", "B", "x.docx", band="6_9")], "different band or ladder_file"),
    ([_row("NS-X", "A"), _row("NS-X", "A")], "listed twice"),
    ([_row("NS-X", "A", "x.docx"), _row("NS-Y", "B", "x.docx")], "claimed by both"),
])
def test_registry_rejects_two_stems_sharing_a_code_or_a_ladder(rows, why):
    with pytest.raises(SystemExit, match=why):
        read_stems_csv(_write_stems_csv(rows))


def test_real_stems_csv_is_consistent():
    """The checked-in stems.csv passes its own registry check."""
    assert read_stems_csv(config.STEMS_CSV)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("all passed")
