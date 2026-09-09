"""
load_layer1.py tests — standard_tag_status must key gaps-sheet rows the same
way mh2.load_standards.load_gaps_sheet keys `standards`, or the two tables
disagree on the code for the same row and every join between them (the
coverage rollup's tab/claim lookup) silently misses it.

Run with: python -m pytest tests/ -q   (or: python tests/test_load_layer1.py)
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

import config  # noqa: E402

from mh2.load_layer1 import load_tag_status  # noqa: E402


def _write_gaps_only_workbook(rows) -> Path:
    tmpdir = Path(tempfile.mkdtemp())
    path = tmpdir / "tagging.xlsx"
    df = pd.DataFrame(rows, columns=[
        "Standard Code", "Tagged to a Stem",
        "Notes from post-ladder check step",
    ])
    with pd.ExcelWriter(path) as xw:
        df.to_excel(xw, sheet_name="Other State Standards Gaps", index=False)
    return path


def test_load_tag_status_unwelds_gaps_sheet_codes():
    path = _write_gaps_only_workbook([
        {"Standard Code": "WI.PK.B.EL.5 not numbered", "Tagged to a Stem": "Yes",
         "Notes from post-ladder check step": None},
    ])
    con = sqlite3.connect(":memory:")
    con.executescript(config.SCHEMA.read_text())
    cur = con.cursor()
    total, collisions = load_tag_status(cur, path)

    assert total == 1
    assert collisions == []
    codes = cur.execute("SELECT standard_code FROM standard_tag_status").fetchall()
    assert codes == [("WI.PK.B.EL.5",)]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("all passed")
