"""
review.py — MH2 Standards Alignment Review, v1.

Read/write UI layer only. Scoring, thresholds, mh2/candidates.py and
mh2/rerank.py are untouched. The only write path is `db.write_ruling()`,
which upserts a row into `node_standards` (layer 3, authoritative, durable
across a rebuild).

    streamlit run app/review.py
"""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db  # noqa: E402
import styles  # noqa: E402

st.set_page_config(page_title="MH2 Standards Alignment Review", layout="wide")
styles.inject()

con = db.get_connection()

if "data_version" not in st.session_state:
    st.session_state.data_version = 0
if "stem_id" not in st.session_state:
    st.session_state.stem_id = None
if "concept_skill" not in st.session_state:
    st.session_state.concept_skill = None
if "node_id" not in st.session_state:
    st.session_state.node_id = None


def bump():
    st.session_state.data_version += 1


V = st.session_state.data_version

# ---------------------------------------------------------------- top bar

top_l, top_r = st.columns([3, 1])
with top_l:
    st.markdown('<span class="mh2-wordmark">MH2 · STANDARDS ALIGNMENT REVIEW</span>',
                unsafe_allow_html=True)
with top_r:
    known = db.known_reviewers(con)
    options = ["(select)"] + known + ["(new reviewer)"]
    choice = st.selectbox("Reviewing as", options, key="reviewer_choice",
                           label_visibility="collapsed")
    if choice == "(new reviewer)":
        REVIEWER = (st.text_input("Your name", key="reviewer_name_input").strip()
                    or "unknown")
    elif choice == "(select)":
        REVIEWER = "unknown"
    else:
        REVIEWER = choice

# ----------------------------------------------------------------- sidebar

with st.sidebar:
    st.markdown("**Review**")
    st.caption("Search")
    st.caption("Leaves")
    st.caption("Export")
    st.divider()

    tree = db.domain_tree(con, V)
    for domain, stems in tree.items():
        drafted = [s for s in stems if s["n_nodes"] > 0]
        st.markdown(f'<div class="mh2-domain">{domain}</div>', unsafe_allow_html=True)
        if not drafted:
            st.caption("no ladder")
            continue
        for s in sorted(stems, key=lambda s: (-s["n_nodes"], s["name"])):
            if s["n_nodes"] == 0:
                st.caption(f"·  {s['name']} — no ladder")
                continue
            label = f"{s['name']}  ({s['ruled']}/{s['total']})"
            if st.button(label, key=f"stem_{s['stem_id']}", use_container_width=True):
                st.session_state.stem_id = s["stem_id"]
                st.session_state.concept_skill = None
                st.session_state.node_id = None
            if st.session_state.stem_id == s["stem_id"]:
                cs_list = db.concept_skills_for_stem(con, s["stem_id"], V)
                for cs in cs_list:
                    cs_label = f" {cs['concept_skill']}  ({cs['ruled']}/{cs['total']})"
                    if st.button(cs_label, key=f"cs_{s['stem_id']}_{cs['concept_skill']}",
                                 use_container_width=True):
                        st.session_state.concept_skill = cs["concept_skill"]
                        st.session_state.node_id = None

# ------------------------------------------------------------------- main

if not st.session_state.stem_id or not st.session_state.concept_skill:
    st.title("MH2 Standards Alignment Review")
    st.write("Pick a stem, then a concept/skill, from the sidebar to begin.")
    st.stop()

stem_id = st.session_state.stem_id
concept_skill = st.session_state.concept_skill

nodes = db.nodes_for_concept_skill(con, stem_id, concept_skill, V)
if not nodes:
    st.warning("No nodes found for this concept/skill.")
    st.stop()

if st.session_state.node_id not in {n["node_id"] for n in nodes}:
    st.session_state.node_id = nodes[0]["node_id"]

cs_total = sum(n["total"] for n in nodes)
cs_ruled = sum(n["ruled"] for n in nodes)

st.markdown(f'<div class="mh2-crumb">{stem_id} &rsaquo; {concept_skill}</div>',
            unsafe_allow_html=True)
h1_l, h1_r = st.columns([3, 1])
with h1_l:
    st.markdown(f"## {concept_skill}")
with h1_r:
    st.markdown(f'<div style="text-align:right;padding-top:14px" class="mh2-ruled-badge">'
                f'ruled {cs_ruled} of {cs_total} candidates</div>', unsafe_allow_html=True)

# ------------------------------------------------------------- node strip

strip_cols = st.columns(len(nodes))
for i, (col, n) in enumerate(zip(strip_cols, nodes)):
    is_cur = n["node_id"] == st.session_state.node_id
    anchorless = len(n["anchors"]) == 0
    if anchorless:
        klass, stat_klass, stat_text = "anchorless", "warn", "no CCSS anchor"
    elif n["ruled"] == n["total"] and n["total"] > 0:
        klass, stat_klass, stat_text = "cur" if is_cur else "", "done", f"{n['ruled']} ruled"
    elif n["ruled"] > 0:
        klass, stat_klass, stat_text = "cur" if is_cur else "", "open", f"{n['total'] - n['ruled']} open"
    else:
        klass, stat_klass, stat_text = "cur" if is_cur else "", "", "not started"
    if is_cur and klass == "":
        klass = "cur"
    with col:
        st.markdown(f"""
        <div class="mh2-ncard {klass}">
          <div class="mh2-nnum">NODE {i + 1}</div>
          <div class="mh2-ngoal">{n['goal'] or n['node_text'][:60]}</div>
          <div class="mh2-nstat {stat_klass}">{stat_text}</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Select", key=f"sel_{n['node_id']}", use_container_width=True):
            st.session_state.node_id = n["node_id"]
            st.rerun()

st.divider()

# --------------------------------------------------------------- node head

node = db.get_node(con, st.session_state.node_id, V)
spec_text = db.field_text(node, "specifications")

nh_l, nh_r = st.columns([3, 1])
with nh_l:
    st.markdown(f"#### {node['node_text']}")
    if spec_text:
        items = "".join(f"<li>{line}</li>" for line in spec_text.split("\n") if line.strip())
        st.markdown(f'<ul class="mh2-spec">{items}</ul>', unsafe_allow_html=True)
with nh_r:
    st.markdown('<div style="text-align:right" class="mh2-domain">CCSS anchors</div>',
                unsafe_allow_html=True)
    if node["anchors"]:
        chips = "".join(f'<span class="mh2-chip">{a}</span>' for a in node["anchors"])
        st.markdown(f'<div style="text-align:right">{chips}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div style="text-align:right" class="mh2-chipnote">'
                     'no CCSS anchor — state anchor pending</div>', unsafe_allow_html=True)
    if node["has_absence_assertion"]:
        st.markdown('<div style="text-align:right" class="mh2-chipnote">'
                     'author asserts: not covered by CCSS</div>', unsafe_allow_html=True)

# ------------------------------------------------------------- state panes

STATE_NAMES = {"CA": "California", "TX": "Texas", "FL": "Florida"}
pool = db.suggested_pool(con, V, node_id=node["node_id"])

panes = st.columns(3)
for pane, state in zip(panes, db.PRIORITY_STATES):
    rows_for_state = [r for r in pool if r["state"] == state]
    tag_label, tag_class = db.pane_header_tag(rows_for_state)
    with pane:
        st.markdown(f"""
        <div style="display:flex;align-items:center;justify-content:space-between;">
          <span style="font-weight:500">{STATE_NAMES[state]}</span>
          <span class="mh2-tag {tag_class}">{tag_label}</span>
        </div>
        """, unsafe_allow_html=True)

        chips = db.from_ladder_chips(con, node["node_id"], state)
        st.markdown('<div class="mh2-domain">From the ladder</div>', unsafe_allow_html=True)
        if chips:
            chip_html = "".join(f'<span class="mh2-chip">{c["standard_code"]}</span>' for c in chips)
            st.markdown(chip_html, unsafe_allow_html=True)
            for c in chips:
                if c["relation"] != "aligned" or c["annotation"]:
                    note = c["annotation"] or c["relation"]
                    st.markdown(f'<div class="mh2-chipnote"><code>{c["standard_code"]}</code>'
                                f' — {note}</div>', unsafe_allow_html=True)
        else:
            st.markdown('<span class="mh2-chipnote">none tagged</span>', unsafe_allow_html=True)

        st.markdown(f'<div class="mh2-domain">Suggested ({len(rows_for_state)} not already tagged)</div>',
                    unsafe_allow_html=True)
        if not rows_for_state:
            st.markdown('<span class="mh2-chipnote">no suggestions clear the surface threshold</span>',
                        unsafe_allow_html=True)
        for r in rows_for_state:
            current = r["ruled_status"] if r["ruled"] else None
            badge = {"accepted": "✓", "rejected": "✕"}.get(current, "")
            with st.expander(f"{badge} {r['standard_code']} — "
                              f"{(r['standard_text'] or '')[:70]}", expanded=False):
                st.markdown("**Node**")
                st.write(node["node_text"])
                st.markdown("**Candidate standard**")
                st.write(r["standard_text"] or "(no text on file)")
                st.markdown(f'<div class="mh2-why">{db.explain(r)}</div>', unsafe_allow_html=True)
                bcol1, bcol2, bcol3 = st.columns(3)
                with bcol1:
                    if st.button("Accept", key=f"acc_{node['node_id']}_{state}_{r['standard_code']}",
                                 type="primary" if current != "accepted" else "secondary"):
                        db.write_ruling(con, node["node_id"], r["standard_code"], "accepted", REVIEWER)
                        bump()
                        st.rerun()
                with bcol2:
                    if st.button("Reject", key=f"rej_{node['node_id']}_{state}_{r['standard_code']}",
                                 type="primary" if current != "rejected" else "secondary"):
                        db.write_ruling(con, node["node_id"], r["standard_code"], "rejected", REVIEWER)
                        bump()
                        st.rerun()
                with bcol3:
                    if current:
                        st.caption(f"ruled: {current} by {r['reviewed_by']}")

st.divider()

# ------------------------------------------------------------ other states

other = db.other_states_summary(con, node["node_id"], V)
st.markdown(f"""
<div style="display:flex;align-items:center;justify-content:space-between;">
  <span style="font-weight:500">Other states</span>
  <span class="mh2-tag det">lesson overlap · crosswalk · model</span>
</div>
""", unsafe_allow_html=True)
if other["total"] == 0:
    st.markdown('<span class="mh2-chipnote">no suggestions for any other state</span>',
                unsafe_allow_html=True)
else:
    st.markdown(f'<span class="mh2-chipnote">{other["total"]} candidates across '
                f'{other["n_states"]} states.</span>', unsafe_allow_html=True)
    m1, m2 = st.columns(2)
    m1.metric("Strong", other["strong"])
    m2.metric("Moderate or weak", other["moderate_or_weak"])
    if st.button(f"Accept all {other['strong'] + other['moderate']} strong or moderate"):
        for r in other["rows"]:
            if r["strength"] in ("strong", "moderate") and not r["ruled"]:
                db.write_ruling(con, node["node_id"], r["standard_code"], "accepted", REVIEWER)
        bump()
        st.rerun()

    strength_filter = st.selectbox(
        "Filter by strength", ["All", "Strong", "Moderate", "Weak"],
        key="other_strength_filter")
    filtered = other["rows"] if strength_filter == "All" else [
        r for r in other["rows"] if r["strength"] == strength_filter.lower()]

    with st.expander(f"Show {len(filtered)} of {other['total']} (stacked, by state)",
                      expanded=True):
        if not filtered:
            st.caption("nothing at this strength")
        for r in filtered:
            current = r["ruled_status"] if r["ruled"] else None
            badge = {"accepted": "✓", "rejected": "✕"}.get(current, "")
            st.markdown(f"**{badge} {r['state']} · {r['standard_code']}** "
                        f"({r['strength']})  —  {r['standard_text'] or ''}")
            st.markdown(f'<div class="mh2-why">{db.explain(r)}</div>', unsafe_allow_html=True)
            bcol1, bcol2 = st.columns(2)
            with bcol1:
                if st.button("Accept", key=f"oacc_{node['node_id']}_{r['state']}_{r['standard_code']}"):
                    db.write_ruling(con, node["node_id"], r["standard_code"], "accepted", REVIEWER)
                    bump()
                    st.rerun()
            with bcol2:
                if st.button("Reject", key=f"orej_{node['node_id']}_{r['state']}_{r['standard_code']}"):
                    db.write_ruling(con, node["node_id"], r["standard_code"], "rejected", REVIEWER)
                    bump()
                    st.rerun()
            st.markdown("---")

st.caption("Everything surfaced here has been ruled on. That is not the same as "
           "complete — nothing in this tool can confirm that a node has found "
           "every standard that belongs to it.")
