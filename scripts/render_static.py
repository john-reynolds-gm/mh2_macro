"""
render_static.py — Step 3: the static coverage render.

One Python script, one HTML file, no server. Reads the coverage query layer
(mh2.coverage.build_rows), serializes it by hand (never dataclasses.asdict --
see row_dict below), and writes a single self-contained HTML file with the
rows and a node-text lookup embedded as JSON literals in <script> tags. All
CSS and JS are inline. No build step, no CDN, no fetch -- openable by
double-click with no network access.

Usage:
    python scripts/render_static.py [path/to/mh2.db] [--out path/to/out.html]

DB resolution mirrors eval/coverage_view.py: argv db path, $MH2_DB,
config.DB, then a short list of likely paths.

The §4.1 ruling (brief, "out-of-scope grades"): GEO, A2, and (murkier) HS
sit outside PK-8 + Algebra 1 entirely -- 396 standards, exactly the
null-band rows. Excluding them from the denominator would mean changing
coverage.py, which is out of scope for this step (see brief §7) and would
break the "rendered count == denominator count" invariant (§8 #4). So the
ruling this script implements is KEEP AND LABEL: all 2,987 rows are
rendered, and OUT_OF_SCOPE_GRADES is embedded so the page can label them
(dashed chips in the grade filter) -- the grade filter itself shows every
grade by default, same as the color and ladder-status filters.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

from mh2 import coverage, node_lookup  # noqa: E402
from mh2.coverage import _collapse_ws  # noqa: E402

# See module docstring. HS is included despite being "murkier" per the brief
# because it is exactly the rest of the null band -- it gets the same
# dashed-chip out-of-scope label as GEO/A2 in the grade filter.
OUT_OF_SCOPE_GRADES = ("GEO", "A2", "HS")

# The names the row contract closes over (brief §2, §8 #2). A test pins
# this -- set equality, not a count.
#
# DISCREPANCY, reported rather than papered over (per the brief's own
# standing principle, §9): the brief's row_dict() sample and acceptance
# criterion #2 both say "fourteen," but its own §2 contract table (7
# dataclass fields + 4 stem-attribution fields + 4 @property fields) and
# DEFERRED.md §0's identical table both enumerate fifteen distinct names --
# the ones below. Dropping one to force fourteen would drop part of the
# audit itself (color, flagged, reasons, and tagged_in_sheet are exactly
# the fields dataclasses.asdict() silently loses -- see row_dict()), so all
# fifteen are kept and the miscount is flagged here instead.
ROW_DICT_KEYS = frozenset({
    "code", "text", "grade", "band", "sheet", "claim",
    "stem_id", "stem_name", "stem_ids", "ladder_status",
    "color", "flagged", "reasons", "tagged_in_sheet", "tags",
})


def resolve_db(argv_path: str | None) -> Path:
    if argv_path:
        return Path(argv_path).expanduser()
    if os.environ.get("MH2_DB"):
        return Path(os.environ["MH2_DB"]).expanduser()
    if config.DB.exists():
        return config.DB
    root = Path(__file__).resolve().parent.parent
    for c in [root / "data" / "build" / "mh2.db", root / "data" / "mh2.db",
              root / "mh2.db"]:
        if c.exists():
            return c
    sys.exit("Could not locate mh2.db. Pass the path as an argument or set $MH2_DB.")


def tag_dict(t: coverage.Tag) -> dict:
    """A tag's own seven fields, JSON-safe. Node TEXT is deliberately not
    here -- see build_node_lookup and the module docstring's contract note;
    the row/tag payload carries node_id only, resolved against NODES."""
    return {
        "node_id": t.node_id,
        "raw_code": t.raw_code,
        "tier": t.tier,
        "node_grades": list(t.node_grades),
        "is_leaf": t.is_leaf,
        "grade_match": t.grade_match,
        "note": t.note,
    }


def row_dict(r: coverage.StandardRow) -> dict:
    """Hand-written, not dataclasses.asdict() -- asdict() returns only the
    dataclass fields and silently drops color/flagged/reasons/
    tagged_in_sheet, which are @property. That would produce well-formed
    output with the point of the audit missing (brief §2.3)."""
    return {
        "code": r.code, "text": r.text, "grade": r.grade, "band": r.band,
        "sheet": r.sheet, "claim": r.claim,
        "stem_id": r.stem_id, "stem_name": r.stem_name,
        "stem_ids": r.stem_ids, "ladder_status": r.ladder_status,
        "color": r.color, "flagged": r.flagged,
        "reasons": r.reasons, "tagged_in_sheet": r.tagged_in_sheet,
        "tags": [tag_dict(t) for t in r.tags],
    }


def build_node_lookup(con: sqlite3.Connection,
                       rows: list[coverage.StandardRow]) -> dict[str, str]:
    """node_id -> node_text for every node any row's tags point at (brief
    §2.2: a separate lookup, sibling to the rows array, not duplicated onto
    each tag -- 353 nodes measured at ~26K vs. 137K if inlined per-tag).
    Only nodes.node_text is used; goal and concept_skill are not needed.
    """
    node_ids = sorted({t.node_id for r in rows for t in r.tags})
    if not node_ids:
        return {}
    placeholders = ",".join("?" * len(node_ids))
    found = dict(con.execute(
        f"SELECT node_id, node_text FROM nodes WHERE node_id IN ({placeholders})",
        node_ids))
    missing = set(node_ids) - set(found)
    if missing:
        sys.exit(f"node lookup is missing {len(missing)} node_id(s) referenced by "
                  f"a tag -- a blank expansion would result: {sorted(missing)[:10]}")
    return {nid: _collapse_ws(found[nid]) for nid in node_ids}


def summary_counts(rows: list[coverage.StandardRow]) -> dict[str, int]:
    """§1.5's one-line scoreboard. Four buckets, not the three raw
    ladder_status values -- splitting 'drafted' by color surfaces the
    load-bearing distinction in §1.3: a Red row on a drafted stem is a
    worklist item ('you missed this'); a Red row on an undrafted stem is
    not ('nothing to do'). The four are mutually exclusive over
    ladder_status x (color == Red), so they sum to the denominator exactly.
    """
    drafted_red = sum(1 for r in rows if r.ladder_status == "drafted" and r.color == "Red")
    drafted_ok = sum(1 for r in rows if r.ladder_status == "drafted" and r.color != "Red")
    undrafted = sum(1 for r in rows if r.ladder_status == "undrafted")
    unattributed = sum(1 for r in rows if r.ladder_status == "unattributed")
    return {
        "total": len(rows),
        "drafted_red": drafted_red,
        "drafted_ok": drafted_ok,
        "undrafted": undrafted,
        "unattributed": unattributed,
    }


def _json_for_script(data) -> str:
    """json.dumps, with '</' escaped so embedding inside a <script> tag can
    never be truncated early by a literal '</script' inside the payload
    (e.g. inside a standard's text)."""
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MH2 Standards Coverage</title>
<style>
:root {
  color-scheme: light;
  --bg: #f4f5f7; --panel: #ffffff; --border: #e2e4e9; --border-strong: #cbcfd8;
  --text: #1a1d23; --muted: #6b7080; --muted-2: #8a8f9c;
  --accent: #3457d5; --accent-fg: #ffffff; --accent-soft: #eaeefc; --accent-soft-bd: #c3ccf3;
  --green-bg: #e6f4ea; --green-fg: #1e7a37; --green-bd: #b3ddbf;
  --yellow-bg: #fdf1d6; --yellow-fg: #93690a; --yellow-bd: #efd694;
  --red-bg: #fbe6e4; --red-fg: #ab2f24; --red-bd: #f0bab2;
  --chip-bg: #eef0f3; --radius-sm: 6px; --radius-md: 10px; --radius-lg: 14px;
  --shadow-sm: 0 1px 2px rgba(20, 24, 33, .05);
  --shadow-md: 0 2px 8px rgba(20, 24, 33, .06), 0 1px 2px rgba(20, 24, 33, .04);
  --shadow-lg: 0 8px 24px rgba(20, 24, 33, .08), 0 2px 6px rgba(20, 24, 33, .05);
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", "Inter", Helvetica, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}
::selection { background: var(--accent-soft-bd); }
header {
  padding: 16px 24px; background: var(--panel); border-bottom: 1px solid var(--border);
  position: sticky; top: 0; z-index: 5; box-shadow: var(--shadow-sm);
}
h1 { font-size: 17px; font-weight: 650; letter-spacing: -.01em; margin: 0 0 6px; }
#summary { color: var(--muted); font-size: 13px; }
#summary b { color: var(--text); font-weight: 650; }
.tabs { display: flex; gap: 4px; margin: 14px 0 0; flex-wrap: wrap; }
.tab {
  padding: 7px 14px; border: 1px solid transparent; border-bottom: none;
  border-radius: var(--radius-sm) var(--radius-sm) 0 0; background: transparent; cursor: pointer;
  font-size: 13px; color: var(--muted); font-weight: 500; transition: background .12s ease, color .12s ease;
}
.tab:hover { background: var(--chip-bg); color: var(--text); }
.tab.active { background: var(--accent-soft); color: var(--accent); font-weight: 650;
  border-color: var(--accent-soft-bd); }
.filters {
  padding: 14px 24px; background: var(--panel); border-bottom: 1px solid var(--border);
  display: flex; flex-wrap: wrap; gap: 22px; align-items: flex-start;
}
.filter-group { display: flex; flex-direction: column; gap: 5px; }
.filter-group .label { font-size: 10.5px; font-weight: 650; text-transform: uppercase; letter-spacing: .06em;
  color: var(--muted-2); margin-bottom: 1px; }
.chip-row { display: flex; flex-wrap: wrap; gap: 6px; max-width: 420px; }
.chip {
  display: inline-flex; align-items: center; gap: 5px; padding: 4px 10px;
  border: 1px solid var(--border); border-radius: 999px; background: var(--panel);
  cursor: pointer; font-size: 12.5px; user-select: none; white-space: nowrap;
  transition: background .12s ease, border-color .12s ease, color .12s ease;
}
.chip:hover { border-color: var(--border-strong); }
.chip input { margin: 0; accent-color: var(--accent); }
.chip.out-of-scope { border-style: dashed; }
.chip .n { color: var(--muted-2); }
.chip.checked { background: var(--accent-soft); border-color: var(--accent-soft-bd); color: var(--accent); }
.chip.checked .n { color: var(--accent); opacity: .75; }
#search {
  padding: 7px 11px; border: 1px solid var(--border); border-radius: var(--radius-sm);
  font-size: 13px; width: 240px; background: var(--panel); transition: border-color .12s ease, box-shadow .12s ease;
}
#search:focus, .write-row input:focus, .write-row textarea:focus, .write-row select:focus,
.writer-bar input:focus {
  outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft);
}
#reset { align-self: flex-end; font-size: 12.5px; color: var(--accent); background: none;
  border: none; cursor: pointer; padding: 7px 0; font-weight: 600; }
#reset:hover { text-decoration: underline; }
main { padding: 24px 24px 48px; }
table {
  width: 100%; border-collapse: separate; border-spacing: 0; background: var(--panel);
  border: 1px solid var(--border); border-radius: var(--radius-lg);
  box-shadow: var(--shadow-md);
}
th, td { text-align: left; padding: 10px 14px; border-bottom: 1px solid var(--border);
  vertical-align: top; font-size: 13px; }
thead th:first-child { border-top-left-radius: var(--radius-lg); }
thead th:last-child { border-top-right-radius: var(--radius-lg); }
tbody tr:last-child td:first-child { border-bottom-left-radius: var(--radius-lg); }
tbody tr:last-child td:last-child { border-bottom-right-radius: var(--radius-lg); }
th { font-size: 10.5px; font-weight: 650; text-transform: uppercase; letter-spacing: .05em; color: var(--muted-2);
  position: sticky; top: 137px; background: #fbfbfc; z-index: 3; }
tr.row { cursor: pointer; transition: background .1s ease; }
tr.row:hover { background: #f7f8fb; }
tr.row:last-child td, tr.detail:last-child td { border-bottom: none; }
td.code { font-weight: 650; white-space: nowrap; }
td.text { color: #33363d; }
td.grade { white-space: nowrap; width: 4ch; color: var(--muted); }
.color-chip { display: inline-block; width: 9px; height: 9px; margin-right: 7px;
  border-radius: 3px; border: 1px solid; vertical-align: middle; }
.color-Green { background: var(--green-fg); border-color: var(--green-fg); }
.color-Yellow { background: var(--yellow-fg); border-color: var(--yellow-fg); }
.color-Red { background: var(--red-fg); border-color: var(--red-fg); }
.ladder-status { font-size: 12.5px; color: var(--muted); }
.ladder-status.drafted { color: var(--text); font-weight: 550; }
tr.detail td { background: #fafbfc; padding: 16px 14px 22px 32px; box-shadow: inset 0 1px 0 var(--border); }
.detail-section { margin-bottom: 14px; }
.detail-section h4 { margin: 0 0 6px; font-size: 10.5px; font-weight: 650; text-transform: uppercase;
  letter-spacing: .05em; color: var(--muted-2); }
.tag-block { border: 1px solid var(--border); border-radius: var(--radius-md); padding: 10px 12px;
  margin-bottom: 8px; background: var(--panel); box-shadow: var(--shadow-sm); }
.tag-block .node-id { font-weight: 650; }
.tag-block .meta { color: var(--muted); font-size: 12px; }
.tag-block .node-text { margin-top: 5px; }
.reason-list { margin: 0; padding-left: 18px; }
#empty { padding: 56px 20px; text-align: center; color: var(--muted-2); display: none;
  background: var(--panel); border: 1px dashed var(--border); border-radius: var(--radius-lg); margin-top: 14px; }
#count { font-size: 12.5px; color: var(--muted); margin-top: 12px; }
.out-of-scope-note { font-size: 11.5px; color: var(--muted-2); margin-top: 3px; }

.writer-bar { margin-top: 10px; display: flex; align-items: center; gap: 10px; font-size: 12.5px; }
.writer-bar input { padding: 5px 9px; border: 1px solid var(--border); border-radius: var(--radius-sm); font-size: 12.5px; }
#api-status { color: var(--muted); }
.override-marker { margin-left: 5px; font-size: 11px; color: var(--accent); }
.reviewed-badge { margin-left: 6px; font-size: 10.5px; font-weight: 600; padding: 2px 8px; border-radius: 999px;
  background: var(--chip-bg); color: var(--muted); }
.write-section { border-top: 1px solid var(--border); margin-top: 14px; padding-top: 12px; }
.write-section h4 { margin: 0 0 8px; font-size: 10.5px; font-weight: 650; text-transform: uppercase;
  letter-spacing: .05em; color: var(--muted-2); }
.write-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 6px; }
.write-row input[type=text], .write-row textarea, .write-row select {
  padding: 5px 9px; border: 1px solid var(--border); border-radius: var(--radius-sm); font-size: 12.5px;
  background: var(--panel); color: var(--text);
}
.write-row textarea { width: 320px; min-height: 32px; font-family: inherit; }
.write-row button {
  padding: 5px 13px; border: 1px solid var(--accent); border-radius: var(--radius-sm); background: var(--accent);
  color: var(--accent-fg); font-size: 12.5px; font-weight: 600; cursor: pointer; transition: filter .1s ease;
}
.write-row button:hover:not(:disabled) { filter: brightness(1.08); }
.write-row button.secondary { background: var(--panel); color: var(--accent); }
.write-row button.secondary:hover:not(:disabled) { background: var(--accent-soft); }
.write-row button:disabled { opacity: .45; cursor: not-allowed; filter: none; }
.write-status { font-size: 12px; color: var(--muted); }
.current-state { font-size: 12.5px; color: var(--muted); margin-bottom: 6px; }
.tag-block .write-row { margin-top: 8px; margin-bottom: 0; }
</style>
</head>
<body>
<header>
  <h1>MH2 Standards Coverage</h1>
  <div id="summary"></div>
  <div class="writer-bar" id="writer-bar" hidden>
    <label>Writer name <input id="writer-name" type="text" placeholder="your name" autocomplete="off"></label>
    <span id="api-status"></span>
  </div>
  <div class="tabs" id="tabs"></div>
</header>
<div class="filters">
  <div class="filter-group">
    <div class="label">Grade</div>
    <div class="chip-row" id="grade-filter"></div>
    <div class="out-of-scope-note">Dashed chips (GEO / A2 / HS) sit outside PK-8 + Algebra 1
      scope -- see §4.1.</div>
  </div>
  <div class="filter-group">
    <div class="label">Color</div>
    <div class="chip-row" id="color-filter"></div>
  </div>
  <div class="filter-group">
    <div class="label">Ladder status</div>
    <div class="chip-row" id="ladder-filter"></div>
  </div>
  <div class="filter-group">
    <div class="label">&nbsp;</div>
    <div class="chip-row">
      <label class="chip" id="flagged-chip"><input type="checkbox" id="flagged-only">Flagged only <span class="n" id="flagged-n"></span></label>
      <label class="chip" id="tagged-chip"><input type="checkbox" id="tagged-only">Tagged in sheet <span class="n" id="tagged-n"></span></label>
    </div>
  </div>
  <div class="filter-group">
    <div class="label">Search</div>
    <input id="search" type="text" placeholder="code or text contains...">
  </div>
  <button id="reset">Reset filters</button>
</div>
<main>
  <table>
    <thead>
      <tr><th>Standard</th><th>Text</th><th>Grade</th><th>Ladder status</th></tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>
  <div id="empty">No standards match the current filters.</div>
  <div id="count"></div>
</main>

<script id="rows-data" type="application/json">__ROWS_JSON__</script>
<script id="nodes-data" type="application/json">__NODES_JSON__</script>
<script id="stem-names-data" type="application/json">__STEM_NAMES_JSON__</script>
<script id="grade-order-data" type="application/json">__GRADE_ORDER_JSON__</script>
<script id="summary-data" type="application/json">__SUMMARY_JSON__</script>
<script id="tabs-data" type="application/json">__TABS_JSON__</script>
<script id="out-of-scope-data" type="application/json">__OUT_OF_SCOPE_JSON__</script>

<script>
(function () {
  var ROWS = JSON.parse(document.getElementById('rows-data').textContent);
  var NODES = JSON.parse(document.getElementById('nodes-data').textContent);
  var STEM_NAMES = JSON.parse(document.getElementById('stem-names-data').textContent);
  var GRADE_ORDER = JSON.parse(document.getElementById('grade-order-data').textContent);
  var SUMMARY = JSON.parse(document.getElementById('summary-data').textContent);
  var TABS = JSON.parse(document.getElementById('tabs-data').textContent);
  var OUT_OF_SCOPE = JSON.parse(document.getElementById('out-of-scope-data').textContent);

  // D1: write affordances exist only when the page is served over HTTP, so
  // opening the file directly makes zero network calls and shows zero
  // write controls (§8 #4). Every call to the API below is gated on ONLINE.
  var ONLINE = location.protocol !== 'file:';

  var rowsByCode = {};
  ROWS.forEach(function (r) { rowsByCode[r.code] = r; });

  // D2: review_state / effective_color / has_override, keyed by standard_id,
  // loaded from GET /api/audit once ONLINE. Never derived from ROWS.
  var auditIndex = {};
  // D3/D6: per-standard detail (tags with source_key, standard_review,
  // override), fetched lazily on row expand and patched after each write --
  // never refetched wholesale.
  var detailCache = {};
  var detailInFlight = {};

  function writerName() {
    try { return localStorage.getItem('mh2_writer_name') || ''; }
    catch (e) { return ''; }
  }
  function setWriterName(name) {
    try { localStorage.setItem('mh2_writer_name', name); } catch (e) {}
  }

  function apiStatus(msg) {
    var el = document.getElementById('api-status');
    if (el) el.textContent = msg || '';
  }

  function apiGet(path) {
    return fetch(path).then(function (resp) {
      if (!resp.ok) throw new Error(path + ' -> ' + resp.status);
      return resp.json();
    });
  }

  function apiSend(method, path, body) {
    return fetch(path, {
      method: method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    }).then(function (resp) {
      if (!resp.ok) return resp.text().then(function (t) { throw new Error(t || (path + ' -> ' + resp.status)); });
      return resp.json();
    });
  }

  // D5: display resolves to the override when one is set. Offline, or
  // before /api/audit has answered, this is just the computed color.
  function effectiveColor(r) {
    var a = auditIndex[r.code];
    return (a && a.has_override) ? a.effective_color : r.color;
  }

  function reviewState(r) {
    var a = auditIndex[r.code];
    return a ? a.review_state : 'unreviewed';
  }

  function fetchAudit() {
    apiStatus('loading review state…');
    return apiGet('/api/audit').then(function (rows) {
      rows.forEach(function (row) {
        auditIndex[row.standard_id] = {
          review_state: row.review_state,
          effective_color: row.effective_color,
          has_override: row.has_override,
        };
      });
      apiStatus('');
      renderAll();
    }).catch(function (err) {
      apiStatus('could not load review state: ' + err.message);
    });
  }

  // D6: after a successful write, re-fetch this standard's detail only and
  // patch its audit-index entry and detail cache from that response -- never
  // an optimistic local mutation, never a full /api/audit refetch.
  function refreshDetail(code) {
    return apiGet('/api/standards/' + encodeURIComponent(code)).then(function (detail) {
      detailCache[code] = detail;
      var row = rowsByCode[code];
      auditIndex[code] = {
        review_state: detail.standard_review ? detail.standard_review.outcome : 'unreviewed',
        effective_color: detail.override ? detail.override.writer_color : (row ? row.color : null),
        has_override: !!detail.override,
      };
      renderAll();
    });
  }

  function ensureDetail(code) {
    if (detailCache[code] || detailInFlight[code]) return;
    detailInFlight[code] = true;
    apiGet('/api/standards/' + encodeURIComponent(code)).then(function (detail) {
      detailCache[code] = detail;
      delete detailInFlight[code];
      if (state.openCode === code) renderRows();
    }).catch(function (err) {
      delete detailInFlight[code];
      detailCache[code] = { error: err.message };
      if (state.openCode === code) renderRows();
    });
  }

  var state = {
    sheet: TABS[0],
    grades: {},     // empty object = no filter (all pass)
    colors: {},     // empty object = no filter (all pass)
    ladders: {},
    flaggedOnly: false,
    taggedOnly: false,
    search: '',
    openCode: null,
  };

  var allGrades = Array.from(new Set(ROWS.map(function (r) { return r.grade; })))
    .filter(function (g) { return g !== null && g !== undefined; })
    .sort(function (a, b) { return (GRADE_ORDER[a] || 0) - (GRADE_ORDER[b] || 0); });

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function truncate(s, n) {
    s = s || '';
    return s.length > n ? s.slice(0, n).trim() + '…' : s;
  }

  function stemDisplayName(id) {
    return STEM_NAMES[id] || id;
  }

  function passesAllExcept(r, dim) {
    if (dim !== 'sheet' && r.sheet !== state.sheet) return false;
    if (dim !== 'grade' && Object.keys(state.grades).length && !state.grades[r.grade]) return false;
    if (dim !== 'color' && Object.keys(state.colors).length && !state.colors[effectiveColor(r)]) return false;
    if (dim !== 'ladder' && Object.keys(state.ladders).length && !state.ladders[r.ladder_status]) return false;
    if (dim !== 'flagged' && state.flaggedOnly && !r.flagged) return false;
    if (dim !== 'tagged' && state.taggedOnly && !r.tagged_in_sheet) return false;
    if (dim !== 'search' && state.search) {
      var q = state.search.toLowerCase();
      if (r.code.toLowerCase().indexOf(q) === -1 && r.text.toLowerCase().indexOf(q) === -1) return false;
    }
    return true;
  }

  function currentRows() {
    return ROWS.filter(function (r) { return passesAllExcept(r, null); });
  }

  function facetCounts(dim, keyFn) {
    var subset = ROWS.filter(function (r) { return passesAllExcept(r, dim); });
    var counts = {};
    subset.forEach(function (r) {
      var k = keyFn(r);
      counts[k] = (counts[k] || 0) + 1;
    });
    return counts;
  }

  function renderTabs() {
    var tabCounts = {};
    ROWS.forEach(function (r) { tabCounts[r.sheet] = (tabCounts[r.sheet] || 0) + 1; });
    var el = document.getElementById('tabs');
    el.innerHTML = '';
    TABS.forEach(function (sheet) {
      var d = document.createElement('div');
      d.className = 'tab' + (sheet === state.sheet ? ' active' : '');
      d.textContent = sheet + ' (' + (tabCounts[sheet] || 0) + ')';
      d.onclick = function () { state.sheet = sheet; state.openCode = null; renderAll(); };
      el.appendChild(d);
    });
  }

  function renderChipGroup(containerId, options, counts, activeMap, onToggle, outOfScope) {
    var el = document.getElementById(containerId);
    el.innerHTML = '';
    options.forEach(function (opt) {
      var label = document.createElement('label');
      label.className = 'chip' + (activeMap[opt] ? ' checked' : '')
        + ((outOfScope && outOfScope.indexOf(opt) !== -1) ? ' out-of-scope' : '');
      var box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = !!activeMap[opt];
      box.onchange = function () { onToggle(opt, box.checked); };
      label.appendChild(box);
      label.appendChild(document.createTextNode(
        opt + ' '));
      var n = document.createElement('span');
      n.className = 'n';
      n.textContent = '(' + (counts[opt] || 0) + ')';
      label.appendChild(n);
      label.onclick = function (e) {
        if (e.target !== box) { box.checked = !box.checked; onToggle(opt, box.checked); }
      };
      el.appendChild(label);
    });
  }

  function renderFilters() {
    renderChipGroup('grade-filter', allGrades, facetCounts('grade', function (r) { return r.grade; }),
      state.grades, function (g, on) {
        if (on) { state.grades[g] = true; } else { delete state.grades[g]; }
        renderAll();
      }, OUT_OF_SCOPE);

    var colors = ['Green', 'Yellow', 'Red'];
    renderChipGroup('color-filter', colors, facetCounts('color', effectiveColor),
      state.colors, function (c, on) {
        if (on) { state.colors[c] = true; } else { delete state.colors[c]; }
        renderAll();
      });

    var ladders = ['drafted', 'undrafted', 'unattributed'];
    renderChipGroup('ladder-filter', ladders, facetCounts('ladder', function (r) { return r.ladder_status; }),
      state.ladders, function (l, on) {
        if (on) { state.ladders[l] = true; } else { delete state.ladders[l]; }
        renderAll();
      });

    var flaggedCount = ROWS.filter(function (r) { return passesAllExcept(r, 'flagged') && r.flagged; }).length;
    var taggedCount = ROWS.filter(function (r) { return passesAllExcept(r, 'tagged') && r.tagged_in_sheet; }).length;
    document.getElementById('flagged-n').textContent = '(' + flaggedCount + ')';
    document.getElementById('tagged-n').textContent = '(' + taggedCount + ')';
    document.getElementById('flagged-only').checked = state.flaggedOnly;
    document.getElementById('tagged-only').checked = state.taggedOnly;
    document.getElementById('flagged-chip').classList.toggle('checked', state.flaggedOnly);
    document.getElementById('tagged-chip').classList.toggle('checked', state.taggedOnly);
  }

  function tagReviewLine(tag) {
    if (!tag.review) return '';
    return '<div class="current-state">tag review: <b>' + esc(tag.review.outcome) + '</b> by '
      + esc(tag.review.reviewed_by) + (tag.review.note ? ' — ' + esc(tag.review.note) : '') + '</div>';
  }

  function nodeExpansion(tag) {
    // D3: tag.node_text/source_key are present only on the fetched detail
    // (online); the embedded NODES lookup is the offline fallback.
    var text = (tag.node_text !== undefined) ? tag.node_text : NODES[tag.node_id];
    var html = '<div class="tag-block">'
      + '<div><span class="node-id">' + esc(tag.node_id) + '</span> '
      + '<span class="meta">' + esc(tag.tier) + (tag.is_leaf ? ' · leaf' : '') + ' · grade-match: ' + esc(tag.grade_match) + '</span></div>'
      + '<div class="node-text">' + esc(text || '(node text unavailable)') + '</div>'
      + '<div class="meta">' + esc(tag.note) + '</div>'
      + tagReviewLine(tag);
    if (ONLINE && tag.source_key) {
      html += '<div class="tag-write-mount" data-source-key="' + esc(tag.source_key) + '"></div>';
    }
    html += '</div>';
    return html;
  }

  function renderDetail(r) {
    var detail = detailCache[r.code];
    var tags = (ONLINE && detail && !detail.error) ? detail.tags : r.tags;

    var html = '<div class="detail-section"><h4>Full text</h4><div>' + esc(r.text) + '</div></div>';
    html += '<div class="detail-section"><h4>Stem' + (r.stem_ids.length > 1 ? 's' : '') + '</h4><div>'
      + (r.stem_ids.length ? esc(r.stem_ids.map(stemDisplayName).join(', ')) : '(none)')
      + '</div></div>';
    html += '<div class="detail-section"><h4>Tags (' + tags.length + ')</h4>'
      + (tags.length ? tags.map(nodeExpansion).join('') : '<div class="meta">no tags in any ladder</div>')
      + '</div>';
    if (r.reasons.length) {
      html += '<div class="detail-section"><h4>Reasons</h4><ul class="reason-list">'
        + r.reasons.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('')
        + '</ul></div>';
    }

    if (ONLINE) {
      if (!detail) {
        html += '<div class="detail-section write-section"><div class="write-status">loading review controls…</div></div>';
      } else if (detail.error) {
        html += '<div class="detail-section write-section"><div class="write-status">could not load review controls: '
          + esc(detail.error) + '</div></div>';
      } else {
        html += '<div class="detail-section write-section standard-write-mount"></div>';
        html += '<div class="detail-section write-section propose-write-mount"></div>';
      }
    }
    return html;
  }

  // ---------------------------------------------------------- write mounts
  // D4: every write control is disabled until the writer-name field is
  // non-empty. Built with DOM APIs (not innerHTML) so handlers close over
  // the row/detail without touching the global namespace.

  function mountStandardWriteControls(mount, r, detail) {
    if (!mount) return;
    mount.innerHTML = '';
    var enabled = !!writerName();

    var reviewWrap = document.createElement('div');
    reviewWrap.innerHTML = '<h4>Standard review</h4>';
    var reviewState = detail.standard_review;
    var reviewCurrent = document.createElement('div');
    reviewCurrent.className = 'current-state';
    reviewCurrent.textContent = reviewState
      ? ('reviewed as ' + reviewState.outcome + ' by ' + reviewState.reviewed_by
         + (reviewState.note ? ' — ' + reviewState.note : ''))
      : 'not yet reviewed';
    reviewWrap.appendChild(reviewCurrent);

    var reviewRow = document.createElement('div');
    reviewRow.className = 'write-row';
    var outcomeSel = document.createElement('select');
    ['confirmed', 'insufficient'].forEach(function (o) {
      var opt = document.createElement('option'); opt.value = o; opt.textContent = o;
      outcomeSel.appendChild(opt);
    });
    if (reviewState) outcomeSel.value = reviewState.outcome;
    var noteInput = document.createElement('input');
    noteInput.type = 'text'; noteInput.placeholder = 'note (optional)';
    if (reviewState && reviewState.note) noteInput.value = reviewState.note;
    var saveBtn = document.createElement('button');
    saveBtn.textContent = 'Save review';
    saveBtn.disabled = !enabled;
    saveBtn.onclick = function () {
      apiSend('POST', '/api/standards/' + encodeURIComponent(r.code) + '/review', {
        outcome: outcomeSel.value, reviewed_by: writerName(), note: noteInput.value || null,
      }).then(function () { return refreshDetail(r.code); })
        .catch(function (err) { alert('Save failed: ' + err.message); });
    };
    reviewRow.appendChild(outcomeSel);
    reviewRow.appendChild(noteInput);
    reviewRow.appendChild(saveBtn);
    if (reviewState) {
      var clearBtn = document.createElement('button');
      clearBtn.className = 'secondary'; clearBtn.textContent = 'Clear review';
      clearBtn.disabled = !enabled;
      clearBtn.onclick = function () {
        apiSend('DELETE', '/api/standards/' + encodeURIComponent(r.code) + '/review')
          .then(function () { return refreshDetail(r.code); })
          .catch(function (err) { alert('Clear failed: ' + err.message); });
      };
      reviewRow.appendChild(clearBtn);
    }
    reviewWrap.appendChild(reviewRow);
    mount.appendChild(reviewWrap);

    var overrideWrap = document.createElement('div');
    overrideWrap.innerHTML = '<h4>Color override</h4>';
    var ov = detail.override;
    var overrideCurrent = document.createElement('div');
    overrideCurrent.className = 'current-state';
    overrideCurrent.textContent = ov
      ? ('writer says ' + ov.writer_color + ' (computed was ' + ov.computed_color_at_set + ') by '
         + ov.set_by + ' — ' + ov.reason)
      : ('no override; computed color is ' + r.color);
    overrideWrap.appendChild(overrideCurrent);

    var overrideRow = document.createElement('div');
    overrideRow.className = 'write-row';
    var colorSel = document.createElement('select');
    ['Green', 'Yellow', 'Red'].forEach(function (c) {
      var opt = document.createElement('option'); opt.value = c; opt.textContent = c;
      colorSel.appendChild(opt);
    });
    colorSel.value = ov ? ov.writer_color : r.color;
    var reasonInput = document.createElement('input');
    reasonInput.type = 'text'; reasonInput.placeholder = 'reason (required)';
    if (ov) reasonInput.value = ov.reason;
    var setBtn = document.createElement('button');
    setBtn.textContent = 'Set override';
    setBtn.disabled = !enabled;
    setBtn.onclick = function () {
      if (!reasonInput.value.trim()) { alert('A reason is required.'); return; }
      apiSend('POST', '/api/standards/' + encodeURIComponent(r.code) + '/override', {
        writer_color: colorSel.value, reason: reasonInput.value, set_by: writerName(),
      }).then(function () { return refreshDetail(r.code); })
        .catch(function (err) { alert('Save failed: ' + err.message); });
    };
    overrideRow.appendChild(colorSel);
    overrideRow.appendChild(reasonInput);
    overrideRow.appendChild(setBtn);
    if (ov) {
      var clearOvBtn = document.createElement('button');
      clearOvBtn.className = 'secondary'; clearOvBtn.textContent = 'Clear override';
      clearOvBtn.disabled = !enabled;
      clearOvBtn.onclick = function () {
        apiSend('DELETE', '/api/standards/' + encodeURIComponent(r.code) + '/override')
          .then(function () { return refreshDetail(r.code); })
          .catch(function (err) { alert('Clear failed: ' + err.message); });
      };
      overrideRow.appendChild(clearOvBtn);
    }
    overrideWrap.appendChild(overrideRow);
    mount.appendChild(overrideWrap);
  }

  function mountTagWriteControls(mount, r, detail, sourceKey) {
    if (!mount || !sourceKey) return;
    mount.innerHTML = '';
    var enabled = !!writerName();
    var tag = detail.tags.filter(function (t) { return t.source_key === sourceKey; })[0];
    var row = document.createElement('div');
    row.className = 'write-row';
    var sel = document.createElement('select');
    ['confirmed', 'insufficient', 'wrong_node'].forEach(function (o) {
      var opt = document.createElement('option'); opt.value = o; opt.textContent = o;
      sel.appendChild(opt);
    });
    if (tag && tag.review) sel.value = tag.review.outcome;
    var noteInput = document.createElement('input');
    noteInput.type = 'text'; noteInput.placeholder = 'note (optional)';
    var btn = document.createElement('button');
    btn.textContent = (tag && tag.review) ? 'Update tag review' : 'Review tag';
    btn.disabled = !enabled;
    btn.onclick = function () {
      apiSend('POST', '/api/tags/review', {
        standard_id: r.code, source_key: sourceKey, outcome: sel.value,
        reviewed_by: writerName(), note: noteInput.value || null,
      }).then(function () { return refreshDetail(r.code); })
        .catch(function (err) { alert('Save failed: ' + err.message); });
    };
    row.appendChild(sel);
    row.appendChild(noteInput);
    row.appendChild(btn);
    mount.appendChild(row);
  }

  var STEM_IDS_BY_NAME = Object.keys(STEM_NAMES).sort(function (a, b) {
    return STEM_NAMES[a].localeCompare(STEM_NAMES[b]);
  });

  function mountProposeControls(mount, r) {
    if (!mount) return;
    mount.innerHTML = '<h4>Propose a tag</h4>';
    var enabled = !!writerName();
    var row = document.createElement('div');
    row.className = 'write-row';

    var nodesByStem = {};

    var stemSel = document.createElement('select');
    stemSel.appendChild(new Option('-- stem --', ''));
    STEM_IDS_BY_NAME.forEach(function (id) {
      stemSel.appendChild(new Option(STEM_NAMES[id], id));
    });

    var nodeSel = document.createElement('select');
    nodeSel.disabled = true;
    nodeSel.appendChild(new Option('-- choose a stem first --', ''));

    function populateNodeSelect(nodes) {
      nodeSel.innerHTML = '';
      nodeSel.appendChild(new Option('-- node --', ''));
      var groupEl = null;
      var lastSkill = null;
      nodes.forEach(function (n) {
        var skill = n.concept_skill || 'Ungrouped';
        if (skill !== lastSkill) {
          groupEl = document.createElement('optgroup');
          groupEl.label = skill;
          nodeSel.appendChild(groupEl);
          lastSkill = skill;
        }
        groupEl.appendChild(new Option(n.node_text, n.source_key));
      });
      nodeSel.disabled = false;
    }

    stemSel.onchange = function () {
      var stemId = stemSel.value;
      nodeSel.innerHTML = '';
      nodeSel.disabled = true;
      if (!stemId) {
        nodeSel.appendChild(new Option('-- choose a stem first --', ''));
        return;
      }
      if (nodesByStem[stemId]) {
        populateNodeSelect(nodesByStem[stemId]);
        return;
      }
      nodeSel.appendChild(new Option('loading…', ''));
      apiGet('/api/nodes?stem_id=' + encodeURIComponent(stemId)).then(function (nodes) {
        nodesByStem[stemId] = nodes;
        if (stemSel.value === stemId) populateNodeSelect(nodes);
      }).catch(function (err) {
        nodeSel.innerHTML = '';
        nodeSel.appendChild(new Option('-- failed to load nodes --', ''));
        apiStatus('node lookup failed: ' + err.message);
      });
    };

    var textInput = document.createElement('textarea');
    textInput.placeholder = 'node text you saw';
    var rationaleInput = document.createElement('textarea');
    rationaleInput.placeholder = 'rationale (optional)';
    var btn = document.createElement('button');
    btn.textContent = 'Propose';
    btn.disabled = !enabled;
    btn.onclick = function () {
      var sourceKey = nodeSel.value;
      if (!sourceKey || !textInput.value.trim()) {
        alert('a node and node text are required.'); return;
      }
      apiSend('POST', '/api/proposals', {
        standard_id: r.code, source_key: sourceKey,
        node_text_seen: textInput.value.trim(), proposed_by: writerName(),
        rationale: rationaleInput.value || null,
      }).then(function () {
        apiStatus('proposal recorded');
        stemSel.value = '';
        nodeSel.innerHTML = '';
        nodeSel.appendChild(new Option('-- choose a stem first --', ''));
        nodeSel.disabled = true;
        textInput.value = ''; rationaleInput.value = '';
      }).catch(function (err) { alert('Propose failed: ' + err.message); });
    };
    row.appendChild(stemSel);
    row.appendChild(nodeSel);
    row.appendChild(textInput);
    row.appendChild(rationaleInput);
    row.appendChild(btn);
    mount.appendChild(row);
  }

  function renderRows() {
    var rows = currentRows();
    var tbody = document.getElementById('rows');
    tbody.innerHTML = '';
    document.getElementById('empty').style.display = rows.length ? 'none' : 'block';
    document.getElementById('count').textContent = rows.length + ' of ' + ROWS.length + ' standards shown';

    rows.forEach(function (r) {
      var tr = document.createElement('tr');
      tr.className = 'row';
      var ec = effectiveColor(r);
      var a = auditIndex[r.code];
      var badges = ((a && a.has_override) ? '<span class="override-marker" title="writer override; computed color: ' + esc(r.color) + '">&#9733;</span>' : '')
        + ((a && a.review_state && a.review_state !== 'unreviewed') ? '<span class="reviewed-badge">' + esc(a.review_state) + '</span>' : '');
      tr.innerHTML = '<td class="code"><span class="color-chip color-' + ec + '" title="' + esc(ec) + '"></span>'
        + esc(r.code) + badges + '</td>'
        + '<td class="text">' + esc(truncate(r.text, 110)) + '</td>'
        + '<td class="grade">' + esc(r.grade) + '</td>'
        + '<td class="ladder-status ' + r.ladder_status + '">' + r.ladder_status + '</td>';
      tr.onclick = function () {
        state.openCode = (state.openCode === r.code) ? null : r.code;
        renderRows();
      };
      tbody.appendChild(tr);
      if (state.openCode === r.code) {
        var dtr = document.createElement('tr');
        dtr.className = 'detail';
        var td = document.createElement('td');
        td.colSpan = 4;
        td.innerHTML = renderDetail(r);
        dtr.appendChild(td);
        tbody.appendChild(dtr);

        if (ONLINE) {
          ensureDetail(r.code);
          var detail = detailCache[r.code];
          if (detail && !detail.error) {
            mountStandardWriteControls(td.querySelector('.standard-write-mount'), r, detail);
            mountProposeControls(td.querySelector('.propose-write-mount'), r);
            Array.prototype.forEach.call(td.querySelectorAll('.tag-write-mount'), function (mount) {
              mountTagWriteControls(mount, r, detail, mount.getAttribute('data-source-key'));
            });
          }
        }
      }
    });
  }

  function renderSummary() {
    document.getElementById('summary').innerHTML =
      '<b>' + SUMMARY.total + '</b> standards owed &middot; '
      + '<b>' + SUMMARY.drafted_red + '</b> red on a drafted stem (worklist) &middot; '
      + '<b>' + SUMMARY.drafted_ok + '</b> covered on a drafted stem &middot; '
      + '<b>' + SUMMARY.undrafted + '</b> undrafted (nothing to do yet) &middot; '
      + '<b>' + SUMMARY.unattributed + '</b> unattributed';
  }

  function renderAll() {
    renderTabs();
    renderFilters();
    renderRows();
  }

  document.getElementById('search').oninput = function (e) {
    state.search = e.target.value;
    renderRows();
    renderFilters();
  };
  document.getElementById('flagged-only').onchange = function (e) {
    state.flaggedOnly = e.target.checked; renderAll();
  };
  document.getElementById('tagged-only').onchange = function (e) {
    state.taggedOnly = e.target.checked; renderAll();
  };
  document.getElementById('reset').onclick = function () {
    state.sheet = TABS[0];
    state.grades = {};
    state.colors = {};
    state.ladders = {};
    state.flaggedOnly = false;
    state.taggedOnly = false;
    state.search = '';
    document.getElementById('search').value = '';
    state.openCode = null;
    renderAll();
  };

  if (ONLINE) {
    var bar = document.getElementById('writer-bar');
    bar.hidden = false;
    var nameInput = document.getElementById('writer-name');
    nameInput.value = writerName();
    nameInput.oninput = function (e) { setWriterName(e.target.value); renderAll(); };
  }

  renderSummary();
  renderAll();
  if (ONLINE) fetchAudit();
})();
</script>
</body>
</html>
"""


def render(con: sqlite3.Connection, out_path: Path) -> list[coverage.StandardRow]:
    rows = coverage.build_rows(con, band=None)
    nodes = build_node_lookup(con, rows)
    stem_names = node_lookup.build_stem_names(con)
    grade_order = dict(con.execute("SELECT grade, ord FROM grade_order"))

    data = [row_dict(r) for r in rows]
    for d in data:
        assert set(d.keys()) == ROW_DICT_KEYS

    html = _PAGE_TEMPLATE
    html = html.replace("__ROWS_JSON__", _json_for_script(data))
    html = html.replace("__NODES_JSON__", _json_for_script(nodes))
    html = html.replace("__STEM_NAMES_JSON__", _json_for_script(stem_names))
    html = html.replace("__GRADE_ORDER_JSON__", _json_for_script(grade_order))
    html = html.replace("__TABS_JSON__", _json_for_script(list(coverage.TABS)))
    html = html.replace("__OUT_OF_SCOPE_JSON__", _json_for_script(list(OUT_OF_SCOPE_GRADES)))
    html = html.replace("__SUMMARY_JSON__", _json_for_script(summary_counts(rows)))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", nargs="?", help="path to mh2.db")
    parser.add_argument("--out", default=str(config.REPORTS / "coverage.html"))
    args = parser.parse_args(argv)

    db_path = resolve_db(args.db)
    if not db_path.exists():
        sys.exit(f"No database at {db_path}")

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    out_path = Path(args.out)
    rows = render(con, out_path)
    con.close()

    print(f"db:  {db_path}")
    print(f"out: {out_path}  ({out_path.stat().st_size / 1024:.0f} KB)")
    print(f"rows: {len(rows)}")
    print(
        "\n§4.1 ruling applied: GEO/A2/HS (396 rows, the null band) are KEPT and "
        "LABELED, not excluded -- excluding them would require changing "
        "coverage.py's denominator, which is out of scope for this step. The "
        "grade filter shows them by default like any other grade; the dashed "
        "chip styling is what marks them as out-of-scope.")


if __name__ == "__main__":
    main()
