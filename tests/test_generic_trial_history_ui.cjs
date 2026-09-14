"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
const {JSDOM} = require("jsdom");
const {trialPresentation, promotionEligible} = require("../frontend/history.js");
const read = file => fs.readFileSync(path.join(__dirname, "../frontend", file), "utf8");
function generic(overrides = {}) {
  return {id: "generic/1", source: "quick", status: "completed", task: {id: "question-1", task: "Extract invoice total", inputs: {document: "Total: 12.50"}}, strategy: {name: "Extractor v2"}, snapshot: {config: {executor_identity: {name: "invoice-extractor", revision: "2"}}}, result: {outcome: "pass", execution: "completed", metric_scope: "executor_assessment", result: {output: {total: 12.5, note: "<img src=x onerror=alert(1)>"}, assessment: {verdict: "pass", reasoning: "Matches the supplied invoice", evidence_quality: "observed", measurements: [{name: "absolute_error", value: 0, unit: "USD", quality: "observed", evidence_refs: ["invoice-total"]}], evidence_refs: ["invoice-total", "https://private.invalid/evidence"]}}}, ...overrides};
}
async function inspect(t, trial, traces = false) {
  const dom = new JSDOM(read("history.html"), {url: `http://localhost/static/history.html?trial=${encodeURIComponent(trial.id)}${traces ? "#traces" : ""}`, runScripts: "outside-only", pretendToBeVisual: true}); t.after(() => dom.window.close());
  const w = dom.window, calls = [];
  w.HTMLElement.prototype.scrollIntoView = () => {};
  w.fetch = async (url, options = {}) => {
    assert.equal(options.method || "GET", "GET"); calls.push(url); let value;
    if (url.startsWith("/api/trials?")) value = {trials: [trial], total: 1};
    else if (url === `/api/trials/${encodeURIComponent(trial.id)}`) value = trial;
    else if (url.includes("/events?")) value = {events: [{event_type: "executor.completed", timestamp: "2026-09-14T00:00:00Z", data: {executor: "invoice-extractor"}}], total: 1};
    else if (url.endsWith("/trace")) value = {trial_id: trial.id, lanes: [], complete: true, loaded_events: 1, total_events: 1};
    else throw Error(`Unexpected request ${url}`);
    return {ok: true, json: async () => value};
  };
  w.eval(read("navigation.js")); w.eval(read("history-traces.js")); w.eval(read("history.js"));
  await new Promise(resolve => setTimeout(resolve, 30));
  return {w, calls, inspector: w.document.getElementById("inspector")};
}

test("generic saved trial renders candidate output, grader measurements and evidence without invented robotics stages", async t => {
  const {inspector, calls} = await inspect(t, generic(), true);
  assert.match(inspector.textContent, /Candidate output/); assert.match(inspector.textContent, /12.5/); assert.match(inspector.textContent, /Task inputs/);
  assert.match(inspector.textContent, /Executor assessment/); assert.match(inspector.textContent, /Matches the supplied invoice/); assert.match(inspector.textContent, /absolute_error/); assert.match(inspector.textContent, /0 USD/);
  assert.match(inspector.textContent, /invoice-total/); assert.match(inspector.textContent, /executor.completed/);
  assert.equal(inspector.querySelectorAll("img").length, 0);
  const headings = [...inspector.querySelectorAll("h3,h4")].map(node => node.textContent).join(" ");
  assert.doesNotMatch(headings, /verify|Recorded stages|Input observation|Configured verifier/i);
  assert.doesNotMatch(inspector.textContent, /robot task success|robot stopped|Promote to case|No stage events/);
  assert.equal(inspector.querySelector('a[href="https://private.invalid/evidence"]'), null);
  assert.ok(calls.includes("/api/trials/generic%2F1/trace"));
  assert.equal(calls.some(url => url.startsWith("https:")), false);
});

test("unfinished generic trials show missing output and unknown assessment, preserving trace access", async t => {
  const trial = generic({status: "interrupted", result: null});
  const {inspector} = await inspect(t, trial);
  assert.equal(trialPresentation(trial).verdict, "unknown");
  assert.match(inspector.textContent, /No candidate output was recorded/); assert.match(inspector.textContent, /No structured assessment was recorded/);
  assert.match(inspector.textContent, /Execution ended before normal completion/); assert.doesNotMatch(inspector.textContent, /robot stopped|observed robot/);
  assert.ok(inspector.querySelector("#traces"));
});

test("generic unknown verdict and explicit null output stay distinct from missing output; promotion stays robotics-only", async t => {
  const trial = generic(); trial.result.outcome = "unknown"; trial.result.result.output = null; trial.result.result.assessment.verdict = "pass";
  trial.result.result.assessment.measurements = [{name: "score", value: null}];
  trial.task.image_asset = {sha256: "a".repeat(64), media_type: "application/octet-stream"};
  assert.equal(trialPresentation(trial).verdict, "unknown"); assert.equal(promotionEligible(trial), false);
  const {inspector} = await inspect(t, trial);
  const output = [...inspector.querySelectorAll("section")].find(section => section.querySelector("h3")?.textContent === "Candidate output");
  assert.equal(output.querySelector("pre").textContent, "null"); assert.doesNotMatch(output.textContent, /No candidate/);
  assert.match(inspector.textContent, /scoreUnknown/);
});

test("robotics records retain verification semantics and promotion behavior", () => {
  const trial = {source: "quick", status: "completed", task: {task: "Pick cube", image_asset: {sha256: "a".repeat(64)}}, result: {stages: [{stage: "verify", status: "completed", output: {evaluator_result: {verdict: "pass", evidence_quality: "synthetic"}}}]}};
  assert.equal(trialPresentation(trial).verdict, "pass"); assert.equal(trialPresentation(trial).quality, "synthetic"); assert.equal(trialPresentation(trial).stages[0].stage, "verify"); assert.equal(promotionEligible(trial), true);
});


test("generic campaign trials return to the supported report and show captured grader revision", async t => {
  const {inspector} = await inspect(t, generic({campaign_id: "campaign-1"}));
  const back = [...inspector.querySelectorAll("a")].find(a => a.textContent === "Back to campaign report");
  assert.equal(back.getAttribute("href"), "/api/campaigns/campaign-1/report?format=html");
  assert.match(inspector.textContent, /revision 2/);
  assert.equal(inspector.querySelector('a[href*="datasets.html?step=review"]'), null);
});
