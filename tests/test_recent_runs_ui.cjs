"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
const {JSDOM} = require("jsdom");
const root = path.resolve(__dirname, ".."), read = file => fs.readFileSync(path.join(root, "frontend", file), "utf8");
const pause = () => new Promise(resolve => setTimeout(resolve, 25));
async function workspace(t, entries) {
  const dom = new JSDOM(read("index.html"), {url: "http://localhost/?view=quick", runScripts: "outside-only", pretendToBeVisual: true}); t.after(() => dom.window.close());
  const w = dom.window, calls = [];
  w.lucide = {createIcons(){}}; w.HTMLElement.prototype.scrollIntoView = () => {};
  w.fetch = async (url, options = {}) => { calls.push({url, method: options.method || "GET"}); return {ok: true, json: async () => url.includes("history") ? [] : url.includes("strategies") ? {strategies: []} : url.includes("endpoints") ? {endpoints: []} : {defaults: {}, endpoints: {}, strategies: {}}}; };
  w.eval(read("navigation.js")); w.eval(read("stage-renderers.js"));
  w.eval(read("app.js") + '\nwindow.sidebarTest = {seed(entries) {runHistory = entries; strategies = [{id:"alpha",display_name:"Vision and planning baseline"},{id:"beta",display_name:"Candidate planner revision 2"}]; renderHistory();}, active() {return runHistory[activeHistoryIndex]?.id;}, records() {return runHistory;}};'); await pause();
  w.sidebarTest.seed(entries);
  return {w, calls, doc: w.document};
}
function entry(index, overrides = {}) {
  return {id: `${String(index).padStart(8, "0")}-stable-run-id`, task: "Pick the bracket beside the fixture and place it in the designated inspection tray", strategyIds: ["alpha"], timestamp: `2026-09-${String(index + 1).padStart(2, "0")}T10:15:00Z`, status: "completed", results: {alpha: {stages: [], success: null}}, summaryResults: {}, ...overrides};
}

test("recent run rows distinguish repeated tasks using strategies, date, stable identity and execution status", async t => {
  const entries = [entry(0), entry(1, {strategyIds: ["alpha", "beta"], status: "running"}), entry(2, {task: '<img src=x onerror=alert(1)> Inspect the long task without truncating its source text', status: "unexpected", timestamp: "invalid"})];
  const {doc, calls} = await workspace(t, entries);
  const rows = [...doc.querySelectorAll(".history-item")];
  assert.equal(rows.length, 3);
  const latest = rows[0]; assert.equal(latest.querySelector(".history-task").textContent, entries[1].task);
  assert.match(latest.querySelector(".history-strategies").textContent, /Vision and planning baseline · Candidate planner revision 2/);
  assert.match(latest.querySelector("time").textContent, /2026/); assert.equal(latest.querySelector("time").dateTime, "2026-09-02T10:15:00.000Z");
  assert.equal(latest.querySelector(".history-id").textContent, "Run 00000001");
  assert.equal(latest.querySelector(".history-execution").textContent, "Execution: Running");
  assert.match(rows[1].textContent, /Execution: Completed/); assert.doesNotMatch(rows[1].textContent, /Success|Passed/);
  assert.match(rows[2].textContent, /Execution: Unknown/); assert.match(rows[2].textContent, /Time not recorded/);
  assert.equal(doc.querySelectorAll("#historyList img").length, 0);
  assert.ok(doc.querySelector(".history-search svg[aria-hidden=true]"));
  assert.equal(doc.querySelector(".history-open button"), null);
  assert.equal(calls.some(call => call.method !== "GET"), false);
});

test("bounded recent list preserves access to older browser-only runs and opens the original full run", async t => {
  const entries = Array.from({length: 12}, (_, index) => entry(index));
  const {w, doc, calls} = await workspace(t, entries);
  assert.equal(doc.querySelectorAll(".history-item").length, 8);
  assert.match(doc.querySelector(".history-more").textContent, /Show 4 older runs/);
  doc.querySelector(".history-more").click(); assert.equal(doc.querySelectorAll(".history-item").length, 12);
  const oldest = [...doc.querySelectorAll(".history-open")].at(-1); oldest.click();
  assert.equal(w.sidebarTest.active(), entries[0].id); assert.equal(w.sidebarTest.records().length, 12);
  assert.equal(w.location.search, "?view=quick"); assert.ok(doc.getElementById("tab-content-alpha"));
  assert.equal(calls.some(call => call.method !== "GET"), false);
  assert.equal(doc.querySelector('[aria-label="Trial library"] a').getAttribute("href"), "/static/history.html?source=quick");
});

test("search finds existing IDs and strategy display names beyond the first eight rows", async t => {
  const entries = Array.from({length: 12}, (_, index) => entry(index, index === 0 ? {strategyIds: ["beta"]} : {}));
  const {w, doc} = await workspace(t, entries);
  const search = doc.querySelector(".history-filter-input");
  assert.match(search.getAttribute("aria-label"), /task, strategy or ID/);
  search.value = "Candidate planner"; search.dispatchEvent(new w.Event("input", {bubbles: true}));
  assert.equal(doc.querySelectorAll(".history-item").length, 1); assert.match(doc.querySelector(".history-id").textContent, /00000000/);
  search.value = "00000005"; search.dispatchEvent(new w.Event("input", {bubbles: true}));
  assert.equal(doc.querySelectorAll(".history-item").length, 1); assert.match(doc.querySelector(".history-id").textContent, /00000005/);
  search.value = "no-such-run"; search.dispatchEvent(new w.Event("input", {bubbles: true})); assert.match(doc.getElementById("historyItems").textContent, /No matches/);
});
