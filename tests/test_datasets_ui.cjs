const test = require("node:test");
const assert = require("node:assert/strict");
const {parseObject, buildCaseMetadata, caseSourceLabels, buildContract, buildReview, buildCampaign, metricLabel, activeReviewIds, assessedCounts, metricValue, pairedTrialId, evidenceHref} = require("../frontend/datasets.js");
const {sampleCaseHref, createSampleCaseCard} = require("../frontend/sample-cases.js");

const review = overrides => ({target: "case_validity", caseId: "case-r1", trialId: "ignored-output", contractId: "contract", reviewer: "Reviewer", status: "final", decision: "accepted", rationale: "The instruction and observation are sufficient.", ...overrides});
const campaign = overrides => ({name: "Baseline", caseIds: ["case-r1"], contractId: "contract", strategyId: "agent", repeats: 3, timeout: 120, ...overrides});

test("structured imports reject arrays, primitives and malformed JSON", () => {
  assert.deepEqual(parseObject("", "Conditions"), {});
  assert.deepEqual(parseObject('{"unit":"m"}', "Conditions"), {unit: "m"});
  for (const input of ["null", "[]", "5", "{bad"]) assert.throws(() => parseObject(input, "Conditions"), /JSON/);
});

test("human acceptance and configured verification have distinct contracts", () => {
  const input = {name: "Plan v1", scope: "plan_quality", evidenceMode: "candidate_output", criteria: "Safe plan", assessment: "human_review"};
  const human = buildContract(input);
  assert.equal(human.criteria[0].assessment, "human_review");
  assert.equal(human.criteria[0].endpoint, undefined);
  assert.equal(human.evidence_mode, "candidate_output");
  assert.throws(() => buildContract({...input, assessment: "configured_verifier"}), /endpoint/);
  assert.equal(buildContract({...input, assessment: "configured_verifier", endpoint: "task-grader"}).criteria[0].endpoint, "task-grader");
});

test("case validity does not become a grade of a particular trial", () => {
  const result = buildReview(review());
  assert.equal(result.target_type, "case_validity");
  assert.equal(result.trial_id, null);
  assert.deepEqual(result.annotations, {});
  assert.equal(result.case_revision_id, "case-r1");
});

test("output ratings require explicit trial identity and preserve unknown criteria", () => {
  assert.throws(() => buildReview(review({target: "trial_output", trialId: ""})), /specific trial/);
  const output = buildReview(review({target: "trial_output", trialId: "trial-1", decision: "unknown", criteria: {completion: "unknown"}}));
  assert.equal(output.trial_id, "trial-1");
  assert.equal(output.criteria.completion, "unknown");
  assert.equal(output.decision, "unknown");
});

test("accepted reusable annotations need labels; plain expectations need no JSON authoring", () => {
  assert.throws(() => buildReview(review({target: "case_annotation", annotations: ""})), /explicit labels/);
  assert.deepEqual(buildReview(review({target: "case_annotation", annotations: "Target the red part"})).annotations, {expectations: "Target the red part"});
  assert.deepEqual(buildReview(review({target: "case_annotation", annotations: '{"object":"red part"}'})).annotations, {object: "red part"});
});

test("final reviews require attribution and rationale; drafts preserve unfinished assessment", () => {
  assert.throws(() => buildReview(review({rationale: " "})), /Explain/);
  assert.throws(() => buildReview(review({reviewer: " "})), /reviewer/);
  assert.equal(buildReview(review({status: "draft", rationale: "", decision: "unknown"})).status, "draft");
  assert.equal(buildReview(review({supersedes: "old-review"})).supersedes_id, "old-review");
});

test("campaign repetitions remain bounded integers and unavailable inputs cannot launch", () => {
  for (const repeats of [0, 2.5, 1001, NaN]) assert.throws(() => buildCampaign(campaign({repeats})), /Repeats/);
  assert.throws(() => buildCampaign(campaign({caseIds: []})), /Select/);
  assert.throws(() => buildCampaign(campaign({contractId: ""})), /contract/);
  assert.throws(() => buildCampaign(campaign({timeout: Infinity})), /timeout/);
  assert.deepEqual(buildCampaign(campaign()).seeds, [0, 1, 2]);
  assert.deepEqual(buildCampaign(campaign()).ks, [1, 3]);
});

test("a frozen dataset replaces mutable selected cases in a launch request", () => {
  const payload = buildCampaign(campaign({datasetId: "frozen-1"}));
  assert.equal(payload.dataset_revision_id, "frozen-1");
  assert.equal(payload.case_revision_ids, undefined);
  assert.deepEqual(buildCampaign(campaign({repeats: 1})).ks, [1]);
});

test("freeze defaults retain current disagreements without selecting superseded finals", () => {
  const records = [{id: "old", status: "final", decision: "accepted"}, {id: "new", status: "final", supersedes_id: "old", decision: "rejected"}, {id: "peer", status: "final", decision: "accepted"}, {id: "draft", status: "draft", supersedes_id: "peer"}];
  assert.deepEqual(activeReviewIds(records), ["new", "peer"]);
});

test("metric names do not relabel pipeline latency as robot completion time", () => {
  assert.equal(metricLabel("pipeline_latency"), "Pipeline latency");
  assert.equal(metricLabel("episode_completion_time"), "Episode completion time");
  assert.match(metricLabel("pass_pow_k"), /pass\^k/);
});

test("report counts use assessment rows, preserving unresolved planned attempts", () => {
  assert.deepEqual(assessedCounts({tasks: [{passed: 1, failed: 2, unknown: 3}, {passed: 0, failed: 0, unknown: 5}], raw_model_successes: 20}), {passed: 1, failed: 2, unknown: 8});
  assert.deepEqual(assessedCounts(null), {passed: 0, failed: 0, unknown: 0});
});

test("missing reliability estimates display bounds rather than an invented point estimate", () => {
  assert.equal(metricValue({value: 0, lower: 0, upper: 0}), "0.0%");
  assert.equal(metricValue({value: null, lower: 0.2, upper: 0.7}), "20.0%–70.0% unresolved range");
  assert.equal(metricValue({value: null, lower: null, upper: null}), "Unavailable");
});

test("paired evidence links match case, repeat and selected strategy", () => {
  const comparison = {baseline: {strategies: [{strategy_id: "baseline"}]}, baseline_trials: [{task_id: "case", seed: 0, strategy_id: "other", trial_id: "wrong"}, {task_id: "case", seed: 0, strategy_id: "baseline", trial_id: "right"}, {task_id: "other-case", seed: 0, strategy_id: "baseline", trial_id: "wrong-case"}]};
  assert.equal(pairedTrialId(comparison, {baseline_task_id: "case"}, {seed: 0}, "baseline"), "right");
  assert.equal(pairedTrialId(comparison, {baseline_task_id: "case"}, {seed: 1}, "baseline"), undefined);
});

test("assistant evidence links cannot become remote or executable URLs", () => {
  for (const input of ["javascript:alert(1)", "https://example.com", "//example.com", "/\\example.com", "/\n/example.com", null]) assert.equal(evidenceHref(input), null);
  assert.equal(evidenceHref("/static/history.html?trial=recorded-id"), "/static/history.html?trial=recorded-id");
});

test("case revisions preserve private references separately from candidate input and episode evidence", () => {
  const references = {expected_subtasks: ["Pick red part", "Place in bin"], ground_truth_action: [0.2, 0.4], nested: {answer: "red"}};
  const metadata = buildCaseMetadata({name: " Revised sample ", task: " Pick the red part ", context: '{"constraints":["Keep it flat"]}', episode: '{}', references: JSON.stringify(references)});
  assert.equal(metadata.name, "Revised sample");
  assert.deepEqual(metadata.reference_data, references);
  assert.deepEqual(metadata.candidate_context, {constraints: ["Keep it flat"]});
  assert.deepEqual(metadata.recorded_evidence, {});
  assert.equal(metadata.expected_subtasks, undefined);
  assert.deepEqual(buildCaseMetadata({name: "New", task: "Task"}).reference_data, {});
  assert.throws(() => buildCaseMetadata({name: "New", task: "Task", references: "[]"}), /Reference annotations/);
});

test("source badges distinguish bundled inputs from user cases without asserting review or robot success", () => {
  assert.deepEqual(caseSourceLabels({conditions: {library: "bundled-gallery", source: {dataset: "RoboVQA"}, eval_category: "scene_analysis"}}), ["Bundled sample", "RoboVQA", "scene analysis"]);
  assert.deepEqual(caseSourceLabels({conditions: {source: "Customer pilot"}}), ["Customer pilot"]);
  assert.deepEqual(caseSourceLabels({}), []);
});

class TestElement {
  constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; this.listeners = {}; }
  appendChild(child) { this.children.push(child); return child; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
}
const testDocument = {createElement: tag => new TestElement(tag)};
const descendants = element => [element, ...element.children.flatMap(descendants)];

test("sample cards keep quick run and versioned case navigation as independent sibling controls", () => {
  const example = {task: "Pick the part", filename: "sample.png", case_id: "case", case_revision_id: "revision-1", import_status: "imported", source: {dataset: "Sample source"}};
  let selected;
  const card = createSampleCaseCard(example, value => { selected = value; }, "http://local", testDocument);
  assert.equal(card.tag, "article");
  const controls = descendants(card).filter(element => ["button", "a"].includes(element.tag));
  assert.equal(controls.length, 2);
  const quick = controls.find(element => element.tag === "button"), open = controls.find(element => element.tag === "a");
  assert.equal(descendants(quick).filter(element => element.tag === "a").length, 0);
  assert.equal(quick.type, "button");
  quick.listeners.click();
  assert.equal(selected, example);
  assert.equal(open.href, "/static/datasets.html?case=revision-1");
  assert.match(open.attributes["aria-label"], /versioned case/);
});

test("unavailable or malformed case identities never create a misleading Open case link", () => {
  for (const record of [{case_revision_id: "good"}, {case_revision_id: "good", import_status: "unavailable"}, {case_revision_id: "../../other", import_status: "imported"}, {case_revision_id: "javascript:alert(1)", import_status: "imported"}]) assert.equal(sampleCaseHref(record), null);
  const record = {task: "<script>unsafe</script>", filename: "a.png", import_status: "unavailable", import_error: "<img src=x onerror=alert(1)>"};
  const card = createSampleCaseCard(record, () => {}, "", testDocument);
  assert.equal(descendants(card).filter(element => element.tag === "a").length, 0);
  assert.equal(descendants(card).find(element => element.tag === "p").textContent, record.task);
  assert.ok(descendants(card).some(element => element.textContent === record.import_error));
  assert.ok(descendants(card).some(element => element.tag === "button"));
});
