"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const {mergeDatasetMembers} = require("../frontend/datasets.js");

const revisions = [
  {id: "old-r1", case_id: "old"}, {id: "old-r2", case_id: "old"},
  {id: "excluded-r1", case_id: "excluded"}, {id: "new-r1", case_id: "new"}
];
const parent = {id: "dataset-1", contract_id: "contract-1", members: [
  {case_revision_id: "old-r1", disposition: "included", review_ids: ["original-review"], reason: "Original choice", case_sha256: "extra-server-field"},
  {case_revision_id: "excluded-r1", disposition: "excluded", review_ids: ["rejected-review"], reason: "Target is occluded"}
]};
const addition = id => ({case_revision_id: id, disposition: "included", review_ids: ["latest-review"], reason: ""});

test("adding cases retains previous inclusions, exclusions, reasons and exact review versions", () => {
  const result = mergeDatasetMembers(parent, [addition("old-r1"), addition("new-r1")], revisions, "contract-1");
  assert.deepEqual(result, [
    {case_revision_id: "old-r1", disposition: "included", review_ids: ["original-review"], reason: "Original choice"},
    {case_revision_id: "excluded-r1", disposition: "excluded", review_ids: ["rejected-review"], reason: "Target is occluded"},
    addition("new-r1")
  ]);
  result[0].review_ids.push("edited-in-preview");
  assert.deepEqual(parent.members[0].review_ids, ["original-review"]);
});

test("another case revision or contract cannot silently replace frozen evaluation provenance", () => {
  assert.throws(() => mergeDatasetMembers(parent, [addition("old-r2")], revisions, "contract-1"), /another revision/);
  assert.throws(() => mergeDatasetMembers(parent, [addition("new-r1")], revisions, "new-contract"), /different success definition/);
  assert.throws(() => mergeDatasetMembers(parent, [], [], "contract-1"), /saved case could not be loaded/);
});

test("new dataset preserves explicit selection without pulling in unrelated old members", () => {
  assert.deepEqual(mergeDatasetMembers(null, [addition("new-r1")], revisions, "contract-1"), [addition("new-r1")]);
});

test("dataset selection inherits its definition and submits all prior and added cases", async () => {
  const fs = require("node:fs"), path = require("node:path"), {JSDOM} = require("jsdom");
  const frontend = path.resolve(__dirname, "../frontend");
  const dom = new JSDOM(fs.readFileSync(path.join(frontend, "datasets.html"), "utf8"), {url: "http://localhost/static/datasets.html", runScripts: "outside-only"});
  const w = dom.window, calls = [], dataset = {...parent, name: "Robot acceptance set"};
  const cases = revisions.map(item => ({...item, case_revision_id: item.id, name: item.id, task: "Place item", readiness: {assessment: "unreviewed"}}));
  w.HTMLElement.prototype.scrollIntoView = () => {};
  w.fetch = async (url, options = {}) => {
    calls.push({url, body: options.body && JSON.parse(options.body)});
    let value;
    if (url.startsWith("/api/cases?")) value = {cases: [cases.find(item => item.id === "new-r1")], total: 1};
    else if (url.startsWith("/api/cases/")) value = cases.find(item => url.endsWith(item.id));
    else if (url.startsWith("/api/reviews?")) value = {reviews: []};
    else if (url === "/api/datasets/dataset-1") value = dataset;
    else if (url.startsWith("/api/datasets?")) value = {datasets: [dataset]};
    else if (url === "/api/datasets/preview") value = {preview_hash: "preview", readiness: "incomplete", issues: []};
    else if (url === "/api/success-contracts") value = {contracts: [{id: "contract-1", name: "Original definition", criteria: [], case_expectations: {"old-r1": "Saved original expectation"}}]};
    else if (url === "/api/strategies") value = {strategies: []};
    else if (url === "/api/campaigns") value = [];
    else if (url === "/api/baselines") value = {baselines: []};
    else if (url === "/api/assistant/status") value = {available: false};
    else throw Error(`Unexpected request ${url}`);
    return {ok: true, json: async () => value};
  };
  const pause = () => new Promise(resolve => setTimeout(resolve, 30));
  try {
    w.eval(fs.readFileSync(path.join(frontend, "navigation.js"), "utf8"));
    w.eval(fs.readFileSync(path.join(frontend, "datasets.js"), "utf8"));
    await pause();
    const el = id => w.document.getElementById(id);
    el("caseList").querySelector("input").click();
    el("datasetParent").value = "dataset-1";
    el("datasetParent").dispatchEvent(new w.Event("change", {bubbles: true}));
    assert.equal(el("datasetName").value, dataset.name);
    assert.equal(el("campaignContract").value, "contract-1");
    el("previewFreeze").click(); await pause();
    const preview = calls.find(call => call.url === "/api/datasets/preview");
    assert.ok(preview, el("freezePreview").textContent);
    assert.equal(preview.body.name, dataset.name);
    assert.equal(preview.body.parent_id, "dataset-1");
    assert.deepEqual(preview.body.members.map(item => item.case_revision_id), ["old-r1", "excluded-r1", "new-r1"]);
    assert.deepEqual(preview.body.members[0].review_ids, ["original-review"]);
    assert.equal(preview.body.members[1].disposition, "excluded");
    assert.equal(preview.body.members[1].reason, "Target is occluded");
    assert.equal(el("freezeDataset").disabled, false);

    [...el("datasetList").querySelectorAll("button")].find(item => item.textContent === "Use these cases").click();
    await pause();
    w.document.querySelector('[data-journey-step="cases"]').click();
    const selectedNames = () => [...el("selectedCases").querySelectorAll("h3,h4")].map(item => item.textContent);
    assert.deepEqual(selectedNames(), ["old-r1"]);
    assert.equal(el("selectedCases").querySelector("textarea").value, "Saved original expectation");
    assert.match(el("runContext").textContent, /Saved dataset: Robot acceptance set/);
    assert.equal(el("campaignContract").value, "contract-1");
    // The previously selected new case was removed when the dataset was loaded.
    const newCase = el("caseList").querySelector("input"); assert.equal(newCase.checked, false);
    newCase.click();
    assert.deepEqual(selectedNames(), ["old-r1", "new-r1"]);
    assert.doesNotMatch(el("runContext").textContent, /Frozen dataset:/);
    assert.equal(el("selectedCases").querySelector("textarea").value, "Saved original expectation");
    assert.equal(el("campaignContract").value, "");
    assert.equal(el("datasetParent").value, "dataset-1");
  } finally { w.close(); }
});
