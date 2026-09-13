"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
const {JSDOM} = require("jsdom");
const source = name => fs.readFileSync(path.resolve(__dirname, "../frontend", name), "utf8");
async function composer(options = {}) {
  const dom = new JSDOM(source("index.html"), {url: "http://localhost/?view=quick&case=case-r1", runScripts: "outside-only", pretendToBeVisual: true});
  const w = dom.window, calls = [];
  w.lucide = {createIcons(){}}; w.HTMLElement.prototype.scrollIntoView = () => {};
  w.URL.createObjectURL = () => "blob:observation";
  const streams = [];
  w.EventSource = class { constructor(){this.listeners = {}; streams.push(this);} addEventListener(type, listener){this.listeners[type] = listener;} close(){} emit(type, data){this.listeners[type]?.({data: JSON.stringify(data)});} };
  const item = {id: "case-r1", case_id: "case", name: "Block on tray", task: "Place the block in the tray", revision: 1, image_asset: {sha256: "a".repeat(64)}, candidate_context: {robot: "panda", proprioception: [0, 1], constraints: ["Avoid cup"]}, reference_data: {eval_qa: {answer: "private answer"}, expected_subtasks: ["private plan"]}};
  w.fetch = async (url, request = {}) => {
    const route = new URL(url, w.location.href).pathname; calls.push({route, method: request.method || "GET", body: request.body});
    if (route === "/api/cases/case-r1") return {ok: !options.missing, json: async () => item};
    if (route.startsWith("/api/trial-assets/")) return {ok: true, blob: async () => new w.Blob(["image"], {type: "image/png"})};
    if (route.endsWith("panda.urdf")) return {ok: !options.noRobot, blob: async () => new w.Blob(['<robot name="panda"/>'], {type: "application/xml"})};
    if (route === "/api/examples") return {ok: true, json: async () => ({examples: [{task: "Place the block", filename: "block.png", import_status: "imported", case_revision_id: "case-r1", eval_category: "atomic"}]})};
    if (route === "/api/evaluate") return {ok: true, json: async () => ({eval_id: "evaluation", trial_ids: {mock: "trial"}, strategies: ["mock"]})};
    return {ok: true, json: async () => route.includes("history") ? [] : route === "/api/strategies" ? {strategies: [{id: "mock", display_name: "Mock", perceive: "mock-vlm", verify: "mock-vlm"}]} : route === "/api/models" ? {models: []} : {defaults: {}, endpoints: {}, strategies: {}}};
  };
  w.eval(source("navigation.js")); w.eval(source("sample-cases.js") + ";window.createSampleCaseCard = createSampleCaseCard;"); w.eval(source("stage-renderers.js"));
  w.eval(source("app.js") + ";window.caseRunnerTest={get savedCase(){return selectedSavedCase;},get robot(){return selectedUrdfFile;},setRunning,showHistoryEntry};");
  const pause = () => new Promise(resolve => setTimeout(resolve, 40)); await pause();
  return {w, calls, streams, el: id => w.document.getElementById(id), pause};
}

test("saved case opens original composer with observation, instruction and supported robot, without executing", async () => {
  const {w, calls, el, pause} = await composer();
  try {
    assert.equal(el("quickComposer").hidden, false);
    assert.equal(el("taskInput").value, "Place the block in the tray");
    assert.equal(el("imagePreview").classList.contains("hidden"), false);
    assert.equal(el("urdfPreview").classList.contains("hidden"), false);
    assert.equal(w.caseRunnerTest.robot.name, "panda.urdf");
    assert.match(el("trialCaseContext").textContent, /Block on tray.*revision 1/);
    assert.equal(w._selectedGroundTruth, null); assert.equal(w._selectedExpectedSubtasks, null);
    assert.equal(calls.some(call => call.method === "POST"), false);
    el("strategyGrid").querySelector('[data-strategy-id="mock"]').click(); el("evalBtn").click(); await pause();
    const call = calls.find(call => call.route === "/api/evaluate"); assert.ok(call);
    assert.equal(call.body.get("case_revision_id"), "case-r1");
    assert.equal(call.body.get("task"), "Place the block in the tray");
    assert.equal(call.body.get("urdf").name, "panda.urdf");
    assert.equal(call.body.get("example_filename"), null);
  } finally { w.close(); }
});

test("manual task changes clear case identity and inherited robot context before execution", async () => {
  const {w, calls, el, pause} = await composer();
  try {
    el("taskInput").value = "A different task"; el("taskInput").dispatchEvent(new w.Event("input", {bubbles: true}));
    assert.equal(w.caseRunnerTest.savedCase, null);
    assert.equal(w.caseRunnerTest.robot, null); assert.equal(w._selectedProprioception, null);
    assert.equal(new URL(w.location.href).searchParams.has("case"), false);
    el("strategyGrid").querySelector('[data-strategy-id="mock"]').click(); el("evalBtn").click(); await pause();
    const call = calls.find(call => call.route === "/api/evaluate"); assert.ok(call);
    assert.equal(call.body.get("case_revision_id"), null); assert.equal(call.body.get("urdf"), null);
    assert.equal(call.body.get("task"), "A different task");
  } finally { w.close(); }
});

test("removing or replacing the observation clears the original case binding", async () => {
  const {w, el} = await composer();
  try { el("removeImage").click(); assert.equal(w.caseRunnerTest.savedCase, null); assert.equal(new URL(w.location.href).searchParams.has("case"), false); }
  finally { w.close(); }
});

test("unavailable robot remains unattached, while unavailable case reports error without execution", async () => {
  const first = await composer({noRobot: true});
  try { assert.equal(first.w.caseRunnerTest.robot, null); assert.equal(first.w.caseRunnerTest.savedCase.id, "case-r1"); assert.match(first.el("trialCaseContext").textContent, /Add a robot description/); }
  finally { first.w.close(); }
  const second = await composer({missing: true});
  try { assert.equal(second.w.caseRunnerTest.savedCase, null); assert.match(second.el("trialCaseContext").textContent, /No case was loaded or executed/); assert.equal(second.calls.some(call => call.method === "POST"), false); }
  finally { second.w.close(); }
});

test("completed saved trial offers Add to campaign with exact durable ID; followup survives reopening without rerun", async () => {
  const {w, calls, streams, el, pause} = await composer();
  try {
    el("strategyGrid").querySelector('[data-strategy-id="mock"]').click(); el("evalBtn").click(); await pause();
    assert.equal(w.document.querySelector(".trial-campaign-link"), null, "a running trial is not offered for reuse");
    streams[0].emit("strategy_complete", {strategy_id: "mock", total_latency_ms: 123, success: false});
    assert.equal(w.document.querySelector(".trial-campaign-link"), null, "strategy events precede durable completion");
    streams[0].emit("complete", {trial_ids: {mock: "trial/a?revision=1"}, results: [{strategy_id: "mock", success: false, stages: [{stage: "verify", status: "completed"}]}]});
    const followup = el("tab-content-mock").querySelector(".trial-campaign-link");
    assert.equal(followup.textContent, "Add to campaign");
    assert.equal(followup.getAttribute("href"), "/static/datasets.html?trial=trial%2Fa%3Frevision%3D1");
    assert.equal(new URL(el("tab-content-mock").querySelector('.trial-next-step-actions a:last-child').href).searchParams.get("trial"), "trial/a?revision=1");
    assert.equal(calls.filter(call => call.method === "POST").length, 1, "followup rendering neither saves a campaign nor reruns");
    w.caseRunnerTest.showHistoryEntry(0);
    assert.equal(el("tab-content-mock").querySelector(".trial-campaign-link").getAttribute("href"), followup.getAttribute("href"));
    assert.equal(calls.filter(call => call.method === "POST").length, 1);
  } finally { w.close(); }
});

test("execution error or missing persisted trial identity never fabricates a campaign reuse link", async () => {
  for (const result of [{trial_ids: {}, results: [{strategy_id: "mock", stages: []}]}, {trial_ids: {mock: "failed-trial"}, results: [{strategy_id: "mock", stages: [{stage: "act", status: "error"}]}]}]) {
    const {w, streams, el, pause} = await composer();
    try {
      el("strategyGrid").querySelector('[data-strategy-id="mock"]').click(); el("evalBtn").click(); await pause();
      streams[0].emit("complete", result);
      assert.equal(w.document.querySelector(".trial-campaign-link"), null);
    } finally { w.close(); }
  }
});

test("trial sample gallery opens the original composer with a versioned case rather than skipping into campaign setup", async () => {
  const {w, el, calls, pause} = await composer();
  try {
    w.document.querySelector('#quickWelcome [data-root-view="examples"]').click(); await pause();
    const sample = el("examplesView").querySelector('article a');
    assert.equal(sample.getAttribute("href"), "/?view=quick&case=case-r1");
    assert.match(sample.textContent, /Use in a trial/);
    assert.equal(calls.some(call => call.method === "POST"), false);
  } finally { w.close(); }
});
