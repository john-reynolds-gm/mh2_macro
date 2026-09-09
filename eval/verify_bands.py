"""
Regression harness for mh2/coverage.py — the single definition of the rollup.

Replaces eval/coverage_audit.py (rev 4 note 4). Asserts nothing about the
database except that coverage.py reports the same numbers it reported when
these baselines were measured. Fails loudly and specifically on drift.

Every figure below was measured against mh2.db, not copied from the handoff.
Most date to 2026-08-31 (where the two disagree, see the note on PK5
on-grade); the stem-attribution figures (PK5_ATTRIBUTION, B69_ATTRIBUTION,
PK5_UNATTRIBUTED, B69_UNATTRIBUTED, the conflicts count, band-aware
precedence flips) were re-pinned 2026-09-01 (Step 1 SS2.1/SS2.2) -- see the
superseded comment above PK5_ATTRIBUTION for why the old ones moved.

Run:
    python -m eval.verify_bands [path/to/mh2.db]

Exit 0 = no drift. Exit 1 = at least one figure moved; the report names which.
"""

import os
import sqlite3
import sys
from collections import Counter

from mh2.coverage import (
    build_rows,
    build_stem_attribution,
    claim_table,
    coverage_table,
    denominator_table,
    flags_summary,
    grade_match_distribution,
)

# --------------------------------------------------------------- baselines

DENOMINATOR = {
    "CCSS":                       {"PK5": 191, "6_9": 126, "NULL": 0},
    "California-not CCSS":        {"PK5": 3,   "6_9": 2,   "NULL": 12},
    "Florida":                    {"PK5": 184, "6_9": 159, "NULL": 294},
    "Texas":                      {"PK5": 246, "6_9": 189, "NULL": 90},
    "Other State Standards Gaps": {"PK5": 922, "6_9": 569, "NULL": 0},
}

BAND_TOTALS = {None: 2987, "PK5": 1546, "6_9": 1045}

PK5_COVERAGE = {
    "CCSS":                       dict(n=191, green=103, yellow=3,  red=85),
    "California-not CCSS":        dict(n=3,   green=3,   yellow=0,  red=0),
    "Florida":                    dict(n=184, green=80,  yellow=4,  red=100),
    "Texas":                      dict(n=246, green=86,  yellow=7,  red=153),
    "Other State Standards Gaps": dict(n=922, green=200, yellow=38, red=684),
}
PK5_TOTAL = {"Green": 472, "Yellow": 52, "Red": 1022}
PK5_CLAIM = {"yes_tag": 404, "yes_notag": 361, "blank_tag": 120, "blank_notag": 661}
PK5_FLAGS = {"flagged_rows": 101, "yellow_rows": 52, "green_with_flag": 49}

# Handoff rev 4 §5 states 955 on-grade. That figure added only the 14 alias
# pairs landing on the 7 Red->Green rows and missed 6 further alias pairs
# landing on rows that already had exact tags (3.OA.A.2, 4.NF.C.5,
# 5.NBT.A.3.a, K.CC.B.4.c, TX.K.2B). Exact-only 941 + 20 alias pairs = 961.
# 961 is correct; do not tune coverage.py toward 955.
PK5_GRADE_MATCH = {"on-grade": 961, "off-grade": 134, "n/a - leaf": 61,
                   "unresolved": 8}

# 6-9 band. First measurement of this slice; no prior figure to reconcile.
B69_TOTAL = {"Green": 210, "Yellow": 42, "Red": 793}
B69_CLAIM = {"yes_tag": 198, "yes_notag": 199, "blank_tag": 54, "blank_notag": 594}
B69_FLAGS = {"flagged_rows": 75, "yellow_rows": 42, "green_with_flag": 33}
B69_GRADE_MATCH = {"on-grade": 498, "off-grade": 103, "n/a - leaf": 26,
                   "unresolved": 17}

# 41 non-exact tag pairs across all bands: 32 punct, 9 nocluster. Four of the
# nocluster pairs are the SAME ladder code, '6.EE.C.7', resolving to
# '6.EE.B.7' across four nodes -- a wrong cluster letter, not an omitted one.
# 6-9 only, which is why PK-5 acceptance never exercised it.
NOCLUSTER_PAIRS = 9
WRONG_CLUSTER_CODES = {"6.EE.C.7"}

# The 7 PK-5 rows the alias table recovers from Red. Rows that merely gain an
# extra alias-resolved tag alongside existing exact tags are NOT recoveries
# and are deliberately not listed — conflating the two is what produced 955.
PK5_ALIAS_RECOVERIES = {
    "3.NF.A.2.a", "3.NF.A.2.b", "3.NF.A.3.a",
    "4.NBT.B.4",
    "TX.4.3E", "TX.4.3F", "TX.5.3H",
}

# --------------------------------------------------- stem attribution (B.2)
#
# SUPERSEDED (rev 5, measured 2026-08-31): PK5_ATTRIBUTION/B69_ATTRIBUTION
# below replace that measurement. It predated:
#   - the G6 workbook load, which gave concept_standards real 6-9 coverage --
#     "concept_standards has zero 6-9 coverage... every 6-9 tab except Other
#     Gaps is 100% unattributed by construction" stopped being true the
#     moment that landed; B69_ATTRIBUTION now shows non-zero drafted/
#     undrafted in every tab.
#   - Step 1 SS2.1 (mh2/coverage.py:build_stem_attribution): the stems-exist
#     gate in _category_route_by_code is reversed -- a resolved stem_map row
#     is an ownership claim whether or not `stems` has a row for it, so
#     MD_LENGTH/AREA/VOLUME/DATA and their 6-9 counterparts now read
#     `undrafted`, never `unattributed`.
#   - Step 1 SS2.2: band-aware precedence (a same-band candidate beats a
#     cross-band one regardless of route). 12 standards' stem_ids or
#     ladder_status changed under this rule -- see
#     data/reports/stem_attribution_conflicts.txt for the full list with
#     both candidates and their bands. This count is measured, not
#     reconciled against the G6 session's 14 or deferred_s5's narrower-
#     predicate 5; neither of those is authoritative.
#
# Re-pinned (Step 1, measured against data/build/mh2.db post SS2.1/SS2.2).
# Predicate: build_rows(con, band=<band>), grouped by r.sheet, counting
# r.ladder_status per row (drafted/undrafted/unattributed, from
# build_stem_attribution -- see its docstring for the precedence rule).

PK5_ATTRIBUTION = {
    "CCSS":                       dict(drafted=91,  undrafted=36, unattributed=64),
    "California-not CCSS":        dict(drafted=2,   undrafted=0,  unattributed=1),
    "Florida":                    dict(drafted=84,  undrafted=42,  unattributed=58),
    "Texas":                      dict(drafted=99,  undrafted=52,  unattributed=95),
    "Other State Standards Gaps": dict(drafted=527, undrafted=293, unattributed=102),
}
B69_ATTRIBUTION = {
    "CCSS":                       dict(drafted=54,  undrafted=15,  unattributed=57),
    "California-not CCSS":        dict(drafted=0,   undrafted=1,   unattributed=1),
    "Florida":                    dict(drafted=45,  undrafted=44,  unattributed=70),
    "Texas":                      dict(drafted=37,  undrafted=49,  unattributed=103),
    "Other State Standards Gaps": dict(drafted=217, undrafted=270, unattributed=82),
}

# Pinned by code, not by count (standing rule): a code silently swapping
# between two rows of equal total would pass a count-only check.
PK5_UNATTRIBUTED = {
    '1.MD.A.1', '1.MD.A.2', '1.MD.C.4', '1.NBT.B.2.a', '1.NBT.B.2.b',
    '1.NBT.B.2.c', '2.MD.A.1', '2.MD.A.2', '2.MD.A.4', '2.MD.D.10',
    '2.MD.D.9', '2.NBT.A.1.b', '2.NBT.B.6', '3.MD.B.3', '3.MD.B.4',
    '3.MD.C.5', '3.MD.C.5.a', '3.MD.C.5.b', '3.MD.C.6', '3.MD.C.7',
    '3.MD.C.7.a', '3.MD.C.7.b', '3.MD.C.7.c', '3.MD.C.7.d', '3.NF.A.2',
    '3.NF.A.2.a', '3.NF.A.2.b', '3.NF.A.3', '3.NF.A.3.a', '3.NF.A.3.b',
    '3.NF.A.3.c', '3.OA.B.6', '4.G.A.3', '4.MD.A.1', '4.MD.B.4',
    '4.MD.C.5.b', '4.NBT.B.4', '4.NF.B.3', '4.NF.B.3.b', '4.NF.B.3.c',
    '4.NF.B.3.d', '4.OA.B.4', '5.MD.B.2', '5.MD.C.3', '5.MD.C.3.a',
    '5.MD.C.3.b', '5.MD.C.4', '5.MD.C.5', '5.MD.C.5.a', '5.MD.C.5.b',
    '5.MD.C.5.c', '5.NF.A.2', '5.NF.B.4.b', '5.NF.B.5', '5.NF.B.5.a',
    '5.NF.B.5.b', '5.NF.B.6', '5.NF.B.7', '5.NF.B.7.a', '5.OA.A.2',
    'AL.3.DA.16.a', 'AR.4.DA.1', 'AR.5.DA.1', 'CA.5.OA.2.1',
    'FL.1.DP.1.1', 'FL.1.DP.1.2', 'FL.1.FR.1.1', 'FL.1.M.1.2',
    'FL.1.M.2.2', 'FL.1.M.2.3', 'FL.1.NSO.2.4', 'FL.1.NSO.2.5',
    'FL.2.DP.1.1', 'FL.2.DP.1.2', 'FL.2.FR.1.1', 'FL.2.FR.1.2',
    'FL.2.GR.1.3', 'FL.2.M.1.2', 'FL.2.M.1.3', 'FL.2.M.2.2',
    'FL.2.NSO.2.3', 'FL.3.AR.3.1', 'FL.3.AR.3.2', 'FL.3.DP.1.1',
    'FL.3.DP.1.2', 'FL.3.FR.1.2', 'FL.3.FR.1.3', 'FL.3.FR.2.2',
    'FL.3.GR.1.3', 'FL.3.GR.2.2', 'FL.3.M.1.1', 'FL.3.M.1.2',
    'FL.3.NSO.2.1', 'FL.3.NSO.2.4', 'FL.4.AR.1.2', 'FL.4.AR.3.1',
    'FL.4.DP.1.1', 'FL.4.DP.1.2', 'FL.4.DP.1.3', 'FL.4.FR.1.1',
    'FL.4.FR.1.3', 'FL.4.FR.2.1', 'FL.4.FR.2.2', 'FL.4.FR.2.3',
    'FL.4.M.1.1', 'FL.4.M.2.2', 'FL.5.AR.1.2', 'FL.5.AR.2.1',
    'FL.5.DP.1.1', 'FL.5.DP.1.2', 'FL.5.FR.2.1', 'FL.5.FR.2.4',
    'FL.5.GR.3.1', 'FL.5.GR.3.2', 'FL.5.GR.3.3', 'FL.5.M.2.1',
    'FL.K.AR.2.1', 'FL.K.DP.1.1', 'FL.K.GR.1.1', 'FL.K.M.1.1',
    'FL.K.M.1.2', 'FL.K.M.1.3', 'GA.3.GSR.6.3', 'GA.3.MDR.5.1',
    'GA.5.MDR.7.2', 'IA.2.MD.D.IA.2', 'ID.5.MD.B.2', 'K.G.B.5',
    'K.MD.A.1', 'K.MD.B.3', 'K.OA.A.5', 'KY.1.MD.4', 'KY.1.MD.4.a',
    'KY.1.MD.4.b', 'KY.2.MD.9', 'KY.2.MD.9a', 'KY.3.MD.3', 'KY.3.MD.3.a',
    'KY.3.MD.4', 'KY.3.MD.4.a', 'KY.4.MD.4.a', 'KY.5.MD.2',
    'MD.1.DS.A.1.a', 'MD.1.DS.A.1.b', 'MD.1.DS.A.1.d', 'MD.2.DS.A.1.a',
    'MD.2.DS.A.1.b', 'MD.2.DS.A.1.d', 'MD.2.GR.B.6', 'MD.3.DS.A.1.b',
    'MD.4.DS.A.1.b', 'MD.4.DS.A.1.c', 'MD.5.DS.A.1.b', 'MN.1.1.1.1',
    'MN.1.1.2.1', 'MN.2.1.1.1', 'MN.2.1.1.2', 'MN.2.1.2.1', 'MN.3.1.1.1',
    'MN.3.1.1.2', 'MN.3.1.1.3', 'MN.3.1.1.5', 'MN.3.1.2.1', 'MN.4.1.1.1',
    'MN.4.1.1.2', 'MN.4.1.1.4', 'MN.4.1.2.1', 'MN.4.1.2.2', 'MN.5.1.1.1',
    'MN.5.1.1.2', 'MN.5.1.1.3', 'MN.5.1.1.4', 'MN.5.1.1.5', 'MN.5.1.2.1',
    'MN.5.1.2.2', 'MN.PK.M10.9', 'NC.5.MD.2', 'ND.3.DPS.D.1',
    'ND.3.GM.G.3', 'ND.4.DPS.D.1', 'NE.2.D.1.a', 'NE.2.D.1.b',
    'NE.5.A.1.d', 'NJ.2.DL.A.1', 'NJ.2.DL.A.2', 'NJ.3.DL.A.1',
    'NJ.3.DL.A.2', 'NJ.4.DL.A.1', 'NJ.4.DL.A.2', 'NJ.4.DL.A.3',
    'NJ.5.DL.A.2', 'NJ.5.DL.A.3', 'OK.K.D.1.1', 'OR.4.DR.A.1',
    'OR.5.DR.A.1', 'SC.3.DPSR.2.1', 'SC.4.DPSR.1.1', 'SC.4.DPSR.2.1',
    'SC.5.DPSR.2.1', 'TX.1.2E', 'TX.1.3E', 'TX.1.4A', 'TX.1.4B',
    'TX.1.4C', 'TX.1.6C', 'TX.1.7A', 'TX.1.7B', 'TX.1.7C', 'TX.1.7D',
    'TX.1.8A', 'TX.1.8B', 'TX.1.8C', 'TX.1.9A', 'TX.1.9B', 'TX.1.9C',
    'TX.1.9D', 'TX.2.10A', 'TX.2.10B', 'TX.2.10C', 'TX.2.10D', 'TX.2.11A',
    'TX.2.11B', 'TX.2.11C', 'TX.2.11D', 'TX.2.11E', 'TX.2.11F', 'TX.2.3D',
    'TX.2.5A', 'TX.2.5B', 'TX.2.9A', 'TX.2.9B', 'TX.2.9D', 'TX.2.9F',
    'TX.3.3B', 'TX.3.3F', 'TX.3.3G', 'TX.3.4C', 'TX.3.4D', 'TX.3.4E',
    'TX.3.4I', 'TX.3.5C', 'TX.3.5E', 'TX.3.7A', 'TX.3.7D', 'TX.3.7E',
    'TX.3.8A', 'TX.3.8B', 'TX.3.9A', 'TX.3.9B', 'TX.3.9C', 'TX.3.9D',
    'TX.3.9E', 'TX.3.9F', 'TX.4.10A', 'TX.4.10B', 'TX.4.10C', 'TX.4.10D',
    'TX.4.10E', 'TX.4.2H', 'TX.4.3B', 'TX.4.3F', 'TX.4.4C', 'TX.4.6B',
    'TX.4.6D', 'TX.4.8A', 'TX.4.9A', 'TX.4.9B', 'TX.5.10A', 'TX.5.10B',
    'TX.5.10C', 'TX.5.10D', 'TX.5.10E', 'TX.5.10F', 'TX.5.3K', 'TX.5.4A',
    'TX.5.4D', 'TX.5.4E', 'TX.5.4G', 'TX.5.6A', 'TX.5.6B', 'TX.5.9A',
    'TX.5.9B', 'TX.5.9C', 'TX.K.3C', 'TX.K.4A', 'TX.K.7A', 'TX.K.7B',
    'TX.K.8A', 'TX.K.8B', 'TX.K.8C', 'TX.K.9A', 'TX.K.9B', 'TX.K.9C',
    'TX.K.9D', 'VA.1.PS.1.c', 'VA.1.PS.1.d', 'VA.2.MG.3.a', 'VA.2.MG.3.b',
    'VA.2.MG.3.c', 'VA.2.PS.1.a', 'VA.2.PS.1.b', 'VA.3.PS.1.a',
    'VA.3.PS.1.b', 'VA.4.PS.1.a', 'VA.4.PS.1.b', 'VA.4.PS.2.a',
    'VA.4.PS.2.b', 'VA.4.PS.2.c', 'VA.4.PS.2.d', 'VA.4.PS.2.e',
    'VA.5.CE.4.a.i', 'VA.5.CE.4.a.ii', 'VA.5.CE.4.a.iii',
    'VA.5.CE.4.a.iv', 'VA.5.CE.4.b', 'VA.5.PS.1.b', 'VA.5.PS.3.a',
    'VA.5.PS.3.b', 'VA.K.PS.1.c', 'VA.K.PS.1.d',
}
B69_UNATTRIBUTED = {
    '6.EE.C.9', '6.G.A.1', '6.G.A.2', '6.G.A.4', '6.NS.A.1', '6.NS.B.2',
    '6.NS.B.3', '6.NS.C.7', '6.NS.C.7.d', '6.RP.A.1', '6.RP.A.2',
    '6.RP.A.3', '6.RP.A.3.a', '6.RP.A.3.b', '6.RP.A.3.d', '6.SP.A.1',
    '6.SP.A.2', '6.SP.A.3', '6.SP.B.4', '6.SP.B.5', '6.SP.B.5.a',
    '6.SP.B.5.b', '6.SP.B.5.c', '6.SP.B.5.d', '7.G.A.1', '7.G.A.3',
    '7.G.B.4', '7.G.B.6', '7.NS.A.1', '7.RP.A.1', '7.RP.A.2',
    '7.RP.A.2.a', '7.RP.A.2.b', '7.RP.A.2.c', '7.RP.A.2.d', '7.SP.A.1',
    '7.SP.A.2', '7.SP.B.3', '7.SP.B.4', '8.EE.A.3', '8.EE.A.4',
    '8.EE.C.7.a', '8.EE.C.7.b', '8.EE.C.8', '8.F.A.2', '8.G.A.1',
    '8.G.A.1.a', '8.G.A.1.b', '8.G.A.1.c', '8.G.A.2', '8.G.A.3',
    '8.G.A.4', '8.G.C.9', '8.SP.A.1', '8.SP.A.2', '8.SP.A.3', '8.SP.A.4',
    'AK.6.G.5', 'AL.6.DSP.24', 'AR.6.GM.7', 'AR.7.SP.1', 'AR.7.SP.2',
    'AR.8.GM.2', 'CA.7-12.A-REI.3.1', 'FL.6.AR.3.1', 'FL.6.AR.3.2',
    'FL.6.AR.3.3', 'FL.6.AR.3.5', 'FL.6.DP.1.1', 'FL.6.DP.1.2',
    'FL.6.DP.1.3', 'FL.6.DP.1.4', 'FL.6.DP.1.5', 'FL.6.DP.1.6',
    'FL.6.GR.2.1', 'FL.6.GR.2.2', 'FL.6.GR.2.3', 'FL.6.GR.2.4',
    'FL.6.NSO.2.1', 'FL.6.NSO.2.2', 'FL.6.NSO.2.3', 'FL.7.AR.3.2',
    'FL.7.AR.3.3', 'FL.7.AR.4.1', 'FL.7.AR.4.2', 'FL.7.AR.4.3',
    'FL.7.AR.4.4', 'FL.7.AR.4.5', 'FL.7.DP.1.1', 'FL.7.DP.1.2',
    'FL.7.DP.1.3', 'FL.7.DP.1.4', 'FL.7.DP.1.5', 'FL.7.DP.2.1',
    'FL.7.GR.1.1', 'FL.7.GR.1.2', 'FL.7.GR.1.3', 'FL.7.GR.1.4',
    'FL.7.GR.1.5', 'FL.7.GR.2.1', 'FL.7.GR.2.2', 'FL.7.GR.2.3',
    'FL.8.AR.1.2', 'FL.8.AR.1.3', 'FL.8.AR.3.1', 'FL.8.DP.1.1',
    'FL.8.DP.1.2', 'FL.8.DP.1.3', 'FL.8.GR.1.4', 'FL.8.GR.1.5',
    'FL.8.GR.1.6', 'FL.8.GR.2.1', 'FL.8.GR.2.2', 'FL.8.GR.2.3',
    'FL.8.GR.2.4', 'FL.8.NSO.1.4', 'FL.8.NSO.1.5', 'FL.8.NSO.1.6',
    'FL.8.NSO.1.7', 'FL.912.AR.1.1', 'FL.912.AR.1.4', 'FL.912.AR.4.3',
    'FL.912.DP.1.1', 'FL.912.DP.1.2', 'FL.912.DP.1.3', 'FL.912.DP.1.4',
    'FL.912.DP.2.4', 'FL.912.DP.2.6', 'FL.912.DP.3.1', 'FL.912.F.1.1',
    'FL.912.F.1.3', 'FL.912.F.2.1', 'FL.912.FL.3.2', 'FL.912.NSO.1.4',
    'GA.6.NR.1.1', 'GA.6.NR.1.2', 'GA.6.NR.4.7', 'IN.6.GM.2', 'KS.6.SP.4',
    'KS.7.G.2', 'KS.7.G.3', 'KY.6.G.4', 'KY.7.SP.0', 'KY.7.SP.3',
    'MA.6.RP.A.3.e', 'MA.6.SP.B.4.a', 'MN.6.1.1.4', 'MN.6.1.1.5',
    'MN.6.2.3.4', 'MN.6.3.5.8', 'MN.6.3.5.9', 'MN.7.1.1.5', 'MN.8.1.1.2',
    'MN.8.1.1.5', 'MN.9.1.1.10', 'MN.9.1.1.12', 'MO.6.DSP.B.4',
    'MO.6.DSP.B.4.b', 'MO.7.GM.A.3', 'ND.6.NO.O.3', 'ND.7.NO.O.2',
    'NE.6.D.2.b', 'NE.7.G.2.a', 'NY.7.SP.1', 'OH.7.SP.2c', 'OH.S.ID.1',
    'OK.6.GM.4.1', 'OK.6.N.4.2', 'OK.6.N.4.3', 'OK.7.D.1.2', 'OK.7.D.1.3',
    'OK.7.GM.3.1', 'PA.CC.2.1.HS.F.3', 'PA.M07.C-G.1.1.2',
    'RI.6.RP.A.3.e', 'RI.6.SP.B.4.a', 'SC.6.PAFR.3.6', 'SC.7.DPSR.1.1',
    'SC.7.MGSR.1.1', 'SC.7.PAFR.2.4', 'TN.6.SP.B.4', 'TX.6.12A',
    'TX.6.12B', 'TX.6.12C', 'TX.6.12D', 'TX.6.13A', 'TX.6.13B',
    'TX.6.14A', 'TX.6.14B', 'TX.6.14C', 'TX.6.14D', 'TX.6.14E',
    'TX.6.14F', 'TX.6.14G', 'TX.6.14H', 'TX.6.2A', 'TX.6.2E', 'TX.6.3A',
    'TX.6.3B', 'TX.6.4A', 'TX.6.4B', 'TX.6.4C', 'TX.6.4D', 'TX.6.4E',
    'TX.6.4F', 'TX.6.4G', 'TX.6.4H', 'TX.6.5A', 'TX.6.6A', 'TX.6.6B',
    'TX.6.6C', 'TX.6.7A', 'TX.6.7B', 'TX.6.7C', 'TX.6.7D', 'TX.6.8A',
    'TX.6.8B', 'TX.6.8C', 'TX.6.8D', 'TX.7.12A', 'TX.7.12B', 'TX.7.12C',
    'TX.7.13A', 'TX.7.13B', 'TX.7.13C', 'TX.7.13D', 'TX.7.13E',
    'TX.7.13F', 'TX.7.2', 'TX.7.4A', 'TX.7.4B', 'TX.7.4C', 'TX.7.4D',
    'TX.7.4E', 'TX.7.5A', 'TX.7.5B', 'TX.7.5C', 'TX.7.6F', 'TX.7.6G',
    'TX.7.7', 'TX.7.8A', 'TX.7.8B', 'TX.7.8C', 'TX.7.9A', 'TX.7.9B',
    'TX.7.9C', 'TX.7.9D', 'TX.8.10A', 'TX.8.10B', 'TX.8.10C', 'TX.8.10D',
    'TX.8.11A', 'TX.8.11B', 'TX.8.11C', 'TX.8.12A', 'TX.8.13B',
    'TX.8.13C', 'TX.8.13D', 'TX.8.13E', 'TX.8.13F', 'TX.8.13G', 'TX.8.2C',
    'TX.8.2D', 'TX.8.3A', 'TX.8.3B', 'TX.8.3C', 'TX.8.5A', 'TX.8.5C',
    'TX.8.5D', 'TX.8.5E', 'TX.8.5F', 'TX.8.6A', 'TX.8.6B', 'TX.8.7A',
    'TX.8.7B', 'TX.8.9', 'TX.A1.10C', 'TX.A1.10F', 'TX.A1.2D', 'TX.A1.4A',
    'TX.A1.4B', 'TX.A1.4C', 'TX.A1.6C', 'TX.A1.7C', 'VA.6.CE.1.a',
    'VA.6.CE.1.b', 'VA.6.CE.1.c', 'VA.6.CE.1.d', 'VA.6.CE.1.e',
    'VA.6.MG.1.a', 'VA.6.MG.1.b', 'VA.6.MG.1.b.i', 'VA.6.MG.1.b.ii',
    'VA.6.MG.1.b.iii', 'VA.6.MG.1.c', 'VA.6.MG.1.d', 'VA.6.MG.1.e',
    'VA.6.MG.4.a', 'VA.6.MG.4.c', 'VA.6.MG.4.d', 'VA.6.PS.1.d',
    'VA.6.PS.1.e', 'VA.6.PS.1.f', 'VA.7.MG.3.c', 'VA.7.MG.3.d',
    'VA.7.PS.7.2.d', 'VA.7.PS.7.2.e', 'VA.7.PS.7.2.f', 'VA.8.PS.2.d',
    'VA.8.PS.2.e', 'VA.8.PS.2.i', 'VA.8.PS.2.j', 'VA.PS.7.2.g',
}

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}\n    expected {want}\n    got      {got}")
        print(f"  DRIFT  {label}: {got} != {want}")
    else:
        print(f"  ok     {label}")


def open_ro(path):
    if not os.path.exists(path):
        sys.exit(f"No such file: {path}")
    print(f"Opened (read-only): {os.path.abspath(path)}\n")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def resolve_db(argv):
    if len(argv) > 1:
        return argv[1]
    if os.environ.get("MH2_DB"):
        return os.environ["MH2_DB"]
    try:
        from config import DB
        return str(DB)
    except Exception:
        return "data/build/mh2.db"


def main():
    con = open_ro(resolve_db(sys.argv))

    print("band totals")
    rows = {}
    for band, want in BAND_TOTALS.items():
        rows[band] = build_rows(con, band=band)
        check(f"band={band!r} row count", len(rows[band]), want)

    print("\ndenominator (band=None)")
    check("denominator table", denominator_table(rows[None]), DENOMINATOR)

    print("\nbands partition the denominator")
    ids_pk5 = {r.code for r in rows["PK5"]}
    ids_69 = {r.code for r in rows["6_9"]}
    check("PK5 n 6_9 overlap", len(ids_pk5 & ids_69), 0)
    check("NULL-band remainder",
          len(rows[None]) - len(ids_pk5 | ids_69), 396)

    print("\nPK-5 coverage")
    got = {k: {m: v[m] for m in ("n", "green", "yellow", "red")}
           for k, v in coverage_table(rows["PK5"]).items()}
    check("per-tab coverage", got, PK5_COVERAGE)
    check("total color", dict(Counter(r.color for r in rows["PK5"])), PK5_TOTAL)
    check("claim axis", claim_table(rows["PK5"]), PK5_CLAIM)
    check("flags axis", flags_summary(rows["PK5"]), PK5_FLAGS)
    check("grade_match", grade_match_distribution(rows["PK5"]), PK5_GRADE_MATCH)

    print("\nPK-5 stem attribution (Session B.2)")
    attrib_pk5 = {}
    for sheet in PK5_ATTRIBUTION:
        g = [r for r in rows["PK5"] if r.sheet == sheet]
        attrib_pk5[sheet] = {
            "drafted": sum(1 for r in g if r.ladder_status == "drafted"),
            "undrafted": sum(1 for r in g if r.ladder_status == "undrafted"),
            "unattributed": sum(1 for r in g if r.ladder_status == "unattributed"),
        }
    check("attribution per tab", attrib_pk5, PK5_ATTRIBUTION)
    check("unattributed row set",
          {r.code for r in rows["PK5"] if r.ladder_status == "unattributed"},
          PK5_UNATTRIBUTED)

    print("\nPK-5 alias recoveries (rows that would be Red without the table)")
    recovered = {
        r.code for r in rows["PK5"]
        if r.tags and not any(t.tier == "exact" for t in r.tags)
    }
    check("recovered row set", recovered, PK5_ALIAS_RECOVERIES)

    print("\n6-9 coverage")
    check("total color", dict(Counter(r.color for r in rows["6_9"])), B69_TOTAL)
    check("claim axis", claim_table(rows["6_9"]), B69_CLAIM)
    check("flags axis", flags_summary(rows["6_9"]), B69_FLAGS)
    check("grade_match", grade_match_distribution(rows["6_9"]), B69_GRADE_MATCH)

    print("\n6-9 stem attribution (Session B.2)")
    attrib_69 = {}
    for sheet in B69_ATTRIBUTION:
        g = [r for r in rows["6_9"] if r.sheet == sheet]
        attrib_69[sheet] = {
            "drafted": sum(1 for r in g if r.ladder_status == "drafted"),
            "undrafted": sum(1 for r in g if r.ladder_status == "undrafted"),
            "unattributed": sum(1 for r in g if r.ladder_status == "unattributed"),
        }
    check("attribution per tab", attrib_69, B69_ATTRIBUTION)
    check("unattributed row set",
          {r.code for r in rows["6_9"] if r.ladder_status == "unattributed"},
          B69_UNATTRIBUTED)

    # SUPERSEDED (rev 5, 2026-08-31): 144, measured pre-G6-workbook, when
    # concept_standards had no 6-9 coverage to disagree with anything. Loading
    # the G6 workbook raised it to 247 (more standards for the category route
    # to disagree with); Step 1 SS2.1's stems-exist reversal raised it again
    # to 258, since the category route no longer comes back empty for
    # standards whose only category-route stem lacked a `stems` row. Band-
    # blind by construction (§5 rule 2) -- unrelated to SS2.2's band-aware
    # precedence, which is pinned separately via the band_flips count below.
    _attribution, conflicts, diag = build_stem_attribution(con)
    check("concept_standards vs. category route conflicts (all bands)",
          len(conflicts), 258)

    # Step 1 SS2.2: standards whose stem_ids or ladder_status changed under
    # band-aware precedence vs. the old band-blind rule. Not reconciled
    # against the G6 session's 14 or deferred_s5's narrower-predicate 5 --
    # this is the measured, authoritative count (see
    # data/reports/stem_attribution_conflicts.txt for the full list).
    check("band-aware precedence flips", len(diag["band_flips"]), 12)

    print("\n alias tiers across all bands")
    non_exact = [t for r in rows[None] for t in r.tags if t.tier != "exact"]
    check("non-exact tag pairs", len(non_exact), 41)
    check("nocluster pairs",
          sum(1 for t in non_exact if t.tier == "nocluster"), NOCLUSTER_PAIRS)
    # A nocluster recovery where the ladder code CARRIED a cluster letter is a
    # silent typo correction, not a formatting recovery. Safe (a CCSS number is
    # unique within its domain) but it must stay on the mismatch report.
    carried = {
        t.raw_code for t in non_exact if t.tier == "nocluster"
        and len(t.raw_code.split(".")) > 3
        and len(t.raw_code.split(".")[2]) == 1
        and t.raw_code.split(".")[2].isalpha()
    }
    check("wrong-cluster-letter codes", carried, WRONG_CLUSTER_CODES)

    print("\n6-9 tokens PK-5 never exercised")
    a1 = [t for r in rows["6_9"] for t in r.tags if "A1" in t.node_grades]
    check("tags on nodes declaring A1", len(a1), 116)
    check("A1 tags on-grade", sum(1 for t in a1 if t.grade_match == "on-grade"), 113)
    stems = {
        s for (s,) in con.execute(
            "SELECT DISTINCT stem_id FROM nodes WHERE node_id IN (%s)"
            % ",".join("?" * len({t.node_id for r in rows["6_9"] for t in r.tags})),
            tuple({t.node_id for r in rows["6_9"] for t in r.tags}))
    }
    check("long-form stem IDs reached",
          len([s for s in stems if len(s) > 4]), 7)

    # The 16 unresolved pairs are the §12.4 blank-grade nodes: a ruling row
    # exists with resolution='no_grade_field', so is_leaf lookup succeeds and
    # containment then has no range to test. Correct behaviour, not a defect.
    print("\n6-9 unresolved cause")
    bad = [
        (r.code, t.node_id) for r in rows["6_9"] for t in r.tags
        if t.grade_match == "unresolved" and con.execute(
            "SELECT resolution FROM node_grade_ruling WHERE node_id=?",
            (t.node_id,)).fetchone() not in (("no_grade_field",),)
    ]
    check("unresolved pairs not from a blank grade cell", bad, [])

    con.close()
    print()
    if failures:
        print(f"{len(failures)} figure(s) drifted:\n")
        for f in failures:
            print("  " + f + "\n")
        print("Report the discrepancy. Do not search for a filter that hits "
              "the old number.")
        sys.exit(1)
    print("No drift.")


if __name__ == "__main__":
    main()
