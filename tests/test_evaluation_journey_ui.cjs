"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
const {JSDOM} = require("jsdom");
const root = path.resolve(__dirname, "..");
async function workspace(query = "", options = {}) {
  const dom = new JSDOM(fs.readFileSync(path.join(root, "frontend/datasets.html"), "utf8"), {url: `http://localhost/static/datasets.html${query}`, runScripts: "outside-only"});
  const w = dom.window, calls = [], cases = [{case_id: "case", case_revision_id: "case-r1", id: "case-r1", name: "Pick red part", task: "Place in bin", revision: 1, readiness: {assessment: "unreviewed"}}];
  const summary = {completed_trials: 1, planned_trials: 3, tasks: [{passed: 0, failed: 0, unknown: 1}], strategies: []};
  const pause = () => new Promise(resolve => setTimeout(resolve, 15));
  w.HTMLElement.prototype.scrollIntoView = () => {};
  w.fetch = async (url, opts = {}) => {
    const method = opts.method || "GET", body = opts.body ? JSON.parse(opts.body) : null; calls.push({url, method, body}); let value;
    if (url === "/api/assistant/status") value = {available: false, reason: "Not configured"};
    else if (url.startsWith("/api/cases?")) value = {cases: options.empty ? [] : cases};
    else if (url === "/api/cases/case-r1") value = cases[0];
    else if (url.startsWith("/api/reviews?")) value = {reviews: []};
    else if (url.startsWith("/api/trials?")) value = {trials: []};
    else if (url === "/api/success-contracts") value = {contracts: [{id: "contract", name: "Plan quality", criteria: []}]};
    else if (url.startsWith("/api/datasets?")) value = {datasets: [{id: "dataset-r1", name: "Reviewed set", contract_id: "contract", members: [{case_revision_id: "case-r1", disposition: "included"}]}]};
    else if (url === "/api/strategies") value = {strategies: [{id: "strategy", display_name: "Mock strategy"}]};
    else if (url === "/api/baselines") value = {baselines: []};
    else if (url === "/api/campaigns") value = [{id: "campaign", name: "Baseline", status: "completed"}];
    else if (url === "/api/campaigns/preview") value = {ready: !options.blocked, blockers: options.blocked ? ["Episode outcome requires recorded evidence"] : [], planned_trials: 3, metrics: [{name: "task_success", status: "needs_review", reason: "SME review required"}]};
    else if (url === "/api/campaigns/from-cases") value = {id: "campaign", planned_trials: 3};
    else if (url === "/api/campaigns/campaign") value = {campaign: {id: "campaign", contract_id: "contract", spec: {name: "Baseline", strategies: ["strategy"], tasks: [{id: "case-r1", case_revision_id: "case-r1"}]}, status: "completed"}, summary};
    else if (url === "/api/campaigns/campaign/assessments") value = {summary, assessments: {}, trials: [{task_id: "case-r1", trial_id: "trial", outcome: "unknown"}]};
    else throw Error(`Unexpected request: ${method} ${url}`);
    return {ok: true, status: 200, json: async () => value};
  };
  const nav = path.join(root, "frontend/navigation.js");
  w.eval(fs.readFileSync(nav, "utf8"));
  w.eval(fs.readFileSync(path.join(root, "frontend/datasets.js"), "utf8")); await pause();
  const el = id => w.document.getElementById(id), step = name => w.document.querySelector(`[data-journey-step="${name}"]`).click();
  const set = (id, value, type = "input") => { el(id).value = value; el(id).dispatchEvent(new w.Event(type, {bubbles: true})); };
  const route = async query => { w.history.pushState({}, "", query); w.dispatchEvent(new w.PopStateEvent("popstate")); await pause(); };
  return {dom, w, el, step, calls, pause, set, route};
}

test("step navigation retains input selections, unsaved forms and a valid launch preview", async () => {
  const {dom, w, el, step, calls, pause, set} = await workspace();
  try {
    assert.equal(el("importPanel").open, false);
    el("caseList").querySelector("input").click();
    el("caseList").querySelector("a").click(); await pause();
    set("reviewRationale", "A draft review must survive switching steps");
    set("caseName", "An unfinished import");
    step("run"); assert.equal(el("casesStep").hidden, true); assert.equal(el("runStep").hidden, false);
    assert.match(el("runContext").textContent, /Pick red part.*revision 1/);
    set("campaignContract", "contract", "change"); set("campaignStrategy", "strategy"); set("campaignName", "My controlled baseline");
    el("previewCampaign").click(); await pause(); assert.equal(el("startBaseline").disabled, false);
    step("cases"); assert.equal(el("caseName").value, "An unfinished import"); assert.match(el("reviewRationale").value, /must survive/);
    step("review"); assert.equal(el("caseWorkspace").parentElement.id, "reviewWorkspaceSlot"); assert.match(el("reviewRationale").value, /must survive/);
    step("run"); assert.equal(el("campaignName").value, "My controlled baseline"); assert.equal(el("startBaseline").disabled, false);
    el("baselineForm").dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true})); await pause();
    assert.equal(el("reviewStep").hidden, false); assert.match(w.location.search, /step=review/); assert.match(w.location.search, /campaign=campaign/);
    assert.equal(calls.filter(call => call.method === "POST" && call.url === "/api/campaigns/from-cases").length, 1);
    const review = [...el("campaignResults").querySelectorAll("a")].find(a => a.textContent === "Review case");
    assert.match(review.href, /campaign=campaign/); review.click(); await pause(); assert.equal(el("reviewStep").hidden, false);
    step("run"); step("review"); assert.equal(calls.filter(call => call.url === "/api/campaigns/from-cases").length, 1);
  } finally { dom.window.close(); }
});

test("campaign and legacy case deep links restore context and history clears stale inspectors", async () => {
  const {dom, el, route, calls} = await workspace("?campaign=campaign&case=case-r1");
  try {
    assert.equal(el("reviewStep").hidden, false); assert.match(el("campaignResults").textContent, /Baseline/); assert.equal(el("caseDetailHeading").textContent, "Pick red part");
    await route("?step=run&campaign=campaign&case=case-r1"); assert.equal(el("runStep").hidden, false);
    await route("?step=cases"); assert.equal(el("casesStep").hidden, false); assert.equal(el("caseDetailHeading"), null); assert.match(el("campaignResults").textContent, /Choose a campaign/);
    await route("?case=case-r1"); assert.equal(el("casesStep").hidden, false); assert.equal(el("caseDetailHeading").textContent, "Pick red part");
    assert.equal(calls.filter(call => call.method === "POST").length, 0);
  } finally { dom.window.close(); }
});

test("frozen dataset reuse moves to Run with exact revision and accessible contract controls", async () => {
  const {dom, w, el, pause, set, step} = await workspace("", {empty: true});
  try {
    assert.equal(el("importPanel").open, true); assert.equal(el("caseReusePanel").closest("[data-workflow-panel]").id, "casesStep");
    set("freezeContract", "contract", "change"); assert.equal(el("campaignContract").value, "contract");
    [...el("datasetList").querySelectorAll("button")].find(b => b.textContent === "Use for next campaign").click();
    assert.equal(el("runStep").hidden, false); assert.match(el("runContext").textContent, /Reviewed set.*dataset-r1/);
    set("campaignStrategy", "strategy"); el("previewCampaign").click(); await pause(); assert.equal(el("startBaseline").disabled, false);
    step("cases"); step("run"); assert.equal(el("startBaseline").disabled, false); assert.match(w.location.search, /step=run/);
    el("defineFreezeContract").click(); assert.equal(el("contractPanel").open, true); assert.equal(el("runStep").hidden, false);
  } finally { dom.window.close(); }
});


test("browser back and forward preserve a preview; blocked evaluation stays visibly blocked", async () => {
  const {dom, w, el, step, calls, pause, set} = await workspace("", {blocked: true});
  try {
    el("caseList").querySelector("input").click(); step("run");
    set("campaignContract", "contract", "change"); set("campaignStrategy", "strategy");
    el("previewCampaign").click(); await pause();
    assert.equal(el("startBaseline").disabled, true); assert.match(el("campaignMeasures").textContent, /Episode outcome requires recorded evidence/);
    step("review"); w.history.back(); await pause();
    assert.equal(el("runStep").hidden, false); assert.equal(el("startBaseline").disabled, true);
    assert.match(el("campaignMeasures").textContent, /Episode outcome requires recorded evidence/);
    w.history.forward(); await pause(); assert.equal(el("reviewStep").hidden, false);
    assert.equal(calls.filter(call => call.url === "/api/campaigns/from-cases").length, 0);
  } finally { dom.window.close(); }
});
