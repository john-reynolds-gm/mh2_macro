/* MH2 Grade Sequencing Tool, v1 prototype frontend.
 *
 * One classic script, no framework, no build step, no network except the
 * same-origin /api/seq/* (or the embedded demo payload).  Binding spec:
 * docs/seq_v1_contract.md (section 7 = this file).
 *
 * Sections, in order:
 *   1. esc            text escaping (every rendered string goes through it)
 *   2. constants      labels, defaults
 *   3. pure logic     no DOM, tested in node: state <-> URL, guardrail mirror,
 *                     compare assembly, slice filtering, ordering arithmetic
 *   4. API adapters   HttpApi (server) and DemoApi (offline, in memory)
 *   5. render         pure (state, data) -> HTML string functions
 *   6. controller     holds the page state, delegated events, writes
 *   7. boot
 *
 * The page never decides a node's kind, grade membership, status or badge:
 * it reads what the server (or the demo payload) provides.  The only
 * exception is DemoApi, which has to rebuild a SequenceView by itself.
 */
"use strict";

/* ========================================================================
 * 1. esc
 * ===================================================================== */

/** HTML-escape any value for safe inclusion in markup or an attribute. */
function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/* ========================================================================
 * 2. constants
 * ===================================================================== */

var GRADE_SHORT = { PK: "PK", K: "K", "1": "G1", "2": "G2", "3": "G3", "4": "G4",
  "5": "G5", "6": "G6", "7": "G7", "8": "G8", A1: "A1" };
var GRADE_LABELS = { PK: "Pre-K", K: "Kindergarten", "1": "Grade 1", "2": "Grade 2",
  "3": "Grade 3", "4": "Grade 4", "5": "Grade 5", "6": "Grade 6", "7": "Grade 7",
  "8": "Grade 8", A1: "Algebra 1" };
var CALIBRATIONS = ["deep", "functional", "illuminating"];
var CAL_LABEL = { deep: "Deep", functional: "Functional", illuminating: "Illuminating" };
var CAL_TEACHER = { deep: "Know it", functional: "Use it", illuminating: "See it" };
var COUNT_TARGET = { deep: 0.25, functional: 0.5, illuminating: 0.25 };
var TIME_TARGET = { deep: 0.4, functional: 0.45, illuminating: 0.15 };
var TIME_MARK_MIN_COVERAGE = 0.5;
var COMPARE_MAX = 4;
var ORDER_STEP = 1024;
var COLLAPSE_CHIP_THRESHOLD = 100;     // Gate B Q1: stems start collapsed above this
var STRUCTURAL_SLICE_CODES = ["bridge", "grade_changed", "before_predecessor", "predecessor_unplaced"];
var DEMO_NOTICE_ORDERING = "Ordering warnings need the server";
var STALE_MESSAGE = "Someone else changed this sequence. Your change was not applied, and you are now seeing the latest.";

var NODE_ROWS = [
  { key: "stem", label: "Stem" }, { key: "concept_skill", label: "Concept/skill" },
  { key: "goal", label: "Goal" }, { key: "grades", label: "Grades" },
  { key: "state", label: "Kind in this grade" }, { key: "grade_raw", label: "Grade (as written)" },
  { key: "period_hint", label: "Period hint" }
];

/* ========================================================================
 * 3. pure logic
 * ===================================================================== */

function isArray(x) { return Object.prototype.toString.call(x) === "[object Array]"; }
function clone(x) { return x === undefined ? undefined : JSON.parse(JSON.stringify(x)); }
function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
function plainSort(a) { return a.slice().sort(); }          // default string sort, like Python sorted()
function uniq(a) { var seen = {}, out = []; a.forEach(function (x) { if (!has(seen, x)) { seen[x] = 1; out.push(x); } }); return out; }

/* ---- view state (contract 5.9) ---------------------------------------- */

/** The default ViewState. `slice` is accepted for symmetry (defaults do not depend on it;
 *  collapsed_stems: null means "use the chips > 100 rule"). */
function defaultState(slice) {
  return { hidden_stems: [], hidden_cs: [], collapsed_stems: null, stem_order: [],
    context: true, leaves: false, state_ext: true, unplaced_only: false,
    pairings: false, flagged: false, node: null, compare: [], sheet: false };
}

/** All stem ids and cs ids present in a slice (used to drop unknown ids). */
function sliceIds(slice) {
  var stems = {}, cs = {};
  if (slice && slice.super_stems) {
    slice.super_stems.forEach(function (ss) {
      ss.stems.forEach(function (st) {
        stems[st.stem_id] = true;
        st.concept_skills.forEach(function (c) { cs[c.cs_id] = true; });
      });
    });
  }
  return { stems: stems, cs: cs };
}

/** Which stems are collapsed right now: the explicit list, or the default rule. */
function effectiveCollapsed(state, slice) {
  var out = {};
  if (state.collapsed_stems === null || state.collapsed_stems === undefined) {
    if (slice && slice.counts && slice.counts.chips > COLLAPSE_CHIP_THRESHOLD) {
      Object.keys(sliceIds(slice).stems).forEach(function (s) { out[s] = true; });
    }
  } else {
    state.collapsed_stems.forEach(function (s) { out[s] = true; });
  }
  return out;
}

function encList(a) { return a.map(encodeURIComponent).join(","); }

/** ViewState -> query string (no leading "?", no "grade"; non-defaults only).
 *  Lists are comma-joined with each item encodeURIComponent'd. `slice` is
 *  unused for encoding but kept in the signature (contract 7.1). */
function encodeState(state, slice) {
  var d = defaultState(slice), q = [];
  function list(k, v) { if (v && v.length) q.push(k + "=" + encList(v)); }
  list("hs", state.hidden_stems);
  list("hc", state.hidden_cs);
  if (state.collapsed_stems !== null && state.collapsed_stems !== undefined) {
    q.push("cl=" + encList(state.collapsed_stems));          // present even when empty
  }
  list("so", state.stem_order);
  if (state.context !== d.context) q.push("ctx=" + (state.context ? 1 : 0));
  if (state.leaves !== d.leaves) q.push("lv=" + (state.leaves ? 1 : 0));
  if (state.state_ext !== d.state_ext) q.push("sx=" + (state.state_ext ? 1 : 0));
  if (state.unplaced_only) q.push("up=1");
  if (state.pairings) q.push("pr=1");
  if (state.flagged) q.push("fl=1");
  if (state.node) q.push("node=" + encodeURIComponent(state.node));
  list("compare", (state.compare || []).slice(0, COMPARE_MAX));
  if (state.sheet) q.push("sheet=1");
  return q.join("&");
}

/** "grade=2&..." prefix + encodeState. */
function encodeUrlQuery(grade, state, slice) {
  var rest = encodeState(state, slice);
  return "grade=" + encodeURIComponent(grade) + (rest ? "&" + rest : "");
}

/** Parse a query string into {key: rawValue} WITHOUT percent-decoding values,
 *  so list items can be split on "," first and decoded one by one. */
function parseRawQuery(query) {
  var out = {};
  String(query || "").replace(/^\?/, "").split("&").forEach(function (part) {
    if (!part) return;
    var i = part.indexOf("=");
    var k = i < 0 ? part : part.slice(0, i);
    out[k] = i < 0 ? "" : part.slice(i + 1);
  });
  return out;
}

function safeDecode(s) { try { return decodeURIComponent(s); } catch (e) { return s; } }
function decList(raw) { return raw === "" ? [] : raw.split(",").map(safeDecode).filter(function (x) { return x !== ""; }); }

/** The grade named in a query string, or null. */
function decodeGrade(query) {
  var q = parseRawQuery(query);
  return has(q, "grade") ? safeDecode(q.grade) : null;
}

/** Query string -> ViewState. Unknown stem / cs ids are dropped silently when
 *  a slice is given; compare is capped at 4. */
function decodeState(query, slice) {
  var q = parseRawQuery(query), s = defaultState(slice);
  var ids = slice ? sliceIds(slice) : null;
  function known(list, which) { return ids ? list.filter(function (x) { return ids[which][x]; }) : list; }
  if (has(q, "hs")) s.hidden_stems = known(decList(q.hs), "stems");
  if (has(q, "hc")) s.hidden_cs = known(decList(q.hc), "cs");
  if (has(q, "cl")) s.collapsed_stems = known(decList(q.cl), "stems");
  if (has(q, "so")) s.stem_order = known(decList(q.so), "stems");
  if (has(q, "ctx")) s.context = q.ctx !== "0";
  if (has(q, "lv")) s.leaves = q.lv === "1";
  if (has(q, "sx")) s.state_ext = q.sx !== "0";
  if (has(q, "up")) s.unplaced_only = q.up === "1";
  if (has(q, "pr")) s.pairings = q.pr === "1";
  if (has(q, "fl")) s.flagged = q.fl === "1";
  if (has(q, "node") && q.node !== "") s.node = safeDecode(q.node);
  if (has(q, "compare")) s.compare = uniq(decList(q.compare)).slice(0, COMPARE_MAX);
  if (has(q, "sheet")) s.sheet = q.sheet === "1";
  return s;
}

/** Saved views keep a lens, not a selection: ViewState minus node, compare, sheet. */
function lensOf(state) {
  var o = clone(state);
  delete o.node; delete o.compare; delete o.sheet;
  return o;
}

/** Apply a saved lens onto the current state (selection kept), dropping unknown ids. */
function applyLens(state, lens, slice) {
  var d = defaultState(slice), ids = sliceIds(slice), out = clone(state);
  function known(list, which) { return (list || []).filter(function (x) { return ids[which][x]; }); }
  out.hidden_stems = known(lens.hidden_stems, "stems");
  out.hidden_cs = known(lens.hidden_cs, "cs");
  out.collapsed_stems = (lens.collapsed_stems === null || lens.collapsed_stems === undefined) ? null : known(lens.collapsed_stems, "stems");
  out.stem_order = known(lens.stem_order, "stems");
  ["context", "leaves", "state_ext", "unplaced_only", "pairings", "flagged"].forEach(function (k) {
    out[k] = has(lens, k) ? !!lens[k] : d[k];
  });
  return out;
}

/** True when a lens differs from the defaults (used to light up "Reset view"). */
function lensIsDefault(state, slice) {
  return encodeState(Object.assign({}, state, { node: null, compare: [], sheet: false }), slice) === "";
}

/* ---- export: the ordered sequence as CSV (pure) ------------------------ */

function csvCell(v) {
  var t = v === null || v === undefined ? "" : String(v);
  if (/^[=+\-@]/.test(t)) t = "'" + t;                       // keep spreadsheets from reading text as a formula
  return /[",\n\r]/.test(t) ? '"' + t.replace(/"/g, '""') + '"' : t;
}

/** One row per placement, in module / slot / placement order. */
function sequenceToCsv(view) {
  var rows = [["module", "module_title", "slot", "slot_label", "node_id", "node_text", "stem", "calibration", "teacher_label", "periods", "note", "status"]];
  ((view && view.modules) || []).forEach(function (m) {
    m.slots.forEach(function (s) {
      s.placements.forEach(function (p) {
        rows.push([m.position, m.title, s.position, s.label, p.node_id || p.node_id_seen, p.node_text, p.stem_name,
          p.calibration ? CAL_LABEL[p.calibration] : "", p.calibration ? CAL_TEACHER[p.calibration] : "", p.period_estimate, p.differentiation_note, p.status]);
      });
    });
  });
  return rows.map(function (r) { return r.map(csvCell).join(","); }).join("\r\n") + "\r\n";
}

/* ---- small state reducers (return a NEW state; tested in node) --------- */

function toggleCompare(state, key) {
  var s = clone(state), i = s.compare.indexOf(key);
  if (i >= 0) s.compare.splice(i, 1);
  else if (s.compare.length < COMPARE_MAX) s.compare.push(key);
  if (!s.compare.length) s.sheet = false;
  return s;
}

function toggleInList(list, id) {
  var i = list.indexOf(id);
  return i >= 0 ? list.filter(function (x) { return x !== id; }) : list.concat([id]);
}

/** Toggle collapse of a stem; materialises the default rule into an explicit list first. */
function toggleCollapse(state, slice, stemId) {
  var s = clone(state), eff = effectiveCollapsed(state, slice);
  if (eff[stemId]) delete eff[stemId]; else eff[stemId] = true;
  s.collapsed_stems = Object.keys(eff);
  return s;
}

/** Stem ids of one super-stem in the order they are displayed. */
function stemIdsInDisplayOrder(slice, state, domain) {
  var out = [];
  visibleSliceAll(slice, state).forEach(function (ss) { if (ss.domain === domain) ss.stems.forEach(function (st) { out.push(st.stem_id); }); });
  return out;
}

/** Move a stem up (-1) or down (+1) within its super-stem by rewriting stem_order. */
function moveStem(state, slice, stemId, dir) {
  var s = clone(state), domain = null;
  slice.super_stems.forEach(function (ss) { ss.stems.forEach(function (st) { if (st.stem_id === stemId) domain = ss.domain; }); });
  if (domain === null) return s;
  var order = stemIdsInDisplayOrder(slice, state, domain), i = order.indexOf(stemId), j = i + dir;
  if (i < 0 || j < 0 || j >= order.length) return s;
  var t = order[i]; order[i] = order[j]; order[j] = t;
  s.stem_order = s.stem_order.filter(function (x) { return order.indexOf(x) < 0; }).concat(order);
  return s;
}

/* ---- guardrail mirror (contract 5.8, authoritative) --------------------- */

function roundHalfUp(x, dp) { var m = Math.pow(10, dp); return Math.floor(x * m + 0.5) / m; }

/** Counts by level and time by level for a list of {calibration, period_estimate}.
 *  Mirrors mh2/seq_guardrail.compute exactly. No pass/fail anywhere: these are
 *  diagnostics, never quotas. */
function computeGuardrail(placements) {
  var L = CALIBRATIONS, n = placements.length;
  var counts = { deep: 0, functional: 0, illuminating: 0, unset: 0 };
  var sums = { deep: 0, functional: 0, illuminating: 0, unset: 0 };
  var nTimed = 0, allSum = 0;
  placements.forEach(function (p) {
    var k = L.indexOf(p.calibration) >= 0 ? p.calibration : "unset";
    counts[k] += 1;
    if (p.period_estimate !== null && p.period_estimate !== undefined) {
      nTimed += 1; sums[k] += p.period_estimate; allSum += p.period_estimate;
    }
  });
  var nCal = n - counts.unset;
  var T = sums.deep + sums.functional + sums.illuminating;     // raw, unrounded
  var countShare = {}, timeShare = {}, periods = {};
  L.forEach(function (l) {
    countShare[l] = nCal > 0 ? roundHalfUp(counts[l] / nCal, 4) : null;
    timeShare[l] = T > 0 ? roundHalfUp(sums[l] / T, 4) : null;
    periods[l] = roundHalfUp(sums[l], 2);
  });
  periods.unset = roundHalfUp(sums.unset, 2);
  return {
    n: n, n_calibrated: nCal, n_timed: nTimed, counts: counts, count_share: countShare,
    periods: periods, total_periods: roundHalfUp(allSum, 2), time_share: timeShare,
    time_coverage: n > 0 ? roundHalfUp(nTimed / n, 4) : null,
    show_time_targets: n > 0 && (nTimed / n >= TIME_MARK_MIN_COVERAGE),
    count_target: clone(COUNT_TARGET), time_target: clone(TIME_TARGET),
    time_mark_min_coverage: TIME_MARK_MIN_COVERAGE
  };
}

/* ---- compare assembly (contract 5.5, mirrored from assemble_compare) ---- */

function cellIsEmpty(c) { return c === false || (isArray(c) && c.length === 0); }

function assembleCompare(columns, fieldDefs, grade) {
  var C = columns, rows = [];
  function nodeRow(key, label, fn) {
    rows.push({ key: key, label: label, group: "node", kind: "text", cells: C.map(fn), shared: false });
  }
  nodeRow("stem", "Stem", function (c) { return [c.stem_name]; });
  nodeRow("concept_skill", "Concept/skill", function (c) { return [c.cs_label_display]; });
  nodeRow("goal", "Goal", function (c) { return c.goal ? [c.goal] : []; });
  nodeRow("grades", "Grades", function (c) { return c.grades.slice(); });
  nodeRow("state", "Kind in this grade", function (c) { return [c.state]; });
  nodeRow("grade_raw", "Grade (as written)", function (c) { return c.grade_raw ? [c.grade_raw] : []; });
  nodeRow("period_hint", "Period hint", function (c) { return c.period_hint_text ? [c.period_hint_text] : []; });
  function flagRows(group, prefix, getter) {
    var all = [];
    C.forEach(function (c) { getter(c).forEach(function (x) { all.push(x); }); });
    plainSort(uniq(all)).forEach(function (code) {
      var cells = C.map(function (c) { return getter(c).indexOf(code) >= 0; });
      var n = 0; cells.forEach(function (b) { if (b) n += 1; });
      rows.push({ key: prefix + code, label: code, group: group, kind: "flag", cells: cells, shared: n >= 2 });
    });
  }
  flagRows("ccss", "ccss:", function (c) { return c.ccss || []; });
  flagRows("lesson", "lesson:", function (c) { return c.lessons || []; });
  fieldDefs.forEach(function (f) {
    rows.push({ key: f.key, label: f.label, group: "field", kind: "text", shared: false,
      cells: C.map(function (c) { return (c.fields && has(c.fields, f.key)) ? c.fields[f.key] : []; }) });
  });
  rows.push({ key: "state_codes", label: "State codes", group: "state", kind: "text", shared: false,
    cells: C.map(function (c) { return c.state_codes || []; }) });
  rows.forEach(function (r) { r.empty = r.cells.every(cellIsEmpty); });
  var sc = [], sl = [];
  rows.forEach(function (r) {
    if (r.shared && r.group === "ccss") sc.push(r.label);
    if (r.shared && r.group === "lesson") sl.push(r.label);
  });
  return { grade: grade, columns: C, field_defs: fieldDefs, rows: rows, shared_ccss: sc, shared_lessons: sl };
}

/** Display order for compare rows: drop empties and/or float shared rows to the top
 *  (node rows always first). Pure; used by the sheet renderer. */
function compareRowsView(compare, hideEmpty, sharedFirst) {
  var rows = compare.rows.filter(function (r) { return !(hideEmpty && r.empty); });
  if (sharedFirst) {
    var head = rows.filter(function (r) { return r.group === "node"; });
    var rest = rows.filter(function (r) { return r.group !== "node"; });
    rest = rest.filter(function (r) { return r.shared; }).concat(rest.filter(function (r) { return !r.shared; }));
    rows = head.concat(rest);
  }
  return rows;
}

/* ---- slice filtering (contract 7.1 visibleSlice) ------------------------- */

function stemOrderSort(stems, order) {
  if (!order || !order.length) return stems;
  var pos = {}; order.forEach(function (id, i) { pos[id] = i; });
  var listed = stems.filter(function (s) { return has(pos, s.stem_id); }).sort(function (a, b) { return pos[a.stem_id] - pos[b.stem_id]; });
  var rest = stems.filter(function (s) { return !has(pos, s.stem_id); });
  return listed.concat(rest);
}

/** Stems and strips after hidden_* and stem_order only (no chip filters). */
function visibleSliceAll(slice, state) {
  var hs = {}, out = [];
  (state.hidden_stems || []).forEach(function (x) { hs[x] = 1; });
  slice.super_stems.forEach(function (ss) {
    var stems = ss.stems.filter(function (st) { return !hs[st.stem_id]; });
    if (stems.length) out.push({ domain: ss.domain, stems: stemOrderSort(stems, state.stem_order) });
  });
  return out;
}

function chipHidden(n, state) {
  if (!state.context && (n.state === "off_grade" || n.state === "no_grade")) return true;
  if (!state.leaves && n.state === "leaf") return true;
  if (!state.state_ext && n.state === "state_extension") return true;
  return false;
}

function hasStructural(n, seqView) {
  var b = (n.badges || []).concat((seqView && seqView.slice_badges && seqView.slice_badges[n.source_key]) || []);
  return b.some(function (x) { return x.tier === "structural"; });
}

/** Apply show/hide only. Returns {super_stems, counts:{shown_chips, shown_cs}}.
 *  Filters act at strip level: a kept strip shows every chip not hidden by the
 *  context / leaves / state-ext toggles, so the progression stays readable. */
function visibleSlice(slice, state, seqView) {
  var hc = {}, shownChips = 0, shownCs = 0, out = [];
  var placed = (seqView && seqView.placed_index) || {};
  (state.hidden_cs || []).forEach(function (x) { hc[x] = 1; });
  visibleSliceAll(slice, state).forEach(function (ss) {
    var stems = [];
    ss.stems.forEach(function (st) {
      var cses = [];
      st.concept_skills.forEach(function (c) {
        if (hc[c.cs_id]) return;
        if (!state.leaves && c.in_grade_count_excl_leaf === 0) return;
        if (state.unplaced_only && !c.nodes.some(function (n) { return n.owed && !has(placed, n.source_key); })) return;
        if (state.flagged && !c.nodes.some(function (n) { return hasStructural(n, seqView); })) return;
        var nodes = c.nodes.filter(function (n) { return !chipHidden(n, state); });
        if (!nodes.length) return;
        var cc = Object.assign({}, c, { nodes: nodes });
        cses.push(cc); shownCs += 1; shownChips += nodes.length;
      });
      if (cses.length) stems.push(Object.assign({}, st, { concept_skills: cses }));
    });
    if (stems.length) out.push({ domain: ss.domain, stems: stems });
  });
  return { super_stems: out, counts: { shown_chips: shownChips, shown_cs: shownCs } };
}

/** source_key -> SliceNode, over the whole slice (incl. hidden chips). */
function indexSlice(slice) {
  var idx = {};
  slice.super_stems.forEach(function (ss) { ss.stems.forEach(function (st) { st.concept_skills.forEach(function (c) {
    c.nodes.forEach(function (n) { idx[n.source_key] = Object.assign({ stem_id: st.stem_id, stem_name: st.stem_name, cs_id: c.cs_id, cs_label: c.label_display }, n); });
  }); }); });
  return idx;
}

/* ---- period hint text helpers ---------------------------------------- */

/** Why a hint has no "Use n" button, in words (contract 7.3). "" when none needed. */
function hintReason(h) {
  if (!h) return "";
  if (h.unit && h.unit !== "period") return "in " + h.unit + "s; not converted";
  if (h.basis === "ungraded_multi_grade") return "estimate covers several grades";
  if (h.qualifier === "part_of") return "\u2264 " + fmtNum(h.high !== null && h.high !== undefined ? h.high : h.low);
  return "";
}

function fmtNum(x) {
  if (x === null || x === undefined) return "";
  return String(Math.round(x * 1000) / 1000);
}

/* ---- order-key arithmetic (data model section 4; mirrored by DemoApi) ---- */

/** New key at the end of a container. */
function orderAppend(keys) { return keys.length ? Math.max.apply(null, keys) + ORDER_STEP : ORDER_STEP; }

/** Key strictly between lo (or start when null) and hi (or end when null);
 *  null when the gap is under 2, meaning "renumber first". */
function orderBetween(lo, hi) {
  var a = (lo === null || lo === undefined) ? 0 : lo;
  if (hi === null || hi === undefined) return a + ORDER_STEP;
  if (hi - a < 2) return null;
  return Math.floor((a + hi) / 2);
}

/** Assign 1024*i to items (already in order), in place. */
function renumber(items) { items.forEach(function (it, i) { it.order_key = ORDER_STEP * (i + 1); }); return items; }

function byOrder(a, b) { return a.order_key - b.order_key; }

/* ---- display helpers shared by renderers ---------------------------- */

function nowIso() { return new Date().toISOString(); }
function gshort(g) { return GRADE_SHORT[g] || g; }
function pct(x) { return x === null || x === undefined ? "\u2013" : String(Math.round(x * 1000) / 10) + "%"; }
function plural(n, one, many) { return n + " " + (n === 1 ? one : (many || one + "s")); }
function firstLine(s) { return String(s || "").split("\n")[0]; }

/** "Also placed in G1 · M3 Place value; G3 · M1 ..." from PlacedRef[]. */
function placedElsewhereText(refs) {
  return (refs || []).map(function (r) { return gshort(r.grade) + " \u00b7 " + r.module_title; }).join("; ");
}

/* ========================================================================
 * 4. API adapters
 *
 * HttpApi and DemoApi expose the same async methods and throw the same
 * ApiError{status, error, message, detail}.  Every write resolves to
 * {sequence: SequenceView, result: {...}} (contract section 4).
 * ===================================================================== */

class ApiError extends Error {
  constructor(status, error, message, detail) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.error = error;
    this.message = message;
    this.detail = detail || { error: error, message: message };
  }
}

function safeStorage() {
  try { if (typeof localStorage !== "undefined" && localStorage) return localStorage; } catch (e) { /* blocked */ }
  return null;
}

/* ---- HttpApi ------------------------------------------------------------ */

/** Talks to /api/seq/*.  `base` is a URL or string ending in "/api/seq/". */
class HttpApi {
  constructor(base, fetchFn) {
    var b = String(base);
    this.base = b.charAt(b.length - 1) === "/" ? b : b + "/";
    this.fetchFn = fetchFn || (typeof fetch !== "undefined" ? fetch.bind(globalThis) : null);
    this.isDemo = false;
  }

  /** Build "<base><path>?k=v..." ; scalar values and list items use encodeURIComponent,
   *  list values are comma-joined (compare keys). undefined / null are skipped. */
  url(path, query) {
    var parts = [];
    if (query) {
      Object.keys(query).forEach(function (k) {
        var v = query[k];
        if (v === null || v === undefined) return;
        parts.push(k + "=" + (isArray(v) ? v.map(encodeURIComponent).join(",") : encodeURIComponent(v)));
      });
    }
    return this.base + path + (parts.length ? "?" + parts.join("&") : "");
  }

  async req(method, path, query, body) {
    var headers = { "Accept": "application/json" };
    try {
      var u = typeof localStorage !== "undefined" && localStorage ? localStorage.getItem("mh2seq-user") : null;
      if (u) headers["X-MH2-User"] = u;
    } catch (e) { /* storage blocked: anonymous */ }
    var init = { method: method, headers: headers, credentials: "same-origin" };
    if (body !== undefined) { headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    var res;
    try { res = await this.fetchFn(this.url(path, query), init); }
    catch (e) { throw new ApiError(0, "network", "Could not reach the server. Check your connection and try again."); }
    var data = null, text = "";
    try { text = await res.text(); data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
    if (!res.ok) {
      var d = data && data.detail !== undefined ? data.detail : null;
      if (isArray(d) || d === null || typeof d !== "object") {
        var msg = res.status === 401 ? "Sign-in required" : (isArray(d) ? "Invalid request" : (typeof d === "string" ? d : "Request failed (" + res.status + ")"));
        var code = isArray(d) ? "invalid" : (res.status === 401 ? "unauthorized" : "http_" + res.status);
        throw new ApiError(res.status, code, msg, { error: code, message: msg });
      }
      throw new ApiError(res.status, d.error || "error", d.message || "Request failed", d);
    }
    return data;
  }

  whoami() { return this.req("GET", "whoami"); }
  grades() { return this.req("GET", "grades"); }
  slice(grade) { return this.req("GET", "slice", { grade: grade }); }
  node(key, grade) { return this.req("GET", "node/" + encodeURIComponent(key), { grade: grade }); }
  compare(keys, grade) { return this.req("GET", "compare", { grade: grade, keys: keys }); }
  sequence(grade) { return this.req("GET", "sequence", { grade: grade }); }
  guardrail(grade) { return this.req("GET", "guardrail", { grade: grade }); }
  attention(grade) { return this.req("GET", "attention", { grade: grade }); }
  events(seqId, limit, before) { return this.req("GET", "sequences/" + seqId + "/events", { limit: limit, before: before }); }
  createSequence(grade, title) { return this.req("POST", "sequences", null, { grade: grade, title: title }); }
  updateSequence(id, rev, fields) { return this.req("PATCH", "sequences/" + id, null, Object.assign({ expected_rev: rev }, fields)); }
  createModule(seqId, rev, title) { return this.req("POST", "sequences/" + seqId + "/modules", null, { expected_rev: rev, title: title }); }
  updateModule(id, rev, fields) { return this.req("PATCH", "modules/" + id, null, Object.assign({ expected_rev: rev }, fields)); }
  moveModule(id, rev, dir) { return this.req("POST", "modules/" + id + "/move", null, { expected_rev: rev, direction: dir }); }
  removeModule(id, rev) { return this.req("DELETE", "modules/" + id, { expected_rev: rev }); }
  updateSlot(id, rev, label) { return this.req("PATCH", "slots/" + id, null, { expected_rev: rev, label: label }); }
  moveSlot(id, rev, opts) { return this.req("POST", "slots/" + id + "/move", null, Object.assign({ expected_rev: rev }, opts)); }
  mergeSlot(id, rev, intoId) { return this.req("POST", "slots/" + id + "/merge", null, { expected_rev: rev, into_slot_id: intoId }); }
  createSlotGroup(seqId, rev, moduleId, keys, confirm, notes) {
    return this.req("POST", "slots", null, { expected_rev: rev, sequence_id: seqId, module_id: moduleId,
      source_keys: keys, confirm_off_grade: !!confirm, differentiation_notes: notes || {} });
  }
  place(seqId, rev, moduleId, key, opts) {
    opts = opts || {};
    var body = { expected_rev: rev, sequence_id: seqId, module_id: moduleId, source_key: key,
      confirm_off_grade: !!opts.confirm_off_grade };
    if (opts.slot_id) body.slot_id = opts.slot_id;
    if (opts.after_slot_id) body.after_slot_id = opts.after_slot_id;
    if (opts.differentiation_note) body.differentiation_note = opts.differentiation_note;
    return this.req("POST", "placements", null, body);
  }
  updatePlacement(id, prev, changes) { return this.req("PATCH", "placements/" + id, null, Object.assign({ expected_rev: prev }, changes)); }
  coPlace(id, rev, slotId) { return this.req("POST", "placements/" + id + "/co-place", null, { expected_rev: rev, target_slot_id: slotId }); }
  ungroup(id, rev) { return this.req("POST", "placements/" + id + "/ungroup", null, { expected_rev: rev }); }
  removePlacement(id, rev, reason) { return this.req("DELETE", "placements/" + id, { expected_rev: rev, reason: reason || null }); }
  reattach(id, rev, key) { return this.req("POST", "placements/" + id + "/reattach", null, { expected_rev: rev, source_key: key }); }
  acknowledge(id, rev) { return this.req("POST", "placements/" + id + "/acknowledge", null, { expected_rev: rev }); }
  listViews(grade) { return this.req("GET", "views", { grade: grade }); }
  createView(grade, name, state) { return this.req("POST", "views", null, { grade: grade, name: name, state: state }); }
  updateView(id, fields) { return this.req("PUT", "views/" + id, null, fields); }
  deleteView(id) { return this.req("DELETE", "views/" + id); }
}

/* ---- DemoApi ------------------------------------------------------------ */

/** Offline API over the embedded payload.  Python is authoritative: this
 *  mirrors the server's write rules (contract 3.5 / 6.2) and degrades where it
 *  cannot (no ordering warnings, no successor suggestions, status stays "ok"
 *  except for placements seeded from a server sequence). */
class DemoApi {
  constructor(payload, storage) {
    this.isDemo = true;
    this.payload = payload;
    this.grade = payload.grade;
    this.storage = storage || null;
    this.storageKey = "mh2seq-demo:" + payload.grade + ":" + payload.generated_at;
    this.index = indexSlice(payload.slice);
    this.persisted = this._probe();
    this.st = this._loadStored() || this._seed();
  }

  /* -- storage (every access wrapped; in-memory only when it fails) -- */
  _probe() {
    if (!this.storage) return false;
    try { this.storage.setItem(this.storageKey + ":probe", "1"); this.storage.removeItem(this.storageKey + ":probe"); return true; }
    catch (e) { return false; }
  }
  _loadStored() {
    if (!this.storage) return null;
    try {
      var raw = this.storage.getItem(this.storageKey);
      var st = raw ? JSON.parse(raw) : null;
      return st && st.next && st.modules ? st : null;
    } catch (e) { return null; }
  }
  _save() {
    if (!this.storage) { this.persisted = false; return; }
    try { this.storage.setItem(this.storageKey, JSON.stringify(this.st)); this.persisted = true; }
    catch (e) { this.persisted = false; }
  }
  /** Drop stored changes and go back to the seed ("Reset demo"). */
  reset() {
    if (this.storage) { try { this.storage.removeItem(this.storageKey); } catch (e) { /* ignore */ } }
    this.persisted = this._probe();
    this.st = this._seed();
  }

  _seed() {
    var st = { sequence: null, modules: [], slots: [], placements: [], views: [], events: [],
      next: { sequence: 1, module: 1, slot: 1, placement: 1, view: 1, event: 1 } };
    var v = this.payload.sequence;
    if (v && v.sequence) {
      st.sequence = Object.assign({ archived: false }, clone(v.sequence));
      st.next.sequence = st.sequence.sequence_id + 1;
      v.modules.forEach(function (m) {
        st.modules.push({ module_id: m.module_id, title: m.title, note: m.note, order_key: m.order_key, removed: false });
        st.next.module = Math.max(st.next.module, m.module_id + 1);
        m.slots.forEach(function (s) {
          st.slots.push({ slot_id: s.slot_id, module_id: m.module_id, label: s.label, order_key: s.order_key, removed: false });
          st.next.slot = Math.max(st.next.slot, s.slot_id + 1);
          s.placements.forEach(function (p) {
            var rec = clone(p); rec.slot_id = s.slot_id; rec.removed = false;
            st.placements.push(rec);
            st.next.placement = Math.max(st.next.placement, p.placement_id + 1);
          });
        });
      });
    }
    return st;
  }

  /* -- errors -- */
  _err(status, error, message, extra) {
    return new ApiError(status, error, message, Object.assign({ error: error, message: message }, extra || {}));
  }
  _notFound(what) { return this._err(404, "not_found", what + " not found"); }
  _invalid(msg) { return this._err(422, "invalid", msg); }
  _stale(current) {
    return this._err(409, "stale_revision", "The sequence changed since you loaded it", { current_rev: current, sequence: this._view() });
  }

  /* -- lookups (existence first, as in the store) -- */
  _seqOf(id) {
    var s = this.st.sequence;
    if (!s || s.archived || s.sequence_id !== id) throw this._notFound("Sequence");
    return s;
  }
  _module(id) {
    var m = this.st.modules.filter(function (x) { return x.module_id === id && !x.removed; })[0];
    if (!m) throw this._notFound("Module");
    return m;
  }
  _slot(id) {
    var s = this.st.slots.filter(function (x) { return x.slot_id === id && !x.removed; })[0];
    if (!s) throw this._notFound("Slot");
    return s;
  }
  _placement(id) {
    var p = this.st.placements.filter(function (x) { return x.placement_id === id && !x.removed; })[0];
    if (!p) throw this._notFound("Placement");
    return p;
  }
  _checkRev(rev) { if (this.st.sequence.rev !== rev) throw this._stale(this.st.sequence.rev); }
  _activeModules() { return this.st.modules.filter(function (m) { return !m.removed; }).sort(byOrder); }
  _slotsOf(moduleId) { return this.st.slots.filter(function (s) { return !s.removed && s.module_id === moduleId; }).sort(byOrder); }
  _placementsOf(slotId) {
    return this.st.placements.filter(function (p) { return !p.removed && p.slot_id === slotId; })
      .sort(function (a, b) { return a.order_in_slot - b.order_in_slot; });
  }
  _activeKeyPlacement(key, exceptId) {
    return this.st.placements.filter(function (p) { return !p.removed && p.source_key === key && p.placement_id !== exceptId; })[0] || null;
  }
  _nextId(kind) { var n = this.st.next[kind]; this.st.next[kind] = n + 1; return n; }

  /* -- events and commit -- */
  _log(action, ids) {
    ids = ids || {};
    this.st.events.push({ event_id: this._nextId("event"), action: action, actor: "demo", at: nowIso(),
      module_id: ids.module_id || null, slot_id: ids.slot_id || null, placement_id: ids.placement_id || null,
      before: null, after: null });
  }
  /** Bump the sequence rev, log, persist, and return the WriteResult. */
  _commit(events, result) {
    var s = this.st.sequence;
    s.rev += 1;
    events.forEach(function (e) { this._log(e.action, e); }, this);
    this._save();
    return { sequence: this._view(), result: result || {} };
  }

  /* -- container ordering helpers -- */
  /** Key for a new slot placed between lo and hi inside a module; renumbers the module if it must. */
  _slotKeyBetween(moduleId, loKey, hiKey) {
    var k = orderBetween(loKey, hiKey);
    if (k === null) {
      renumber(this._slotsOf(moduleId));
      this._log("renumber", { module_id: moduleId });
      return null;           // caller recomputes from the fresh keys
    }
    return k;
  }

  /* ---------------------------------------------------------------- reads */
  async whoami() { return clone(this.payload.whoami); }
  async grades() {
    var g = clone(this.payload.grades), v = this._view();
    g.grades.forEach(function (gi) {
      if (gi.grade === this.grade) {
        gi.sequence = v.sequence ? { sequence_id: v.sequence.sequence_id, grade: v.sequence.grade, title: v.sequence.title,
          owner: v.sequence.owner, rev: v.sequence.rev, n_modules: v.modules.length,
          n_placements: v.guardrail.n, updated_at: this.st.sequence.updated_at || v.sequence.created_at } : null;
      }
    }, this);
    return g;
  }
  async slice(grade) {
    if (grade !== this.grade) throw this._notFound("Demo contains " + (GRADE_LABELS[this.grade] || this.grade) + " only; grade");
    return this.payload.slice;
  }
  async node(key, grade) {
    var d = this.payload.drawers[key];
    if (!d) throw this._notFound("Node");
    return d;
  }
  async compare(keys, grade) {
    if (!keys.length || keys.length > COMPARE_MAX || uniq(keys).length !== keys.length) throw this._invalid("Compare needs 1 to 4 distinct nodes");
    var cols = [];
    for (var i = 0; i < keys.length; i++) {
      var c = this.payload.compare_columns[keys[i]];
      if (!c) throw this._notFound("Node");
      cols.push(c);
    }
    return assembleCompare(cols, this.payload.field_defs, this.grade);
  }
  async sequence(grade) { return this._view(); }
  async guardrail(grade) {
    var v = this._view();
    return { sequence_id: v.sequence ? v.sequence.sequence_id : null, sequence: v.guardrail,
      modules: v.modules.map(function (m) { return { module_id: m.module_id, title: m.title, guardrail: m.guardrail }; }) };
  }
  async attention(grade) {
    var v = this._view();
    return { sequence_id: v.sequence ? v.sequence.sequence_id : null, items: v.attention };
  }
  async events(seqId, limit, before) {
    var all = this.st.events.slice().sort(function (a, b) { return b.event_id - a.event_id; });
    if (before) all = all.filter(function (e) { return e.event_id < before; });
    limit = limit || 50;
    var page = all.slice(0, limit);
    return { events: clone(page), next_before: all.length > limit ? page[page.length - 1].event_id : null };
  }

  /* --------------------------------------------------------------- views */
  _viewRow(v) { return { view_id: v.view_id, grade: v.grade, name: v.name, state: clone(v.state), created_at: v.created_at, updated_at: v.updated_at }; }
  async listViews(grade) {
    return { views: this.st.views.filter(function (v) { return !grade || v.grade === grade; }).map(this._viewRow, this) };
  }
  async createView(grade, name, state) {
    name = String(name || "").trim();
    if (!name) throw this._invalid("A view needs a name");
    if (this.st.views.some(function (v) { return v.grade === grade && v.name === name; })) throw this._err(409, "view_name_exists", "A view with that name exists");
    var now = nowIso();
    var v = { view_id: this._nextId("view"), grade: grade, name: name, state: clone(state || {}), created_at: now, updated_at: now };
    this.st.views.push(v); this._save();
    return this._viewRow(v);
  }
  async updateView(id, fields) {
    var v = this.st.views.filter(function (x) { return x.view_id === id; })[0];
    if (!v) throw this._notFound("View");
    if (fields && has(fields, "name")) {
      var nm = String(fields.name || "").trim();
      if (!nm) throw this._invalid("A view needs a name");
      if (this.st.views.some(function (x) { return x.view_id !== id && x.grade === v.grade && x.name === nm; })) throw this._err(409, "view_name_exists", "A view with that name exists");
      v.name = nm;
    }
    if (fields && has(fields, "state")) v.state = clone(fields.state);
    v.updated_at = nowIso(); this._save();
    return this._viewRow(v);
  }
  async deleteView(id) {
    var i = this.st.views.map(function (x) { return x.view_id; }).indexOf(id);
    if (i < 0) throw this._notFound("View");
    this.st.views.splice(i, 1); this._save();
    return { deleted: id };
  }

  /* ---------------------------------------------------------- sequences */
  async createSequence(grade, title) {
    if (grade !== this.grade) throw this._notFound("Demo contains " + (GRADE_LABELS[this.grade] || this.grade) + " only; grade");
    var cur = this.st.sequence;
    if (cur && !cur.archived) throw this._err(409, "sequence_exists", "This grade already has a sequence", { sequence_id: cur.sequence_id });
    title = String(title || "").trim();
    if (!title) throw this._invalid("A sequence needs a title");
    var now = nowIso(), id = this._nextId("sequence");
    this.st.sequence = { sequence_id: id, grade: grade, title: title, owner: "demo", note: null, rev: 1,
      created_by: "demo", created_at: now, updated_at: now, archived: false };
    this._log("sequence_create", {});
    this._save();
    return { sequence: this._view(), result: { sequence_id: id } };
  }
  async updateSequence(id, rev, fields) {
    var s = this._seqOf(id); this._checkRev(rev);
    if (has(fields, "title") && !String(fields.title || "").trim()) throw this._invalid("A sequence needs a title");
    if (has(fields, "title")) s.title = String(fields.title).trim();
    if (has(fields, "owner")) s.owner = fields.owner;
    if (has(fields, "note")) s.note = fields.note;
    var archived = has(fields, "archived") && !!fields.archived;
    if (archived) s.archived = true;
    s.updated_at = nowIso();
    return this._commit([{ action: archived ? "sequence_archive" : "sequence_update" }], {});
  }

  /* ------------------------------------------------------------ modules */
  async createModule(seqId, rev, title) {
    this._seqOf(seqId); this._checkRev(rev);
    title = String(title || "").trim();
    if (!title) throw this._invalid("A module needs a title");
    var id = this._nextId("module");
    this.st.modules.push({ module_id: id, title: title, note: null, order_key: orderAppend(this._activeModules().map(function (m) { return m.order_key; })), removed: false });
    return this._commit([{ action: "module_create", module_id: id }], { module_id: id });
  }
  async updateModule(id, rev, fields) {
    var m = this._module(id); this._checkRev(rev);
    if (has(fields, "title") && !String(fields.title || "").trim()) throw this._invalid("A module needs a title");
    if (has(fields, "title")) m.title = String(fields.title).trim();
    if (has(fields, "note")) m.note = fields.note;
    return this._commit([{ action: "module_update", module_id: id }], {});
  }
  async moveModule(id, rev, dir) {
    var m = this._module(id); this._checkRev(rev);
    if (dir !== "up" && dir !== "down") throw this._invalid("direction must be up or down");
    var mods = this._activeModules(), i = mods.indexOf(m), j = dir === "up" ? i - 1 : i + 1;
    if (j < 0 || j >= mods.length) throw this._err(422, "at_edge", "Already at the " + (dir === "up" ? "top" : "bottom"));
    var t = m.order_key; m.order_key = mods[j].order_key; mods[j].order_key = t;
    return this._commit([{ action: "module_move", module_id: id }], {});
  }
  async removeModule(id, rev) {
    var m = this._module(id); this._checkRev(rev);
    if (this._slotsOf(id).length) throw this._err(409, "module_not_empty", "Move or remove this module's placements first");
    m.removed = true;
    return this._commit([{ action: "module_remove", module_id: id }], {});
  }

  /* -------------------------------------------------------------- slots */
  async updateSlot(id, rev, label) {
    var s = this._slot(id); this._checkRev(rev);
    s.label = (label === null || label === undefined || String(label).trim() === "") ? null : String(label).trim();
    return this._commit([{ action: "slot_update", slot_id: id, module_id: s.module_id }], {});
  }
  async moveSlot(id, rev, opts) {
    var s = this._slot(id); this._checkRev(rev);
    var hasDir = opts && opts.direction, hasTo = opts && opts.to_module_id !== undefined && opts.to_module_id !== null;
    if ((hasDir && hasTo) || (!hasDir && !hasTo)) throw this._invalid("Give exactly one of direction or to_module_id");
    if (hasTo) {
      var target = this._module(opts.to_module_id);
      s.module_id = target.module_id;
      s.order_key = orderAppend(this._slotsOf(target.module_id).filter(function (x) { return x !== s; }).map(function (x) { return x.order_key; }));
      return this._commit([{ action: "slot_move", slot_id: id, module_id: target.module_id }], {});
    }
    var dir = opts.direction;
    if (dir !== "up" && dir !== "down") throw this._invalid("direction must be up or down");
    var slots = this._slotsOf(s.module_id), i = slots.indexOf(s), j = dir === "up" ? i - 1 : i + 1;
    if (j >= 0 && j < slots.length) {
      var t = s.order_key; s.order_key = slots[j].order_key; slots[j].order_key = t;
    } else {
      // module boundary: cross into the neighbouring module (contract 1.2)
      var mods = this._activeModules(), mi = mods.map(function (m) { return m.module_id; }).indexOf(s.module_id);
      var nm = mods[dir === "up" ? mi - 1 : mi + 1];
      if (!nm) throw this._err(422, "at_edge", "Already at the " + (dir === "up" ? "start" : "end") + " of the sequence");
      var there = this._slotsOf(nm.module_id);
      s.module_id = nm.module_id;
      if (dir === "up") s.order_key = orderAppend(there.map(function (x) { return x.order_key; }));
      else {
        var k = there.length ? orderBetween(null, there[0].order_key) : ORDER_STEP;
        if (k === null) { renumber(there); this._log("renumber", { module_id: nm.module_id }); k = orderBetween(null, there[0].order_key); }
        s.order_key = k;
      }
    }
    return this._commit([{ action: "slot_move", slot_id: id, module_id: s.module_id }], {});
  }
  async mergeSlot(id, rev, intoId) {
    var s = this._slot(id), into = this._slot(intoId); this._checkRev(rev);
    if (s === into) throw this._invalid("Cannot merge a slot into itself");
    var target = this._placementsOf(into.slot_id), moving = this._placementsOf(s.slot_id);
    var next = orderAppend(target.map(function (p) { return p.order_in_slot; }));
    moving.forEach(function (p, i) { p.slot_id = into.slot_id; p.order_in_slot = next + i * ORDER_STEP; p.rev += 1; });
    s.removed = true;
    return this._commit([{ action: "slot_merge", slot_id: intoId, module_id: into.module_id }], { slot_id: intoId });
  }

  /* --------------------------------------------------------- placements */
  _snap(key) {
    var info = this.index[key];
    if (!info) throw this._notFound("Node");
    return info;
  }
  _newPlacement(key, slotId, note, orderInSlot) {
    var info = this._snap(key), dr = this.payload.drawers[key], now = nowIso();
    return { placement_id: this._nextId("placement"), rev: 1, slot_id: slotId, order_in_slot: orderInSlot,
      source_key: key, node_id: info.node_id, node_id_seen: info.node_id, node_text: info.node_text,
      node_text_seen: info.node_text, stem_id: info.stem_id, stem_name: info.stem_name,
      concept_skill_display: dr ? dr.cs.label_display : info.cs_label, ladder_file_seen: dr ? dr.source_file : null,
      grade_kind_seen: info.state, state_now: info.state, status: "ok", relabelled: false, relabel: null,
      is_bridge: !!info.requires_confirm, calibration: null, period_estimate: null, period_hint: info.period_hint || null,
      period_hint_seen: null, differentiation_note: note || null, placed_by: "demo", placed_at: now,
      updated_by: "demo", updated_at: now, placed_elsewhere: clone(info.placed_elsewhere || []), badges: [], removed: false };
  }
  _needConfirm(keys) {
    var states = {}, list = [];
    keys.forEach(function (k) { var n = this._snap(k); if (n.requires_confirm) { list.push(k); states[k] = n.state; } }, this);
    return list.length ? { source_keys: list, states: states } : null;
  }
  _confirmError(nc) {
    return this._err(422, "confirm_off_grade_required", "Some nodes are outside this grade's inventory; confirm to place them as bridges", nc);
  }
  _dropIfEmpty(slotId) {
    if (!this._placementsOf(slotId).length) { var s = this.st.slots.filter(function (x) { return x.slot_id === slotId; })[0]; if (s) s.removed = true; }
  }

  async createSlotGroup(seqId, rev, moduleId, keys, confirm, notes) {
    this._seqOf(seqId); var mod = this._module(moduleId);
    if (!keys || !keys.length || keys.length > COMPARE_MAX) throw this._invalid("A group needs 1 to 4 nodes");
    keys.forEach(function (k) { this._snap(k); }, this);
    this._checkRev(rev);
    var nc = this._needConfirm(keys);
    if (nc && !confirm) throw this._confirmError(nc);
    var skipped = [], todo = [];
    keys.forEach(function (k) {
      var ex = this._activeKeyPlacement(k, null);
      if (ex) skipped.push({ source_key: k, reason: "already_placed", placement_id: ex.placement_id }); else todo.push(k);
    }, this);
    if (!todo.length) return this._commit([], { slot_id: null, placement_ids: [], skipped: skipped });
    var slotId = this._nextId("slot");
    this.st.slots.push({ slot_id: slotId, module_id: moduleId, label: null, removed: false,
      order_key: orderAppend(this._slotsOf(moduleId).map(function (x) { return x.order_key; })) });
    var ids = [], events = [];
    todo.forEach(function (k, i) {
      var p = this._newPlacement(k, slotId, notes && notes[k], ORDER_STEP * (i + 1));
      this.st.placements.push(p); ids.push(p.placement_id);
      events.push({ action: "place", placement_id: p.placement_id, slot_id: slotId, module_id: moduleId });
    }, this);
    return this._commit(events, { slot_id: slotId, placement_ids: ids, skipped: skipped });
  }

  async place(seqId, rev, moduleId, key, opts) {
    opts = opts || {};
    this._seqOf(seqId); var mod = this._module(moduleId); this._snap(key);
    var slot = opts.slot_id ? this._slot(opts.slot_id) : null;
    var after = opts.after_slot_id ? this._slot(opts.after_slot_id) : null;
    this._checkRev(rev);
    var ex = this._activeKeyPlacement(key, null);
    if (ex) throw this._err(409, "already_placed", "That node is already placed in this sequence", { placement_id: ex.placement_id });
    var nc = this._needConfirm([key]);
    if (nc && !opts.confirm_off_grade) throw this._confirmError(nc);
    var slotId;
    if (slot) {
      slotId = slot.slot_id;
    } else {
      var slots = this._slotsOf(moduleId), ok;
      if (after && after.module_id === moduleId) {
        var ai = slots.indexOf(after), nxt = slots[ai + 1] ? slots[ai + 1].order_key : null;
        ok = orderBetween(after.order_key, nxt);
        if (ok === null) { renumber(slots); this._log("renumber", { module_id: moduleId }); ok = orderBetween(after.order_key, nxt === null ? null : slots[ai + 1].order_key); }
      } else {
        ok = orderAppend(slots.map(function (x) { return x.order_key; }));
      }
      slotId = this._nextId("slot");
      this.st.slots.push({ slot_id: slotId, module_id: moduleId, label: null, order_key: ok, removed: false });
    }
    var inSlot = this._placementsOf(slotId);
    var p = this._newPlacement(key, slotId, opts.differentiation_note, orderAppend(inSlot.map(function (x) { return x.order_in_slot; })));
    this.st.placements.push(p);
    return this._commit([{ action: "place", placement_id: p.placement_id, slot_id: slotId, module_id: moduleId }],
      { placement_id: p.placement_id, slot_id: slotId, placed_elsewhere: clone(p.placed_elsewhere) });
  }

  async updatePlacement(id, prev, changes) {
    var p = this._placement(id);
    if (p.rev !== prev) throw this._stale(p.rev);
    var events = [], c = changes || {};
    if (has(c, "calibration") && c.calibration !== null && CALIBRATIONS.indexOf(c.calibration) < 0) throw this._invalid("Unknown calibration");
    if (has(c, "period_estimate") && c.period_estimate !== null) {
      var num = Number(c.period_estimate);
      if (isNaN(num) || num < 0 || num > 200) throw this._invalid("Periods must be between 0 and 200");
      c = Object.assign({}, c, { period_estimate: num });
    }
    if (has(c, "calibration")) { p.calibration = c.calibration; events.push("set_calibration"); }
    if (has(c, "period_estimate")) {
      p.period_estimate = c.period_estimate;
      p.period_hint_seen = p.period_hint ? p.period_hint.text : null;
      events.push("set_period");
    }
    if (has(c, "differentiation_note")) {
      p.differentiation_note = c.differentiation_note ? String(c.differentiation_note) : null;
      events.push("set_note");
    }
    if (events.length) {
      p.rev += 1; p.updated_by = "demo"; p.updated_at = nowIso();
      var slot = this.st.slots.filter(function (s) { return s.slot_id === p.slot_id; })[0];
      events.forEach(function (a) { this._log(a, { placement_id: id, slot_id: p.slot_id, module_id: slot ? slot.module_id : null }); }, this);
      this._save();
    }
    return { sequence: this._view(), result: { placement_id: id } };   // the sequence rev is NOT bumped
  }

  async coPlace(id, rev, targetSlotId) {
    var p = this._placement(id), target = this._slot(targetSlotId); this._checkRev(rev);
    if (p.slot_id === targetSlotId) throw this._invalid("Already in that slot");
    var old = p.slot_id;
    p.slot_id = targetSlotId;
    p.order_in_slot = orderAppend(this._placementsOf(targetSlotId).filter(function (x) { return x !== p; }).map(function (x) { return x.order_in_slot; }));
    p.rev += 1;
    this._dropIfEmpty(old);
    return this._commit([{ action: "co_place", placement_id: id, slot_id: targetSlotId, module_id: target.module_id }], { slot_id: targetSlotId });
  }

  async ungroup(id, rev) {
    var p = this._placement(id); this._checkRev(rev);
    var cur = this._slot(p.slot_id);
    if (this._placementsOf(cur.slot_id).length < 2) throw this._err(422, "already_alone", "This placement is already in a slot of its own");
    var slots = this._slotsOf(cur.module_id), i = slots.indexOf(cur), nxt = slots[i + 1] ? slots[i + 1].order_key : null;
    var k = orderBetween(cur.order_key, nxt);
    if (k === null) { renumber(slots); this._log("renumber", { module_id: cur.module_id }); k = orderBetween(cur.order_key, slots[i + 1].order_key); }
    var slotId = this._nextId("slot");
    this.st.slots.push({ slot_id: slotId, module_id: cur.module_id, label: null, order_key: k, removed: false });
    p.slot_id = slotId; p.order_in_slot = ORDER_STEP; p.rev += 1;
    return this._commit([{ action: "ungroup", placement_id: id, slot_id: slotId, module_id: cur.module_id }], { slot_id: slotId });
  }

  async removePlacement(id, rev, reason) {
    var p = this._placement(id); this._checkRev(rev);
    p.removed = true;
    var slot = this.st.slots.filter(function (s) { return s.slot_id === p.slot_id; })[0];
    this._dropIfEmpty(p.slot_id);
    return this._commit([{ action: "remove", placement_id: id, slot_id: p.slot_id, module_id: slot ? slot.module_id : null }], {});
  }

  async reattach(id, rev, key) {
    var p = this._placement(id);
    var info = this.index[key];
    if (!info || !this.payload.drawers[key]) throw this._notFound("Node");
    this._checkRev(rev);
    if (this._activeKeyPlacement(key, id)) throw this._err(409, "reattach_target_placed", "That node is already placed in this sequence");
    var fresh = this._newPlacement(key, p.slot_id, null, p.order_in_slot);
    this.st.next.placement -= 1;                                   // the fresh record was only a snapshot source
    ["source_key", "node_id", "node_text", "stem_id", "stem_name", "concept_skill_display", "ladder_file_seen", "state_now",
     "is_bridge", "period_hint", "placed_elsewhere"].forEach(function (f) { p[f] = fresh[f]; });
    p.node_id_seen = fresh.node_id; p.node_text_seen = fresh.node_text; p.grade_kind_seen = fresh.grade_kind_seen;
    p.status = "ok"; p.rev += 1; p.updated_by = "demo"; p.updated_at = nowIso();
    return this._commit([{ action: "reattach", placement_id: id, slot_id: p.slot_id }], { placement_id: id });
  }

  async acknowledge(id, rev) {
    var p = this._placement(id); this._checkRev(rev);
    p.status = "ok"; p.grade_kind_seen = p.state_now || p.grade_kind_seen; p.rev += 1;
    p.updated_by = "demo"; p.updated_at = nowIso();
    return this._commit([{ action: "acknowledge", placement_id: id, slot_id: p.slot_id }], { placement_id: id });
  }

  /* ---------------------------------------------------- SequenceView build */
  _placementView(p) {
    var info = p.status === "orphaned" ? null : this.index[p.source_key];
    var v = clone(p);
    delete v.slot_id; delete v.removed;
    if (info) { v.node_text = info.node_text; v.period_hint = info.period_hint || null; v.placed_elsewhere = clone(info.placed_elsewhere || []); }
    var b = [];
    if (p.status === "orphaned") {
      b.push({ code: "orphaned", tier: "structural", label: "orphaned", refs: [], partners: [],
        detail: "Node no longer in the ladder as placed; was: " + p.node_text_seen + " (" + (p.ladder_file_seen || "unknown file") + ")" });
    }
    if (p.status === "grade_changed") {
      b.push({ code: "grade_changed", tier: "structural", label: "was " + p.grade_kind_seen + " \u2192 now " + p.state_now, refs: [], partners: [],
        detail: "The grade ruling changed since this was placed" });
    }
    if (p.status !== "orphaned" && p.is_bridge) {
      b.push({ code: "bridge", tier: "structural", label: "bridge \u00b7 " + (p.state_now || p.grade_kind_seen), refs: [], partners: [],
        detail: "Placed outside this grade's inventory on purpose" });
    }
    v.badges = b;
    return v;
  }

  _view() {
    var st = this.st, seq = st.sequence, slicedLast = this.payload.slice.ladders_last_read;
    if (!seq || seq.archived) {
      return { grade: this.grade, ladders_last_read: slicedLast, sequence: null, modules: [], placed_index: {},
        slice_badges: {}, guardrail: computeGuardrail([]), attention: [] };
    }
    var self = this, all = [], placedIndex = {}, sliceBadges = {}, attention = [];
    var mods = this._activeModules().map(function (m, mi) {
      var modPl = [];
      var slots = self._slotsOf(m.module_id).map(function (s, si) {
        var pls = self._placementsOf(s.slot_id).map(function (p) {
          var pv = self._placementView(p);
          modPl.push(pv); all.push(pv);
          placedIndex[pv.source_key] = { placement_id: pv.placement_id, module_id: m.module_id, module_title: m.title,
            module_position: mi + 1, slot_id: s.slot_id, slot_position: si + 1, status: pv.status, is_bridge: pv.is_bridge };
          var sb = pv.badges.filter(function (x) { return STRUCTURAL_SLICE_CODES.indexOf(x.code) >= 0; });
          if (sb.length) sliceBadges[pv.source_key] = sb;
          if (pv.status === "orphaned" || pv.status === "grade_changed") {
            attention.push({ placement_id: pv.placement_id, rev: pv.rev, module_id: m.module_id, module_title: m.title,
              module_position: mi + 1, slot_id: s.slot_id, slot_position: si + 1, status: pv.status, source_key: pv.source_key,
              node_text_seen: pv.node_text_seen, ladder_file_seen: pv.ladder_file_seen, stem_id_seen: pv.stem_id,
              grade_kind_seen: pv.grade_kind_seen, state_now: pv.state_now, suggestions: [],
              actions: pv.status === "orphaned" ? ["reattach", "remove"] : ["acknowledge", "remove"] });
          }
          return pv;
        });
        return { slot_id: s.slot_id, label: s.label, order_key: s.order_key, position: si + 1, placements: pls };
      });
      return { module_id: m.module_id, title: m.title, note: m.note, order_key: m.order_key, position: mi + 1,
        guardrail: computeGuardrail(modPl), slots: slots };
    });
    return { grade: this.grade, ladders_last_read: slicedLast,
      sequence: { sequence_id: seq.sequence_id, grade: seq.grade, title: seq.title, owner: seq.owner, note: seq.note,
        rev: seq.rev, created_by: seq.created_by, created_at: seq.created_at },
      modules: mods, placed_index: placedIndex, slice_badges: sliceBadges, guardrail: computeGuardrail(all), attention: attention };
  }
}

/* ========================================================================
 * 5. render functions
 *
 * Pure (state, M) -> HTML string.  `state` is the ViewState (contract 5.9);
 * `M` is the page model built by the controller:
 *   { grade, gradesInfo, slice, index, seqView, ui, whoami, views, drawer,
 *     compareData, demo:{on, persisted, source, generatedAt}, vis? }
 * Interactive elements carry data-action / data-id (and data-val); the
 * controller listens once per region.  Every text goes through esc().
 * ===================================================================== */

/** data-* attribute string: da({action:"x", id:5}) -> ' data-action="x" data-id="5"'. */
function da(o) {
  return Object.keys(o).map(function (k) { return " data-" + k + '="' + esc(o[k]) + '"'; }).join("");
}
function fk(key) { return ' data-focus-key="' + esc(key) + '"'; }
function isOpen(M, key, dflt) { var o = M.ui && M.ui.open; return o && has(o, key) ? !!o[key] : !!dflt; }
function seqOf(M) { return M.seqView && M.seqView.sequence ? M.seqView.sequence : null; }
function placedOf(M) { return (M.seqView && M.seqView.placed_index) || {}; }
function visOf(state, M) { return M.vis || visibleSlice(M.slice, state, M.seqView); }

/** data-focus-key for a button: explicit, else derived from its action, so focus can be restored after a re-render. */
function fkAuto(explicit, attrs) {
  var k = explicit || (attrs.action ? attrs.action + ":" + (attrs.id === undefined ? "" : attrs.id) + (attrs.val === undefined ? "" : ":" + attrs.val) : "");
  return k ? fk(k) : "";
}

/** A button with a visible label plus aria-label/title when the label alone is terse. */
function button(label, attrs, o) {
  o = o || {};
  return '<button type="button"' + (o.cls ? ' class="' + o.cls + '"' : "") + da(attrs) +
    fkAuto(o.focus, attrs) + (o.aria ? ' aria-label="' + esc(o.aria) + '"' : "") +
    (o.title ? ' title="' + esc(o.title) + '"' : "") +
    (o.pressed !== undefined ? ' aria-pressed="' + (o.pressed ? "true" : "false") + '"' : "") +
    (o.disabled ? " disabled" : "") + ">" + label + "</button>";
}

function badgeHtml(b) {
  return '<span class="b b-' + esc(b.code) + " tier-" + esc(b.tier) + '" title="' + esc(b.detail || b.label) + '">' + esc(b.label) + "</span>";
}

function gradePills(grades, cur) {
  return (grades || []).map(function (g) { return '<span class="gp' + (g === cur ? " cur" : "") + '">' + esc(gshort(g)) + "</span>"; }).join("");
}

/* ---- header ------------------------------------------------------------ */

function gradeOptionLabel(gi, M) {
  var placed = gi.sequence ? gi.sequence.n_placements : 0;
  if (gi.grade === M.grade && M.seqView) placed = M.seqView.guardrail ? M.seqView.guardrail.n : placed;
  return gi.short + " \u00b7 " + gi.owed_nodes + " owed \u00b7 " + placed + " placed";
}

function renderHeader(state, M) {
  var gi = (M.gradesInfo && M.gradesInfo.grades) || [];
  var opts = gi.map(function (g) {
    return '<option value="' + esc(g.grade) + '"' + (g.grade === M.grade ? " selected" : "") + ">" + esc(gradeOptionLabel(g, M)) + "</option>";
  }).join("");
  var ks = M.gradesInfo ? M.gradesInfo.kind_source : (M.slice && M.slice.kind_source);
  var last = (M.slice && M.slice.ladders_last_read) || (M.gradesInfo && M.gradesInfo.ladders_last_read) || "unknown";
  var stamp = '<span class="stamp" title="Kind source: ' + esc(ks) + "; ladders last read: " + esc(last) + '">Ladders read ' +
    esc(String(last).slice(0, 16)) + " \u00b7 kinds: " + (ks === "grade_type" ? "grade rulings" : "fallback (kind pending)") + "</span>";
  var who = "";
  if (M.whoami) {
    who = '<span class="who">Signed in as <b>' + esc(M.whoami.user) + "</b></span>";
    if (!M.whoami.auth_enabled && !M.demo.on) {
      who += '<label class="who-name">Your name <input type="text" value="' + esc(M.ui.userName || "") + '" placeholder="local" maxlength="64"' +
        da({ action: "set-user" }) + ' aria-label="Your name, used to attribute your edits"></label>';
    }
  }
  var demo = "";
  if (M.demo.on) {
    demo = '<span class="demo-badge" title="' + esc("Runs offline from an embedded snapshot; nothing is sent anywhere") + '">Demo</span>' +
      button("Reset demo", { action: "reset-demo" }, { cls: "linklike", title: "Discard demo edits and start again" });
  }
  return '<div class="hdr-row"><h1>MH2 Grade Sequencing</h1>' +
    '<label class="grade-pick"><span class="lbl">Grade</span><select' + da({ action: "pick-grade" }) + ' aria-label="Grade">' + opts + "</select></label>" +
    stamp + '<span class="grow"></span>' + demo + who +
    button("Sequence", { action: "toggle-rail" }, { cls: "rail-toggle", aria: "Show or hide the sequence rail", pressed: !!M.ui.railOpen }) + "</div>";
}

/** Banners under the header: owner notice, demo storage warning. */
function renderBanner(state, M) {
  var out = "", seq = seqOf(M);
  if (seq && M.whoami && M.whoami.user && seq.owner && seq.owner !== M.whoami.user) {
    out += '<div class="banner owner">You are editing <b>' + esc(seq.owner) + "</b>\u2019s sequence.</div>";
  }
  if (M.demo.on && !M.demo.persisted) {
    out += '<div class="banner warn">Changes will not be kept after reload.</div>';
  }
  return out;
}

/* ---- toolbar ------------------------------------------------------------- */

var TOGGLES = [
  { id: "context", label: "Context", tip: "Show chips from other grades (dimmed)" },
  { id: "leaves", label: "Leaves", tip: "Show leaf chips" },
  { id: "state_ext", label: "State ext", tip: "Show state-extension chips" },
  { id: "unplaced_only", label: "Unplaced only", tip: "Only concept/skills with an owed node not yet placed" },
  { id: "pairings", label: "Pairings", tip: "Show shared-standard pairing badges" },
  { id: "flagged", label: "Flagged", tip: "Only concept/skills with a structural warning (bridge, changed grade, ordering)" }
];

function renderStemsMenu(state, M) {
  var hs = {}, hc = {};
  state.hidden_stems.forEach(function (x) { hs[x] = 1; });
  state.hidden_cs.forEach(function (x) { hc[x] = 1; });
  var rows = "", nh = 0;
  M.slice.super_stems.forEach(function (ss) {
    rows += '<div class="menu-dom">' + esc(ss.domain) + "</div>";
    ss.stems.forEach(function (st) {
      rows += '<label class="menu-row"><input type="checkbox" ' + (hs[st.stem_id] ? "" : "checked ") + da({ action: "stem-visible", id: st.stem_id }) + "> " + esc(st.stem_name) + "</label>";
      if (hs[st.stem_id]) nh += 1;
      st.concept_skills.forEach(function (c) {
        if (hc[c.cs_id]) {
          nh += 1;
          rows += '<div class="menu-cs">Hidden concept/skill: ' + esc(c.label_display) + " " + button("Show", { action: "cs-show", id: c.cs_id }, { cls: "linklike" }) + "</div>";
        }
      });
    });
  });
  var open = isOpen(M, "stemsmenu", false);
  return '<span class="menu-wrap">' + button("Stems" + (nh ? " (" + nh + " hidden)" : "") + " \u25be", { action: "disclose", id: "stemsmenu" }, { cls: "tbtn", pressed: open, aria: "Show or hide stems and concept/skills" }) +
    (open ? '<div class="menu-panel" role="group" aria-label="Stems">' + rows + '<div class="menu-foot">' + button("Show all hidden", { action: "show-all-hidden" }, { cls: "linklike" }) + " " + button("Expand all", { action: "stems-expand-all" }, { cls: "linklike" }) + " " + button("Collapse all", { action: "stems-collapse-all" }, { cls: "linklike" }) + "</div></div>" : "") + "</span>";
}

function renderViewsControls(state, M) {
  var views = M.views || [], ui = M.ui, sel = ui.selectedViewId;
  var opts = '<option value="">Saved views\u2026</option>' + views.map(function (v) {
    return '<option value="' + esc(v.view_id) + '"' + (String(v.view_id) === String(sel) ? " selected" : "") + ">" + esc(v.name) + "</option>";
  }).join("");
  var selView = views.filter(function (v) { return String(v.view_id) === String(sel); })[0];
  var out = '<span class="views"><select' + da({ action: "pick-view" }) + ' aria-label="Saved views">' + opts + "</select>";
  if (ui.viewMode === "save" || ui.viewMode === "rename") {
    out += '<input type="text" class="view-name" maxlength="80" placeholder="View name" value="' + esc(ui.viewMode === "rename" && selView ? selView.name : "") + '"' + da({ action: "view-name" }) + fk("view-name") + ' aria-label="View name">' +
      button("Save", { action: "view-commit" }, { cls: "tbtn primary" }) + button("Cancel", { action: "view-cancel" }, { cls: "tbtn" });
  } else if (ui.viewMode === "delete" && selView) {
    out += '<span class="confirm-inline">Delete \u201c' + esc(selView.name) + "\u201d? " + button("Yes, delete", { action: "view-delete-yes" }, { cls: "tbtn danger" }) + button("Keep", { action: "view-cancel" }, { cls: "tbtn" }) + "</span>";
  } else {
    out += button("Save as\u2026", { action: "view-save" }, { cls: "tbtn" }) +
      button("Rename", { action: "view-rename" }, { cls: "tbtn", disabled: !selView }) +
      button("Delete", { action: "view-delete" }, { cls: "tbtn", disabled: !selView });
  }
  return out + "</span>";
}

function renderToolbar(state, M) {
  var vis = visOf(state, M), total = M.slice.counts.chips;
  var toggles = TOGGLES.map(function (t) {
    return button(esc(t.label), { action: "toggle", id: t.id }, { cls: "tog", pressed: !!state[t.id], title: t.tip });
  }).join("");
  return '<div class="tb-row"><span class="tb-group" role="group" aria-label="View toggles">' + toggles + "</span>" +
    renderStemsMenu(state, M) + renderViewsControls(state, M) +
    button("Reset view", { action: "reset-view" }, { cls: "tbtn", disabled: lensIsDefault(state, M.slice) }) +
    '<span class="grow"></span><span class="counts" aria-live="polite">shown <b>' + vis.counts.shown_chips + "</b> of " + total + " chips \u00b7 " +
    vis.counts.shown_cs + " concept/skills</span></div>";
}

/* ---- slice --------------------------------------------------------------- */

function renderChip(n, state, M) {
  var ph = placedOf(M)[n.source_key], cls = ["chip"];
  if (n.state === "off_grade" || n.state === "no_grade") cls.push("ctx");
  else if (n.state === "state_extension") cls.push("ctx", "stateext");
  else if (n.state === "leaf") cls.push("leaf");
  else cls.push("ingrade");
  if (n.state === "no_grade") cls.push("nograde");
  if (state.node === n.source_key) cls.push("selected");
  if (ph) cls.push("placed");
  var sb = (M.seqView && M.seqView.slice_badges && M.seqView.slice_badges[n.source_key]) || [];
  var kind = (n.badges || []).filter(function (b) { return b.code !== "shared_code" || state.pairings; }).map(badgeHtml).join("");
  var pills = "";
  if (ph) pills += '<span class="pill here" title="Placed in this sequence: ' + esc(ph.module_title) + ", slot " + esc(ph.slot_position) + '">M' + esc(ph.module_position) + " \u00b7 " + esc(ph.slot_position) + "</span>";
  if (ph && ph.is_bridge) pills += '<span class="pill bridge">bridge</span>';
  var inCmp = state.compare.indexOf(n.source_key) >= 0, full = state.compare.length >= COMPARE_MAX && !inCmp;
  var act = "";
  if (!ph) act += button("+ Place", { action: "place-chip", id: n.source_key }, { cls: "cbtn", aria: "Place " + n.node_id + " in the sequence", title: "Place in the target module" });
  act += button(inCmp ? "\u2713 compare" : "\u2295 compare", { action: "toggle-compare", id: n.source_key },
    { cls: "cbtn", pressed: inCmp, disabled: full, aria: (inCmp ? "Remove " : "Add ") + n.node_id + (inCmp ? " from" : " to") + " compare", title: full ? "Compare holds at most 4" : "Compare up to 4 nodes" });
  return '<li class="chip-li"><div class="' + cls.join(" ") + '" data-node="' + esc(n.source_key) + '">' +
    '<button type="button" class="chip-body"' + da({ action: "open-node", id: n.source_key }) + fk("chip:" + n.source_key) + ' aria-label="Open details for ' + esc(n.node_id) + ": " + esc(n.node_text) + '">' +
    '<span class="c-id">' + esc(n.node_id) + "</span>" +
    '<span class="c-text" title="' + esc(n.node_text) + '">' + esc(n.node_text) + "</span>" +
    '<span class="c-pills">' + gradePills(n.grades, M.grade) + "</span>" +
    '<span class="c-badges">' + pills + kind + sb.map(badgeHtml).join("") + "</span></button>" +
    '<div class="c-actions">' + act + "</div></div></li>";
}

function renderStrip(c, state, M) {
  var goal = "";
  if (c.goal_missing) goal = '<div class="goal missing">No goal in ladder</div>';
  else {
    var long = c.goal.length > 150 || c.goal.split("\n").length > 2, open = M.ui.goalOpen && M.ui.goalOpen[c.cs_id];
    goal = '<div class="goal' + (open ? "" : " clamp") + '">' + esc(c.goal) + "</div>" +
      (long ? button(open ? "less" : "more", { action: "goal-toggle", id: c.cs_id }, { cls: "linklike", aria: (open ? "Collapse" : "Expand") + " the goal" }) : "");
  }
  var chips = c.nodes.map(function (n, i) { return (i ? '<li class="arrow" aria-hidden="true">\u2192</li>' : "") + renderChip(n, state, M); }).join("");
  return '<div class="cs" data-cs="' + esc(c.cs_id) + '"><div class="cs-h"><div class="cs-title">' + esc(c.label_display) + "</div>" +
    button("Hide", { action: "cs-hide", id: c.cs_id }, { cls: "linklike", aria: "Hide concept/skill " + c.label_display }) + "</div>" + goal +
    '<ol class="strip">' + chips + "</ol></div>";
}

function stemStats(stem, M) {
  var inGrade = 0, unplaced = 0, placed = placedOf(M);
  stem.concept_skills.forEach(function (c) { c.nodes.forEach(function (n) {
    if (n.in_grade) inGrade += 1;
    if (n.owed && !has(placed, n.source_key)) unplaced += 1;
  }); });
  return { inGrade: inGrade, unplaced: unplaced };
}

function renderSlice(state, M) {
  var vis = visOf(state, M), collapsed = effectiveCollapsed(state, M.slice), full = {};
  M.slice.super_stems.forEach(function (ss) { ss.stems.forEach(function (st) { full[st.stem_id] = st; }); });
  if (!vis.super_stems.length) {
    return '<div class="empty-slice"><p>No concept/skills match the current filters.</p>' + button("Reset view", { action: "reset-view" }, { cls: "tbtn" }) + "</div>";
  }
  return vis.super_stems.map(function (ss) {
    var stems = ss.stems.map(function (st, i) {
      var stat = stemStats(full[st.stem_id], M), isC = !!collapsed[st.stem_id];
      var head = '<header class="stem-h"><h3>' + esc(st.stem_name) + "</h3>" +
        '<span class="stem-stat">' + stat.inGrade + " in grade \u00b7 " + stat.unplaced + " owed unplaced</span><span class=\"grow\"></span>" +
        button("\u25b2", { action: "stem-move", id: st.stem_id, val: "-1" }, { cls: "ibtn", aria: "Move stem " + st.stem_name + " up", title: "Move stem up", disabled: i === 0, focus: "stem:" + st.stem_id + ":up" }) +
        button("\u25bc", { action: "stem-move", id: st.stem_id, val: "1" }, { cls: "ibtn", aria: "Move stem " + st.stem_name + " down", title: "Move stem down", disabled: i === ss.stems.length - 1, focus: "stem:" + st.stem_id + ":down" }) +
        button(isC ? "Expand" : "Collapse", { action: "stem-collapse", id: st.stem_id }, { cls: "tbtn sm", pressed: !isC, aria: (isC ? "Expand " : "Collapse ") + st.stem_name }) +
        button("Hide", { action: "stem-hide", id: st.stem_id }, { cls: "tbtn sm", aria: "Hide stem " + st.stem_name }) + "</header>";
      var body = isC
        ? '<div class="stem-collapsed">' + plural(st.concept_skills.length, "concept/skill") + " \u00b7 " + plural(st.concept_skills.reduce(function (a, c) { return a + c.nodes.length; }, 0), "chip") + " hidden while collapsed.</div>"
        : st.concept_skills.map(function (c) { return renderStrip(c, state, M); }).join("");
      return '<section class="stem" data-stem="' + esc(st.stem_id) + '">' + head + body + "</section>";
    }).join("");
    return '<section class="super"><h2 class="super-h">' + esc(ss.domain) + "</h2>" + stems + "</section>";
  }).join("");
}

/* ---- guardrail readout ------------------------------------------------ */

/** The two bars for one Guardrail object.  Reference marks, never quotas:
 *  no pass/fail wording and no red/green verdict colours anywhere. */
function renderGuardrailBars(g) {
  function seg(shares) {
    return CALIBRATIONS.map(function (l) {
      var w = shares[l]; if (w === null || w === undefined || w <= 0) return "";
      return '<span class="seg seg-' + l + '" style="width:' + (w * 100) + '%"></span>';
    }).join("");
  }
  function ticks(target, cls) {
    var acc = 0, out = "";
    CALIBRATIONS.slice(0, 2).forEach(function (l) {
      acc += target[l];
      out += '<span class="tick ' + cls + '" style="left:' + roundHalfUp(acc * 100, 2) + '%" aria-hidden="true"></span>';
    });
    return out;
  }
  function legend(countMode) {
    return CALIBRATIONS.map(function (l) {
      var share = countMode ? g.count_share[l] : g.time_share[l];
      var val = countMode ? g.counts[l] + " " + (g.counts[l] === 1 ? "placement" : "placements") : fmtNum(g.periods[l]) + " periods";
      var ref = countMode ? g.count_target[l] : (g.show_time_targets ? g.time_target[l] : null);
      return '<span class="lg"><i class="sw seg-' + l + '"></i>' + CAL_LABEL[l] + ' <span class="teach">(' + CAL_TEACHER[l] + ")</span> <b>" + esc(val) + "</b> " +
        (share === null || share === undefined ? "" : "\u00b7 " + pct(share)) + (ref !== null && ref !== undefined ? ' <span class="ref">ref ' + pct(ref) + "</span>" : "") + "</span>";
    }).join("");
  }
  var cCap = g.n_calibrated > 0
    ? "Calibrated " + g.n_calibrated + " of " + g.n + " placements \u00b7 unset " + g.counts.unset
    : (g.n > 0 ? "Nothing calibrated yet \u00b7 unset " + g.counts.unset : "No placements yet");
  var tCap = g.n > 0
    ? "Periods known for " + g.n_timed + " of " + g.n + " placements \u00b7 total " + fmtNum(g.total_periods) + " periods" + (g.show_time_targets ? "" : " (reference marks shown at \u2265 " + Math.round(g.time_mark_min_coverage * 100) + "% coverage)")
    : "No placements yet";
  return '<div class="gsub">Reference marks, not quotas</div>' +
    '<div class="gblock"><div class="glabel">Placements by level</div>' +
    '<div class="gbar" role="img" aria-label="' + esc(cCap) + '"><span class="segs">' + seg(g.count_share) + "</span>" + ticks(g.count_target, "tick-count") + "</div>" +
    '<div class="glegend">' + legend(true) + '</div><div class="gcap">' + esc(cCap) + "</div></div>" +
    '<div class="gblock"><div class="glabel">Time by level</div>' +
    '<div class="gbar" role="img" aria-label="' + esc(tCap) + '"><span class="segs">' + seg(g.time_share) + "</span>" + (g.show_time_targets ? ticks(g.time_target, "tick-time") : "") + "</div>" +
    '<div class="glegend">' + legend(false) + '</div><div class="gcap">' + esc(tCap) + "</div></div>";
}

function targetModuleOf(M) {
  var mods = (M.seqView && M.seqView.modules) || [];
  var t = mods.filter(function (m) { return m.module_id === M.ui.targetModuleId; })[0];
  return t || mods[mods.length - 1] || null;
}

function renderGuardrail(state, M) {
  var seq = seqOf(M);
  if (!seq) return "";
  var tm = targetModuleOf(M), tab = M.ui.guardTab === "module" && tm ? "module" : "seq";
  var g = tab === "module" ? tm.guardrail : M.seqView.guardrail;
  return '<div class="guard"><div class="guard-h"><h2>Balance</h2><span class="tabs2" role="group" aria-label="Balance scope">' +
    button("Sequence", { action: "guard-tab", id: "seq" }, { cls: "tab2", pressed: tab === "seq" }) +
    button(tm ? "This module (M" + tm.position + ")" : "This module", { action: "guard-tab", id: "module" }, { cls: "tab2", pressed: tab === "module", disabled: !tm }) +
    "</span></div>" + renderGuardrailBars(g) + "</div>";
}

/* ---- needs attention ---------------------------------------------------- */

var REASON_TEXT = { same_text_other_stem: "same wording, other stem", same_cs_similar: "similar wording, same concept/skill",
  same_stem_similar: "similar wording, same stem" };

function kindLabel(state) {
  return { core: "core", span: "span", unconfirmed: "unconfirmed", unknown: "kind pending", off_grade: "off-grade",
    leaf: "leaf", no_grade: "no grade", state_extension: "state ext" }[state] || String(state);
}

function renderAttention(state, M) {
  var items = (M.seqView && M.seqView.attention) || [];
  if (!items.length) return "";
  var open = isOpen(M, "attention", true), body = "", lastMod = null;
  items.forEach(function (it) {
    if (it.module_id !== lastMod) { body += '<div class="att-mod">M' + esc(it.module_position) + " \u00b7 " + esc(it.module_title) + "</div>"; lastMod = it.module_id; }
    var acts = "", detail = "";
    if (it.status === "orphaned") {
      detail = '<div class="att-line"><b>Orphaned.</b> This node is no longer in the ladder as it was placed.</div>' +
        '<div class="att-was"><span class="lbl">was</span> ' + esc(it.node_text_seen) + '</div><div class="att-file">' + esc(it.ladder_file_seen || "") + "</div>";
      if (it.suggestions && it.suggestions.length) {
        detail += '<div class="att-sugs">' + it.suggestions.map(function (s) {
          return '<div class="att-sug"><div class="cols"><div><span class="lbl">was</span><br>' + esc(it.node_text_seen) + '</div><div><span class="lbl">now</span><br>' + esc(s.node_text) + "</div></div>" +
            '<div class="att-why">' + esc(s.node_id) + " \u00b7 " + esc(REASON_TEXT[s.reason] || s.reason) + " \u00b7 " + Math.round(s.ratio * 1000) / 10 + "% similar</div>" +
            button("Re-attach", { action: "reattach", id: it.placement_id, key: s.source_key }, { cls: "tbtn primary sm", aria: "Re-attach to " + s.node_id }) + "</div>";
        }).join("") + "</div>";
      } else detail += '<div class="att-none">No close match found.</div>';
      acts = button("Remove", { action: "remove-direct", id: it.placement_id }, { cls: "tbtn sm danger" });
    } else {
      detail = '<div class="att-line"><b>Grade ruling changed:</b> was ' + esc(kindLabel(it.grade_kind_seen)) + " \u2192 now " + esc(kindLabel(it.state_now)) + ".</div>" +
        '<div class="att-was">' + esc(it.node_text_seen) + "</div>";
      acts = button("Keep (acknowledge)", { action: "acknowledge", id: it.placement_id }, { cls: "tbtn primary sm" }) + button("Remove", { action: "remove-direct", id: it.placement_id }, { cls: "tbtn sm danger" });
    }
    body += '<div class="att-item att-' + esc(it.status) + '" data-att="' + esc(it.placement_id) + '">' + detail + '<div class="att-acts">' + acts + "</div></div>";
  });
  return '<section class="attention"><button type="button" class="disc"' + da({ action: "disclose", id: "attention" }) + ' aria-expanded="' + (open ? "true" : "false") + '">' +
    "Needs attention <span class=\"count\">" + items.length + "</span></button>" + (open ? '<div class="att-body">' + body + "</div>" : "") + "</section>";
}

/* ---- rail: modules, slots, placements ------------------------------------ */

function moduleSummaryText(g) {
  return "D " + g.counts.deep + " \u00b7 F " + g.counts.functional + " \u00b7 I " + g.counts.illuminating + " \u00b7 unset " + g.counts.unset + " \u00b7 " + fmtNum(g.total_periods) + " periods";
}

function renderCalibration(p) {
  return '<div class="seg-ctl" role="group" aria-label="Calibration: Deep is Know it, Functional is Use it, Illuminating is See it" title="Know it / Use it / See it">' +
    CALIBRATIONS.map(function (l) {
      return button(CAL_LABEL[l], { action: "set-cal", id: p.placement_id, val: l }, { cls: "segb seg-" + l, pressed: p.calibration === l, title: CAL_TEACHER[l], focus: "cal:" + p.placement_id + ":" + l });
    }).join("") + button("\u2014", { action: "set-cal", id: p.placement_id, val: "" }, { cls: "segb", pressed: !p.calibration, aria: "Calibration not set", title: "Not set", focus: "cal:" + p.placement_id + ":none" }) + "</div>";
}

function renderPeriods(p) {
  var h = p.period_hint, hint = "";
  if (h) {
    var why = hintReason(h);
    hint = '<span class="hint" title="Period hint, shown as written">' + esc(h.text) + "</span>" +
      (h.value !== null && h.value !== undefined ? button("Use " + fmtNum(h.value), { action: "use-hint", id: p.placement_id, val: h.value }, { cls: "tbtn sm", aria: "Use " + fmtNum(h.value) + " periods from the hint" }) : "") +
      (why ? ' <span class="hint-why">(' + esc(why) + ")</span>" : "");
  }
  return '<div class="pl-per"><label class="per-lbl">Periods <input type="number" min="0" max="200" step="0.25" inputmode="decimal" value="' +
    (p.period_estimate === null || p.period_estimate === undefined ? "" : esc(p.period_estimate)) + '"' + da({ action: "set-period", id: p.placement_id }) + fk("per:" + p.placement_id) + ' aria-label="Instructional periods for ' + esc(p.node_id || p.node_id_seen) + '"></label>' + hint + "</div>";
}

/** "Co-place with..." select: every other slot, labelled by module, position and first node. */
function coPlaceSelect(p, M) {
  var opts = "";
  ((M.seqView && M.seqView.modules) || []).forEach(function (m) {
    m.slots.forEach(function (s) {
      if (s.placements.some(function (q) { return q.placement_id === p.placement_id; })) return;
      var first = s.placements[0];
      opts += '<option value="' + esc(s.slot_id) + '">M' + esc(m.position) + " \u00b7 slot " + esc(s.position) + ": " + esc(first ? (first.node_id || first.node_id_seen) : "") + (s.placements.length > 1 ? " +" + (s.placements.length - 1) : "") + "</option>";
    });
  });
  if (!opts) return "";
  return '<select class="mini"' + da({ action: "coplace-to", id: p.placement_id }) + ' aria-label="Co-place ' + esc(p.node_id || p.node_id_seen) + ' with another slot"><option value="">Co-place with\u2026</option>' + opts + "</select>";
}

function renderPlacement(p, M, ctx) {
  var ed = M.ui.editing && M.ui.editing.kind === "note" && M.ui.editing.id === p.placement_id;
  var draft = M.ui.noteDraft && has(M.ui.noteDraft, String(p.placement_id)) ? M.ui.noteDraft[String(p.placement_id)] : (p.differentiation_note || "");
  var confirming = M.ui.confirmRemove === p.placement_id;
  var statusChip = p.status && p.status !== "ok" ? '<span class="b b-' + esc(p.status) + ' tier-structural">' + esc(p.status === "grade_changed" ? "grade changed" : p.status) + "</span>" : "";
  var badges = (p.badges || []).filter(function (b) { return b.code !== "orphaned" && b.code !== "grade_changed"; }).map(badgeHtml).join("");
  var elsewhere = p.placed_elsewhere && p.placed_elsewhere.length ? '<span class="b b-placed_elsewhere tier-info" title="' + esc(placedElsewhereText(p.placed_elsewhere)) + '">also ' + esc(p.placed_elsewhere.map(function (r) { return gshort(r.grade); }).join(", ")) + "</span>" : "";
  var note = ed
    ? '<div class="note-edit"><textarea rows="3" maxlength="1000"' + da({ action: "note-text", id: p.placement_id }) + fk("note:" + p.placement_id) + ' aria-label="Differentiation note">' + esc(draft) + "</textarea>" +
      button("Save note", { action: "note-save", id: p.placement_id }, { cls: "tbtn primary sm" }) + button("Cancel", { action: "note-cancel" }, { cls: "tbtn sm" }) + "</div>"
    : button(p.differentiation_note ? "Note: " + esc(firstLine(p.differentiation_note)) : "Add note", { action: "note-edit", id: p.placement_id }, { cls: "notebtn", focus: "noteb:" + p.placement_id, title: p.differentiation_note || "Add a differentiation note", aria: p.differentiation_note ? "Edit note" : "Add a note" });
  return '<div class="pl' + (p.status && p.status !== "ok" ? " pl-warn" : "") + '" data-pl="' + esc(p.placement_id) + '"' + fk("pl:" + p.placement_id) + ' tabindex="-1">' +
    '<div class="pl-top"><span class="pl-id">' + esc(p.node_id || p.node_id_seen) + " \u00b7 " + esc(p.stem_name) + "</span>" + statusChip + badges + elsewhere + "</div>" +
    '<div class="pl-text">' + esc(p.node_text) + "</div>" + renderCalibration(p) + renderPeriods(p) + '<div class="pl-note">' + note + "</div>" +
    '<div class="pl-tools">' + (ctx.multi ? button("Ungroup", { action: "ungroup", id: p.placement_id }, { cls: "tbtn sm", title: "Give this node a slot of its own" }) : "") +
    coPlaceSelect(p, M) +
    button(confirming ? "Confirm remove" : "Remove", { action: "remove-placement", id: p.placement_id }, { cls: "tbtn sm danger", aria: "Remove " + (p.node_id || p.node_id_seen) + " from the sequence", title: confirming ? "Click again to remove. History keeps a record." : "Remove from the sequence" }) +
    button("Open in drawer", { action: "open-node", id: p.source_key }, { cls: "tbtn sm" }) + "</div></div>";
}

function renderSlot(s, mod, M, flat) {
  var ed = M.ui.editing && M.ui.editing.kind === "slot" && M.ui.editing.id === s.slot_id, multi = s.placements.length > 1;
  var idx = flat.indexOf(s.slot_id), mods = M.seqView.modules;
  var firstOfAll = idx === 0, lastOfAll = idx === flat.length - 1;
  var others = mods.filter(function (m) { return m.module_id !== mod.module_id; });
  var label = ed
    ? '<input type="text" class="slot-label-in" maxlength="120" value="' + esc(s.label || "") + '"' + da({ action: "slot-label", id: s.slot_id }) + fk("slotlabel:" + s.slot_id) + ' aria-label="Slot label">'
    : button(s.label ? esc(s.label) : (multi ? "Add label (co-placed)" : "Add label"), { action: "slot-label-edit", id: s.slot_id }, { cls: "linklike slot-label", focus: "slotl:" + s.slot_id, aria: "Edit slot label" });
  var tip = "Moves across module boundaries at the ends";
  var moveTo = '<select class="mini"' + da({ action: "slot-move-to", id: s.slot_id }) + ' aria-label="Move slot to module"><option value="">Move to module\u2026</option>' +
    others.map(function (m) { return '<option value="' + esc(m.module_id) + '">M' + esc(m.position) + " " + esc(m.title) + "</option>"; }).join("") + "</select>";
  return '<div class="slot' + (multi ? " multi" : "") + '" role="group" data-kind="slot" data-id="' + esc(s.slot_id) + '"' + fk("slot:" + s.slot_id) + ' tabindex="0" aria-label="Slot ' + esc(s.position) + (s.label ? ": " + esc(s.label) : "") + '. Alt plus arrow up or down moves it.">' +
    '<div class="slot-h"><span class="slot-pos">Slot ' + esc(s.position) + (multi ? " \u00b7 co-placed" : "") + "</span>" + label + '<span class="grow"></span>' +
    button("\u25b2", { action: "slot-move", id: s.slot_id, val: "up" }, { cls: "ibtn", aria: "Move slot up", title: "Move slot up. " + tip, disabled: firstOfAll, focus: "slot:" + s.slot_id + ":up" }) +
    button("\u25bc", { action: "slot-move", id: s.slot_id, val: "down" }, { cls: "ibtn", aria: "Move slot down", title: "Move slot down. " + tip, disabled: lastOfAll, focus: "slot:" + s.slot_id + ":down" }) +
    (others.length ? moveTo : "") + button("Merge into slot above", { action: "slot-merge", id: s.slot_id }, { cls: "tbtn sm", disabled: firstOfAll, title: "Co-place this slot's nodes with the slot above it" }) + "</div>" +
    s.placements.map(function (p) { return renderPlacement(p, M, { multi: multi }); }).join("") + "</div>";
}

function renderModule(mod, M, flat, isFirst, isLast, target) {
  var ed = M.ui.editing && M.ui.editing.kind === "module" && M.ui.editing.id === mod.module_id;
  var title = ed
    ? '<input type="text" class="mod-title-in" maxlength="120" value="' + esc(mod.title) + '"' + da({ action: "module-title", id: mod.module_id }) + fk("modtitle:" + mod.module_id) + ' aria-label="Module title">'
    : '<h3 class="mod-title" title="Double-click to rename"' + da({ action: "module-edit-dbl", id: mod.module_id }) + ">" + esc(mod.title) + "</h3>" + button("Edit", { action: "module-edit", id: mod.module_id }, { cls: "linklike", aria: "Rename module " + mod.title, focus: "modedit:" + mod.module_id });
  var isT = target && target.module_id === mod.module_id;
  return '<section class="module' + (isT ? " target" : "") + '" role="group" data-kind="module" data-id="' + esc(mod.module_id) + '"' + fk("module:" + mod.module_id) + ' tabindex="0" aria-label="Module ' + esc(mod.position) + ": " + esc(mod.title) + '. Alt plus arrow up or down moves it.">' +
    '<header class="mod-h"><span class="mod-pos">M' + esc(mod.position) + "</span>" + title + '<span class="grow"></span>' +
    button("\u25b2", { action: "module-move", id: mod.module_id, val: "up" }, { cls: "ibtn", aria: "Move module up", title: "Move module up", disabled: isFirst, focus: "module:" + mod.module_id + ":up" }) +
    button("\u25bc", { action: "module-move", id: mod.module_id, val: "down" }, { cls: "ibtn", aria: "Move module down", title: "Move module down", disabled: isLast, focus: "module:" + mod.module_id + ":down" }) + "</header>" +
    '<div class="mod-sub"><label class="target-pick"><input type="radio" name="target-module" value="' + esc(mod.module_id) + '"' + (isT ? " checked" : "") + da({ action: "target-module", id: mod.module_id }) + "> Place into this module</label>" +
    '<span class="mod-sum">' + esc(moduleSummaryText(mod.guardrail)) + "</span></div>" +
    (mod.slots.length ? mod.slots.map(function (s) { return renderSlot(s, mod, M, flat); }).join("") : '<div class="mod-empty">Empty. Use \u201c+ Place\u201d on a chip.</div>') +
    '<footer class="mod-f">' + button("Remove module", { action: "module-remove", id: mod.module_id }, { cls: "tbtn sm danger", disabled: mod.slots.length > 0, title: mod.slots.length ? "Only an empty module can be removed" : "Remove this empty module" }) + "</footer></section>";
}

/** The place prompt (contract 7.3): cross-grade differentiation note and/or off-grade bridge confirm. */
function renderPrompt(state, M) {
  var pr = M.ui.prompt;
  if (!pr) return "";
  var mods = (M.seqView && M.seqView.modules) || [], mod = mods.filter(function (m) { return m.module_id === pr.moduleId; })[0];
  var gl = GRADE_LABELS[M.grade] || M.grade, lines = "", needNote = false, needBridge = false;
  pr.keys.forEach(function (k) {
    var n = M.index[k]; if (!n) return;
    if (n.requires_confirm) {
      needBridge = true;
      lines += '<p class="pr-line"><b>' + esc(n.node_id) + "</b> is not in " + esc(gl) + "\u2019s inventory (" + esc(kindLabel(n.state)) + "; its grades: " + esc((n.grades.length ? n.grades.map(gshort).join(", ") : "none recorded")) + "). Place it as a bridge?</p>";
    }
    if (n.placed_elsewhere && n.placed_elsewhere.length) {
      needNote = true;
      lines += '<p class="pr-line"><b>' + esc(n.node_id) + "</b> \u2014 Also placed in " + esc(placedElsewhereText(n.placed_elsewhere)) + ".</p>" +
        '<label class="pr-note">How is it different in ' + esc(gl) + "? (optional)<textarea rows=\"2\" maxlength=\"1000\"" + da({ action: "prompt-note", id: k }) + ">" + esc((pr.notes && pr.notes[k]) || "") + "</textarea></label>";
    }
  });
  var goBtns = "";
  if (needNote) goBtns += button(needBridge ? "Place as bridge with note" : "Place with note", { action: "prompt-go", val: "note" }, { cls: "tbtn primary" }) + button(needBridge ? "Place as bridge without note" : "Place without note", { action: "prompt-go", val: "nonote" }, { cls: "tbtn" });
  else goBtns += button(pr.keys.length > 1 ? "Place as bridge(s)" : "Place as bridge", { action: "prompt-go", val: "nonote" }, { cls: "tbtn primary" });
  return '<section class="prompt" role="dialog" aria-modal="false" aria-label="Confirm placement"' + fk("prompt") + ' tabindex="-1"><div class="pr-h">Place into ' + (mod ? "M" + esc(mod.position) + " " + esc(mod.title) : "the module") + "</div>" +
    lines + '<div class="pr-acts">' + goBtns + button("Cancel", { action: "prompt-cancel" }, { cls: "tbtn" }) + "</div></section>";
}

function renderHistory(state, M) {
  var open = isOpen(M, "history", false), ev = M.ui.events || [];
  var body = "";
  if (open) {
    body = ev.length ? '<ul class="events">' + ev.map(function (e) { return "<li><b>" + esc(e.actor) + "</b> \u00b7 " + esc(String(e.action).replace(/_/g, " ")) + ' \u00b7 <span class="when">' + esc(String(e.at).replace("T", " ").slice(0, 19)) + "</span></li>"; }).join("") + "</ul>" : '<p class="muted">No changes recorded yet.</p>';
    if (M.ui.eventsNext) body += button("More", { action: "history-more" }, { cls: "tbtn sm" });
  }
  return '<section class="history"><button type="button" class="disc"' + da({ action: "disclose", id: "history" }) + ' aria-expanded="' + (open ? "true" : "false") + '">History</button>' + body + "</section>";
}

function renderRail(state, M) {
  var seq = seqOf(M), gl = GRADE_LABELS[M.grade] || M.grade;
  if (!seq) {
    return '<div class="rail-empty"><h2>Sequence</h2><p>No ' + esc(gl) + " sequence yet. Start one to place nodes into modules, calibrate them, and see the balance.</p>" +
      button("Start the " + esc(gl) + " sequence", { action: "start-sequence" }, { cls: "tbtn primary big", focus: "start-seq" }) + "</div>" + renderDemoFootnote(M);
  }
  var mods = M.seqView.modules, flat = [];
  mods.forEach(function (m) { m.slots.forEach(function (s) { flat.push(s.slot_id); }); });
  var target = targetModuleOf(M), prompt = renderPrompt(state, M);
  var cards = mods.map(function (m, i) {
    return (prompt && target && m.module_id === target.module_id ? prompt : "") + renderModule(m, M, flat, i === 0, i === mods.length - 1, target);
  }).join("");
  if (prompt && !target) cards = prompt + cards;
  var ed = M.ui.editing && M.ui.editing.kind === "seqtitle";
  var head = '<div class="seq-h">' + (ed
    ? '<input type="text" class="mod-title-in" maxlength="120" value="' + esc(seq.title) + '"' + da({ action: "seq-title" }) + fk("seqtitle") + ' aria-label="Sequence title">'
    : "<h2>" + esc(seq.title) + "</h2>" + button("Edit", { action: "seq-edit" }, { cls: "linklike", aria: "Rename the sequence" })) +
    '<div class="seq-meta">Owner ' + esc(seq.owner) + " \u00b7 " + plural(mods.length, "module") + " \u00b7 " + button("Download CSV", { action: "export-csv" }, { cls: "linklike", title: "The ordered sequence as a spreadsheet file" }) + "</div></div>";
  return renderGuardrail(state, M) + renderAttention(state, M) + head + '<div class="modules">' + cards + "</div>" +
    button("+ Add module", { action: "add-module" }, { cls: "tbtn add", focus: "add-module" }) + renderHistory(state, M) + renderDemoFootnote(M);
}

function renderDemoFootnote(M) {
  return M.demo && M.demo.on ? '<p class="footnote">' + esc(DEMO_NOTICE_ORDERING) + ".</p>" : "";
}

/* ---- drawer ---------------------------------------------------------------- */

function nodeLink(n, M) {
  var label = esc(n.node_id) + (n.in_grade ? " (in grade)" : "");
  return M.index && M.index[n.source_key]
    ? button(label, { action: "open-node", id: n.source_key }, { cls: "nodelink" })
    : '<span class="nodelink plain">' + label + "</span>";
}

function renderDrawer(state, M) {
  var d = M.drawer;
  if (!d) return "";
  var ph = placedOf(M)[d.source_key], seq = seqOf(M), mods = (M.seqView && M.seqView.modules) || [], target = targetModuleOf(M);
  var inCmp = state.compare.indexOf(d.source_key) >= 0, full = state.compare.length >= COMPARE_MAX && !inCmp;
  var cs = '<div class="d-cs"><div class="d-k">' + esc(d.stem_name) + " \u00b7 concept/skill</div><div class=\"d-cs-t\">" + esc(d.cs.label_display) + "</div>" +
    (d.cs.goal_missing ? '<div class="goal missing">No goal in ladder</div>' : '<div class="d-goal"><span class="lbl">Goal</span>' + esc(d.cs.goal) + "</div>") + "</div>";
  var rul = d.ruling || {};
  var node = '<div class="d-node"><div class="d-id">' + esc(d.node_id) + '</div><div class="d-text">' + esc(d.node_text) + "</div>" +
    '<div class="d-kind"><span class="lbl">Kind in ' + esc(GRADE_LABELS[d.grade] || d.grade) + '</span> <span class="b b-kind tier-info">' + esc(kindLabel(d.state)) + "</span> " +
    (d.badges || []).filter(function (b) { return b.code !== "shared_code" || state.pairings; }).map(badgeHtml).join("") + "</div>" +
    '<div class="d-grades">' + (d.grade_states || []).map(function (g) { return '<span class="gp' + (g.grade === d.grade ? " cur" : "") + '" title="' + esc(kindLabel(g.state)) + '">' + esc(g.short) + " " + esc(kindLabel(g.state)) + "</span>"; }).join("") + "</div>" +
    (rul.raw_value ? '<div class="d-line"><span class="lbl">Grade as written</span> ' + esc(rul.raw_value) + "</div>" : "") +
    (d.state === "unconfirmed" ? '<div class="d-line"><span class="lbl">Ruling</span> ' + esc(rul.ruling_type || "unconfirmed") + (rul.states_mentioned ? "; states: " + esc(rul.states_mentioned) : "") + "</div>" : "") +
    (rul.notes ? '<div class="d-line muted">' + esc(rul.notes) + "</div>" : "") + "</div>";
  var place;
  if (ph) {
    place = '<div class="d-place"><b>In this sequence:</b> M' + esc(ph.module_position) + " \u00b7 slot " + esc(ph.slot_position) + (ph.is_bridge ? " (bridge)" : "") + " " +
      button("Show in rail", { action: "show-in-rail", id: ph.placement_id }, { cls: "tbtn sm" }) + "</div>";
  } else if (!seq) {
    place = '<div class="d-place muted">Start the sequence in the rail to place this node.</div>';
  } else {
    place = '<div class="d-place"><label>Place in <select' + da({ action: "drawer-module" }) + ' aria-label="Target module">' + mods.map(function (m) {
      return '<option value="' + esc(m.module_id) + '"' + (target && m.module_id === target.module_id ? " selected" : "") + ">M" + esc(m.position) + " " + esc(m.title) + "</option>";
    }).join("") + "</select></label> " + button("Place", { action: "place-chip", id: d.source_key }, { cls: "tbtn primary sm" }) + "</div>";
  }
  var els = d.placed_elsewhere && d.placed_elsewhere.length ? '<div class="d-else"><span class="lbl">Also placed in</span> ' + esc(placedElsewhereText(d.placed_elsewhere)) + "</div>" : "";
  var si = d.strip.map(function (s) { return s.source_key; }), me = si.indexOf(d.source_key);
  var strip = '<div class="d-strip"><div class="d-k">Progression</div><div class="mini-strip">' + d.strip.map(function (s) {
    return button(esc(s.node_id), { action: "open-node", id: s.source_key }, { cls: "mini" + (s.is_self ? " self" : "") + (s.in_grade ? "" : " ctx"), aria: "Open " + s.node_id + " (" + kindLabel(s.state) + ")", title: kindLabel(s.state) });
  }).join('<span class="arrow" aria-hidden="true">\u2192</span>') + "</div>" +
    '<div class="d-nav">' + button("\u2039 prev", { action: "open-node", id: me > 0 ? si[me - 1] : "" }, { cls: "tbtn sm", disabled: me <= 0 }) + button("next \u203a", { action: "open-node", id: me >= 0 && me < si.length - 1 ? si[me + 1] : "" }, { cls: "tbtn sm", disabled: me < 0 || me >= si.length - 1 }) + "</div></div>";
  var hint = "";
  if (d.period_hint) hint = '<div class="d-sec"><h4>Period hint</h4><div>' + esc(d.period_hint.text) + (hintReason(d.period_hint) ? ' <span class="hint-why">(' + esc(hintReason(d.period_hint)) + ")</span>" : "") + "</div></div>";
  var showAll = !!M.ui.showAllFields;
  var fields = '<div class="d-sec"><h4>Node details</h4>' + d.fields.map(function (f) {
    return '<div class="fld"><h5>' + esc(f.label) + "</h5><ul>" + f.values.map(function (v) { return "<li>" + esc(v) + "</li>"; }).join("") + "</ul></div>";
  }).join("") + (showAll ? d.fields_empty.map(function (f) { return '<div class="fld empty"><h5>' + esc(f.label) + '</h5><ul><li class="muted">\u2014</li></ul></div>'; }).join("") : "") +
    (d.fields_empty.length ? button(showAll ? "Hide empty fields" : "Show all fields (" + d.fields_empty.length + " empty)", { action: "toggle-all-fields" }, { cls: "linklike" }) : "") + "</div>";
  var std = '<div class="d-sec"><h4>Standards</h4>' + (d.standards.length ? '<div class="codes">' + d.standards.map(function (s) {
    return '<span class="code' + (s.shared ? " shared" : "") + '" title="' + esc(s.relation || "") + (s.annotation ? ": " + esc(s.annotation) : "") + '">' + esc(s.code) + (s.shared ? ' <em>shared</em>' : "") + "</span>";
  }).join("") + "</div>" : '<div class="muted">No CCSS codes.</div>');
  if (d.state_codes.length) {
    var so = isOpen(M, "statecodes", false);
    std += button("State codes (" + d.state_codes.length + ")", { action: "disclose", id: "statecodes" }, { cls: "disc sm" }) + (so ? '<div class="codes">' + d.state_codes.map(function (s) { return '<span class="code" title="' + esc(s.relation || "") + '">' + esc(s.code) + "</span>"; }).join("") + "</div>" : "");
  }
  std += "</div>";
  var pair = "";
  if (d.pairings && d.pairings.length) pair = '<div class="d-sec pair-sec"><h4>Pairings (shared standards)</h4>' + d.pairings.map(function (p) {
    return '<div class="pair"><b>' + esc(p.stem_name) + ":</b> " + esc(p.codes.join(", ")) + " \u2192 " + p.nodes.map(function (n) { return nodeLink(n, M); }).join(" ") + "</div>";
  }).join("") + "</div>";
  var les = "";
  var sharedLessons = (d.lessons || []).filter(function (l) { return l.shared_with.length; });
  if (sharedLessons.length) les = '<div class="d-sec pair-sec"><h4>Shared EM2 lessons</h4>' + sharedLessons.map(function (l) {
    return '<div class="pair"><b>' + esc(l.lesson_id) + "</b> \u2192 " + l.shared_with.map(function (w) { return esc(w.stem_name) + ": " + w.nodes.map(function (n) { return nodeLink(n, M); }).join(" "); }).join("; ") + "</div>";
  }).join("") + "</div>";
  var stemsInSlice = {};
  M.slice.super_stems.forEach(function (ss) { ss.stems.forEach(function (st) { stemsInSlice[st.stem_id] = true; }); });
  var links = '<div class="d-sec"><h4>Stated links <span class="asc">as captured</span></h4>' + (d.links.length ? '<div class="codes">' + d.links.map(function (l) {
    return l.stem_id && stemsInSlice[l.stem_id] ? button(esc(l.text), { action: "goto-stem", id: l.stem_id }, { cls: "linkchip", title: "Go to this stem in the slice" }) : '<span class="linkchip plain">' + esc(l.text) + "</span>";
  }).join("") + "</div>" : '<div class="muted">None captured.</div>') + "</div>";
  var refs = '<div class="d-sec"><h4>Product references</h4>' + (d.product_refs.length ? "<ul class=\"refs\">" + d.product_refs.map(function (r) { return "<li>" + esc(r.product || "") + " \u00b7 " + esc(r.raw_ref) + "</li>"; }).join("") + "</ul>" : '<div class="muted">None.</div>') + "</div>";
  return '<div class="drawer-h"><h2 tabindex="-1"' + fk("drawer-h") + ">" + esc(d.node_id) + "</h2><span class=\"grow\"></span>" +
    button(inCmp ? "\u2713 In compare" : "\u2295 Compare", { action: "toggle-compare", id: d.source_key }, { cls: "tbtn sm", pressed: inCmp, disabled: full }) +
    button("\u2715 Close", { action: "close-drawer" }, { cls: "tbtn sm", aria: "Close details", title: "Close (Esc)" }) + "</div>" +
    '<div class="drawer-body">' + cs + node + place + els + strip + hint + fields + std + pair + les + links + refs + "</div>";
}

/* ---- compare tray and sheet ---------------------------------------------------- */

function renderTray(state, M) {
  if (!state.compare.length || state.sheet) return "";
  return '<div class="tray-in"><b>Compare ' + state.compare.length + "/" + COMPARE_MAX + "</b> \u00b7 " +
    state.compare.map(function (k) { var n = M.index[k]; return '<span class="tray-chip">' + esc(n ? n.node_id : k) + "</span>"; }).join("") +
    '<span class="grow"></span>' + button("Open", { action: "open-sheet" }, { cls: "tbtn primary sm" }) + button("Clear", { action: "clear-compare" }, { cls: "tbtn sm" }) + "</div>";
}

function renderCell(row, cell) {
  if (row.kind === "flag") return cell ? '<span class="tick-ok" aria-label="yes">\u2713</span>' : "";
  if (!cell.length) return '<span class="muted">\u2014</span>';
  if (row.key === "state") return cell.map(function (x) { return '<span class="b tier-info">' + esc(kindLabel(x)) + "</span>"; }).join("");
  if (row.key === "grades") return cell.map(function (g) { return '<span class="gp">' + esc(gshort(g)) + "</span>"; }).join(" ");
  if (row.key === "state_codes") return '<span class="codes-inline">' + esc(cell.join(", ")) + "</span>";
  return cell.map(function (x) { return '<div class="cell-line">' + esc(x) + "</div>"; }).join("");
}

function renderSheet(state, M) {
  if (!state.sheet || !state.compare.length) return "";
  var cmp = M.compareData, ui = M.ui, placed = placedOf(M);
  var head = '<div class="sheet-h"><span class="sheet-grip" role="separator" tabindex="0" aria-orientation="horizontal" aria-label="Resize compare sheet. Arrow up or down."' + da({ action: "sheet-grip" }) + fk("sheet-grip") + "></span>" +
    "<h2>Compare " + state.compare.length + "/" + COMPARE_MAX + "</h2>" +
    '<label class="chk"><input type="checkbox" ' + (ui.cmpHideEmpty !== false ? "checked " : "") + da({ action: "cmp-hide-empty" }) + "> Hide empty rows</label>" +
    '<label class="chk"><input type="checkbox" ' + (ui.cmpSharedFirst ? "checked " : "") + da({ action: "cmp-shared-first" }) + "> Shared rows first</label>" +
    '<span class="grow"></span>';
  var seq = seqOf(M), mods = (M.seqView && M.seqView.modules) || [], target = targetModuleOf(M);
  if (seq && mods.length) {
    var skipped = state.compare.filter(function (k) { return has(placed, k); }).map(function (k) { var n = M.index[k]; return (n ? n.node_id : k) + " in M" + placed[k].module_position; });
    head += '<label class="coplace">Co-place in module <select' + da({ action: "cmp-module" }) + ' aria-label="Module to co-place into">' + mods.map(function (m) {
      return '<option value="' + esc(m.module_id) + '"' + (target && m.module_id === target.module_id ? " selected" : "") + ">M" + esc(m.position) + " " + esc(m.title) + "</option>";
    }).join("") + "</select></label>" + button("Co-place in a module", { action: "cmp-coplace" }, { cls: "tbtn primary", disabled: skipped.length === state.compare.length }) +
      (skipped.length ? '<span class="skipnote">skipped: already placed (' + esc(skipped.join(", ")) + ")</span>" : "");
  } else {
    head += '<span class="muted">Start a sequence with a module to co-place these nodes.</span>';
  }
  head += button("\u2715 Close", { action: "close-sheet" }, { cls: "tbtn sm", aria: "Close compare", title: "Close (Esc)" }) + "</div>";
  if (!cmp) return head + '<div class="sheet-body"><p class="muted">Loading\u2026</p></div>';
  var rows = compareRowsView(cmp, ui.cmpHideEmpty !== false, !!ui.cmpSharedFirst);
  var cols = cmp.columns.map(function (c) {
    var p = placed[c.source_key];
    return '<th scope="col" class="cmp-col"><div class="cmp-id">' + button(esc(c.node_id), { action: "open-node", id: c.source_key }, { cls: "nodelink" }) +
      button("\u2715", { action: "cmp-remove", id: c.source_key }, { cls: "ibtn", aria: "Remove " + c.node_id + " from compare", title: "Remove from compare" }) + "</div>" +
      '<div class="cmp-stem">' + esc(c.stem_name) + '</div><div class="cmp-text">' + esc(c.node_text) + '</div><div><span class="b tier-info">' + esc(kindLabel(c.state)) + "</span>" +
      (p ? ' <span class="pill here">M' + esc(p.module_position) + " \u00b7 " + esc(p.slot_position) + "</span>" : "") + "</div></th>";
  }).join("");
  var body = rows.map(function (r, i) {
    var prev = rows[i - 1], sep = !prev || prev.group !== r.group;
    return "<tr" + (r.shared ? ' class="shared"' : "") + (sep ? ' data-group-start="' + esc(r.group) + '"' : "") + '><th scope="row" class="row-lbl">' + esc(r.label) + (r.shared ? ' <span class="tag-shared">shared</span>' : "") + "</th>" +
      r.cells.map(function (cell) { return "<td>" + renderCell(r, cell) + "</td>"; }).join("") + "</tr>";
  }).join("");
  return head + '<div class="sheet-body"><table class="cmp"><thead><tr><th class="corner" scope="col">' + (cmp.shared_ccss.length || cmp.shared_lessons.length ? "Shared: " + esc(cmp.shared_ccss.concat(cmp.shared_lessons).join(", ")) : "No shared standards or lessons") + "</th>" + cols + "</tr></thead><tbody>" + body + "</tbody></table></div>";
}

/* ---- notices and toasts ------------------------------------------------------------ */

function renderNotice(M) {
  var n = M.ui.notice;
  return n ? '<div class="notice ' + esc(n.kind || "info") + '"><span>' + esc(n.text) + "</span>" + button("Dismiss", { action: "dismiss-notice" }, { cls: "linklike" }) + "</div>" : "";
}

function renderToasts(M) {
  return (M.ui.toasts || []).map(function (t) {
    return '<div class="toast" role="alert"><span>' + esc(t.text) + "</span>" + button("\u2715", { action: "dismiss-toast", id: t.id }, { cls: "ibtn", aria: "Dismiss message" }) + "</div>";
  }).join("");
}

function renderFooter(M) {
  if (!M.demo || !M.demo.on) return "";
  return "<span>Demo \u00b7 data built from " + esc(M.demo.source === "mh2.db" ? "mh2.db" : "fixture") + (M.demo.generatedAt ? " \u00b7 generated " + esc(String(M.demo.generatedAt).slice(0, 16).replace("T", " ")) : "") +
    " \u00b7 " + (M.demo.persisted ? "edits are kept in this browser" : "edits are not kept after reload") + "</span>";
}

/* ========================================================================
 * 6. controller
 *
 * createApp(env) wires one page: it owns the view state (contract 5.9) and the
 * model M, renders the regions, and turns delegated DOM events into calls on
 * `api`.  Everything goes through act(name, data), so tests can drive it
 * without a DOM.  After any write the returned SequenceView replaces the old
 * one wholesale and the regions are re-rendered, with scroll positions and
 * keyboard focus (data-focus-key) restored.
 * ===================================================================== */

function newUi(userName) {
  return { open: {}, goalOpen: {}, editing: null, prompt: null, notice: null, toasts: [], toastSeq: 0,
    railOpen: false, guardTab: "seq", targetModuleId: null, viewMode: null, viewDraft: "", selectedViewId: null,
    events: [], eventsNext: null, showAllFields: false, cmpHideEmpty: true, cmpSharedFirst: false, cmpModuleId: null,
    userName: userName || "", sheetH: 45, confirmRemove: null, noteDraft: {}, focusOverride: null, flashIds: [], scrollTo: null };
}

var REGIONS = ["hdr", "banner", "toolbar", "rail", "slice", "drawer", "sheet", "tray", "notice", "toasts", "foot"];

function createApp(env) {
  var doc = env.doc, api = env.api, hist = env.hist || null, loc = env.loc || { search: "", pathname: "" };
  var setT = env.setTimeout || (typeof setTimeout !== "undefined" ? setTimeout : function () { return 0; });
  var store = env.storage === undefined ? safeStorage() : env.storage;
  var userName = "";
  try { userName = store ? (store.getItem("mh2seq-user") || "") : ""; } catch (e) { userName = ""; }

  var state = defaultState(null);
  var M = { grade: null, gradesInfo: null, slice: null, index: {}, seqView: null, ui: newUi(userName), whoami: null, views: [],
    drawer: null, compareData: null, vis: null,
    demo: { on: !!api.isDemo, persisted: !!api.persisted, source: env.demoSource || null, generatedAt: env.demoGeneratedAt || null } };
  var ui = M.ui, busy = false, nodeCache = {}, lastHtml = {}, tokNode = 0, tokCmp = 0, lastPlace = null;
  var R = {};
  REGIONS.forEach(function (id) { R[id] = doc.getElementById(id); });
  var root = doc.getElementById("app");

  /* ---- small helpers ---- */
  function num(x) { return x === "" || x === null || x === undefined ? null : Number(x); }
  function gradeLabel() { return GRADE_LABELS[M.grade] || M.grade; }
  function findBy(attr, value) {
    try { return doc.querySelector("[" + attr + '="' + String(value).replace(/"/g, '\\"') + '"]'); } catch (e) { return null; }
  }
  function findModule(id) { return ((M.seqView && M.seqView.modules) || []).filter(function (m) { return m.module_id === id; })[0] || null; }
  function findSlot(id) {
    var out = null;
    ((M.seqView && M.seqView.modules) || []).forEach(function (m) { m.slots.forEach(function (s) { if (s.slot_id === id) out = { slot: s, module: m }; }); });
    return out;
  }
  function findPlacement(id) {
    var out = null;
    ((M.seqView && M.seqView.modules) || []).forEach(function (m) { m.slots.forEach(function (s) { s.placements.forEach(function (p) { if (p.placement_id === id) out = { placement: p, slot: s, module: m }; }); }); });
    return out;
  }
  function flatSlots() {
    var out = [];
    ((M.seqView && M.seqView.modules) || []).forEach(function (m) { m.slots.forEach(function (s) { out.push(s); }); });
    return out;
  }
  function fixTarget() {
    var mods = (M.seqView && M.seqView.modules) || [];
    if (!mods.length) { ui.targetModuleId = null; return; }
    if (!mods.some(function (m) { return m.module_id === ui.targetModuleId; })) ui.targetModuleId = mods[mods.length - 1].module_id;
  }

  /* ---- rendering with scroll / focus preservation ---- */
  function capture() {
    var k = null, a = doc.activeElement;
    try {
      if (a && a.getAttribute) k = a.getAttribute("data-focus-key");
      if (!k && a && a.closest) { var c = a.closest("[data-focus-key]"); k = c ? c.getAttribute("data-focus-key") : null; }
    } catch (e) { k = null; }
    return { focus: k, slice: R.slice ? R.slice.scrollTop : 0, rail: R.rail ? R.rail.scrollTop : 0 };
  }
  function focusKey(key) {
    var el = findBy("data-focus-key", key);
    while ((!el || el.disabled) && key.lastIndexOf(":") > 0) { key = key.slice(0, key.lastIndexOf(":")); el = findBy("data-focus-key", key); }
    if (el && !el.disabled && el.focus) el.focus();
  }
  function restore(k) {
    if (R.slice) R.slice.scrollTop = k.slice;
    if (R.rail) R.rail.scrollTop = k.rail;
    var want = ui.focusOverride || k.focus;
    ui.focusOverride = null;
    if (want) focusKey(want);
  }
  function put(id, html) {
    if (!R[id] || lastHtml[id] === html) return;
    R[id].innerHTML = html; lastHtml[id] = html;
  }
  function afterRender() {
    ui.flashIds.forEach(function (pid) {
      var el = findBy("data-pl", pid);
      if (el) { if (el.classList) el.classList.add("flash"); if (el.scrollIntoView) el.scrollIntoView({ block: "nearest" }); }
    });
    ui.flashIds = [];
    if (ui.scrollTo) {
      var t = findBy(ui.scrollTo.attr, ui.scrollTo.value);
      if (t && t.scrollIntoView) t.scrollIntoView({ block: "start" });
      ui.scrollTo = null;
    }
  }
  var pointerDown = false, renderPending = false;
  function renderAll() {
    if (!M.slice) return;
    // Do not swap the DOM between mousedown and mouseup: the browser would drop the click.
    if (pointerDown) { renderPending = true; return; }
    fixTarget();
    M.vis = visibleSlice(M.slice, state, M.seqView);
    M.demo.persisted = !!api.persisted;
    var keep = capture();
    put("hdr", renderHeader(state, M)); put("banner", renderBanner(state, M)); put("toolbar", renderToolbar(state, M));
    put("slice", renderSlice(state, M)); put("rail", renderRail(state, M)); put("drawer", renderDrawer(state, M));
    put("sheet", renderSheet(state, M)); put("tray", renderTray(state, M)); put("notice", renderNotice(M));
    put("toasts", renderToasts(M)); put("foot", renderFooter(M));
    if (R.drawer) R.drawer.hidden = !M.drawer;
    if (R.sheet) { R.sheet.hidden = !(state.sheet && state.compare.length); if (R.sheet.style && R.sheet.style.setProperty) R.sheet.style.setProperty("--sheet-h", ui.sheetH + "vh"); }
    if (R.tray) R.tray.hidden = !(state.compare.length && !state.sheet);
    if (root && root.classList) {
      root.classList.toggle("drawer-open", !!M.drawer);
      root.classList.toggle("rail-open", !!ui.railOpen);
    }
    restore(keep);
    afterRender();
  }
  function syncUrl() {
    if (!hist || !hist.replaceState || !M.slice) return;
    try { hist.replaceState(null, "", "?" + encodeUrlQuery(M.grade, state, M.slice)); } catch (e) { /* e.g. sandboxed file:// */ }
  }
  function setState(next) { state = next; syncUrl(); renderAll(); }

  function notice(text, kind) { ui.notice = text ? { text: text, kind: kind || "info" } : null; }
  function toast(text) {
    var id = ++ui.toastSeq;
    ui.toasts.push({ id: id, text: text });
    setT(function () { ui.toasts = ui.toasts.filter(function (t) { return t.id !== id; }); renderAll(); }, 7000);
  }

  /* ---- errors and writes ---- */
  function handleError(e) {
    if (!(e instanceof ApiError)) { toast("Something went wrong. " + (e && e.message ? e.message : "")); if (typeof console !== "undefined") console.error(e); renderAll(); return; }
    if (e.error === "stale_revision" && e.detail && e.detail.sequence) {
      M.seqView = e.detail.sequence; ui.prompt = null; ui.editing = null;
      notice(STALE_MESSAGE, "warn");
    } else if (e.error === "confirm_off_grade_required" && lastPlace) {
      ui.prompt = Object.assign({}, lastPlace, { keys: e.detail.source_keys || lastPlace.keys, notes: lastPlace.notes || {} });
      ui.railOpen = true;
    } else if (e.error === "already_placed") {
      toast("That node is already placed in this sequence.");
    } else if (e.error === "at_edge") {
      notice(e.message, "info");
    } else {
      toast(e.message || "Request failed");
    }
    renderAll();
  }
  /** Run one API write; on success adopt the returned SequenceView. */
  async function write(fn) {
    if (busy) return null;
    busy = true;
    ui.notice = null;
    try {
      var res = await fn();
      M.seqView = res.sequence;
      fixTarget();
      if (isOpen(M, "history", false)) await loadEvents(true);
      renderAll();
      return res;
    } catch (e) { handleError(e); return null; }
    finally { busy = false; }
  }
  function seqWrite(fn) {
    return write(function () { var s = seqOf(M); return fn(s.sequence_id, s.rev); });
  }
  async function loadEvents(reset) {
    var s = seqOf(M); if (!s) return;
    var r = await api.events(s.sequence_id, 50, reset ? undefined : ui.eventsNext);
    ui.events = reset ? r.events : ui.events.concat(r.events);
    ui.eventsNext = r.next_before;
  }

  /* ---- loading ---- */
  async function loadGrade(grade, query) {
    var res = await Promise.all([api.slice(grade), api.sequence(grade), api.listViews(grade)]);
    M.grade = grade; M.slice = res[0]; M.index = indexSlice(res[0]); M.seqView = res[1]; M.views = res[2].views || [];
    state = query !== undefined ? decodeState(query, M.slice) : defaultState(M.slice);
    M.drawer = null; M.compareData = null; nodeCache = {};
    ui.prompt = null; ui.editing = null; ui.selectedViewId = null; ui.viewMode = null; ui.events = []; ui.eventsNext = null;
    ui.targetModuleId = null; ui.guardTab = "seq"; ui.notice = null;
    fixTarget();
  }
  async function restoreSelection() {
    if (state.node) { try { await openNode(state.node, { focus: false, keepState: true }); } catch (e) { state.node = null; } }
    if (state.sheet && state.compare.length) { try { await loadCompare(); } catch (e) { state.sheet = false; } }
  }
  async function init() {
    try {
      try { M.whoami = await api.whoami(); } catch (e) { M.whoami = { user: "local", auth_enabled: false, source: "default" }; }
      M.gradesInfo = await api.grades();
      var want = decodeGrade(loc.search);
      var have = M.gradesInfo.grades.map(function (g) { return g.grade; });
      var pick = want && have.indexOf(want) >= 0 ? want
        : (api.isDemo ? api.grade : ((M.gradesInfo.grades.filter(function (g) { return g.sequence; })[0] || {}).grade || (have.indexOf("2") >= 0 ? "2" : have[0])));
      await loadGrade(pick, loc.search);
      renderAll();
      await restoreSelection();
      renderAll();
    } catch (e) {
      if (R.slice) R.slice.innerHTML = '<div class="fatal"><p><b>Could not load the sequencing tool.</b></p><p>' + esc(e && e.message ? e.message : e) + "</p>" +
        button("Try again", { action: "retry-boot" }, { cls: "tbtn primary" }) + "</div>";
      if (typeof console !== "undefined") console.error(e);
    }
  }

  /* ---- drawer and compare ---- */
  async function openNode(key, o) {
    o = o || {};
    var tok = ++tokNode;
    if (!o.keepState) { state = Object.assign({}, state, { node: key }); syncUrl(); }
    else state.node = key;
    ui.drawerReturn = key;
    var d = nodeCache[key];
    if (!d) {
      try { d = await api.node(key, M.grade); } catch (e) { state.node = null; M.drawer = null; syncUrl(); handleError(e); return; }
      nodeCache[key] = d;
    }
    if (tok !== tokNode) return;
    M.drawer = d; renderAll();
    if (o.focus !== false) focusKey("drawer-h");
  }
  function closeDrawer() {
    var key = state.node;
    tokNode += 1; M.drawer = null;
    setState(Object.assign({}, state, { node: null }));
    if (key) focusKey("chip:" + key);
  }
  async function loadCompare() {
    var tok = ++tokCmp;
    if (!state.sheet || !state.compare.length) { M.compareData = null; return; }
    var c = await api.compare(state.compare, M.grade);
    if (tok === tokCmp) { M.compareData = c; }
  }
  async function compareChanged() {
    syncUrl();
    if (state.sheet && state.compare.length) { try { await loadCompare(); } catch (e) { handleError(e); return; } } else M.compareData = null;
    renderAll();
  }

  /* ---- placing ---- */
  function startPlace(keys, o) {
    o = o || {};
    var seq = seqOf(M);
    if (!seq) { notice("Start the " + gradeLabel() + " sequence in the rail first.", "warn"); ui.railOpen = true; renderAll(); focusKey("start-seq"); return null; }
    var mod = o.moduleId ? findModule(o.moduleId) : targetModuleOf(M);
    if (!mod) { notice("Add a module first, then place nodes into it.", "warn"); ui.railOpen = true; renderAll(); focusKey("add-module"); return null; }
    var placed = placedOf(M), todo = keys.filter(function (k) { return !has(placed, k); });
    if (!todo.length) { notice(keys.length === 1 ? "That node is already placed in this sequence." : "All of these nodes are already placed.", "info"); renderAll(); return null; }
    var pr = { keys: todo, allKeys: keys, moduleId: mod.module_id, slotId: o.slotId || null, afterSlotId: o.afterSlotId || null, notes: {} };
    var needs = todo.some(function (k) { var n = M.index[k]; return n && (n.requires_confirm || (n.placed_elsewhere && n.placed_elsewhere.length)); });
    if (needs) {
      ui.prompt = pr; ui.railOpen = true; renderAll(); ui.scrollTo = { attr: "data-focus-key", value: "prompt" }; afterRender(); focusKey("prompt");
      return null;
    }
    return executePlace(pr, false);
  }
  async function executePlace(pr, withNote) {
    lastPlace = pr;
    var notes = {};
    if (withNote) pr.keys.forEach(function (k) { var t = pr.notes && pr.notes[k]; if (t && String(t).trim()) notes[k] = String(t).trim(); });
    var confirm = pr.keys.some(function (k) { return M.index[k] && M.index[k].requires_confirm; });
    var res = await seqWrite(function (sid, rev) {
      return pr.keys.length === 1
        ? api.place(sid, rev, pr.moduleId, pr.keys[0], { slot_id: pr.slotId, after_slot_id: pr.afterSlotId, differentiation_note: notes[pr.keys[0]], confirm_off_grade: confirm })
        : api.createSlotGroup(sid, rev, pr.moduleId, pr.keys, confirm, notes);
    });
    if (res) {
      ui.prompt = null;
      var ids = res.result.placement_ids || (res.result.placement_id ? [res.result.placement_id] : []);
      ui.flashIds = ids;
      var mod = findModule(pr.moduleId), names = pr.keys.map(function (k) { return M.index[k] ? M.index[k].node_id : k; }).join(", ");
      var skipped = (res.result.skipped || []).length;
      notice("Placed " + names + (mod ? " in M" + mod.position : "") + (skipped ? " (" + skipped + " skipped: already placed)" : "") + ".", "ok");
      renderAll();
    }
    return res;
  }

  /* ---- the action table: name -> {on: event type, fn} ---- */
  var ACTIONS = {};
  function on(type, name, fn) { ACTIONS[name] = { on: type, fn: fn }; }

  // toolbar
  on("click", "toggle", function (d) { var o = {}; o[d.id] = !state[d.id]; setState(Object.assign({}, state, o)); });
  on("click", "reset-view", function () { setState(applyLens(state, lensOf(defaultState(M.slice)), M.slice)); ui.selectedViewId = null; renderAll(); });
  on("click", "disclose", function (d) {
    ui.open[d.id] = !isOpen(M, d.id, d.id === "attention");
    var p = d.id === "history" && ui.open.history ? loadEvents(true) : null;
    renderAll();
    return p ? p.then(renderAll) : null;
  });
  on("click", "stems-expand-all", function () { setState(Object.assign({}, state, { collapsed_stems: [] })); });
  on("click", "stems-collapse-all", function () { setState(Object.assign({}, state, { collapsed_stems: Object.keys(sliceIds(M.slice).stems) })); });
  on("click", "show-all-hidden", function () { setState(Object.assign({}, state, { hidden_stems: [], hidden_cs: [] })); });
  on("change", "stem-visible", function (d) { setState(Object.assign({}, state, { hidden_stems: d.checked ? state.hidden_stems.filter(function (x) { return x !== d.id; }) : state.hidden_stems.concat([d.id]) })); });
  on("click", "cs-show", function (d) { setState(Object.assign({}, state, { hidden_cs: state.hidden_cs.filter(function (x) { return x !== d.id; }) })); });
  on("click", "cs-hide", function (d) { setState(Object.assign({}, state, { hidden_cs: toggleInList(state.hidden_cs, d.id) })); });
  on("click", "stem-hide", function (d) { setState(Object.assign({}, state, { hidden_stems: toggleInList(state.hidden_stems, d.id) })); });
  on("click", "stem-collapse", function (d) { setState(toggleCollapse(state, M.slice, d.id)); });
  on("click", "stem-move", function (d) { ui.focusOverride = "stem:" + d.id + (Number(d.val) < 0 ? ":up" : ":down"); setState(moveStem(state, M.slice, d.id, Number(d.val))); });
  on("click", "goal-toggle", function (d) { ui.goalOpen[d.id] = !ui.goalOpen[d.id]; renderAll(); });
  on("click", "goto-stem", function (d) {
    var next = Object.assign({}, state, { hidden_stems: state.hidden_stems.filter(function (x) { return x !== d.id; }) });
    var eff = effectiveCollapsed(next, M.slice); delete eff[d.id]; next.collapsed_stems = Object.keys(eff);
    ui.scrollTo = { attr: "data-stem", value: d.id };
    setState(next);
  });
  on("change", "pick-grade", async function (d) {
    var prev = M.grade;
    try { await loadGrade(d.value); } catch (e) { handleError(e); renderAll(); return; }
    syncUrl(); renderAll();
  });
  // saved views
  on("change", "pick-view", function (d) {
    var v = M.views.filter(function (x) { return String(x.view_id) === String(d.value); })[0];
    ui.selectedViewId = v ? v.view_id : null; ui.viewMode = null;
    if (v) setState(applyLens(state, v.state, M.slice)); else renderAll();
  });
  on("click", "view-save", function () { ui.viewMode = "save"; ui.viewDraft = ""; renderAll(); focusKey("view-name"); });
  on("click", "view-rename", function () { var v = M.views.filter(function (x) { return String(x.view_id) === String(ui.selectedViewId); })[0]; if (!v) return; ui.viewMode = "rename"; ui.viewDraft = v.name; renderAll(); focusKey("view-name"); });
  on("click", "view-delete", function () { ui.viewMode = "delete"; renderAll(); });
  on("click", "view-cancel", function () { ui.viewMode = null; renderAll(); });
  on("input", "view-name", function (d) { ui.viewDraft = d.value; });
  on("click", "view-commit", async function () {
    var name = String(ui.viewDraft || "").trim();
    if (!name) { toast("Give the view a name."); renderAll(); return; }
    try {
      if (ui.viewMode === "rename") {
        var u = await api.updateView(ui.selectedViewId, { name: name });
        M.views = M.views.map(function (v) { return v.view_id === u.view_id ? u : v; });
      } else {
        var c = await api.createView(M.grade, name, lensOf(state));
        M.views = M.views.concat([c]); ui.selectedViewId = c.view_id;
      }
      ui.viewMode = null; renderAll();
    } catch (e) {
      if (e instanceof ApiError && e.error === "view_name_exists") { toast("A view with that name exists."); renderAll(); } else handleError(e);
    }
  });
  on("click", "view-delete-yes", async function () {
    try { await api.deleteView(ui.selectedViewId); M.views = M.views.filter(function (v) { return v.view_id !== ui.selectedViewId; }); ui.selectedViewId = null; ui.viewMode = null; renderAll(); }
    catch (e) { handleError(e); }
  });
  // header
  on("click", "toggle-rail", function () { ui.railOpen = !ui.railOpen; renderAll(); });
  on("change", "set-user", async function (d) {
    var v = String(d.value || "").trim();
    try { if (store) { if (v) store.setItem("mh2seq-user", v); else store.removeItem("mh2seq-user"); } } catch (e) { /* ignore */ }
    ui.userName = v;
    try { M.whoami = await api.whoami(); } catch (e) { /* keep old */ }
    renderAll();
  });
  on("click", "reset-demo", async function () {
    if (!api.reset) return;
    api.reset(); await loadGrade(M.grade, ""); syncUrl(); notice("Demo reset to its starting data.", "info"); renderAll();
  });
  on("click", "retry-boot", function () { return init(); });
  on("click", "dismiss-notice", function () { notice(null); renderAll(); });
  on("click", "dismiss-toast", function (d) { ui.toasts = ui.toasts.filter(function (t) { return String(t.id) !== String(d.id); }); renderAll(); });

  // chips, drawer, compare
  on("click", "open-node", function (d) { if (d.id) return openNode(d.id); });
  on("click", "close-drawer", function () { closeDrawer(); });
  on("click", "toggle-all-fields", function () { ui.showAllFields = !ui.showAllFields; renderAll(); });
  on("click", "place-chip", function (d) { return startPlace([d.id]); });
  on("change", "drawer-module", function (d) { ui.targetModuleId = num(d.value); renderAll(); });
  on("click", "show-in-rail", function (d) {
    ui.railOpen = true; ui.flashIds = [num(d.id)]; renderAll(); focusKey("pl:" + d.id);
  });
  on("click", "toggle-compare", function (d) {
    var before = state.compare.length;
    var next = toggleCompare(state, d.id);
    if (before >= COMPARE_MAX && next.compare.length === before && next.compare.indexOf(d.id) < 0) { notice("Compare holds at most 4 nodes.", "info"); renderAll(); return; }
    state = next; return compareChanged();
  });
  on("click", "clear-compare", function () { state = Object.assign({}, state, { compare: [], sheet: false }); return compareChanged(); });
  on("click", "open-sheet", function () { state = Object.assign({}, state, { sheet: true }); return compareChanged(); });
  on("click", "close-sheet", function () { state = Object.assign({}, state, { sheet: false }); M.compareData = null; syncUrl(); renderAll(); focusKey("chip:" + (state.compare[0] || "")); });
  on("click", "cmp-remove", function (d) { state = toggleCompare(state, d.id); return compareChanged(); });
  on("change", "cmp-hide-empty", function (d) { ui.cmpHideEmpty = !!d.checked; renderAll(); });
  on("change", "cmp-shared-first", function (d) { ui.cmpSharedFirst = !!d.checked; renderAll(); });
  on("change", "cmp-module", function (d) { ui.targetModuleId = num(d.value); renderAll(); });
  on("click", "cmp-coplace", function () { return startPlace(state.compare.slice(), { moduleId: ui.targetModuleId }); });
  on("click", "sheet-grip", function () { /* drag handled by pointer events in boot; keyboard below */ });

  // prompt
  on("input", "prompt-note", function (d) { if (ui.prompt) ui.prompt.notes[d.id] = d.value; });
  on("click", "prompt-go", function (d) { if (ui.prompt) return executePlace(ui.prompt, d.val === "note"); });
  on("click", "prompt-cancel", function () { ui.prompt = null; renderAll(); });

  // rail: sequence
  on("click", "start-sequence", async function () {
    var r = await write(function () { return api.createSequence(M.grade, gradeLabel() + " sequence"); });
    if (r) {
      var s = seqOf(M);
      var r2 = await write(function () { return api.createModule(s.sequence_id, s.rev, "Module 1"); });
      if (r2) { ui.targetModuleId = r2.result.module_id; notice("Sequence started with Module 1. Use \u201c+ Place\u201d on any chip.", "ok"); renderAll(); }
    }
  });
  on("click", "seq-edit", function () { ui.editing = { kind: "seqtitle" }; renderAll(); focusKey("seqtitle"); });
  on("change", "seq-title", function (d) { ui.editing = null; var t = String(d.value || "").trim(); if (!t || t === seqOf(M).title) { renderAll(); return; } return seqWrite(function (sid, rev) { return api.updateSequence(sid, rev, { title: t }); }); });
  on("click", "export-csv", function () {
    var csv = sequenceToCsv(M.seqView), name = "grade" + M.grade + "_sequence.csv";
    ui.lastCsv = csv;
    if (typeof Blob === "undefined" || typeof URL === "undefined" || !URL.createObjectURL || !doc.createElement) return;
    try {
      var a = doc.createElement("a");
      a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); a.download = name;
      doc.body.appendChild(a); a.click(); doc.body.removeChild(a);
      notice("Downloaded " + name + ".", "ok"); renderAll();
    } catch (e) { toast("Could not start the download in this browser."); renderAll(); }
  });
  on("click", "guard-tab", function (d) { ui.guardTab = d.id; renderAll(); });
  on("click", "add-module", async function () {
    var n = ((M.seqView && M.seqView.modules) || []).length + 1;
    var r = await seqWrite(function (sid, rev) { return api.createModule(sid, rev, "Module " + n); });
    if (r) { ui.targetModuleId = r.result.module_id; ui.editing = { kind: "module", id: r.result.module_id }; ui.railOpen = true; renderAll(); focusKey("modtitle:" + r.result.module_id); }
  });
  // rail: modules
  on("click", "module-edit", function (d) { ui.editing = { kind: "module", id: num(d.id) }; renderAll(); focusKey("modtitle:" + d.id); });
  on("dblclick", "module-edit-dbl", function (d) { ui.editing = { kind: "module", id: num(d.id) }; renderAll(); focusKey("modtitle:" + d.id); });
  on("change", "module-title", function (d) {
    ui.editing = null; var t = String(d.value || "").trim(); var cur = findModule(num(d.id));
    ui.focusOverride = "modedit:" + d.id;
    if (!t || (cur && cur.title === t)) { renderAll(); return; }
    return seqWrite(function (sid, rev) { return api.updateModule(num(d.id), rev, { title: t }); });
  });
  on("click", "module-move", function (d) { ui.focusOverride = "module:" + d.id + ":" + d.val; return seqWrite(function (sid, rev) { return api.moveModule(num(d.id), rev, d.val); }); });
  on("click", "module-remove", function (d) { return seqWrite(function (sid, rev) { return api.removeModule(num(d.id), rev); }); });
  on("change", "target-module", function (d) { ui.targetModuleId = num(d.id); renderAll(); });
  // rail: slots
  on("click", "slot-move", function (d) { if (!ui.focusOverride) ui.focusOverride = "slot:" + d.id + ":" + d.val; return seqWrite(function (sid, rev) { return api.moveSlot(num(d.id), rev, { direction: d.val }); }); });
  on("change", "slot-move-to", function (d) {
    if (!d.value) return;
    ui.focusOverride = "slot:" + d.id;
    return seqWrite(function (sid, rev) { return api.moveSlot(num(d.id), rev, { to_module_id: num(d.value) }); });
  });
  on("click", "slot-merge", function (d) {
    var flat = flatSlots(), i = flat.map(function (s) { return s.slot_id; }).indexOf(num(d.id));
    if (i <= 0) return;
    ui.focusOverride = "slot:" + flat[i - 1].slot_id;
    return seqWrite(function (sid, rev) { return api.mergeSlot(num(d.id), rev, flat[i - 1].slot_id); });
  });
  on("click", "slot-label-edit", function (d) { ui.editing = { kind: "slot", id: num(d.id) }; renderAll(); focusKey("slotlabel:" + d.id); });
  on("change", "slot-label", function (d) {
    var f = findSlot(num(d.id)), t = String(d.value || "").trim() || null;
    ui.editing = null; ui.focusOverride = "slot:" + d.id;
    if (f && (f.slot.label || null) === t) { renderAll(); return; }
    return seqWrite(function (sid, rev) { return api.updateSlot(num(d.id), rev, t); });
  });
  // rail: placements
  on("click", "set-cal", function (d) {
    var f = findPlacement(num(d.id)); if (!f) return;
    var v = d.val || null; if (v === f.placement.calibration) return;
    return write(function () { return api.updatePlacement(f.placement.placement_id, f.placement.rev, { calibration: v }); });
  });
  function setPeriod(id, raw) {
    var f = findPlacement(num(id)); if (!f) return;
    var v = raw === "" || raw === null || raw === undefined ? null : Number(raw);
    if (v !== null && (isNaN(v) || v < 0 || v > 200)) { toast("Periods must be a number from 0 to 200."); renderAll(); return; }
    if (v === f.placement.period_estimate) return;
    return write(function () { return api.updatePlacement(f.placement.placement_id, f.placement.rev, { period_estimate: v }); });
  }
  on("change", "set-period", function (d) { return setPeriod(d.id, d.value); });
  on("click", "use-hint", function (d) { return setPeriod(d.id, d.val); });
  on("click", "note-edit", function (d) { var f = findPlacement(num(d.id)); ui.editing = { kind: "note", id: num(d.id) }; ui.noteDraft[d.id] = f ? (f.placement.differentiation_note || "") : ""; renderAll(); focusKey("note:" + d.id); });
  on("input", "note-text", function (d) { ui.noteDraft[d.id] = d.value; });
  on("click", "note-cancel", function () { var e = ui.editing; ui.editing = null; if (e) delete ui.noteDraft[e.id]; renderAll(); });
  on("click", "note-save", function (d) {
    var f = findPlacement(num(d.id)); if (!f) return;
    var t = String(ui.noteDraft[d.id] === undefined ? (f.placement.differentiation_note || "") : ui.noteDraft[d.id]).trim() || null;
    ui.editing = null; delete ui.noteDraft[d.id]; ui.focusOverride = "noteb:" + d.id;
    if (t === (f.placement.differentiation_note || null)) { renderAll(); return; }
    return write(function () { return api.updatePlacement(f.placement.placement_id, f.placement.rev, { differentiation_note: t }); });
  });
  on("click", "ungroup", function (d) { return seqWrite(function (sid, rev) { return api.ungroup(num(d.id), rev); }); });
  on("change", "coplace-to", function (d) {
    if (!d.value) return;
    return seqWrite(function (sid, rev) { return api.coPlace(num(d.id), rev, num(d.value)); });
  });
  on("click", "remove-placement", function (d) {
    if (ui.confirmRemove !== num(d.id)) { ui.confirmRemove = num(d.id); renderAll(); return; }
    ui.confirmRemove = null;
    return seqWrite(function (sid, rev) { return api.removePlacement(num(d.id), rev, null); });
  });
  on("click", "remove-direct", function (d) { return seqWrite(function (sid, rev) { return api.removePlacement(num(d.id), rev, null); }); });
  on("click", "reattach", function (d) { return seqWrite(function (sid, rev) { return api.reattach(num(d.id), rev, d.key); }); });
  on("click", "acknowledge", function (d) { return seqWrite(function (sid, rev) { return api.acknowledge(num(d.id), rev); }); });
  on("click", "history-more", async function () { await loadEvents(false); renderAll(); });

  /** Run a named action. Used by the DOM layer and directly by tests. */
  function act(name, data) {
    var spec = ACTIONS[name];
    if (!spec) return Promise.resolve(null);
    data = data || {};
    if (data.id !== undefined && data.id !== null) data.id = String(data.id);
    if (data.val !== undefined && data.val !== null) data.val = String(data.val);
    try { return Promise.resolve(spec.fn(data)).catch(handleError); } catch (e) { handleError(e); return Promise.resolve(null); }
  }

  /* ---- keyboard ---- */
  /** Esc closes the topmost thing: an open edit or menu, the prompt, the drawer, then the sheet. */
  function escapeKey() {
    if (ui.editing) { ui.editing = null; renderAll(); return true; }
    if (ui.viewMode) { ui.viewMode = null; renderAll(); return true; }
    if (ui.prompt) { ui.prompt = null; renderAll(); return true; }
    if (ui.open.stemsmenu) { ui.open.stemsmenu = false; renderAll(); return true; }
    if (state.node) { closeDrawer(); return true; }
    if (state.sheet) { act("close-sheet"); return true; }
    return false;
  }
  function onKeydown(ev) {
    var k = ev.key;
    if (k === "Escape") { if (escapeKey() && ev.preventDefault) ev.preventDefault(); return; }
    var t = ev.target, actEl = t && t.closest ? t.closest("[data-action]") : null;
    if (ev.altKey && (k === "ArrowUp" || k === "ArrowDown")) {
      var card = t && t.closest ? t.closest("[data-kind]") : null;
      if (!card) return;
      if (ev.preventDefault) ev.preventDefault();
      var kind = card.getAttribute("data-kind"), id = card.getAttribute("data-id");
      ui.focusOverride = kind + ":" + id;
      act(kind === "slot" ? "slot-move" : "module-move", { id: id, val: k === "ArrowUp" ? "up" : "down" });
      return;
    }
    if (k === "Enter" && actEl && actEl.getAttribute("data-action") === "view-name") { if (ev.preventDefault) ev.preventDefault(); act("view-commit"); return; }
    if (k === "Enter" && actEl && ["module-title", "slot-label", "seq-title"].indexOf(actEl.getAttribute("data-action")) >= 0) {
      if (ev.preventDefault) ev.preventDefault();
      act(actEl.getAttribute("data-action"), { id: actEl.getAttribute("data-id"), value: actEl.value });
      return;
    }
    if (actEl && actEl.getAttribute("data-action") === "sheet-grip" && (k === "ArrowUp" || k === "ArrowDown")) {
      if (ev.preventDefault) ev.preventDefault();
      ui.sheetH = Math.max(20, Math.min(80, ui.sheetH + (k === "ArrowUp" ? 5 : -5))); renderAll();
    }
  }
  function onEvent(type, ev) {
    if (type === "keydown") return onKeydown(ev);
    var t = ev.target, el = t && t.closest ? t.closest("[data-action]") : null;
    if (!el) return;
    var name = el.getAttribute("data-action"), spec = ACTIONS[name];
    if (!spec || spec.on !== type) return;
    act(name, { id: el.getAttribute("data-id"), val: el.getAttribute("data-val"), key: el.getAttribute("data-key"),
      value: el.value, checked: el.checked, el: el });
  }
  function bind() {
    ["hdr", "toolbar", "rail", "slice", "drawer", "sheet", "tray", "notice", "toasts"].forEach(function (id) {
      var el = R[id]; if (!el || !el.addEventListener) return;
      ["click", "change", "input", "keydown", "dblclick"].forEach(function (type) { el.addEventListener(type, function (ev) { onEvent(type, ev); }); });
    });
    if (doc.addEventListener) {
      doc.addEventListener("keydown", function (ev) { if (ev.key === "Escape") onKeydown(ev); });
      doc.addEventListener("mousedown", function () { pointerDown = true; }, true);
      doc.addEventListener("mouseup", function () {
        pointerDown = false;
        if (renderPending) setT(function () { if (!pointerDown && renderPending) { renderPending = false; renderAll(); } }, 0);
      }, true);
    }
    if (R.sheet && R.sheet.addEventListener) {
      R.sheet.addEventListener("pointerdown", function (ev) {
        var g = ev.target && ev.target.closest ? ev.target.closest(".sheet-grip") : null;
        if (!g || typeof window === "undefined") return;
        ev.preventDefault();
        function move(e) { ui.sheetH = Math.max(20, Math.min(80, Math.round(100 * (window.innerHeight - e.clientY) / window.innerHeight))); if (R.sheet.style && R.sheet.style.setProperty) R.sheet.style.setProperty("--sheet-h", ui.sheetH + "vh"); }
        function up() { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); }
        window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
      });
    }
  }

  bind();
  return { init: init, act: act, api: api, M: M, ui: ui, getState: function () { return state; }, setState: setState,
    renderAll: renderAll, openNode: openNode, escapeKey: escapeKey, onEvent: onEvent, loadGrade: loadGrade, regions: R };
}

/* ========================================================================
 * 7. boot
 * ===================================================================== */

/** Pick the API: embedded demo payload if present, else the server. */
function boot(env0) {
  var env = env0 || {};
  var doc = env.doc || document;
  var el = doc.getElementById("seq-demo-data");
  var api = env.api, demoSource = null, demoGen = null;
  if (!api) {
    if (el) {
      var payload = JSON.parse(el.textContent);
      api = new DemoApi(payload, env.storage === undefined ? safeStorage() : env.storage);
      demoSource = payload.source || null; demoGen = payload.generated_at || null;
    } else {
      api = new HttpApi(new URL("../api/seq/", env.href || location.href));
    }
  }
  var app = createApp({ doc: doc, api: api, loc: env.loc || (typeof location !== "undefined" ? location : { search: "" }),
    hist: env.hist || (typeof history !== "undefined" ? history : null), storage: env.storage, setTimeout: env.setTimeout,
    demoSource: demoSource, demoGeneratedAt: demoGen });
  app.init();
  return app;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { esc, encodeState, decodeState, defaultState, computeGuardrail, assembleCompare,
    visibleSlice, DemoApi, HttpApi, ApiError, renderSlice, renderDrawer,
    renderRail, renderSheet, renderGuardrail, renderAttention, renderToolbar,
    // extras used by the tests
    encodeUrlQuery, decodeGrade, lensOf, applyLens, toggleCompare, toggleCollapse, moveStem, effectiveCollapsed,
    compareRowsView, sequenceToCsv, csvCell, indexSlice, hintReason, orderAppend, orderBetween, renumber, roundHalfUp,
    renderChip, renderHeader, renderTray, renderPrompt, renderPlacement, renderGuardrailBars, renderNotice, renderFooter,
    createApp, boot, safeStorage, sliceIds, lensIsDefault, stemOrderSort, kindLabel, COMPARE_MAX };
} else { document.addEventListener("DOMContentLoaded", function () { boot(); }); }
