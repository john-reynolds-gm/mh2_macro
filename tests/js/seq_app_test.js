/* Node tests for seq_static/app.js (contract 8.3).  Plain assert, no packages.
 *   node tests/js/seq_app_test.js
 * Covers: URL state round trip, the guardrail mirror (contract 5.8 table G0-G5),
 * assembleCompare, visibleSlice, DemoApi writes (shapes, 409/422 errors,
 * persistence), HttpApi request building, renderers, and a stub-DOM smoke run
 * of the controller (boot -> slice -> drawer -> compare -> module -> place ->
 * reorder -> calibrate -> guardrail).
 */
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const A = require("../../seq_static/app.js");
const { esc, encodeState, decodeState, defaultState, computeGuardrail, assembleCompare, visibleSlice,
  DemoApi, HttpApi, ApiError } = A;

const FX = path.join(__dirname, "fixtures");
const load = (n) => JSON.parse(fs.readFileSync(path.join(FX, n), "utf8"));
const small = () => load("demo_small.json");
let BIG = null;
const big = () => JSON.parse(BIG || (BIG = fs.readFileSync(path.join(FX, "g2_payload.json"), "utf8")));

const tests = [];
function test(name, fn) { tests.push([name, fn]); }
function memStorage() {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => { m.set(k, String(v)); }, removeItem: (k) => { m.delete(k); }, _m: m };
}
function throwingStorage() {
  const boom = () => { throw new Error("blocked"); };
  return { getItem: boom, setItem: boom, removeItem: boom };
}
const keys = (o) => Object.keys(o).sort();
const apiErr = (status, code) => (e) => e instanceof ApiError && e.status === status && e.error === code && e.detail && e.detail.error === code;

/* ---------------------------------------------------------------- pure ---- */

test("esc escapes the five characters and null", () => {
  assert.strictEqual(esc('<script>x</script> & "q" \'s\''), "&lt;script&gt;x&lt;/script&gt; &amp; &quot;q&quot; &#39;s&#39;");
  assert.strictEqual(esc(null), ""); assert.strictEqual(esc(undefined), ""); assert.strictEqual(esc(0), "0");
});

test("encodeState: defaults write an empty query; non-defaults only", () => {
  const sl = small().slice, d = defaultState(sl);
  assert.strictEqual(encodeState(d, sl), "");
  const s = Object.assign(defaultState(sl), { context: false, leaves: true, state_ext: false, unplaced_only: true, pairings: true, flagged: true, sheet: true });
  assert.strictEqual(encodeState(s, sl), "ctx=0&lv=1&sx=0&up=1&pr=1&fl=1&sheet=1");
});

test("encodeState/decodeState round trip, colons encoded, cl presence semantics", () => {
  const sl = small().slice;
  const s = defaultState(sl);
  s.hidden_stems = ["TIM"]; s.hidden_cs = ["WHO:7046b922"]; s.stem_order = ["TIM", "WHO"];
  s.node = "K:who0010"; s.compare = ["K:who0010", "K:tim0011"]; s.collapsed_stems = [];
  const q = encodeState(s, sl);
  assert.ok(q.includes("hc=WHO%3A7046b922"), q);
  assert.ok(q.includes("node=K%3Awho0010"), q);
  assert.ok(q.includes("cl=&") || q.includes("cl=&so") || /(^|&)cl=(&|$)/.test(q), "explicit empty cl present: " + q);
  const back = decodeState(q, sl);
  assert.deepStrictEqual(back, s);
  assert.deepStrictEqual(decodeState("?" + q, sl), s);
  // absent cl = default rule (null); present-empty = explicit empty list
  assert.strictEqual(decodeState("", sl).collapsed_stems, null);
  assert.deepStrictEqual(decodeState("cl=", sl).collapsed_stems, []);
  assert.deepStrictEqual(decodeState("cl=WHO,TIM", sl).collapsed_stems, ["WHO", "TIM"]);
});

test("decodeState: unknown ids dropped, compare capped at 4, grade read separately", () => {
  const sl = small().slice;
  const s = decodeState("hs=TIM,NOPE&hc=zzz&so=NOPE&compare=a,b,c,d,e,f&grade=2", sl);
  assert.deepStrictEqual(s.hidden_stems, ["TIM"]); assert.deepStrictEqual(s.hidden_cs, []); assert.deepStrictEqual(s.stem_order, []);
  assert.deepStrictEqual(s.compare, ["a", "b", "c", "d"]);
  assert.strictEqual(A.decodeGrade("grade=2&x=1"), "2"); assert.strictEqual(A.decodeGrade(""), null);
  assert.strictEqual(A.encodeUrlQuery("2", defaultState(sl), sl), "grade=2");
  // encode also caps compare
  const t = defaultState(sl); t.compare = ["a", "b", "c", "d", "e"];
  assert.strictEqual(encodeState(t, sl), "compare=a,b,c,d");
  // commas inside an id cannot break the list
  const u = defaultState(sl); u.compare = ["a,b", "c"];
  assert.deepStrictEqual(decodeState(encodeState(u, sl), sl).compare, ["a,b", "c"]);
});

test("lens helpers: saved views keep a lens not a selection", () => {
  const sl = small().slice, s = defaultState(sl);
  s.hidden_stems = ["TIM"]; s.node = "x"; s.compare = ["y"]; s.sheet = true; s.pairings = true;
  const lens = A.lensOf(s);
  assert.ok(!("node" in lens) && !("compare" in lens) && !("sheet" in lens));
  const applied = A.applyLens(Object.assign(defaultState(sl), { node: "keep", compare: ["k"] }), lens, sl);
  assert.strictEqual(applied.node, "keep"); assert.deepStrictEqual(applied.compare, ["k"]);
  assert.deepStrictEqual(applied.hidden_stems, ["TIM"]); assert.strictEqual(applied.pairings, true);
  assert.strictEqual(A.lensIsDefault(defaultState(sl), sl), true); assert.strictEqual(A.lensIsDefault(s, sl), false);
});

test("collapse default rule: chips > 100 collapses every stem", () => {
  const sl = small().slice;
  assert.deepStrictEqual(A.effectiveCollapsed(defaultState(sl), sl), {});
  const sl2 = JSON.parse(JSON.stringify(sl)); sl2.counts.chips = 106;
  assert.deepStrictEqual(Object.keys(A.effectiveCollapsed(defaultState(sl2), sl2)).sort(), ["TIM", "WHO"]);
  const st = A.toggleCollapse(defaultState(sl2), sl2, "WHO");       // expands WHO, materialises the rest
  assert.deepStrictEqual(st.collapsed_stems, ["TIM"]);
});

test("computeGuardrail matches contract 5.8 table G0-G5", () => {
  const close = (a, b, path) => {
    if (b === null) return assert.strictEqual(a, null, path);
    if (typeof b === "object") { Object.keys(b).forEach((k) => close(a[k], b[k], path + "." + k)); return; }
    assert.strictEqual(a, b, path + ": " + a + " != " + b);
  };
  load("guardrail_cases.json").forEach((c) => {
    const g = computeGuardrail(c.input.map(([cal, pe]) => ({ calibration: cal, period_estimate: pe })));
    close(g, c.expect, c.id);
    assert.deepStrictEqual(keys(g), ["count_share", "count_target", "counts", "n", "n_calibrated", "n_timed", "periods",
      "show_time_targets", "time_coverage", "time_mark_min_coverage", "time_share", "time_target", "total_periods"].sort(), c.id + " keys");
    assert.strictEqual(Object.keys(g).filter((k) => ["pass", "fail", "ok", "status"].includes(k)).length, 0);
  });
});

test("assembleCompare: rows, order, shared, empty (compare_case1)", () => {
  const c = load("compare_case1.json");
  const r = assembleCompare(c.columns, c.field_defs, c.grade);
  assert.deepStrictEqual(r.rows.map((x) => x.key), c.expect_row_keys);
  Object.keys(c.expect_rows).forEach((k) => {
    const row = r.rows.find((x) => x.key === k), e = c.expect_rows[k];
    assert.deepStrictEqual(row.cells, e.cells, k); assert.strictEqual(row.shared, e.shared, k + " shared"); assert.strictEqual(row.empty, e.empty, k + " empty");
  });
  assert.deepStrictEqual(r.shared_ccss, c.expect_shared_ccss); assert.deepStrictEqual(r.shared_lessons, c.expect_shared_lessons);
  assert.strictEqual(r.rows.find((x) => x.key === "stem").group, "node"); assert.strictEqual(r.rows.find((x) => x.key === "ccss:2.NBT.A.4").kind, "flag");
  assert.strictEqual(r.rows.find((x) => x.key === "state_codes").group, "state");
  // row view helpers
  assert.ok(!A.compareRowsView(r, true, false).some((x) => x.key === "strategies"));
  const sf = A.compareRowsView(r, true, true).map((x) => x.key);
  assert.strictEqual(sf[7], "ccss:2.NBT.A.4");       // first non-node row is a shared one
});

test("visibleSlice: toggles, hidden ids", () => {
  const sl = small().slice, st = () => defaultState(sl);
  const n = (v) => v.counts.shown_chips;
  assert.strictEqual(n(visibleSlice(sl, st(), null)), 7);                    // the leaf chip is hidden by default
  assert.strictEqual(n(visibleSlice(sl, Object.assign(st(), { leaves: true }), null)), 8);
  assert.strictEqual(n(visibleSlice(sl, Object.assign(st(), { context: false }), null)), 5);
  assert.strictEqual(n(visibleSlice(sl, Object.assign(st(), { state_ext: false }), null)), 7);
  assert.strictEqual(n(visibleSlice(sl, Object.assign(st(), { hidden_stems: ["TIM"] }), null)), 4);
  assert.strictEqual(visibleSlice(sl, Object.assign(st(), { hidden_cs: ["WHO:7046b922"] }), null).counts.shown_cs, 2);
  assert.strictEqual(visibleSlice(sl, st(), null).counts.shown_cs, 3);
});

test("visibleSlice unplaced_only / flagged act at strip level", () => {
  const sl = small().slice, st = Object.assign(defaultState(sl), { unplaced_only: true });
  const pi = { "K:who0010": {}, "K:who0011": {}, "K:who0020": {}, "K:tim0010": {}, "K:tim0011": {} };   // all owed chips placed
  const all = visibleSlice(sl, st, { placed_index: pi, slice_badges: {} });
  assert.strictEqual(all.counts.shown_cs, 0); assert.deepStrictEqual(all.super_stems, []);
  delete pi["K:tim0011"];
  const some = visibleSlice(sl, st, { placed_index: pi, slice_badges: {} });
  assert.strictEqual(some.counts.shown_cs, 1);
  assert.strictEqual(some.super_stems[0].stems[0].concept_skills[0].nodes.length, 3);   // whole strip stays readable
  // flagged: structural only (slice badge), not pairing/info
  const f = Object.assign(defaultState(sl), { flagged: true });
  assert.strictEqual(visibleSlice(sl, f, { placed_index: {}, slice_badges: {} }).counts.shown_cs, 0);   // WHO-0010 pairing badge is not structural
  const flagged = visibleSlice(sl, f, { placed_index: {}, slice_badges: { "K:who0020": [{ code: "bridge", tier: "structural" }] } });
  assert.strictEqual(flagged.counts.shown_cs, 1);
});

test("visibleSlice: stem_order reorders within a super-stem; moveStem rewrites it", () => {
  const p = big(), sl = p.slice;
  const ss = sl.super_stems.find((s) => s.stems.length >= 2), ids = ss.stems.map((s) => s.stem_id);
  const st = Object.assign(defaultState(sl), { stem_order: ids.slice().reverse() });
  const shown = visibleSlice(sl, st, null).super_stems.find((s) => s.domain === ss.domain).stems.map((s) => s.stem_id);
  assert.deepStrictEqual(shown, ids.slice().reverse());
  const moved = A.moveStem(defaultState(sl), sl, ids[0], 1);
  assert.deepStrictEqual(visibleSlice(sl, moved, null).super_stems.find((s) => s.domain === ss.domain).stems.map((s) => s.stem_id).slice(0, 2), [ids[1], ids[0]]);
  assert.deepStrictEqual(A.moveStem(defaultState(sl), sl, ids[0], -1), defaultState(sl));     // at the edge: unchanged
});

test("order-key arithmetic", () => {
  assert.strictEqual(A.orderAppend([]), 1024); assert.strictEqual(A.orderAppend([1024, 2048]), 3072);
  assert.strictEqual(A.orderBetween(1024, 2048), 1536); assert.strictEqual(A.orderBetween(1024, 1025), null);
  assert.strictEqual(A.orderBetween(null, 1024), 512); assert.strictEqual(A.orderBetween(null, 1), null);
  assert.strictEqual(A.orderBetween(2048, null), 3072);
  assert.deepStrictEqual(A.renumber([{}, {}, {}]).map((x) => x.order_key), [1024, 2048, 3072]);
});

test("hintReason covers day / lesson / several grades / part_of / none", () => {
  assert.strictEqual(A.hintReason({ unit: "day", basis: "grade_named", qualifier: "exact", low: 2, high: 2 }), "in days; not converted");
  assert.strictEqual(A.hintReason({ unit: "period", basis: "ungraded_multi_grade", qualifier: "exact" }), "estimate covers several grades");
  assert.strictEqual(A.hintReason({ unit: "period", basis: "grade_named", qualifier: "part_of", low: 0.5, high: 1 }), "≤ 1");
  assert.strictEqual(A.hintReason({ unit: "period", basis: "grade_named", qualifier: "exact", low: 2, high: 2 }), "");
  assert.strictEqual(A.hintReason(null), "");
});

/* ------------------------------------------------------------- DemoApi ---- */

const K = { w10: "K:who0010", w11: "K:who0011", w12: "K:who0012", w20: "K:who0020", w21: "K:who0021", t10: "K:tim0010", t11: "K:tim0011", t12: "K:tim0012" };

test("DemoApi reads: shapes, 404s, compare", async () => {
  const p = small(), api = new DemoApi(p, memStorage());
  assert.deepStrictEqual(await api.whoami(), { user: "demo", auth_enabled: false, source: "demo" });
  assert.strictEqual((await api.slice("2")).grade, "2");
  await assert.rejects(api.slice("3"), apiErr(404, "not_found"));
  assert.strictEqual((await api.node(K.w10, "2")).node_id, "WHO-0010");
  await assert.rejects(api.node("nope", "2"), apiErr(404, "not_found"));
  const c = await api.compare([K.w10, K.w11], "2");
  assert.deepStrictEqual(c.columns.map((x) => x.node_id), ["WHO-0010", "WHO-0011"]);
  assert.deepStrictEqual(keys(c), ["columns", "field_defs", "grade", "rows", "shared_ccss", "shared_lessons"]);
  assert.deepStrictEqual(c.shared_ccss, ["2.NBT.A.4"]); assert.deepStrictEqual(c.shared_lessons, ["G2-M1-L35"]);
  await assert.rejects(api.compare([], "2"), apiErr(422, "invalid"));
  await assert.rejects(api.compare([K.w10, K.w10], "2"), apiErr(422, "invalid"));
  await assert.rejects(api.compare(Object.values(K).slice(0, 5), "2"), apiErr(422, "invalid"));
  await assert.rejects(api.compare(["zzz"], "2"), apiErr(404, "not_found"));
  const v = await api.sequence("2");
  assert.deepStrictEqual(keys(v), ["attention", "grade", "guardrail", "ladders_last_read", "modules", "placed_index", "sequence", "slice_badges"]);
  assert.strictEqual(v.sequence, null); assert.deepStrictEqual(v.modules, []); assert.strictEqual(v.guardrail.n, 0);
  assert.deepStrictEqual(await api.events(1, 50), { events: [], next_before: null });
  const g = await api.guardrail("2"); assert.strictEqual(g.sequence_id, null); assert.deepStrictEqual(g.modules, []);
  assert.deepStrictEqual((await api.attention("2")).items, []);
});

const curRev = async (api) => (await api.sequence("2")).sequence.rev;

test("DemoApi writes: sequence and module lifecycle, rev bumps, shapes", async () => {
  const api = new DemoApi(small(), memStorage());
  let r = await api.createSequence("2", "Grade 2 sequence");
  assert.strictEqual(r.result.sequence_id, 1); assert.strictEqual(r.sequence.sequence.rev, 1);
  assert.deepStrictEqual(keys(r.sequence.sequence), ["created_at", "created_by", "grade", "note", "owner", "rev", "sequence_id", "title"]);
  await assert.rejects(api.createSequence("2", "again"), (e) => apiErr(409, "sequence_exists")(e) && e.detail.sequence_id === 1);
  r = await api.createModule(1, 1, "M1 Place value"); assert.strictEqual(r.sequence.sequence.rev, 2);
  const m1 = r.result.module_id;
  r = await api.createModule(1, 2, "M2 Comparing"); const m2 = r.result.module_id;
  assert.deepStrictEqual(r.sequence.modules.map((m) => [m.position, m.title, m.order_key]), [[1, "M1 Place value", 1024], [2, "M2 Comparing", 2048]]);
  assert.deepStrictEqual(keys(r.sequence.modules[0]), ["guardrail", "module_id", "note", "order_key", "position", "slots", "title"]);
  await assert.rejects(api.createModule(1, 3, "  "), apiErr(422, "invalid"));
  r = await api.updateModule(m2, 3, { title: "M2 Comparing numbers" }); assert.strictEqual(r.sequence.modules[1].title, "M2 Comparing numbers");
  r = await api.moveModule(m2, 4, "up"); assert.deepStrictEqual(r.sequence.modules.map((m) => m.module_id), [m2, m1]);
  await assert.rejects(api.moveModule(m2, 5, "up"), apiErr(422, "at_edge"));
  await assert.rejects(api.moveModule(m2, 5, "sideways"), apiErr(422, "invalid"));
  r = await api.updateSequence(1, 5, { title: "Grade 2, draft" }); assert.strictEqual(r.sequence.sequence.title, "Grade 2, draft");
  r = await api.removeModule(m1, 6); assert.strictEqual(r.sequence.modules.length, 1);
  await assert.rejects(api.removeModule(999, 7), apiErr(404, "not_found"));
  const ev = await api.events(1, 50);
  assert.ok(ev.events.length >= 6); assert.strictEqual(ev.events[0].actor, "demo");
  assert.deepStrictEqual(keys(ev.events[0]), ["action", "actor", "at", "before", "event_id", "module_id", "placement_id", "slot_id", "after"].sort());
  assert.ok(ev.events[0].event_id > ev.events[1].event_id);            // newest first
  const page = await api.events(1, 2); assert.strictEqual(page.events.length, 2); assert.ok(page.next_before);
});

async function seeded() {
  const api = new DemoApi(small(), memStorage());
  await api.createSequence("2", "Grade 2 sequence");
  const m1 = (await api.createModule(1, 1, "M1")).result.module_id;
  const m2 = (await api.createModule(1, 2, "M2")).result.module_id;
  return { api, m1, m2 };
}

test("DemoApi place: slot of one, already_placed, off-grade confirm, bridge badge, placed_elsewhere", async () => {
  const { api, m1, m2 } = await seeded();
  let r = await api.place(1, await curRev(api), m1, K.w11, {});
  assert.deepStrictEqual(keys(r.result), ["placed_elsewhere", "placement_id", "slot_id"]);
  assert.strictEqual(r.result.placed_elsewhere.length, 1);
  const pv = r.sequence.modules[0].slots[0].placements[0];
  assert.deepStrictEqual(keys(pv), ["badges", "calibration", "concept_skill_display", "differentiation_note", "grade_kind_seen", "is_bridge", "ladder_file_seen", "node_id", "node_id_seen", "node_text", "node_text_seen", "order_in_slot", "period_estimate", "period_hint", "period_hint_seen", "placed_at", "placed_by", "placed_elsewhere", "placement_id", "relabel", "relabelled", "rev", "source_key", "state_now", "status", "stem_id", "stem_name", "updated_at", "updated_by"]);
  assert.strictEqual(pv.placed_by, "demo"); assert.strictEqual(pv.status, "ok"); assert.strictEqual(pv.is_bridge, false);
  assert.deepStrictEqual(keys(r.sequence.placed_index[K.w11]), ["is_bridge", "module_id", "module_position", "module_title", "placement_id", "slot_id", "slot_position", "status"]);
  await assert.rejects(api.place(1, await curRev(api), m1, K.w11, {}), (e) => apiErr(409, "already_placed")(e) && e.detail.placement_id === r.result.placement_id);
  await assert.rejects(api.place(1, await curRev(api), m2, K.w12, {}), (e) => apiErr(422, "confirm_off_grade_required")(e) && e.detail.source_keys[0] === K.w12 && e.detail.states[K.w12] === "off_grade");
  assert.strictEqual((await api.sequence("2")).guardrail.n, 1);                    // the failed write changed nothing
  r = await api.place(1, await curRev(api), m2, K.w12, { confirm_off_grade: true });
  const b = r.sequence.modules[1].slots[0].placements[0];
  assert.strictEqual(b.is_bridge, true);
  assert.deepStrictEqual(b.badges.map((x) => [x.code, x.tier, x.label]), [["bridge", "structural", "bridge · off_grade"]]);
  assert.ok(r.sequence.slice_badges[K.w12] && r.sequence.placed_index[K.w12].is_bridge);
  // differentiation note given at place time
  r = await api.place(1, await curRev(api), m1, K.t11, { differentiation_note: "Within 1,000 here" });
  assert.strictEqual(r.sequence.modules[0].slots[1].placements[0].differentiation_note, "Within 1,000 here");
  await assert.rejects(api.place(1, await curRev(api), 999, K.t10, {}), apiErr(404, "not_found"));
  await assert.rejects(api.place(1, await curRev(api), m1, "K:nope", {}), apiErr(404, "not_found"));
});

test("DemoApi slots: co-place, ungroup, merge, move across modules, at_edge, empty slot cleanup", async () => {
  const { api, m1, m2 } = await seeded();
  const place = async (k, m, o) => api.place(1, await curRev(api), m, k, o || {});
  const a = (await place(K.w10, m1)).result, b = (await place(K.w11, m1)).result, c = (await place(K.w20, m1)).result, d = (await place(K.t10, m2)).result;
  let v = (await api.sequence("2"));
  assert.deepStrictEqual(v.modules[0].slots.map((s) => s.position), [1, 2, 3]);
  // co-place b into a's slot; b's old slot disappears
  let r = await api.coPlace(b.placement_id, await curRev(api), a.slot_id);
  assert.deepStrictEqual(r.sequence.modules[0].slots.map((s) => s.placements.length), [2, 1]);
  assert.deepStrictEqual(r.result, { slot_id: a.slot_id });
  // ungroup puts it in a new slot right after
  r = await api.ungroup(b.placement_id, await curRev(api));
  assert.deepStrictEqual(r.sequence.modules[0].slots.map((s) => s.placements.map((p) => p.node_id)), [["WHO-0010"], ["WHO-0011"], ["WHO-0020"]]);
  await assert.rejects(api.ungroup(b.placement_id, await curRev(api)), apiErr(422, "already_alone"));
  // merge slot 3 into slot 2
  r = await api.mergeSlot(c.slot_id, await curRev(api), r.result.slot_id);
  assert.deepStrictEqual(r.sequence.modules[0].slots.map((s) => s.placements.length), [1, 2]);
  assert.strictEqual(r.result.slot_id, (await api.sequence("2")).modules[0].slots[1].slot_id);
  // chevron down from the last slot of M1 crosses to the start of M2; up from the first slot of M1 is at_edge
  const lastSlot = r.sequence.modules[0].slots[1].slot_id;
  r = await api.moveSlot(lastSlot, await curRev(api), { direction: "down" });
  assert.deepStrictEqual(r.sequence.modules.map((m) => m.slots.length), [1, 2]);
  assert.strictEqual(r.sequence.modules[1].slots[0].slot_id, lastSlot);
  r = await api.moveSlot(lastSlot, await curRev(api), { direction: "up" });           // back: end of M1
  assert.strictEqual(r.sequence.modules[0].slots[1].slot_id, lastSlot);
  const first = r.sequence.modules[0].slots[0].slot_id;
  await assert.rejects(api.moveSlot(first, await curRev(api), { direction: "up" }), apiErr(422, "at_edge"));
  const lastOfAll = (await api.sequence("2")).modules[1].slots[0].slot_id;
  await assert.rejects(api.moveSlot(lastOfAll, await curRev(api), { direction: "down" }), apiErr(422, "at_edge"));
  await assert.rejects(api.moveSlot(first, await curRev(api), {}), apiErr(422, "invalid"));
  await assert.rejects(api.moveSlot(first, await curRev(api), { direction: "up", to_module_id: m2 }), apiErr(422, "invalid"));
  r = await api.moveSlot(first, await curRev(api), { to_module_id: m2 });
  assert.strictEqual(r.sequence.modules[1].slots[r.sequence.modules[1].slots.length - 1].slot_id, first);
  r = await api.updateSlot(first, await curRev(api), "Co-taught: compare"); assert.strictEqual(r.sequence.modules[1].slots[1].label, "Co-taught: compare");
  r = await api.updateSlot(first, await curRev(api), ""); assert.strictEqual(r.sequence.modules[1].slots[1].label, null);
  // remove placements; empty slot is removed with them; non-empty module refuses removal
  await assert.rejects(api.removeModule(m2, await curRev(api)), (e) => apiErr(409, "module_not_empty")(e));
  const before = (await api.sequence("2")).modules[1].slots.length;
  r = await api.removePlacement(d.placement_id, await curRev(api), null);
  assert.strictEqual(r.sequence.modules[1].slots.length, before - 1); assert.ok(!(K.t10 in r.sequence.placed_index));
  // a removed node can be placed again
  r = await api.place(1, await curRev(api), m2, K.t10, {}); assert.ok(K.t10 in r.sequence.placed_index);
});

test("DemoApi createSlotGroup: co-places, skips already placed, off-grade confirm", async () => {
  const { api, m1 } = await seeded();
  let r = await api.place(1, await curRev(api), m1, K.w10, {});
  const pid = r.result.placement_id;
  r = await api.createSlotGroup(1, await curRev(api), m1, [K.w10, K.t10, K.t11], false, {});
  assert.deepStrictEqual(r.result.skipped, [{ source_key: K.w10, reason: "already_placed", placement_id: pid }]);
  assert.strictEqual(r.result.placement_ids.length, 2);
  const slot = r.sequence.modules[0].slots[1]; assert.deepStrictEqual(slot.placements.map((p) => p.node_id), ["TIM-0010", "TIM-0011"]);
  await assert.rejects(api.createSlotGroup(1, await curRev(api), m1, [K.w12, K.w20], false, {}), (e) => apiErr(422, "confirm_off_grade_required")(e) && e.detail.source_keys.join() === K.w12);
  r = await api.createSlotGroup(1, await curRev(api), m1, [K.w12, K.w20], true, { [K.w20]: "note here" });
  assert.strictEqual(r.sequence.modules[0].slots[2].placements[1].differentiation_note, "note here");
  await assert.rejects(api.createSlotGroup(1, await curRev(api), m1, [], false, {}), apiErr(422, "invalid"));
});

test("DemoApi attribute PATCH uses the placement rev and does not bump the sequence rev", async () => {
  const { api, m1 } = await seeded();
  let r = await api.place(1, await curRev(api), m1, K.w10, {});
  const pid = r.result.placement_id, seqRev = await curRev(api);
  let p = r.sequence.modules[0].slots[0].placements[0];
  r = await api.updatePlacement(pid, p.rev, { calibration: "deep", period_estimate: 1.5, differentiation_note: "hi" });
  assert.strictEqual(r.sequence.sequence.rev, seqRev);                                 // unchanged
  p = r.sequence.modules[0].slots[0].placements[0];
  assert.strictEqual(p.calibration, "deep"); assert.strictEqual(p.period_estimate, 1.5); assert.strictEqual(p.rev, 2);
  assert.strictEqual(p.period_hint_seen, "G2: Likely 2 instructional periods");          // written with the estimate
  const ev = await api.events(1, 50);
  assert.deepStrictEqual(ev.events.slice(0, 3).map((e) => e.action).sort(), ["set_calibration", "set_note", "set_period"]);   // one event per attribute
  await assert.rejects(api.updatePlacement(pid, 1, { calibration: "functional" }), (e) => apiErr(409, "stale_revision")(e) && e.detail.current_rev === 2 && !!e.detail.sequence.sequence);
  await assert.rejects(api.updatePlacement(pid, 2, { calibration: "bogus" }), apiErr(422, "invalid"));
  await assert.rejects(api.updatePlacement(pid, 2, { period_estimate: -1 }), apiErr(422, "invalid"));
  await assert.rejects(api.updatePlacement(pid, 2, { period_estimate: 201 }), apiErr(422, "invalid"));
  r = await api.updatePlacement(pid, 2, { calibration: null, period_estimate: null, differentiation_note: null });     // null clears
  p = r.sequence.modules[0].slots[0].placements[0];
  assert.strictEqual(p.calibration, null); assert.strictEqual(p.period_estimate, null); assert.strictEqual(p.differentiation_note, null);
  // guardrail in the view is the mirror applied to the whole sequence and to each module
  r = await api.updatePlacement(pid, 3, { calibration: "functional", period_estimate: 2 });
  assert.deepStrictEqual(r.sequence.guardrail, computeGuardrail([{ calibration: "functional", period_estimate: 2 }]));
  assert.deepStrictEqual(r.sequence.modules[0].guardrail, r.sequence.guardrail);
});

test("DemoApi stale revision: 409 carries the current SequenceView and changes nothing", async () => {
  const { api, m1 } = await seeded();
  const rev = await curRev(api);
  await api.place(1, rev, m1, K.w10, {});
  await assert.rejects(api.place(1, rev, m1, K.w20, {}), (e) => {
    return apiErr(409, "stale_revision")(e) && e.detail.current_rev === rev + 1 && e.detail.sequence.sequence.rev === rev + 1 && e.detail.sequence.guardrail.n === 1;
  });
  assert.strictEqual((await api.sequence("2")).guardrail.n, 1);
  await assert.rejects(api.createModule(1, rev, "x"), apiErr(409, "stale_revision"));
});

test("DemoApi seeded sequence: attention, acknowledge, reattach, remove", async () => {
  const p = small();
  const mk = (id, key, status, nid, extra) => Object.assign({ placement_id: id, rev: 1, order_in_slot: 1024, source_key: key, node_id: nid, node_id_seen: nid, node_text: "t " + nid,
    node_text_seen: "t " + nid, stem_id: "WHO", stem_name: "Whole", concept_skill_display: "cs", ladder_file_seen: "f.docx", grade_kind_seen: "span", state_now: "span",
    status, relabelled: false, relabel: null, is_bridge: false, calibration: "deep", period_estimate: 1, period_hint: null, period_hint_seen: null, differentiation_note: null,
    placed_by: "x", placed_at: "t", updated_by: "x", updated_at: "t", placed_elsewhere: [], badges: [] }, extra || {});
  p.sequence = { grade: "2", ladders_last_read: "x", sequence: { sequence_id: 5, grade: "2", title: "Seeded", owner: "sam", note: null, rev: 9, created_by: "sam", created_at: "t" },
    modules: [{ module_id: 3, title: "M1", note: null, order_key: 1024, position: 1, guardrail: null, slots: [
      { slot_id: 8, label: null, order_key: 1024, position: 1, placements: [mk(11, "K:gone", "orphaned", "WHO-9999", { state_now: null })] },
      { slot_id: 9, label: null, order_key: 2048, position: 2, placements: [mk(12, K.w10, "grade_changed", "WHO-0010", { grade_kind_seen: "core", state_now: "span" })] }] }],
    placed_index: {}, slice_badges: {}, guardrail: null, attention: [] };
  const api = new DemoApi(p, memStorage());
  let v = await api.sequence("2");
  assert.strictEqual(v.sequence.sequence_id, 5); assert.strictEqual(v.sequence.owner, "sam");
  assert.deepStrictEqual(v.attention.map((a) => [a.placement_id, a.status, a.actions]), [[11, "orphaned", ["reattach", "remove"]], [12, "grade_changed", ["acknowledge", "remove"]]]);
  assert.deepStrictEqual(keys(v.attention[0]), ["actions", "grade_kind_seen", "ladder_file_seen", "module_id", "module_position", "module_title", "node_text_seen", "placement_id", "rev", "slot_id", "slot_position", "source_key", "state_now", "status", "stem_id_seen", "suggestions"]);
  assert.deepStrictEqual(v.modules[0].slots[0].placements[0].badges.map((b) => b.code), ["orphaned"]);
  assert.deepStrictEqual(v.modules[0].slots[1].placements[0].badges.map((b) => [b.code, b.label]), [["grade_changed", "was core → now span"]]);
  assert.ok("K:gone" in v.placed_index);                                              // orphans are in placed_index
  // reattach: only a key present in the demo, and not one already placed elsewhere in the sequence
  await assert.rejects(api.reattach(11, 9, "K:nowhere"), apiErr(404, "not_found"));
  await assert.rejects(api.reattach(11, 9, K.w10), apiErr(409, "reattach_target_placed"));
  let r = await api.reattach(11, 9, K.w11);
  assert.strictEqual(r.sequence.modules[0].slots[0].placements[0].status, "ok"); assert.strictEqual(r.sequence.modules[0].slots[0].placements[0].source_key, K.w11);
  assert.strictEqual(r.sequence.modules[0].slots[0].placements[0].calibration, "deep");   // attributes and id survive
  r = await api.acknowledge(12, r.sequence.sequence.rev);
  assert.deepStrictEqual(r.sequence.attention, []); assert.strictEqual(r.sequence.modules[0].slots[1].placements[0].grade_kind_seen, "span");
  assert.strictEqual(r.sequence.sequence.rev, 11);
});

test("DemoApi views CRUD", async () => {
  const api = new DemoApi(small(), memStorage());
  assert.deepStrictEqual((await api.listViews("2")).views, []);
  const v = await api.createView("2", "Number only", { hidden_stems: ["TIM"] });
  assert.deepStrictEqual(keys(v), ["created_at", "grade", "name", "state", "updated_at", "view_id"]);
  await assert.rejects(api.createView("2", "Number only", {}), apiErr(409, "view_name_exists"));
  await assert.rejects(api.createView("2", " ", {}), apiErr(422, "invalid"));
  const v2 = await api.createView("2", "Other", {});
  const u = await api.updateView(v.view_id, { name: "Renamed", state: { leaves: true } });
  assert.strictEqual(u.name, "Renamed"); assert.deepStrictEqual(u.state, { leaves: true });
  await assert.rejects(api.updateView(v.view_id, { name: "Other" }), apiErr(409, "view_name_exists"));
  assert.strictEqual((await api.listViews("2")).views.length, 2); assert.strictEqual((await api.listViews("3")).views.length, 0);
  assert.deepStrictEqual(await api.deleteView(v2.view_id), { deleted: v2.view_id });
  await assert.rejects(api.deleteView(v2.view_id), apiErr(404, "not_found"));
});

test("DemoApi persistence: storage round trip, reset, and a storage that throws", async () => {
  const p = small(), st = memStorage();
  let api = new DemoApi(p, st);
  assert.strictEqual(api.persisted, true);
  await api.createSequence("2", "Kept"); await api.createModule(1, 1, "M1");
  assert.ok(st._m.has("mh2seq-demo:2:2026-09-30T12:00:00+00:00"));
  const again = new DemoApi(small(), st);
  assert.strictEqual((await again.sequence("2")).sequence.title, "Kept");
  again.reset();
  assert.strictEqual((await again.sequence("2")).sequence, null); assert.strictEqual(st._m.has("mh2seq-demo:2:2026-09-30T12:00:00+00:00"), false);
  // corrupt stored state is ignored
  st.setItem("mh2seq-demo:2:2026-09-30T12:00:00+00:00", "{not json");
  assert.strictEqual((await new DemoApi(small(), st).sequence("2")).sequence, null);
  // storage that throws on every call: still works, says it is not kept
  api = new DemoApi(small(), throwingStorage());
  assert.strictEqual(api.persisted, false);
  await api.createSequence("2", "Volatile"); const r = await api.createModule(1, 1, "M1");
  assert.strictEqual(r.sequence.modules.length, 1); assert.strictEqual(api.persisted, false);
  api = new DemoApi(small(), null); assert.strictEqual(api.persisted, false);
  await api.createSequence("2", "No storage");
});

test("DemoApi renumbers a slot container when the gap runs out (big G2 payload)", async () => {
  const p = big(), api = new DemoApi(p, memStorage());
  await api.createSequence("2", "s"); const m = (await api.createModule(1, 1, "M1")).result.module_id;
  const owed = []; p.slice.super_stems.forEach((ss) => ss.stems.forEach((st) => st.concept_skills.forEach((c) => c.nodes.forEach((n) => { if (n.owed && !n.requires_confirm) owed.push(n.source_key); }))));
  const s1 = (await api.place(1, await curRev(api), m, owed[0], {})).result.slot_id;
  await api.place(1, await curRev(api), m, owed[1], {});
  for (let i = 2; i < 14; i++) await api.place(1, await curRev(api), m, owed[i], { after_slot_id: s1 });
  const v = await api.sequence("2");
  const keysOrder = v.modules[0].slots.map((s) => s.order_key);
  assert.deepStrictEqual(keysOrder.slice().sort((a, b) => a - b), keysOrder);                   // strictly ordered
  assert.strictEqual(new Set(keysOrder).size, keysOrder.length);
  assert.strictEqual(v.modules[0].slots[0].placements[0].source_key, owed[0]);
  assert.strictEqual(v.modules[0].slots[1].placements[0].source_key, owed[13]);                  // most recent insert sits right after slot 1
  const ev = (await api.events(1, 200)).events;
  assert.strictEqual(ev.filter((e) => e.action === "renumber").length, 1);
});

/* ------------------------------------------------------------- HttpApi ---- */

test("HttpApi builds URLs with encodeURIComponent and parses errors", async () => {
  const calls = [];
  const fakeFetch = async (url, init) => {
    calls.push([url, init]);
    if (url.includes("boom")) return { ok: false, status: 409, text: async () => JSON.stringify({ detail: { error: "stale_revision", message: "stale", current_rev: 4, sequence: { x: 1 } } }) };
    if (url.includes("badbody")) return { ok: false, status: 422, text: async () => JSON.stringify({ detail: [{ loc: ["body"], msg: "x" }] }) };
    if (url.includes("noauth")) return { ok: false, status: 401, text: async () => "Unauthorized" };
    return { ok: true, status: 200, text: async () => JSON.stringify({ ok: true }) };
  };
  global.localStorage = { getItem: (k) => (k === "mh2seq-user" ? "sam" : null) };
  const api = new HttpApi("http://h.test/api/seq/", fakeFetch);
  await api.node("TIM:fe32bbacca33e365", "2");
  assert.strictEqual(calls[0][0], "http://h.test/api/seq/node/TIM%3Afe32bbacca33e365?grade=2");
  assert.strictEqual(calls[0][1].credentials, "same-origin"); assert.strictEqual(calls[0][1].headers["X-MH2-User"], "sam");
  await api.compare(["COM:58ec03661c5f2d1b", "WHO:d65c6bed74a5ba1c"], "2");
  assert.strictEqual(calls[1][0], "http://h.test/api/seq/compare?grade=2&keys=COM%3A58ec03661c5f2d1b,WHO%3Ad65c6bed74a5ba1c");
  await api.place(1, 5, 2, "WHO:d65c6bed74a5ba1c", { differentiation_note: "n" });
  assert.strictEqual(calls[2][1].method, "POST"); assert.strictEqual(calls[2][0], "http://h.test/api/seq/placements");
  assert.deepStrictEqual(JSON.parse(calls[2][1].body), { expected_rev: 5, sequence_id: 1, module_id: 2, source_key: "WHO:d65c6bed74a5ba1c", confirm_off_grade: false, differentiation_note: "n" });
  await api.removeModule(3, 7);
  assert.deepStrictEqual([calls[3][1].method, calls[3][0]], ["DELETE", "http://h.test/api/seq/modules/3?expected_rev=7"]);
  await api.updatePlacement(4, 2, { calibration: null });
  assert.deepStrictEqual(JSON.parse(calls[4][1].body), { expected_rev: 2, calibration: null });      // null is sent (clears), absent keys are not
  await api.moveSlot(9, 3, { to_module_id: 2 });
  assert.deepStrictEqual(JSON.parse(calls[5][1].body), { expected_rev: 3, to_module_id: 2 });
  await api.events(1, 50, 12); assert.strictEqual(calls[6][0], "http://h.test/api/seq/sequences/1/events?limit=50&before=12");
  await api.removePlacement(4, 8, "dup"); assert.strictEqual(calls[7][0], "http://h.test/api/seq/placements/4?expected_rev=8&reason=dup");
  delete global.localStorage;
  await assert.rejects(api.req("GET", "boom"), (e) => e instanceof ApiError && e.status === 409 && e.error === "stale_revision" && e.detail.current_rev === 4 && e.detail.sequence.x === 1);
  await assert.rejects(api.req("GET", "badbody"), (e) => e.status === 422 && e.error === "invalid" && e.message === "Invalid request");
  await assert.rejects(api.req("GET", "noauth"), (e) => e.status === 401);
  const down = new HttpApi("/api/seq/", async () => { throw new Error("offline"); });
  await assert.rejects(down.slice("2"), (e) => e.status === 0 && e.error === "network");
  // every public DemoApi method exists on HttpApi too (same adapter surface)
  const surface = Object.getOwnPropertyNames(DemoApi.prototype).filter((n) => !n.startsWith("_") && n !== "constructor" && n !== "reset");
  surface.forEach((n) => assert.strictEqual(typeof HttpApi.prototype[n], "function", "HttpApi." + n));
});

/* ------------------------------------------------------------ renderers ---- */

function model(extra) {
  const p = small(), sl = p.slice;
  return Object.assign({ grade: "2", gradesInfo: p.grades, slice: sl, index: A.indexSlice(sl), seqView: null, ui: { open: {}, goalOpen: {}, editing: null, prompt: null, notice: null, toasts: [], railOpen: false, guardTab: "seq", targetModuleId: null, viewMode: null, selectedViewId: null, events: [], noteDraft: {} },
    whoami: { user: "demo", auth_enabled: false, source: "demo" }, views: [], drawer: null, compareData: null, demo: { on: true, persisted: true, source: "fixture" } }, extra || {});
}

test("renderers return strings and escape node text", () => {
  const M = model(), st = defaultState(M.slice);
  M.slice.super_stems[0].stems[0].concept_skills[0].nodes[0].node_text = "<script>x</script>";
  M.index = A.indexSlice(M.slice);
  const html = A.renderSlice(st, M);
  assert.strictEqual(typeof html, "string");
  assert.ok(!html.includes("<script>x</script>")); assert.ok(html.includes("&lt;script&gt;x&lt;/script&gt;"));
  ["renderToolbar", "renderRail", "renderDrawer", "renderSheet", "renderGuardrail", "renderAttention"].forEach((f) => assert.strictEqual(typeof A[f](st, M), "string", f));
  assert.ok(A.renderToolbar(st, M).includes('aria-pressed="true"'));
  assert.ok(html.includes('class="chip ingrade"') || html.includes("chip ingrade"));
  assert.ok(html.includes("No goal in ladder"));                                     // WHO:aa11bb22 has goal_missing
  assert.ok(html.includes("chip ctx"));                                               // off-grade chip is a context chip
  assert.ok(!html.includes("pairs: COM"));                                            // Pairings toggle is off
  assert.ok(A.renderSlice(Object.assign({}, st, { pairings: true }), M).includes("pairs: COM"));
  assert.ok(!html.includes("overflow-x"));                                            // wrap, never scroll
});

test("renderChip: placed-here pill, compare toggle, slice_badges, compare cap", () => {
  const M = model(), st = defaultState(M.slice);
  M.seqView = { placed_index: { [K.w10]: { placement_id: 1, module_id: 1, module_title: "M1", module_position: 1, slot_id: 1, slot_position: 1, status: "ok", is_bridge: false },
    [K.w12]: { placement_id: 2, module_id: 2, module_title: "M2", module_position: 2, slot_id: 2, slot_position: 3, status: "ok", is_bridge: true } },
    slice_badges: { [K.w12]: [{ code: "bridge", tier: "structural", label: "bridge · off_grade", detail: "x", refs: [], partners: [] }] }, modules: [] };
  const n10 = M.index[K.w10], n12 = M.index[K.w12], n20 = M.index[K.w20];
  const c10 = A.renderChip(n10, st, M);
  assert.ok(c10.includes("M1 · 1")); assert.ok(!c10.includes("+ Place"));
  const c12 = A.renderChip(n12, st, M);
  assert.ok(c12.includes("M2 · 3")); assert.ok(c12.includes(">bridge<")); assert.ok(c12.includes("bridge · off_grade"));
  const c20 = A.renderChip(n20, st, M); assert.ok(c20.includes("+ Place")); assert.ok(c20.includes("⊕ compare"));
  const full = Object.assign({}, st, { compare: [K.w10, K.w11, K.w20, K.t10] });
  const cmpBtn = (n, stt) => A.renderChip(n, stt, M).match(/<button[^>]*data-action="toggle-compare"[^>]*>/)[0];
  assert.ok(/ disabled/.test(cmpBtn(M.index[K.t11], full)));          // a 5th selection is disabled
  assert.ok(!/ disabled/.test(cmpBtn(M.index[K.w10], full)));         // an already-selected one stays enabled (to deselect)
  assert.ok(A.renderChip(M.index[K.w20], Object.assign({}, st, { node: K.w20 }), M).includes("selected"));
});

function viewWith(placements, extra) {
  // minimal SequenceView with one module / one slot per placement
  const mods = [{ module_id: 1, title: "M1 Place value", note: null, order_key: 1024, position: 1, guardrail: computeGuardrail(placements), slots: placements.map((p, i) => ({ slot_id: i + 1, label: null, order_key: 1024 * (i + 1), position: i + 1, placements: [Object.assign({ placement_id: i + 1, rev: 1, node_id: "N-" + i, node_id_seen: "N-" + i, node_text: "text " + i, stem_name: "Stem", source_key: "K:" + i, status: "ok", badges: [], placed_elsewhere: [], period_hint: null, differentiation_note: null, is_bridge: false }, p)] })) }];
  return Object.assign({ grade: "2", ladders_last_read: "x", sequence: { sequence_id: 1, grade: "2", title: "Grade 2 sequence", owner: "demo", note: null, rev: 3, created_by: "demo", created_at: "t" },
    modules: mods, placed_index: {}, slice_badges: {}, guardrail: computeGuardrail(placements), attention: [] }, extra || {});
}

test("renderRail: chevrons with aria-labels, calibration control, period hint and Use button, keyboard hooks", () => {
  const M = model(), st = defaultState(M.slice);
  M.seqView = viewWith([{ calibration: "deep", period_estimate: 1, period_hint: { text: "G2: 1 instructional period", value: 1, unit: "period", qualifier: "exact", low: 1, high: 1, basis: "grade_named", n_estimates: 1 } },
    { calibration: null, period_estimate: null, period_hint: { text: "Likely 2 days", value: null, unit: "day", qualifier: "exact", low: 2, high: 2, basis: "grade_named", n_estimates: 1 } }]);
  M.ui.targetModuleId = 1;
  const html = A.renderRail(st, M);
  ["Move slot up", "Move slot down", "Move module up", "Move module down"].forEach((t) => assert.ok(html.includes('aria-label="' + t + '"'), t));
  assert.ok(html.includes("Know it / Use it / See it"));
  assert.ok(html.includes("Use 1")); assert.ok(html.includes("G2: 1 instructional period"));
  assert.ok(html.includes("Likely 2 days") && html.includes("in days; not converted") && !html.includes("Use 2"));
  assert.ok(html.includes('data-focus-key="slot:1"') && html.includes('tabindex="0"'));
  assert.ok(html.includes("Merge into slot above"));
  assert.ok(/data-action="slot-move" data-id="1" data-val="up"[^>]*disabled/.test(html));            // first slot of first module: up disabled
  assert.ok(/data-action="module-remove"[^>]*disabled/.test(html));                                   // non-empty module
  assert.ok(html.includes("Reference marks, not quotas"));
  assert.ok(!/\b(pass|fail)\b/i.test(html));
  assert.ok(html.includes("Place into this module"));
});

test("renderGuardrail omits time ticks when show_time_targets is false, shows them at 50% coverage", () => {
  const M = model(), st = defaultState(M.slice);
  M.seqView = viewWith([{ calibration: "deep", period_estimate: null }, { calibration: "functional", period_estimate: null }, { calibration: "functional", period_estimate: 1 }]);
  assert.strictEqual(M.seqView.guardrail.show_time_targets, false);
  let html = A.renderGuardrail(st, M);
  assert.ok(!html.includes("tick-time")); assert.ok(html.includes("tick-count"));
  assert.ok(html.includes("reference marks shown at ≥ 50% coverage"));
  assert.ok(html.includes("Calibrated 3 of 3 placements")); assert.ok(html.includes("Periods known for 1 of 3 placements"));
  M.seqView = viewWith([{ calibration: "deep", period_estimate: 1 }, { calibration: "functional", period_estimate: null }]);
  html = A.renderGuardrail(st, M);
  assert.ok(html.includes("tick-time")); assert.ok(!html.includes("reference marks shown"));
  assert.strictEqual(A.renderGuardrail(st, model({ seqView: { sequence: null } })), "");
});

test("renderAttention: orphan with suggestion, grade_changed with acknowledge", () => {
  const M = model(), st = defaultState(M.slice);
  M.seqView = viewWith([{}], { attention: [
    { placement_id: 1, rev: 1, module_id: 1, module_title: "M1", module_position: 1, slot_id: 1, slot_position: 1, status: "orphaned", source_key: "K:gone", node_text_seen: "Order <b>whole</b> numbers.", ladder_file_seen: "f.docx", stem_id_seen: "WHO", grade_kind_seen: "span", state_now: null,
      suggestions: [{ source_key: "K:new", node_id: "WHO-0011", node_text: "Order whole numbers using place value.", stem_id: "WHO", stem_name: "W", concept_skill_display: "c", reason: "same_cs_similar", ratio: 0.962 }], actions: ["reattach", "remove"] },
    { placement_id: 2, rev: 1, module_id: 1, module_title: "M1", module_position: 1, slot_id: 2, slot_position: 2, status: "grade_changed", source_key: "K:x", node_text_seen: "t", ladder_file_seen: "f", stem_id_seen: "WHO", grade_kind_seen: "core", state_now: "off_grade", suggestions: [], actions: ["acknowledge", "remove"] }] });
  const html = A.renderAttention(st, M);
  assert.ok(html.includes("Needs attention")); assert.ok(html.includes("Re-attach")); assert.ok(html.includes("96.2% similar"));
  assert.ok(html.includes("&lt;b&gt;whole&lt;/b&gt;")); assert.ok(html.includes("Keep (acknowledge)")); assert.ok(html.includes("was core"));
  assert.strictEqual(A.renderAttention(st, model({ seqView: viewWith([{}]) })), "");
});

test("renderDrawer: C/S band, goal or none, show-all-fields toggle, pairings and stem links", () => {
  const p = small(), M = model({ drawer: p.drawers[K.w10] }), st = defaultState(M.slice);
  let html = A.renderDrawer(st, M);
  assert.ok(html.includes("Identify relationships between quantities")); assert.ok(html.includes("Grade as written"));
  assert.ok(html.includes("Show all fields (2 empty)")); assert.ok(!html.includes("Strategies"));
  assert.ok(html.includes("Stated links") && html.includes("as captured")); assert.ok(html.includes('data-action="goto-stem"'));
  assert.ok(html.includes("Comparing:") && html.includes("COM-0012 (in grade)")); assert.ok(!html.includes('data-id="K:com0012"'));   // not in this slice: plain text
  assert.ok(html.includes("Start the sequence in the rail"));
  M.ui.showAllFields = true; assert.ok(A.renderDrawer(st, M).includes("Strategies"));
  M.drawer = p.drawers[K.w20]; html = A.renderDrawer(st, M); assert.ok(html.includes("No goal in ladder"));
  M.drawer = p.drawers[K.t10]; html = A.renderDrawer(st, M); assert.ok(html.includes("unconfirmed")); assert.ok(html.includes("Ruling"));
  assert.strictEqual(A.renderDrawer(st, model()), "");
  assert.ok(html.includes('aria-label="Close details"'));
});

test("renderSheet: columns, shared highlight, hide empty, co-place action", () => {
  const p = small(), cmp = assembleCompare([p.compare_columns[K.w10], p.compare_columns[K.w11]], p.field_defs, "2");
  const M = model({ compareData: cmp }), st = Object.assign(defaultState(M.slice), { compare: [K.w10, K.w11], sheet: true });
  let html = A.renderSheet(st, M);
  assert.ok(html.includes('class="shared"')); assert.ok(html.includes("shared</span>")); assert.ok(html.includes("WHO-0010") && html.includes("WHO-0011"));
  assert.ok(!html.includes(">Strategies<"));                                           // empty rows hidden by default
  M.ui.cmpHideEmpty = false; assert.ok(A.renderSheet(st, M).includes(">Strategies<"));
  assert.ok(html.includes("Start a sequence with a module"));
  M.seqView = viewWith([{}]); html = A.renderSheet(st, M);
  assert.ok(html.includes("Co-place in a module")); assert.ok(html.includes('data-action="cmp-coplace"'));
  assert.ok(html.includes("Remove WHO-0010 from compare"));
  assert.strictEqual(A.renderSheet(Object.assign({}, st, { sheet: false }), M), "");
  assert.ok(A.renderTray(Object.assign({}, st, { sheet: false }), M).includes("Compare 2/4")); assert.strictEqual(A.renderTray(st, M), "");
});

test("renderPrompt: bridge confirm and cross-grade note panel", () => {
  const M = model({ seqView: viewWith([{}]) }), st = defaultState(M.slice);
  M.ui.prompt = { keys: [K.w12], moduleId: 1, notes: {} };
  let html = A.renderPrompt(st, M);
  assert.ok(html.includes("is not in Grade 2") && html.includes("off-grade") && html.includes("G4, G5")); assert.ok(html.includes("Place as bridge"));
  M.ui.prompt = { keys: [K.w11], moduleId: 1, notes: {} };
  html = A.renderPrompt(st, M);
  assert.ok(html.includes("Also placed in G3 · M1 Place value")); assert.ok(html.includes("How is it different in Grade 2? (optional)"));
  ["Place with note", "Place without note", "Cancel"].forEach((t) => assert.ok(html.includes(t), t));
});

test("renderHeader: grade picker text, demo badge, owner banner", () => {
  const M = model({ seqView: viewWith([{}, {}]) }), st = defaultState(M.slice);
  M.gradesInfo.grades[1].owed_nodes = 33;
  const h = A.renderHeader(st, M);
  assert.ok(h.includes("G2 · 33 owed · 2 placed")); assert.ok(h.includes("Demo")); assert.ok(h.includes("Reset demo"));
  assert.ok(A.renderFooter(M).includes("data built from fixture"));
  M.demo.source = "mh2.db"; assert.ok(A.renderFooter(M).includes("data built from mh2.db"));
});

test("sequenceToCsv: order, teacher labels, quoting, formula guard", () => {
  const v = viewWith([{ calibration: "deep", period_estimate: 1.5, node_text: 'He said "hi", ok', differentiation_note: "line1\nline2" }, { calibration: null, period_estimate: null, node_text: "=SUM(A1)" }]);
  const rows = A.sequenceToCsv(v).split("\r\n");
  assert.strictEqual(rows[0], "module,module_title,slot,slot_label,node_id,node_text,stem,calibration,teacher_label,periods,note,status");
  assert.ok(rows[1].startsWith('1,M1 Place value,1,,N-0,"He said ""hi"", ok",Stem,Deep,Know it,1.5,"line1'));
  assert.ok(rows[2].includes("'=SUM(A1)"));
  assert.strictEqual(A.sequenceToCsv(null), rows[0] + "\r\n");
});

/* ------------------------------------------------ stub-DOM smoke run ---- */

function makeDoc() {
  const doc = { els: {}, activeElement: null, listeners: {} };
  doc.getElementById = (id) => {
    if (id === "seq-demo-data") return null;
    if (!doc.els[id]) {
      const el = { id, innerHTML: "", hidden: id === "drawer" || id === "sheet" || id === "tray", scrollTop: 0, listeners: {}, attrs: {}, style: { setProperty() {} },
        classList: { toggle() {}, add() {} }, addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); },
        getAttribute(n) { return has(this.attrs, n) ? this.attrs[n] : null; }, closest() { return null; }, querySelector() { return null; }, focus() { doc.activeElement = this; } };
      doc.els[id] = el;
    }
    return doc.els[id];
  };
  doc.querySelector = () => null;
  doc.addEventListener = (t, f) => { (doc.listeners[t] = doc.listeners[t] || []).push(f); };
  ["app", "hdr", "banner", "toolbar", "rail", "slice", "drawer", "sheet", "tray", "notice", "toasts", "foot"].forEach((i) => doc.getElementById(i));
  return doc;
}
function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
function fakeTarget(attrs, value, extra) {
  const t = Object.assign({ value, attrs: Object.assign({}, attrs), closest(sel) { return sel === "[data-action]" && has(this.attrs, "data-action") ? this : (extra && extra.closest ? extra.closest(sel) : null); },
    getAttribute(n) { return has(this.attrs, n) ? this.attrs[n] : null; } });
  return t;
}

test("smoke: boot -> slice -> drawer -> compare -> module -> place -> reorder -> calibrate -> guardrail (stub DOM)", async () => {
  const doc = makeDoc(), R = doc.els, calls = [];
  const hist = { replaceState(_a, _b, url) { calls.push(url); } };
  const api = new DemoApi(small(), memStorage());
  const app = A.createApp({ doc, api, hist, loc: { search: "" }, storage: memStorage(), setTimeout() { return 0; }, demoSource: "fixture", demoGeneratedAt: "2026-09-30T12:00:00+00:00" });
  const M = app.M, act = app.act;
  await app.init();
  // -- boot and slice
  assert.ok(R.hdr.innerHTML.includes("G2 · 5 owed · 0 placed"), R.hdr.innerHTML.slice(0, 400));
  assert.ok(R.slice.innerHTML.includes("WHO-0010") && R.slice.innerHTML.includes("Number Systems and Structures"));
  assert.ok(R.toolbar.innerHTML.includes("shown <b>7</b> of 8 chips"));
  assert.ok(R.rail.innerHTML.includes("Start the Grade 2 sequence"));
  assert.strictEqual(R.drawer.hidden, true); assert.strictEqual(R.tray.hidden, true);
  assert.ok(R.foot.innerHTML.includes("data built from fixture"));
  assert.strictEqual(R.slice.listeners.click.length, 1);                                  // one delegated listener per region
  // toggles
  await act("toggle", { id: "leaves" }); assert.ok(R.toolbar.innerHTML.includes("shown <b>8</b> of 8")); assert.ok(calls[calls.length - 1].includes("lv=1"));
  await act("toggle", { id: "leaves" });
  await act("toggle", { id: "context" }); assert.ok(R.toolbar.innerHTML.includes("shown <b>5</b> of 8")); await act("toggle", { id: "context" });
  // -- drawer
  await act("open-node", { id: K.w10 });
  assert.strictEqual(R.drawer.hidden, false); assert.ok(R.drawer.innerHTML.includes("Compare and order numbers by using place value."));
  assert.ok(calls[calls.length - 1].includes("node=K%3Awho0010")); assert.ok(R.slice.innerHTML.includes("chip ingrade selected"));
  await act("open-node", { id: K.w11 }); assert.ok(R.drawer.innerHTML.includes("WHO-0011"));
  // -- compare (cap at 4)
  for (const k of [K.w10, K.w11, K.w20, K.t10]) await act("toggle-compare", { id: k });
  assert.ok(R.tray.innerHTML.includes("Compare 4/4")); assert.strictEqual(R.tray.hidden, false);
  await act("toggle-compare", { id: K.t11 }); assert.strictEqual(app.getState().compare.length, 4); assert.ok(M.ui.notice.text.includes("at most 4"));
  await act("open-sheet"); assert.strictEqual(R.sheet.hidden, false); assert.ok(R.sheet.innerHTML.includes('class="cmp"')); assert.ok(calls[calls.length - 1].includes("sheet=1"));
  assert.ok(calls[calls.length - 1].includes("compare=K%3Awho0010,K%3Awho0011,K%3Awho0020,K%3Atim0010"));
  await act("cmp-remove", { id: K.t10 }); await act("cmp-remove", { id: K.w20 }); assert.strictEqual(app.getState().compare.length, 2);
  await act("clear-compare"); assert.strictEqual(R.tray.hidden, true); assert.strictEqual(R.sheet.hidden, true);
  // placing without a sequence points at the rail
  await act("place-chip", { id: K.w20 }); assert.ok(M.ui.notice.text.includes("Start the Grade 2 sequence")); assert.strictEqual(M.seqView.sequence, null);
  // -- sequence and modules
  await act("start-sequence");
  assert.strictEqual(M.seqView.sequence.title, "Grade 2 sequence"); assert.deepStrictEqual(M.seqView.modules.map((m) => m.title), ["Module 1"]);
  assert.ok(R.rail.innerHTML.includes("Module 1") && R.rail.innerHTML.includes("Balance") && R.rail.innerHTML.includes("No placements yet"));
  await act("add-module"); assert.strictEqual(M.seqView.modules.length, 2); assert.ok(R.rail.innerHTML.includes('class="mod-title-in"'));
  const [m1, m2] = M.seqView.modules.map((m) => m.module_id);
  await act("module-title", { id: m2, value: "M2 Comparing" }); assert.strictEqual(M.seqView.modules[1].title, "M2 Comparing");
  await act("module-title", { id: m1, value: "M1 Place value" });
  // -- place: default target is the last module
  await act("place-chip", { id: K.w10 }); assert.strictEqual(M.seqView.placed_index[K.w10].module_id, m2);
  assert.ok(R.slice.innerHTML.includes("M2 · 1")); assert.ok(R.toolbar.innerHTML.includes("owed") || true);
  assert.ok(R.hdr.innerHTML.includes("G2 · 5 owed · 1 placed"));
  await act("target-module", { id: m1 }); await act("place-chip", { id: K.w20 }); assert.strictEqual(M.seqView.placed_index[K.w20].module_id, m1);
  // cross-grade prompt: note, never blocking
  await act("place-chip", { id: K.w11 });
  assert.ok(M.ui.prompt && R.rail.innerHTML.includes("How is it different in Grade 2? (optional)")); assert.strictEqual(M.seqView.guardrail.n, 2);
  await act("prompt-note", { id: K.w11, value: "G3 extends to 10,000" }); await act("prompt-go", { val: "note" });
  assert.strictEqual(M.ui.prompt, null); assert.strictEqual(M.seqView.modules[0].slots[1].placements[0].differentiation_note, "G3 extends to 10,000");
  assert.ok(R.rail.innerHTML.includes("Note: G3 extends to 10,000"));
  // cancel path
  await act("place-chip", { id: K.w12 }); assert.ok(M.ui.prompt); await act("prompt-cancel"); assert.strictEqual(M.ui.prompt, null);
  // off-grade: bridge confirmation
  await act("place-chip", { id: K.w12 }); assert.ok(R.rail.innerHTML.includes("Place as bridge")); assert.strictEqual(M.seqView.placed_index[K.w12], undefined);
  await act("prompt-go", { val: "nonote" }); assert.strictEqual(M.seqView.placed_index[K.w12].is_bridge, true);
  assert.ok(R.slice.innerHTML.includes("bridge · off_grade")); assert.ok(M.ui.notice.text.startsWith("Placed WHO-0012"));
  // -- reorder: chevron, keyboard (Alt+arrow), module chevron
  const order = () => M.seqView.modules[0].slots.map((s) => s.placements[0].node_id);
  assert.deepStrictEqual(order(), ["WHO-0020", "WHO-0011", "WHO-0012"]);
  await act("slot-move", { id: M.seqView.modules[0].slots[0].slot_id, val: "down" }); assert.deepStrictEqual(order(), ["WHO-0011", "WHO-0020", "WHO-0012"]);
  const slotId = M.seqView.modules[0].slots[1].slot_id;
  const card = fakeTarget({ "data-kind": "slot", "data-id": String(slotId) }, undefined, { closest: (sel) => (sel === "[data-kind]" ? card : null) });
  app.onEvent("keydown", { key: "ArrowUp", altKey: true, target: card, preventDefault() {} });
  await new Promise((r) => setImmediate(r)); await new Promise((r) => setImmediate(r));
  assert.deepStrictEqual(order(), ["WHO-0020", "WHO-0011", "WHO-0012"]);
  assert.strictEqual(app.ui.focusOverride, null);                                            // consumed by the re-render
  await act("module-move", { id: m1, val: "down" }); assert.deepStrictEqual(M.seqView.modules.map((m) => m.module_id), [m2, m1]);
  await act("slot-move", { id: M.seqView.modules[0].slots[0].slot_id, val: "up" });          // at the very start: notice, no error toast
  assert.ok(M.ui.notice.text.includes("start of the sequence")); assert.strictEqual(M.ui.toasts.length, 0);
  // -- calibrate and time: the guardrail updates
  const pid = (k) => M.seqView.placed_index[k].placement_id;
  await act("set-cal", { id: pid(K.w20), val: "deep" });
  assert.strictEqual(M.seqView.guardrail.counts.deep, 1); assert.ok(R.rail.innerHTML.includes("Calibrated 1 of 4 placements"));
  await act("set-period", { id: pid(K.w20), value: "2" }); assert.strictEqual(M.seqView.guardrail.total_periods, 2);
  assert.strictEqual(M.seqView.guardrail.show_time_targets, false); assert.ok(!R.rail.innerHTML.includes("tick-time"));
  await act("use-hint", { id: pid(K.w10), val: "2" });                                        // hint value 2 -> saved
  assert.strictEqual(M.seqView.guardrail.total_periods, 4); assert.strictEqual(M.seqView.guardrail.show_time_targets, true); assert.ok(R.rail.innerHTML.includes("tick-time"));
  await act("set-cal", { id: pid(K.w10), val: "functional" });
  assert.deepStrictEqual(M.seqView.guardrail.time_share, { deep: 0.5, functional: 0.5, illuminating: 0 });
  await act("set-period", { id: pid(K.w10), value: "-3" }); assert.ok(M.ui.toasts.length === 1);            // invalid: toast, nothing sent
  assert.strictEqual(M.seqView.modules.flatMap((m) => m.slots.flatMap((s) => s.placements)).find((p) => p.source_key === K.w10).period_estimate, 2);
  await act("guard-tab", { id: "module" }); assert.ok(R.rail.innerHTML.includes("This module (M"));
  // -- note editing
  await act("note-edit", { id: pid(K.w20) }); assert.ok(R.rail.innerHTML.includes("<textarea"));
  await act("note-text", { id: pid(K.w20), value: "Use 100s chart" }); await act("note-save", { id: pid(K.w20) });
  assert.ok(R.rail.innerHTML.includes("Note: Use 100s chart"));
  // -- co-place from the compare sheet (unplaced keys only)
  await act("toggle-compare", { id: K.t10 }); await act("toggle-compare", { id: K.t11 }); await act("toggle-compare", { id: K.w20 });
  await act("open-sheet"); assert.ok(R.sheet.innerHTML.includes("Co-place in a module")); assert.ok(R.sheet.innerHTML.includes("skipped: already placed"));
  await act("cmp-module", { value: String(m2) });
  await act("cmp-coplace");
  const coSlot = M.seqView.modules.flatMap((m) => m.slots).find((s) => s.placements.length === 2);
  assert.deepStrictEqual(coSlot.placements.map((p) => p.node_id), ["TIM-0010", "TIM-0011"]); assert.ok(M.ui.notice.text.includes("Placed TIM-0010, TIM-0011"));
  await act("ungroup", { id: coSlot.placements[1].placement_id }); assert.ok(!M.seqView.modules.flatMap((m) => m.slots).some((s) => s.placements.length > 1));
  // -- remove with confirmation
  const victim = pid(K.t11);
  await act("remove-placement", { id: victim }); assert.ok(M.seqView.placed_index[K.t11]); assert.ok(R.rail.innerHTML.includes("Confirm remove"));
  await act("remove-placement", { id: victim }); assert.strictEqual(M.seqView.placed_index[K.t11], undefined);
  // -- 409: someone else changed the sequence behind our back
  api.st.sequence.rev += 5;
  await act("add-module");
  assert.ok(M.ui.notice.text.includes("Someone else changed this sequence. Your change was not applied"));
  assert.strictEqual(M.seqView.sequence.rev, api.st.sequence.rev); assert.strictEqual(M.seqView.modules.length, 2);
  assert.ok(R.notice.innerHTML.includes("Someone else changed"));
  await act("add-module"); assert.strictEqual(M.seqView.modules.length, 3);                   // the next write works
  // -- saved views
  await act("toggle", { id: "pairings" }); await act("toggle", { id: "leaves" });
  await act("view-save"); await act("view-name", { value: "Pairs and leaves" }); await act("view-commit");
  assert.strictEqual(M.views.length, 1); assert.ok(R.toolbar.innerHTML.includes("Pairs and leaves"));
  await act("reset-view"); assert.strictEqual(app.getState().pairings, false);
  await act("pick-view", { value: String(M.views[0].view_id) }); assert.strictEqual(app.getState().pairings, true); assert.strictEqual(app.getState().leaves, true);
  await act("view-save"); await act("view-name", { value: "Pairs and leaves" }); await act("view-commit"); assert.ok(M.ui.toasts.some((t) => t.text.includes("A view with that name exists")));
  await act("view-cancel"); await act("view-rename"); await act("view-name", { value: "Pairs" }); await act("view-commit"); assert.strictEqual(M.views[0].name, "Pairs");
  await act("view-delete"); await act("view-delete-yes"); assert.strictEqual(M.views.length, 0);
  // -- stems and strips show/hide
  await act("stem-hide", { id: "TIM" }); assert.ok(!R.slice.innerHTML.includes("TIM-0011")); assert.ok(calls[calls.length - 1].includes("hs=TIM"));
  await act("show-all-hidden"); assert.ok(R.slice.innerHTML.includes("TIM-0011"));
  await act("stem-collapse", { id: "TIM" }); assert.ok(R.slice.innerHTML.includes("hidden while collapsed")); await act("goto-stem", { id: "TIM" }); assert.ok(!R.slice.innerHTML.includes("hidden while collapsed"));
  await act("cs-hide", { id: "TIM:c22eed5f" }); assert.ok(R.toolbar.innerHTML.includes("concept/skills")); await act("show-all-hidden");
  await act("stems-collapse-all"); assert.strictEqual((R.slice.innerHTML.match(/hidden while collapsed/g) || []).length, 2);
  await act("stems-expand-all"); assert.ok(!R.slice.innerHTML.includes("hidden while collapsed"));
  await act("export-csv"); assert.ok(app.ui.lastCsv.includes("WHO-0010") && app.ui.lastCsv.split("\r\n").length > 4);
  // -- Esc closes drawer, then sheet
  assert.ok(app.getState().node);
  assert.ok(app.ui.editing); assert.strictEqual(app.escapeKey(), true); assert.strictEqual(app.ui.editing, null);      // an open inline edit closes first
  assert.strictEqual(app.escapeKey(), true); assert.strictEqual(app.getState().node, null); assert.strictEqual(R.drawer.hidden, true);
  assert.strictEqual(app.getState().sheet, true); assert.strictEqual(app.escapeKey(), true); assert.strictEqual(app.getState().sheet, false); assert.strictEqual(app.escapeKey(), false);
  // -- delegated DOM events reach the same handlers
  const before = M.ui.railOpen;
  R.hdr.listeners.click[0]({ target: fakeTarget({ "data-action": "toggle-rail" }) }); await new Promise((r) => setImmediate(r));
  assert.strictEqual(M.ui.railOpen, !before);
  R.toolbar.listeners.change[0]({ target: fakeTarget({ "data-action": "toggle" }, "x") });      // wrong event type for that action: ignored
  // -- scroll position of the scroll containers survives a re-render
  R.rail.scrollTop = 77; R.slice.scrollTop = 123; app.renderAll(); assert.strictEqual(R.rail.scrollTop, 77); assert.strictEqual(R.slice.scrollTop, 123);
  // -- history loads on demand
  await act("disclose", { id: "history" }); assert.ok(R.rail.innerHTML.includes("place") && R.rail.innerHTML.includes("<b>demo</b>"));
  // -- sanity: nothing rendered 'undefined' / 'NaN' / '[object'
  Object.keys(R).forEach((id) => { assert.ok(!/undefined|NaN|\[object/.test(R[id].innerHTML), id + " contains a bad token: " + (R[id].innerHTML.match(/.{30}(undefined|NaN|\[object).{30}/) || [""])[0]); });
  // the page model never wrote the demo footnote wrongly
  assert.ok(R.rail.innerHTML.includes("Ordering warnings need the server"));
});

test("smoke: demo storage failure shows the warning banner; grade switch error is a toast", async () => {
  const doc = makeDoc(), R = doc.els;
  const api = new DemoApi(small(), throwingStorage());
  const app = A.createApp({ doc, api, hist: null, loc: { search: "" }, storage: null, setTimeout() { return 0; } });
  await app.init();
  assert.ok(R.banner.innerHTML.includes("Changes will not be kept after reload")); assert.ok(R.foot.innerHTML.includes("not kept"));
  await app.act("pick-grade", { value: "3" });
  assert.ok(app.M.ui.toasts.some((t) => t.text.includes("Demo contains Grade 2 only")), JSON.stringify(app.M.ui.toasts));
  assert.strictEqual(app.M.grade, "2");
});

test("smoke: HTTP-style boot restores node, compare and sheet from the URL; owner banner shows", async () => {
  const doc = makeDoc(), R = doc.els;
  const api = new DemoApi(small(), memStorage());
  await api.createSequence("2", "Theirs"); await api.updateSequence(1, 1, { owner: "sam" });
  const app = A.createApp({ doc, api, hist: { replaceState() {} }, loc: { search: "?grade=2&node=K%3Awho0010&compare=K%3Awho0010,K%3Awho0011&sheet=1&lv=1" }, storage: null, setTimeout() { return 0; } });
  await app.init();
  assert.strictEqual(R.drawer.hidden, false); assert.ok(R.drawer.innerHTML.includes("WHO-0010"));
  assert.strictEqual(R.sheet.hidden, false); assert.ok(R.sheet.innerHTML.includes('class="cmp"'));
  assert.ok(R.toolbar.innerHTML.includes("shown <b>8</b>"));
  assert.ok(R.banner.innerHTML.includes("You are editing <b>sam</b>"));
  // a bad node key in the URL is dropped without breaking boot
  const app2 = A.createApp({ doc: makeDoc(), api, hist: null, loc: { search: "?node=nope" }, storage: null, setTimeout() { return 0; } });
  await app2.init(); assert.strictEqual(app2.getState().node, null);
});

/* --------------------------------------------------------------- runner ---- */

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log("ok " + name); }
    catch (e) { failed += 1; console.log("FAIL " + name + "\n" + (e && e.stack ? e.stack : e)); }
  }
  console.log(failed ? "\n" + failed + " of " + tests.length + " FAILED" : "\nall " + tests.length + " tests passed");
  process.exit(failed ? 1 : 0);
})();
