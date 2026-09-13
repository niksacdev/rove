"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
const {JSDOM} = require("jsdom");
const root = path.resolve(__dirname, "..");
const isMutation = call => call.method !== "GET" && !/^\/api\/campaigns\/[^/]+\/outcome-summary$/.test(call.url) && call.url !== "/api/campaigns/metrics-draft";
const metricContract = {name: "Placement quality", scope: "plan_quality", evidence_mode: "candidate_output", criteria: [{id: "safe_plan", description: "Explain a safe placement plan", assessment: "human_review"}], metrics: ["task_success", "pipeline_latency", "pass_at_k", "pass_pow_k"], case_expectations: {"case-r1": "Part ends in the bin"}};
async function workspace(query = "", options = {}) {
  const dom = new JSDOM(fs.readFileSync(path.join(root, "frontend/datasets.html"), "utf8"), {url: `http://localhost/static/datasets.html${query}`, runScripts: "outside-only", pretendToBeVisual: true});
  const w = dom.window, calls = [], cases = options.cases || [{case_id: "case", case_revision_id: "case-r1", id: "case-r1", name: "Pick red part", task: "Place in bin", revision: 1, image_asset: {sha256: "a".repeat(64)}, conditions: {eval_category: "placement"}, readiness: {assessment: "unreviewed"}}];
  const contracts = options.contracts || [{id: "contract", name: "Plan quality", criteria: []}];
  let summary = options.summary || {completed_trials: 1, planned_trials: 3, tasks: [{passed: 0, failed: 0, unknown: 1}], strategies: []};
  let campaignTrials = [{task_id: "case-r1", trial_id: "trial", strategy_id: "strategy", seed: 0, outcome: "unknown"}];
  const pause = () => new Promise(resolve => setTimeout(resolve, 15));
  w.HTMLElement.prototype.scrollIntoView = () => {};
  w.fetch = async (url, opts = {}) => {
    const method = opts.method || "GET", body = opts.body ? JSON.parse(opts.body) : null; calls.push({url, method, body}); let value;
    if (url === "/api/assistant/status") value = {available: false, reason: "Not configured"};
    else if (url === "/api/examples") value = {examples: []};
    else if (url.startsWith("/api/cases?")) {
      const query = new URL(url, w.location.origin).searchParams;
      const filtered = (options.empty ? [] : cases).filter(item => (!query.get("q") || `${item.name} ${item.task}`.toLowerCase().includes(query.get("q").toLowerCase())) && (!query.get("category") || item.conditions?.eval_category === query.get("category")));
      const offset = Number(query.get("offset") || 0), limit = Number(query.get("limit") || 12);
      value = {cases: filtered.slice(offset, offset + limit), total: filtered.length, categories: [{name: "placement", count: cases.length}]};
    }
    else if (url.startsWith("/api/cases/") && method === "GET") value = cases.find(item => item.case_revision_id === url.split("/").at(-1));
    else if (url.startsWith("/api/reviews?")) value = {reviews: []};
    else if (url.startsWith("/api/trials?")) value = {trials: []};
    else if (url === "/api/trials/trial") value = {id: "trial", status: "completed", result: {plan: "Approach the red part from above", task_success: null}};
    else if (url === "/api/trials/trial/events?limit=200") value = {events: [{event_type: "rove.stage.started", stage: "perceive", endpoint_id: "mock-vision"}, {event_type: "stage", data: {stage: "perceive", status: "completed", model_id: "mock-vision"}}, {event_type: "rove.stage.completed", stage: "plan", endpoint_id: "mock-planner"}], total: 3};
    else if (url === "/api/success-contracts" && method === "POST") { if (options.contractWait) await options.contractWait; value = {...body, id: `new-contract-${contracts.length}`}; contracts.push(value); }
    else if (url === "/api/success-contracts") value = {contracts};
    else if (url.startsWith("/api/datasets?")) value = {datasets: [{id: "dataset-r1", name: "Reviewed set", contract_id: "contract", members: [{case_revision_id: "case-r1", disposition: "included"}]}]};
    else if (url === "/api/datasets/dataset-r1") { if (options.datasetWait) await options.datasetWait; value = {id: "dataset-r1", name: "Reviewed set", contract_id: "contract", members: [{case_revision_id: "case-r1", disposition: "included"}]}; }
    else if (url === "/api/strategies") value = {strategies: [{id: "strategy", display_name: "Mock strategy", perceive: "mock-vision", plan: "mock-planner"}]};
    else if (url === "/api/baselines") value = {baselines: []};
    else if (url === "/api/campaigns") value = [{id: "campaign", name: "Baseline", status: "completed"}];
    else if (url === "/api/campaigns/metrics-draft") { if (options.metricWait) await options.metricWait; value = {contract: metricContract, source: options.metricSource || "ai", rationale: "Based on the selected placement case", evidence_fingerprint: "case-evidence-r1"}; }
    else if (url.endsWith("/outcome-summary")) { if (options.summaryFail) throw Error("Assistant unavailable"); value = options.outcomeSummary || {source: "recorded", headline: "Assessments remain pending", findings: [], next_steps: []}; }
    else if (url === "/api/campaigns/preview") value = {ready: !options.blocked, blockers: options.blocked ? ["Episode outcome requires recorded evidence"] : [], planned_trials: 3, metrics: [{name: "task_success", status: "needs_review", reason: "SME review required"}]};
    else if (url === "/api/campaigns/from-cases") value = {id: "campaign", planned_trials: 3};
    else if (url === "/api/campaigns/campaign") value = {campaign: {id: "campaign", contract_id: "contract", spec: {name: "Baseline", seeds: [0, 1, 2], strategies: ["strategy"], tasks: [{id: "case-r1", case_revision_id: "case-r1", task: "Place in bin"}]}, status: "completed"}, summary};
    else if (url === "/api/campaigns/campaign/assessments") value = {summary, assessments: {}, trials: campaignTrials};
    else throw Error(`Unexpected request: ${method} ${url}`);
    return {ok: true, status: 200, json: async () => value};
  };
  const nav = path.join(root, "frontend/navigation.js");
  w.eval(fs.readFileSync(nav, "utf8"));
  w.eval(fs.readFileSync(path.join(root, "frontend/stage-renderers.js"), "utf8"));
  w.eval(fs.readFileSync(path.join(root, "frontend/trial-output.js"), "utf8"));
  w.eval(fs.readFileSync(path.join(root, "frontend/campaign-progress.js"), "utf8"));
  w.eval(fs.readFileSync(path.join(root, "frontend/campaign-results.js"), "utf8"));
  w.eval(fs.readFileSync(path.join(root, "frontend/datasets.js"), "utf8")); await pause();
  const el = id => w.document.getElementById(id), step = name => w.document.querySelector(`[data-journey-step="${name}"]`).click();
  const set = (id, value, type = "input") => { el(id).value = value; el(id).dispatchEvent(new w.Event(type, {bubbles: true})); };
  const route = async query => { w.history.pushState({}, "", query); w.dispatchEvent(new w.PopStateEvent("popstate")); await pause(); };
  const pick = async (ids = ["case-r1"]) => {
    el("selectExisting").focus(); el("selectExisting").click(); await pause();
    for (const id of ids) el("caseList").querySelector(`input[data-case-id="${id}"]`).click();
    el("confirmCasePicker").click(); await pause();
  };
  const completeCampaign = () => {
    summary = {...summary, completed_trials: 3, tasks: [{passed: 0, failed: 0, unknown: 3}]};
    campaignTrials = [0, 1, 2].map(seed => ({task_id: "case-r1", trial_id: seed ? `trial-${seed}` : "trial", strategy_id: "strategy", seed, outcome: "unknown"}));
  };
  return {dom, w, el, step, calls, pause, set, route, pick, completeCampaign};
}

test("step navigation retains input selections, unsaved forms and a valid launch preview", async () => {
  const {dom, w, el, step, calls, pause, set, pick} = await workspace();
  try {
    assert.equal(el("importPanel").open, false);
    await pick();
    set("reviewRationale", "A draft review must survive switching steps");
    set("caseName", "An unfinished import");
    step("run"); assert.equal(el("casesStep").hidden, true); assert.equal(el("runStep").hidden, false);
    assert.match(el("runContext").textContent, /Pick red part.*revision 1/);
    set("campaignStrategy", "strategy"); set("campaignName", "My controlled baseline"); set("campaignContract", "contract", "change");
    el("previewCampaign").click(); await pause(); assert.equal(el("startBaseline").disabled, false);
    step("cases"); assert.equal(el("caseName").value, "An unfinished import"); assert.match(el("reviewRationale").value, /must survive/);
    step("review"); assert.equal(el("caseWorkspace").parentElement.id, "reviewWorkspaceSlot"); assert.match(el("reviewRationale").value, /must survive/);
    step("run"); assert.equal(el("campaignName").value, "My controlled baseline"); assert.equal(el("startBaseline").disabled, false);
    el("baselineForm").dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true})); await pause();
    assert.equal(el("runStep").hidden, false); assert.match(w.location.search, /step=run/); assert.match(w.location.search, /campaign=campaign/);
    assert.match(el("liveTrials").textContent, /Trial trial/);
    step("review"); await pause();
    assert.equal(calls.filter(call => call.method === "POST" && call.url === "/api/campaigns/from-cases").length, 1);
    const review = [...el("campaignResults").querySelectorAll("a")].find(a => a.textContent === "Review case");
    assert.match(review.href, /campaign=campaign/); review.click(); await pause(); assert.equal(el("reviewStep").hidden, false);
    step("run"); step("review"); await pause(); assert.equal(calls.filter(call => call.url === "/api/campaigns/from-cases").length, 1);
  } finally { dom.window.close(); }
});

test("campaign and legacy case deep links restore context and history clears stale inspectors", async () => {
  const {dom, el, route, calls} = await workspace("?campaign=campaign&case=case-r1");
  try {
    assert.equal(el("reviewStep").hidden, false); assert.match(el("campaignResults").textContent, /Baseline/); assert.equal(el("caseDetailHeading").textContent, "Pick red part");
    await route("?step=run&campaign=campaign&case=case-r1"); assert.equal(el("runStep").hidden, false);
    await route("?step=cases"); assert.equal(el("casesStep").hidden, false); assert.equal(el("caseDetailHeading"), null); assert.match(el("campaignResults").textContent, /Choose a campaign/);
    await route("?case=case-r1"); assert.equal(el("casesStep").hidden, false); assert.equal(el("caseDetailHeading").textContent, "Pick red part");
    assert.equal(calls.filter(isMutation).length, 0);
  } finally { dom.window.close(); }
});

test("saved dataset reuse stays in Cases and Run retains exact revision and accessible scoring controls", async () => {
  const {dom, w, el, pause, set, step} = await workspace("", {empty: true});
  try {
    assert.equal(el("importPanel").open, false); el("showImport").click(); assert.equal(el("importPanel").open, true); assert.equal(el("caseReusePanel").closest("[data-workflow-panel]").id, "reviewStep");
    set("freezeContract", "contract", "change"); assert.equal(el("campaignContract").value, "contract");
    [...el("datasetList").querySelectorAll("button")].find(b => b.textContent === "Use these cases").click(); await pause();
    assert.equal(el("casesStep").hidden, false); assert.match(el("runContext").textContent, /Reviewed set.*dataset-r1/);
    step("run");
    set("campaignStrategy", "strategy"); set("campaignContract", "contract", "change"); el("previewCampaign").click(); await pause(); assert.equal(el("startBaseline").disabled, false);
    step("cases"); step("run"); assert.equal(el("startBaseline").disabled, false); assert.match(w.location.search, /step=run/);
    el("defineFreezeContract").click(); assert.equal(el("contractPanel").open, true); assert.equal(el("metricsStep").hidden, false);
  } finally { dom.window.close(); }
});


test("browser back and forward preserve a preview; blocked evaluation stays visibly blocked", async () => {
  const {dom, w, el, step, calls, pause, set, pick} = await workspace("", {blocked: true});
  try {
    await pick(); step("run");
    set("campaignStrategy", "strategy"); set("campaignContract", "contract", "change");
    el("previewCampaign").click(); await pause();
    assert.equal(el("startBaseline").disabled, true); assert.match(el("campaignMeasures").textContent, /Episode outcome requires recorded evidence/);
    step("review"); w.history.back(); await pause();
    assert.equal(el("runStep").hidden, false); assert.equal(el("startBaseline").disabled, true);
    assert.match(el("campaignMeasures").textContent, /Episode outcome requires recorded evidence/);
    w.history.forward(); await pause(); assert.equal(el("reviewStep").hidden, false);
    assert.equal(calls.filter(call => call.url === "/api/campaigns/from-cases").length, 0);
  } finally { dom.window.close(); }
});

function libraryCases(count = 15) {
  return Array.from({length: count}, (_, index) => ({case_id: `case-${index}`, case_revision_id: `revision-${index}`, id: `revision-${index}`, name: index === 14 ? "Blue bowl" : `Red part ${index}`, task: index === 14 ? "Place bowl on plate" : `Place part ${index} in bin`, revision: 1, image_asset: {sha256: "b".repeat(64)}, conditions: {eval_category: "placement"}, readiness: {assessment: "unreviewed"}}));
}

test("case gallery paginates and searches, preserves staged selections, then adds image/task cards without navigation", async () => {
  const {dom, w, el, calls, pause, set} = await workspace("", {cases: libraryCases()});
  try {
    const originalPath = w.location.pathname;
    el("selectExisting").focus(); el("selectExisting").click(); await pause();
    assert.equal(el("casePicker").open, true); assert.equal(w.document.activeElement, el("caseSearch"));
    assert.equal(el("caseList").children.length, 12); assert.equal(el("nextCases").disabled, false);
    assert.equal(el("caseList").querySelectorAll("img").length, 12);
    el("caseList").querySelector('input[data-case-id="revision-0"]').click();
    assert.equal(el("selectedCases").querySelectorAll("article").length, 0, "selection is staged until confirmation");
    el("nextCases").click(); await pause();
    assert.equal(el("caseList").children.length, 3); assert.equal(el("nextCases").disabled, true);
    el("caseList").querySelector('input[data-case-id="revision-14"]').click();
    set("caseSearch", "bowl"); await new Promise(resolve => setTimeout(resolve, 230));
    assert.equal(el("caseList").children.length, 1); assert.match(el("caseList").textContent, /Blue bowl.*Place bowl on plate/);
    assert.equal(el("caseList").querySelector("input").checked, true);
    set("caseCategory", "placement", "change"); await pause();
    const request = calls.filter(call => call.url.startsWith("/api/cases?")).at(-1);
    const query = new URL(request.url, w.location.origin).searchParams;
    assert.equal(query.get("offset"), "0"); assert.equal(query.get("q"), "bowl"); assert.equal(query.get("category"), "placement");
    el("confirmCasePicker").click(); await pause();
    assert.equal(el("casePicker").open, false); assert.equal(el("selectedCases").querySelectorAll("article").length, 2);
    assert.equal(el("selectedCases").querySelectorAll("img").length, 2);
    assert.match(el("selectedCases").textContent, /Red part 0/); assert.match(el("selectedCases").textContent, /Blue bowl/);
    assert.equal(w.location.pathname, originalPath); assert.doesNotMatch(w.location.search, /view=quick/);
    assert.equal(calls.filter(isMutation).length, 0);
    assert.equal([...el("caseList").querySelectorAll('a[href]')].some(a => /view=quick/.test(a.href)), false);
    assert.match(el("caseInspector").querySelector('a[href*="view=quick"]').href, /case=revision-0/);
  } finally { dom.window.close(); }
});

test("closing the case picker discards pending selections and restores focus to its opener", async () => {
  const {dom, w, el, pause} = await workspace();
  try {
    el("selectExisting").focus(); el("selectExisting").click(); await pause();
    el("caseList").querySelector("input").click(); el("closeCasePicker").click();
    assert.equal(el("casePicker").open, false); assert.equal(w.document.activeElement, el("selectExisting"));
    assert.equal(el("selectedCases").querySelectorAll("article").length, 0);
    el("selectExisting").click(); await pause(); assert.equal(el("caseList").querySelector("input").checked, false);
  } finally { dom.window.close(); }
});

test("edited expected outcomes survive gallery refresh and stage changes without creating expert judgments", async () => {
  const {dom, w, el, step, pick, calls, pause} = await workspace();
  try {
    await pick();
    const expectation = el("selectedCases").querySelector("textarea"); expectation.value = "Identify red part and keep gripper away from blue bowl.";
    expectation.dispatchEvent(new w.Event("input", {bubbles: true}));
    el("refreshCases").click(); await pause();
    assert.equal(el("selectedCases").querySelector("textarea").value, expectation.value);
    step("configure"); assert.match(el("successCriteria").value, /case-specific expected outcome/);
    step("cases"); assert.equal(el("selectedCases").querySelector("textarea").value, expectation.value);
    assert.match(el("selectedCases").textContent, /Draft criteria/i);
    assert.equal(calls.filter(isMutation).length, 0);
    step("configure"); el("contractForm").dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true})); await pause();
    const saved = calls.find(call => call.method === "POST" && call.url === "/api/success-contracts");
    assert.equal(saved.body.case_expectations["case-r1"], expectation.value);
    assert.equal(calls.filter(call => call.url.startsWith("/api/reviews") && call.method !== "GET").length, 0);
  } finally { dom.window.close(); }
});

test("Configure owns optional assistant and displays existing strategy stages while manual run remains available", async () => {
  const {dom, el, step, pick, pause, set, calls} = await workspace();
  try {
    await pick();
    assert.equal(el("assistantPanel").closest("[data-workflow-panel]").id, "configureStep");
    assert.equal(el("assistantQuestion").disabled, true);
    step("configure"); set("campaignStrategy", "strategy", "change");
    assert.match(el("strategyCards").textContent, /Mock strategy/);
    assert.match(el("strategyCards").textContent, /Perceive.*mock-vision/);
    assert.match(el("strategyCards").textContent, /Plan.*mock-planner/);
    set("campaignContract", "contract", "change"); el("prepareRun").click(); await pause();
    assert.equal(el("runStep").hidden, false); assert.equal(el("startBaseline").disabled, false);
    assert.match(el("runSummary").textContent, /3.*trial/i);
    assert.equal(calls.filter(call => call.url === "/api/assistant/ask").length, 0);
    assert.equal(calls.filter(call => call.url === "/api/campaigns/from-cases").length, 0);
  } finally { dom.window.close(); }
});

test("case membership changes create scoring rules for only the current case revisions", async () => {
  const {dom, w, el, step, pick, pause, calls} = await workspace("", {cases: libraryCases(2)});
  try {
    await pick(["revision-0", "revision-1"]); step("configure");
    el("contractForm").dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true})); await pause();
    const saved = () => calls.filter(call => call.method === "POST" && call.url === "/api/success-contracts").at(-1).body;
    assert.deepEqual(Object.keys(saved().case_expectations).sort(), ["revision-0", "revision-1"]);
    step("cases"); el("selectedCases").querySelector("article button").click(); step("configure");
    assert.equal(el("campaignContract").value, "", "changed membership requires confirming its generated rules again");
    el("contractForm").dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true})); await pause();
    assert.deepEqual(Object.keys(saved().case_expectations), ["revision-1"]);
    assert.equal(calls.filter(call => call.url === "/api/campaigns/from-cases").length, 0);
  } finally { dom.window.close(); }
});

test("editing expected outcomes cannot silently launch against previously selected scoring rules", async () => {
  const {dom, w, el, step, pick, set, pause, calls} = await workspace();
  try {
    await pick(); step("configure"); set("campaignStrategy", "strategy", "change");
    set("campaignContract", "contract", "change"); el("prepareRun").click(); await pause();
    assert.equal(el("startBaseline").disabled, false);
    step("cases"); const expectation = el("selectedCases").querySelector("textarea"); expectation.value = "Require a newly specified clearance constraint.";
    expectation.dispatchEvent(new w.Event("input", {bubbles: true}));
    step("configure"); el("prepareRun").click(); await pause();
    assert.equal(el("startBaseline").disabled, true, "edited criteria require confirmation before another launch");
    assert.equal(el("campaignContract").value, "");
    el("contractForm").dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true})); await pause();
    const saved = calls.filter(call => call.method === "POST" && call.url === "/api/success-contracts").at(-1);
    assert.match(saved.body.case_expectations["case-r1"], /newly specified clearance/);
    assert.equal(calls.filter(call => call.url === "/api/campaigns/from-cases").length, 0);
  } finally { dom.window.close(); }
});

test("saved Run links restore identified trials; refresh and browser navigation never relaunch a campaign", async () => {
  const {dom, el, calls, pause, route} = await workspace("?step=run&campaign=campaign");
  try {
    assert.equal(el("runStep").hidden, false); assert.equal(el("liveTrialsPanel").hidden, false);
    assert.match(el("liveTrials").textContent, /Place in bin/); assert.match(el("liveTrials").textContent, /strategy.*repetition 1/);
    assert.match(el("liveTrials").textContent, /Trial trial/);
    assert.equal(new URL(el("liveTrials").querySelector("a").href).searchParams.get("trial"), "trial");
    el("refreshLiveTrials").click(); await pause();
    await route("?step=review&campaign=campaign"); await route("?step=run&campaign=campaign");
    assert.equal(el("liveTrials").querySelectorAll("article").length, 1, "refresh replaces cards rather than duplicating them");
    assert.equal(calls.filter(isMutation).length, 0);
  } finally { dom.window.close(); }
});

test("editing confirmed scoring controls invalidates launch and preserves the new criterion until reconfirmed", async () => {
  const {dom, w, el, step, pick, set, pause, calls} = await workspace();
  try {
    await pick(); step("configure"); set("campaignStrategy", "strategy", "change");
    el("contractForm").dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true})); await pause();
    el("prepareRun").click(); await pause(); assert.equal(el("startBaseline").disabled, false);
    step("configure"); set("successCriteria", "Report occlusion before proposing any grasp.");
    assert.equal(el("startBaseline").disabled, true, "a changed visible criterion must invalidate the old preview");
    step("cases"); step("configure");
    assert.equal(el("successCriteria").value, "Report occlusion before proposing any grasp.", "stage changes preserve manually edited scoring text");
    el("prepareRun").click(); await pause(); assert.equal(el("startBaseline").disabled, true);
    el("contractForm").dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true})); await pause();
    const saved = calls.filter(call => call.url === "/api/success-contracts" && call.method === "POST").at(-1);
    assert.equal(saved.body.criteria[0].description, "Report occlusion before proposing any grasp.");
    el("prepareRun").click(); await pause(); assert.equal(el("startBaseline").disabled, false);
    step("configure"); set("assessmentMethod", "configured_verifier", "change");
    assert.equal(el("startBaseline").disabled, true, "changing grading method also requires new scoring confirmation");
    assert.equal(calls.filter(call => call.url === "/api/campaigns/from-cases").length, 0);
  } finally { dom.window.close(); }
});

test("using a saved collection shows its actual cases and adding a gallery case retains the collection members", async () => {
  const base = {case_id: "case", case_revision_id: "case-r1", id: "case-r1", name: "Saved collection case", task: "Place in bin", revision: 1};
  const {dom, el, step, pick, pause, set, calls} = await workspace("", {cases: [base, ...libraryCases(1)]});
  try {
    el("datasetList").querySelector("button").click(); await pause();
    assert.equal(el("casesStep").hidden, false);
    step("cases");
    assert.match(el("selectedCases").textContent, /Saved collection case/);
    assert.equal(el("selectedCases").querySelectorAll("article").length, 1);
    await pick(["revision-0"]);
    assert.equal(el("selectedCases").querySelectorAll("article").length, 2);
    assert.match(el("selectedCases").textContent, /Saved collection case/); assert.match(el("selectedCases").textContent, /Red part 0/);
    step("configure"); set("campaignStrategy", "strategy", "change"); set("campaignContract", "contract", "change");
    el("prepareRun").click(); await pause();
    const preview = calls.filter(call => call.url === "/api/campaigns/preview").at(-1);
    assert.deepEqual(preview.body.case_revision_ids.sort(), ["case-r1", "revision-0"]);
    assert.equal(preview.body.dataset_revision_id, undefined, "an edited collection becomes an explicit case selection instead of rewriting its saved revision");
    assert.equal(calls.filter(call => call.url === "/api/campaigns/from-cases").length, 0);
  } finally { dom.window.close(); }
});

test("clicking a gallery image after checking another case never drops staged selections", async () => {
  const {dom, el, pause, calls} = await workspace("", {cases: libraryCases(2)});
  try {
    el("selectExisting").click(); await pause();
    el("caseList").querySelector('input[data-case-id="revision-0"]').click();
    el("caseList").querySelector('a[data-case-id="revision-1"]').click(); await pause();
    assert.equal(el("casePicker").open, true, "image clicks keep selection staged inside the gallery");
    assert.equal(el("selectedCases").querySelectorAll("article").length, 0);
    el("confirmCasePicker").click(); await pause();
    assert.equal(el("selectedCases").querySelectorAll("article").length, 2);
    assert.match(el("selectedCases").textContent, /Red part 0/); assert.match(el("selectedCases").textContent, /Red part 1/);
    assert.equal(calls.filter(isMutation).length, 0);
  } finally { dom.window.close(); }
});

test("a slow scoring save cannot activate stale rules after the draft changes", async () => {
  let release;
  const contractWait = new Promise(resolve => { release = resolve; });
  const {dom, w, el, step, pick, set, pause} = await workspace("", {contractWait});
  try {
    await pick(); step("configure"); set("campaignStrategy", "strategy", "change");
    el("contractForm").dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true})); await pause();
    const editor = el("successCriteria");
    if (!editor.matches(":disabled")) {
      set("successCriteria", "A changed criterion entered while the server saves.");
      release(); await pause();
      assert.equal(el("campaignContract").value, "", "the response must not reactivate rules for an earlier draft");
      assert.equal(editor.value, "A changed criterion entered while the server saves.");
      assert.equal(el("startBaseline").disabled, true);
    } else {
      release(); await pause();
      assert.equal(editor.matches(":disabled"), false, "the editor must recover after a serialized save");
      assert.notEqual(el("campaignContract").value, "");
    }
  } finally { release(); await pause(); dom.window.close(); }
});

test("Run confirms strategy and exact scoring rules, then lazily exposes recorded stage progress and output", async () => {
  const {dom, w, el, step, pick, set, pause, calls} = await workspace();
  try {
    await pick(); step("configure"); set("campaignStrategy", "strategy", "change");
    set("campaignName", "Warehouse pilot"); set("contractName", "Grasp planning acceptance");
    set("successCriteria", "Identify occlusion before planning a grasp.");
    el("contractForm").dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true})); await pause();
    el("prepareRun").click(); await pause();
    assert.match(el("runSummary").textContent, /Warehouse pilot/);
    assert.match(el("runSummary").textContent, /1 cases × 1 strategy × 3 repetitions = 3 trials/);
    assert.match(el("runSummary").textContent, /Strategy: Mock strategy/);
    assert.match(el("runSummary").textContent, /Scoring rules: Grasp planning acceptance/);
    const criteria = el("runSummary").querySelector("details"); assert.equal(criteria.open, false);
    criteria.open = true; assert.match(criteria.textContent, /Identify occlusion before planning a grasp/);
    assert.match(criteria.textContent, /Pick red part/);
    el("baselineForm").dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true})); await pause();
    assert.equal(calls.filter(call => call.url.startsWith("/api/trials/trial")).length, 0, "stage details load only when expanded");
    const progress = el("liveTrials").querySelector("details");
    assert.equal(progress.querySelector("summary").textContent, "Pipeline progress and output");
    progress.open = true; await pause();
    assert.match(progress.textContent, /Execution: completed/);
    assert.match(progress.textContent, /perceive · completed · mock-vision/);
    assert.doesNotMatch(progress.textContent, /perceive · started/, "latest stage event supersedes its earlier start");
    assert.match(progress.textContent, /plan · completed · mock-planner/);
    assert.match(progress.textContent, /Stage execution and task acceptance are separate measures/);
    const output = [...progress.querySelectorAll("details")].find(item => item.querySelector("summary").textContent === "Recorded output"); assert.equal(output.open, false);
    output.open = true; assert.match(output.textContent, /Approach the red part from above/);
    progress.querySelector("button").click(); await pause();
    assert.equal(calls.filter(call => call.url === "/api/trials/trial/events?limit=200").length, 2);
    assert.equal(calls.filter(call => call.url === "/api/campaigns/from-cases").length, 1, "refreshing evidence never runs the pipeline again");
  } finally { dom.window.close(); }
});

test("Review refreshes completed campaign assessments after Run and saved Run uses recorded configuration", async () => {
  const {dom, el, step, calls, pause, completeCampaign} = await workspace("?step=run&campaign=campaign");
  try {
    assert.match(el("liveTrialStatus").textContent, /1 \/ 3 trials recorded/);
    assert.match(el("runSummary").textContent, /Baseline/);
    assert.match(el("runSummary").textContent, /3 planned trials across 1 cases/);
    assert.match(el("runSummary").textContent, /Strategies: strategy/);
    assert.doesNotMatch(el("runSummary").textContent, /Not selected|0 cases/);
    assert.equal(el("runLaunchActions").hidden, true, "saved campaign entry does not present another launch action");
    completeCampaign();
    el("refreshLiveTrials").click(); await pause();
    assert.match(el("liveTrialStatus").textContent, /3 \/ 3 trials recorded/);
    step("review"); await pause();
    assert.equal(el("trialInspection").querySelectorAll('a[href*="history.html?trial="]').length, 3);
    assert.match(el("campaignResults").textContent, /Unknown or pending3/);
    assert.equal(calls.filter(isMutation).length, 0);
  } finally { dom.window.close(); }
});

test("refreshing active campaign cards preserves expanded trial progress", async () => {
  const {dom, el, pause, calls} = await workspace("?step=run&campaign=campaign");
  try {
    const progress = el("liveTrials").querySelector("details"); progress.open = true; await pause();
    assert.match(progress.textContent, /perceive · completed/);
    el("refreshLiveTrials").click(); await pause();
    const refreshed = el("liveTrials").querySelector("details");
    assert.equal(refreshed.open, true, "campaign refresh must not collapse the evidence being inspected");
    assert.match(refreshed.textContent, /perceive · completed/);
    assert.equal(calls.filter(isMutation).length, 0);
  } finally { dom.window.close(); }
});


test("case library contains datasets and selecting a collection closes the picker without skipping case inspection", async () => {
  const {dom, el, pause, calls} = await workspace();
  try {
    assert.equal(el("showDatasets"), null);
    el("selectExisting").click(); await pause();
    el("browseCollections").click();
    assert.equal(el("datasetLibrary").hidden, false);
    assert.equal(el("individualCaseLibrary").hidden, true);
    assert.equal(el("casePickerActions").hidden, true);
    el("datasetList").querySelector("button").click(); await pause();
    assert.equal(el("casePicker").open, false);
    assert.equal(el("casesStep").hidden, false);
    assert.equal(el("selectedCases").querySelectorAll("article").length, 1);
    assert.equal(calls.filter(isMutation).length, 0);
    el("selectExisting").click(); await pause();
    assert.equal(el("individualCaseLibrary").hidden, false);
  } finally { dom.window.close(); }
});


test("dismissing a dataset picker cancels its late selection response", async () => {
  let release;
  const datasetWait = new Promise(resolve => { release = resolve; });
  const {dom, el, pause, pick} = await workspace("", {datasetWait});
  try {
    await pick();
    const before = el("selectedCases").textContent;
    el("selectExisting").click(); await pause(); el("browseCollections").click();
    el("datasetList").querySelector("button").click(); await pause();
    el("closeCasePicker").click(); release(); await pause();
    assert.equal(el("casePicker").open, false);
    assert.equal(el("selectedCases").textContent, before);
    assert.equal(el("campaignContract").value, "");
  } finally { release(); dom.window.close(); }
});

test("five-step workflow drafts task-specific metrics without saving or running until confirmed", async () => {
  const {dom, w, el, step, pick, set, pause, calls} = await workspace();
  try {
    assert.deepEqual([...w.document.querySelectorAll('[data-journey-step]')].map(node => node.dataset.journeyStep), ["cases", "configure", "metrics", "run", "review"]);
    await pick(); step("configure"); set("campaignStrategy", "strategy", "change"); set("campaignRepeats", "2");
    el("continueMetrics").click(); await pause();
    assert.equal(el("metricsStep").hidden, false); assert.equal(el("configureStep").hidden, true);
    const draft = calls.find(call => call.url === "/api/campaigns/metrics-draft");
    assert.deepEqual(draft.body.case_revision_ids, ["case-r1"]); assert.deepEqual(draft.body.strategies, ["strategy"]); assert.deepEqual(draft.body.seeds, [0, 1]);
    assert.equal(el("metricDraftSource").textContent, "Agent-drafted success metrics");
    assert.match(el("generatedCriteria").textContent, /Safe plan|safe plan/);
    const criterion = el("generatedCriteria").querySelector("textarea[data-criterion-id]");
    assert.equal(criterion.value, metricContract.criteria[0].description);
    assert.equal(el("campaignContract").value, ""); assert.equal(calls.filter(isMutation).length, 0);
    criterion.value = "Keep the gripper clear of the person"; criterion.dispatchEvent(new w.Event("input", {bubbles: true}));
    step("cases"); step("metrics"); await pause();
    assert.equal(criterion.value, "Keep the gripper clear of the person");
    assert.equal(calls.filter(call => call.url === "/api/campaigns/metrics-draft").length, 1);
    el("contractForm").dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true})); await pause();
    const save = calls.find(call => call.url === "/api/success-contracts" && call.method === "POST");
    assert.equal(save.body.criteria[0].description, "Keep the gripper clear of the person");
    assert.ok(el("campaignContract").value); assert.equal(calls.some(call => call.url === "/api/campaigns/from-cases"), false);
  } finally { dom.window.close(); }
});

test("template metrics honestly identify AI unavailability and configuration changes require reconfirmation", async () => {
  const {dom, w, el, step, pick, set, pause, calls} = await workspace("", {metricSource: "template"});
  try {
    await pick(); step("configure"); set("campaignStrategy", "strategy", "change"); step("metrics"); await pause();
    assert.match(el("metricDraftSource").textContent, /AI unavailable/); assert.doesNotMatch(el("metricDraftSource").textContent, /Agent-drafted/);
    const criterion = el("generatedCriteria").querySelector("textarea[data-criterion-id]"); criterion.value = "User-approved clearance"; criterion.dispatchEvent(new w.Event("input", {bubbles: true}));
    el("contractForm").dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true})); await pause();
    assert.ok(el("campaignContract").value);
    step("configure"); set("campaignRepeats", "5");
    assert.equal(el("campaignContract").value, "", "confirmed rules belong to the reviewed campaign configuration");
    step("metrics"); await pause();
    assert.equal(el("generatedCriteria").querySelector("textarea[data-criterion-id]").value, "User-approved clearance");
    assert.equal(calls.filter(call => call.url === "/api/campaigns/metrics-draft").length, 1);
    assert.match(el("metricDraftStatus").textContent, /edits are preserved/);
  } finally { dom.window.close(); }
});

test("slow metric drafts cannot overwrite edits or changed campaign context", async () => {
  for (const change of ["edit", "configuration"]) {
    let release; const metricWait = new Promise(resolve => {release = resolve;});
    const {dom, el, step, pick, set, pause} = await workspace("", {metricWait});
    try {
      await pick(); step("configure"); set("campaignStrategy", "strategy", "change"); step("metrics"); await pause();
      if (change === "edit") set("successCriteria", "My unsaved safety criterion");
      else { step("configure"); set("campaignRepeats", "6"); }
      release(); await pause();
      assert.equal(el("generatedCriteria").querySelector("textarea[data-criterion-id]"), null, "stale suggestion does not become the visible contract");
      if (change === "edit") assert.equal(el("successCriteria").value, "My unsaved safety criterion");
      assert.equal(el("campaignContract").value, ""); assert.match(el("metricDraftStatus").textContent, /changed|preserved/);
    } finally { release(); dom.window.close(); }
  }
});

test("Results lead with recorded charts, followed by AI evidence links, while detailed trials stay collapsed", async () => {
  const summary = {planned_trials: 3, completed_trials: 3, strategies: [{strategy_id: "strategy", pass_at_k: [{k: 1, value: .5}], pass_pow_k: [{k: 1, value: .5}], latency_p95_ms: 45}], tasks: [{strategy_id: "strategy", passed: 1, failed: 1, unknown: 1}]};
  const {dom, el, calls} = await workspace("?step=review&campaign=campaign", {summary, outcomeSummary: {source: "ai", headline: "One placement still needs assessment", findings: [{text: "The plan keeps a safe approach", trial_ids: ["trial"]}], next_steps: ["Review the remaining trial"]}});
  try {
    assert.equal(el("campaignResults").children[1].id, "campaignOutcomeCharts");
    assert.equal(el("campaignResults").children[2].id, "campaignAiSummary");
    assert.match(el("campaignOutcomeCharts").textContent, /Outcomes by strategy/); assert.match(el("campaignOutcomeCharts").textContent, /Pipeline latency/);
    assert.match(el("campaignAiSummary").textContent, /AI outcome summary/);
    assert.equal(el("campaignAiSummary").querySelector("a").getAttribute("href"), "/static/history.html?trial=trial");
    assert.equal(el("trialInspection").open, false); assert.equal(el("resultDetails").open, false);
    assert.equal(calls.filter(isMutation).length, 0);
  } finally { dom.window.close(); }
});

test("assistant summary failure leaves recorded outcome charts usable and offers retry", async () => {
  const {dom, el, calls, pause} = await workspace("?step=review&campaign=campaign", {summaryFail: true});
  try {
    assert.match(el("campaignAiSummary").textContent, /AI summary unavailable/);
    assert.match(el("campaignOutcomeCharts").textContent, /Outcomes by strategy/);
    const prior = calls.filter(call => call.url.endsWith("/outcome-summary")).length;
    el("campaignAiSummary").querySelector("button").click(); await pause();
    assert.equal(calls.filter(call => call.url.endsWith("/outcome-summary")).length, prior + 1);
    assert.equal(calls.filter(isMutation).length, 0);
  } finally { dom.window.close(); }
});

function mixedContract() {
  return {id: "mixed-r1", sha256: "stored-hash", created_at: "2026-09-13T10:00:00Z", name: "Mixed acceptance", scope: "plan_quality", evidence_mode: "candidate_output", metrics: ["task_success", "pipeline_latency"],
    criteria: [{id: "clearance", description: "An expert checks clearance", assessment: "human_review"}, {id: "schema", description: "Tool checks command structure", assessment: "configured_verifier", endpoint: "schema-grader"}],
    case_expectations: {"case-r1": "Use the blue destination bin"},
    campaign_targets: [{id: "accept", metric: "task_success", operator: "gte", threshold: .9}, {id: "timing", metric: "pipeline_latency", operator: "lte", threshold: 900}],
    annotation_bindings: [{endpoint: "schema-grader", annotation_key: "labels", target_key: "reference"}, {endpoint: "other-grader", annotation_key: "clearance", target_key: "expected_clearance"}]};
}

test("selecting saved scoring rules immediately shows their criteria and expected outcomes without inheriting stale controls", async () => {
  const mixed = mixedContract(), plain = {id: "plain-r1", name: "Simple human review", scope: "scene_understanding", evidence_mode: "candidate_output", criteria: [{id: "identify", description: "Identify the blue bin", assessment: "human_review"}], metrics: ["task_success"], case_expectations: {"case-r1": "The blue bin must be identified"}};
  const {dom, w, el, step, pick, set, pause, calls} = await workspace("", {contracts: [mixed, plain]});
  try {
    await pick(); step("configure"); set("campaignStrategy", "strategy", "change"); set("campaignContract", mixed.id, "change");
    assert.equal(el("contractName").value, mixed.name);
    assert.equal(el("generatedCriteria").querySelectorAll("textarea[data-criterion-id]").length, 2);
    assert.equal(el("generatedCriteria").querySelector("textarea[data-expectation-id]").value, mixed.case_expectations["case-r1"]);
    assert.equal(el("targetMetric").value, "task_success"); assert.equal(el("targetThreshold").value, "0.9"); assert.equal(el("annotationEndpoint").value, "schema-grader");
    set("campaignContract", plain.id, "change");
    assert.equal(el("contractName").value, plain.name); assert.equal(el("successScope").value, "scene_understanding");
    assert.equal(el("generatedCriteria").querySelectorAll("textarea[data-criterion-id]").length, 1);
    assert.equal(el("generatedCriteria").querySelector("textarea[data-expectation-id]").value, plain.case_expectations["case-r1"]);
    for (const id of ["targetMetric", "targetThreshold", "annotationEndpoint", "annotationKey", "annotationTarget", "verifierEndpoint"]) assert.equal(el(id).value, "", `${id} must not leak from previous rules`);
    step("metrics"); el("contractForm").dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true})); await pause();
    const save = calls.find(call => call.url === "/api/success-contracts" && call.method === "POST");
    assert.deepEqual(save.body.campaign_targets, []); assert.deepEqual(save.body.annotation_bindings, []);
    assert.equal(save.body.criteria[0].description, plain.criteria[0].description);
  } finally { dom.window.close(); }
});

test("description edits preserve mixed grading, all targets and bindings while stripping stored record metadata", async () => {
  const mixed = mixedContract(), {dom, w, el, step, pick, set, pause, calls} = await workspace("", {contracts: [mixed]});
  try {
    await pick(); step("configure"); set("campaignStrategy", "strategy", "change"); set("campaignContract", mixed.id, "change"); step("metrics");
    const descriptions = el("generatedCriteria").querySelectorAll("textarea[data-criterion-id]");
    descriptions[0].value = "An expert checks an unobstructed gripper approach"; descriptions[0].dispatchEvent(new w.Event("input", {bubbles: true}));
    el("contractForm").dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true})); await pause();
    const save = calls.find(call => call.url === "/api/success-contracts" && call.method === "POST");
    assert.ok(save, "edited displayed contract is saved");
    assert.deepEqual(save.body.criteria.map(item => [item.id, item.assessment, item.endpoint || null]), [["clearance", "human_review", null], ["schema", "configured_verifier", "schema-grader"]]);
    assert.match(save.body.criteria[0].description, /unobstructed/);
    assert.deepEqual(save.body.campaign_targets, mixed.campaign_targets); assert.deepEqual(save.body.annotation_bindings, mixed.annotation_bindings);
    for (const key of ["id", "sha256", "created_at"]) assert.equal(Object.hasOwn(save.body, key), false);
    assert.equal(calls.some(call => call.url === "/api/campaigns/from-cases"), false);
  } finally { dom.window.close(); }
});

test("an explicit grading-method change applies to all criteria and clearing targets or bindings removes them", async () => {
  const mixed = mixedContract(), {dom, w, el, step, pick, set, pause, calls} = await workspace("", {contracts: [mixed]});
  try {
    await pick(); step("configure"); set("campaignStrategy", "strategy", "change"); set("campaignContract", mixed.id, "change"); step("metrics");
    set("assessmentMethod", "configured_verifier", "change"); set("verifierEndpoint", "new-grader", "change");
    set("targetMetric", "", "change"); set("annotationEndpoint", "", "change"); set("annotationKey", "", "change"); set("annotationTarget", "", "change");
    el("contractForm").dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true})); await pause();
    const save = calls.find(call => call.url === "/api/success-contracts" && call.method === "POST");
    assert.ok(save); assert.equal(save.body.criteria.length, 2);
    for (const criterion of save.body.criteria) {assert.equal(criterion.assessment, "configured_verifier"); assert.equal(criterion.endpoint, "new-grader");}
    assert.deepEqual(save.body.campaign_targets, []); assert.deepEqual(save.body.annotation_bindings, []);
  } finally { dom.window.close(); }
});
