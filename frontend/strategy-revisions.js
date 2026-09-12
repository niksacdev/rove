"use strict";

// Revision editing prepares configuration only; campaign execution remains explicit.
(() => {
  const $ = id => document.getElementById(id);
  const node = (tag, text, attrs = {}) => { const el = document.createElement(tag); if (text != null) el.textContent = text; for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value); return el; };
  const copy = value => JSON.parse(JSON.stringify(value));
  const stageNames = ["perceive", "plan", "act", "verify", "sim"];
  const dialog = node("dialog", null, {id: "strategyRevisionDialog", "aria-labelledby": "strategyRevisionHeading"});
  const header = node("div", null, {class: "revision-heading"});
  header.append(node("h2", "Create a strategy revision", {id: "strategyRevisionHeading"}));
  const close = node("button", "Close", {type: "button", class: "secondary", id: "closeStrategyRevision"}); header.append(close); dialog.append(header);
  dialog.append(node("p", "Start from a current strategy, change its components, and save a new reusable revision. This does not change the original strategy or execute any trials.", {class: "muted"}));
  dialog.append(node("p", "The source is today's configuration, not the archived baseline. Preview the campaign comparison afterward to check all differences from the saved baseline.", {class: "notice", id: "strategyRevisionSourceNotice"}));
  const form = node("form", null, {id: "strategyRevisionForm"}); const fields = node("fieldset", null, {class: "revision-fields", id: "strategyRevisionFields"});
  const addControl = (title, control) => { const label = node("label", title); label.append(control); fields.append(label); return control; };
  const source = addControl("Source strategy", node("select", null, {id: "revisionParent", required: ""}));
  const name = addControl("New strategy name", node("input", null, {id: "revisionName", maxlength: "160", required: ""}));
  const stages = {};
  for (const stage of stageNames) stages[stage] = addControl(`${stage === "sim" ? "Simulation" : stage[0].toUpperCase() + stage.slice(1)} endpoint`, node("select", null, {id: `revisionStage_${stage}`}));
  const mode = addControl("Pipeline execution", node("select", null, {id: "revisionPipelineMode"}));
  for (const value of ["sequential", "parallel"]) mode.append(node("option", value, {value}));
  const verify = addControl("Verification mode", node("select", null, {id: "revisionVerifyMode"}));
  for (const value of ["auto", "precompute", "agent_loop"]) verify.append(node("option", value.replaceAll("_", " "), {value}));
  const advanced = node("details", null, {class: "revision-advanced"}); advanced.append(node("summary", "Advanced: complete strategy settings"));
  const advancedLabel = node("label", "Edit complete definition instead of the controls above ");
  const useJson = node("input", null, {type: "checkbox", id: "revisionUseJson"}); advancedLabel.prepend(useJson); advanced.append(advancedLabel);
  const json = node("textarea", null, {id: "revisionJson", rows: "10", spellcheck: "false", "aria-label": "Complete strategy definition JSON", disabled: ""});
  advanced.append(node("p", "Preserves stage timeouts, required checks, tags and other existing strategy settings. Endpoint credentials remain in Settings.", {class: "muted"}), json); fields.append(advanced); form.append(fields);
  const status = node("p", "Choose a source strategy.", {id: "strategyRevisionStatus", role: "status", "aria-live": "polite"});
  const differences = node("div", null, {id: "strategyRevisionDiff"});
  const actions = node("div", null, {class: "form-actions"});
  const previewButton = node("button", "Preview changes", {id: "previewStrategyRevision", type: "button"});
  const saveButton = node("button", "Save strategy revision", {id: "saveStrategyRevision", type: "submit", disabled: ""}); actions.append(previewButton, saveButton);
  form.append(status, differences, actions); dialog.append(form); document.body.append(dialog);
  const state = {parent: null, preview: null, version: 0, candidate: false, opener: null, saving: false, saved: false};
  async function request(url, body) { const response = await fetch(url, body ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {}); const result = await response.json(); if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : `Unable to save configuration (HTTP ${response.status}).`); return result; }
  const invalidate = () => { state.preview = null; state.saved = false; saveButton.disabled = true; differences.replaceChildren(); };
  function draft() {
    if (!state.parent) throw new Error("Load a source strategy first.");
    let definition;
    if (useJson.checked) { try { definition = JSON.parse(json.value); } catch { throw new Error("Complete strategy settings must be valid JSON."); } if (!definition || Array.isArray(definition) || typeof definition !== "object") throw new Error("Strategy settings must be a JSON object."); }
    else {
      definition = copy(state.parent.definition); definition.display_name = name.value.trim();
      if (!definition.display_name) throw new Error("Name the new strategy revision.");
      for (const stage of stageNames) { const selected = stages[stage].value; definition[stage] = !selected ? null : typeof definition[stage] === "object" && definition[stage] ? {...definition[stage], endpoint: selected} : selected; }
      definition.pipeline_mode = mode.value; definition.verify_mode = verify.value;
      if (typeof definition.verify === "object" && definition.verify) definition.verify.mode = verify.value;
    }
    return {parent_id: state.parent.strategy_id, expected_parent_fingerprint: state.parent.fingerprint, definition};
  }
  function setSimpleEnabled() { for (const control of [name, mode, verify, ...Object.values(stages)]) control.disabled = useJson.checked; json.disabled = !useJson.checked; }
  async function loadParent() {
    const version = ++state.version; invalidate(); state.parent = null; fields.disabled = true; previewButton.disabled = true; status.textContent = "Loading current strategy settings…";
    try {
      const parent = await request(`/api/strategy-revisions/parents/${encodeURIComponent(source.value)}`); if (version !== state.version) return;
      state.parent = parent; name.value = `${parent.definition.display_name || parent.strategy_id} revision`.slice(0, 160); useJson.checked = false;
      for (const stage of stageNames) {
        const selected = typeof parent.definition[stage] === "string" ? parent.definition[stage] : parent.definition[stage]?.endpoint || "";
        stages[stage].replaceChildren(); if (stage !== "verify") stages[stage].append(node("option", "Not used", {value: ""}));
        const compatible = parent.endpoints.filter(endpoint => endpoint.stages?.includes(stage));
        for (const endpoint of compatible) stages[stage].append(node("option", `${endpoint.display_name || endpoint.id} · ${endpoint.id}`, {value: endpoint.id}));
        if (selected && !compatible.some(endpoint => endpoint.id === selected)) stages[stage].append(node("option", `${selected} · unavailable for this stage`, {value: selected, disabled: ""}));
        stages[stage].value = selected;
      }
      mode.value = parent.definition.pipeline_mode || "sequential"; verify.value = parent.definition.verify_mode || "auto";
      if (parent.definition.verify?.mode && parent.definition.verify.mode !== "auto") verify.value = parent.definition.verify.mode;
      setSimpleEnabled(); json.value = JSON.stringify(draft().definition, null, 2);
      status.textContent = `Source: ${parent.strategy_id}. The original strategy stays unchanged. Preview your changes before saving.`;
    } catch (error) { if (version === state.version) status.textContent = error.message; }
    finally { if (version === state.version) { fields.disabled = false; previewButton.disabled = !state.parent; } }
  }
  source.addEventListener("change", loadParent);
  fields.addEventListener("input", event => { if (event.target !== source) { ++state.version; invalidate(); } });
  fields.addEventListener("change", event => { if (event.target !== source && event.target !== useJson) { ++state.version; invalidate(); } });
  useJson.addEventListener("change", () => { if (useJson.checked) { useJson.checked = false; try { json.value = JSON.stringify(draft().definition, null, 2); } finally { useJson.checked = true; } } setSimpleEnabled(); ++state.version; invalidate(); });
  previewButton.addEventListener("click", async () => {
    const version = ++state.version; invalidate(); previewButton.disabled = true; status.textContent = "Checking the proposed revision…";
    try {
      const payload = draft(), signature = JSON.stringify(payload); const preview = await request("/api/strategy-revisions/preview", payload);
      if (version !== state.version || signature !== JSON.stringify(draft())) return;
      state.preview = {payload, signature, hash: preview.preview_hash, operation: crypto.randomUUID()};
      differences.append(node("p", `New strategy ID: ${preview.strategy_id}`, {class: "record-id"}));
      for (const item of preview.differences || []) { const row = node("div", null, {class: "revision-difference"}); row.append(node("strong", item.path), node("pre", JSON.stringify(item.before ?? null, null, 2)), node("span", "→"), node("pre", JSON.stringify(item.after ?? null, null, 2))); differences.append(row); }
      saveButton.disabled = !preview.preview_hash; status.textContent = "Review these exact changes. Saving adds a strategy revision; it does not run the pipeline.";
    } catch (error) { if (version === state.version) status.textContent = error.message; }
    finally { if (!state.saving) previewButton.disabled = !state.parent; }
  });
  form.addEventListener("submit", async event => {
    event.preventDefault(); if (state.saving || state.saved) return;
    try {
      const preview = state.preview; if (!preview || preview.signature !== JSON.stringify(draft())) throw new Error("Preview the current changes before saving.");
      state.saving = true; fields.disabled = true; previewButton.disabled = true; saveButton.disabled = true; close.disabled = true;
      const saved = await request("/api/strategy-revisions", {...preview.payload, expected_preview_hash: preview.hash, operation_id: preview.operation});
      state.saved = true; state.preview = null; const identity = saved.strategy_id;
      status.textContent = `Strategy revision ${identity} saved. Updating available strategies…`;
      try { await window.refreshStrategyCatalog(identity, state.candidate); } catch (error) { status.textContent = `Revision ${identity} was saved, but the list could not refresh: ${error.message}. Close and refresh the page to select it. Do not save it again.`; return; }
      const messageId = state.candidate ? "candidateRevisionMessage" : "campaignRevisionMessage";
      $(messageId).textContent = `Saved ${saved.definition?.display_name || name.value} (${identity}). ${state.candidate ? "Selected as the candidate; preview the comparison before running." : "Added to the selected strategies; review the campaign before running."}`;
      state.saving = false; dismiss();
    } catch (error) { status.textContent = error.message; }
    finally { state.saving = false; fields.disabled = false; previewButton.disabled = state.saved; saveButton.disabled = state.saved || !state.preview; close.disabled = false; }
  });
  function dismiss() { if (state.saving) return; ++state.version; if (typeof dialog.close === "function") dialog.close(); else dialog.removeAttribute("open"); state.opener?.focus(); }
  close.addEventListener("click", dismiss); dialog.addEventListener("cancel", event => { event.preventDefault(); dismiss(); });
  async function open(candidate, opener) {
    state.candidate = candidate; state.opener = opener; state.saved = false; state.parent = null; invalidate(); const version = ++state.version;
    source.replaceChildren(); fields.disabled = true; previewButton.disabled = true; status.textContent = "Loading strategies…";
    if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", ""); close.focus();
    try {
      const catalog = await request("/api/strategies"); if (version !== state.version) return;
      for (const item of catalog.strategies || []) source.append(node("option", item.display_name || item.id, {value: item.id}));
      const preferred = candidate ? $("baselineStrategy")?.value : $("campaignStrategy")?.value;
      if ([...source.options].some(option => option.value === preferred)) source.value = preferred;
      if (!source.value) throw new Error("No source strategies are available. Add a strategy in Settings first.");
      await loadParent(); if (dialog.open) name.focus();
    } catch (error) { if (version === state.version) status.textContent = error.message; }
  }
  $("createCampaignStrategyRevision")?.addEventListener("click", event => open(false, event.currentTarget));
  $("createCandidateStrategyRevision")?.addEventListener("click", event => open(true, event.currentTarget));
})();
