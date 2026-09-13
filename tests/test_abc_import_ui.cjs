"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), {JSDOM} = require("jsdom");
const {selectAbcEpisodeFiles, validateAbcOptions} = require("../frontend/abc-import.js");
const names = ["episode_metadata.json", "states_actions.bin", "combined_camera-images-rgb.mp4"];
const options = {episode_id: "episode-1", source_revision: "a".repeat(40), frame_index: 0};
const pause = () => new Promise(resolve => setTimeout(resolve, 10));
function setup() {
  const dom = new JSDOM(fs.readFileSync(path.join(__dirname, "../frontend/datasets.html"), "utf8"), {url: "http://localhost", runScripts: "outside-only"});
  const w = dom.window, $ = id => w.document.getElementById(id), calls = [], imported = [];
  w.TextEncoder = TextEncoder;
  for (const script of ["case-import.js", "abc-import.js"]) w.eval(fs.readFileSync(path.join(__dirname, "../frontend", script), "utf8"));
  const files = names.map(name => {
    const file = new w.File(["data"], name);
    Object.defineProperty(file, "webkitRelativePath", {value: `episode-1/${name}`}); return file;
  });
  Object.defineProperty($("abcEpisodeFolder"), "files", {value: files, configurable: true});
  $("caseImportFormat").value = "abc"; $("caseImportFormat").dispatchEvent(new w.Event("change"));
  $("abcEpisodeFolder").dispatchEvent(new w.Event("change"));
  $("abcSourceRevision").value = "a".repeat(40);
  const payload = {payload: {name: "Pick bowl", task: "Put the bowl on the tray", candidate_context: {}}, preview_hash: "bound-preview", thumbnail_data_uri: "data:image/png;base64,YQ==", warnings: ["Demonstration evidence only."]};
  w.fetch = async (url, request) => { calls.push({url, request}); return {ok: true, json: async () => url.endsWith("preview") ? payload : {id: "case-1", name: "Pick bowl"}}; };
  w.addEventListener("rove:case-imported", event => imported.push(event.detail));
  return {w, $, calls, imported, payload, close: () => w.close()};
}

test("ABC folder selection ignores unrelated assets but rejects ambiguous episodes and oversized files", () => {
  const files = names.map(name => ({name, size: 50, webkitRelativePath: `one/${name}`}));
  assert.equal(selectAbcEpisodeFiles([...files, {name: "notes.txt"}]).episodeId, "one");
  assert.throws(() => selectAbcEpisodeFiles(files.slice(1)), /episode_metadata/);
  assert.throws(() => selectAbcEpisodeFiles([...files, files[0]]), /exactly one/);
  assert.throws(() => selectAbcEpisodeFiles([files[0], {...files[1], webkitRelativePath: "two/states_actions.bin"}, files[2]]), /one episode/);
  assert.throws(() => selectAbcEpisodeFiles([{...files[0], size: 256 * 1024 + 1}, ...files.slice(1)]), /256 KiB/);
  assert.throws(() => selectAbcEpisodeFiles([files[0], {...files[1], size: 65 * 1024 * 1024}, files[2]]), /64 MiB/);
  for (const frame_index of [-1, 0.5, NaN, Infinity]) assert.throws(() => validateAbcOptions({...options, frame_index}), /frame index/);
  for (const source_revision of ["", "latest", "main", "HEAD"]) assert.throws(() => validateAbcOptions({...options, source_revision}), /exact dataset/);
});

test("ABC preview uploads only selected files; explicit confirmation publishes one case with bound hash", async () => {
  const h = setup(), {$, calls, imported} = h;
  try {
    assert.equal($("bulkJsonlPanel").hidden, true); assert.equal($("abcImportPanel").hidden, false);
    $("previewAbcImport").click(); await pause();
    assert.equal(calls.length, 1); assert.equal(imported.length, 0);
    assert.equal($("abcPreview").hidden, false); assert.equal($("confirmAbcImport").disabled, false);
    assert.match($("abcPreview").textContent, /does not prove/);
    const form = calls[0].request.body;
    assert.deepEqual([...form.keys()], ["metadata_file", "states_file", "video_file", "options"]);
    assert.deepEqual(JSON.parse(form.get("options")), {...options, camera: "top", split: "unknown", domain: "unknown"});
    $("confirmAbcImport").click(); await pause();
    assert.equal(calls[1].url, "/api/cases/import/abc");
    assert.equal(calls[1].request.body.get("preview_hash"), "bound-preview");
    assert.equal(imported.length, 1); assert.equal($("confirmAbcImport").disabled, true);
    assert.match($("abcImportStatus").textContent, /saved and added/);
  } finally { h.close(); }
});

test("any source change invalidates preview and stale responses cannot re-enable import", async () => {
  const h = setup(), {$, w} = h;
  try {
    $("previewAbcImport").click(); await pause();
    for (const id of ["abcFrameIndex", "abcCamera", "abcSourceRevision", "abcEpisodeId", "abcSplit", "abcDomain"]) {
      $(id).dispatchEvent(new w.Event("input"));
      assert.equal($("confirmAbcImport").disabled, true); assert.equal($("abcPreview").hidden, true);
      $("previewAbcImport").click(); await pause();
    }
    let respond;
    w.fetch = () => new Promise(resolve => { respond = resolve; });
    $("previewAbcImport").click();
    assert.equal($("closeBulkImport").disabled, true); assert.equal($("caseImportFormat").disabled, true);
    assert.equal($("abcEpisodeFolder").disabled, true);
    const dialog = $("bulkCaseDialog"); dialog.setAttribute("open", "");
    dialog.dispatchEvent(new w.Event("cancel", {cancelable: true})); assert.equal(dialog.hasAttribute("open"), true);
    $("abcFrameIndex").value = "1"; $("abcFrameIndex").dispatchEvent(new w.Event("change"));
    respond({ok: true, json: async () => h.payload}); await pause();
    assert.equal($("confirmAbcImport").disabled, true); assert.equal($("abcPreview").hidden, true);
    assert.equal($("closeBulkImport").disabled, false);
  } finally { h.close(); }
});

test("uncertain import can retry exactly the reviewed snapshot without duplicate UI events", async () => {
  const h = setup(), {$, w, imported} = h;
  try {
    $("previewAbcImport").click(); await pause();
    const attempts = [];
    w.fetch = async (_url, request) => {
      attempts.push(request.body);
      if (attempts.length === 1) throw Error("Connection lost");
      return {ok: true, json: async () => ({id: "same-case"})};
    };
    $("confirmAbcImport").click(); await pause();
    assert.equal($("confirmAbcImport").disabled, false); assert.equal(imported.length, 0);
    assert.match($("abcImportStatus").textContent, /retry this same preview safely/);
    $("confirmAbcImport").click(); await pause();
    assert.equal(attempts.length, 2); assert.equal(imported.length, 1);
    assert.equal(attempts[0].get("preview_hash"), attempts[1].get("preview_hash"));
    assert.equal(attempts[0].get("options"), attempts[1].get("options"));
  } finally { h.close(); }
});

test("preview output uses text nodes and format switching clears the authorization", async () => {
  const h = setup(), {$, w, payload} = h;
  try {
    payload.payload.task = '<img src=x onerror="alert(1)">'; payload.warnings = ['<script>bad()</script>'];
    $("previewAbcImport").click(); await pause();
    assert.equal($("abcPreviewTask").querySelector("img"), null);
    assert.equal($("abcPreviewWarnings").querySelector("script"), null);
    $("caseImportFormat").value = "jsonl"; $("caseImportFormat").dispatchEvent(new w.Event("change"));
    assert.equal($("abcImportPanel").hidden, true); assert.equal($("bulkJsonlPanel").hidden, false);
    assert.equal($("confirmAbcImport").disabled, true);
  } finally { h.close(); }
});

test("preview shows detected simulation origin and warns about incomplete exported robot state", async () => {
  const h = setup(), {$, payload} = h;
  try {
    payload.payload.conditions = {source_domain: "sim"};
    payload.payload.candidate_context = {robot_state: {completeness: "unknown_upstream_may_zero_fill_missing_channels"}};
    $("previewAbcImport").click(); await pause();
    assert.equal($("abcDomain").value, "unknown");
    assert.match($("abcPreviewContext").textContent, /Simulation recording/);
    assert.match($("abcPreviewState").textContent, /completeness is unknown/);
    assert.match($("abcPreviewState").textContent, /calibration are not supplied/);
  } finally { h.close(); }
});
