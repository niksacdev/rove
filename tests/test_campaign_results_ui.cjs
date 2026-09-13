"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const {JSDOM} = require("jsdom");
const {outcomeRows, render} = require("../frontend/campaign-results.js");
const campaign = {strategy_definitions: {a: {display_name: "Current pipeline"}, b: {display_name: "Changed planner"}}};
function summary() {
  return {completed_trials: 6, planned_trials: 6, tasks: [{strategy_id: "a", passed: 1, failed: 1, unknown: 1}, {strategy_id: "b", passed: 2, failed: 0, unknown: 1}], strategies: [
    {strategy_id: "a", pass_at_k: [{k: 1, value: .5}, {k: 2, value: null, lower: .5, upper: 1}], pass_pow_k: [{k: 1, value: .5}, {k: 2, value: .25}], latency_p95_ms: 400},
    {strategy_id: "b", pass_at_k: [{k: 1, value: null, lower: 0, upper: 1}], pass_pow_k: [{k: 1, value: null, lower: 0, upper: 1}], latency_p95_ms: null},
  ], campaign_targets: [{strategy_id: "a", metric: "task_success", status: "unknown"}, {strategy_id: "b", metric: "pipeline_latency", status: "not_met"}]};
}
function view(t, result = summary(), definition = campaign) {
  const dom = new JSDOM('<main id="charts"></main>'); t.after(() => dom.window.close());
  const container = dom.window.document.getElementById("charts"); render(container, result, definition);
  return {container, w: dom.window};
}
test("outcome charts use strategy-specific counts and expose unresolved assessments instead of converting them to failure", t => {
  const data = summary(), rows = outcomeRows(data, campaign);
  assert.deepEqual(rows.map(row => [row.name, row.passed, row.failed, row.unknown]), [["Current pipeline", 1, 1, 1], ["Changed planner", 2, 0, 1]]);
  const {container} = view(t, data);
  assert.match(container.querySelector(".outcome-statistics").textContent, /Accepted3Rejected1Unassessed2Trials recorded6 \/ 6/);
  assert.equal(container.querySelectorAll(".outcome-bar").length, 2);
  assert.match(container.querySelector(".outcome-bar").getAttribute("aria-label"), /1 accepted, 1 rejected, 1 unassessed/);
  assert.ok(Math.abs(parseFloat(container.querySelector(".outcome-bar .unknown").style.width) - 100 / 3) < 0.001);
  assert.match(container.querySelector(".target-chart").textContent, /task success · Unassessed/);
  assert.match(container.querySelector(".target-chart").textContent, /pipeline latency · Not met/);
});
test("reliability toggles between actual pass@k and pass^k while bounds remain visibly distinct from estimates", t => {
  const {container, w} = view(t);
  assert.equal(container.querySelectorAll("svg circle").length, 1, "unknown values are not plotted as estimates");
  assert.equal(container.querySelectorAll('svg line[stroke-dasharray]').length, 2);
  assert.match(container.textContent, /Dashed bounds show unresolved outcomes/);
  const values = container.querySelector("details"); assert.equal(values.open, false);
  assert.match(values.textContent, /Not yet known50.0–100.0%/);
  const measure = container.querySelector("select"); measure.value = "pass_pow_k"; measure.dispatchEvent(new w.Event("change"));
  assert.match(container.querySelector("svg").getAttribute("aria-label"), /pass\^k/);
  assert.equal(container.querySelectorAll("svg circle").length, 2);
  assert.match(values.textContent, /25.0%/);
  assert.doesNotMatch(values.textContent, /50.0–100.0%/);
});
test("missing timing, all-unknown reliability, zero trials and hostile labels remain honest and safe", t => {
  const data = summary(); data.strategies.forEach(row => {row.pass_at_k = [{k: 1, value: null, lower: 0, upper: 1}];});
  const {container} = view(t, data, {strategy_definitions: {a: {display_name: "<img src=x onerror=alert(1)>"}}});
  assert.equal(container.querySelector("img"), null);
  assert.equal(container.querySelectorAll("svg circle").length, 0);
  assert.match(container.textContent, /they are not confidence intervals/);
  assert.match(container.querySelector(".latency-chart").textContent, /400 ms.*Not recorded/s);
  assert.match(container.textContent, /Pipeline timing is not robot task completion time/);
  render(container, {strategies: [], tasks: [], completed_trials: 0, planned_trials: 0}, {});
  assert.match(container.textContent, /No strategy outcomes recorded yet/);
  assert.match(container.textContent, /Reliability becomes available/);
  assert.equal(container.querySelector("svg"), null);
  assert.equal(container.querySelectorAll(".outcome-statistics").length, 1, "refresh replaces, rather than duplicates, charts");
});
