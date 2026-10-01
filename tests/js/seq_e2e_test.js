/* End-to-end: the real app.js HttpApi against the real Python service, over HTTP.
 *
 *   MH2_DATA_DIR=... node tests/js/seq_e2e_test.js
 *
 * 1. Copies mh2.db (and mh2_seq.db if present) into a temp dir; the real files
 *    are never opened for writing.
 * 2. Starts tests/seq_http_harness.py on that copy (seq_api.py's own routes over
 *    stdlib http.server, no FastAPI) with --test-hooks.
 * 3. Runs one scenario through HttpApi: grades -> slice G2 -> drawer -> compare
 *    3 -> create sequence -> modules -> place (incl. bridge confirm, already
 *    placed) -> co-place -> reorder -> ungroup/merge -> move between modules ->
 *    calibration + periods -> guardrail -> events -> saved views -> stale 409s ->
 *    module_not_empty -> reconcile after a reworded node (orphan + reattach).
 * 4. Runs the same scenario through DemoApi seeded from the real export
 *    (export_seq_demo.build_payload_from_db on the same copy) and compares:
 *    response key sets recursively, result keys, error codes, placement
 *    positions, and guardrail numbers.
 * 5. Checks the JS guardrail mirror against Python seq_guardrail on the final
 *    state, and runs the page controller (stub DOM) against the live server.
 * Prints "SKIP" and exits 0 when there is no mh2.db.
 */
"use strict";
const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn, execFileSync } = require("child_process");

const ROOT = path.resolve(__dirname, "..", "..");
const A = require(path.join(ROOT, "seq_static", "app.js"));
const { HttpApi, DemoApi, ApiError, computeGuardrail } = A;
const PY = process.env.PYTHON || "python3";

function cfgPaths() {
  const out = execFileSync(PY, ["-c", "import config;print(config.DB);print(config.SEQ_DB)"], { cwd: ROOT, encoding: "utf8" });
  const [db, seq] = out.trim().split("\n");
  return { db, seq };
}

/* ----------------------------------------------------------- the server ---- */

async function startHarness(dir) {
  const child = spawn(PY, [path.join(ROOT, "tests", "seq_http_harness.py"), "--port", "0", "--test-hooks",
    "--db", path.join(dir, "mh2.db"), "--seq-db", path.join(dir, "mh2_seq.db")], { cwd: ROOT, stdio: ["ignore", "pipe", "pipe"] });
  let err = "";
  child.stderr.on("data", (d) => { err += d; });
  const base = await new Promise((resolve, reject) => {
    let buf = "";
    const t = setTimeout(() => reject(new Error("harness did not start: " + err)), 30000);
    child.stdout.on("data", (d) => {
      buf += d;
      const m = buf.match(/LISTENING (http:\/\/[^ ]+\/)/);
      if (m) { clearTimeout(t); resolve(m[1]); }
    });
    child.on("exit", (c) => { clearTimeout(t); reject(new Error("harness exited " + c + ": " + err)); });
  });
  return { child, base, stderr: () => err };
}

async function hook(base, name, body) {
  const r = await fetch(base + "__harness/" + name, { method: "POST", body: JSON.stringify(body || {}), headers: { "Content-Type": "application/json" } });
  const j = await r.json();
  if (!r.ok) throw new Error("hook " + name + ": " + JSON.stringify(j));
  return j;
}

/* -------------------------------------------------------------- shapes ---- */

// Objects whose keys are data (source keys, ids), not field names.
const MAP_KEYS = new Set(["placed_index", "slice_badges", "states", "drawers", "compare_columns", "differentiation_notes", "fields"]);

/** Recursively compare key sets. null / [] on either side is compatible with anything
 *  (a nullable value, or a list the demo documents as empty). Returns a list of diffs. */
function shapeDiff(a, b, p, out, parentKey) {
  out = out || []; p = p || "$";
  if (a === null || b === null || a === undefined || b === undefined) {
    if ((a === undefined) !== (b === undefined)) out.push(p + ": " + (a === undefined ? "missing on left" : "missing on right"));
    return out;
  }
  const ta = Array.isArray(a) ? "array" : typeof a, tb = Array.isArray(b) ? "array" : typeof b;
  if (ta !== tb) { out.push(p + ": type " + ta + " vs " + tb); return out; }
  if (ta === "array") {
    if (!a.length || !b.length) return out;
    // same length: element by element (rows / cells can be heterogeneous);
    // otherwise every element of each side against the first of the other
    if (a.length === b.length) { a.forEach((x, i) => shapeDiff(x, b[i], p + "[" + i + "]", out)); return out; }
    a.forEach((x, i) => shapeDiff(x, b[0], p + "[" + i + "]", out));
    b.forEach((x, i) => shapeDiff(a[0], x, p + "[~" + i + "]", out));
    return out;
  }
  if (ta !== "object") return out;
  if (MAP_KEYS.has(parentKey)) {
    const va = Object.values(a), vb = Object.values(b);
    if (va.length && vb.length) va.forEach((x, i) => shapeDiff(x, vb[0], p + "{" + i + "}", out)), vb.forEach((x, i) => shapeDiff(va[0], x, p + "{~" + i + "}", out));
    return out;
  }
  const ka = Object.keys(a).sort(), kb = Object.keys(b).sort();
  ka.filter((k) => !(k in b)).forEach((k) => out.push(p + "." + k + ": only on left"));
  kb.filter((k) => !(k in a)).forEach((k) => out.push(p + "." + k + ": only on right"));
  ka.filter((k) => k in b).forEach((k) => shapeDiff(a[k], b[k], p + "." + k, out, k));
  // dedupe
  return Array.from(new Set(out));
}

function positions(view) {
  const out = {};
  Object.keys(view.placed_index || {}).forEach((k) => { const x = view.placed_index[k]; out[k] = [x.module_position, x.slot_position, x.status, x.is_bridge]; });
  return out;
}
function flatPlacements(view) { return view.modules.flatMap((m) => m.slots.flatMap((s) => s.placements)); }

/* ------------------------------------------------------------ scenario ---- */

/** Run the scenario on one API. Returns {steps: {name: response|{error}}, finalView, keys}. */
async function scenario(api, label, opts) {
  const steps = {}, log = [];
  const G = "2";
  async function step(name, fn, expectError) {
    try {
      const r = await fn();
      if (expectError) throw new Error(label + " " + name + ": expected error " + expectError + ", got success");
      steps[name] = r; log.push(name); return r;
    } catch (e) {
      if (!(e instanceof ApiError)) throw e;
      if (expectError !== e.error) throw new Error(label + " " + name + ": unexpected ApiError " + e.status + " " + e.error + " " + e.message);
      steps[name] = { __error: { status: e.status, error: e.error, detail: e.detail } }; log.push(name);
      return e;
    }
  }
  let view = null;
  const rev = () => view.sequence.rev;
  const W = async (name, fn, expectError) => {            // a write: adopt the returned view
    const r = await step(name, fn, expectError);
    if (r && !(r instanceof ApiError) && r.sequence) view = r.sequence;
    if (r instanceof ApiError && r.detail && r.detail.sequence) view = r.detail.sequence;
    return r;
  };

  await step("whoami", () => api.whoami());
  const grades = await step("grades", () => api.grades());
  assert.ok(grades.grades.some((g) => g.grade === G), label + ": G2 in grades");
  const slice = await step("slice", () => api.slice(G));
  const byId = {};
  slice.super_stems.forEach((ss) => ss.stems.forEach((st) => st.concept_skills.forEach((c) => c.nodes.forEach((n) => { byId[n.node_id] = n; }))));
  const K = {};
  ["WHO-0010", "WHO-0011", "WHO-0012", "COM-0012"].forEach((id) => { assert.ok(byId[id], label + ": " + id + " in the G2 slice"); K[id] = byId[id].source_key; });
  assert.strictEqual(byId["WHO-0012"].requires_confirm, true, "WHO-0012 is off-grade in G2");
  await step("drawer", () => api.node(K["WHO-0010"], G));
  const cmp = await step("compare3", () => api.compare([K["COM-0012"], K["WHO-0010"], K["WHO-0011"]], G));
  assert.strictEqual(cmp.columns.length, 3);
  await step("compare5_invalid", () => api.compare([K["COM-0012"], K["WHO-0010"], K["WHO-0011"], K["WHO-0012"], byId["WHO-0013"] ? byId["WHO-0013"].source_key : "x:y"], G), "invalid");
  await step("node_404", () => api.node("NOPE:0000000000000000", G), "not_found");
  view = await step("sequence_empty", () => api.sequence(G));
  assert.strictEqual(view.sequence, null);

  const cs = await W("create_sequence", () => api.createSequence(G, "Grade 2 sequence"));
  const sid = cs.result.sequence_id;
  await W("create_sequence_again", () => api.createSequence(G, "Second"), "sequence_exists");
  const m1 = (await W("create_module_1", () => api.createModule(sid, rev(), "M1 Place value"))).result.module_id;
  const m2 = (await W("create_module_2", () => api.createModule(sid, rev(), "M2 Comparing numbers"))).result.module_id;

  await W("place_who11", () => api.place(sid, rev(), m1, K["WHO-0011"], {}));
  const p10 = await W("place_who10", () => api.place(sid, rev(), m2, K["WHO-0010"], {}));
  await W("place_com12", () => api.place(sid, rev(), m2, K["COM-0012"], {}));
  const nc = await W("place_who12_unconfirmed", () => api.place(sid, rev(), m2, K["WHO-0012"], {}), "confirm_off_grade_required");
  assert.deepStrictEqual(nc.detail.source_keys, [K["WHO-0012"]]); assert.strictEqual(nc.detail.states[K["WHO-0012"]], "off_grade");
  await W("place_who12_bridge", () => api.place(sid, rev(), m2, K["WHO-0012"], { confirm_off_grade: true }));
  await W("place_dup", () => api.place(sid, rev(), m2, K["WHO-0010"], {}), "already_placed");
  const pid = (k) => view.placed_index[k].placement_id, slotOf = (k) => view.placed_index[k].slot_id;
  assert.strictEqual(view.placed_index[K["WHO-0012"]].is_bridge, true);
  assert.ok(view.slice_badges[K["WHO-0012"]].some((b) => b.code === "bridge"));

  // co-place COM-0012 into WHO-0010's slot (the §5.6 worked example)
  await W("co_place", () => api.coPlace(pid(K["COM-0012"]), rev(), slotOf(K["WHO-0010"])));
  assert.strictEqual(slotOf(K["COM-0012"]), slotOf(K["WHO-0010"]));
  // reorder inside M2, then back; module chevrons; edge
  await W("slot_up", () => api.moveSlot(slotOf(K["WHO-0012"]), rev(), { direction: "up" }));
  assert.strictEqual(view.placed_index[K["WHO-0012"]].slot_position, 1);
  await W("slot_down", () => api.moveSlot(slotOf(K["WHO-0012"]), rev(), { direction: "down" }));
  await W("module_up", () => api.moveModule(m2, rev(), "up"));
  assert.strictEqual(view.modules[0].module_id, m2);
  await W("module_down", () => api.moveModule(m2, rev(), "down"));
  await W("module_at_edge", () => api.moveModule(m1, rev(), "up"), "at_edge");
  await W("slot_at_edge", () => api.moveSlot(slotOf(K["WHO-0011"]), rev(), { direction: "up" }), "at_edge");
  // ungroup and merge back
  await W("ungroup", () => api.ungroup(pid(K["COM-0012"]), rev()));
  assert.notStrictEqual(slotOf(K["COM-0012"]), slotOf(K["WHO-0010"]));
  await W("ungroup_alone", () => api.ungroup(pid(K["WHO-0011"]), rev()), "already_alone");
  await W("merge", () => api.mergeSlot(slotOf(K["COM-0012"]), rev(), slotOf(K["WHO-0010"])));
  assert.strictEqual(slotOf(K["COM-0012"]), slotOf(K["WHO-0010"]));
  // move between modules: bridge to M1 (end), then "down" across the boundary to M2's start
  await W("slot_to_m1", () => api.moveSlot(slotOf(K["WHO-0012"]), rev(), { to_module_id: m1 }));
  assert.deepStrictEqual([view.placed_index[K["WHO-0012"]].module_id, view.placed_index[K["WHO-0012"]].slot_position], [m1, 2]);
  await W("slot_cross_down", () => api.moveSlot(slotOf(K["WHO-0012"]), rev(), { direction: "down" }));
  assert.deepStrictEqual([view.placed_index[K["WHO-0012"]].module_id, view.placed_index[K["WHO-0012"]].slot_position], [m2, 1]);
  await W("slot_cross_up", () => api.moveSlot(slotOf(K["WHO-0012"]), rev(), { direction: "up" }));
  assert.strictEqual(view.placed_index[K["WHO-0012"]].module_id, m1);
  // labels and titles
  await W("slot_label", () => api.updateSlot(slotOf(K["WHO-0010"]), rev(), "Co-taught: compare"));
  await W("module_title", () => api.updateModule(m1, rev(), { title: "M1 Place value and order" }));
  await W("sequence_title", () => api.updateSequence(sid, rev(), { title: "Grade 2 sequence (draft)" }));

  // calibration + periods (placement rev; the sequence rev does not move)
  const prevSeqRev = rev();
  const plRev = (k) => flatPlacements(view).find((p) => p.source_key === k).rev;
  const cal = [["WHO-0011", "functional", 1], ["WHO-0010", "deep", 1.5], ["COM-0012", "deep", 0.5], ["WHO-0012", "illuminating", null]];
  for (const [id, c, per] of cal) {
    await W("cal_" + id, () => api.updatePlacement(pid(K[id]), plRev(K[id]), { calibration: c }));
    if (per !== null) await W("per_" + id, () => api.updatePlacement(pid(K[id]), plRev(K[id]), { period_estimate: per }));
  }
  await W("note_who10", () => api.updatePlacement(pid(K["WHO-0010"]), plRev(K["WHO-0010"]), { differentiation_note: "Within 1,000 here" }));
  assert.strictEqual(rev(), prevSeqRev, label + ": attribute PATCH must not bump the sequence rev");
  await W("placement_stale", () => api.updatePlacement(pid(K["WHO-0010"]), plRev(K["WHO-0010"]) - 1, { calibration: "functional" }), "stale_revision");
  await W("placement_bad_cal", () => api.updatePlacement(pid(K["WHO-0010"]), plRev(K["WHO-0010"]), { calibration: "loud" }), "invalid");
  const who10 = flatPlacements(view).find((p) => p.source_key === K["WHO-0010"]);
  assert.strictEqual(who10.calibration, "deep"); assert.strictEqual(who10.period_estimate, 1.5); assert.strictEqual(who10.differentiation_note, "Within 1,000 here");

  const gr = await step("guardrail", () => api.guardrail(G));
  assert.deepStrictEqual(gr.sequence, view.guardrail);
  assert.strictEqual(gr.sequence.n, 4); assert.deepStrictEqual(gr.sequence.counts, { deep: 2, functional: 1, illuminating: 1, unset: 0 });
  assert.strictEqual(gr.sequence.total_periods, 3); assert.strictEqual(gr.sequence.show_time_targets, true);
  await step("attention_empty", () => api.attention(G));
  const ev = await step("events", () => api.events(sid, 50));
  assert.ok(ev.events.length >= 10, label + ": events recorded");

  // saved views
  const lens = { hidden_stems: [], hidden_cs: [], collapsed_stems: null, stem_order: [], context: true, leaves: true, state_ext: true, unplaced_only: false, pairings: true, flagged: false };
  const v1 = await step("view_create", () => api.createView(G, "Pairings and leaves", lens));
  await step("view_list", () => api.listViews(G));
  await step("view_dup", () => api.createView(G, "Pairings and leaves", lens), "view_name_exists");
  await step("view_rename", () => api.updateView(v1.view_id, { name: "Pairings" }));
  await step("view_delete", () => api.deleteView(v1.view_id));

  // structural stale revision: carries the current view
  const staleRev = rev() - 1;
  const st = await W("stale_structural", () => api.createModule(sid, staleRev, "Nope"), "stale_revision");
  assert.strictEqual(st.detail.current_rev, staleRev + 1); assert.strictEqual(st.detail.sequence.sequence.rev, staleRev + 1);
  assert.strictEqual(view.modules.length, 2);
  await W("module_not_empty", () => api.removeModule(m1, rev()), "module_not_empty");
  const m3 = (await W("create_module_3", () => api.createModule(sid, rev(), "M3 Spare"))).result.module_id;
  await W("remove_module_3", () => api.removeModule(m3, rev()));
  // place-then-remove a node
  const extra = byId["WHO-0013"] && !byId["WHO-0013"].requires_confirm ? "WHO-0013" : null;
  const tmpKey = extra ? K[extra] = byId[extra].source_key : null;
  if (tmpKey) {
    await W("place_tmp", () => api.place(sid, rev(), m2, tmpKey, {}));
    await W("remove_tmp", () => api.removePlacement(pid(tmpKey), rev(), null));
  }
  const sv = await step("sequence_final", () => api.sequence(G));
  return { steps, log, view: sv, K, sid, m1, m2 };
}

/* ---------------------------------------------------- controller smoke ---- */

function makeDoc() {
  const doc = { els: {}, activeElement: null, listeners: {} };
  doc.getElementById = (id) => {
    if (id === "seq-demo-data") return null;
    if (!doc.els[id]) {
      const el = { id, innerHTML: "", hidden: id === "drawer" || id === "sheet" || id === "tray", scrollTop: 0, listeners: {}, attrs: {}, style: { setProperty() {} },
        classList: { toggle() {}, add() {} }, addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); },
        getAttribute(n) { return Object.prototype.hasOwnProperty.call(this.attrs, n) ? this.attrs[n] : null; }, closest() { return null; }, querySelector() { return null; }, focus() { doc.activeElement = this; } };
      doc.els[id] = el;
    }
    return doc.els[id];
  };
  doc.querySelector = () => null;
  doc.addEventListener = (t, f) => { (doc.listeners[t] = doc.listeners[t] || []).push(f); };
  ["app", "hdr", "banner", "toolbar", "rail", "slice", "drawer", "sheet", "tray", "notice", "toasts", "foot"].forEach((i) => doc.getElementById(i));
  return doc;
}

/* ---------------------------------------------------------------- main ---- */

(async () => {
  const cfg = cfgPaths();
  if (!fs.existsSync(cfg.db)) { console.log("SKIP seq_e2e: no mh2.db at " + cfg.db); process.exit(0); }
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "seqe2e_"));
  fs.copyFileSync(cfg.db, path.join(dir, "mh2.db"));
  if (fs.existsSync(cfg.seq)) fs.copyFileSync(cfg.seq, path.join(dir, "mh2_seq.db"));
  let H = null, failed = 0;
  const bail = () => { try { if (H) H.child.kill(); fs.rmSync(dir, { recursive: true, force: true }); } catch (e) { /* ignore */ } process.exit(130); };
  process.on("SIGINT", bail); process.on("SIGTERM", bail);
  const ok = (name) => console.log("ok " + name);
  const check = async (name, fn) => { try { await fn(); ok(name); } catch (e) { failed += 1; console.log("FAIL " + name + "\n" + (e && e.stack ? e.stack : e)); } };
  try {
    H = await startHarness(dir);
    const base = H.base;
    const http = new HttpApi(base + "api/seq/", fetch);

    await check("static: /seq redirects, page and whitelisted assets serve, others 404", async () => {
      let r = await fetch(base + "seq", { redirect: "manual" }); assert.strictEqual(r.status, 307); assert.strictEqual(r.headers.get("location"), "seq/");
      r = await fetch(base + "seq/"); assert.strictEqual(r.status, 200); const html = await r.text();
      assert.ok(html.includes('<script src="assets/app.js"></script>') && html.includes('href="assets/app.css"'));
      r = await fetch(base + "seq/assets/app.js"); assert.strictEqual(r.status, 200); assert.ok(r.headers.get("content-type").startsWith("text/javascript"));
      r = await fetch(base + "seq/assets/app.css"); assert.strictEqual(r.status, 200);
      r = await fetch(base + "seq/assets/index.html"); assert.strictEqual(r.status, 404);
      r = await fetch(base + "seq/assets/..%2Fseq_api.py"); assert.strictEqual(r.status, 404);
      // HttpApi's base as the page computes it
      assert.strictEqual(new URL("../api/seq/", base + "seq/").href, base + "api/seq/");
    });

    // A G3 sequence first, so G2 sees placed_elsewhere for WHO-0010.
    let g3key = null;
    await check("setup: a G3 placement for the cross-grade overlay", async () => {
      const s3 = await http.slice("3");
      s3.super_stems.forEach((ss) => ss.stems.forEach((st) => st.concept_skills.forEach((c) => c.nodes.forEach((n) => { if (n.node_id === "WHO-0010") g3key = n.source_key; }))));
      let r = await http.createSequence("3", "Grade 3 sequence");
      r = await http.createModule(r.result.sequence_id, r.sequence.sequence.rev, "M1 Place value to 1,000");
      r = await http.place(r.sequence.sequence.sequence_id, r.sequence.sequence.rev, r.result.module_id, g3key, {});
      assert.strictEqual(r.sequence.placed_index[g3key].module_position, 1);
    });

    // The demo payload is exported from the same DB copy, before any G2 write.
    const payload = await hook(base, "demo_payload", { grade: "2", with_sequence: true });
    assert.strictEqual(payload.format, "mh2-seq-demo/1");

    let H1 = null, D1 = null;
    await check("scenario over HTTP (real HttpApi -> harness -> seq_service)", async () => { H1 = await scenario(http, "http"); });
    await check("the same scenario over DemoApi seeded from the real export", async () => { D1 = await scenario(new DemoApi(JSON.parse(JSON.stringify(payload)), null), "demo"); });

    await check("parity: every step has the same outcome and the same response key sets", async () => {
      assert.deepStrictEqual(D1.log, H1.log);
      const diffs = [];
      H1.log.forEach((name) => {
        const h = H1.steps[name], d = D1.steps[name];
        if (h && h.__error) {
          assert.ok(d.__error, name + ": demo succeeded where HTTP failed");
          assert.strictEqual(d.__error.error, h.__error.error, name); assert.strictEqual(d.__error.status, h.__error.status, name);
          shapeDiff(h.__error.detail, d.__error.detail, name + ".detail").forEach((x) => diffs.push(x));
        } else {
          shapeDiff(h, d, name).forEach((x) => diffs.push(x));
          if (h && h.result) assert.deepStrictEqual(Object.keys(d.result).sort(), Object.keys(h.result).sort(), name + " result keys");
        }
      });
      assert.deepStrictEqual(diffs, []);
    });

    await check("parity: placement positions, statuses and bridges match after every write", async () => {
      H1.log.forEach((name) => {
        const h = H1.steps[name], d = D1.steps[name];
        const hv = h && h.sequence && h.sequence.placed_index ? h.sequence : (h && h.__error && h.__error.detail.sequence) || null;
        const dv = d && d.sequence && d.sequence.placed_index ? d.sequence : (d && d.__error && d.__error.detail.sequence) || null;
        if (hv && dv) assert.deepStrictEqual(positions(dv), positions(hv), name);
      });
    });

    await check("parity: guardrail (sequence and per module) equal between server and DemoApi", async () => {
      assert.deepStrictEqual(D1.view.guardrail, H1.view.guardrail);
      assert.deepStrictEqual(D1.view.modules.map((m) => m.guardrail), H1.view.modules.map((m) => m.guardrail));
    });

    await check("guardrail mirror: JS computeGuardrail == Python seq_guardrail on the final state", async () => {
      const pl = flatPlacements(H1.view).map((p) => ({ calibration: p.calibration, period_estimate: p.period_estimate }));
      const py = await hook(base, "guardrail", { placements: pl });
      assert.deepStrictEqual(computeGuardrail(pl), py);
      assert.deepStrictEqual(py, H1.view.guardrail);
      for (const m of H1.view.modules) {
        const mp = m.slots.flatMap((s) => s.placements).map((p) => ({ calibration: p.calibration, period_estimate: p.period_estimate }));
        assert.deepStrictEqual(computeGuardrail(mp), await hook(base, "guardrail", { placements: mp }));
      }
      // and a few odd lists
      for (const pl2 of [[], [{ calibration: null, period_estimate: 0.125 }, { calibration: "illuminating", period_estimate: 0.375 }], [{ calibration: "deep", period_estimate: null }]]) {
        assert.deepStrictEqual(computeGuardrail(pl2), await hook(base, "guardrail", { placements: pl2 }));
      }
    });

    await check("server-only: ordering warnings and cross-grade overlay reach the page", async () => {
      const who11 = flatPlacements(H1.view).find((p) => p.source_key === H1.K["WHO-0011"]);
      assert.ok(who11.badges.some((b) => b.code === "before_predecessor"), JSON.stringify(who11.badges));
      assert.ok(H1.view.slice_badges[H1.K["WHO-0011"]].some((b) => b.code === "before_predecessor"));
      const who10 = flatPlacements(H1.view).find((p) => p.source_key === H1.K["WHO-0010"]);
      assert.strictEqual(who10.placed_elsewhere.length, 1); assert.strictEqual(who10.placed_elsewhere[0].grade, "3");
      assert.deepStrictEqual(H1.steps.place_who10.result.placed_elsewhere, who10.placed_elsewhere);
      // DemoApi takes placed_elsewhere from the exported slice: same refs
      assert.deepStrictEqual(D1.steps.place_who10.result.placed_elsewhere.map((r) => r.grade), ["3"]);
      const s2 = await http.slice("2");
      const n = s2.super_stems.flatMap((ss) => ss.stems.flatMap((st) => st.concept_skills.flatMap((c) => c.nodes))).find((x) => x.node_id === "WHO-0010");
      assert.ok(n.badges.some((b) => b.code === "placed_elsewhere" && b.label === "also G3"), JSON.stringify(n.badges));
    });

    await check("reconcile: a reworded node shows as an orphan with a suggestion; reattach clears it", async () => {
      const K = H1.K, before = flatPlacements(H1.view).find((p) => p.source_key === K["WHO-0011"]);
      const rw = await hook(base, "reword", { node_id: "WHO-0011", suffix: " today" });
      assert.strictEqual(rw.old_key, K["WHO-0011"]);
      const v = await http.sequence("2");
      assert.strictEqual(v.placed_index[K["WHO-0011"]].status, "orphaned");
      const att = await http.attention("2");
      assert.strictEqual(att.items.length, 1); const it = att.items[0];
      assert.strictEqual(it.status, "orphaned"); assert.deepStrictEqual(it.actions, ["reattach", "remove"]);
      assert.ok(it.suggestions.length >= 1 && it.suggestions[0].source_key === rw.new_key, JSON.stringify(it.suggestions));
      const orphanPv = flatPlacements(v).find((p) => p.placement_id === it.placement_id);
      assert.ok(orphanPv.badges.some((b) => b.code === "orphaned")); assert.strictEqual(orphanPv.node_id, null);
      // shape parity of the orphan: DemoApi seeded with the server's view keeps the status
      const p2 = await hook(base, "demo_payload", { grade: "2", with_sequence: true });
      const demo = new DemoApi(p2, null);
      const dv = await demo.sequence("2");
      assert.deepStrictEqual(shapeDiff(v, dv, "orphan_view"), []);
      assert.strictEqual(dv.attention.length, 1); assert.strictEqual(dv.placed_index[K["WHO-0011"]].status, "orphaned");
      // reattach on both
      const r = await http.reattach(it.placement_id, v.sequence.rev, rw.new_key);
      assert.deepStrictEqual(r.sequence.attention, []);
      const after = flatPlacements(r.sequence).find((p) => p.placement_id === it.placement_id);
      assert.strictEqual(after.source_key, rw.new_key); assert.strictEqual(after.status, "ok");
      assert.strictEqual(after.calibration, before.calibration); assert.strictEqual(after.period_estimate, before.period_estimate);
      const dr = await demo.reattach(it.placement_id, dv.sequence.rev, rw.new_key);
      assert.deepStrictEqual(dr.sequence.attention, []);
      assert.deepStrictEqual(shapeDiff(r, dr, "reattach"), []);
      assert.deepStrictEqual(positions(dr.sequence), positions(r.sequence));
    });

    await check("reconcile: a changed grade ruling is grade_changed; acknowledge (sequence rev) keeps it", async () => {
      const K = H1.K;
      const r0 = await hook(base, "set_kind", { node_id: "COM-0012", grade: "2", kind: "state_extension" });
      assert.strictEqual(r0.updated, 1);
      const v = await http.sequence("2");
      const it = v.attention.find((a) => a.source_key === K["COM-0012"]);
      assert.ok(it, JSON.stringify(v.attention)); assert.strictEqual(it.status, "grade_changed");
      assert.deepStrictEqual(it.actions, ["acknowledge", "remove"]); assert.strictEqual(it.state_now, "state_extension");
      const pv = flatPlacements(v).find((p) => p.source_key === K["COM-0012"]);
      assert.ok(pv.badges.some((b) => b.code === "grade_changed" && b.label.includes("state_extension")));
      assert.ok(v.slice_badges[K["COM-0012"]].some((b) => b.code === "grade_changed"));
      // demo seeded from this server view shows the same item, and acknowledges the same way
      const demo = new DemoApi(await hook(base, "demo_payload", { grade: "2", with_sequence: true }), null);
      const dv = await demo.sequence("2");
      assert.deepStrictEqual(shapeDiff(v, dv, "grade_changed_view"), []);
      const a = await http.acknowledge(it.placement_id, v.sequence.rev);
      assert.ok(!a.sequence.attention.some((x) => x.placement_id === it.placement_id));
      const da = await demo.acknowledge(it.placement_id, dv.sequence.rev);
      assert.ok(!da.sequence.attention.some((x) => x.placement_id === it.placement_id));
      assert.deepStrictEqual(shapeDiff(a, da, "acknowledge"), []);
      const after = flatPlacements(a.sequence).find((p) => p.placement_id === it.placement_id);
      assert.strictEqual(after.status, "ok"); assert.strictEqual(after.grade_kind_seen, "state_extension");
      // stale acknowledge is refused with the current view
      await assert.rejects(http.acknowledge(it.placement_id, v.sequence.rev), (e) => e.error === "stale_revision" && !!e.detail.sequence);
    });

    await check("errors: FastAPI-style validation list and a bad key both become ApiError", async () => {
      const v = await http.sequence("2");
      await assert.rejects(http.createModule(v.sequence.sequence_id, "not-a-number", "x"), (e) => e instanceof ApiError && e.status === 422 && e.error === "invalid");
      await assert.rejects(http.slice("Z9"), (e) => e.status === 404 && e.error === "not_found");
      await assert.rejects(http.events(999999, 50), (e) => e.status === 404);
    });

    await check("controller: the page (stub DOM) drives the live server through HttpApi", async () => {
      const doc = makeDoc(), R = doc.els;
      const app = A.createApp({ doc, api: http, hist: { replaceState() {} }, loc: { search: "?grade=2" }, storage: null, setTimeout() { return 0; } });
      await app.init();
      const M = app.M;
      assert.strictEqual(M.grade, "2"); assert.ok(M.seqView.sequence, "the scenario's sequence loads");
      assert.ok(R.rail.innerHTML.includes("M1 Place value and order"));
      const key = Object.keys(M.index).find((k) => M.index[k].owed && !M.seqView.placed_index[k] && !(M.index[k].placed_elsewhere || []).length);
      const n0 = M.seqView.guardrail.n;
      await app.act("place-chip", { id: key });
      assert.strictEqual(M.seqView.guardrail.n, n0 + 1, JSON.stringify(M.ui.toasts));
      const pid = M.seqView.placed_index[key].placement_id;
      await app.act("set-cal", { id: pid, val: "functional" });
      await app.act("set-period", { id: pid, value: "0.75" });
      const p = flatPlacements(M.seqView).find((x) => x.placement_id === pid);
      assert.strictEqual(p.calibration, "functional"); assert.strictEqual(p.period_estimate, 0.75);
      await app.act("slot-move", { id: M.seqView.placed_index[key].slot_id, val: "up" });
      await app.act("disclose", { id: "history" });
      assert.ok(R.rail.innerHTML.includes("set calibration"));
      // a second "window" writes; our next structural write gets the 409 notice and the fresh view
      const s = M.seqView.sequence;
      await http.createModule(s.sequence_id, s.rev, "From the other window");
      await app.act("add-module");
      assert.ok(M.ui.notice && M.ui.notice.text.includes("Someone else changed this sequence"), JSON.stringify(M.ui.notice));
      assert.ok(M.seqView.modules.some((m) => m.title === "From the other window"));
      await app.act("remove-placement", { id: pid }); await app.act("remove-placement", { id: pid });
      assert.strictEqual(M.seqView.placed_index[key], undefined);
      Object.keys(R).forEach((id) => assert.ok(!/undefined|NaN|\[object/.test(R[id].innerHTML), id + ": " + (R[id].innerHTML.match(/.{30}(undefined|NaN|\[object).{30}/) || [""])[0]));
      assert.ok(!R.rail.innerHTML.includes("Ordering warnings need the server"));
    });

    await check("dev identity: X-MH2-User attributes writes; a client-sent placed_by is ignored", async () => {
      const v = await http.sequence("2");
      const mod = v.modules[0].module_id;
      const key = Object.keys(v.placed_index).length ? null : null;
      const r = await fetch(base + "api/seq/sequences/" + v.sequence.sequence_id + "/modules", { method: "POST", headers: { "Content-Type": "application/json", "X-MH2-User": "amy" },
        body: JSON.stringify({ expected_rev: v.sequence.rev, title: "Amy's module", created_by: "evil", placed_by: "evil" }) });
      assert.strictEqual(r.status, 200);
      const ev = await http.events(v.sequence.sequence_id, 1);
      assert.strictEqual(ev.events[0].actor, "amy"); assert.strictEqual(ev.events[0].action, "module_create");
      const bad = await fetch(base + "api/seq/whoami", { headers: { "X-MH2-User": "a b" } });
      assert.deepStrictEqual(await bad.json(), { user: "local", auth_enabled: false, source: "default" });
      void mod; void key;
    });
  } catch (e) {
    failed += 1; console.log("FAIL setup\n" + (e && e.stack ? e.stack : e) + (H ? "\n" + H.stderr() : ""));
  } finally {
    if (H) H.child.kill();
    fs.rmSync(dir, { recursive: true, force: true });
  }
  console.log(failed ? "\n" + failed + " FAILED" : "\nall e2e checks passed");
  process.exit(failed ? 1 : 0);
})();
