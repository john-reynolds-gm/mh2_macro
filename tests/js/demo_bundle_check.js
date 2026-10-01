/* Runs the inline app script of a built demo bundle under node with a tiny stub
 * and checks that DemoApi loads the embedded payload.
 *   node tests/js/demo_bundle_check.js path/to/seq_demo_g2.html
 * Prints one JSON line: {"ok": true, "grade": "2", "chips": 57, "nodes": 57}.
 */
"use strict";
const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync(process.argv[2], "utf8");
const dataM = html.match(/<script id="seq-demo-data" type="application\/json">([\s\S]*?)<\/script>/);
if (!dataM) { console.log(JSON.stringify({ ok: false, why: "no embedded payload" })); process.exit(1); }
const payload = JSON.parse(dataM[1]);
const scripts = [...html.matchAll(/<script>\n([\s\S]*?)\n<\/script>/g)].map((m) => m[1]);
if (scripts.length !== 1) { console.log(JSON.stringify({ ok: false, why: "expected one inline app script, found " + scripts.length })); process.exit(1); }

const sandbox = { module: { exports: {} }, console, setTimeout, clearTimeout, URL };
vm.createContext(sandbox);
vm.runInContext(scripts[0], sandbox);
const { DemoApi } = sandbox.module.exports;
const store = new Map();
const api = new DemoApi(JSON.parse(dataM[1]), { getItem: (k) => store.get(k) || null, setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) });
(async () => {
  const sl = await api.slice(payload.grade);
  const same = JSON.stringify(sl) === JSON.stringify(payload.slice);
  let chips = 0;
  sl.super_stems.forEach((s) => s.stems.forEach((st) => st.concept_skills.forEach((c) => { chips += c.nodes.length; })));
  const node = Object.keys(payload.drawers)[0];
  const d = await api.node(node, payload.grade);
  const r = await api.createSequence(payload.grade, "check");
  const ok = same && chips === sl.counts.chips && d.source_key === node && r.sequence.sequence.title === "check";
  console.log(JSON.stringify({ ok, grade: payload.grade, chips, nodes: Object.keys(payload.drawers).length, source: payload.source }));
  process.exit(ok ? 0 : 1);
})().catch((e) => { console.log(JSON.stringify({ ok: false, why: String(e) })); process.exit(1); });
