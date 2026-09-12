"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
const {JSDOM} = require("jsdom");
async function setup(options = {}) {
  const root = path.resolve(__dirname, "../frontend");
  const dom = new JSDOM(fs.readFileSync(path.join(root, "datasets.html"), "utf8"), {url: "http://localhost/static/datasets.html", runScripts: "outside-only", pretendToBeVisual: true});
  const w = dom.window, calls = [], el = id => w.document.getElementById(id), pause = () => new Promise(resolve => setTimeout(resolve, 20));
  w.HTMLElement.prototype.scrollIntoView = () => {};
  let saved = null;
  w.fetch = async (url, opts = {}) => {
    calls.push({url, method: opts.method || "GET", body: opts.body}); let value;
    if (url === "/api/cases" && opts.method === "POST") {
      const metadata = JSON.parse(opts.body.get("metadata")); if (options.wait) await options.wait;
      if (options.fail) return {ok: false, status: 422, json: async () => ({detail: "Observation could not be decoded as PNG or JPEG."})};
      saved = {id: "saved-case-r1", case_revision_id: "saved-case-r1", case_id: "saved-case", revision: 1, ...metadata, image_asset: {sha256: "a".repeat(64)}}; value = saved;
    } else if (url.startsWith("/api/cases?")) value = {cases: saved ? [saved] : [], total: saved ? 1 : 0};
    else if (url === "/api/cases/saved-case-r1") value = saved;
    else if (url === "/api/assistant/status") value = {available: false};
    else if (url === "/api/success-contracts") value = {contracts: []};
    else if (url.startsWith("/api/datasets?")) value = {datasets: []};
    else if (url === "/api/strategies") value = {strategies: []};
    else if (url === "/api/campaigns") value = [];
    else if (url === "/api/baselines") value = {baselines: []};
    else if (url.startsWith("/api/reviews?")) value = {reviews: []};
    else if (url.startsWith("/api/trials?")) value = {trials: []};
    else throw Error(`Unexpected ${url}`);
    return {ok: true, status: 200, json: async () => value};
  };
  w.eval(fs.readFileSync(path.join(root, "navigation.js"), "utf8")); w.eval(fs.readFileSync(path.join(root, "datasets.js"), "utf8")); await pause();
  el("showImport").click(); el("caseName").value = "Customer scene"; el("caseTask").value = "Place red part into bin"; el("newCaseExpectation").value = "Identify the red part accurately.";
  Object.defineProperty(el("caseImage"), "files", {value: [new w.File(["png"], "scene.png", {type: "image/png"})]});
  const submit = () => el("caseForm").dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true}));
  return {w, el, calls, pause, submit};
}

test("single-case save locks editing and dismissal, captures expectation once and prevents duplicate submission", async () => {
  let release; const wait = new Promise(resolve => { release = resolve; });
  const {w, el, calls, pause, submit} = await setup({wait});
  try {
    submit(); await pause();
    assert.equal(el("caseName").disabled, true); assert.equal(el("newCaseExpectation").disabled, true); assert.equal(el("closeNewCase").disabled, true);
    assert.match(el("caseSaveStatus").textContent, /Saving this case/);
    el("newCaseDialog").dispatchEvent(new w.Event("cancel", {cancelable: true})); assert.equal(el("newCaseDialog").open, true);
    submit(); assert.equal(calls.filter(call => call.method === "POST").length, 1);
    el("newCaseExpectation").value = "A later programmatic change must not attach to the saved case.";
    release(); await pause();
    assert.equal(el("newCaseDialog").open, false); assert.equal(el("caseName").disabled, false); assert.equal(el("closeNewCase").disabled, false);
    assert.equal(el("attachRecording").disabled, true, "new form reset must keep an unuploaded recording unavailable");
    assert.equal(el("selectedCases").querySelector("textarea").value, "Identify the red part accurately.");
    assert.match(el("selectedCases").textContent, /Customer scene.*Place red part into bin/);
    assert.equal(calls.filter(call => call.method === "POST").length, 1);
  } finally { release(); await pause(); w.close(); }
});

test("API validation failure is visible inside the open case dialog and restores prior control states", async () => {
  const {w, el, pause, submit} = await setup({fail: true});
  try {
    el("caseConditions").disabled = true; submit(); await pause();
    assert.equal(el("newCaseDialog").open, true); assert.match(el("caseSaveStatus").textContent, /could not be decoded/);
    assert.ok(el("newCaseDialog").contains(el("caseSaveStatus")));
    assert.equal(el("caseName").value, "Customer scene"); assert.equal(el("newCaseExpectation").value, "Identify the red part accurately.");
    assert.equal(el("caseName").disabled, false); assert.equal(el("caseConditions").disabled, true); assert.equal(el("saveCase").disabled, false);
    el("closeNewCase").click(); assert.equal(el("newCaseDialog").open, false);
  } finally { w.close(); }
});
