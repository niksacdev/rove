"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
const {JSDOM} = require("jsdom");
const isMutation = call => call.method !== "GET" && !call.url.endsWith("/outcome-summary");
async function baselineWorkspace(options = {}) {
  const root = path.resolve(__dirname, "../frontend");
  const dom = new JSDOM(fs.readFileSync(path.join(root, "datasets.html"), "utf8"), {url: `http://localhost/static/datasets.html?step=review&campaign=A${options.baselineId ? `&baseline=${options.baselineId}` : ""}`, runScripts: "outside-only"});
  const w = dom.window, calls = [], baselines = [...(options.baselines || [])], revisions = [...(options.revisions || []), ...baselines];
  const campaigns = {A: {id: "A", status: options.running ? "running" : "completed", contract_id: "contract", spec: {name: "Warehouse baseline campaign", strategies: ["alpha", "beta"], tasks: []}}, B: {id: "B", status: "completed", contract_id: "contract", spec: {name: "Other campaign", strategies: ["gamma"], tasks: []}}};
  w.HTMLElement.prototype.scrollIntoView = () => {};
  const pause = () => new Promise(resolve => setTimeout(resolve, 30));
  w.fetch = async (url, request = {}) => {
    const method = request.method || "GET", body = request.body ? JSON.parse(request.body) : null; calls.push({url, method, body}); let value;
    if (url.startsWith("/api/cases?")) value = {cases: [], total: 0};
    else if (url === "/api/assistant/status") value = {available: false};
    else if (url === "/api/success-contracts") value = {contracts: []};
    else if (url.startsWith("/api/datasets?")) value = {datasets: []};
    else if (url === "/api/strategies") value = {strategies: [{id: "candidate", display_name: "Changed planner"}]};
    else if (url === "/api/campaigns") value = Object.values(campaigns).map(item => ({id: item.id, name: item.spec.name, status: item.status}));
    else if (url === "/api/baselines" && method === "GET") value = {baselines};
    else if (url.startsWith("/api/baselines") && method !== "GET") {
      if (options.failSave) return {ok: false, status: 422, json: async () => ({detail: "This campaign is not ready"})};
      value = {id: "saved", revision_id: "saved-r1", revision: 1, ...body}; baselines.push(value);
    } else if (/^\/api\/baselines\/[^/]+\/revisions$/.test(url)) value = {revisions: [...revisions, ...baselines].filter(item => url.split("/")[3] === item.id)};
    else if (url.startsWith("/api/baselines/")) value = revisions.find(item => url.endsWith(item.revision_id)) || baselines.find(item => url.endsWith(item.id) || url.endsWith(item.revision_id));
    else if (/^\/api\/campaigns\/[AB]$/.test(url)) value = {campaign: campaigns[url.at(-1)]};
    else if (url.endsWith("/outcome-summary")) value = {source: "recorded", headline: "No assessments", findings: [], next_steps: []};
    else if (url.endsWith("/assessments")) value = {summary: {completed_trials: 2, planned_trials: 2, tasks: [], strategies: []}, assessments: {}, trials: []};
    else if (url.endsWith("/ablation-preview")) value = {ready: true, planned_trials: 2, comparison: {comparable: true, reasons: [], differences: []}};
    else throw new Error(`Unexpected request ${method} ${url}`);
    return {ok: true, json: async () => value};
  };
  w.eval(fs.readFileSync(path.join(root, "navigation.js"), "utf8"));
  w.eval(fs.readFileSync(path.join(root, "datasets.js"), "utf8")); await pause();
  const el = id => w.document.getElementById(id);
  const set = (id, value) => { el(id).value = value; el(id).dispatchEvent(new w.Event("change", {bubbles: true})); };
  const save = async () => { el("namedBaselineForm").dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true})); await pause(); };
  return {w, calls, el, set, save, pause};
}

test("completed campaign is not a baseline until explicitly saved, then comparison pins exact revision", async () => {
  const {w, calls, el, set, save, pause} = await baselineWorkspace();
  try {
    assert.equal(el("namedBaselinePanel").tagName, "SECTION");
    assert.equal(el("baselineReferenceBadge").textContent, "Not set as baseline");
    assert.equal(el("saveNamedBaseline").disabled, false);
    assert.equal(el("showComparison").disabled, true);
    assert.equal(calls.filter(isMutation).length, 0);
    assert.equal(el("namedBaselineName").value, "Warehouse baseline campaign · alpha");
    await save();
    const mutation = calls.find(item => item.method === "POST" && item.url === "/api/baselines");
    assert.equal(mutation.body.campaign_id, "A"); assert.equal(mutation.body.strategy_id, "alpha"); assert.equal(mutation.body.pinned, true);
    assert.equal(el("baselineReferenceBadge").textContent, "Baseline");
    assert.match(el("baselineReferenceIdentity").textContent, /Warehouse baseline campaign.*alpha.*Campaign: A.*saved-r1/);
    assert.equal(el("showComparison").disabled, false);
    set("candidateStrategy", "candidate"); el("previewAblation").click(); await pause();
    const preview = calls.find(item => item.url.endsWith("/ablation-preview"));
    assert.equal(preview.body.baseline_revision_id, "saved-r1");
    assert.equal(preview.body.baseline_strategy_id, "alpha");
    assert.match(el("baselineComparisonIdentity").textContent, /saved-r1/);
    assert.match(el("baselineComparisonIdentity").textContent, /candidate/);
    set("baselineStrategy", "beta");
    assert.equal(el("baselineReferenceBadge").textContent, "Not set as baseline");
    assert.equal(el("showComparison").disabled, true);
  } finally { w.close(); }
});

test("failed baseline save never creates a badge or enables comparison", async () => {
  const {w, el, save} = await baselineWorkspace({failSave: true});
  try { await save(); assert.equal(el("baselineReferenceBadge").textContent, "Not set as baseline"); assert.equal(el("showComparison").disabled, true); assert.match(el("namedBaselineStatus").textContent, /not ready/); }
  finally { w.close(); }
});

test("saved reference selection opens its actual campaign and strategy without writes", async () => {
  const record = {id: "other", revision_id: "other-r3", revision: 3, campaign_id: "B", strategy_id: "gamma", name: "Release reference", pinned: true};
  const {w, el, set, calls, pause} = await baselineWorkspace({baselines: [record]});
  try {
    assert.equal(el("baselineReferenceBadge").textContent, "Not set as baseline");
    set("namedBaseline", "other"); await pause();
    assert.equal(el("resultCampaign").value, "B"); assert.equal(el("baselineStrategy").value, "gamma");
    assert.equal(el("baselineReferenceBadge").textContent, "Baseline");
    assert.match(el("baselineReferenceIdentity").textContent, /Other campaign.*gamma.*Campaign: B.*other-r3/);
    assert.match(el("baselineReferenceSummary").textContent, /Release reference.*revision 3/);
    set("resultCampaign", "A"); await pause();
    assert.equal(el("baselineReferenceBadge").textContent, "Not set as baseline");
    assert.doesNotMatch(el("baselineReferenceIdentity").textContent, /other-r3/);
    assert.equal(calls.filter(isMutation).length, 0);
  } finally { w.close(); }
});

test("running campaign cannot be marked as a baseline", async () => {
  const {w, el, save, calls} = await baselineWorkspace({running: true});
  try { assert.equal(el("saveNamedBaseline").disabled, true); await save(); assert.equal(calls.filter(isMutation).length, 0); assert.equal(el("baselineReferenceBadge").textContent, "Not set as baseline"); }
  finally { w.close(); }
});

test("baseline deep link selects the exact saved strategy, not the campaign's first strategy", async () => {
  const record = {id: "beta-reference", revision_id: "beta-r2", revision: 2, campaign_id: "A", strategy_id: "beta", name: "Beta release", pinned: true};
  const {w, el, calls} = await baselineWorkspace({baselineId: record.id, baselines: [record]});
  try {
    assert.equal(el("baselineStrategy").value, "beta");
    assert.match(el("baselineReferenceSummary").textContent, /Beta release.*revision 2/);
    assert.match(el("baselineReferenceIdentity").textContent, /beta-r2/);
    assert.equal(el("showComparison").disabled, false);
    assert.equal(calls.filter(isMutation).length, 0);
  } finally { w.close(); }
});

test("review refresh retains an explicitly selected older baseline revision and its strategy", async () => {
  const head = {id: "release", revision_id: "release-r3", revision: 3, campaign_id: "A", strategy_id: "beta", name: "Latest assessment", pinned: true};
  const older = {...head, revision_id: "release-r1", revision: 1, name: "Original assessment"};
  const {w, el, set, calls, pause} = await baselineWorkspace({baselines: [head], revisions: [older]});
  try {
    set("namedBaseline", "release"); await pause();
    set("baselineRevisionHistory", "release-r1"); await pause();
    assert.match(el("baselineReferenceIdentity").textContent, /release-r1/);
    el("refreshCampaigns").click(); await pause();
    assert.equal(el("baselineStrategy").value, "beta"); assert.match(el("baselineReferenceIdentity").textContent, /release-r1/);
    w.document.querySelector('[data-journey-step="run"]').click();
    w.document.querySelector('[data-journey-step="review"]').click(); await pause();
    assert.equal(el("baselineStrategy").value, "beta"); assert.match(el("baselineReferenceIdentity").textContent, /release-r1/);
    assert.doesNotMatch(el("baselineReferenceIdentity").textContent, /release-r3/);
    set("candidateStrategy", "candidate"); el("previewAblation").click(); await pause();
    assert.equal(calls.filter(item => item.url.endsWith("/ablation-preview")).at(-1).body.baseline_revision_id, "release-r1");
    set("resultCampaign", "B"); await pause();
    assert.doesNotMatch(el("baselineReferenceIdentity").textContent, /release-r1/);
    assert.equal(el("baselineReferenceBadge").textContent, "Not set as baseline");
    assert.equal(calls.filter(item => isMutation(item) && !item.url.endsWith("/ablation-preview")).length, 0);
  } finally { w.close(); }
});
