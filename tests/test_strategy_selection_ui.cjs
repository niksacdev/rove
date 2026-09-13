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
  w.eval(require("node:fs").readFileSync(require("node:path").resolve(__dirname,"../frontend/strategy-table.js"),"utf8"));
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

test("strategy checkbox table exposes actual endpoint assignments without automatically selecting or running", async t => {
  const {el, calls} = await workspace(t);
  assert.equal(el("campaignStrategy").hidden, true);
  assert.equal(el("strategyCards").querySelectorAll("input:checked").length, 0);
  const card = el("strategyCards").querySelector('[data-strategy-id="alpha"]');
  assert.match(card.textContent, /Vision and planner/);
  assert.match(card.textContent, /Grounded two-stage pipeline/);
  assert.equal(card.tagName,"TR");
  assert.deepEqual([...el("strategyCards").querySelectorAll("th")].map(cell=>cell.textContent),["Select","Strategy","Perceive","Plan","Act","Verify"]);
  assert.deepEqual([...card.querySelectorAll(".strategy-stage")].map(cell=>cell.textContent),["camera-vlm","planner-agent","Skipped","expert-grader"]);
  assert.equal(card.querySelector("img"), null);
  assert.equal(calls.some(call => call.method !== "GET"), false);
});

test("multiple selections survive steps, drive preview payload and invalidate prior approval on change", async t => {
  const {w, el, calls, choose, set} = await workspace(t);
  set("campaignContract", "rules", "change");
  choose("alpha"); choose("beta"); set("campaignRepeats", "4");
  // A deep-linked case is already selected, including when opening Configure.
  el("selectExisting").click(); await pause();
  assert.equal(el("caseList").querySelector('input[data-case-id="case-r1"]').checked,true); el("confirmCasePicker").click(); await pause();
  set("campaignContract", "rules", "change");
  w.document.querySelector('[data-journey-step="run"]').click();
  assert.match(el("runSummary").textContent, /1 case × 2 strategies × 4 attempts = 8 planned trials/);
  assert.match(el("runSummary").textContent, /Vision and planner, Direct VLA/);
  assert.match(el("campaignCalculation").textContent, /1 case × 2 strategies × 4 attempts = 8 planned trials/);
  el("previewCampaign").click(); await pause();
  assert.deepEqual(calls.find(call => call.url === "/api/campaigns/preview").body.strategies, ["alpha", "beta"]);
  assert.equal(el("startBaseline").disabled, false);
  w.document.querySelector('[data-journey-step="configure"]').click();
  assert.equal(el("strategyCards").querySelectorAll("input:checked").length, 2);
  choose("alpha");
  assert.equal(el("startBaseline").disabled, true);
  assert.equal(el("campaignMeasures").childElementCount, 0);
  assert.match(el("runSummary").textContent, /1 case × 1 strategy × 4 attempts = 4 planned trials/);
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


test("strategy rows render typed endpoints and configured verifier checks without JSON or inferred stages",async t=>{
  const catalog=[{...alpha,id:"typed",perceive:{endpoint:"camera-vlm",timeout_ms:2000},verify:{endpoint:"primary-verifier",checks:[{endpoint:"grounding-check",role:"constraint",required:true}]},sim:"recorded-sim"},
    {...beta,id:"runtime",verification_checks:[{endpoint:"<script>unsafe</script>",role:"diagnostic",required:false}],compute_dynamics:true}];
  const {w,el,calls}=await workspace(t,catalog);
  const row=el("strategyCards").querySelector('tr[data-strategy-id="typed"]');
  assert.match(row.querySelector('[data-label="Perceive"]').textContent,/camera-vlm/);
  assert.match(row.querySelector('[data-label="Verify"]').textContent,/primary-verifier.*grounding-check.*Required constraint/);
  assert.match(row.querySelector('.strategy-identity small').textContent,/Simulation: recorded-sim/);
  const runtime=el("strategyCards").querySelector('tr[data-strategy-id="runtime"]');
  assert.match(runtime.querySelector('[data-label="Verify"]').textContent,/<script>unsafe<\/script>.*Diagnostic check.*Dynamics check enabled/);
  assert.equal(runtime.querySelector("script"),null);assert.doesNotMatch(el("strategyCards").textContent,/\[object Object\]/);
  row.querySelector(".strategy-identity label").click();
  const chosen=el("strategyCards").querySelector('input[value="typed"]');assert.equal(chosen.checked,true);assert.equal(w.document.activeElement,chosen);
  assert.equal(el("strategyCards").querySelectorAll("input:checked").length,1);assert.equal(calls.some(call=>call.method!=="GET"),false);
});


test("shared strategy columns retain table layout alongside legacy campaign styles",async t=>{
 const {w,el}=await workspace(t);
 for(const file of ["datasets.css","strategy-table.css","fluent.css"]){const style=w.document.createElement("style");style.textContent=read(file);w.document.head.append(style);}
 for(const cell of el("strategyCards").querySelectorAll("td"))assert.equal(w.getComputedStyle(cell).display,"table-cell");
});
