"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
const {JSDOM} = require("jsdom");
const {buildCampaign} = require("../frontend/datasets.js");
const root = path.resolve(__dirname, ".."), read = name => fs.readFileSync(path.join(root, "frontend", name), "utf8");
const pause = () => new Promise(resolve => setTimeout(resolve, 15));
const alpha = {id: "alpha", display_name: "Vision and planner", description: "Grounded two-stage pipeline", perceive: "camera-vlm", plan: "planner-agent", act: null, verify: "expert-grader", pipeline_mode: "sequential"};
const beta = {id: "beta", display_name: "Direct VLA", description: "An action policy", act: "vla-policy", verify: "trajectory-grader", pipeline_mode: "parallel"};
async function workspace(t, catalog = [alpha, beta]) {
  const dom = new JSDOM(read("datasets.html"), {url: "http://localhost/static/datasets.html?step=configure&case=case-r1", runScripts: "outside-only", pretendToBeVisual: true});
  t.after(() => dom.window.close());
  const w = dom.window, calls = [], el = id => w.document.getElementById(id);
  const sample = {id: "case-r1", case_revision_id: "case-r1", case_id: "case", name: "Place part", task: "Put part in bin", revision: 1, conditions: {}, readiness: {assessment: "unreviewed"}};
  w.HTMLElement.prototype.scrollIntoView = () => {};
  w.fetch = async (url, options = {}) => {
    const method = options.method || "GET", body = options.body ? JSON.parse(options.body) : null;
    calls.push({url, method, body}); let payload;
    if (url === "/api/strategies") payload = {strategies: catalog};
    else if (url === "/api/assistant/status") payload = {available: false};
    else if (url.startsWith("/api/cases?")) payload = {cases: [sample], total: 1, categories: []};
    else if (url === "/api/cases/case-r1") payload = sample;
    else if (url.startsWith("/api/reviews?")) payload = {reviews: []};
    else if (url.startsWith("/api/trials?")) payload = {trials: []};
    else if (url === "/api/success-contracts") payload = {contracts: [{id: "rules", name: "Plan acceptance", criteria: []}]};
    else if (url.startsWith("/api/datasets?")) payload = {datasets: []};
    else if (url === "/api/baselines") payload = {baselines: []};
    else if (url === "/api/campaigns") payload = [];
    else if (url === "/api/campaigns/preview") payload = {ready: true, planned_trials: body.case_revision_ids.length * body.strategies.length * body.seeds.length, metrics: []};
    else if (url === "/api/examples") payload = {examples: []};
    else throw Error(`Unexpected request ${url}`);
    return {ok: true, json: async () => payload};
  };
  w.eval(read("navigation.js")); w.eval(read("datasets.js")); await pause();
  const choose = id => [...el("strategyCards").querySelectorAll("input")].find(input => input.value === id).click();
  const set = (id, value, type = "input") => { el(id).value = value; el(id).dispatchEvent(new w.Event(type, {bubbles: true})); };
  return {w, el, calls, choose, set};
}

test("campaign payload accepts distinct strategy sets, supports legacy input and enforces the limit", () => {
  const input = {name: "Compare", caseIds: ["case"], contractId: "rules", strategyIds: ["alpha", "beta"], repeats: 2, timeout: 120};
  assert.deepEqual(buildCampaign(input).strategies, ["alpha", "beta"]);
  assert.throws(() => buildCampaign({...input, strategyIds: []}), /at least one strategy/);
  assert.throws(() => buildCampaign({...input, strategyIds: ["alpha", "alpha"]}), /distinct/);
  assert.throws(() => buildCampaign({...input, strategyIds: Array.from({length: 21}, (_, i) => `s${i}`)}), /20/);
  assert.deepEqual(buildCampaign({...input, strategyIds: undefined, strategyId: "alpha"}).strategies, ["alpha"]);
  assert.throws(() => buildCampaign({...input, strategyIds: [], strategyId: "alpha"}), /at least one strategy/);
});

test("strategy cards expose actual endpoint assignments without automatically selecting or running", async t => {
  const {el, calls} = await workspace(t);
  assert.equal(el("campaignStrategy").hidden, true);
  assert.equal(el("strategyCards").querySelectorAll("input:checked").length, 0);
  const card = el("strategyCards").querySelector('[data-strategy-id="alpha"]');
  assert.match(card.textContent, /Vision and planner/);
  assert.match(card.textContent, /Grounded two-stage pipeline/);
  assert.match(card.textContent, /Perceivecamera-vlm/);
  assert.match(card.textContent, /Planplanner-agent/);
  assert.match(card.textContent, /ActSkipped/);
  assert.match(card.textContent, /Verifyexpert-grader/);
  assert.equal(card.querySelector("img"), null);
  assert.equal(calls.some(call => call.method !== "GET"), false);
});

test("multiple selections survive steps, drive preview payload and invalidate prior approval on change", async t => {
  const {w, el, calls, choose, set} = await workspace(t);
  set("campaignContract", "rules", "change");
  choose("alpha"); choose("beta"); set("campaignRepeats", "4");
  // The deep-linked case is inspected initially; selecting it in the picker includes it in this draft.
  el("selectExisting").click(); await pause();
  el("caseList").querySelector('input[data-case-id="case-r1"]').click(); el("confirmCasePicker").click(); await pause();
  set("campaignContract", "rules", "change");
  w.document.querySelector('[data-journey-step="run"]').click();
  assert.match(el("runSummary").textContent, /1 cases × 2 strategies × 4 repetitions = 8 trials/);
  assert.match(el("runSummary").textContent, /Vision and planner, Direct VLA/);
  assert.match(el("campaignBudget").textContent, /1 cases × 2 strategies × 4 repeats = 8 planned trials/);
  el("previewCampaign").click(); await pause();
  assert.deepEqual(calls.find(call => call.url === "/api/campaigns/preview").body.strategies, ["alpha", "beta"]);
  assert.equal(el("startBaseline").disabled, false);
  w.document.querySelector('[data-journey-step="configure"]').click();
  assert.equal(el("strategyCards").querySelectorAll("input:checked").length, 2);
  choose("alpha");
  assert.equal(el("startBaseline").disabled, true);
  assert.equal(el("campaignMeasures").childElementCount, 0);
  assert.match(el("runSummary").textContent, /1 cases × 1 strategy × 4 repetitions = 4 trials/);
  assert.equal(calls.some(call => call.url === "/api/campaigns/from-cases"), false);
});

test("catalog refresh adds a saved revision alongside existing selections and supports ablation-only selection", async t => {
  const catalog = [alpha, beta], {w, el, calls, choose} = await workspace(t, catalog);
  choose("alpha"); catalog.push({...beta, id: "beta-r2", display_name: "Direct VLA revision 2"});
  await w.refreshStrategyCatalog("beta-r2");
  assert.deepEqual([...el("strategyCards").querySelectorAll("input:checked")].map(input => input.value), ["alpha", "beta-r2"]);
  await w.refreshStrategyCatalog("beta", true);
  assert.equal(el("candidateStrategy").value, "beta");
  assert.deepEqual([...el("strategyCards").querySelectorAll("input:checked")].map(input => input.value), ["alpha", "beta-r2"]);
  assert.equal(calls.some(call => call.method !== "GET"), false);
});

test("the twentieth selection disables further cards and deselecting re-enables them", async t => {
  const catalog = Array.from({length: 21}, (_, i) => ({...alpha, id: `s${i}`, display_name: `Strategy ${i}`}));
  const {el, choose} = await workspace(t, catalog);
  for (let i = 0; i < 20; i++) choose(`s${i}`);
  assert.equal(el("strategyCards").querySelectorAll("input:checked").length, 20);
  assert.equal(el("strategyCards").querySelector('input[value="s20"]').disabled, true);
  choose("s0");
  assert.equal(el("strategyCards").querySelector('input[value="s20"]').disabled, false);
});
