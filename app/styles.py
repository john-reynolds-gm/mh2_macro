"""styles.py — shared CSS for the MH2 app, one copy for every page."""

import streamlit as st

CSS = """
<style>
:root {
  --paper:#FBFBF9; --surface:#FFFFFF; --sunk:#F4F4F1;
  --ink:#1A1D21; --ink-2:#5A6169; --ink-3:#949AA0;
  --rule:#E4E5E2; --rule-2:#CFD1CD;
  --accent:#24455C; --accent-bg:#ECF1F4;
  --judge:#8A6A2B; --judge-bg:#FAF3E4;
  --settled:#3E6B45; --settled-bg:#EDF3EC;
}
.stApp { background: var(--paper); }
.mh2-wordmark { font-family: monospace; font-size:12px; letter-spacing:.06em; color:var(--ink-2); }
.mh2-crumb { font-size:12px; color:var(--ink-3); }
.mh2-domain { font-size:10.5px; letter-spacing:.07em; text-transform:uppercase; color:var(--ink-3); margin:14px 0 2px 2px; }
.mh2-tag { font-size:10.5px; padding:2px 8px; border-radius:4px; letter-spacing:.01em; }
.mh2-tag.det { background:var(--settled-bg); color:var(--settled); }
.mh2-tag.mod { background:var(--judge-bg); color:var(--judge); }
.mh2-chip { font-family:monospace; font-size:11px; background:var(--sunk); color:var(--ink-2);
            border-radius:4px; padding:3px 7px; margin:2px 3px 2px 0; display:inline-block; }
.mh2-chipnote { font-size:11px; color:var(--ink-3); margin-top:4px; line-height:1.4; }
.mh2-ncard { border:1px solid var(--rule); border-radius:6px; padding:8px 10px; background:var(--surface); }
.mh2-ncard.cur { border:1px solid var(--accent); background:var(--accent-bg); }
.mh2-ncard.anchorless { border:1px dashed var(--rule-2); background:var(--paper); }
.mh2-nnum { font-family:monospace; font-size:10px; letter-spacing:.05em; color:var(--ink-3); }
.mh2-ngoal { font-size:13px; line-height:1.35; margin-top:2px; }
.mh2-nstat { margin-top:6px; font-size:11px; color:var(--ink-3); }
.mh2-nstat.done { color:var(--settled); }
.mh2-nstat.open { color:var(--ink-2); }
.mh2-nstat.warn { color:var(--judge); }
.mh2-spec { font-size:13.5px; line-height:1.5; color:var(--ink-2); margin:0; padding-left:1.2em; }
.mh2-rid { font-family:monospace; font-size:11px; color:var(--ink-3); margin-right:6px; }
.mh2-why { font-size:11.5px; color:var(--ink-3); line-height:1.4; margin-top:4px; }
.mh2-ruled-badge { font-family:monospace; font-size:11.5px; color:var(--ink-2); }
</style>
"""


def inject():
    st.markdown(CSS, unsafe_allow_html=True)
