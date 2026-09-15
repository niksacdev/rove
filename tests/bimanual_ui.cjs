"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
const {JSDOM} = require("jsdom");
const {parseSeeds, countLabel, criterionLabel} = require("../frontend/bimanual.js");
const root = path.resolve(__dirname, "..");
const read = file => fs.readFileSync(path.join(root, "frontend", file), "utf8");
const pause = () => new Promise(resolve => setTimeout(resolve, 30));

test("seed inputs retain distinct fixed scenes and repeated model attempts", () => {
  assert.deepEqual(parseSeeds("0, 9, 17"), [0, 9, 17]);
  for (const value of ["", "0,", "0,0", "-1", "1.3", "Infinity", "2147483648", "<script>"]) assert.throws(() => parseSeeds(value));
  assert.equal(countLabel(2, 2, 3), "2 cases × 2 strategies × 3 attempts = 12 planned trials");
  assert.match(criterionLabel({success_mode: "final"}), /at the end/);
  assert.match(criterionLabel({success_mode: "ever"}), /at least once/);
});

async function workspace(t, options = {}) {
  const dom = new JSDOM(read("bimanual.html"), {url: `http://localhost/static/bimanual.html${options.query || ""}`, runScripts: "outside-only", pretendToBeVisual: true});
  t.after(() => dom.window.close()); const w = dom.window, calls = [], opened = [];
  let completed = false, changed = false;
  w.open = (...args) => opened.push(args); w.confirm = () => false;
  w.fetch = async (url, config = {}) => {
    const body = config.body ? JSON.parse(config.body) : null;
    calls.push({url, method: config.method || "GET", body});
    let value, ok = true;
    if (url === "/api/bimanual/config") value = options.unconfigured ? {configured: false, strategies: []} : {configured: true, ready: false, issues: [], strategies: [{id: "abc-vla", label: "ABC model", kind: "abc_vla", ready: true, issues: []}, {id: "pi05", label: "<img src=x onerror=alert(1)>", kind: "pi05", ready: !options.unready, issues: options.unready ? ["Adapted checkpoint missing"] : []}]};
    else if (url === "/api/bimanual/preview") value = {name: body.name, ready: true, preview_hash: "a".repeat(64), cases: body.reset_seeds.length, strategies: body.strategy_ids, attempts_per_case: body.policy_seeds.length, max_steps: body.max_steps, issues: [], success_criterion: {success_mode: "ever"}};
    else if (url === "/api/bimanual/trials" || url === "/api/bimanual/campaigns") {
      if (changed) {ok = false; value = {detail: "Configuration changed. Review again."};}
      else value = {id: url.endsWith("trials") ? "job1" : "campaign1"};
    }
    else if (url === "/api/bimanual/trials/job1") value = {id: "job1", status: completed ? "completed" : "running", trial_ids: {"abc-vla": "trial1"}, strategy_ids: ["abc-vla", "pi05"], tasks: [{id: "bottles-scene-0", task: "Put bottles"}], seeds: [0]};
    else if (url === "/api/bimanual/trials/job1/cancel") { completed = true; value = {status: "cancelled"}; }
    else if (url === "/api/trials/trial1") value = {id: "trial1", status: completed ? "completed" : "running", created_at: new Date().toISOString(), result: completed ? {outcome: "pass"} : null};
    else if (url.startsWith("/api/trials/trial1/events?")) value = {events: [{event_type: "evaluation.progress", name: "Executing action chunk", stage: "episode", data: {completed_steps: 15, max_steps: 3540}}], total: 1};
    else if (url === "/api/campaigns/campaign1") value = {campaign: {id: "campaign1", status: "completed", spec: {tasks: [{id: "case", task: "Put bottles"}], strategies: ["abc-vla"], seeds: [0]}}};
    else if (url === "/api/campaigns/campaign1/assessments") value = {trials: [{trial_id: "trial1", task_id: "case", seed: 0, strategy_id: "abc-vla", execution: "completed", outcome: "unknown"}]};
    else throw Error(`Unexpected request ${url}`);
    return {ok, json: async () => value};
  };
  for (const file of ["navigation.js", "trial-output.js", "campaign-progress.js", "bimanual.js"]) w.eval(read(file));
  await pause();
  return {w, calls, opened, complete: () => {completed = true;}, changed: () => {changed = true;}, el: id => w.document.getElementById(id)};
}

test("unconfigured runtime is a useful setup state with no runnable or dead-end form", async t => {
  const {el, calls} = await workspace(t, {unconfigured: true});
  assert.equal(el("setup").hidden, false);
  assert.equal(el("comparisonForm").hidden, true);
  assert.match(el("setup").textContent, /ROVE CLI|rove bimanual/);
  assert.equal(el("reviewButton").disabled, true);
  assert.equal(calls.length, 1);
});

test("model list safely escapes labels, disables unavailable models and updates the calculator", async t => {
  const {w, el} = await workspace(t, {unready: true});
  const selections = el("strategyRows").querySelectorAll("input");
  assert.equal(selections[0].checked, true); assert.equal(selections[1].disabled, true);
  assert.equal(el("strategyRows").querySelectorAll("img,script").length, 0);
  assert.match(el("strategyRows").textContent, /<img src=x/);
  assert.equal(el("plannedCount").textContent, "1 case × 1 strategy × 1 attempt = 1 planned trial");
  el("comparisonMode").value = "campaign"; el("comparisonMode").dispatchEvent(new w.Event("change"));
  assert.match(el("plannedCount").textContent, /3 cases × 1 strategy × 3 attempts = 9/);
  assert.equal(w.document.querySelector('#roveNav nav [aria-current="page"]').textContent, "Campaigns");
  selections[0].click(); assert.equal(el("reviewButton").disabled, true);
  assert.match(el("plannedCount").textContent, /= 0 planned trials/);
});

test("reviewing cannot start inference; editing invalidates the reviewed plan and departure warns", async t => {
  const {w, el, calls} = await workspace(t);
  el("comparisonForm").dispatchEvent(new w.Event("submit", {cancelable: true})); await pause();
  assert.equal(el("review").hidden, false); assert.equal(el("comparisonForm").hidden, true);
  assert.match(el("reviewFacts").textContent, /goal reached at least once/);
  assert.equal(calls.filter(call => call.method === "POST").length, 1);
  assert.equal(calls.some(call => call.url === "/api/bimanual/trials"), false);
  el("editButton").click(); assert.equal(el("review").hidden, true);
  el("comparisonName").value = "Changed"; el("comparisonName").dispatchEvent(new w.Event("input", {bubbles: true}));
  const click = new w.MouseEvent("click", {bubbles: true, cancelable: true});
  el("backToBrowser").dispatchEvent(click); assert.equal(click.defaultPrevented, true);
  el("launchButton").click(); await pause();
  assert.equal(calls.some(call => call.url === "/api/bimanual/trials"), false);
});

test("reviewed launch shows shared strategy progress and inspect opens without losing all runs", async t => {
  const {w, el, calls, opened} = await workspace(t);
  el("comparisonForm").dispatchEvent(new w.Event("submit", {cancelable: true})); await pause();
  el("launchButton").click(); await pause();
  const launch = calls.find(call => call.url === "/api/bimanual/trials");
  assert.equal(launch.body.preview_hash, "a".repeat(64));
  assert.deepEqual(launch.body.reset_seeds, [0]);
  assert.equal(el("execution").hidden, false);
  assert.match(el("executionProgress").textContent, /abc-vla.*Running.*pi05.*Queued/s);
  assert.match(el("episodeActivity").textContent, /Executing action chunk · 15 \/ 3540 control steps/);
  el("executionProgress").querySelector("button").click();
  assert.equal(opened[0][0], "/static/history.html?trial=trial1#traces");
  assert.equal(el("execution").hidden, false);
  assert.equal(w.location.search, "?job=job1");
});

test("reloading a finished campaign shows persisted report and trace actions without selecting another campaign", async t => {
  const {el, calls} = await workspace(t, {query: "?campaign=campaign1"});
  assert.equal(el("results").hidden, false);
  assert.equal(el("comparisonForm").hidden, true);
  assert.match(el("resultRows").textContent, /Not assessed/);
  assert.doesNotMatch(el("resultRows").textContent, /Criterion not met/);
  assert.ok(el("resultActions").querySelector('a[href="/api/campaigns/campaign1/report?format=html"]'));
  assert.equal(el("results").querySelectorAll("select").length, 0);
  assert.equal(calls.some(call => call.method === "POST"), false);
  assert.match(el("resultRows").textContent, /abc-vlacase0Not assessed/);
  el("newComparison").click(); await pause();
  assert.equal(el("comparisonForm").hidden, false);
  assert.equal(el("comparisonMode").value, "campaign");
  assert.equal(el("strategyRows").querySelectorAll("input").length, 2);
  assert.equal(el("reviewButton").disabled, false);
});

test("stale server configuration rejects launch visibly and leaves review editable", async t => {
  const {w, el, changed} = await workspace(t);
  el("comparisonForm").dispatchEvent(new w.Event("submit", {cancelable: true})); await pause();
  changed(); el("launchButton").click(); await pause();
  assert.match(el("pageStatus").textContent, /Configuration changed/);
  assert.equal(el("review").hidden, false);
  assert.equal(el("execution").hidden, true);
});
