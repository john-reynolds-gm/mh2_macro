"""
config.py — every path in the project, in one place.

Nothing else should hardcode a path. When files move, this is the only
file that changes.

Layout convention:
    data/source/   read-only originals. Scripts NEVER write here.
    data/build/    generated. Safe to delete at any time and rebuild.
    data/reports/  output meant for humans to open.
"""

from pathlib import Path

# Repo root = the folder containing this file.
ROOT = Path(__file__).resolve().parent

DATA = ROOT / "data"
SOURCE = DATA / "source"
BUILD = DATA / "build"
REPORTS = DATA / "reports"

# --- source data (read-only) -------------------------------------------------
LADDERS = SOURCE / "ladders"        # MH2_*.docx
WORKBOOKS = SOURCE / "workbooks"    # the two Excel files
STANDARDS = SOURCE / "standards"    # the big CSVs
PREDICTIONS = SOURCE / "predictions"  # Path C, JSONL from the offline run

STEM_WORKBOOK = WORKBOOKS / "H2_Stem_and_Leaf_Spreadsheet_K_to_G5.xlsm"
STEM_WORKBOOK_G6 = WORKBOOKS / "H2 Stem and Leaf Spreadsheet G6 to Alg1.xlsm"
TAGGING_WORKBOOK = WORKBOOKS / "Standards_for_Stems_Tagging.xlsx"
ALL_STATES_CSV = STANDARDS / "all_states.csv"
SCORED_ALIGNMENTS_CSV = STANDARDS / "scored_alignments.csv"
LESSON_METADATA_CSV = STANDARDS / "lesson_metadata.csv"

# The recorded ladder <-> workbook stem mapping. Hand-maintained data, not
# derivable from the names -- see mh2/load_stems.py.
STEMS_CSV = WORKBOOKS / "stems.csv"

# These two arrived alongside the workbooks rather than in standards/, so they
# are declared where they actually live.
CCSS_ALIGNMENT_GUIDE_CSV = WORKBOOKS / "CCSS_alignment_guide.csv"
LEARNOSITY_COMBINED_CSV = WORKBOOKS / "learnosity_combined.csv"

# First-pass, hand-maintained gap-pool category -> stem routing. John expects
# to revise this; the app reads it live rather than hardcoding the mapping.
CATEGORY_TO_STEMS_CSV = WORKBOOKS / "mh2_category_to_stems.csv"

# Human ruling on the 96 distinct raw `grade_or_leaf` strings across the
# ladders -- see mh2/load_grade_ruling.py.
GRADE_WORKSHEET = WORKBOOKS / "mh2_grade_normalization_worksheet.xlsx"

# --- generated ---------------------------------------------------------------
DB = BUILD / "mh2.db"
SCHEMA = ROOT / "mh2" / "schema.sql"

# Review write-path state (Session C). A separate database on purpose: it
# holds durable human review decisions that must survive `rebuild.py`
# unlinking DB and rebuilding it from data/source -- see rebuild.py's
# handling of SEQ_DB, which creates but never unlinks it.
SEQ_DB = BUILD / "mh2_seq.db"
SCHEMA_SEQ = ROOT / "mh2" / "schema_seq.sql"

LADDER_GLOB = str(LADDERS / "*.docx")

# Reranker cache. Deliberately NOT under data/build: it is paid API output
# (mh2/rerank.py, scripts/run_reranks.py), and `rm -rf data/build` is the
# documented safe default for "anything looks wrong, delete it and rebuild" --
# that must never delete money already spent. mh2/load_reranks.py reads this
# file back into model_reranks on every rebuild at zero API cost.
RERANK_CACHE = DATA / "reranks" / "cache.jsonl"


def ensure_dirs() -> None:
    for d in (BUILD, REPORTS, LADDERS, WORKBOOKS, STANDARDS, PREDICTIONS,
              RERANK_CACHE.parent):
        d.mkdir(parents=True, exist_ok=True)
