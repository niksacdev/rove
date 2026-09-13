"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
const {JSDOM} = require("jsdom");
const {buildStrategyProgress, render, elapsedLabel} = require("../frontend/campaign-progress.js");
const root = path.resolve(__dirname, "..");
const campaign = {id: "campaign", status: "running", spec: {name: "Pick comparison", tasks: [{id: "case", task: "Place the red part in the bin"}], strategies: ["baseline", "candidate"], seeds: [10, 20]}, strategy_definitions: {baseline: {display_name: "Current pipeline"}, candidate: {display_name: "Candidate pipeline"}}};
const started = "2026-09-13T10:00:00Z", now = Date.parse(started) + 65000;
const running = {trial_id: "trial", task_id: "case", strategy_id: "baseline", seed: 20, execution: "running", started_at: started};
const events = [{event_type: "rove.stage.started", stage: "reset", endpoint_id: "sim"}, {event_type: "stage", data: {stage: "act", status: "running", model_id: "agent"}}];

test("strategy lanes distinguish queued work, actual running trial and elapsed time without fabricated percentage", () => {
  const lanes = buildStrategyProgress(campaign, [running], {trial: {events}}, now);
  assert.deepEqual(lanes.map(lane => [lane.status, lane.planned, lane.finished, lane.remaining]), [["running", 2, 0, 1], ["queued", 2, 0, 2]]);
  assert.equal(lanes[0].executions[0].repetition, 2, "repeat is its position in configured seeds, not seed + 1");
  assert.equal(lanes[0].executions[0].elapsed, "1m 5s elapsed");
  assert.deepEqual(lanes[0].executions[0].stages.map(stage => stage.stage), ["act"]);
  assert.equal(lanes[1].executions.length, 0);
  assert.equal(elapsedLabel(null), "Elapsed time unavailable");
});

test("execution completion is distinct from acceptance and errors; cancelled campaigns do not promise queued work", () => {
  const rows = [{...running, seed: 10, execution: "invalid_verdict", finished_at: "2026-09-13T10:00:01Z", attempt_wall_ms: 1500}, {...running, execution: "timeout", finished_at: "2026-09-13T10:00:02Z", attempt_wall_ms: 2500}];
  const lanes = buildStrategyProgress({...campaign, status: "completed"}, [...rows, rows[1]], {}, now);
  assert.equal(lanes[0].status, "error"); assert.equal(lanes[0].finished, 2); assert.equal(lanes[0].errors, 1);
  assert.equal(lanes[0].executions[0].elapsed, "2s elapsed");
  assert.equal(lanes[1].remainingLabel, "not run"); assert.equal(lanes[1].status, "incomplete");
  const cancelled = buildStrategyProgress({...campaign, status: "cancelled"}, [{...running, execution: "cancelled"}], {}, now);
  assert.equal(cancelled[0].status, "cancelled"); assert.equal(cancelled[0].remainingLabel, "not run");
  assert.equal(cancelled[0].executions[0].elapsed, "Elapsed time unavailable");
});

test("real parallel agent-loop fixture cannot fabricate stages from adapter telemetry or configured endpoints", () => {
  const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures/parallel_agent_loop_stage_events.json"), "utf8"));
  const lanes = buildStrategyProgress(campaign, [{...running, execution: "completed", result: fixture.trial.result}], {trial: {events: fixture.events}}, now);
  assert.deepEqual(lanes[0].executions[0].stages.map(stage => stage.stage), ["act", "verify"]);
});

test("progress is visible without a disclosure, safely escaped and preserves inspect-button focus across polls", t => {
  const dom = new JSDOM('<main id="lanes"></main>'); t.after(() => dom.window.close());
  const container = dom.window.document.getElementById("lanes"), inspected = [];
  let lanes = buildStrategyProgress(campaign, [running], {trial: {events}}, now);
  lanes[0].name = "<img src=x onerror=alert(1)>";
  render(container, lanes, id => inspected.push(id));
  assert.equal(container.querySelectorAll("article").length, 2);
  assert.equal(container.querySelectorAll("details,img").length, 0);
  assert.match(container.textContent, /Current trial · repetition 2 · 1m 5s elapsed/);
  assert.match(container.textContent, /Act · running · agent/);
  assert.match(container.textContent, /Waiting for its turn/);
  assert.equal(container.querySelectorAll('[role="status"]').length, 2);
  const button = container.querySelector("button"); button.focus(); button.click(); assert.deepEqual(inspected, ["trial"]);
  lanes[0].executions[0].elapsed = "1m 7s elapsed";
  render(container, lanes, id => inspected.push(id));
  assert.equal(container.querySelector("button"), button); assert.equal(dom.window.document.activeElement, button);
  assert.match(container.textContent, /1m 7s elapsed/);
  render(container, lanes.slice(1), () => {}); assert.equal(container.querySelectorAll("article").length, 1);
});

async function workspace(t) {
  const dom = new JSDOM(fs.readFileSync(path.join(root, "frontend/datasets.html"), "utf8"), {url: "http://localhost/static/datasets.html?step=run&campaign=campaign", runScripts: "outside-only", pretendToBeVisual: true});
  t.after(() => dom.window.close()); const w = dom.window, calls = [];
  w.HTMLElement.prototype.scrollIntoView = () => {};
  const read = file => fs.readFileSync(path.join(root, "frontend", file), "utf8");
  let stageFailure = false, eventNumber = 0;
  w.fetch = async (url, options = {}) => {
    calls.push({url, method: options.method || "GET"}); let value;
    if (url === "/api/campaigns/campaign") value = {campaign, summary: {planned_trials: 4, completed_trials: 0, strategies: [], tasks: []}};
    else if (url === "/api/campaigns/campaign/assessments") value = {trials: [running], summary: {planned_trials: 4, completed_trials: 0}, assessments: {}};
    else if (url.startsWith("/api/trials/trial/events?")) {
      if (stageFailure) throw Error("Temporarily disconnected");
      const offset = Number(new URL(url, w.location.origin).searchParams.get("offset") || 0);
      value = {events: offset ? [{event_type: "stage", data: {stage: "act", status: "completed", model_id: "agent"}}] : events, total: offset ? offset + 1 : events.length}; eventNumber++;
    }
    else if (url === "/api/trials/trial") value = {id: "trial", status: "running", result: null};
    else if (url === "/api/assistant/status") value = {available: false};
    else if (url === "/api/examples") value = {examples: []};
    else if (url.startsWith("/api/cases?")) value = {cases: [], total: 0};
    else if (url.startsWith("/api/datasets?")) value = {datasets: []};
    else if (url.startsWith("/api/trials?")) value = {trials: []};
    else if (url === "/api/strategies") value = {strategies: []};
    else if (url === "/api/success-contracts") value = {contracts: []};
    else if (url === "/api/baselines") value = {baselines: []};
    else if (url === "/api/campaigns") value = [];
    else throw Error(`Unexpected request ${url}`);
    return {ok: true, json: async () => value};
  };
  for (const file of ["navigation.js", "stage-renderers.js", "trial-output.js", "campaign-progress.js", "datasets.js"]) w.eval(read(file));
  const pause = () => new Promise(resolve => setTimeout(resolve, 30)); await pause();
  return {w, calls, pause, fail: value => {stageFailure = value;}, eventCount: () => eventNumber};
}

test("Run loads real active stage feedback automatically, incrementally retries and keeps full output lazy", async t => {
  const {w, calls, pause, fail} = await workspace(t), el = id => w.document.getElementById(id);
  assert.match(el("strategyProgress").textContent, /Current pipeline.*Running/s);
  assert.match(el("strategyProgress").textContent, /Act · running · agent/);
  assert.equal(calls.some(call => call.url === "/api/trials/trial"), false);
  assert.equal(el("liveTrials").querySelector("details").open, false);
  fail(true); el("refreshLiveTrials").click(); await pause();
  assert.match(el("strategyProgress").textContent, /Stage updates unavailable/);
  fail(false); el("refreshLiveTrials").click(); await pause();
  assert.match(el("strategyProgress").textContent, /Act · completed · agent/);
  assert.ok(calls.some(call => /events\?limit=200&offset=2$/.test(call.url)));
  el("strategyProgress").querySelector("button").click(); await pause();
  assert.equal(el("liveTrials").querySelector("details").open, true);
  assert.ok(calls.some(call => call.url === "/api/trials/trial"));
  assert.equal(calls.some(call => call.method !== "GET" && !call.url.endsWith("/outcome-summary")), false);
});
