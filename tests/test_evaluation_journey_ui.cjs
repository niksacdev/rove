"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
const {JSDOM} = require("jsdom");
const root = path.resolve(__dirname, "..");
async function workspace(query = "", options = {}) {
  const dom = new JSDOM(fs.readFileSync(path.join(root, "frontend/datasets.html"), "utf8"), {url: `http://localhost/static/datasets.html${query}`, runScripts: "outside-only", pretendToBeVisual: true});
  const w = dom.window, calls = [], cases = options.cases || [{case_id: "case", case_revision_id: "case-r1", id: "case-r1", name: "Pick red part", task: "Place in bin", revision: 1, image_asset: {sha256: "a".repeat(64)}, conditions: {eval_category: "placement"}, readiness: {assessment: "unreviewed"}}];
  const contracts = [{id: "contract", name: "Plan quality", criteria: []}];
  let summary = {completed_trials: 1, planned_trials: 3, tasks: [{passed: 0, failed: 0, unknown: 1}], strategies: []};
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
    else if (url === "/api/trials/trial/events?limit=200") value = {events: [{event_type: "rove.stage.started", stage: "perceive", endpoint_id: "mock-vision"}, {event_type: "rove.stage.completed", stage: "perceive", endpoint_id: "mock-vision"}, {event_type: "rove.stage.completed", stage: "plan", endpoint_id: "mock-planner"}], total: 3};
    else if (url === "/api/success-contracts" && method === "POST") { if (options.contractWait) await options.contractWait; value = {...body, id: `new-contract-${contracts.length}`}; contracts.push(value); }
    else if (url === "/api/success-contracts") value = {contracts};
    else if (url.startsWith("/api/datasets?")) value = {datasets: [{id: "dataset-r1", name: "Reviewed set", contract_id: "contract", members: [{case_revision_id: "case-r1", disposition: "included"}]}]};
    else if (url === "/api/datasets/dataset-r1") value = {id: "dataset-r1", name: "Reviewed set", contract_id: "contract", members: [{case_revision_id: "case-r1", disposition: "included"}]};
    else if (url === "/api/strategies") value = {strategies: [{id: "strategy", display_name: "Mock strategy", perceive: "mock-vision", plan: "mock-planner"}]};
    else if (url === "/api/baselines") value = {baselines: []};
    else if (url === "/api/campaigns") value = [{id: "campaign", name: "Baseline", status: "completed"}];
    else if (url === "/api/campaigns/preview") value = {ready: !options.blocked, blockers: options.blocked ? ["Episode outcome requires recorded evidence"] : [], planned_trials: 3, metrics: [{name: "task_success", status: "needs_review", reason: "SME review required"}]};
    else if (url === "/api/campaigns/from-cases") value = {id: "campaign", planned_trials: 3};
    else if (url === "/api/campaigns/campaign") value = {campaign: {id: "campaign", contract_id: "contract", spec: {name: "Baseline", strategies: ["strategy"], tasks: [{id: "case-r1", case_revision_id: "case-r1", task: "Place in bin"}]}, status: "completed"}, summary};
    else if (url === "/api/campaigns/campaign/assessments") value = {summary, assessments: {}, trials: campaignTrials};
    else throw Error(`Unexpected request: ${method} ${url}`);
    return {ok: true, status: 200, json: async () => value};
  };
  const nav = path.join(root, "frontend/navigation.js");
  w.eval(fs.readFileSync(nav, "utf8"));
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
    set("campaignContract", "contract", "change"); set("campaignStrategy", "strategy"); set("campaignName", "My controlled baseline");
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
    assert.equal(calls.filter(call => call.method === "POST").length, 0);
  } finally { dom.window.close(); }
});

test("saved dataset reuse moves to Configure and Run retains exact revision and accessible scoring controls", async () => {
  const {dom, w, el, pause, set, step} = await workspace("", {empty: true});
  try {
    assert.equal(el("importPanel").open, false); el("showImport").click(); assert.equal(el("importPanel").open, true); assert.equal(el("caseReusePanel").closest("[data-workflow-panel]").id, "casesStep");
    set("freezeContract", "contract", "change"); assert.equal(el("campaignContract").value, "contract");
    [...el("datasetList").querySelectorAll("button")].find(b => b.textContent === "Use these cases").click(); await pause();
    assert.equal(el("configureStep").hidden, false); assert.match(el("runContext").textContent, /Reviewed set.*dataset-r1/);
    step("run");
    set("campaignStrategy", "strategy"); el("previewCampaign").click(); await pause(); assert.equal(el("startBaseline").disabled, false);
    step("cases"); step("run"); assert.equal(el("startBaseline").disabled, false); assert.match(w.location.search, /step=run/);
    el("defineFreezeContract").click(); assert.equal(el("contractPanel").open, true); assert.equal(el("configureStep").hidden, false);
  } finally { dom.window.close(); }
});


test("browser back and forward preserve a preview; blocked evaluation stays visibly blocked", async () => {
  const {dom, w, el, step, calls, pause, set, pick} = await workspace("", {blocked: true});
  try {
    await pick(); step("run");
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
    assert.equal(calls.filter(call => call.method !== "GET").length, 0);
    assert.equal([...w.document.querySelectorAll('a[href]')].some(a => /view=quick/.test(a.href)), false);
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
    assert.match(el("selectedCases").textContent, /draft for expert review/i);
    assert.equal(calls.filter(call => call.method !== "GET").length, 0);
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
    assert.match(el("strategySummary").textContent, /Mock strategy/);
    assert.match(el("strategySummary").textContent, /perceive.*mock-vision/);
    assert.match(el("strategySummary").textContent, /plan.*mock-planner/);
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
    assert.equal(calls.filter(call => call.method !== "GET").length, 0);
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
    assert.equal(el("configureStep").hidden, false);
    step("cases");
    assert.match(el("selectedCases").textContent, /Saved collection case/);
    assert.equal(el("selectedCases").querySelectorAll("article").length, 1);
    await pick(["revision-0"]);
    assert.equal(el("selectedCases").querySelectorAll("article").length, 2);
    assert.match(el("selectedCases").textContent, /Saved collection case/); assert.match(el("selectedCases").textContent, /Red part 0/);
    step("configure"); set("campaignContract", "contract", "change"); set("campaignStrategy", "strategy", "change");
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
    assert.equal(calls.filter(call => call.method !== "GET").length, 0);
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
    const output = progress.querySelector("details"); assert.equal(output.querySelector("summary").textContent, "Recorded output");
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
    assert.equal(el("campaignResults").querySelectorAll('a[href*="history.html?trial="]').length, 3);
    assert.match(el("campaignResults").textContent, /Unknown or pending3/);
    assert.equal(calls.filter(call => call.method !== "GET").length, 0);
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
    assert.equal(calls.filter(call => call.method !== "GET").length, 0);
  } finally { dom.window.close(); }
});
