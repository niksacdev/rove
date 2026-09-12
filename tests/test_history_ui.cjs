// Run with: node --test tests/test_history_ui.cjs
const test = require("node:test");
const assert = require("node:assert/strict");
const {trialPresentation, trialTitle, trialStrategy, usagePresentation} = require("../frontend/history.js");

function trial(output, extra = {}) {
  return {status: "completed", result: {stages: [{stage: "verify", status: "completed", output}]}, ...extra};
}

test("a completed process and top-level success do not establish an assessment", () => {
  assert.equal(trialPresentation({status: "completed", result: {success: true}}).verdict, "unknown");
  assert.equal(trialPresentation({status: "running"}).verdict, "unknown");
});

test("invalid and failed verification cannot be displayed as a pass", () => {
  assert.equal(trialPresentation(trial({success: true, verdict_valid: false})).verdict, "unknown");
  const failed = trial({success: true}); failed.result.stages.push({stage: "act", status: "error"});
  assert.equal(trialPresentation(failed).verdict, "unknown");
});

test("required constraints override the evaluator's optimistic verdict", () => {
  const check = {required: true, execution: "completed", result: {verdict: "fail"}};
  assert.equal(trialPresentation(trial({success: true, check_results: [check]})).verdict, "fail");
  assert.equal(trialPresentation(trial({success: true, check_results: [{...check, execution: "timeout"}]})).verdict, "unknown");
  assert.equal(trialPresentation(trial({success: true, check_results: [{...check, result: {verdict: "unknown"}}]})).verdict, "unknown");
  assert.equal(trialPresentation(trial({success: true, check_results: [{...check, required: false}]})).verdict, "pass");
});

test("campaign wrapper and declared synthetic quality remain distinct", () => {
  const output = {success: true, evaluator_result: {verdict: "pass", evidence_quality: "synthetic"}};
  const saved = {result: {outcome: "pass", result: trial(output).result}};
  assert.equal(trialPresentation(saved).verdict, "pass");
  assert.equal(trialPresentation(saved).quality, "synthetic");
  assert.equal(trialPresentation(trial({success: true})).quality, "unknown");
});

test("explicit unknown verdict is preserved", () => {
  assert.equal(trialPresentation({result: {outcome: "unknown", result: trial({success: true}).result}}).verdict, "unknown");
  assert.equal(trialPresentation(trial({success: true, evaluator_result: {verdict: "unknown"}})).verdict, "unknown");
});

test("missing usage is different from a provider reporting zero", () => {
  assert.equal(usagePresentation([]), "Not reported");
  assert.equal(usagePresentation([{event_type: "assistant.usage", data: {input_tokens: 0, output_tokens: 0}}]), "0 input / 0 output tokens");
  assert.equal(usagePresentation([{event_type: "assistant.usage", data: {inputTokens: 12}}]), "12 input / unknown output tokens");
  assert.equal(usagePresentation([{event_type: "assistant.usage", data: {costMultiplier: 2}}]), "Token counts not reported");
  assert.equal(usagePresentation([{event_type: "assistant.usage", data: {input_tokens: -1, output_tokens: NaN}}]), "Token counts not reported");
});

test("legacy and current task/configuration names retain their text", () => {
  assert.equal(trialTitle({task: {instruction: "Pick <img src=x>"}}), "Pick <img src=x>");
  assert.equal(trialTitle({task: "Pick the cup"}), "Pick the cup");
  assert.equal(trialStrategy({strategy: {name: "Baseline"}}), "Baseline");
  assert.equal(trialStrategy({}), "Configuration unavailable");
});

test('a failed assessment is distinct from a tool execution error', () => {
  const {eventIsError} = require('../frontend/history.js');
  assert.equal(eventIsError({event_type: 'strategy_complete', data: {success: false, outcome: 'fail'}}), false);
  assert.equal(eventIsError({event_type: 'tool.execution_complete', data: {success: false}}), true);
});
