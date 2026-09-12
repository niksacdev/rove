"use strict";

function parseObject(text, label) {
  if (!text.trim()) return {};
  let value;
  try { value = JSON.parse(text); } catch { throw new Error(`${label} must be valid JSON.`); }
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} must be a JSON object.`);
  return value;
}

function buildCaseMetadata(input) {
  return {name: input.name.trim(), task: input.task.trim(), candidate_context: parseObject(input.context || "", "Candidate context"), conditions: parseObject(input.conditions || "", "Conditions"), recorded_evidence: parseObject(input.episode || "", "Recorded evidence"), reference_data: parseObject(input.references || "", "Reference annotations")};
}

function buildRecordingReference(input, asset) {
  if (!asset?.sha256) throw new Error("Upload the evidence file first.");
  if (!/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(input.id)) throw new Error("Use a unique reference ID with letters, numbers, dots, colons or dashes.");
  const units = parseObject(input.units || "", "Recording units");
  if (Object.values(units).some(value => typeof value !== "string" || !value.trim())) throw new Error("Each recording unit must be an explicit unit name.");
  const reference = {id: input.id, asset_sha256: asset.sha256, kind: input.kind, units};
  if (input.clock?.trim()) reference.clock_id = input.clock.trim();
  if (input.frame?.trim()) reference.frame = input.frame.trim();
  if (input.range) {
    const start = Number(input.start), end = Number(input.end);
    if (String(input.start).trim() === "" || String(input.end).trim() === "" || !Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start) throw new Error("Enter a nonempty range with a start before its end.");
    if (input.range !== "second" && (!Number.isInteger(start) || !Number.isInteger(end))) throw new Error("Bytes, samples and frames require integer offsets.");
    if (input.range === "byte" && end > asset.size_bytes) throw new Error("Byte range exceeds the uploaded file.");
    if (input.range !== "byte" && asset.media_type !== "application/json") throw new Error("Sample, frame and time ranges require an indexed JSON recording.");
    if (input.range === "second" && !reference.clock_id) throw new Error("Time ranges require an explicit source clock ID.");
    reference.selector = {unit: input.range, start, end};
  }
  return reference;
}

function caseSourceLabels(item) {
  const conditions = item.conditions || {};
  const labels = [];
  if (conditions.library === "bundled-gallery") labels.push("Bundled sample");
  const source = typeof conditions.source === "string" ? conditions.source : conditions.source?.dataset;
  if (typeof source === "string" && source.trim()) labels.push(source);
  if (typeof conditions.eval_category === "string") labels.push(conditions.eval_category.replace(/_/g, " "));
  return labels;
}

function buildContract(input) {
  if (input.json?.trim()) return parseObject(input.json, "Success contract");
  const criterion = {id: "task_acceptance", description: input.criteria.trim(), assessment: input.assessment, required: true};
  if (input.assessment === "configured_verifier") {
    if (!input.endpoint?.trim()) throw new Error("Choose the configured verifier endpoint for this criterion.");
    criterion.endpoint = input.endpoint.trim();
  }
  if (!criterion.description) throw new Error("Describe the acceptance criterion before saving.");
  const contract = {name: input.name.trim(), scope: input.scope, evidence_mode: input.evidenceMode, criteria: [criterion], metrics: ["task_success", "pass_at_k", "pass_pow_k", "pipeline_latency"]};
  if (input.targetMetric) {
    const threshold = Number(input.targetThreshold), unit = input.targetMetric === "pipeline_latency" ? "ms" : input.targetMetric === "episode_completion_time" ? "s" : "fraction";
    if (String(input.targetThreshold).trim() === "" || !Number.isFinite(threshold) || threshold < 0 || (unit === "fraction" && threshold > 1)) throw new Error("Enter a valid target threshold; fractions range from 0 to 1.");
    contract.campaign_targets = [{metric: input.targetMetric, operator: input.targetOperator, threshold, unit}];
    if (!contract.metrics.includes(input.targetMetric)) contract.metrics.push(input.targetMetric);
  }
  if (input.annotationEndpoint || input.annotationKey || input.annotationTarget) {
    if (![input.annotationEndpoint, input.annotationKey, input.annotationTarget].every(value => value?.trim())) throw new Error("An annotation binding needs the verifier endpoint, reviewed field and reference field.");
    contract.annotation_bindings = [{endpoint: input.annotationEndpoint.trim(), annotation_key: input.annotationKey.trim(), target_key: input.annotationTarget.trim()}];
  }
  return contract;
}

function buildReview(input) {
  if (!input.contractId) throw new Error("Choose the success contract used for this review.");
  if (!input.reviewer.trim()) throw new Error("Enter a reviewer name for attribution.");
  if (input.target === "trial_output" && !input.trialId) throw new Error("Choose the specific trial output to rate.");
  if (input.status === "final" && !input.rationale.trim()) throw new Error("Explain the final review so another person can understand it.");
  const annotationText = (input.annotations || "").trim();
  const annotations = input.target === "case_annotation" ? (annotationText.startsWith("{") ? parseObject(annotationText, "Annotations") : annotationText ? {expectations: annotationText} : {}) : {};
  if (input.target === "case_annotation" && input.decision === "accepted" && !Object.keys(annotations).length) throw new Error("Accepted reusable annotations must contain explicit labels or expectations.");
  return {target_type: input.target, case_revision_id: input.caseId, trial_id: input.target === "trial_output" ? input.trialId : null, contract_id: input.contractId, reviewer: input.reviewer.trim(), status: input.status, decision: input.decision, criteria: input.criteria || {}, annotations, rationale: input.rationale.trim(), evidence_refs: (input.evidence || "").split("\n").map(value => value.trim()).filter(Boolean), supersedes_id: input.supersedes || null};
}

function buildCampaign(input) {
  if (!input.caseIds.length && !input.datasetId) throw new Error("Select at least one case or a saved dataset.");
  if (!input.contractId || !input.strategyId) throw new Error("Choose scoring rules and strategy.");
  const repeats = Number(input.repeats), timeout = Number(input.timeout);
  if (!Number.isInteger(repeats) || repeats < 1 || repeats > 1000) throw new Error("Repeats must be an integer from 1 to 1000.");
  if (!Number.isFinite(timeout) || timeout < 1 || timeout > 3600) throw new Error("Attempt timeout must be between 1 and 3600 seconds.");
  return {name: input.name.trim(), ...(input.datasetId ? {dataset_revision_id: input.datasetId} : {case_revision_ids: [...input.caseIds]}), contract_id: input.contractId, strategies: [input.strategyId], seeds: Array.from({length: repeats}, (_, index) => index), ks: [...new Set([1, Math.min(3, repeats), repeats])].sort((a, b) => a - b), timeout_s: timeout};
}

function metricLabel(name) {
  return {task_success: "Task acceptance", pass_at_k: "At least one success · pass@k", pass_pow_k: "Repeatability · pass^k", pipeline_latency: "Pipeline latency", constraint_outcomes: "Required constraints", episode_completion_time: "Episode completion time", autonomous_completion: "Autonomous completion", recovery: "Recovery"}[name] || name;
}

function activeReviewIds(reviews) {
  const superseded = new Set(reviews.filter(review => review.status === "final").map(review => review.supersedes_id).filter(Boolean));
  return reviews.filter(review => review.status === "final" && !superseded.has(review.id)).map(review => review.id);
}

function mergeDatasetMembers(parent, additions, cases, contractId) {
  if (parent && parent.contract_id !== contractId) throw new Error("This dataset uses a different success definition. Select it again to inherit its definition, or save a new dataset.");
  const identities = new Map(cases.map(item => [item.case_revision_id || item.id, item.case_id]));
  const members = new Map(), revisions = new Map();
  for (const member of parent?.members || []) {
    const identity = identities.get(member.case_revision_id);
    if (!identity) throw new Error("A saved case could not be loaded. The dataset has not been changed.");
    revisions.set(identity, member.case_revision_id);
    members.set(member.case_revision_id, {case_revision_id: member.case_revision_id, disposition: member.disposition, reason: member.reason || "", review_ids: [...(member.review_ids || [])]});
  }
  for (const member of additions) {
    const identity = identities.get(member.case_revision_id);
    if (!identity) throw new Error("A selected case could not be loaded. Preview the selection again.");
    if (revisions.has(identity) && revisions.get(identity) !== member.case_revision_id) throw new Error("This dataset already contains another revision of a selected case. Remove that case from your selection to retain the saved revision, or save a separate dataset for the revised case.");
    if (!members.has(member.case_revision_id)) members.set(member.case_revision_id, {...member, review_ids: [...member.review_ids]});
    revisions.set(identity, member.case_revision_id);
  }
  return [...members.values()];
}

function assessedCounts(summary) {
  return (summary?.tasks || []).reduce((counts, task) => ({passed: counts.passed + task.passed, failed: counts.failed + task.failed, unknown: counts.unknown + task.unknown}), {passed: 0, failed: 0, unknown: 0});
}

function metricValue(point) {
  const percent = value => `${(value * 100).toFixed(1)}%`;
  if (point.value != null) return percent(point.value);
  if (point.lower != null && point.upper != null) return `${percent(point.lower)}–${percent(point.upper)} unresolved range`;
  return "Unavailable";
}

function pairedTrialId(comparison, item, attempt, side) {
  if (attempt[`${side}_trial_id`]) return attempt[`${side}_trial_id`];
  const strategyId = comparison[side]?.strategies?.[0]?.strategy_id;
  return (comparison[`${side}_trials`] || []).find(trial => trial.task_id === item[`${side}_task_id`] && trial.seed === attempt.seed && (!strategyId || trial.strategy_id === strategyId))?.trial_id;
}

function evidenceHref(value) {
  // Assistant evidence is local navigation, never executable or a remote fetch.
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//") || value.includes("\\") || /[\u0000-\u001f]/.test(value)) return null;
  return value;
}

if (typeof module !== "undefined" && module.exports) module.exports = {buildRecordingReference, parseObject, buildCaseMetadata, caseSourceLabels, buildContract, buildReview, buildCampaign, metricLabel, activeReviewIds, mergeDatasetMembers, assessedCounts, metricValue, pairedTrialId, evidenceHref};

if (typeof document !== "undefined") (() => {
  const $ = id => document.getElementById(id);
  const state = {cases: [], selected: new Map(), contracts: [], datasets: [], caseId: null, detail: null, reviews: [], trials: [], offset: 0, limit: 12, listVersion: 0, detailVersion: 0, contractId: "", frozenDataset: null, campaignPreview: null, campaignOperation: null, freezePreview: null, freezeMembers: null, reviewEdit: null};
  Object.assign(state, {campaigns: [], campaignId: null, campaignVersion: 0, ablationPreview: null, ablationOperation: null, caseEdit: null, namedBaselines: [], namedBaseline: null, baselineOperation: null, assistantAssets: [], recordingAsset: null});
  const node = (tag, text, className) => { const element = document.createElement(tag); if (text != null) element.textContent = String(text); if (className) element.className = className; return element; };
  const objectDetails = (title, data) => { const element = node("details"); element.append(node("summary", title), node("pre", JSON.stringify(data ?? null, null, 2))); return element; };
  const link = (text, href) => { const element = node("a", text); element.href = href; return element; };
  const date = value => value && Number.isFinite(new Date(value).getTime()) ? new Date(value).toLocaleString() : "Time not recorded";
  const caseId = item => item.case_revision_id || item.id;
  const select = (id, options, value) => { const element = node("select"); if (id) element.id = id; for (const [key, text] of options) { const option = node("option", text); option.value = key; element.append(option); } if (value != null) element.value = value; return element; };
  const label = (text, control) => { const element = node("label", text); element.append(control); return element; };
  const button = (text, action, style = "secondary") => { const element = node("button", text, style); element.type = "button"; element.addEventListener("click", action); return element; };
  state.step = "cases";
  Object.assign(state, {expectations: new Map(), pickerSelected: null, strategies: [], search: "", category: "", liveVersion: 0});
  const stepCopy = {cases: ["What should your agent be able to do?", "Add an observation and task, or choose from your existing case library. Each selected case stays in this campaign."], configure: ["Choose the strategy. Define a good outcome.", "Use your existing pipeline configuration and confirm how its outputs will be assessed."], run: ["Run your campaign", "One case × one strategy × one repetition = one trial. Follow each trial, then inspect its evidence."], review: ["What worked, and what needs to improve?", "Review trial outputs against your criteria, then compare a changed strategy on the same cases."]};
  function writeRoute() {
    const url = new URL(location.href); url.searchParams.set("step", state.step);
    for (const [key, value] of [["campaign", state.campaignId], ["case", state.caseId]]) { if (value) url.searchParams.set(key, value); else url.searchParams.delete(key); }
    if (url.href !== location.href) history.pushState({}, "", url);
  }
  function goStep(step, push = true, focus = true) {
    if (state.step !== step) tell("");
    state.step = Object.hasOwn(stepCopy, step) ? step : "cases";
    $("workspaceTitle").textContent = state.step === "review" ? "Review results" : state.step === "run" && state.campaignId && !state.preparingRun ? "Campaign trials" : "Create a campaign";
    document.title = state.step === "review" ? "ROVE · Results · Review" : "ROVE · Evaluate";
    window.RoveNavigation?.setActive(state.step === "review" ? "results" : "evaluate");
    for (const panel of document.querySelectorAll("[data-workflow-panel]")) panel.hidden = panel.dataset.workflowPanel !== state.step;
    for (const anchor of document.querySelectorAll("[data-journey-step]")) {
      if (anchor.dataset.journeyStep === state.step) anchor.setAttribute("aria-current", "step"); else anchor.removeAttribute("aria-current");
      const url = new URL(location.href); url.searchParams.set("step", anchor.dataset.journeyStep); url.searchParams.delete("pick"); anchor.href = url.pathname + url.search;
    }
    const slot = $(state.step === "review" ? "reviewWorkspaceSlot" : "casesWorkspaceSlot");
    if ($("caseWorkspace").parentElement !== slot) slot.append($("caseWorkspace"));
    $("stepHeading").textContent = stepCopy[state.step][0]; $("stepDescription").textContent = stepCopy[state.step][1];
    if (state.step === "configure") prepareCriteriaDraft();
    if (state.step === "run" && state.campaignId && !state.preparingRun) refreshLiveTrials();
    if (state.step === "review" && state.campaignId) openCampaign(state.campaignId, false);
    if (push) writeRoute(); if (focus) $("stepHeading").focus();
  }
  async function restoreRoute() {
    const query = new URLSearchParams(location.search), campaign = query.get("campaign"), id = query.get("case");
    goStep(query.get("step") || (campaign ? "review" : "cases"), false, false);
    if (campaign !== state.campaignId) { $("resultCampaign").value = campaign || ""; await openCampaign(campaign, false); }
    // A newer history transition may have occurred while campaign data loaded.
    if (query.toString() !== new URLSearchParams(location.search).toString()) return;
    if (id && id !== state.caseId) { await openCase(id); if (state.step === "cases" && state.detail) { state.selected.set(caseId(state.detail), state.detail); updateSelected(); } }
    if (state.step === "run" && state.campaignId) refreshLiveTrials();
    if (query.get("pick") === "existing" && !$("casePicker").open) openPicker();
    else if (!id && state.caseId) { ++state.detailVersion; state.caseId = null; state.detail = null; state.reviewEdit = null; $("caseInspector").replaceChildren(node("h2", "Inspect a case"), node("p", "Open a case to inspect its inputs, outputs and reviews.", "muted")); $("caseInspector").setAttribute("aria-busy", "false"); updateSelected(); }
  }
  function caseLink(text, id) {
    const url = new URL(location.href); url.searchParams.set("case", id); url.searchParams.set("step", state.step);
    if (state.campaignId) url.searchParams.set("campaign", state.campaignId);
    const anchor = link(text, url.pathname + url.search);
    anchor.addEventListener("click", event => { if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; event.preventDefault(); openCase(id, true); });
    return anchor;
  }
  $("caseReuseSlot").append($("caseReusePanel"));
  $("assistantConfigureSlot").append($("assistantPanel"));
  $("baselinePanel").insertBefore($("baselineForm"), $("contractPanel"));
  const runAction = $("prepareRun").parentElement; $("baselinePanel").append(runAction);
  const savedRules = node("details"); savedRules.append(node("summary", "Use previously saved scoring rules"), $("campaignContract").closest("label")); $("baselineForm").append(savedRules);
  for (const id of ["contractName", "successScope", "evidenceMode", "assessmentMethod"]) $("scoringAdvanced").append($(id).closest("label"));
  for (const anchor of document.querySelectorAll("[data-journey-step]")) anchor.addEventListener("click", event => { if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; event.preventDefault(); goStep(anchor.dataset.journeyStep); });
  for (const control of document.querySelectorAll("[data-go-step]")) control.addEventListener("click", () => goStep(control.dataset.goStep));
  $("reuseReviewed").addEventListener("click", () => { goStep("cases"); $("caseReusePanel").open = true; $("caseReusePanel").scrollIntoView({block: "start"}); });
  $("defineFreezeContract").addEventListener("click", () => { goStep("configure"); $("contractPanel").open = true; $("contractName").focus(); });
  $("freezeContract").addEventListener("change", () => { $("campaignContract").value = $("freezeContract").value; $("campaignContract").dispatchEvent(new Event("change")); });
  function tell(text, error = false) { $("message").textContent = text; $("message").className = error ? "error-text" : "notice"; }
  function contractOptions() { return [["", "Choose scoring rules"], ...state.contracts.map(item => [item.id, item.name])]; }
  function reviewContractId() { return state.step === "review" && state.reviewCampaignContract ? state.reviewCampaignContract : $("campaignContract").value; }
  function activeContract() { return state.contracts.find(item => item.id === $("campaignContract").value); }
  async function request(url, options = {}) {
    const response = await fetch(url, options);
    let value;
    try { value = await response.json(); } catch { throw new Error(`Unable to read the server response (HTTP ${response.status}).`); }
    if (!response.ok) {
      const detail = value.detail;
      throw new Error(Array.isArray(detail) ? detail.map(item => item.msg || String(item)).join("; ") : typeof detail === "string" ? detail : `Request failed (HTTP ${response.status}). Refresh to check current state before retrying.`);
    }
    return value;
  }
  const post = (url, data, method = "POST") => request(url, {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
  async function loadAssistantStatus() {
    try {
      const status = await request("/api/assistant/status");
      $("assistantStatus").textContent = status.available ? "Uses your configured Copilot model to inspect and preview evaluations. Every prepared change requires your explicit confirmation; the assistant cannot create SME judgments." : status.reason || "The optional assistant is not configured. All evaluation controls below remain available.";
      $("assistantQuestion").disabled = !status.available; $("askAssistant").disabled = !status.available;
    } catch { $("assistantStatus").textContent = "The optional assistant is unavailable. Use the case, campaign and review controls below."; }
  }
  $("assistantForm").addEventListener("submit", async event => {
    event.preventDefault(); $("askAssistant").disabled = true; $("assistantResponse").replaceChildren(node("p", "Inspecting the selected evaluation context…", "muted"));
    try {
      const context = {};
      if (state.selected.size > 100) throw new Error("The assistant accepts up to 100 selected cases. Narrow the selection or use a saved dataset.");
      if (state.selected.size) context.case_revision_ids = [...state.selected.keys()];
      if (state.frozenDataset) context.dataset_revision_id = state.frozenDataset.id;
      if ($("campaignContract").value) context.contract_id = $("campaignContract").value;
      if (state.campaignId) context.campaign_id = state.campaignId;
      if (state.assistantAssets.length) context.asset_sha256s = state.assistantAssets;
      if (state.namedBaseline) context.baseline_revision_id = state.namedBaseline.revision_id;
      const result = await post("/api/assistant/ask", {message: $("assistantQuestion").value.trim(), context});
      $("assistantResponse").replaceChildren(node("p", result.answer || "No answer was returned.", "assistant-answer"));
      const evidence = node("div", null, "assistant-evidence");
      for (const item of result.evidence || []) { const href = evidenceHref(item.url); if (href) evidence.append(link(item.label || "Inspect evidence", href)); }
      $("assistantResponse").append(evidence);
      for (const proposal of result.proposals || []) {
        const card = node("div", null, "review-card"); card.append(node("h4", proposal.request?.name || "Proposed evaluation"));
        const operations = {launch_campaign: "Confirm campaign launch", launch_ablation: "Confirm candidate launch", import_case: "Confirm case import", revise_case: "Confirm case revision", create_contract: "Confirm success contract", freeze_dataset: "Confirm dataset freeze", save_baseline: "Confirm named baseline"};
        if (!operations[proposal.kind]) { card.append(node("p", "This operation is not supported by this interface.", "muted")); $("assistantResponse").append(card); continue; }
        const isRun = ["launch_campaign", "launch_ablation"].includes(proposal.kind);
        card.append(node("p", isRun ? `${proposal.preview?.planned_trials ?? "Unknown number of"} planned trials. Review exact cases, strategy, conditions and contract before confirming.` : proposal.preview?.note || "Review the exact prepared change before confirming. Existing judgments are never invented.", "notice"));
        if (isRun) { const metrics = node("div", null, "metric-preview"); metricsPreview(metrics, proposal.preview || {}); card.append(metrics); }
        card.append(objectDetails("Exact prepared change", proposal.request), objectDetails("Validated preview and evidence revisions", proposal.preview));
        for (const blocker of proposal.preview?.blockers || []) card.append(node("p", blocker, "error-text"));
        const confirm = button(operations[proposal.kind], async () => {
          confirm.disabled = true;
          try {
            const result = await post("/api/assistant/confirm", {operation_id: proposal.operation_id, confirmation_token: proposal.confirmation_token, confirmed: true});
            confirm.remove(); card.append(node("p", "Confirmed change saved."), objectDetails("Saved record", result));
            if (isRun) { card.append(link("Open report", `/api/campaigns/${encodeURIComponent(result.id)}/report?format=html`)); await loadCampaigns(result.id); }
            else if (["import_case", "revise_case"].includes(proposal.kind)) { state.selected.set(caseId(result), result); selectionChanged(); await loadCases(); await openCase(caseId(result), true); }
            else if (proposal.kind === "create_contract") { await loadContracts(); $("campaignContract").value = result.id; invalidateCampaign(); }
            else if (proposal.kind === "freeze_dataset") await loadDatasets();
            else if (proposal.kind === "save_baseline") await loadNamedBaselines(result.id);
          } catch (error) { tell(`${error.message} If the preview changed, ask the assistant to prepare it again.`, true); confirm.disabled = false; }
        }, "");
        confirm.disabled = !proposal.preview?.ready || !proposal.operation_id || !proposal.confirmation_token; card.append(confirm); $("assistantResponse").append(card);
      }
    } catch (error) { $("assistantResponse").replaceChildren(node("p", error.message, "error-text")); }
    finally { $("askAssistant").disabled = false; }
  });
  $("recordingFile").addEventListener("change", async () => {
    const file = $("recordingFile").files?.[0]; state.recordingAsset = null; $("attachRecording").disabled = true;
    $("recordingSnippet").hidden = true;
    if (!file) return;
    $("recordingUploadStatus").textContent = "Uploading the selected evidence file…";
    try { if (file.size > 64 * 1024 * 1024) throw new Error("Use a file no larger than 64 MiB."); const media = file.name.toLowerCase().endsWith(".json") ? "application/json" : file.type || "application/octet-stream"; state.recordingAsset = await request("/api/evidence-assets", {method:"POST",headers:{"Content-Type":media},body:file}); $("recordingUploadStatus").textContent = `${file.name} uploaded (${state.recordingAsset.size_bytes} bytes). Supply the reference meaning and ranges explicitly.`; $("attachRecording").disabled = false; }
    catch(error) { $("recordingUploadStatus").textContent = error.message; }
  });
  $("attachRecording").addEventListener("click", () => {
    try {
      const reference = buildRecordingReference({id:$("recordingId").value.trim(),kind:$("recordingKind").value,clock:$("recordingClock").value,frame:$("recordingFrame").value,units:$("recordingUnits").value,range:$("recordingRange").value,start:$("recordingStart").value,end:$("recordingEnd").value}, state.recordingAsset);
      const evidence = parseObject($("episodeEvidence").value,"Recorded evidence");
      if (evidence.evidence_refs && !Array.isArray(evidence.evidence_refs)) throw new Error("Recorded evidence_refs must be an array.");
      const refs = evidence.evidence_refs || [];
      if (refs.some(item=>item.id===reference.id)) throw new Error("This reference ID is already present; choose a new ID or edit the existing JSON.");
      if (["trajectory","state"].includes(reference.kind)) {
        if (!reference.clock_id || !reference.frame || !Object.keys(reference.units).length) throw new Error("Trajectory/state references require explicit clock, frame and units.");
        for (const [key,value] of [["source_clock_id",reference.clock_id],["frame",reference.frame],["units",reference.units]]) { if (evidence[key] != null && JSON.stringify(evidence[key]) !== JSON.stringify(value)) throw new Error("Reference metadata differs from the existing recording envelope."); evidence[key]=value; }
      }
      evidence.evidence_refs = [...refs,reference]; $("episodeEvidence").value = JSON.stringify(evidence,null,2); $("recordingSnippet").textContent=JSON.stringify(reference,null,2); $("recordingSnippet").hidden=false; $("recordingUploadStatus").textContent="Reference added to the unsaved case. Review the episode metadata and save the case when ready.";
    } catch(error) { $("recordingUploadStatus").textContent=error.message; }
  });
  $("assistantAsset").addEventListener("change", async () => {
    const file = $("assistantAsset").files?.[0]; state.assistantAssets = [];
    if (!file) return;
    $("assistantAssetStatus").textContent = "Uploading the selected observation…";
    try { if (file.size > 16 * 1024 * 1024) throw new Error("Use an image under 16 MiB."); const asset = await request("/api/evidence-assets", {method: "POST", headers: {"Content-Type": file.type || "application/octet-stream"}, body: file}); state.assistantAssets = [asset.sha256]; $("assistantAssetStatus").textContent = `${file.name} attached. Ask the assistant to prepare case metadata; no case has been created yet.`; }
    catch (error) { $("assistantAssetStatus").textContent = error.message; }
  });
  async function loadNamedBaselines(selectedId) {
    const page = await request("/api/baselines"); state.namedBaselines = page.baselines;
    $("namedBaseline").replaceChildren(...select(null, [["", "Choose a saved baseline"], ...page.baselines.map(row => [row.id, `${row.pinned ? "Pinned · " : ""}${row.name} · revision ${row.revision}`])], selectedId || "").childNodes); $("namedBaseline").value = selectedId || "";
    if (selectedId) await openNamedBaseline(selectedId);
  }
  async function openNamedBaseline(id, revisionId) {
    if (!id) { state.namedBaseline = null; $("namedBaselineDetails").replaceChildren(); return; }
    const item = await request(`/api/baselines/${encodeURIComponent(revisionId || id)}`);
    await openCampaign(item.campaign_id); state.namedBaseline = item; state.baselineOperation = null;
    $("namedBaselineName").value = item.name; $("namedBaselinePinned").checked = item.pinned; $("baselineStrategy").value = item.strategy_id; invalidateAblation();
    $("saveNamedBaseline").textContent = "Save a new baseline revision";
    const container = $("namedBaselineDetails"); container.replaceChildren(node("p", `${item.name} · revision ${item.revision}. ${item.note}`, "notice"), objectDetails("Frozen assessment and provenance", item));
    const page = await request(`/api/baselines/${encodeURIComponent(id)}/revisions`);
    const history = select("baselineRevisionHistory", page.revisions.map(row => [row.revision_id, `Revision ${row.revision} · ${row.name} · ${date(row.created_at)}`]), item.revision_id);
    history.addEventListener("change", () => openNamedBaseline(id, history.value).catch(error => tell(error.message, true))); container.append(label("Baseline revision history", history));
  }
  $("namedBaseline").addEventListener("change", () => openNamedBaseline($("namedBaseline").value).catch(error => tell(error.message, true)));
  $("newNamedBaseline").addEventListener("click", () => { state.namedBaseline = null; state.baselineOperation = null; $("namedBaseline").value = ""; $("namedBaselineName").value = ""; $("namedBaselineDetails").replaceChildren(); $("saveNamedBaseline").textContent = "Save selected campaign as baseline"; invalidateAblation(); });
  for (const id of ["namedBaselineName", "namedBaselinePinned", "baselineStrategy"]) $(id).addEventListener("input", () => { state.baselineOperation = null; });
  $("namedBaselineForm").addEventListener("submit", async event => {
    event.preventDefault(); $("saveNamedBaseline").disabled = true;
    try {
      if (!state.campaignId || !$("baselineStrategy").value) throw new Error("Open a completed campaign and choose its baseline strategy first.");
      state.baselineOperation ||= crypto.randomUUID();
      const payload = {name: $("namedBaselineName").value.trim(), pinned: $("namedBaselinePinned").checked, campaign_id: state.campaignId, strategy_id: $("baselineStrategy").value, operation_id: state.baselineOperation, ...(state.namedBaseline ? {expected_head_revision_id: state.namedBaseline.revision_id} : {})};
      const result = await post(state.namedBaseline ? `/api/baselines/${encodeURIComponent(state.namedBaseline.id)}` : "/api/baselines", payload, state.namedBaseline ? "PATCH" : "POST");
      await loadNamedBaselines(result.id); $("namedBaselineStatus").textContent = `Saved ${result.name}, revision ${result.revision}. Later reviews do not change this reference.`;
    } catch (error) { $("namedBaselineStatus").textContent = error.message; }
    finally { $("saveNamedBaseline").disabled = false; }
  });
  function invalidateCampaign() { state.draftVersion = (state.draftVersion || 0) + 1; state.campaignPreview = null; state.campaignOperation = null; $("startBaseline").disabled = true; $("campaignMeasures").replaceChildren(); updateBudget(); }
  function invalidateFreeze() { state.freezePreview = null; $("freezeDataset").disabled = true; }
  function updateBudget() {
    const count = state.frozenDataset ? state.frozenDataset.members.filter(item => item.disposition === "included").length : state.selected.size;
    const context = $("runContext"); context.replaceChildren();
    if (state.frozenDataset) context.append(node("strong", `Saved dataset: ${state.frozenDataset.name}`), node("p", `${count} included case revisions · ${state.frozenDataset.id}`, "record-id"));
    else if (state.selected.size) { context.append(node("strong", `${state.selected.size} selected case revisions`)); const list = node("ul"); for (const item of state.selected.values()) list.append(node("li", `${item.name} · revision ${item.revision || caseId(item)}`)); context.append(list); }
    else context.append(node("p", "No cases selected. Return to Cases to select inputs or use a saved dataset.", "notice"));
    const strategy = state.strategies.find(item => item.id === $("campaignStrategy").value), contract = activeContract();
    $("runSummary").replaceChildren(node("strong", $("campaignName").value || "Untitled campaign"), node("p", `${count} cases × ${strategy ? 1 : 0} strategy × ${Number($("campaignRepeats").value) || 0} repetitions = ${count * (strategy ? 1 : 0) * (Number($("campaignRepeats").value) || 0)} trials.`), node("p", `Strategy: ${strategy?.display_name || strategy?.id || "Not selected"}`), node("p", `Scoring rules: ${contract?.name || "Not confirmed"}`));
    if (contract) { const rubric = node("details"); rubric.append(node("summary", "Review exact scoring criteria")); for (const criterion of contract.criteria || []) rubric.append(node("p", criterion.description)); for (const [id, value] of Object.entries(contract.case_expectations || {})) { rubric.append(node("h4", state.selected.get(id)?.name || id), node("p", value, "detail-instruction muted")); } $("runSummary").append(rubric); }

    $("continueConfigure").disabled = !count;
    $("freezeContract").value = $("campaignContract").value;
    $("selectionCount").textContent = `${state.selected.size} selected`;
    const repeats = Number($("campaignRepeats").value);
    $("selectedCaseCount").textContent = `${count} cases`;
    $("campaignBudget").textContent = `${count} cases × ${repeats || 0} repeats = ${count * (repeats || 0)} planned trials${state.frozenDataset ? ` from saved dataset ${state.frozenDataset.name}` : ""}. Preview the available measures before launch.`;
  }
  function selectionChanged() { if (state.generatedContractId && $("campaignContract").value === state.generatedContractId) $("campaignContract").value = ""; state.criteriaDirty = true; state.frozenDataset = null; state.freezeMembers = null; invalidateCampaign(); invalidateFreeze(); $("freezePreview").textContent = "Selection changed. Preview exact case and review revisions again."; updateSelected(); }
  function updateSelected() {
    const chosen = state.pickerSelected || state.selected;
    for (const input of $("caseList").querySelectorAll("input")) input.checked = chosen.has(input.dataset.caseId);
    const onPage = state.cases.filter(item => chosen.has(caseId(item))).length;
    $("selectPage").checked = state.cases.length > 0 && onPage === state.cases.length;
    $("selectPage").indeterminate = onPage > 0 && onPage < state.cases.length;
    for (const anchor of $("caseList").querySelectorAll("a")) {
      if (anchor.dataset.caseId === state.caseId) anchor.setAttribute("aria-current", "true"); else anchor.removeAttribute("aria-current");
    }
    updateBudget(); renderSelectedCases();
  }
  async function loadCases() {
    const version = ++state.listVersion;
    $("caseListStatus").textContent = "Loading cases…"; $("previousCases").disabled = true; $("nextCases").disabled = true;
    try {
      const query = new URLSearchParams({limit: state.limit, offset: state.offset}); if (state.search) query.set("q", state.search); if (state.category) query.set("category", state.category);
      const page = await request(`/api/cases?${query}`);
      if (version !== state.listVersion) return;
      state.cases = page.cases; $("caseList").replaceChildren();
      if (page.categories) { $("caseCategory").replaceChildren(...select(null, [["", "All categories"], ...page.categories.map(item => [item.name, `${item.name.replaceAll("_", " ")} (${item.count})`])], state.category).childNodes); $("caseCategory").value = state.category; }
      for (const item of state.cases) {
        const row = node("li", null, "case-list-item"), checkbox = node("input"); checkbox.type = "checkbox"; checkbox.dataset.caseId = caseId(item); checkbox.setAttribute("aria-label", `Select ${item.name}`);
        checkbox.addEventListener("change", () => { const chosen = state.pickerSelected || state.selected; if (checkbox.checked) chosen.set(caseId(item), item); else chosen.delete(caseId(item)); updatePickerSelection(); if (!state.pickerSelected) selectionChanged(); });
        const anchor = link("", `/static/datasets.html?case=${encodeURIComponent(caseId(item))}`); anchor.className = "case-link"; anchor.dataset.caseId = caseId(item);
        const thumbnail = caseImage(item); if (thumbnail) anchor.append(thumbnail);
        anchor.append(node("strong", item.name), node("span", item.task, "muted"), node("span", `Revision ${item.revision || "unknown"} · ${item.readiness?.assessment || "Unreviewed"}`, "badge"));
        const sources = node("span", null, "case-source-labels");
        for (const source of caseSourceLabels(item)) sources.append(node("span", source, "badge"));
        if (sources.childNodes.length) anchor.append(sources);
        anchor.addEventListener("click", event => { if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; event.preventDefault(); if (state.pickerSelected) { checkbox.checked = !checkbox.checked; checkbox.dispatchEvent(new Event("change")); } else { state.selected.set(caseId(item), item); selectionChanged(); openCase(caseId(item), true); } });
        row.append(checkbox, anchor); $("caseList").append(row);
      }
      $("caseListStatus").textContent = state.cases.length ? `Showing ${state.cases.length} of ${page.total ?? state.cases.length} cases. Select an image to inspect its task and expected outcome.` : "No cases yet. Add an image and task or try the sample cases.";
      $("casePageLabel").textContent = state.cases.length ? `${state.offset + 1}–${state.offset + state.cases.length}` : "0 cases";
      $("previousCases").disabled = state.offset === 0;
      $("nextCases").disabled = page.total == null ? state.cases.length < state.limit : state.offset + state.limit >= page.total;
      updateSelected();
    } catch (error) { if (version === state.listVersion) $("caseListStatus").textContent = error.message; }
  }
  async function loadContracts(selectedId) {
    const page = await request("/api/success-contracts"); state.contracts = page.contracts;
    const selected = selectedId || $("campaignContract").value;
    $("campaignContract").replaceChildren(...select(null, contractOptions(), selected).childNodes); $("campaignContract").value = selected;
    $("freezeContract").replaceChildren(...select(null, contractOptions(), selected).childNodes); $("freezeContract").value = selected;
    if (!state.contracts.length) $("contractPanel").open = true;
    if ($("reviewContract")) { const current = $("reviewContract").value; $("reviewContract").replaceChildren(...select(null, contractOptions(), current || selected).childNodes); $("reviewContract").value = current || selected; renderReviewCriteria(); }
  }
  function metricsPreview(container, preview) {
    container.replaceChildren();
    for (const metric of preview.metrics || []) {
      const card = node("div", null, "metric-card"); card.append(node("strong", metricLabel(metric.name)), node("span", {available: "Available", needs_review: "Needs expert review", unavailable: "Unavailable"}[metric.status] || metric.status || "Unknown", "badge"), node("p", metric.reason || "No availability explanation supplied.", "muted")); container.append(card);
    }
    if (preview.evidence_note) container.append(node("p", preview.evidence_note, "notice full-width"));
  }
  function currentCampaign() { return buildCampaign({name: $("campaignName").value, caseIds: [...state.selected.keys()], datasetId: state.frozenDataset?.id, contractId: $("campaignContract").value, strategyId: $("campaignStrategy").value, repeats: $("campaignRepeats").value, timeout: $("campaignTimeout").value}); }
  async function previewCampaign() {
    $("previewCampaign").disabled = true; $("startBaseline").disabled = true;
    try {
      const payload = currentCampaign(), signature = JSON.stringify(payload);
      const preview = await post("/api/campaigns/preview", payload);
      if (JSON.stringify(currentCampaign()) !== signature) return;
      state.campaignPreview = {payload, signature, preview}; state.campaignOperation = crypto.randomUUID();
      metricsPreview($("campaignMeasures"), preview);
      $("campaignBudget").textContent = `${preview.planned_trials} planned trials. ${preview.ready ? "Configuration is ready." : "Resolve the blockers before running."}`;
      for (const blocker of preview.blockers || []) $("campaignMeasures").append(node("p", typeof blocker === "string" ? blocker : JSON.stringify(blocker), "error-text full-width"));
      $("startBaseline").disabled = !preview.ready;
    } catch (error) { tell(error.message, true); } finally { $("previewCampaign").disabled = false; }
  }
  function renderCase(item) {
    const container = $("caseInspector"); container.replaceChildren();
    const title = node("h2", item.name); title.id = "caseDetailHeading"; title.tabIndex = -1;
    container.append(title, node("p", item.task, "detail-instruction"), node("p", `Revision ${item.revision || "unknown"} · ${date(item.created_at)}`, "muted"));
    const sources = node("div", null, "case-source-labels");
    for (const source of caseSourceLabels(item)) sources.append(node("span", source, "badge"));
    if (sources.childNodes.length) container.append(sources);
    if (item.image_asset && /^[a-f0-9]{64}$/.test(item.image_asset.sha256 || "")) {
      const load = button("Load observation", () => { load.disabled = true; const image = node("img"); image.className = "observation-image"; image.alt = `Observation for ${item.name}`; image.addEventListener("load", () => load.remove()); image.addEventListener("error", () => { image.remove(); load.disabled = false; load.textContent = "Image unavailable — retry"; }); image.src = `/api/trial-assets/${item.image_asset.sha256}`; load.after(image); }); container.append(load); load.click();
    }
    if (Object.keys(item.reference_data || {}).length) {
      const references = objectDetails("Source annotations · not yet reviewed", item.reference_data);
      references.insertBefore(node("p", "Imported reference material has not been approved by an expert. It is kept separate from candidate inputs and recorded episode evidence. Review the case before creating reusable labels.", "muted"), references.lastChild);
      container.append(references);
    }
    const {reference_data, ...provenance} = item;
    container.append(objectDetails("Inputs, evidence and provenance", provenance));
    container.append(button("Create case revision", () => {
      goStep("cases"); state.caseEdit = item; $("caseName").value = item.name; $("caseTask").value = item.task; $("candidateContext").value = JSON.stringify(item.candidate_context || {}, null, 2); $("caseConditions").value = JSON.stringify(item.conditions || {}, null, 2); $("episodeEvidence").value = JSON.stringify(item.recorded_evidence || {}, null, 2); $("referenceData").value = JSON.stringify(item.reference_data || {}, null, 2); $("caseImage").value = ""; $("caseImage").required = false;
      $("caseEditStatus").textContent = `Creating a new revision from ${caseId(item)}. Existing trials and saved datasets keep their original inputs. A replacement image is optional.`;
      $("saveCase").textContent = "Save new revision"; $("cancelCaseRevision").hidden = false; $("importPanel").open = true; $("caseName").focus();
    }));
    const outputSection = node("section", null, "inspector-section"); outputSection.append(node("h3", "Trial outputs"));
    if (!state.trials.length) outputSection.append(node("p", "No trials yet. Continue to Configure to choose a strategy, then Run to execute it.", "muted"));
    for (const trial of state.trials) {
      const card = node("div", null, "output-card"); card.append(node("h4", trial.strategy?.name || trial.strategy?.id || "Strategy attempt"), node("p", `${trial.status} · ${date(trial.created_at)}`, "muted"), link("Inspect output and evidence", `/static/history.html?trial=${encodeURIComponent(trial.id)}`), objectDetails("Saved output", trial.result));
      if (trial.status !== "running" && trial.result != null) card.append(button("Rate this output", () => { resetReview(); $("reviewTarget").value = "trial_output"; reviewTargetChanged(); $("reviewTrial").value = trial.id; $("reviewerName").focus(); }));
      outputSection.append(card);
    }
    if (state.trials.length === 100) outputSection.append(node("p", "Showing the first 100 recorded trials. Open Results → Trials for older attempts.", "muted"));
    container.append(outputSection);
    const reviewSection = node("section", null, "inspector-section"); reviewSection.append(node("h3", "Expert review"), node("p", "Case validity, reusable annotations and ratings of particular outputs are separate records. A failed output can still be a useful case.", "muted"));
    const form = node("form"); form.id = "reviewForm";
    const target = select("reviewTarget", [["case_validity", "Case validity — is this test usable?"], ["case_annotation", "Reusable annotation — expected properties"], ["trial_output", "Trial output — rate this agent's result"]]);
    form.append(label("Review target", target), label("Scoring rules", select("reviewContract", contractOptions(), reviewContractId())));
    const trialSelect = select("reviewTrial", [["", "Choose an output"], ...state.trials.filter(trial => trial.status !== "running" && trial.result != null).map(trial => [trial.id, `${trial.strategy?.name || trial.strategy?.id || "Trial"} · ${trial.id.slice(0, 12)}`])]);
    const trialLabel = label("Specific trial output", trialSelect); trialLabel.id = "reviewTrialLabel"; trialLabel.hidden = true; form.append(trialLabel);
    const reviewer = node("input"); reviewer.id = "reviewerName"; reviewer.maxLength = 160; reviewer.required = true; reviewer.placeholder = "Your name (local attribution)";
    const grid = node("div", null, "form-grid"); grid.append(label("Reviewer", reviewer), label("Decision", select("reviewDecision", [["unknown", "Unknown / not assessable"], ["accepted", "Accepted"], ["rejected", "Rejected"]]))); form.append(grid);
    const criteria = node("div"); criteria.id = "reviewCriteria"; form.append(criteria);
    const annotation = node("textarea"); annotation.id = "reviewAnnotations"; annotation.rows = 3; annotation.placeholder = "The target is the red part. Keep adjacent objects undisturbed. Advanced: a JSON object is also accepted.";
    const annotationLabel = label("Reusable expectations or labels", annotation); annotationLabel.id = "reviewAnnotationsLabel"; annotationLabel.hidden = true; form.append(annotationLabel);
    const rationale = node("textarea"); rationale.id = "reviewRationale"; rationale.rows = 3; rationale.placeholder = "Why is this accepted, rejected or not assessable?"; form.append(label("Rationale", rationale));
    const advanced = node("details"); advanced.append(node("summary", "Evidence references")); const refs = node("textarea"); refs.id = "reviewEvidence"; refs.rows = 2; refs.placeholder = "One recorded evidence reference per line"; advanced.append(label("References", refs)); form.append(advanced);
    const status = node("p", "New review. Reviewer names record attribution, not authenticated identity.", "muted"); status.id = "reviewEditStatus"; form.append(status);
    const actions = node("div", null, "form-actions"), final = node("button", "Save final review"); final.type = "submit"; final.id = "saveReview";
    actions.append(button("Save draft", () => saveReview("draft")), final, button("Clear form", resetReview)); form.append(actions);
    form.addEventListener("submit", event => { event.preventDefault(); saveReview("final"); }); target.addEventListener("change", reviewTargetChanged);
    reviewSection.append(form);
    if (!state.reviews.length && state.step === "cases") { target.value = "case_annotation"; $("reviewAnnotationsLabel")?.setAttribute("hidden", ""); annotation.value = state.expectations.get(caseId(item)) || suggestedExpectation(item); annotationLabel.hidden = false; }
    const reviews = node("div", null, "review-list"); reviews.id = "reviewList"; reviewSection.append(reviews); container.append(reviewSection);
    $("reviewContract").addEventListener("change", renderReviewCriteria); renderReviewCriteria(); renderReviews();
  }
  function reviewTargetChanged() { $("reviewTrialLabel").hidden = $("reviewTarget").value !== "trial_output"; $("reviewAnnotationsLabel").hidden = $("reviewTarget").value !== "case_annotation"; renderReviewCriteria(); }
  function renderReviewCriteria(values = {}) {
    if (!$("reviewCriteria")) return;
    $("reviewCriteria").replaceChildren();
    if ($("reviewTarget").value !== "trial_output") return;
    const contract = state.contracts.find(item => item.id === $("reviewContract").value);
    if (contract?.case_expectations?.[state.caseId]) $("reviewCriteria").append(node("p", contract.case_expectations[state.caseId], "notice detail-instruction"));
    for (const criterion of contract?.criteria || []) {
      const input = select(null, [["unknown", "Unknown"], ["accepted", "Accepted"], ["rejected", "Rejected"]], values[criterion.id] || "unknown"); input.dataset.criterionId = criterion.id;
      $("reviewCriteria").append(label(`${criterion.description}${criterion.required ? " (required)" : ""}`, input));
    }
  }
  function resetReview() { state.reviewEdit = null; $("reviewForm").reset(); for (const id of ["reviewTarget", "reviewContract", "reviewTrial", "reviewerName"]) $(id).disabled = false; $("reviewContract").value = reviewContractId(); $("reviewEditStatus").textContent = "New review. Reviewer names record attribution, not authenticated identity."; reviewTargetChanged(); }
  function editReview(review) {
    state.reviewEdit = review; $("reviewTarget").value = review.target_type; $("reviewContract").value = review.contract_id; $("reviewTrial").value = review.trial_id || ""; $("reviewerName").value = review.reviewer; $("reviewDecision").value = review.decision; $("reviewRationale").value = review.rationale; $("reviewAnnotations").value = Object.keys(review.annotations || {}).length ? JSON.stringify(review.annotations, null, 2) : ""; $("reviewEvidence").value = (review.evidence_refs || []).join("\n"); reviewTargetChanged(); renderReviewCriteria(review.criteria);
    for (const id of ["reviewTarget", "reviewTrial", "reviewerName"]) $(id).disabled = true;
    $("reviewContract").disabled = review.status === "draft";
    $("reviewEditStatus").textContent = review.status === "draft" ? `Editing draft ${review.id}, revision ${review.revision}. Concurrent edits will be rejected.` : `New review superseding ${review.id}. The original final review remains preserved.`; $("reviewerName").focus();
  }
  function renderReviews() {
    $("reviewList").replaceChildren(node("h3", "Saved reviews"));
    if (!state.reviews.length) $("reviewList").append(node("p", "No reviews yet. Drafts and final reviews are saved with this case revision.", "muted"));
    for (const review of state.reviews) {
      const card = node("div", null, "review-card"); card.append(node("h4", `${review.target_type.replaceAll("_", " ")} · ${review.decision}`), node("p", `${review.reviewer} · ${review.status} · revision ${review.revision} · ${date(review.updated_at || review.created_at)}`, "muted"), node("p", review.rationale || "Draft without rationale", "muted"), objectDetails("Exact review and rubric reference", review), button(review.status === "draft" ? "Continue draft" : "Create correction", () => editReview(review))); $("reviewList").append(card);
    }
  }
  async function saveReview(status) {
    const activeCase = state.caseId;
    const submit = $("saveReview"); submit.disabled = true;
    try {
      const criteria = Object.fromEntries([...$("reviewCriteria").querySelectorAll("select")].map(input => [input.dataset.criterionId, input.value]));
      const payload = buildReview({target: $("reviewTarget").value, caseId: activeCase, trialId: $("reviewTrial").value, contractId: $("reviewContract").value, reviewer: $("reviewerName").value, status, decision: $("reviewDecision").value, criteria, annotations: $("reviewAnnotations").value, rationale: $("reviewRationale").value, evidence: $("reviewEvidence").value, supersedes: state.reviewEdit?.status === "final" ? state.reviewEdit.id : state.reviewEdit?.supersedes_id});
      if (state.reviewEdit?.status === "draft") await post(`/api/reviews/${encodeURIComponent(state.reviewEdit.id)}`, {...payload, expected_revision: state.reviewEdit.revision}, "PATCH");
      else await post("/api/reviews", payload);
      invalidateFreeze(); state.freezeMembers = null;
      if (state.caseId === activeCase) { const page = await request(`/api/reviews?case_revision_id=${encodeURIComponent(activeCase)}`); if (state.caseId === activeCase) { state.reviews = page.reviews; resetReview(); renderReviews(); } }
      tell(`${status === "draft" ? "Draft" : "Final review"} saved. A review adds an assessment; it does not create another trial.`);
    } catch (error) { tell(error.message, true); } finally { if (submit.isConnected) submit.disabled = false; }
  }
  async function openCase(id, push = false) {
    const version = ++state.detailVersion; state.caseId = id; state.reviewEdit = null;
    if (push) { if (state.step === "run") goStep("cases", false); writeRoute(); }
    updateSelected(); $("caseInspector").setAttribute("aria-busy", "true"); $("caseInspector").replaceChildren(node("p", "Loading case and review history…", "muted"));
    try {
      const [item, reviews, trials] = await Promise.all([request(`/api/cases/${encodeURIComponent(id)}`), request(`/api/reviews?case_revision_id=${encodeURIComponent(id)}`), request(`/api/trials?case_revision_id=${encodeURIComponent(id)}&limit=100`)]);
      if (version !== state.detailVersion) return;
      state.detail = item; state.reviews = reviews.reviews; state.trials = trials.trials; renderCase(item);
      if (push) { $("caseInspection").open = true; $("caseDetailHeading").focus(); }
    } catch (error) { if (version === state.detailVersion) $("caseInspector").replaceChildren(node("h2", "Case unavailable"), node("p", error.message, "error-text"), button("Try again", () => openCase(id))); }
    finally { if (version === state.detailVersion) $("caseInspector").setAttribute("aria-busy", "false"); }
  }
  async function loadDatasets() {
    const page = await request("/api/datasets?limit=100"); state.datasets = page.datasets; $("datasetList").replaceChildren();
    const previous = $("datasetParent").value; $("datasetParent").replaceChildren(...select(null, [["", "First revision"], ...state.datasets.map(item => [item.id, `${item.name} · ${item.id.slice(0, 10)}`])], previous).childNodes); $("datasetParent").value = previous;
    if (!state.datasets.length) $("datasetList").append(node("p", "No saved datasets yet. Save the cases in this campaign as a collection you can reuse.", "muted"));
    for (const dataset of state.datasets) {
      const use = button("Use these cases", async () => {
        const version = state.datasetUseVersion = (state.datasetUseVersion || 0) + 1;
        const previousSelection = JSON.stringify([...state.selected.keys()]); use.disabled = true;
        try {
          const snapshot = await request(`/api/datasets/${encodeURIComponent(dataset.id)}`);
          const cases = await Promise.all(snapshot.members.filter(member => member.disposition === "included").map(member => request(`/api/cases/${encodeURIComponent(member.case_revision_id)}`)));
          const contract = state.contracts.find(item => item.id === snapshot.contract_id);
          if (!contract) throw new Error("The dataset's saved scoring rules are unavailable. Refresh before using these cases.");
          if (version !== state.datasetUseVersion) return;
          if (previousSelection !== JSON.stringify([...state.selected.keys()])) throw new Error("Your selected cases changed while the dataset loaded. Choose the saved dataset again to replace that selection.");
          state.selected = new Map(cases.map(item => [caseId(item), item])); state.pickerSelected = null;
          state.expectations = new Map(cases.map(item => [caseId(item), contract.case_expectations?.[caseId(item)] || contract.criteria.map(criterion => criterion.description).join("\n")]));
          state.frozenDataset = snapshot; state.freezeParent = snapshot; state.freezeMembers = null;
          state.generatedContractId = snapshot.contract_id; state.criteriaDirty = false;
          $("campaignContract").value = snapshot.contract_id; $("freezeContract").value = snapshot.contract_id;
          $("datasetParent").value = snapshot.id; $("datasetName").value = snapshot.name;
          state.caseId = null; state.detail = null; state.detailVersion++; state.reviews = []; state.trials = []; state.reviewEdit = null;
          $("caseInspector").replaceChildren(); $("caseInspection").open = false;
          invalidateCampaign(); invalidateFreeze(); updateSelected(); goStep("configure");
          $("freezePreview").textContent = `Using saved dataset ${snapshot.name}. Its exact case and review versions remain unchanged. Editing the selected cases creates a new campaign selection; saving it adds a dataset version.`;
          tell(`Loaded ${cases.length} cases from ${snapshot.name} with their saved scoring rules. You can inspect them in Cases or configure the next campaign.`);
        } catch (error) { tell(error.message, true); }
        finally { if (use.isConnected) use.disabled = false; }
      });
      const card = node("div", null, "dataset-card"); card.append(node("h4", dataset.name), node("p", `${dataset.readiness || "Unknown readiness"} · ${date(dataset.created_at)}`, "muted"), node("p", dataset.sha256, "record-id"), objectDetails("Saved cases and review versions", dataset), use); $("datasetList").append(card);
    }
  }
  async function loadCampaigns(selectedId) {
    const campaigns = await request("/api/campaigns"); state.campaigns = campaigns;
    const selected = selectedId || $("resultCampaign").value;
    $("resultCampaign").replaceChildren(...select(null, [["", "Choose a campaign"], ...campaigns.map(item => [item.id, `${item.name} · ${item.status}`])], selected).childNodes); $("resultCampaign").value = selected;
    if (selectedId) { await openCampaign(selectedId); goStep("review"); }
  }
  function displayMeasure(value, unit) {
    if (value == null) return "unavailable";
    return unit === "fraction" ? `${(value * 100).toFixed(1)}%` : `${Number(value.toFixed(3))} ${unit}`;
  }
  function renderSummary(summary, container, heading) {
    if (heading) container.append(node("h3", heading));
    const counts = assessedCounts(summary), grid = node("div", null, "summary-grid");
    for (const [name, count] of [["Passed assessment", counts.passed], ["Failed assessment", counts.failed], ["Unknown or pending", counts.unknown]]) { const card = node("div", null, "summary-item"); card.append(node("span", name, "summary-label"), node("strong", count, "summary-value")); grid.append(card); } container.append(grid);
    container.append(node("p", `${summary.completed_trials ?? 0} recorded / ${summary.planned_trials ?? 0} planned trials. Unknown outcomes remain in the planned denominator.`, "muted"));
    const metrics = node("details"); metrics.append(node("summary", "Reliability, latency and metric details"), node("p", "pass@k estimates at least one success; pass^k estimates all k succeeding. Unresolved ranges are bounds from missing outcomes, not confidence intervals or robot deployment guarantees.", "muted"));
    for (const strategy of summary.strategies || []) {
      metrics.append(node("h4", strategy.strategy_id));
      const table = node("table"), head = node("tr"); for (const text of ["Measure", "k", "Estimate or unresolved range"]) head.append(node("th", text)); const thead = node("thead"); thead.append(head); const body = node("tbody");
      for (const measure of ["pass_at_k", "pass_pow_k"]) for (const point of strategy[measure] || []) { const row = node("tr"); row.append(node("td", metricLabel(measure)), node("td", point.k), node("td", metricValue(point))); body.append(row); }
      table.append(thead, body); const scroll = node("div", null, "table-scroll"); scroll.append(table); metrics.append(scroll, node("p", `Pipeline latency p95: ${strategy.latency_p95_ms == null ? "unavailable" : `${strategy.latency_p95_ms} ms`}. This is not robot task completion time.`, "muted"));
    }
    container.append(metrics);
    if (summary.campaign_targets?.length) {
      const targets = node("section", null, "target-results"); targets.append(node("h4", "Campaign targets"));
      for (const target of summary.campaign_targets) {
        const row = node("div", null, "notice"); row.append(node("strong", `${target.strategy_id} · ${metricLabel(target.metric)}: ${target.status === "met" ? "Met" : target.status === "not_met" ? "Not met" : "Unknown"}`), node("p", `${target.operator === "gte" ? "At least" : "At most"} ${displayMeasure(target.threshold, target.unit)}. Measured value: ${displayMeasure(target.value, target.unit)}. Denominator: ${target.denominator}; unknown trials: ${target.unknown_trials}.`, "muted")); targets.append(row);
      }
      container.append(targets);
    }
    if (summary.robotics?.length) {
      const robotics = node("details"); robotics.append(node("summary", "Robotics measures and evidence coverage"));
      for (const item of summary.robotics) {
        robotics.append(node("h4", `${item.strategy_id} · ${item.evidence_mode}`));
        for (const [name, metric] of Object.entries(item.metrics || {})) robotics.append(node("p", `${metricLabel(name)}: ${displayMeasure(metric.value, metric.unit)}. ${metric.known_trials}/${metric.planned_trials} trials with evidence; quality ${metric.quality}; aggregation ${metric.aggregation}. ${metric.reason || ""}`, "muted"));
      }
      robotics.append(objectDetails("Exact metric denominators", summary.robotics)); container.append(robotics);
    }
  }
  function renderDifferences(comparison, container) {
    const details = node("details"); details.append(node("summary", `${comparison.differences?.length || 0} recorded component differences`));
    for (const difference of comparison.differences || []) { const card = node("div", null, "review-card"); card.append(node("h4", difference.path), objectDetails("Baseline value", difference.baseline), objectDetails("Candidate value", difference.candidate)); details.append(card); }
    if (!comparison.differences?.length) details.append(node("p", "No recorded component differences. Identical aliases do not prove immutable remote model weights.", "muted")); container.append(details);
  }
  function renderComparison(comparison) {
    const container = $("comparisonResults"); container.replaceChildren(); container.className = "inspector-section"; container.append(node("h3", "Baseline and candidate comparison"), node("p", comparison.comparable ? "Recorded evaluation conditions match. Counts below describe paired attempts." : "These campaigns are not comparable under the recorded conditions.", "notice"));
    for (const reason of comparison.reasons || []) container.append(node("p", reason, "error-text"));
    container.append(node("p", comparison.evidence_note, "muted"));
    if (comparison.baseline && comparison.candidate) { const pair = node("div", null, "form-grid"); const baseline = node("section"), candidate = node("section"); renderSummary(comparison.baseline, baseline, "Baseline"); renderSummary(comparison.candidate, candidate, "Candidate"); pair.append(baseline, candidate); container.append(pair); }
    renderDifferences(comparison, container);
    for (const item of comparison.case_comparisons || []) {
      const card = node("div", null, "output-card"); card.append(caseLink(`Case ${item.case_id}`, item.case_id), node("p", `${item.status.replaceAll("_", " ")} · ${item.planned_pairs} planned pairs · ${((item.paired_coverage || 0) * 100).toFixed(1)}% assessed pairing coverage`, "muted"));
      const rows = node("details"); rows.append(node("summary", "Paired attempts and evidence"));
      for (const attempt of item.attempts || []) { const line = node("p", `Seed ${attempt.seed}: ${attempt.baseline} → ${attempt.candidate}. `, "muted"); const baselineId = pairedTrialId(comparison, item, attempt, "baseline"), candidateId = pairedTrialId(comparison, item, attempt, "candidate"); if (baselineId) line.append(link("Baseline trial", `/static/history.html?trial=${encodeURIComponent(baselineId)}`), document.createTextNode(" · ")); if (candidateId) line.append(link("Candidate trial", `/static/history.html?trial=${encodeURIComponent(candidateId)}`)); if (candidateId && baselineId) line.append(document.createTextNode(" · "), link("Compare trace lanes", `/static/history.html?trial=${encodeURIComponent(candidateId)}&compare=${encodeURIComponent(baselineId)}`)); rows.append(line); }
      card.append(rows); container.append(card);
    }
    container.append(objectDetails("Exact comparison and assessment revisions", comparison));
  }
  async function openCampaign(id, push = true) {
    const version = ++state.campaignVersion; state.campaignId = id; state.reviewCampaignContract = null; $("resultCampaign").value = id || ""; state.namedBaseline = null; state.baselineOperation = null; invalidateAblation(); $("comparisonResults").replaceChildren(); $("ablationPanel").hidden = true;
    if (push) { goStep("review", false); writeRoute(); }
    if (!id) { $("campaignResults").replaceChildren(node("p", "Choose a campaign to inspect its assessed outcomes.", "muted")); return; }
    $("campaignResults").replaceChildren(node("p", "Loading authoritative campaign assessments…", "muted"));
    try {
      const [detail, result] = await Promise.all([request(`/api/campaigns/${encodeURIComponent(id)}`), request(`/api/campaigns/${encodeURIComponent(id)}/assessments`)]);
      if (version !== state.campaignVersion) return;
      const campaign = detail.campaign; state.reviewCampaignContract = campaign.contract_id || null; $("campaignResults").replaceChildren();
      $("campaignResults").append(node("p", `${campaign.spec.name} · ${campaign.status} · ${date(campaign.created_at)}`, "muted"));
      renderSummary(result.summary, $("campaignResults"));
      $("campaignResults").append(link("Open full report", `/api/campaigns/${encodeURIComponent(id)}/report?format=html`), objectDetails("Assessment source, coverage and review revisions", result.assessments));
      if (campaign.contract?.criteria?.some(criterion => criterion.assessment === "human_review")) $("campaignResults").append(node("p", "Human-review criteria use expert ratings of each output. A model's success statement does not fill missing reviews.", "notice"));
      for (const trial of result.trials || []) {
        const taskName = campaign.spec.tasks.find(entry => entry.id === trial.task_id)?.task || trial.task_id;
        const item = node("div", null, "compact-actions"); item.append(node("span", `${taskName} · ${trial.strategy_id} · repetition ${Number(trial.seed) + 1}: ${trial.outcome}`, "muted")); if (trial.trial_id) item.append(link("Inspect trial", `/static/history.html?trial=${encodeURIComponent(trial.trial_id)}`));
        const task = campaign.spec.tasks.find(entry => entry.id === trial.task_id); const revision = campaign.case_revision_ids?.find(value => value === trial.task_id) || task?.case_revision_id;
        if (revision) item.append(caseLink("Review case", revision)); $("campaignResults").append(item);
      }
      $("baselineStrategy").replaceChildren(...select(null, (campaign.spec.strategies || []).map(value => [value, value])).childNodes);
      $("baselineStrategy").value = campaign.spec.strategies?.[0] || "";
      $("ablationPanel").hidden = !campaign.contract_id;
      if (campaign.baseline) { const comparison = await request(`/api/campaigns/${encodeURIComponent(id)}/comparison`); if (version === state.campaignVersion) renderComparison(comparison); }
    } catch (error) { if (version === state.campaignVersion) $("campaignResults").replaceChildren(node("p", error.message, "error-text"), button("Retry results", () => openCampaign(id))); }
  }
  function invalidateAblation() { state.ablationPreview = null; state.ablationOperation = null; $("runAblation").disabled = true; $("ablationPreview").textContent = "Preview to confirm that conditions match and inspect recorded differences."; }
  function ablationPayload() { if (!state.campaignId || !$("baselineStrategy").value || !$("candidateStrategy").value) throw new Error("Choose the baseline and candidate strategies."); return {...(state.namedBaseline ? {baseline_revision_id: state.namedBaseline.revision_id} : {}), baseline_strategy_id: $("baselineStrategy").value, candidate_strategy_id: $("candidateStrategy").value, name: $("ablationName").value.trim(), intended_change: $("intendedChange").value.trim()}; }
  $("previewAblation").addEventListener("click", async () => {
    $("previewAblation").disabled = true; $("runAblation").disabled = true;
    try {
      const id = state.campaignId, payload = ablationPayload(), signature = JSON.stringify(payload);
      const preview = await post(`/api/campaigns/${encodeURIComponent(id)}/ablation-preview`, payload);
      if (id !== state.campaignId || signature !== JSON.stringify(ablationPayload())) return;
      state.ablationPreview = {id, payload, signature, preview}; state.ablationOperation = crypto.randomUUID();
      $("ablationPreview").replaceChildren(node("p", `${preview.planned_trials} planned candidate attempts. ${preview.comparison.comparable ? "Recorded conditions match." : "Comparison is blocked."}`));
      for (const reason of [...(preview.blockers || []), ...(preview.comparison.reasons || [])]) $("ablationPreview").append(node("p", reason, "error-text"));
      renderDifferences(preview.comparison, $("ablationPreview")); $("ablationPreview").append(node("p", preview.comparison.evidence_note, "muted")); $("runAblation").disabled = !preview.ready || !preview.comparison.comparable;
    } catch (error) { tell(error.message, true); } finally { $("previewAblation").disabled = false; }
  });
  $("ablationForm").addEventListener("submit", async event => {
    event.preventDefault(); $("runAblation").disabled = true;
    try {
      const saved = state.ablationPreview; if (!saved || saved.id !== state.campaignId || saved.signature !== JSON.stringify(ablationPayload()) || !saved.preview.ready || !saved.preview.comparison.comparable) throw new Error("Preview the current candidate before launching.");
      const result = await post(`/api/campaigns/${encodeURIComponent(saved.id)}/ablation`, {...saved.payload, operation_id: state.ablationOperation}); await loadCampaigns(result.id); tell(`Candidate campaign started: ${result.planned_trials} planned trials. Refresh results after execution or expert review.`);
    } catch (error) { tell(error.message, true); $("runAblation").disabled = !state.ablationPreview?.preview.ready || !state.ablationPreview?.preview.comparison.comparable; }
  });
  for (const id of ["baselineStrategy", "candidateStrategy", "ablationName", "intendedChange"]) $(id).addEventListener("input", invalidateAblation);
  $("resultCampaign").addEventListener("change", () => openCampaign($("resultCampaign").value));
  $("refreshCampaigns").addEventListener("click", async () => { try { await loadCampaigns(); if ($("resultCampaign").value) await openCampaign($("resultCampaign").value); } catch (error) { tell(error.message, true); } });
  async function loadFreezeMembers() {
    const contractId = $("campaignContract").value, ids = [...state.selected.keys()], parentId = $("datasetParent").value;
    if ((!ids.length && !parentId) || !contractId) throw new Error("Select cases and a success definition before saving a dataset.");
    const parent = parentId ? await request(`/api/datasets/${encodeURIComponent(parentId)}`) : null;
    if (parent && parent.contract_id !== contractId) throw new Error("This dataset uses a different success definition. Select it again to inherit its definition, or save a new dataset.");
    const allIds = [...new Set([...ids, ...(parent?.members || []).map(member => member.case_revision_id)])];
    const cases = await Promise.all(allIds.map(id => request(`/api/cases/${encodeURIComponent(id)}`)));
    const available = new Map(await Promise.all(allIds.map(async id => { const page = await request(`/api/reviews?case_revision_id=${encodeURIComponent(id)}&limit=1000`); return [id, page.reviews.filter(review => review.status === "final" && review.contract_id === contractId)]; })));
    const additions = ids.map(id => ({case_revision_id: id, disposition: "included", reason: "", review_ids: activeReviewIds(available.get(id))}));
    const members = mergeDatasetMembers(parent, additions, cases, contractId).map(member => ({...member, reviews: available.get(member.case_revision_id)}));
    if (parentId !== $("datasetParent").value || contractId !== $("campaignContract").value || JSON.stringify(ids) !== JSON.stringify([...state.selected.keys()])) throw new Error("Selection changed. Preview the new selection again.");
    state.freezeParent = parent; state.freezeMembers = members; renderFreezeMembers();
  }
  function renderFreezeMembers() {
    $("freezePreview").replaceChildren(node("p", "Choose exact final reviews to preserve. Disagreements stay visible; excluding a case requires a reason."));
    for (const member of state.freezeMembers) {
      const card = node("div", null, "review-card"); card.append(node("h4", state.selected.get(member.case_revision_id)?.name || member.case_revision_id));
      const disposition = select(null, [["included", "Include case"], ["excluded", "Exclude with reason"]], member.disposition);
      disposition.addEventListener("change", () => { member.disposition = disposition.value; invalidateFreeze(); }); card.append(label("Dataset membership", disposition));
      const reason = node("input"); reason.value = member.reason; reason.placeholder = "Reason required when excluding"; reason.addEventListener("input", () => { member.reason = reason.value; invalidateFreeze(); }); card.append(label("Membership rationale", reason));
      if (!member.reviews.length) card.append(node("p", "No final reviews under the selected contract. Preview will show the resulting coverage gap.", "muted"));
      for (const review of member.reviews) {
        const input = node("input"); input.type = "checkbox"; input.checked = member.review_ids.includes(review.id);
        input.addEventListener("change", () => { member.review_ids = input.checked ? [...member.review_ids, review.id] : member.review_ids.filter(id => id !== review.id); invalidateFreeze(); });
        const text = `${review.target_type.replaceAll("_", " ")} · ${review.decision} · ${review.reviewer} · revision ${review.revision}`; card.append(label(text, input));
      }
      $("freezePreview").append(card);
    }
    const result = node("div"); result.id = "freezeResult"; $("freezePreview").append(result);
  }
  function freezePayload() {
    const parentId = $("datasetParent").value || null;
    if (parentId && (state.freezeParent?.id !== parentId || state.freezeParent.contract_id !== $("campaignContract").value)) throw new Error("The dataset or its success definition changed. Select the dataset and preview again.");
    return {name: $("datasetName").value.trim() || state.freezeParent?.name || "", parent_id: parentId, contract_id: $("campaignContract").value, members: (state.freezeMembers || []).map(({reviews, ...member}) => member)};
  }
  async function previewFreeze() {
    $("previewFreeze").disabled = true; invalidateFreeze();
    try {
      if (!state.freezeMembers) await loadFreezeMembers();
      const payload = freezePayload(), signature = JSON.stringify(payload), preview = await post("/api/datasets/preview", payload);
      if (JSON.stringify(freezePayload()) !== signature) return;
      state.freezePreview = {payload, signature, hash: preview.preview_hash};
      $("freezeResult").replaceChildren(node("p", `Readiness: ${preview.readiness}. Review the preserved coverage and any issues before saving.`), objectDetails("Coverage and exact preview", preview));
      for (const issue of preview.issues || []) $("freezeResult").append(node("p", typeof issue === "string" ? issue : JSON.stringify(issue), "muted"));
      $("freezeDataset").disabled = !preview.preview_hash;
    } catch (error) { tell(error.message, true); } finally { $("previewFreeze").disabled = false; }
  }
  $("caseForm").addEventListener("submit", async event => {
    event.preventDefault(); $("saveCase").disabled = true;
    try {
      const image = $("caseImage").files[0]; if (!image && !state.caseEdit) throw new Error("Select an observation image."); if (image?.size > 16 * 1024 * 1024) throw new Error("Choose an image no larger than 16 MiB.");
      const metadata = buildCaseMetadata({name: $("caseName").value, task: $("caseTask").value, context: $("candidateContext").value, conditions: $("caseConditions").value, episode: $("episodeEvidence").value, references: $("referenceData").value});
      if (state.caseEdit) metadata.expected_head_revision_id = state.caseEdit.head_revision_id;
      const data = new FormData(); if (image) data.append("image", image); data.append("metadata", JSON.stringify(metadata)); const saved = await request(state.caseEdit ? `/api/cases/${encodeURIComponent(state.caseEdit.case_id)}/revisions` : "/api/cases", {method: "POST", body: data});
      for (const [id, item] of state.selected) if (item.case_id === saved.case_id) state.selected.delete(id);
      state.selected.set(caseId(saved), saved); if ($("newCaseExpectation").value.trim()) state.expectations.set(caseId(saved), $("newCaseExpectation").value.trim()); selectionChanged(); $("importPanel").open = false; resetCaseForm(); await loadCases(); await openCase(caseId(saved), true); tell("Case added to this campaign. Add another case or continue to configuration.");
    } catch (error) { tell(error.message, true); } finally { $("saveCase").disabled = false; }
  });
  $("contractForm").addEventListener("submit", async event => {
    event.preventDefault(); $("saveContract").disabled = true; const draftVersion = state.draftVersion || 0;
    try {
      const payload = buildContract({json: $("contractJson").value, targetMetric: $("targetMetric").value, targetOperator: $("targetOperator").value, targetThreshold: $("targetThreshold").value, annotationEndpoint: $("annotationEndpoint").value, annotationKey: $("annotationKey").value, annotationTarget: $("annotationTarget").value, name: $("contractName").value, scope: $("successScope").value, evidenceMode: $("evidenceMode").value, assessment: $("assessmentMethod").value, criteria: $("successCriteria").value, endpoint: $("verifierEndpoint").value});
      if (!$("contractJson").value.trim()) payload.case_expectations = Object.fromEntries([...state.selected].map(([id, item]) => [id, state.expectations.get(id) || suggestedExpectation(item)]));
      const contract = await post("/api/success-contracts", payload); state.generatedContractId = contract.id; await loadContracts(contract.id); if ((state.draftVersion || 0) !== draftVersion) { $("campaignContract").value = ""; invalidateCampaign(); tell("Earlier scoring rules were saved, but your draft changed while saving. Confirm the current rules before running."); return; } invalidateCampaign(); invalidateFreeze(); state.freezeMembers = null; $("contractPanel").open = false; tell("Scoring rules confirmed. Continue to review the run and its expected measures.");
    } catch (error) { tell(error.message, true); } finally { $("saveContract").disabled = false; }
  });
  $("baselineForm").addEventListener("submit", async event => {
    event.preventDefault(); $("startBaseline").disabled = true;
    try {
      const preview = state.campaignPreview; if (!preview || preview.signature !== JSON.stringify(currentCampaign()) || !preview.preview.ready) throw new Error("Preview the current configuration before launching.");
      const campaign = await post("/api/campaigns/from-cases", {...preview.payload, operation_id: state.campaignOperation});
      $("campaignStatus").replaceChildren(node("span", `Campaign started: ${campaign.planned_trials} planned trials. `), link("Open report", `/api/campaigns/${encodeURIComponent(campaign.id)}/report?format=html`), document.createTextNode(" · "), link("Inspect recorded trials", `/static/history.html?source=campaign`));
      state.campaignPreview = null; tell("Campaign started. Each complete pipeline execution is saved as a trial below.");
      state.preparingRun = false; await loadCampaigns(campaign.id); goStep("run"); await refreshLiveTrials(); $("liveTrialsPanel").scrollIntoView({block: "start"});
    } catch (error) { tell(error.message, true); $("startBaseline").disabled = !state.campaignPreview?.preview.ready; }
  });
  $("freezeForm").addEventListener("submit", async event => {
    event.preventDefault(); $("freezeDataset").disabled = true;
    try {
      const preview = state.freezePreview; if (!preview || preview.signature !== JSON.stringify(freezePayload())) throw new Error("Preview the current case and review selection before saving.");
      const dataset = await post("/api/datasets", {...preview.payload, expected_preview_hash: preview.hash});
      state.freezePreview = null; await loadDatasets(); tell(`Saved dataset ${dataset.name} saved with exact case and review references. Later corrections require another revision.`);
    } catch (error) { tell(error.message, true); invalidateFreeze(); }
  });
  function resetCaseForm() { state.recordingAsset = null; $("attachRecording").disabled = true; $("recordingSnippet").hidden = true; $("recordingUploadStatus").textContent = "Choose a file to upload it."; state.caseEdit = null; $("caseForm").reset(); $("caseImage").required = true; $("saveCase").textContent = "Save case"; $("cancelCaseRevision").hidden = true; $("caseEditStatus").textContent = "New case. Saved inputs receive an immutable revision."; }
  $("loadExamples").addEventListener("click", async () => { $("loadExamples").disabled = true; try { const result = await post("/api/cases/examples", {}); for (const item of result.cases) state.selected.set(caseId(item), item); selectionChanged(); await loadCases(); if (result.cases.length) await openCase(caseId(result.cases[0]), true); $("importPanel").open = false; tell("Synthetic robotics examples imported and selected. Their observations demonstrate evaluation behavior, not physical performance."); } catch (error) { tell(error.message, true); } finally { $("loadExamples").disabled = false; } });
  $("showImport").addEventListener("click", () => { goStep("cases"); if (state.caseEdit) resetCaseForm(); $("importPanel").open = true; $("caseName").focus(); });
  $("cancelCaseRevision").addEventListener("click", resetCaseForm);
  $("refreshCases").addEventListener("click", () => { loadCases(); if (state.caseId) openCase(state.caseId); });
  $("selectPage").addEventListener("change", () => { const chosen = state.pickerSelected || state.selected; for (const item of state.cases) { if ($("selectPage").checked) chosen.set(caseId(item), item); else chosen.delete(caseId(item)); } updatePickerSelection(); if (!state.pickerSelected) selectionChanged(); });
  $("previousCases").addEventListener("click", () => { state.offset = Math.max(0, state.offset - state.limit); loadCases(); });
  $("nextCases").addEventListener("click", () => { state.offset += state.limit; loadCases(); });
  $("previewCampaign").addEventListener("click", previewCampaign); $("previewFreeze").addEventListener("click", previewFreeze);
  for (const id of ["campaignName", "campaignStrategy", "campaignRepeats", "campaignTimeout"]) $(id).addEventListener("input", invalidateCampaign);
  $("campaignContract").addEventListener("change", () => { state.frozenDataset = null; state.freezeMembers = null; invalidateCampaign(); invalidateFreeze(); if ($("reviewContract") && !state.reviewEdit) { $("reviewContract").value = $("campaignContract").value; renderReviewCriteria(); } });
  $("datasetName").addEventListener("input", invalidateFreeze);
  $("datasetParent").addEventListener("change", () => {
    state.freezeMembers = null; state.freezeParent = null; invalidateFreeze();
    const parent = state.datasets.find(item => item.id === $("datasetParent").value);
    if (parent) {
      $("datasetName").value = parent.name; $("campaignContract").value = parent.contract_id; $("freezeContract").value = parent.contract_id;
      $("campaignContract").dispatchEvent(new Event("change"));
      $("freezePreview").textContent = `The next version of ${parent.name} will retain its ${parent.members.length} saved cases and exact review choices, then add your selected cases. Preview the complete collection before saving.`;
    } else $("freezePreview").textContent = "Save the selected cases as a new dataset. Preview its contents before saving.";
  });
  $("assessmentMethod").addEventListener("change", () => { $("verifierEndpointLabel").hidden = $("assessmentMethod").value !== "configured_verifier"; });
  $("contractJson").addEventListener("input", () => { $("successCriteria").required = !$("contractJson").value.trim(); });
  function scopeHint() { $("scopeHint").textContent = $("evidenceMode").value === "candidate_output" ? "Output acceptance does not establish observed robot task success." : $("evidenceMode").value === "recorded_episode" ? "This assesses supplied episode evidence from its producing system. Replaying a recording cannot prove a new candidate executed successfully." : "Synthetic rollout evidence demonstrates the evaluation workflow; it is not measured robot performance."; }
  $("evidenceMode").addEventListener("change", scopeHint);
  function caseImage(item) {
    const digest = item.image_asset?.sha256;
    if (!/^[a-f0-9]{64}$/.test(digest || "")) return null;
    const image = node("img"); image.src = `/api/trial-assets/${digest}`; image.alt = `Observation: ${item.name}`; image.loading = "lazy"; image.className = "case-thumbnail"; return image;
  }
  function suggestedExpectation(item) {
    return item.suggested_success?.contract?.criteria?.map(criterion => criterion.description).join("\n") || `Address the instruction: ${item.task}\nGround the response in the supplied observation. Identify missing or ambiguous evidence instead of claiming the robot completed the task.`;
  }
  function renderSelectedCases() {
    const container = $("selectedCases"); container.replaceChildren(); $("selectedCaseCount").textContent = `${state.selected.size} cases`;
    if (!state.selected.size) { const empty = node("div", null, "case-empty"); empty.append(node("h3", "Start with a scene and a task"), node("p", "Add your own observation, or select robotics samples from the case library. Your selected cases will appear here.", "muted")); container.append(empty); return; }
    for (const [id, item] of state.selected) {
      const card = node("article", null, "selected-case-card"), image = caseImage(item), body = node("div", null, "selected-case-body");
      if (image) card.append(image);
      const head = node("div", null, "panel-heading"); head.append(node("h3", item.name), button("Remove", () => { state.selected.delete(id); selectionChanged(); }));
      body.append(head, node("p", item.task, "detail-instruction"));
      const expectation = node("textarea"); expectation.rows = 3; expectation.value = state.expectations.get(id) || suggestedExpectation(item); expectation.setAttribute("aria-label", `Expected outcome for ${item.name}`);
      expectation.addEventListener("input", () => { state.expectations.set(id, expectation.value); state.frozenDataset = null; state.criteriaDirty = true; $("campaignContract").value = ""; invalidateCampaign(); });
      body.append(label("Expected outcome · draft for expert review", expectation), node("p", "Draft criteria are not a review or proof of robot completion. Confirm the scoring rules in Configure; rate actual outputs after running.", "muted"));
      const actions = node("div", null, "compact-actions"); actions.append(button("Inspect & review case", () => { openCase(id, true); }), node("span", `Case revision ${item.revision || 1} · ${item.readiness?.final_review_count || 0} expert reviews`, "badge")); body.append(actions); card.append(body); container.append(card);
    }
  }
  function updatePickerSelection() { const chosen = state.pickerSelected || state.selected; $("pickerSelection").textContent = `${chosen.size} cases selected`; updateSelected(); }
  async function openPicker() {
    state.pickerSelected = new Map(state.selected); state.pickerOpener = document.activeElement;
    if (typeof $("casePicker").showModal === "function") $("casePicker").showModal(); else $("casePicker").setAttribute("open", "");
    $("caseSearch").focus(); updatePickerSelection();
    try { if (!state.galleryReady) { await request("/api/examples"); state.galleryReady = true; } await loadCases(); } catch (error) { $("caseListStatus").textContent = `Unable to load sample library: ${error.message}`; }
  }
  function closePicker() { if (typeof $("casePicker").close === "function") $("casePicker").close(); else $("casePicker").removeAttribute("open"); state.pickerSelected = null; const url = new URL(location.href); url.searchParams.delete("pick"); history.replaceState({}, "", url); state.pickerOpener?.focus(); }
  $("selectExisting").addEventListener("click", openPicker);
  $("closeCasePicker").addEventListener("click", closePicker);
  $("casePicker").addEventListener("cancel", event => { event.preventDefault(); closePicker(); });
  $("confirmCasePicker").addEventListener("click", async () => { state.selected = new Map(state.pickerSelected || state.selected); closePicker(); selectionChanged(); if (state.selected.size && !state.caseId) await openCase(state.selected.keys().next().value, false); tell(`${state.selected.size} cases in this campaign. Review their expected outcomes below.`); });
  let searchTimer;
  $("caseSearch").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.search = $("caseSearch").value.trim(); state.offset = 0; loadCases(); }, 200); });
  $("caseCategory").addEventListener("change", () => { state.category = $("caseCategory").value; state.offset = 0; loadCases(); });
  $("showDatasets").addEventListener("click", () => { $("caseReusePanel").open = true; $("caseReusePanel").scrollIntoView({block: "start"}); });
  function prepareCriteriaDraft() {
    if ((!$("successCriteria").value.trim()) && !$("campaignContract").value) {
      $("successCriteria").value = "Assess this trial against its case instruction and the case-specific expected outcome saved with these scoring rules. Ground the response in the observation, respect task constraints, and make uncertainty explicit. A proposed plan does not establish physical completion.";
      $("contractName").value = `${$("campaignName").value || "Campaign"} scoring rules`; state.criteriaDirty = false;
    }
    renderStrategy();
  }
  function renderStrategy() {
    const strategy = state.strategies.find(item => item.id === $("campaignStrategy").value), container = $("strategySummary"); container.replaceChildren();
    if (!strategy) { container.append(node("p", "Choose a strategy defined in Settings. Its existing stages and model endpoints will run on every selected case.", "muted"), link("Manage strategies in Settings", "/?view=strategies")); return; }
    container.append(node("strong", strategy.display_name || strategy.id), node("p", strategy.description || "The selected strategy supplies the pipeline stages and model endpoints for each trial.", "muted"));
    const configuredSteps = strategy.steps || ["perceive", "plan", "act", "verify"].filter(name => strategy[name]).map(name => ({name, endpoint: strategy[name]}));
    if (configuredSteps.length) { const stages = node("div", null, "pipeline-stages"); for (const step of configuredSteps) stages.append(node("span", `${step.name || step.type || step.id || step.step}${step.endpoint ? ` · ${step.endpoint}` : ""}`, "badge")); container.append(stages); }
    container.append(node("p", "Each repetition creates a new trial with its own stages, trace and outcome. Running stages is not itself a passing assessment.", "muted"));
  }
  $("campaignStrategy").addEventListener("change", renderStrategy);
  $("prepareRun").addEventListener("click", async () => {
    $("prepareRun").disabled = true;
    try {
      if (!$("campaignContract").value) throw new Error("Confirm the scoring rules above, or choose saved scoring rules, before continuing.");
      state.preparingRun = true; $("liveTrialsPanel").hidden = true; $("runLaunchActions").hidden = false; $("runConfirmationHeading").textContent = "Ready to run?"; goStep("run"); await previewCampaign();
    } catch (error) { tell(error.message, true); $("contractPanel").open = true; $("successCriteria").focus(); }
    finally { $("prepareRun").disabled = false; }
  });
  $("contractForm").addEventListener("input", () => { $("campaignContract").value = ""; invalidateCampaign(); });
  $("contractForm").addEventListener("change", () => { $("campaignContract").value = ""; invalidateCampaign(); });
  let liveTimer;
  async function refreshLiveTrials() {
    clearTimeout(liveTimer); if (!state.campaignId || state.step !== "run" || document.hidden) return;
    const id = state.campaignId, version = ++state.liveVersion; $("liveTrialsPanel").hidden = false; $("runConfirmationHeading").textContent = "Campaign configuration"; $("runLaunchActions").hidden = true;
    try {
      const [detail, result] = await Promise.all([request(`/api/campaigns/${encodeURIComponent(id)}`), request(`/api/campaigns/${encodeURIComponent(id)}/assessments`)]);
      if (id !== state.campaignId || version !== state.liveVersion) return;
      const campaign = detail.campaign, summary = result.summary;
      $("liveTrialStatus").textContent = `${campaign.spec.name} · ${campaign.status} · ${summary.completed_trials || 0} / ${summary.planned_trials || 0} trials recorded. Expert-reviewed outcomes remain pending until rated.`;
      const container = $("liveTrials"), expanded = new Set([...container.querySelectorAll("details[open][data-trial-id]")].map(element => element.dataset.trialId)); container.replaceChildren();
      $("runSummary").replaceChildren(node("strong", campaign.spec.name), node("p", `${summary.planned_trials || 0} planned trials across ${campaign.spec.tasks.length} cases.`), node("p", `Strategies: ${(campaign.spec.strategies || []).join(", ")}`), node("p", `Scoring rules: ${campaign.contract?.name || campaign.contract_id || "See campaign report"}`));
      if (campaign.contract) $("runSummary").append(objectDetails("Saved scoring criteria and case expectations", campaign.contract));
      for (const trial of result.trials || []) {
        const task = campaign.spec.tasks.find(item => item.id === trial.task_id), card = node("article", null, "live-trial-card");
        card.append(node("h3", task?.task || task?.name || trial.task_id), node("p", `${trial.strategy_id || "Selected strategy"} · repetition ${Number(trial.seed) + 1} · assessment: ${trial.outcome || "pending"}`, "muted"));
        if (trial.trial_id) { card.append(node("p", `Trial ${trial.trial_id}`, "record-id"), link("Inspect pipeline stages, output & trace →", `/static/history.html?trial=${encodeURIComponent(trial.trial_id)}`));
          const stages = node("details"); stages.dataset.trialId = trial.trial_id; stages.append(node("summary", "Pipeline progress and output")); const content = node("div"); stages.append(content); stages.addEventListener("toggle", () => { if (stages.open) loadTrialProgress(trial.trial_id, content); }); card.append(stages);
        }
        else card.append(node("p", "Queued · trial evidence will appear when execution starts.", "muted"));
        container.append(card); if (trial.trial_id && expanded.has(trial.trial_id)) card.querySelector("details").open = true;
      }
      if (!container.childNodes.length) container.append(node("p", "Preparing trials. This view updates automatically.", "muted"));
      if (["running", "pending", "queued"].includes(campaign.status)) liveTimer = setTimeout(refreshLiveTrials, 2000);
    } catch (error) { $("liveTrialStatus").textContent = `Progress unavailable: ${error.message}. Your campaign continues on the server. Retry with Refresh.`; }
  }
  async function loadTrialProgress(id, container) {
    container.replaceChildren(node("p", "Loading recorded stages…", "muted"));
    try {
      const [trial, page] = await Promise.all([request(`/api/trials/${encodeURIComponent(id)}`), request(`/api/trials/${encodeURIComponent(id)}/events?limit=200`)]);
      if (!container.isConnected) return;
      container.replaceChildren(node("p", `Execution: ${trial.status}. Stage execution and task acceptance are separate measures.`, "muted"));
      const stages = new Map(); for (const event of page.events || []) if (event.event_type?.startsWith("rove.stage.") && event.stage) stages.set(event.stage, event);
      const lane = node("div", null, "pipeline-stages"); for (const [stage, event] of stages) lane.append(node("span", `${stage} · ${event.event_type.split(".").pop()}${event.endpoint_id ? ` · ${event.endpoint_id}` : ""}`, "badge")); container.append(lane);
      if (!stages.size) container.append(node("p", "No stage events recorded yet.", "muted"));
      if (page.total > 200) container.append(node("p", "Showing the first 200 events. Open the full trial trace for all recorded stages.", "muted"));
      if (trial.result) container.append(objectDetails("Recorded output", trial.result));
      container.append(button("Refresh this trial", () => loadTrialProgress(id, container)));
    } catch (error) { container.replaceChildren(node("p", error.message, "error-text"), button("Retry trial progress", () => loadTrialProgress(id, container))); }
  }
  $("refreshLiveTrials").addEventListener("click", refreshLiveTrials);
  document.addEventListener("visibilitychange", () => { if (document.hidden) clearTimeout(liveTimer); else if (state.step === "run") refreshLiveTrials(); });
  window.addEventListener("popstate", () => restoreRoute().catch(error => tell(error.message, true)));
  goStep(new URLSearchParams(location.search).get("step") || (new URLSearchParams(location.search).has("campaign") ? "review" : "cases"), false, false); updateBudget(); loadAssistantStatus();
  Promise.all([loadCases(), loadContracts(), loadDatasets(), loadCampaigns(), loadNamedBaselines(), request("/api/strategies").then(page => { state.strategies = page.strategies; const options = [["", "Choose a strategy"], ...page.strategies.map(item => [item.id, item.display_name || item.id])]; $("campaignStrategy").replaceChildren(...select(null, options).childNodes); $("candidateStrategy").replaceChildren(...select(null, options).childNodes); $("campaignStrategy").value = ""; $("candidateStrategy").value = ""; })]).then(restoreRoute).catch(error => tell(error.message, true));
})();
