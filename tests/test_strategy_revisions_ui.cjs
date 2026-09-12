"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
const {JSDOM} = require("jsdom");
const root = path.resolve(__dirname, "..");
const definition = {display_name: "Original pipeline", description: "Original description", perceive: {endpoint: "vision", timeout_ms: 5000}, plan: "planner", act: null, verify: {endpoint: "grader", mode: "auto", checks: [{endpoint: "constraint", required: true, role: "constraint"}]}, sim: null, pipeline_mode: "sequential", verify_mode: "auto", tags: ["original"], latency_budget: {plan: 4000}};
async function setup(options = {}) {
  const dom = new JSDOM(fs.readFileSync(path.join(root, "frontend/datasets.html"), "utf8"), {url: "http://localhost/static/datasets.html", runScripts: "outside-only", pretendToBeVisual: true});
  const w = dom.window, calls = [], selections = [], el = id => w.document.getElementById(id), pause = () => new Promise(resolve => setTimeout(resolve, 15));
  el("campaignStrategy").replaceChildren(new w.Option("Original", "original")); el("baselineStrategy").replaceChildren(new w.Option("Baseline source", "baseline-source"));
  const endpoints = [{id: "vision", display_name: "Vision", stages: ["perceive"]}, {id: "new-vision", display_name: "New vision", stages: ["perceive"]}, {id: "planner", stages: ["plan"]}, {id: "grader", stages: ["verify"]}, {id: "vla", stages: ["act"]}, {id: "constraint", stages: ["verify"]}];
  let previews = 0;
  w.fetch = async (url, opts = {}) => {
    const body = opts.body ? JSON.parse(opts.body) : null; calls.push({url, method: opts.method || "GET", body}); let result;
    if (url === "/api/strategies") result = {strategies: [{id: "original", display_name: "Original"}, {id: "baseline-source", display_name: "Baseline source"}]};
    else if (url.startsWith("/api/strategy-revisions/parents/")) result = {strategy_id: url.split("/").at(-1), fingerprint: "a".repeat(64), definition, endpoints};
    else if (url === "/api/strategy-revisions/preview") { if (options.previewWait && !previews++) await options.previewWait; result = {strategy_id: "revision_123", preview_hash: "b".repeat(64), differences: [{path: "perceive.endpoint", before: "vision", after: body.definition.perceive?.endpoint || body.definition.perceive}], definition: body.definition}; }
    else if (url === "/api/strategy-revisions") result = {strategy_id: "revision_123", definition: body.definition};
    else throw Error(`Unexpected request ${url}`);
    return {ok: true, status: 200, json: async () => result};
  };
  w.refreshStrategyCatalog = async (...args) => { selections.push(args); if (options.refreshError) throw Error("Temporary catalog error"); };
  w.eval(fs.readFileSync(path.join(root, "frontend/strategy-revisions.js"), "utf8"));
  const set = (id, value, type = "input") => { el(id).value = value; el(id).dispatchEvent(new w.Event(type, {bubbles: true})); };
  const open = async candidate => { const button = el(candidate ? "createCandidateStrategyRevision" : "createCampaignStrategyRevision"); button.focus(); button.click(); await pause(); };
  const preview = async () => { el("previewStrategyRevision").click(); await pause(); };
  const save = async () => { el("strategyRevisionForm").dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true})); await pause(); };
  return {dom, w, el, calls, selections, pause, set, open, preview, save};
}

test("strategy editor previews compatible endpoint changes, preserves structured settings and saves without running", async () => {
  const {dom, el, calls, selections, set, open, preview, save} = await setup();
  try {
    await open(false); assert.equal(el("strategyRevisionDialog").open, true);
    assert.match(el("strategyRevisionSourceNotice").textContent, /not the archived baseline/);
    assert.deepEqual([...el("revisionStage_perceive").options].map(x => x.value), ["", "vision", "new-vision"]);
    assert.equal([...el("revisionStage_verify").options].some(x => !x.value), false);
    set("revisionName", "Vision candidate"); set("revisionStage_perceive", "new-vision", "change");
    await preview(); assert.equal(el("saveStrategyRevision").disabled, false);
    assert.match(el("strategyRevisionDiff").textContent, /revision_123.*perceive.endpoint.*vision.*new-vision/);
    const payload = calls.find(x => x.url.endsWith("/preview")).body;
    assert.equal(payload.definition.perceive.timeout_ms, 5000); assert.deepEqual(payload.definition.verify.checks, definition.verify.checks);
    assert.deepEqual(payload.definition.tags, ["original"]); assert.deepEqual(payload.definition.latency_budget, {plan: 4000});
    await save(); assert.equal(el("strategyRevisionDialog").open, false); assert.deepEqual(selections, [["revision_123", false]]);
    const saved = calls.find(x => x.url === "/api/strategy-revisions").body;
    assert.equal(saved.expected_parent_fingerprint, "a".repeat(64)); assert.equal(saved.expected_preview_hash, "b".repeat(64)); assert.ok(saved.operation_id);
    assert.equal(calls.some(x => /campaigns|evaluate/.test(x.url)), false); assert.match(el("campaignRevisionMessage").textContent, /Saved Vision candidate/);
  } finally { dom.window.close(); }
});

test("candidate editor starts from selected baseline source and saves only into candidate selection", async () => {
  const {dom, el, calls, selections, open, preview, save} = await setup();
  try {
    await open(true); assert.equal(el("revisionParent").value, "baseline-source");
    assert.ok(calls.some(x => x.url.endsWith("/parents/baseline-source")));
    await preview(); await save(); assert.deepEqual(selections, [["revision_123", true]]);
    assert.match(el("candidateRevisionMessage").textContent, /preview the comparison before running/);
  } finally { dom.window.close(); }
});

test("editing after a preview disables saving and stale preview responses cannot enable it", async () => {
  let release; const previewWait = new Promise(resolve => { release = resolve; });
  const {dom, el, calls, set, open, preview, save, pause} = await setup({previewWait});
  try {
    await open(); el("previewStrategyRevision").click(); await pause(); set("revisionName", "Changed during preview"); release(); await pause();
    assert.equal(el("saveStrategyRevision").disabled, true); assert.equal(el("previewStrategyRevision").disabled, false);
    await save(); assert.equal(calls.some(x => x.url === "/api/strategy-revisions"), false);
    await preview(); assert.equal(el("saveStrategyRevision").disabled, false);
    set("revisionVerifyMode", "precompute", "change"); assert.equal(el("saveStrategyRevision").disabled, true);
    await preview(); const latest = calls.filter(x => x.url.endsWith("/preview")).at(-1).body;
    assert.equal(latest.definition.verify_mode, "precompute"); assert.equal(latest.definition.verify.mode, "precompute");
  } finally { release(); dom.window.close(); }
});

test("advanced definition is explicit and invalid JSON never reaches the server", async () => {
  const {dom, w, el, calls, open, preview, set} = await setup();
  try {
    await open(); el("revisionUseJson").click(); assert.equal(el("revisionName").disabled, true); assert.equal(el("revisionJson").disabled, false);
    set("revisionJson", "{"); await preview(); assert.match(el("strategyRevisionStatus").textContent, /valid JSON/);
    assert.equal(calls.some(x => x.url.endsWith("/preview")), false);
    const full = {...definition, display_name: "Advanced revision", compute_dynamics: false}; set("revisionJson", JSON.stringify(full)); await preview();
    assert.deepEqual(calls.find(x => x.url.endsWith("/preview")).body.definition, full);
    el("strategyRevisionDialog").dispatchEvent(new w.Event("cancel", {cancelable: true})); assert.equal(el("strategyRevisionDialog").open, false);
    assert.equal(w.document.activeElement, el("createCampaignStrategyRevision"));
  } finally { dom.window.close(); }
});

test("successful save followed by catalog refresh failure cannot create a duplicate revision", async () => {
  const {dom, el, calls, open, preview, save} = await setup({refreshError: true});
  try {
    await open(); await preview(); await save(); assert.match(el("strategyRevisionStatus").textContent, /was saved.*list could not refresh/);
    assert.equal(el("saveStrategyRevision").disabled, true); await save();
    assert.equal(calls.filter(x => x.url === "/api/strategy-revisions").length, 1);
    assert.equal(el("closeStrategyRevision").disabled, false);
  } finally { dom.window.close(); }
});
