"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const {parseCaseImport} = require("../frontend/case-import.js");
const image = {name: "scene.png", size: 500};
const record = {name: "Put bowl", task: "Place bowl on plate", image: "scene.png", reference_data: {expected: "plate"}};
test("JSONL maps multiple cases to selected images and keeps private annotations separate", () => {
  const result = parseCaseImport(JSON.stringify(record) + "\n\n" + JSON.stringify({...record, name: "Second case"}), [image]);
  assert.equal(result.length, 2); assert.equal(result[1].line, 3);
  assert.equal(result[0].file, image); assert.equal(result[0].metadata.image, undefined);
  assert.deepEqual(result[0].metadata.reference_data, record.reference_data);
  assert.equal(result[0].metadata.candidate_context, undefined);
});
test("all rows are checked before import: missing images, malformed JSON, unsafe shapes and size caps", () => {
  for (const text of ["{}", "null", "[]", "false", "bad", JSON.stringify({...record, extra: true}), JSON.stringify({...record, conditions: []})]) assert.throws(() => parseCaseImport(text, [image]));
  assert.throws(() => parseCaseImport(JSON.stringify(record), []), /select the image/);
  assert.throws(() => parseCaseImport(JSON.stringify(record), [image, image]), /unique filenames/);
  assert.throws(() => parseCaseImport(JSON.stringify(record), [{...image, size: 17 * 1024 * 1024}]), /16 MiB/);
  assert.throws(() => parseCaseImport(Array(101).fill(JSON.stringify(record)).join("\n"), [image]), /100 cases/);
});
test("preview makes no writes; explicit import publishes successful cases and stops after an uncertain response", async () => {
  const {JSDOM} = require("jsdom"), fs = require("node:fs"), path = require("node:path");
  const dom = new JSDOM(fs.readFileSync(path.join(__dirname, "../frontend/datasets.html"), "utf8"), {url: "http://localhost", runScripts: "outside-only"});
  const w = dom.window, $ = id => w.document.getElementById(id), calls = [], imported = [];
  w.TextEncoder = TextEncoder;
  const file = new w.File(["png"], "scene.png", {type: "image/png"});
  Object.defineProperty($("bulkCaseImages"), "files", {value: [file]});
  Object.defineProperty($("bulkCaseFile"), "files", {value: [{size: 200, text: async () => Array(3).fill(JSON.stringify(record)).join("\n")}]});
  w.fetch = async (url, options) => { calls.push({url, options}); if (calls.length === 2) throw Error("Disconnected"); return {ok: true, json: async () => ({id: "case1", name: record.name})}; };
  w.addEventListener("rove:case-imported", event => imported.push(event.detail));
  w.eval(fs.readFileSync(path.join(__dirname, "../frontend/case-import.js"), "utf8"));
  const pause = () => new Promise(resolve => setTimeout(resolve, 20));
  try {
    $("previewBulkImport").click(); await pause(); assert.equal(calls.length, 0); assert.equal($("confirmBulkImport").disabled, false);
    $("confirmBulkImport").click(); await pause();
    assert.equal(calls.length, 2); assert.equal(imported.length, 1);
    assert.equal(calls[0].url, "/api/cases"); assert.match($("bulkImportStatus").textContent, /Earlier successful cases are saved/);
    assert.match($("bulkImportPreview").textContent, /check library/); assert.equal($("confirmBulkImport").disabled, true);
  } finally { w.close(); }
});
