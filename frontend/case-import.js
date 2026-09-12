"use strict";

function parseCaseImport(text, files) {
  if (new TextEncoder().encode(text).length > 1024 * 1024) throw Error("Choose a JSONL file smaller than 1 MiB.");
  const lines = text.split(/\r?\n/).map((text, index) => ({text: text.trim(), line: index + 1})).filter(row => row.text);
  if (!lines.length || lines.length > 100) throw Error("Import between 1 and 100 cases at a time.");
  const images = new Map();
  for (const file of files) {
    if (images.has(file.name)) throw Error(`Two selected images have the name ${file.name}. Use unique filenames.`);
    images.set(file.name, file);
  }
  return lines.map(({text, line}) => {
    let record;
    try { record = JSON.parse(text); } catch { throw Error(`Line ${line}: invalid JSON.`); }
    if (!record || Array.isArray(record) || typeof record !== "object") throw Error(`Line ${line}: expected a case object.`);
    const {image, ...metadata} = record;
    const allowed = ["name", "task", "candidate_context", "conditions", "recorded_evidence", "reference_data"];
    if (Object.keys(metadata).some(key => !allowed.includes(key))) throw Error(`Line ${line}: unrecognized field. Use the example format.`);
    if (typeof metadata.name !== "string" || !metadata.name.trim() || metadata.name.length > 160) throw Error(`Line ${line}: supply a case name (up to 160 characters).`);
    if (typeof metadata.task !== "string" || !metadata.task.trim() || metadata.task.length > 10000) throw Error(`Line ${line}: supply a task (up to 10,000 characters).`);
    for (const key of allowed.slice(2)) if (metadata[key] !== undefined && (!metadata[key] || Array.isArray(metadata[key]) || typeof metadata[key] !== "object")) throw Error(`Line ${line}: ${key} must be an object.`);
    if (new TextEncoder().encode(JSON.stringify(metadata)).length > 256 * 1024) throw Error(`Line ${line}: metadata exceeds 256 KiB.`);
    if (typeof image !== "string" || !images.has(image)) throw Error(`Line ${line}: select the image file ${typeof image === "string" ? image : "named in image"}.`);
    const file = images.get(image);
    if (!/\.(png|jpe?g)$/i.test(file.name) || file.size > 16 * 1024 * 1024) throw Error(`Line ${line}: use a PNG or JPEG image up to 16 MiB.`);
    return {line, metadata, file, status: "ready"};
  });
}
if (typeof module !== "undefined") module.exports = {parseCaseImport};

if (typeof document !== "undefined") (() => {
  const $ = id => document.getElementById(id);
  const dialog = $("bulkCaseDialog");
  if (!dialog) return;
  let rows = [], busy = false, opener;
  const notice = text => { $("bulkImportStatus").textContent = text; };
  const invalidate = () => { rows = []; $("bulkImportPreview").replaceChildren(); $("confirmBulkImport").disabled = true; notice(""); };
  $("showBulkImport").addEventListener("click", () => { opener = document.activeElement; if (dialog.showModal) dialog.showModal(); else dialog.setAttribute("open", ""); $("bulkCaseFile").focus(); });
  const close = () => { if (busy) return; if (dialog.close) dialog.close(); else dialog.removeAttribute("open"); opener?.focus(); };
  $("closeBulkImport").addEventListener("click", close);
  dialog.addEventListener("cancel", event => { event.preventDefault(); close(); });
  $("bulkCaseFile").addEventListener("change", invalidate); $("bulkCaseImages").addEventListener("change", invalidate);
  function render() {
    const list = $("bulkImportPreview"); list.replaceChildren();
    for (const row of rows) { const item = document.createElement("li"); item.textContent = `${row.metadata.name} — ${row.status}${row.error ? `: ${row.error}` : ""}`; list.append(item); }
  }
  $("previewBulkImport").addEventListener("click", async () => {
    const file = $("bulkCaseFile").files[0], images = [...$("bulkCaseImages").files];
    invalidate();
    try {
      if (!file || file.size > 1024 * 1024) throw Error("Select a JSONL file up to 1 MiB.");
      const contents = await file.text();
      if (file !== $("bulkCaseFile").files[0] || images.some((image, index) => image !== $("bulkCaseImages").files[index]) || images.length !== $("bulkCaseImages").files.length) return;
      rows = parseCaseImport(contents, images); render(); $("confirmBulkImport").disabled = false;
      notice(`${rows.length} cases ready. Nothing has been uploaded yet.`);
    } catch (error) { notice(error.message); }
  });
  $("confirmBulkImport").addEventListener("click", async () => {
    if (busy || !rows.length) return;
    busy = true;
    for (const id of ["bulkCaseFile", "bulkCaseImages", "previewBulkImport", "confirmBulkImport", "closeBulkImport"]) $(id).disabled = true;
    const imported = [];
    try {
      for (const row of rows) {
        if (row.status !== "ready") continue;
        row.status = "importing"; render();
        const data = new FormData(); data.append("image", row.file); data.append("metadata", JSON.stringify(row.metadata));
        try {
          const response = await fetch("/api/cases", {method: "POST", body: data});
          const payload = await response.json();
          if (!response.ok) throw Error(typeof payload.detail === "string" ? payload.detail : "The server could not validate this case.");
          row.status = "imported"; imported.push(payload);
          window.dispatchEvent(new CustomEvent("rove:case-imported", {detail: payload}));
        } catch (error) {
          row.status = "check library"; row.error = error.message;
          notice("Import stopped. Earlier successful cases are saved. Check the library before retrying this file, because an interrupted response may still have saved its case.");
          break;
        }
        render();
      }
      render();
      if (rows.every(row => row.status === "imported")) notice(`Imported ${imported.length} cases and added them to your campaign. Close to inspect them.`);
    } finally {
      busy = false;
      for (const id of ["bulkCaseFile", "bulkCaseImages", "previewBulkImport", "closeBulkImport"]) $(id).disabled = false;
      // A retry requires a new explicit preview; never blindly resubmit uncertain imports.
      $("confirmBulkImport").disabled = true;
    }
  });
})();
