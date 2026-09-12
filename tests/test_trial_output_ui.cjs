"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
const {JSDOM} = require("jsdom");
const {stagesFromTrial} = require("../frontend/trial-output.js");
const root = path.resolve(__dirname, ".."), read = file => fs.readFileSync(path.join(root, "frontend", file), "utf8");
function viewer(t) {
  const dom = new JSDOM('<div id="output"></div>', {url: "http://localhost/", runScripts: "outside-only"}); t.after(() => dom.window.close());
  const w = dom.window; w.eval(read("stage-renderers.js")); w.eval(read("trial-output.js"));
  return {w, output: w.document.getElementById("output")};
}
const scene = {stage: "perceive", status: "completed", model_id: "vlm-camera", latency_ms: 12, output: {objects: [{label: "red cube", confidence: 0.9}], scene_description: "A cube on the table"}};
const plan = {stage: "plan", status: "completed", model_id: "planner", latency_ms: 24, output: {target_object: "red cube", steps: ["pick cube", "place cube"], reasoning: "Keep the approach clear"}};

test("shared renderer shows VLM objects, agent plan, VLA trajectory and configured verification measurements", t => {
  const {w, output} = viewer(t);
  const trial = {id: "trial-x", status: "completed", result: {stages: [scene, plan,
    {stage: "act", status: "completed", model_id: "vla", output: {action_type: "trajectory", actions: [[0.1, 0.2, -0.3, 0, 0, 0, 1]], sim_is_mock: true, sim_success: true, sim_done: true}},
    {stage: "verify", status: "completed", model_id: "grader", output: {evaluator_version: "r1", evaluator_result: {verdict: "unknown", evidence_quality: "unknown", reasoning: "Robot execution evidence is absent", measurements: [{name: "completion", value: null, quality: "unknown"}]}}},
  ]}};
  w.RoveTrialOutput.render(output, trial, [], {caseRecord: {name: "Pick red cube", image_asset: {sha256: "a".repeat(64)}}, task: {task: "Place the cube in the bin"}});
  assert.equal(output.querySelectorAll(".rich-trial-stage").length, 4);
  assert.match(output.textContent, /Detected objects/); assert.match(output.textContent, /red cube \(90%\)/);
  assert.match(output.textContent, /Keep the approach clear/); assert.match(output.textContent, /pick cube/);
  assert.match(output.textContent, /Trajectory/); assert.match(output.textContent, /0.1000/); assert.match(output.textContent, /Sim: mock/);
  assert.match(output.textContent, /completion: Unavailable/); assert.match(output.textContent, /Robot execution evidence is absent/);
  assert.equal(output.querySelector("img").getAttribute("src"), `/api/trial-assets/${"a".repeat(64)}`);
  assert.equal(output.querySelectorAll(".stage-raw-output[open]").length, 0);
  assert.equal(output.querySelector("a").getAttribute("href"), "/static/history.html?trial=trial-x");
});

test("agent tool calls and computed dynamics reuse original output components", t => {
  const {w, output} = viewer(t);
  w.RoveTrialOutput.render(output, {id: "agent", status: "completed", result: {stages: [
    {stage: "act", status: "completed", output: {action_type: "tool_calls", tool_calls: [{tool: "move_gripper", args: {x: 0.2}}]}},
    {stage: "dynamics", status: "completed", output: {evidence: [{field: "joint_limits", value: null, confidence: "unknown", detail: "Initial state missing"}], assumptions: {}}},
  ]}}, []);
  assert.match(output.textContent, /Tool invocations/); assert.match(output.textContent, /move_gripper/);
  assert.match(output.textContent, /joint limits: Unknown \/ not computed/);
  assert.match(output.textContent, /This is not observed robot execution/);
});

test("stage events show running output and final saved data supersedes transient updates without duplicate phases", () => {
  const events = [
    {event_type: "rove.stage.started", stage: "perceive", endpoint_id: "vlm"},
    {event_type: "stage", data: {...scene, phase: "execution"}},
    {event_type: "rove.stage.ended", stage: "perceive", endpoint_id: "vlm"},
    {event_type: "stage", data: {stage: "plan", status: "running", model_id: "planner"}},
  ];
  let stages = stagesFromTrial({status: "running"}, events);
  assert.equal(stages.length, 2); assert.equal(stages[0].output.objects[0].label, "red cube");
  assert.equal(stages[1].status, "running");
  stages = stagesFromTrial({result: {result: {stages: [{...scene, phase: "execution"}, plan]}}}, events);
  assert.equal(stages.length, 2); assert.equal(stages[0].status, "completed"); assert.equal(stages[1].output.target_object, "red cube");
});

test("case expectations are explicit per render and cannot leak from another trial or global sample selection", t => {
  const {w, output} = viewer(t);
  w._selectedExpectedSubtasks = ["unrelated global answer"];
  w.RoveTrialOutput.render(output, {id: "a", status: "completed", result: {stages: [plan]}}, [], {caseRecord: {reference_data: {expected_subtasks: ["case A secret"]}}});
  assert.match(output.textContent, /case A secret/); assert.doesNotMatch(output.textContent, /unrelated global answer/);
  w.RoveTrialOutput.render(output, {id: "b", status: "completed", result: {stages: [plan]}}, []);
  assert.doesNotMatch(output.textContent, /case A secret|unrelated global answer/);
});

test("rich output preserves collapsed stages and expanded raw evidence on refresh and renders hostile text safely", t => {
  const {w, output} = viewer(t), trial = {id: "hostile/a", status: "completed", result: {stages: [{...plan, output: {...plan.output, reasoning: "<img src=x onerror=alert(1)>"}}]}};
  w.RoveTrialOutput.render(output, trial, []);
  const panel = output.querySelector(".rich-trial-stage"); panel.open = false;
  output.querySelector(".stage-raw-output").open = true;
  w.RoveTrialOutput.render(output, trial, []);
  assert.equal(output.querySelector(".rich-trial-stage").open, false);
  assert.equal(output.querySelector(".stage-raw-output").open, true);
  assert.equal(output.querySelector("img"), null);
  assert.match(output.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.equal(output.querySelector("a").getAttribute("href"), "/static/history.html?trial=hostile%2Fa");
});

test("both original runner and campaigns load the one shared stage implementation", () => {
  assert.match(read("index.html"), /stage-renderers.js/); assert.match(read("datasets.html"), /stage-renderers.js/);
  assert.match(read("app.js"), /RoveStageRenderers.renderStageOutput/);
  assert.doesNotMatch(read("app.js"), /Detected objects/);
  assert.match(read("trial-output.js"), /RoveStageRenderers.renderStageOutput/);
});


test("real parallel agent-loop trace shows only orchestrator stages, not internal perception or simulator reset", () => {
  const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures/parallel_agent_loop_stage_events.json"), "utf8"));
  assert.ok(fixture.events.some(event => event.stage === "perceive" && event.event_type === "rove.stage.ended"));
  assert.equal(fixture.trial.strategy.perceive, "mock-vlm", "a configured endpoint alone cannot identify a top-level stage");
  const completed = stagesFromTrial(fixture.trial, fixture.events);
  assert.deepEqual(completed.map(stage => [stage.stage, stage.phase, stage.status]), [["act", "execution", "completed"], ["verify", "evaluation", "completed"]]);
  const runningEvents = fixture.events.slice(0, -1);
  const running = stagesFromTrial({...fixture.trial, status: "running", result: null}, runningEvents);
  assert.deepEqual(running.map(stage => stage.stage), ["act", "verify"]);
  assert.equal(running[1].status, "running", "internal ended spans do not complete the verifier");
  assert.deepEqual(stagesFromTrial({status: "running"}, fixture.events.filter(event => event.event_type.startsWith("rove.stage."))), []);
});

test("a saved empty stage list remains empty and ended instrumentation cannot assert completion", () => {
  const spans = [{event_type: "rove.stage.ended", stage: "plan", endpoint_id: "planner"}];
  assert.deepEqual(stagesFromTrial({result: {stages: []}}, spans), []);
  const stages = stagesFromTrial({result: {plan: "Saved plan"}}, spans);
  assert.equal(stages.length, 1); assert.equal(stages[0].status, "recorded"); assert.equal(stages[0].model_id, "planner");
});
