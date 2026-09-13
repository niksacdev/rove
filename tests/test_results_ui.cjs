const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {JSDOM} = require("jsdom");

const frontend = path.join(__dirname, "..", "frontend");
const campaign = {id: "campaign-a", name: "Baseline reaching", revision: "v1", status: "completed", created_at: "2026-09-12T10:00:00Z"};
const flush = async () => { await new Promise(setImmediate); await new Promise(setImmediate); };

function setup(t, {search = "", campaigns = [campaign], baselines = [], respond} = {}) {
  const dom = new JSDOM(fs.readFileSync(path.join(frontend, "benchmarks.html"), "utf8"), {
    url: `http://localhost/static/benchmarks.html${search}`, runScripts: "outside-only", pretendToBeVisual: true,
  });
  t.after(() => dom.window.close());
  const {window} = dom;
  const calls = [], timers = new Map(), scrolled = [];
  let timerId = 0, hidden = false;
  Object.defineProperty(window.document, "hidden", {get: () => hidden});
  window.setTimeout = (callback, delay) => { timers.set(++timerId, {callback, delay}); return timerId; };
  window.clearTimeout = id => timers.delete(id);
  window.HTMLElement.prototype.scrollIntoView = function () { scrolled.push(this); };
  window.fetch = async (url, options) => {
    calls.push({url, options});
    if (respond) {
      const custom = await respond(url, options);
      if (custom !== undefined) return custom;
    }
    let payload;
    if (url === "/api/campaigns" && !options?.method) payload = campaigns;
    else if (url === "/api/baselines") payload = {baselines};
    else if (url.startsWith("/api/campaigns/") && !options?.method) payload = {summary: {completed_trials: 3, planned_trials: 3}};
    else if (url === "/api/strategies") payload = {strategies: [{id: "mock", display_name: "Mock"}]};
    else if (url === "/api/examples") payload = {examples: [{task: "Pick the cube", filename: "images/cube.png", eval_qa: {expected: "cube"}}]};
    else if (url === "/api/campaigns" && options?.method === "POST") payload = {id: "new-campaign", planned_trials: 2};
    else if (options?.method === "POST") payload = {ok: true};
    else throw new Error(`Unexpected request: ${url}`);
    return {ok: true, json: async () => payload};
  };
  window.eval(fs.readFileSync(path.join(frontend, "navigation.js"), "utf8"));
  window.eval(fs.readFileSync(path.join(frontend, "benchmarks.js"), "utf8"));
  return {
    window, document: window.document, calls, timers, scrolled,
    hide(value) { hidden = value; window.document.dispatchEvent(new window.Event("visibilitychange")); },
    openAdvanced() {
      window.document.getElementById("advancedConfiguration").open = true;
      window.document.getElementById("advancedConfiguration").dispatchEvent(new window.Event("toggle"));
    },
  };
}

test("Results opens history first with shared navigation, explicit review routes and no builder fetches", async t => {
  const {document, calls} = setup(t);
  await flush();
  assert.equal(document.querySelector("h1").textContent, "Campaigns");
  assert.equal(document.querySelector("#roveNav [aria-current]").textContent, "Campaigns");
  assert.equal(document.querySelector(".workspace-tabs [aria-current]").textContent, "Campaigns");
  assert.equal(document.querySelector(".workspace-tabs a:last-child").getAttribute("href"), "/static/history.html");
  assert.equal(document.querySelector(".page-heading a").getAttribute("href"), "/static/datasets.html");
  assert.equal(document.getElementById("advancedConfiguration").open, false);
  assert.deepEqual(calls.map(c => c.url), ["/api/campaigns", "/api/baselines", "/api/campaigns/campaign-a"]);
  const links = [...document.querySelectorAll(".campaign a")];
  assert.deepEqual(links.map(a => a.textContent), ["Review results", "Improve", "Open report", "Set as baseline", "JSON", "CSV"]);
  assert.equal(links[0].getAttribute("href"), "/static/datasets.html?step=review&campaign=campaign-a");
  assert.equal(links[1].getAttribute("href"), "/static/datasets.html?improve=campaign-a");
  assert.match(links[2].getAttribute("href"), /report\?format=html$/);
  assert.equal(links[2].rel, "noopener");
  assert.equal(calls.filter(c => c.options?.method === "POST").length, 0);
});

test("legacy campaign links focus the exact card and preserve untrusted labels as text", async t => {
  const row = {...campaign, id: "part/a?b", name: "<img src=x onerror=alert(1)>"};
  const {document, calls, scrolled} = setup(t, {campaigns: [row], search: "?campaign=part%2Fa%3Fb"});
  await flush();
  const card = document.querySelector(".campaign");
  assert.equal(document.activeElement, card);
  assert.equal(scrolled[0], card);
  assert.ok(card.classList.contains("requested-campaign"));
  assert.equal(card.querySelector("h3").textContent, row.name);
  assert.equal(card.querySelector("img"), null);
  assert.equal(card.querySelector("a").getAttribute("href"), "/static/datasets.html?step=review&campaign=part%2Fa%3Fb");
  assert.ok(calls.some(c => c.url === "/api/campaigns/part%2Fa%3Fb"));
  assert.equal(calls.filter(c => c.options?.method).length, 0);
});

test("missing campaign links report absence instead of opening configuration or starting a run", async t => {
  const {document, calls} = setup(t, {search: "?campaign=missing", campaigns: []});
  await flush();
  assert.equal(document.getElementById("requestedCampaignMessage").hidden, false);
  assert.match(document.getElementById("requestedCampaignMessage").textContent, /not found/);
  assert.match(document.getElementById("historyEmpty").textContent, /No campaigns yet/);
  assert.equal(document.getElementById("advancedConfiguration").open, false);
  assert.deepEqual(calls.map(c => c.url), ["/api/campaigns", "/api/baselines"]);
});

test("advanced initialization is lazy, retryable and does not duplicate choices", async t => {
  let fail = true;
  const page = setup(t, {respond: async url => {
    if (url === "/api/strategies" && fail) return {ok: false, text: async () => "Temporarily unavailable"};
  }});
  await flush(); page.openAdvanced(); await flush();
  assert.equal(page.document.getElementById("advancedFields").disabled, true);
  assert.equal(page.document.getElementById("retryAdvanced").hidden, false);
  assert.equal(page.document.querySelectorAll(".campaign").length, 1);
  fail = false;
  page.document.getElementById("retryAdvanced").click(); await flush();
  assert.equal(page.document.getElementById("advancedFields").disabled, false);
  assert.equal(page.document.querySelectorAll("#strategies input").length, 1);
  assert.equal(page.document.querySelectorAll("#tasks input").length, 1);
  const count = page.calls.length;
  page.openAdvanced(); await flush();
  assert.equal(page.calls.length, count);
  assert.equal(page.calls.filter(c => c.options?.method === "POST").length, 0);
});

test("advanced deep link loads after results, retains seed/k configuration and only runs on submit", async t => {
  const page = setup(t, {search: "?configure=advanced"});
  await flush();
  assert.equal(page.document.getElementById("advancedConfiguration").open, true);
  assert.equal(page.calls[0].url, "/api/campaigns");
  assert.ok(page.calls.findIndex(c => c.url === "/api/strategies") > page.calls.findIndex(c => c.url === "/api/campaigns/campaign-a"));
  const saved = {name: "Saved comparison", suite_version: "suite-v2", revision: "candidate-v3", seeds: [17, 29], ks: [1, 2], timeout_s: 42, strategies: ["mock"], tasks: [{id: "saved-case", task: "Move cube", image_base64: "YWJj"}]};
  const load = page.document.getElementById("load");
  Object.defineProperty(load, "files", {value: [{text: async () => JSON.stringify(saved)}]});
  load.dispatchEvent(new page.window.Event("change")); await flush();
  assert.match(page.document.getElementById("message").textContent, /seed list and k values/);
  assert.equal(page.calls.filter(c => c.options?.method === "POST").length, 0);
  page.document.getElementById("campaignForm").dispatchEvent(new page.window.Event("submit", {cancelable: true}));
  await flush();
  const submitted = page.calls.find(c => c.url === "/api/campaigns" && c.options?.method === "POST");
  assert.deepEqual(JSON.parse(submitted.options.body), saved);
  assert.match(page.document.getElementById("message").textContent, /Campaign started/);
});

test("visible active campaigns refresh incrementally; hidden and completed pages do not poll", async t => {
  const page = setup(t, {campaigns: [{...campaign, status: "running"}]});
  await flush();
  const card = page.document.querySelector(".campaign");
  assert.equal(page.timers.size, 1);
  assert.equal([...page.timers.values()][0].delay, 15000);
  const beforeHide = page.calls.length;
  page.hide(true); await flush();
  assert.equal(page.timers.size, 0);
  assert.equal(page.calls.length, beforeHide);
  page.hide(false); await flush();
  assert.ok(page.calls.length > beforeHide);
  assert.equal(page.document.querySelector(".campaign"), card);
  assert.equal(page.calls.filter(c => c.options?.method).length, 0);
  const completed = setup(t);
  await flush();
  assert.equal(completed.timers.size, 0);
});

test("history and per-card failures preserve useful result links and recover without page reload", async t => {
  let failList = false;
  const page = setup(t, {respond: async url => {
    if (url === "/api/campaigns" && failList) return {ok: false, text: async () => "Offline"};
    if (url === "/api/campaigns/campaign-a") return {ok: false, text: async () => "Count unavailable"};
  }});
  await flush();
  const card = page.document.querySelector(".campaign");
  assert.match(card.querySelector(".progress").textContent, /Trial counts unavailable/);
  assert.equal(card.querySelectorAll("a").length, 6);
  failList = true;
  page.document.getElementById("refreshHistory").click(); await flush();
  assert.equal(page.document.querySelector(".campaign"), card);
  assert.match(page.document.getElementById("historyStatus").textContent, /Could not load saved campaigns/);
  assert.equal(page.document.getElementById("refreshHistory").disabled, false);
  failList = false;
  page.document.getElementById("refreshHistory").click(); await flush();
  assert.match(page.document.getElementById("historyStatus").textContent, /1 saved campaign/);
});

test("an active campaign's terminal refresh updates counts before stopping polling", async t => {
  const row = {...campaign, status: "running"};
  const page = setup(t, {campaigns: [row], respond: async url => {
    if (url === "/api/campaigns/campaign-a") return {ok: true, json: async () => ({summary: {
      completed_trials: row.status === "running" ? 2 : 3, planned_trials: 3,
    }})};
  }});
  await flush();
  const card = page.document.querySelector(".campaign");
  assert.match(card.querySelector(".progress").textContent, /2 \/ 3/);
  page.hide(true); row.status = "completed"; page.hide(false); await flush();
  assert.match(card.querySelector(".progress").textContent, /3 \/ 3/);
  assert.equal(page.timers.size, 0);
  assert.equal(card.querySelector("button").hidden, true);
});

test("campaign cancellation and resume are explicit actions, never consequences of visiting results", async t => {
  const page = setup(t, {campaigns: [{...campaign, status: "cancelled"}]});
  await flush();
  const control = page.document.querySelector(".campaign button");
  assert.equal(control.textContent, "Resume remaining trials");
  assert.equal(page.calls.filter(c => c.options?.method).length, 0);
  control.click(); await flush();
  assert.equal(page.calls.filter(c => c.options?.method === "POST").length, 1);
  assert.equal(page.calls.find(c => c.options?.method === "POST").url, "/api/campaigns/campaign-a/resume");
});


test("only saved baseline records badge matching campaigns with their actual strategy", async t => {
  const baseline = {id: "baseline-1", name: "Production grasping", campaign_id: "campaign-a", strategy_id: "grasp-v2"};
  const page = setup(t, {campaigns: [campaign, {...campaign, id: "campaign-b", name: "Another baseline experiment"}], baselines: [baseline]});
  await flush();
  const [saved, ordinary] = page.document.querySelectorAll(".campaign");
  assert.equal(saved.querySelector(".baseline-badge").hidden, false);
  assert.equal(saved.querySelector(".campaign-baselines .baseline-name").textContent, "Production grasping");
  assert.equal(saved.querySelector(".campaign-baselines span").textContent, "Strategy: grasp-v2");
  assert.equal(saved.querySelector(".baseline-action").hidden, true);
  assert.equal(saved.querySelector(".baseline-name").getAttribute("href"), "/static/datasets.html?step=review&campaign=campaign-a&baseline=baseline-1");
  assert.equal(ordinary.querySelector(".baseline-badge").hidden, true);
  assert.equal(ordinary.querySelector(".baseline-action").textContent, "Set as baseline");
  assert.equal(ordinary.querySelector(".baseline-action").getAttribute("href"), "/static/datasets.html?step=review&campaign=campaign-b&baseline=setup");
  assert.equal(page.calls.some(call => call.options?.method), false);
});

test("baseline setup is offered only after completion and saved badges update on refresh", async t => {
  const row = {...campaign, status: "running"};
  const baselines = [];
  const page = setup(t, {campaigns: [row], baselines});
  await flush();
  const card = page.document.querySelector(".campaign");
  assert.equal(card.querySelector(".baseline-action").hidden, true);
  row.status = "completed";
  baselines.push({id: "saved", campaign_id: row.id, strategy_id: "mock", name: "<img src=x onerror=alert(1)>"});
  page.document.getElementById("refreshHistory").click(); await flush();
  assert.equal(page.document.querySelector(".campaign"), card);
  assert.equal(card.querySelector(".baseline-action").hidden, true);
  assert.equal(card.querySelector(".baseline-badge").hidden, false);
  assert.equal(card.querySelector(".campaign-baselines .baseline-name").textContent, baselines[0].name);
  assert.equal(card.querySelector("img"), null);
  assert.equal(page.timers.size, 0);
  assert.equal(page.calls.some(call => call.options?.method), false);
});

test("unavailable baseline service preserves campaign results and exposes retry status", async t => {
  let failed = true;
  const page = setup(t, {respond: async url => {
    if (url === "/api/baselines" && failed) return {ok: false, text: async () => "Offline"};
  }});
  await flush();
  const card = page.document.querySelector(".campaign");
  assert.ok(card);
  assert.equal(card.querySelector(".baseline-badge").hidden, true);
  assert.match(page.document.getElementById("historyStatus").textContent, /Baseline status unavailable/);
  assert.match(card.querySelector(".progress").textContent, /3 \/ 3 trials/);
  failed = false; page.document.getElementById("refreshHistory").click(); await flush();
  assert.doesNotMatch(page.document.getElementById("historyStatus").textContent, /unavailable/);
});


test("multiple saved strategies keep separate exact baseline links without mislabeling other campaigns", async t => {
  const page = setup(t, {baselines: [
    {id: "reference-a", campaign_id: campaign.id, strategy_id: "agent-a", name: "Agent A"},
    {id: "reference-b", campaign_id: campaign.id, strategy_id: "agent-b", name: "Agent B"},
    {id: "unrelated", campaign_id: "elsewhere", strategy_id: "agent-c", name: "Other campaign"},
  ]});
  await flush();
  const links = [...page.document.querySelectorAll(".baseline-name")];
  assert.deepEqual(links.map(link => link.textContent), ["Agent A", "Agent B"]);
  assert.deepEqual(links.map(link => new URL(link.href).searchParams.get("baseline")), ["reference-a", "reference-b"]);
  assert.equal(page.document.querySelector(".campaign small").textContent.includes("v1"), false);
  assert.match(page.document.querySelector(".campaign small").textContent, /^Completed/);
  assert.equal(page.calls.some(call => call.options?.method), false);
});

test("campaign Improve action retains exact source and is offered only after execution completes", async t => {
  const rows = [{...campaign, id: "saved/a?b", name: "Warehouse candidate"}, {...campaign, id: "running", status: "running"}];
  const {document, calls, timers} = setup(t, {campaigns: rows}); await flush();
  let cards = [...document.querySelectorAll(".campaign")];
  assert.equal(cards[0].querySelector(".improve-link").getAttribute("href"), "/static/datasets.html?improve=saved%2Fa%3Fb");
  assert.equal(cards[0].querySelector(".improve-link").hidden, false);
  assert.equal(cards[0].querySelector(".improve-link").getAttribute("aria-label"), "Improve Warehouse candidate");
  assert.equal(cards[1].querySelector(".improve-link").hidden, true);
  rows[1].status = "completed"; const timer = [...timers.values()][0]; timer.callback(); await flush();
  cards = [...document.querySelectorAll(".campaign")]; assert.equal(cards[1].querySelector(".improve-link").hidden, false);
  assert.equal(calls.filter(call => call.options?.method).length, 0);
});
