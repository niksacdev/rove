"use strict";

function selectAbcEpisodeFiles(files) {
  const names = {metadata_file: "episode_metadata.json", states_file: "states_actions.bin", video_file: "combined_camera-images-rgb.mp4"};
  const selected = {};
  for (const [key, name] of Object.entries(names)) {
    const matches = [...files].filter(file => file.name === name);
    if (matches.length !== 1) throw Error(`Select one episode folder containing exactly one ${name}.`);
    const file = matches[0], limit = key === "metadata_file" ? 256 * 1024 : 64 * 1024 * 1024;
    if (!file.size || file.size > limit) throw Error(`${name} must be nonempty and no larger than ${key === "metadata_file" ? "256 KiB" : "64 MiB"}. Export a smaller episode to stay within this limit.`);
    selected[key] = file;
  }
  const parents = Object.values(selected).map(file => (file.webkitRelativePath || file.name).split("/").slice(0, -1).join("/"));
  if (new Set(parents).size !== 1) throw Error("Select files from one episode, not several episode folders.");
  return {files: selected, episodeId: parents[0].split("/").pop() || ""};
}

function validateAbcOptions(options) {
  if (!options.episode_id.trim()) throw Error("Enter an episode ID in Observation and source details.");
  if (!/^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$/.test(options.source_revision.trim())) throw Error("Enter the exact dataset commit or export checksum (40 or 64 hexadecimal characters).");
  if (!Number.isSafeInteger(options.frame_index) || options.frame_index < 0) throw Error("Choose a whole-number frame index, starting at 0.");
  return options;
}

if (typeof module !== "undefined") module.exports = {selectAbcEpisodeFiles, validateAbcOptions};

if (typeof document !== "undefined") (() => {
  const $ = id => document.getElementById(id), panel = $("abcImportPanel");
  if (!panel) return;
  let revision = 0, preview = null, busy = false;
  const notice = text => { $("abcImportStatus").textContent = text; };
  function invalidate() {
    revision += 1; preview = null; $("abcPreview").hidden = true;
    $("abcPreviewImage").removeAttribute("src"); $("confirmAbcImport").disabled = true; notice("");
  }
  function setBusy(value) {
    busy = value; $("bulkCaseDialog").dataset.importBusy = String(value);
    for (const control of panel.querySelectorAll("input, select, button")) control.disabled = value;
    $("closeBulkImport").disabled = value; $("caseImportFormat").disabled = value;
    $("confirmAbcImport").disabled = value || !preview;
  }
  function capture() {
    const selection = selectAbcEpisodeFiles($("abcEpisodeFolder").files);
    const rawFrame = $("abcFrameIndex").value;
    return {files: selection.files, options: validateAbcOptions({
      episode_id: $("abcEpisodeId").value.trim(), source_revision: $("abcSourceRevision").value.trim(),
      frame_index: rawFrame.trim() ? Number(rawFrame) : NaN, camera: $("abcCamera").value,
      split: $("abcSplit").value, domain: $("abcDomain").value,
    })};
  }
  function formData(snapshot, hash) {
    const form = new FormData();
    for (const [key, file] of Object.entries(snapshot.files)) form.append(key, file, file.name);
    form.append("options", JSON.stringify(snapshot.options));
    if (hash) form.append("preview_hash", hash);
    return form;
  }
  async function request(url, body) {
    const response = await fetch(url, {method: "POST", body});
    let result;
    try { result = await response.json(); } catch { throw Error("The server response was interrupted. Try again."); }
    if (!response.ok) throw Error(typeof result.detail === "string" ? result.detail : "The server could not validate this episode. Check the selected files and source details.");
    return result;
  }
  for (const input of panel.querySelectorAll("input, select")) input.addEventListener("input", invalidate);
  for (const input of panel.querySelectorAll("input, select")) input.addEventListener("change", invalidate);
  $("caseImportFormat").addEventListener("change", invalidate);
  $("abcEpisodeFolder").addEventListener("change", () => {
    $("abcSelectedFiles").textContent = ""; $("abcEpisodeId").value = "";
    try {
      const selected = selectAbcEpisodeFiles($("abcEpisodeFolder").files);
      $("abcEpisodeId").value = selected.episodeId;
      $("abcSelectedFiles").textContent = `${selected.episodeId || "Episode"} · Metadata, robot states and camera video ready.`;
    } catch (error) { notice(error.message); }
  });
  $("previewAbcImport").addEventListener("click", async () => {
    if (busy) return;
    invalidate(); const generation = revision;
    try {
      const snapshot = capture(); setBusy(true); notice("Preparing observation preview…");
      const result = await request("/api/cases/import/abc/preview", formData(snapshot));
      if (generation !== revision) return;
      if (!result.preview_hash || !result.payload || !/^data:image\/(png|jpeg);base64,/.test(result.thumbnail_data_uri || "")) throw Error("The server returned an incomplete preview. Try again.");
      preview = {snapshot, hash: result.preview_hash};
      $("abcPreviewImage").src = result.thumbnail_data_uri;
      $("abcPreviewName").textContent = result.payload.name;
      $("abcPreviewTask").textContent = result.payload.task;
      const sourceDomain = result.payload.conditions?.source_domain || "unknown";
      const state = result.payload.candidate_context?.robot_state;
      $("abcPreviewState").textContent = state ? "Robot state included: two 6-joint arms and two grippers. Channel completeness is unknown; upstream exports may fill missing values with zeros. Robot model and calibration are not supplied." : "No robot state is supplied in this preview.";
      $("abcPreviewContext").textContent = `Frame ${snapshot.options.frame_index} · ${snapshot.options.camera} camera · ${sourceDomain === "unknown" ? "Environment unspecified" : sourceDomain === "real" ? "Real robot recording" : "Simulation recording"} · ${snapshot.options.split === "unknown" ? "Split unspecified" : snapshot.options.split === "train" ? "Training split" : "Validation split"}`;
      $("abcPreviewWarnings").replaceChildren();
      for (const warning of result.warnings || []) { const item = document.createElement("li"); item.textContent = warning; $("abcPreviewWarnings").append(item); }
      $("abcPreview").hidden = false; notice("Preview ready. No case has been saved yet.");
    } catch (error) { if (generation === revision) notice(error.message); }
    finally { setBusy(false); }
  });
  $("confirmAbcImport").addEventListener("click", async () => {
    if (busy || !preview) return;
    const approved = preview, generation = revision;
    setBusy(true); notice("Adding case…");
    try {
      const result = await request("/api/cases/import/abc", formData(approved.snapshot, approved.hash));
      if (!result.id) throw Error("The server returned no case ID. Retry to recover the saved case.");
      window.dispatchEvent(new CustomEvent("rove:case-imported", {detail: result}));
      if (generation === revision) { preview = null; notice("Case saved and added to your campaign. Close to compare strategies."); }
    } catch (error) {
      if (generation === revision) notice(`${error.message} You can retry this same preview safely; it will reuse the case if already saved.`);
    } finally { setBusy(false); }
  });
})();
