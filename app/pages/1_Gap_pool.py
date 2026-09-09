"""
Gap pool — category-routed browse of the 1,493-row / 37-state
"Other State Standards Gaps" sheet (`leaves`, origin='gaps_sheet').

Browse only: these rows have no node_id, so there's nothing here to rule on.
Routing to a stem/domain comes from `data/source/workbooks/mh2_category_to_stems.csv`
(John's first-pass draft) — read live, never hardcoded, so edits to that file
don't require a code change.
"""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402
import styles  # noqa: E402

st.set_page_config(page_title="MH2 Gap Pool", layout="wide")
styles.inject()

con = db.get_connection()
V = st.session_state.get("data_version", 0)

st.markdown('<span class="mh2-wordmark">MH2 · GAP POOL</span>', unsafe_allow_html=True)

categories = db.gap_pool_categories(con, V)
total = sum(c["total"] for c in categories)
proposed = sum(c["proposed"] for c in categories)
covered = sum(c["covered"] for c in categories)

# ----------------------------------------------------------------- sidebar

with st.sidebar:
    st.markdown("**Gap pool**")
    st.caption(f"{total} rows · {proposed} proposed · {covered} covered")
    st.divider()

    groups = {}
    for c in categories:
        head = c["category"].split(":", 1)[0].strip() if ":" in c["category"] else "Other"
        groups.setdefault(head, []).append(c)

    if "gap_category" not in st.session_state:
        st.session_state.gap_category = categories[0]["category"] if categories else None

    for head in sorted(groups):
        st.markdown(f'<div class="mh2-domain">{head}</div>', unsafe_allow_html=True)
        for c in sorted(groups[head], key=lambda c: c["category"]):
            label = f"{c['category']}  ({c['total']})"
            if st.button(label, key=f"gapcat_{c['category']}", use_container_width=True):
                st.session_state.gap_category = c["category"]

# ------------------------------------------------------------------- main

if not st.session_state.gap_category:
    st.write("No gap-pool categories found.")
    st.stop()

current = next((c for c in categories if c["category"] == st.session_state.gap_category), None)
if current is None:
    current = categories[0]
    st.session_state.gap_category = current["category"]

st.markdown(f"## {current['category']}")
st.markdown(f'<span class="mh2-chipnote">{current["total"]} standards across '
            f'{current["n_states"]} states &mdash; {current["proposed"]} proposed, '
            f'{current["covered"]} already covered</span>', unsafe_allow_html=True)

st.markdown('<div class="mh2-domain">Routes to</div>', unsafe_allow_html=True)
if not current["targets"]:
    st.markdown('<span class="mh2-chipnote">no routing on file for this category yet</span>',
                unsafe_allow_html=True)
else:
    for t in current["targets"]:
        band_label = "PK–5" if t["band"] == "PK5" else "6–9"
        if t["stem_id"]:
            tag = f'<span class="mh2-tag det">{t["n_nodes"]} nodes</span>'
        else:
            tag = '<span class="mh2-tag mod">no ladder yet</span>'
        st.markdown(
            f'<div style="margin:3px 0">'
            f'<span class="mh2-chipnote">{band_label} &rsaquo; {t["domain"]} &rsaquo; {t["stem_name"]}</span> '
            f'{tag}</div>', unsafe_allow_html=True)

st.divider()

# ------------------------------------------------------------------ filters

all_states = db.gap_pool_states(con, V)
f1, f2 = st.columns([2, 1])
with f1:
    picked_states = st.multiselect("States", all_states, default=[], key="gap_states")
with f2:
    picked_status = st.selectbox("Status", ["(all)", "proposed", "covered"], key="gap_status")

rows = db.gap_pool_rows(
    con, current["category"], V,
    states=tuple(picked_states),
    status=None if picked_status == "(all)" else picked_status,
)

st.caption(f"{len(rows)} of {current['total']} shown")

st.dataframe(
    [{
        "State": r["state"],
        "Standard": r["standard_id"],
        "Grade": r["grade"] or "",
        "Text": r["standard_text"] or "(no text on file)",
        "Status": r["status"],
        "Notes": r["notes"] or "",
    } for r in rows],
    use_container_width=True,
    hide_index=True,
    column_config={
        "Standard": st.column_config.TextColumn(width="small"),
        "Grade": st.column_config.TextColumn(width="small"),
        "Status": st.column_config.TextColumn(width="small"),
        "Text": st.column_config.TextColumn(width="large"),
        "Notes": st.column_config.TextColumn(width="medium"),
    },
)

st.caption("Browse only — these rows aren't tied to a node yet, so there's nothing "
           "to rule on here. Use Review once a category's target stem has a ladder.")
