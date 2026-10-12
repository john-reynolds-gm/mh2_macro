"""
config.py — every path in the project, in one place.

Nothing else should hardcode a path. When files move, this is the only
file that changes.

Layout convention:
    data/source/   read-only originals. Scripts NEVER write here.
    data/build/    mostly generated, but NOT disposable: it also holds durable
                   state that cannot be rebuilt -- mh2_seq.db (writer review
                   and sequencing decisions) and ladder_uploads/ (every
                   ladder version writers have uploaded, docs/brief_ladder_
                   uploads.md). `rm -rf data/build` is no longer safe; to force
                   a clean rebuild delete only mh2.db* and run rebuild.py.
    data/reports/  output meant for humans to open.
"""

import os
from pathlib import Path

# Repo root = the folder containing this file.
ROOT = Path(__file__).resolve().parent

# MH2_DATA_DIR relocates the whole data tree off the repo, for hosts where
# the repo is redeployed but the databases must survive it. Deliberately ONE
# variable: everything below stays relative to DATA, so build and source
# cannot be pointed at separate places -- which would make "which of them
# does RERANK_CACHE follow?" a live question rather than a settled one.
DATA = Path(os.environ.get("MH2_DATA_DIR") or ROOT / "data").resolve()
SOURCE = DATA / "source"
BUILD = DATA / "build"
REPORTS = DATA / "reports"

# --- source data (read-only) -------------------------------------------------
# MH2_LADDERS_DIR / MH2_STEMS_CSV point the pipeline at a different ladder
# set. rebuild.py sets both on every step it runs, to the effective inputs it
# assembles under data/build/_effective (repo ladders with writers' uploads
# laid over them -- mh2/ladder_store.py). Overriding here rather than in
# rebuild.py's argument lists is what reaches the readers that take no
# argument, e.g. load_standards._build_stem_lookup.
LADDERS = Path(os.environ.get("MH2_LADDERS_DIR") or SOURCE / "ladders")  # MH2_*.docx
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
STEMS_CSV = Path(os.environ.get("MH2_STEMS_CSV") or WORKBOOKS / "stems.csv")

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

# Writer-uploaded ladders (docs/brief_ladder_uploads.md). DURABLE: on the
# persistent volume with mh2_seq.db, which holds their metadata. For any stem
# with an upload, the upload is the source of truth and the repo copy in
# LADDERS is only the seed it replaced.
LADDER_UPLOADS = BUILD / "ladder_uploads"

# The inputs a rebuild actually reads, reassembled from scratch on every run.
# Disposable.
EFFECTIVE_INPUTS = BUILD / "_effective"

# Reranker cache. Deliberately NOT under data/build: it is paid API output
# (mh2/rerank.py, scripts/run_reranks.py), and deleting build output is the
# documented response to "anything looks wrong, delete it and rebuild" --
# that must never delete money already spent. mh2/load_reranks.py reads this
# file back into model_reranks on every rebuild at zero API cost.
RERANK_CACHE = DATA / "reranks" / "cache.jsonl"


def ensure_dirs() -> None:
    for d in (BUILD, REPORTS, LADDERS, WORKBOOKS, STANDARDS, PREDICTIONS,
              RERANK_CACHE.parent):
        d.mkdir(parents=True, exist_ok=True)
