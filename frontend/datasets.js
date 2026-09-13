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
  const strategies = input.strategyIds === undefined ? (input.strategyId ? [input.strategyId] : []) : input.strategyIds;
  if (!input.contractId || !Array.isArray(strategies) || !strategies.length) throw new Error("Choose scoring rules and at least one strategy.");
  if (strategies.length > 20) throw new Error("Choose no more than 20 strategies for one campaign.");
  if (strategies.some(id => typeof id !== "string" || !id.trim()) || new Set(strategies).size !== strategies.length) throw new Error("Choose distinct configured strategies.");
  const repeats = Number(input.repeats), timeout = Number(input.timeout);
  if (!Number.isInteger(repeats) || repeats < 1 || repeats > 1000) throw new Error("Repeats must be an integer from 1 to 1000.");
  if (!Number.isFinite(timeout) || timeout < 1 || timeout > 3600) throw new Error("Attempt timeout must be between 1 and 3600 seconds.");
  return {name: input.name.trim(), ...(input.datasetId ? {dataset_revision_id: input.datasetId} : {case_revision_ids: [...input.caseIds]}), contract_id: input.contractId, strategies: [...strategies], seeds: Array.from({length: repeats}, (_, index) => index), ks: [...new Set([1, Math.min(3, repeats), repeats])].sort((a, b) => a - b), timeout_s: timeout};
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
  const initialLocation=location.href;
  state.step = "cases";
  Object.assign(state, {expectations: new Map(), pickerSelected: null, strategies: [], selectedStrategyIds: [], search: "", category: "", liveVersion: 0});
  const stepCopy = {cases: ["What should your agent be able to do?", "A case pairs a task with its observation and test conditions."], configure: ["Choose the strategies to compare", "Use the same cases to compare your pipelines."], metrics: ["Success criteria", "Determined for your campaign. Review or edit before running."], run: ["Run your campaign", "One case × one strategy × one repetition = one trial. Follow each trial, then inspect its evidence."], review: ["Campaign outcomes", "Compare performance, understand the evidence, then inspect any trial."]};
  function writeRoute() {
    const url = new URL(location.href); url.searchParams.set("step", state.step); if(state.campaignId){url.searchParams.delete("trial");url.searchParams.delete("improve");}
    for (const [key, value] of [["campaign", state.campaignId], ["case", state.caseId]]) { if (value) url.searchParams.set(key, value); else url.searchParams.delete(key); }
    if (url.href !== location.href) history.pushState({}, "", url);
  }
  function goStep(step, push = true, focus = true) {
    if (state.step !== step) { tell(""); state.datasetUseVersion = (state.datasetUseVersion || 0) + 1; }
    state.step = Object.hasOwn(stepCopy, step) ? step : "cases"; document.body.dataset.workflowStep = state.step;
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
    $("improvementIntro").hidden = (!state.seedDraft && !state.seedCancelled) || state.step !== "cases";
    if (state.step === "configure") prepareCriteriaDraft();
    if (state.step === "metrics") { prepareCriteriaDraft(); draftMetrics(); }
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

  const savedRules = node("details"); savedRules.append(node("summary", "Use previously saved scoring rules"), $("campaignContract").closest("label")); $("savedMetricsSlot").append(savedRules);
  for (const item of [$("scoringAdvanced"), $("verifierEndpointLabel"), $("scopeHint"), $("contractJson").closest("details"), $("targetMetric").closest("details"), $("saveContract")]) $("moreScoringOptions").append(item);
  $("saveContract").setAttribute("form","contractForm");
  for(const field of $("moreScoringOptions").querySelectorAll("input,select,textarea")) field.setAttribute("form","contractForm");
  for (const id of ["contractName", "successScope", "evidenceMode", "assessmentMethod"]) {$("scoringAdvanced").append($(id).closest("label"));$(id).setAttribute("form","contractForm");}
  for (const anchor of document.querySelectorAll("[data-journey-step]")) anchor.addEventListener("click", event => { if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; event.preventDefault(); goStep(anchor.dataset.journeyStep); });
  for (const control of document.querySelectorAll("[data-go-step]")) control.addEventListener("click", () => goStep(control.dataset.goStep));
  $("reuseReviewed").addEventListener("click", () => { $("caseReusePanel").open = true; $("caseReusePanel").scrollIntoView({block: "start"}); });
  $("defineFreezeContract").addEventListener("click", () => { goStep("metrics"); $("contractPanel").open = true; $("contractName").focus(); });
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
  function renderBaselineReference() {
    const campaign = state.reviewCampaign, strategy = $("baselineStrategy").value;
    const bound = item => item && item.campaign_id === campaign?.id && item.strategy_id === strategy;
    if (!bound(state.namedBaseline)) state.namedBaseline = null;
    if (!state.namedBaseline && !state.baselineNew) state.namedBaseline = state.namedBaselines.find(bound) || null;
    const saved = state.namedBaseline, complete = campaign?.status === "completed" && campaign.spec.strategies.includes(strategy);
    $("openBaselineDialog").disabled=!complete; $("improveCampaign").disabled=campaign?.status!=="completed"; $("currentBaselineLabel").textContent=saved ? `Baseline: ${campaign.strategy_definitions?.[strategy]?.display_name || strategy}` : "";
    $("openBaselineDialog").textContent=saved ? "View baseline" : "Set as baseline";
    $("baselineReferenceBadge").textContent = saved ? "Baseline" : "Not set as baseline";
    $("baselineReferenceBadge").className = `badge${saved ? " baseline-saved" : ""}`;
    $("baselineReferenceSummary").textContent = saved ? `${saved.name} · revision ${saved.revision}. This saved reference preserves the assessments at the time it was set.` : complete ? "Use this completed strategy result as the reference for future comparisons. It becomes a baseline only when you save it." : "Choose a completed campaign before setting its strategy result as a baseline.";
    const identity = $("baselineReferenceIdentity"); identity.replaceChildren();
    if (campaign) identity.append(node("strong", campaign.spec.name), node("span", `Strategy: ${strategy || "Choose a strategy"}`), node("span", `Campaign: ${campaign.id}`, "record-id"));
    if (saved) identity.append(node("span", `Saved revision: ${saved.revision_id}`, "record-id"));
    $("saveNamedBaseline").hidden = !!saved; $("saveNamedBaseline").disabled = !complete || !!state.baselineSaving;
    $("saveBaselineRevision").hidden = !saved; $("saveBaselineRevision").disabled = !complete || !!state.baselineSaving;
    $("namedBaseline").value = saved?.id || "";
    const context = `${campaign?.id || ""}:${strategy}`;
    if (saved && (state.baselineDisplayedRevision !== saved.revision_id || state.baselineNameContext !== context)) { $("namedBaselineName").value = saved.name; $("namedBaselinePinned").checked = saved.pinned; }
    else if (state.baselineNameContext !== context) { $("namedBaselineName").value = campaign ? `${campaign.spec.name} · ${strategy}`.slice(0, 160) : ""; $("namedBaselinePinned").checked = true; }
    state.baselineNameContext = context; state.baselineDisplayedRevision = saved?.revision_id || null;
    $("showComparison").disabled = !saved; $("previewAblation").disabled = !saved;
    const candidate = $("candidateStrategy").value;
    $("baselineComparisonIdentity").replaceChildren(node("strong", saved ? `Baseline: ${saved.name} · revision ${saved.revision}` : "Set or select a baseline before comparing"), node("span", campaign ? `${campaign.spec.name} · ${campaign.id} · strategy ${strategy}` : "Choose a campaign"), node("span", `Candidate strategy: ${candidate || "Choose a candidate below"}`));
    if (saved) $("baselineComparisonIdentity").append(node("span", `Saved revision: ${saved.revision_id}`, "record-id"));
  }
  async function loadNamedBaselines(selectedId) {
    const page = await request("/api/baselines"); state.namedBaselines = page.baselines;
    $("namedBaseline").replaceChildren(...select(null, [["", "Choose a saved baseline"], ...page.baselines.map(row => [row.id, `${row.name} · ${row.strategy_id} · revision ${row.revision}`])], selectedId || "").childNodes);
    if (selectedId) await openNamedBaseline(selectedId); else renderBaselineReference();
  }
  async function openNamedBaseline(id, revisionId) {
    const version = state.baselineLoadVersion = (state.baselineLoadVersion || 0) + 1;
    if (!id) { state.namedBaseline = null; state.baselineNew = true; $("namedBaselineDetails").replaceChildren(); invalidateAblation(); renderBaselineReference(); return; }
    const item = await request(`/api/baselines/${encodeURIComponent(revisionId || id)}`);
    if (version !== state.baselineLoadVersion) return;
    await openCampaign(item.campaign_id);
    if (version !== state.baselineLoadVersion || state.campaignId !== item.campaign_id || !state.reviewCampaign?.spec.strategies.includes(item.strategy_id)) return;
    state.namedBaseline = item; state.baselineNew = false; state.baselineOperation = null;
    $("baselineStrategy").value = item.strategy_id; invalidateAblation(); renderBaselineReference();
    const container = $("namedBaselineDetails"); container.replaceChildren(objectDetails("Saved assessment and provenance", item));
    const page = await request(`/api/baselines/${encodeURIComponent(id)}/revisions`);
    if (version !== state.baselineLoadVersion || state.namedBaseline?.revision_id !== item.revision_id) return;
    const history = select("baselineRevisionHistory", page.revisions.map(row => [row.revision_id, `Revision ${row.revision} · ${row.name} · ${date(row.created_at)}`]), item.revision_id);
    history.addEventListener("change", () => openNamedBaseline(id, history.value).catch(error => tell(error.message, true))); container.append(label("Baseline revision history", history));
  }
  $("namedBaseline").addEventListener("change", () => openNamedBaseline($("namedBaseline").value).catch(error => tell(error.message, true)));
  $("baselineOptions").addEventListener("toggle", () => { if ($("baselineOptions").open && state.namedBaseline && !$("baselineRevisionHistory")) openNamedBaseline(state.namedBaseline.id, state.namedBaseline.revision_id).catch(error => tell(error.message, true)); });
  $("newNamedBaseline").addEventListener("click", () => { state.namedBaseline = null; state.baselineNew = true; state.baselineOperation = null; $("namedBaseline").value = ""; $("namedBaselineName").value = `${state.reviewCampaign?.spec.name || "Campaign"} · ${$("baselineStrategy").value} comparison`.slice(0, 160); $("namedBaselineDetails").replaceChildren(); invalidateAblation(); renderBaselineReference(); $("namedBaselineName").focus(); });
  for (const id of ["namedBaselineName", "namedBaselinePinned", "baselineStrategy"]) $(id).addEventListener("input", () => { state.baselineOperation = null; });
  $("baselineStrategy").addEventListener("change", () => { state.namedBaseline = null; state.baselineNew = false; $("namedBaselineDetails").replaceChildren(); invalidateAblation(); renderBaselineReference(); });
  $("candidateStrategy").addEventListener("change", renderBaselineReference);
  $("namedBaselineForm").addEventListener("submit", async event => {
    event.preventDefault(); state.baselineSaving = true; renderBaselineReference();
    try {
      const campaign = state.reviewCampaign, strategy = $("baselineStrategy").value;
      if (!campaign || campaign.id !== state.campaignId || campaign.status !== "completed" || !campaign.spec.strategies.includes(strategy)) throw new Error("Open a completed campaign and choose one of its recorded strategies first.");
      const current = state.namedBaseline;
      if (current && (current.campaign_id !== campaign.id || current.strategy_id !== strategy)) throw new Error("The selected reference no longer matches this campaign and strategy. Select it again.");
      state.baselineOperation ||= crypto.randomUUID();
      const payload = {name: $("namedBaselineName").value.trim() || `${campaign.spec.name} · ${strategy}`.slice(0, 160), pinned: $("namedBaselinePinned").checked, campaign_id: campaign.id, strategy_id: strategy, operation_id: state.baselineOperation, ...(current ? {expected_head_revision_id: current.revision_id} : {})};
      const result = await post(current ? `/api/baselines/${encodeURIComponent(current.id)}` : "/api/baselines", payload, current ? "PATCH" : "POST");
      state.namedBaselines = [result, ...state.namedBaselines.filter(item => item.id !== result.id)];
      if (state.campaignId === campaign.id && $("baselineStrategy").value === strategy) { state.namedBaseline = result; state.baselineNew = false; renderBaselineReference(); await loadNamedBaselines(result.id); $("baselineDialog").close(); $("namedBaselineStatus").textContent = `Baseline saved: ${result.name}, revision ${result.revision}. Later reviews do not change this reference.`; }
      else await loadNamedBaselines();
    } catch (error) { $("namedBaselineStatus").textContent = error.message; }
    finally { state.baselineSaving = false; renderBaselineReference(); }
  });
  function invalidateCampaign() {
    if (state.confirmedMetricContext && state.confirmedMetricContext !== metricsContextSignature()) {
      $("campaignContract").value = ""; state.confirmedMetricContext = null; state.metricsEdited = true;
      $("metricDraftSource").textContent = "Review updated configuration";
      $("metricDraftStatus").textContent = "Your configuration changed. Review and confirm success metrics again.";
      $("contractPanel").open = true;
    }
    state.draftVersion = (state.draftVersion || 0) + 1; state.campaignPreview = null; state.campaignOperation = null; $("startBaseline").disabled = true; $("campaignMeasures").replaceChildren(); updateBudget(); }
  function invalidateFreeze() { state.freezePreview = null; $("freezeDataset").disabled = true; }
  function updateBudget() {
    const count = state.frozenDataset ? state.frozenDataset.members.filter(item => item.disposition === "included").length : state.selected.size;
    const context = $("runContext"); context.replaceChildren();
    if (state.frozenDataset) context.append(node("strong", `Saved dataset: ${state.frozenDataset.name}`), node("p", `${count} included case revisions · ${state.frozenDataset.id}`, "record-id"));
    else if (state.selected.size) { context.append(node("strong", `${state.selected.size} selected case revisions`)); const list = node("ul"); for (const item of state.selected.values()) list.append(node("li", `${item.name} · revision ${item.revision || caseId(item)}`)); context.append(list); }
    else context.append(node("p", "No cases selected. Return to Cases to select inputs or use a saved dataset.", "notice"));
    const strategies = state.selectedStrategyIds.map(id => state.strategies.find(item => item.id === id)).filter(Boolean), contract = activeContract();
    $("runSummary").replaceChildren(node("strong", $("campaignName").value || "Untitled campaign"), node("p", `${count} cases × ${strategies.length} ${strategies.length === 1 ? "strategy" : "strategies"} × ${Number($("campaignRepeats").value) || 0} repetitions = ${count * strategies.length * (Number($("campaignRepeats").value) || 0)} trials.`), node("p", `${strategies.length === 1 ? "Strategy" : "Strategies"}: ${strategies.map(item => item.display_name || item.id).join(", ") || "Not selected"}`), node("p", `Scoring rules: ${contract?.name || "Not confirmed"}`));
    if (contract) { const rubric = node("details"); rubric.append(node("summary", "Review exact scoring criteria")); for (const criterion of contract.criteria || []) rubric.append(node("p", criterion.description)); for (const [id, value] of Object.entries(contract.case_expectations || {})) { rubric.append(node("h4", state.selected.get(id)?.name || id), node("p", value, "detail-instruction muted")); } $("runSummary").append(rubric); }

    $("continueConfigure").disabled = !count;
    $("freezeContract").value = $("campaignContract").value;
    $("selectionCount").textContent = `${state.selected.size} selected`;
    const repeats = Number($("campaignRepeats").value);
    $("selectedCaseCount").textContent = `${count} cases`;
    $("campaignBudget").textContent = `${count} cases × ${strategies.length} ${strategies.length === 1 ? "strategy" : "strategies"} × ${repeats || 0} repeats = ${count * strategies.length * (repeats || 0)} planned trials${state.frozenDataset ? ` from saved dataset ${state.frozenDataset.name}` : ""}. Preview the available measures before launch.`;
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
    updateBudget(); $("selectionCount").textContent = `${chosen.size} selected`; renderSelectedCases();
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
  function currentCampaign() { const payload=buildCampaign({name: $("campaignName").value, caseIds: [...state.selected.keys()], datasetId: state.frozenDataset?.id, contractId: $("campaignContract").value, strategyIds: [...state.selectedStrategyIds], repeats: $("campaignRepeats").value, timeout: $("campaignTimeout").value});
    if(state.seedDraft) {
      if(!state.seedReady || (state.seedDraft.blockers?.length && !state.seedSelectionReviewed))throw new Error("Resolve the source configuration issues before running.");
      payload.source=state.seedDraft.source;
      if(state.seedBaselineRevision)payload.baseline_revision_id=state.seedBaselineRevision;
      if(Number($("campaignRepeats").value)===state.seedDraft.defaults.seeds.length) {payload.seeds=[...state.seedDraft.defaults.seeds];payload.ks=[...state.seedDraft.defaults.ks];}
    }
    return payload;
  }
  async function previewCampaign() {
    $("previewCampaign").disabled = true; $("startBaseline").disabled = true;
    try {
      const payload = currentCampaign(), signature = JSON.stringify(payload);
      const preview = await post("/api/campaigns/preview", payload);
      if (JSON.stringify(currentCampaign()) !== signature) return;
      state.campaignPreview = {payload, signature, preview}; state.campaignOperation = crypto.randomUUID();
      metricsPreview($("campaignMeasures"), preview);
      if(preview.comparisons) for(const item of preview.comparisons) {const comparison=item.comparison || item;$("campaignMeasures").append(node("p",`${item.strategy_id || item.candidate_strategy_id}: ${comparison.comparable ? "Conditions match the saved baseline." : "Changed assessment conditions: " + (comparison.reasons || []).join("; ")}`,comparison.comparable?"comparison-context":"notice full-width"));}
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
    container.append(link("Run this case in the trial workspace →", `/?view=quick&case=${encodeURIComponent(caseId(item))}`));
    container.append(button("Create case revision", () => {
      goStep("cases"); openNewCase(); state.caseEdit = item; $("caseName").value = item.name; $("caseTask").value = item.task; $("candidateContext").value = JSON.stringify(item.candidate_context || {}, null, 2); $("caseConditions").value = JSON.stringify(item.conditions || {}, null, 2); $("episodeEvidence").value = JSON.stringify(item.recorded_evidence || {}, null, 2); $("referenceData").value = JSON.stringify(item.reference_data || {}, null, 2); $("caseImage").value = ""; $("caseImage").required = false;
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
        const previousSelection = JSON.stringify([...state.selected.keys()]), draftVersion = state.draftVersion || 0; use.disabled = true;
        try {
          const snapshot = await request(`/api/datasets/${encodeURIComponent(dataset.id)}`);
          const cases = await Promise.all(snapshot.members.filter(member => member.disposition === "included").map(member => request(`/api/cases/${encodeURIComponent(member.case_revision_id)}`)));
          const contract = state.contracts.find(item => item.id === snapshot.contract_id);
          if (!contract) throw new Error("The dataset's saved scoring rules are unavailable. Refresh before using these cases.");
          if (version !== state.datasetUseVersion) return;
          if (draftVersion !== (state.draftVersion || 0) || previousSelection !== JSON.stringify([...state.selected.keys()])) throw new Error("Your campaign changed while the dataset loaded. Choose the dataset again to replace the current selection.");
          state.selected = new Map(cases.map(item => [caseId(item), item])); state.pickerSelected = null;
          state.expectations = new Map(cases.map(item => [caseId(item), contract.case_expectations?.[caseId(item)] || contract.criteria.map(criterion => criterion.description).join("\n")]));
          state.frozenDataset = snapshot; state.freezeParent = snapshot; state.freezeMembers = null;
          state.generatedContractId = snapshot.contract_id; state.criteriaDirty = false;
          $("campaignContract").value = snapshot.contract_id; $("freezeContract").value = snapshot.contract_id; state.confirmedMetricContext=null; state.metricDraftContract=null; state.appliedContractId=null; state.metricsEdited=false;
          $("datasetParent").value = snapshot.id; $("datasetName").value = snapshot.name;
          state.caseId = null; state.detail = null; state.detailVersion++; state.reviews = []; state.trials = []; state.reviewEdit = null;
          $("caseInspector").replaceChildren(); $("caseInspection").open = false;
          invalidateCampaign(); invalidateFreeze(); updateSelected(); if ($("casePicker").open) closePicker(); goStep("cases");
          $("freezePreview").textContent = `Using saved dataset ${snapshot.name}. Its exact case and review versions remain unchanged. Editing the selected cases creates a new campaign selection; saving it adds a dataset version.`;
          tell(`Loaded ${cases.length} cases from ${snapshot.name} with their saved scoring rules. You can inspect them in Cases or configure the next campaign.`);
        } catch (error) { tell(error.message, true); }
        finally { if (use.isConnected) use.disabled = false; }
      });
      const card = node("div", null, "dataset-card"); card.append(node("h3", dataset.name), node("p", `${(dataset.members || []).filter(member => member.disposition === "included").length} cases · ${date(dataset.created_at)}`, "muted"), use); $("datasetList").append(card);
    }
  }
  async function loadCampaigns(selectedId, navigate = true) {
    const campaigns = await request("/api/campaigns"); state.campaigns = campaigns;
    const selected = selectedId || $("resultCampaign").value;
    $("resultCampaign").replaceChildren(...select(null, [["", "Choose a campaign"], ...campaigns.map(item => [item.id, `${item.name} · ${item.status}`])], selected).childNodes); $("resultCampaign").value = selected;
    if (selectedId) { await openCampaign(selectedId, navigate); if(navigate) goStep("review"); }
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
    // Refreshing evidence must not replace an explicitly selected historical snapshot
    // with the catalog head. The saved object is immutable and already fetched by ID.
    const retainedBaseline = state.campaignId === id && state.namedBaseline?.campaign_id === id && state.namedBaseline.strategy_id === $("baselineStrategy").value ? state.namedBaseline : null;
    $("resultsPanel").append($("comparisonResults"));
    const version = ++state.campaignVersion; state.campaignId = id; state.reviewCampaign = null; state.baselineNew = false; state.reviewCampaignContract = null; $("resultCampaign").value = id || ""; state.baselineOperation = null; invalidateAblation(); $("comparisonResults").replaceChildren(); $("ablationPanel").hidden = true; $("comparisonAction").hidden = true;
    if (push) { goStep("review", false); writeRoute(); }
    state.namedBaseline = null;
    $("namedBaselineDetails").replaceChildren(); $("namedBaselineStatus").textContent = ""; renderBaselineReference();
    if (!id) { $("campaignResults").replaceChildren(node("p", "Choose a campaign to inspect its assessed outcomes.", "muted")); return; }
    $("campaignResults").replaceChildren(node("p", "Loading authoritative campaign assessments…", "muted"));
    try {
      const [detail, result] = await Promise.all([request(`/api/campaigns/${encodeURIComponent(id)}`), request(`/api/campaigns/${encodeURIComponent(id)}/assessments`)]);
      if (version !== state.campaignVersion) return;
      const campaign = detail.campaign; const selectedOption=[...$("resultCampaign").options].find(option=>option.value===id); if(selectedOption)selectedOption.textContent=`${campaign.spec.name} · ${campaign.status}`; state.reviewCampaign = campaign; state.reviewCampaignContract = campaign.contract_id || null; $("campaignResults").replaceChildren();
      $("campaignResults").append(node("p", `${campaign.spec.name} · ${campaign.status} · ${date(campaign.created_at)}`, "muted"));
      const charts = node("div"); charts.id = "campaignOutcomeCharts"; $("campaignResults").append(charts);
      if (window.RoveCampaignResults) window.RoveCampaignResults.render(charts, result.summary, campaign); else renderSummary(result.summary, charts);
      const analysis = node("section", null, "ai-outcome-summary"); analysis.id = "campaignAiSummary"; $("campaignResults").append(analysis); if (state.step === "review") renderOutcomeSummary(id, analysis, version);
      if (window.RoveCampaignTrajectory) { const timeline=node("section",null,"campaign-timeline-panel"); timeline.id="campaignTimeline"; $("campaignResults").append(timeline); window.RoveCampaignTrajectory.mount(timeline,id); }
      const trialInspection = node("details"); trialInspection.id = "trialInspection"; trialInspection.append(node("summary", `Inspect trials (${result.trials?.length || 0})`));
      const details = node("details"); details.id = "resultDetails"; details.append(node("summary", "Metrics, scoring and provenance")); renderSummary(result.summary, details);
      $("campaignResults").append(trialInspection, details);
      details.append(link("Open full report", `/api/campaigns/${encodeURIComponent(id)}/report?format=html`), objectDetails("Assessment source, coverage and review revisions", result.assessments));
      if (campaign.contract?.criteria?.some(criterion => criterion.assessment === "human_review")) details.append(node("p", "Human-review criteria use expert ratings of each output. A model's success statement does not fill missing reviews.", "notice"));
      for (const trial of result.trials || []) {
        const taskName = campaign.spec.tasks.find(entry => entry.id === trial.task_id)?.task || trial.task_id;
        const item = node("div", null, "compact-actions"); item.append(node("span", `${taskName} · ${trial.strategy_id} · repetition ${Number(trial.seed) + 1}: ${trial.outcome}`, "muted")); if (trial.trial_id) item.append(link("Inspect trial", `/static/history.html?trial=${encodeURIComponent(trial.trial_id)}`));
        const task = campaign.spec.tasks.find(entry => entry.id === trial.task_id); const revision = campaign.case_revision_ids?.find(value => value === trial.task_id) || task?.case_revision_id;
        if (revision) item.append(caseLink("Review case", revision)); trialInspection.append(item);
      }
      $("baselineStrategy").replaceChildren(...select(null, (campaign.spec.strategies || []).map(value => [value, campaign.strategy_definitions?.[value]?.display_name || value])).childNodes);
      if (retainedBaseline && campaign.spec.strategies?.includes(retainedBaseline.strategy_id)) {
        state.namedBaseline = retainedBaseline;
        $("baselineStrategy").value = retainedBaseline.strategy_id;
      } else $("baselineStrategy").value = campaign.spec.strategies?.[0] || "";
      renderBaselineReference();
      const baselineRoute = new URLSearchParams(location.search).get("baseline");
      if (baselineRoute === "setup") { if(!$("baselineDialog").open) $("baselineDialog").showModal(); $("saveNamedBaseline").focus(); }
      else if (baselineRoute && state.baselineRoute !== baselineRoute) { state.baselineRoute = baselineRoute; await openNamedBaseline(baselineRoute); return; }
      $("ablationPanel").hidden = !campaign.contract_id; $("comparisonAction").hidden = !campaign.contract_id;
      if (campaign.baseline) {
        const comparisonPanel=node("details");comparisonPanel.id="baselineComparisonPanel";comparisonPanel.append(node("summary","Compare with saved baseline"));
        const strategy=select("comparisonStrategy",campaign.spec.strategies.map(sid=>[sid,campaign.strategy_definitions?.[sid]?.display_name || sid]),campaign.spec.strategies.find(sid=>sid!==campaign.baseline.strategy_id) || campaign.spec.strategies[0]);
        const content=node("div");comparisonPanel.append(label("Candidate strategy",strategy),content);$("campaignResults").append(comparisonPanel);
        async function updateComparison(){const selected=strategy.value;try{const comparison=await request(`/api/campaigns/${encodeURIComponent(id)}/comparison?candidate_strategy_id=${encodeURIComponent(selected)}`);if(version!==state.campaignVersion || selected!==strategy.value)return;renderComparison(comparison);content.append($("comparisonResults"));}catch(error){content.replaceChildren(node("p",error.message,"error-text"));}}
        strategy.addEventListener("change",updateComparison);await updateComparison();
      }
    } catch (error) { if (version === state.campaignVersion) $("campaignResults").replaceChildren(node("p", error.message, "error-text"), button("Retry results", () => openCampaign(id))); }
  }
  function invalidateAblation() { state.ablationPreview = null; state.ablationOperation = null; $("runAblation").disabled = true; $("ablationPreview").textContent = "Preview to confirm that conditions match and inspect recorded differences."; }
  function ablationPayload() { if (!state.namedBaseline || state.namedBaseline.campaign_id !== state.campaignId || state.namedBaseline.strategy_id !== $("baselineStrategy").value) throw new Error("Set this strategy result as a baseline, or select a saved baseline before comparing."); if (!$("candidateStrategy").value) throw new Error("Choose a candidate strategy to compare with the baseline."); return {baseline_revision_id: state.namedBaseline.revision_id, baseline_strategy_id: $("baselineStrategy").value, candidate_strategy_id: $("candidateStrategy").value, name: $("ablationName").value.trim(), intended_change: $("intendedChange").value.trim()}; }
  $("openBaselineDialog").addEventListener("click",()=>$("baselineDialog").showModal());
  $("closeBaselineDialog").addEventListener("click",()=>$("baselineDialog").close());
  $("improveCampaign").addEventListener("click",()=>{location.href=`/static/datasets.html?improve=${encodeURIComponent(state.campaignId)}`;});
  $("showComparison").addEventListener("click", () => { $("ablationPanel").open = true; $("candidateStrategy").focus(); });
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
      const result = await post(`/api/campaigns/${encodeURIComponent(saved.id)}/ablation`, {...saved.payload, operation_id: state.ablationOperation}); state.preparingRun = false; await loadCampaigns(result.id); goStep("run"); await refreshLiveTrials(); tell(`Comparison campaign started: ${result.planned_trials} planned trials. Review results when execution finishes.`);
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
    event.preventDefault(); if (state.caseSaving) return;
    const controls = [...$("caseForm").querySelectorAll("input, select, textarea, button"), $("closeNewCase")];
    const disabledStates = controls.map(control => [control, control.disabled]);
    let restored = false;
    const restoreControls = () => { if (restored) return; for (const [control, disabled] of disabledStates) control.disabled = disabled; restored = true; };
    const source = state.caseEdit, expectedOutcome = $("newCaseExpectation").value.trim();
    state.caseSaving = true; for (const control of controls) control.disabled = true;
    $("caseSaveStatus").textContent = "Saving this case…";
    try {
      const image = $("caseImage").files[0]; if (!image && !source) throw new Error("Select an observation image."); if (image?.size > 16 * 1024 * 1024) throw new Error("Choose an image no larger than 16 MiB.");
      const metadata = buildCaseMetadata({name: $("caseName").value, task: $("caseTask").value, context: $("candidateContext").value, conditions: $("caseConditions").value, episode: $("episodeEvidence").value, references: $("referenceData").value});
      if (source) metadata.expected_head_revision_id = source.head_revision_id;
      const data = new FormData(); if (image) data.append("image", image); data.append("metadata", JSON.stringify(metadata)); const saved = await request(source ? `/api/cases/${encodeURIComponent(source.case_id)}/revisions` : "/api/cases", {method: "POST", body: data});
      for (const [id, item] of state.selected) if (item.case_id === saved.case_id) state.selected.delete(id);
      state.selected.set(caseId(saved), saved); if (expectedOutcome) state.expectations.set(caseId(saved), expectedOutcome);
      selectionChanged(); await loadCases(); await openCase(caseId(saved), true);
      restoreControls(); state.caseSaving = false; $("importPanel").open = false; closeNewCase(); resetCaseForm(); $("caseSaveStatus").textContent = "";
      tell("Case added to this campaign. Add another case or continue to configuration.");
    } catch (error) { $("caseSaveStatus").textContent = error.message; }
    finally { restoreControls(); state.caseSaving = false; }
  });
  async function saveCurrentContract() {
    if ($("generatedCriteria").querySelector("[data-editing=true]")) throw new Error("Save or cancel your criterion edit before continuing.");
    $("saveContract").disabled = true; const draftVersion = state.draftVersion || 0;
    try {
      let payload = buildContract({json: $("contractJson").value, targetMetric: $("targetMetric").value, targetOperator: $("targetOperator").value, targetThreshold: $("targetThreshold").value, annotationEndpoint: $("annotationEndpoint").value, annotationKey: $("annotationKey").value, annotationTarget: $("annotationTarget").value, name: $("contractName").value, scope: $("successScope").value, evidenceMode: $("evidenceMode").value, assessment: $("assessmentMethod").value, criteria: $("successCriteria").value, endpoint: $("verifierEndpoint").value});
      if (state.metricDraftContract && !$("contractJson").value.trim()) {
        const criteria=[...$("generatedCriteria").querySelectorAll("textarea[data-criterion-id]")].map(input=>{
          const original=state.metricDraftContract.criteria.find(item=>item.id===input.dataset.criterionId);
          const criterion={...original,description:input.value.trim()};
          if (state.gradingOverride) {
            criterion.assessment=$("assessmentMethod").value;
            if(criterion.assessment==="configured_verifier")criterion.endpoint=$("verifierEndpoint").value.trim();else delete criterion.endpoint;
          }
          return criterion;
        });
        payload={...state.metricDraftContract,name:payload.name,scope:payload.scope,evidence_mode:payload.evidence_mode,criteria,metrics:[...new Set([...(state.metricDraftContract.metrics || []), ...payload.metrics])],campaign_targets:state.targetEdited ? payload.campaign_targets || [] : state.metricDraftContract.campaign_targets || [],annotation_bindings:state.bindingEdited ? payload.annotation_bindings || [] : state.metricDraftContract.annotation_bindings || []};
      }
      if (!$("contractJson").value.trim()) payload.case_expectations = Object.fromEntries([...state.selected].map(([id, item]) => [id, state.expectations.get(id) || suggestedExpectation(item)]));
      const contract = await post("/api/success-contracts", payload); state.generatedContractId = contract.id; await loadContracts(contract.id); if ((state.draftVersion || 0) !== draftVersion) { $("campaignContract").value = ""; invalidateCampaign(); tell("Earlier scoring rules were saved, but your draft changed while saving. Confirm the current rules before running."); return; } state.confirmedMetricContext=metricsContextSignature(); state.appliedContractId=contract.id; state.metricsEdited=false; invalidateCampaign(); invalidateFreeze(); state.freezeMembers = null; $("contractPanel").open = true; $("metricDraftSource").textContent="Confirmed success metrics"; $("metricDraftStatus").textContent="Your scoring rules are saved. Continue to review and run."; tell("Scoring rules saved."); return contract;
    } finally { $("saveContract").disabled = false; }
  }
  $("contractForm").addEventListener("submit", async event => { event.preventDefault(); try { await saveCurrentContract(); } catch(error) { tell(error.message,true); } });
  $("baselineForm").addEventListener("submit", async event => {
    event.preventDefault(); $("startBaseline").disabled = true;
    try {
      const preview = state.campaignPreview; if (!preview || preview.signature !== JSON.stringify(currentCampaign()) || !preview.preview.ready) throw new Error("Preview the current configuration before launching.");
      const campaign = await post("/api/campaigns/from-cases", {...preview.payload, operation_id: state.campaignOperation});
      $("campaignStatus").replaceChildren(node("span", `Campaign started: ${campaign.planned_trials} planned trials. `), link("Open report", `/api/campaigns/${encodeURIComponent(campaign.id)}/report?format=html`), document.createTextNode(" · "), link("Inspect recorded trials", `/static/history.html?source=campaign`));
      state.campaignPreview = null; tell("Campaign started. Each complete pipeline execution is saved as a trial below.");
      state.autoResultsCampaign=campaign.id; state.preparingRun = false; await loadCampaigns(campaign.id, false); goStep("run"); await refreshLiveTrials(); $("liveTrialsPanel").scrollIntoView({block: "start"});
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
  $("showImport").addEventListener("click", () => { goStep("cases"); if (state.caseEdit) resetCaseForm(); openNewCase(); $("importPanel").open = true; $("caseName").focus(); });
  $("cancelCaseRevision").addEventListener("click", resetCaseForm);
  $("refreshCases").addEventListener("click", () => { loadCases(); if (state.caseId) openCase(state.caseId); });
  $("selectPage").addEventListener("change", () => { const chosen = state.pickerSelected || state.selected; for (const item of state.cases) { if ($("selectPage").checked) chosen.set(caseId(item), item); else chosen.delete(caseId(item)); } updatePickerSelection(); if (!state.pickerSelected) selectionChanged(); });
  $("previousCases").addEventListener("click", () => { state.offset = Math.max(0, state.offset - state.limit); loadCases(); });
  $("nextCases").addEventListener("click", () => { state.offset += state.limit; loadCases(); });
  $("previewCampaign").addEventListener("click", previewCampaign); $("previewFreeze").addEventListener("click", previewFreeze);
  for (const id of ["campaignName", "campaignRepeats", "campaignTimeout"]) $(id).addEventListener("input", invalidateCampaign);
  $("campaignContract").addEventListener("change", () => { state.frozenDataset = null; const saved=activeContract(); if(saved) { applyMetricContract(saved, false); state.appliedContractId=saved.id; state.confirmedMetricContext=metricsContextSignature(); state.metricsEdited=false; $("metricDraftSource").textContent="Confirmed success metrics"; $("metricDraftStatus").textContent="These saved criteria are selected for your campaign."; } else state.confirmedMetricContext=null; state.frozenDataset = null; state.freezeMembers = null; invalidateCampaign(); invalidateFreeze(); if ($("reviewContract") && !state.reviewEdit) { $("reviewContract").value = $("campaignContract").value; renderReviewCriteria(); } });
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
      const expected = node("details", null, "case-expectation"); expected.append(node("summary", "Expected outcome"), label("Draft criteria", expectation)); body.append(expected);
      const actions = node("div", null, "compact-actions"); actions.append(button("Details", () => { openCase(id, true); }), node("span", `${item.readiness?.final_review_count || 0} expert reviews`, "muted")); body.append(actions); card.append(body); container.append(card);
    }
  }
  function updatePickerSelection() { const chosen = state.pickerSelected || state.selected; $("pickerSelection").textContent = `${chosen.size} cases selected`; updateSelected(); }
  async function openPicker() {
    state.pickerSelected = new Map(state.selected); state.pickerOpener = document.activeElement; pickerView(false);
    if (typeof $("casePicker").showModal === "function") $("casePicker").showModal(); else $("casePicker").setAttribute("open", "");
    $("caseSearch").focus(); updatePickerSelection();
    try { if (!state.galleryReady) { await request("/api/examples"); state.galleryReady = true; } await loadCases(); } catch (error) { $("caseListStatus").textContent = `Unable to load sample library: ${error.message}`; }
  }
  function closePicker() { state.datasetUseVersion = (state.datasetUseVersion || 0) + 1; if (typeof $("casePicker").close === "function") $("casePicker").close(); else $("casePicker").removeAttribute("open"); state.pickerSelected = null; const url = new URL(location.href); url.searchParams.delete("pick"); history.replaceState({}, "", url); state.pickerOpener?.focus(); }
  window.addEventListener("rove:case-imported", event => {
    const item = event.detail; if (!item || !caseId(item)) return;
    state.selected.set(caseId(item), item); selectionChanged(); loadCases();
  });
  $("selectExisting").addEventListener("click", openPicker);
  $("closeCasePicker").addEventListener("click", closePicker);
  $("casePicker").addEventListener("cancel", event => { event.preventDefault(); closePicker(); });
  $("confirmCasePicker").addEventListener("click", async () => { state.selected = new Map(state.pickerSelected || state.selected); closePicker(); selectionChanged(); if (state.selected.size && !state.caseId) await openCase(state.selected.keys().next().value, false); tell(`${state.selected.size} cases in this campaign. Review their expected outcomes below.`); });
  let searchTimer;
  $("caseSearch").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.search = $("caseSearch").value.trim(); state.offset = 0; loadCases(); }, 200); });
  $("caseCategory").addEventListener("change", () => { state.category = $("caseCategory").value; state.offset = 0; loadCases(); });
  function pickerView(collections) {
    state.datasetUseVersion = (state.datasetUseVersion || 0) + 1;
    $("individualCaseLibrary").hidden = collections; $("datasetLibrary").hidden = !collections; $("casePickerActions").hidden = collections;
    $("browseIndividual").setAttribute("aria-pressed", String(!collections)); $("browseCollections").setAttribute("aria-pressed", String(collections));
  }
  $("browseIndividual").addEventListener("click", () => pickerView(false));
  $("browseCollections").addEventListener("click", () => pickerView(true));
  function openNewCase() { const dialog = $("newCaseDialog"); state.newCaseOpener = document.activeElement; if (dialog.showModal) dialog.showModal(); else dialog.setAttribute("open", ""); }
  function closeNewCase() { if (state.caseSaving) return; const dialog = $("newCaseDialog"); if (dialog.close) dialog.close(); else dialog.removeAttribute("open"); state.newCaseOpener?.focus(); }
  $("closeNewCase").addEventListener("click", closeNewCase);
  $("newCaseDialog").addEventListener("cancel", event => { event.preventDefault(); closeNewCase(); });
  function prepareCriteriaDraft() {
    if ((!$("successCriteria").value.trim()) && !$("campaignContract").value) {
      $("successCriteria").value = "Assess this trial against its case instruction and the case-specific expected outcome saved with these scoring rules. Ground the response in the observation, respect task constraints, and make uncertainty explicit. A proposed plan does not establish physical completion.";
      $("contractName").value = `${$("campaignName").value || "Campaign"} scoring rules`; state.criteriaDirty = false;
    }
    renderStrategy();
  }
  function renderStrategy() {
    const container = $("strategyCards"), summary = $("strategySummary");
    container.replaceChildren(); summary.replaceChildren();
    const selectedIds = new Set(state.selectedStrategyIds);
    $("strategySelectionCount").textContent = `${selectedIds.size} selected · up to 20`;
    if (!state.strategies.length) { container.append(node("p", "No strategies are available. Add a pipeline in Settings to start comparing.", "muted")); return; }
    for (const strategy of state.strategies) {
      const card = node("label", null, "campaign-strategy-card"); card.dataset.strategyId = strategy.id;
      card.classList.toggle("selected", selectedIds.has(strategy.id));
      const heading = node("span", null, "strategy-card-heading"), checkbox = node("input");
      checkbox.type = "checkbox"; checkbox.value = strategy.id; checkbox.checked = selectedIds.has(strategy.id);
      checkbox.dataset.strategyId = strategy.id; checkbox.disabled = !checkbox.checked && selectedIds.size >= 20;
      checkbox.setAttribute("aria-label", `Compare ${strategy.display_name || strategy.id}`);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked && !state.selectedStrategyIds.includes(strategy.id)) {
          if (state.selectedStrategyIds.length >= 20) { checkbox.checked = false; tell("Choose no more than 20 strategies for one campaign.", true); return; }
          state.selectedStrategyIds.push(strategy.id);
        } else if (!checkbox.checked) state.selectedStrategyIds = state.selectedStrategyIds.filter(id => id !== strategy.id);
        $("campaignStrategy").value = state.selectedStrategyIds[0] || ""; state.seedSelectionReviewed=true;
        invalidateCampaign(); renderStrategy();
        [...$("strategyCards").querySelectorAll("input")].find(input => input.value === strategy.id)?.focus();
      });
      heading.append(checkbox, node("strong", strategy.display_name || strategy.id)); card.append(heading);
      card.append(node("span", strategy.description || "Saved pipeline configuration", "strategy-card-description"));
      const stages = node("span", null, "strategy-stage-grid");
      for (const stage of ["perceive", "plan", "act", "verify"]) {
        const configured = strategy[stage], endpoint = typeof configured === "string" ? configured : configured?.endpoint;
        const row = node("span", null, "strategy-stage");
        row.append(node("span", stage.charAt(0).toUpperCase() + stage.slice(1)), node("span", endpoint || "Skipped", endpoint ? "strategy-endpoint" : "muted")); stages.append(row);
      }
      card.append(stages);
      if (strategy.pipeline_mode) card.append(node("span", `${strategy.pipeline_mode === "parallel" ? "Parallel" : "Sequential"} pipeline${strategy.sim ? ` · Simulation: ${strategy.sim}` : ""}`, "strategy-card-meta"));
      container.append(card);
    }
    summary.append(node("p", selectedIds.size ? "Every selected strategy runs on every case with the same repetitions and scoring rules. Each attempt becomes a separate trial for comparison." : "Choose one or more strategies. A campaign can compare models, complete agents and pipeline revisions on the same cases.", "muted"));
  }
  // Compatibility for saved tooling that sets the original single-selection control.
  function legacyStrategySelection() { state.seedSelectionReviewed=true;
    state.selectedStrategyIds = $("campaignStrategy").value ? [$("campaignStrategy").value] : [];
    invalidateCampaign(); renderStrategy();
  }
  $("campaignStrategy").addEventListener("input", legacyStrategySelection);
  $("campaignStrategy").addEventListener("change", legacyStrategySelection);
  async function refreshStrategyCatalog(newId, selectCandidateOnly = false) {
    const version = state.strategyCatalogVersion = (state.strategyCatalogVersion || 0) + 1;
    const page = await request("/api/strategies");
    if (version !== state.strategyCatalogVersion) return;
    if (!Array.isArray(page.strategies)) throw new Error("Configured strategies could not be loaded.");
    const candidate = $("candidateStrategy").value;
    state.strategies = page.strategies;
    const ids = new Set(state.strategies.map(item => item.id));
    state.selectedStrategyIds = state.selectedStrategyIds.filter(id => ids.has(id));
    if(newId)state.seedSelectionReviewed=true;
    if (newId && ids.has(newId) && !selectCandidateOnly && !state.selectedStrategyIds.includes(newId)) {
      if (state.selectedStrategyIds.length >= 20) tell("Revision saved. Deselect a strategy before adding it to this campaign.", true);
      else state.selectedStrategyIds.push(newId);
    }
    const options = [["", "Choose a strategy"], ...state.strategies.map(item => [item.id, item.display_name || item.id])];
    $("campaignStrategy").replaceChildren(...select(null, options).childNodes);
    $("candidateStrategy").replaceChildren(...select(null, options).childNodes);
    $("campaignStrategy").value = state.selectedStrategyIds[0] || "";
    $("candidateStrategy").value = selectCandidateOnly && newId && ids.has(newId) ? newId : ids.has(candidate) ? candidate : "";
    invalidateCampaign(); invalidateAblation(); renderStrategy(); renderBaselineReference();
    window.dispatchEvent(new CustomEvent("rove:strategy-catalog-updated"));
  }
  function metricsContextPayload() {
    const repeats = Number($("campaignRepeats").value);
    return {name: $("campaignName").value.trim(), ...(state.frozenDataset ? {dataset_revision_id: state.frozenDataset.id} : {case_revision_ids: [...state.selected.keys()]}), strategies: [...state.selectedStrategyIds], seeds: state.seedDraft && repeats===state.seedDraft.defaults.seeds.length ? [...state.seedDraft.defaults.seeds] : Array.from({length: Number.isInteger(repeats) && repeats > 0 && repeats <= 1000 ? repeats : 0}, (_, index) => index), timeout_s: Number($("campaignTimeout").value), case_expectations: Object.fromEntries([...state.expectations].filter(([id]) => state.selected.has(id)))};
  }
  function metricsContextSignature() { return JSON.stringify([metricsContextPayload(), state.strategies.filter(item => state.selectedStrategyIds.includes(item.id))]); }
  function applyMetricContract(contract, preserveExpectations = true) {
    state.metricDraftContract = JSON.parse(JSON.stringify(contract));
    for (const key of ["id", "sha256", "created_at"]) delete state.metricDraftContract[key];
    state.gradingOverride=false; state.targetEdited=false; state.bindingEdited=false;
    const target=contract.campaign_targets?.[0], binding=contract.annotation_bindings?.[0];
    $("targetMetric").value=target?.metric || ""; $("targetOperator").value=target?.operator || "gte"; $("targetThreshold").value=target?.threshold ?? "";
    $("annotationEndpoint").value=binding?.endpoint || ""; $("annotationKey").value=binding?.annotation_key || ""; $("annotationTarget").value=binding?.target_key || "";
    $("contractName").value = contract.name; $("successScope").value = contract.scope; $("evidenceMode").value = contract.evidence_mode;
    $("assessmentMethod").value = contract.criteria[0]?.assessment || "human_review";
    $("verifierEndpoint").value = contract.criteria[0]?.endpoint || "";
    $("successCriteria").value = contract.criteria[0]?.description || ""; $("contractJson").value = "";
    const container = $("generatedCriteria"); container.replaceChildren();
    for (const [index,criterion] of (contract.criteria || []).entries()) {
      const card=node("div",null,"suggested-criterion"), row=node("div",null,"criterion-row"), copy=node("p",criterion.description,"criterion-copy"), number=node("span",index+1,"criterion-number");
      const input=node("textarea"); input.rows=3; input.value=criterion.description; input.maxLength=2000; input.dataset.criterionId=criterion.id; input.setAttribute("aria-label",`Criterion ${index+1}`);
      const editor=node("div",null,"criterion-editor");editor.hidden=true;editor.append(input);
      const edit=button("Edit",()=>{card.dataset.editing="true";editor.hidden=false;row.hidden=true;input.focus();});edit.setAttribute("aria-label",`Edit criterion ${index+1}`);
      function closeEdit(){delete card.dataset.editing;editor.hidden=true;row.hidden=false;edit.focus();}
      editor.append(button("Save change",()=>{if(!input.value.trim()){input.setCustomValidity("Describe this criterion.");input.reportValidity();return;}input.setCustomValidity("");copy.textContent=input.value.trim();input.value=copy.textContent;input.dispatchEvent(new Event("change",{bubbles:true}));closeEdit();}),button("Cancel",()=>{input.value=copy.textContent;closeEdit();}));
      row.append(number,copy,edit);card.append(row,editor,node("small",criterion.assessment==="configured_verifier"?`Verified by ${criterion.endpoint}`:"Expert review","muted"));container.append(card);
    }
    $("successCriteria").closest("label").hidden = true;
    for (const [id, expectation] of Object.entries(contract.case_expectations || {})) if (state.selected.has(id) && (!preserveExpectations || !state.expectations.has(id))) state.expectations.set(id, expectation);
    const cases = node("details"); cases.append(node("summary", "Case-specific expected outcomes"));
    for (const [id,item] of state.selected) {
      const input=node("textarea");input.rows=2;input.value=state.expectations.get(id)||suggestedExpectation(item);input.dataset.expectationId=id;input.setAttribute("aria-label",`Expected outcome for ${item.name}`);
      input.addEventListener("input",()=>state.expectations.set(id,input.value));cases.append(label(item.name,input));
    }
    if (state.selected.size) container.append(cases);
    const measures=$("suggestedMeasures"); measures.replaceChildren();
    for (const metric of contract.metrics || []) { const card=node("div",null,"suggested-measure");card.append(node("strong",metricLabel(metric)),node("span",metric==="pipeline_latency"?"Recorded automatically":"Uses the confirmed assessment"));measures.append(card); }
    const needsReview=(contract.criteria || []).some(criterion=>criterion.assessment==="human_review");
    $("gradingNotice").textContent=needsReview ? "Expert review is required for these criteria. Acceptance stays pending until the required ratings are recorded." : "Configured verification stages assess these criteria. Missing or invalid evidence remains unassessed.";
    scopeHint(); $("verifierEndpointLabel").hidden=$("assessmentMethod").value!=="configured_verifier";
  }
  async function draftMetrics(force = false) {
    const context=metricsContextPayload(), signature=metricsContextSignature();
    $("metricsContext").textContent=`${state.selected.size} cases · ${state.selectedStrategyIds.length} strategies · ${context.seeds.length} repeats`;
    if (!state.selected.size || !context.strategies.length || !context.seeds.length) { $("metricDraftStatus").textContent="Choose cases and at least one strategy before drafting success metrics."; return; }
    if (!force && $("campaignContract").value) {
      const saved=activeContract(); if(saved && state.appliedContractId !== saved.id) { applyMetricContract(saved, false); state.appliedContractId=saved.id; } state.confirmedMetricContext=metricsContextSignature();
      $("metricDraftSource").textContent="Confirmed success metrics";$("metricDraftStatus").textContent="These saved criteria will be used for this campaign. Regenerate to propose a change.";return;
    }
    if(!force && (state.metricDraftContext===signature || state.metricPendingSignature===signature))return;
    if(!force && state.metricsEdited){$("metricDraftStatus").textContent="Your edits are preserved. Regenerate suggestions to reconsider them for this configuration.";return;}
    const version=state.metricDraftVersion=(state.metricDraftVersion || 0)+1, draftVersion=state.draftVersion || 0;
    state.metricPendingSignature=signature;$("regenerateMetrics").disabled=true;$("metricDraftSource").textContent="Preparing suggestions";$("metricDraftStatus").textContent="Considering your tasks, selected strategies and available evidence…";
    try {
      const result=await post("/api/campaigns/metrics-draft",context);
      if(version!==state.metricDraftVersion)return;
      if(signature!==metricsContextSignature() || draftVersion!==(state.draftVersion||0)){ $("metricDraftStatus").textContent="Your configuration changed. Generate suggestions for the current selection.";return; }
      applyMetricContract(result.contract); state.metricsEdited=false;state.metricDraftContext=metricsContextSignature();state.metricDraftEvidence=result.evidence_fingerprint;
      $("campaignContract").value="";invalidateCampaign();
      $("metricDraftSource").textContent=result.source==="ai"?"Agent-drafted success metrics":(result.assistant_configured ?? result.assistant_available)?"Suggested metrics":"Suggested metrics · AI unavailable";
      $("metricDraftStatus").textContent=result.source==="ai"?result.rationale:`${(result.assistant_configured ?? result.assistant_available) ? "AI suggestions could not be generated." : "The assistant is not connected."} Review these task-based suggestions before confirming.`;
      if(result.source==="ai")$("metricDraftStatus").append(node("span"," Review these criteria against the observations before confirming."));
      $("contractPanel").open=true;
    } catch(error){if(version===state.metricDraftVersion){$("metricDraftSource").textContent="Suggestions unavailable";$("metricDraftStatus").textContent=`Could not prepare suggestions: ${error.message}. You can edit and confirm criteria below.`;}}
    finally{if(version===state.metricDraftVersion){state.metricPendingSignature=null;$("regenerateMetrics").disabled=false;}}
  }
  $("continueMetrics").addEventListener("click",()=>goStep("metrics"));
  $("regenerateMetrics").addEventListener("click",()=>draftMetrics(true));
  async function renderOutcomeSummary(id, container, version) {
    container.replaceChildren(node("span","Interpreting results","ai-source"),node("h3","What do these results mean?"),node("p","Preparing a summary from recorded outcomes and trial evidence…","muted"));
    try {
      const result=await post(`/api/campaigns/${encodeURIComponent(id)}/outcome-summary`,{});
      if(version!==state.campaignVersion || !container.isConnected)return;
      container.replaceChildren(node("span",result.source==="ai"?"AI outcome summary":(result.assistant_configured ?? result.assistant_available)?"Recorded outcome summary":"Recorded outcome summary · AI unavailable","ai-source"),node("h3",result.headline));
      const list=node("ul");
      for(const finding of result.findings || []) {
        const item=node("li",finding.text);
        for(const trialId of finding.trial_ids || [])item.append(document.createTextNode(" "),link("Inspect evidence",`/static/history.html?trial=${encodeURIComponent(trialId)}`));
        list.append(item);
      }
      container.append(list);
      if(result.next_steps?.length){const next=node("details");next.append(node("summary","Suggested next steps"));for(const action of result.next_steps)next.append(node("p",typeof action==="string"?action:action.text));container.append(next);}
      if(result.source!=="ai")container.append(node("p",result.warnings?.some(warning=>warning.includes("not completed")) ? "AI interpretation will be available after the campaign finishes." : (result.assistant_configured ?? result.assistant_available) ? "AI interpretation could not be generated. Showing recorded outcomes." : "The assistant is not connected. Showing recorded outcomes.","chart-note"));
      if(result.source==="ai")container.append(node("p","AI interpretation of saved aggregates and trial outcomes. Inspect the evidence before acting on suggestions.","chart-note"));
      if(result.warnings?.length) { const details=node("details"); details.append(node("summary","Assistant status")); for(const warning of result.warnings)details.append(node("p",warning)); container.append(details); }
      container.append(button("Refresh summary",()=>renderOutcomeSummary(id,container,version)));
    } catch(error){if(version===state.campaignVersion && container.isConnected)container.replaceChildren(node("h3","AI summary unavailable"),node("p","Your measured outcomes are shown above. The assistant could not prepare a summary.","muted"),button("Retry summary",()=>renderOutcomeSummary(id,container,version)));}
  }

  window.refreshStrategyCatalog = refreshStrategyCatalog;
  $("prepareRun").addEventListener("click", async () => {
    $("prepareRun").disabled = true;
    try {
      if ($("generatedCriteria").querySelector("[data-editing=true]")) throw new Error("Save or cancel your criterion edit before continuing.");
      if (!$("campaignContract").value) { if (!await saveCurrentContract()) return; }
      state.preparingRun = true; $("liveTrialsPanel").hidden = true; $("runLaunchActions").hidden = false; $("runConfirmationHeading").textContent = "Ready to run?"; goStep("run"); await previewCampaign();
    } catch (error) { goStep("metrics"); tell(error.message, true); $("contractPanel").open = true; ($("generatedCriteria").querySelector("textarea") || $("successCriteria")).focus(); }
    finally { $("prepareRun").disabled = false; }
  });
  for (const id of ["assessmentMethod", "verifierEndpoint"]) $(id).addEventListener("change", () => { state.gradingOverride=true;
    $("gradingNotice").textContent=$("assessmentMethod").value==="human_review" ? "All criteria will use expert review. Acceptance stays pending until the required ratings are recorded." : "All criteria will use the selected verification endpoint. Missing or invalid evidence remains unassessed.";
    for(const description of $("generatedCriteria").querySelectorAll(".suggested-criterion small")) description.textContent=$("assessmentMethod").value==="human_review" ? "Expert review" : `Verified by ${$("verifierEndpoint").value || "selected endpoint"}`;
  });
  for (const id of ["targetMetric", "targetOperator", "targetThreshold"]) for (const type of ["input","change"]) $(id).addEventListener(type,()=>{state.targetEdited=true;});
  for (const id of ["annotationEndpoint", "annotationKey", "annotationTarget"]) for (const type of ["input","change"]) $(id).addEventListener(type,()=>{state.bindingEdited=true;});
  $("contractForm").addEventListener("input", () => { state.metricsEdited = true; $("campaignContract").value = ""; invalidateCampaign(); });
  $("contractForm").addEventListener("change", () => { state.metricsEdited = true; $("campaignContract").value = ""; invalidateCampaign(); });
  for(const type of ["input","change"]) $("moreScoringOptions").addEventListener(type,event=>{if(event.target.id==="campaignContract")return;state.metricsEdited=true;$("campaignContract").value="";invalidateCampaign();});
  let liveTimer;
  async function loadCampaignStageEvents(row) {
    if (!row.trial_id) return;
    let cache = state.progressEvents.get(row.trial_id);
    if (!cache) { cache = {events: [], stageEvents: new Map(), offset: 0, total: 0, error: ""}; state.progressEvents.set(row.trial_id, cache); }
    if (cache.loading) return cache.loading;
    cache.loading = (async () => {
    try {
      let pages = 0;
      do {
        const page = await request(`/api/trials/${encodeURIComponent(row.trial_id)}/events?limit=200${cache.offset ? `&offset=${cache.offset}` : ""}`);
        if (!Array.isArray(page.events)) throw new Error("Stage events unavailable");
        for (const event of page.events) if (event.event_type === "stage" && event.data?.stage) cache.stageEvents.set(`${event.data.phase || ""}:${event.data.stage}`, event);
        cache.events = [...cache.stageEvents.values()]; cache.offset += page.events.length; cache.total = page.total || cache.offset; cache.error = ""; pages += 1;
        if (!page.events.length) break;
      } while (cache.offset < cache.total && pages < 3);
    } catch (error) { cache.error = error.message || "Stage updates unavailable"; }
    finally { cache.loading = null; }
    })();
    return cache.loading;
  }
  function renderCampaignProgress(campaign, trials) {
    if (!window.RoveCampaignProgress) return;
    const eventPages = Object.fromEntries(state.progressEvents || []);
    const lanes = window.RoveCampaignProgress.buildStrategyProgress(campaign, trials, eventPages);
    window.RoveCampaignProgress.render($("strategyProgress"), lanes, id => {
      const entry = state.liveTrialCards?.get(id); if (!entry?.stages) return; $("liveTrialDetails").open=true;
      entry.stages.open = true; entry.card.scrollIntoView({block: "start", behavior: "smooth"}); entry.stages.querySelector("summary")?.focus();
    });
  }
  async function refreshLiveTrials() {
    clearTimeout(liveTimer); if (!state.campaignId || state.step !== "run" || state.preparingRun || document.hidden) return;
    const id = state.campaignId, version = ++state.liveVersion; $("liveTrialsPanel").hidden = false; $("runConfirmationHeading").textContent = "Campaign configuration"; $("runLaunchActions").hidden = true;
    try {
      const [detail, result] = await Promise.all([request(`/api/campaigns/${encodeURIComponent(id)}`), request(`/api/campaigns/${encodeURIComponent(id)}/assessments`)]);
      if (id !== state.campaignId || version !== state.liveVersion) return;
      const campaign = detail.campaign, summary = result.summary;
      if (["running","pending","queued","cancelling"].includes(campaign.status)) state.autoResultsCampaign=id;
      $("workspaceTitle").textContent = "Campaign trials";
      $("liveTrialStatus").textContent = `${campaign.spec.name} · ${campaign.status} · ${summary.completed_trials || 0} / ${summary.planned_trials || 0} trials recorded. Expert-reviewed outcomes remain pending until rated.`;
      const container = $("liveTrials");
      if (state.liveCardCampaign !== id) { state.liveCardCampaign = id; state.liveTrialCards = new Map(); state.progressEvents = new Map(); container.replaceChildren(); }
      $("runSummary").replaceChildren(node("strong", campaign.spec.name), node("p", `${summary.planned_trials || 0} planned trials across ${campaign.spec.tasks.length} cases.`), node("p", `Strategies: ${(campaign.spec.strategies || []).map(id => campaign.strategy_definitions?.[id]?.display_name || campaign.config?.strategies?.[id]?.display_name || id).join(", ")}`), node("p", `Scoring rules: ${campaign.contract?.name || campaign.contract_id || "See campaign report"}`));
      if (campaign.contract) $("runSummary").append(objectDetails("Saved scoring criteria and case expectations", campaign.contract));
      const strategyName = id => campaign.strategy_definitions?.[id]?.display_name || campaign.config?.strategies?.[id]?.display_name || id;
      const currentKeys = new Set(); let position = 0;
      for (const trial of result.trials || []) {
        const task = campaign.spec.tasks.find(item => item.id === trial.task_id), key = trial.trial_id || `${trial.task_id}:${trial.strategy_id}:${trial.seed}`;
        currentKeys.add(key); let entry = state.liveTrialCards.get(key);
        if (!entry) {
          const card = node("article", null, "live-trial-card"), title = node("h3"), detail = node("p", null, "muted"); card.append(title, detail);
          entry = {card, title, detail, task};
          if (trial.trial_id) {
            card.append(node("p", `Trial ${trial.trial_id}`, "record-id"), link("Inspect full trace and evidence →", `/static/history.html?trial=${encodeURIComponent(trial.trial_id)}`));
            const stages = node("details"); stages.dataset.trialId = trial.trial_id; stages.append(node("summary", "Pipeline progress and output")); const content = node("div"); stages.append(content);
            entry.stages = stages; entry.content = content; stages.addEventListener("toggle", () => { if (stages.open) loadTrialProgress(trial.trial_id, content, entry.task); }); card.append(stages);
          } else card.append(node("p", "Queued · trial evidence will appear when execution starts.", "muted"));
          state.liveTrialCards.set(key, entry);
        }
        entry.task = task; entry.title.textContent = strategyName(trial.strategy_id) || "Strategy trial";
        const repeatIndex = (campaign.spec.seeds || []).indexOf(trial.seed);
        entry.detail.textContent = `${task?.task || task?.name || trial.task_id} · ${repeatIndex >= 0 ? `repetition ${repeatIndex + 1}` : `seed ${trial.seed}`} · assessment: ${trial.outcome || "pending"}`;
        if (container.children[position] !== entry.card) container.insertBefore(entry.card, container.children[position] || null);
        position += 1;
        if (entry.stages?.open) loadTrialProgress(trial.trial_id, entry.content, task);
      }
      for (const [key, entry] of state.liveTrialCards) if (!currentKeys.has(key)) { entry.card.remove(); state.liveTrialCards.delete(key); }
      for (const empty of container.querySelectorAll(":scope > .live-trials-empty")) empty.remove();
      if (!container.childNodes.length) container.append(node("p", "Preparing trials. This view updates automatically.", "muted live-trials-empty"));
      renderCampaignProgress(campaign, result.trials || []);
      const active = (result.trials || []).filter(trial => trial.execution === "running" && trial.trial_id);
      if (active.length) {
        await Promise.all(active.map(loadCampaignStageEvents));
        if (id !== state.campaignId || version !== state.liveVersion || state.step !== "run") return;
        renderCampaignProgress(campaign, result.trials || []);
      }
      if (["running", "pending", "queued", "cancelling"].includes(campaign.status)) liveTimer = setTimeout(refreshLiveTrials, 2000);
      else if(state.autoResultsCampaign===id && state.step==="run" && !document.hidden) { state.autoResultsCampaign=null; goStep("review"); }
    } catch (error) { $("liveTrialStatus").textContent = `Progress unavailable: ${error.message}. Your campaign continues on the server. Retry with Refresh.`; }
  }
  async function loadTrialProgress(id, container, task) {
    const version = container.dataset.loadVersion = String(Number(container.dataset.loadVersion || 0) + 1);
    if (!container.childNodes.length) container.append(node("p", "Loading recorded stages…", "muted"));
    try {
      const [trial, firstPage] = await Promise.all([request(`/api/trials/${encodeURIComponent(id)}`), request(`/api/trials/${encodeURIComponent(id)}/events?limit=200`)]);
      const page = firstPage.total > 200 ? await request(`/api/trials/${encodeURIComponent(id)}/events?limit=200&offset=${Math.max(0, firstPage.total - 200)}`) : firstPage;
      const revision = task?.case_revision_id || trial.task?.case_revision_id;
      let caseRecord = revision ? state.selected.get(revision) : null;
      if (revision && !caseRecord) { try { caseRecord = await request(`/api/cases/${encodeURIComponent(revision)}`); } catch { /* Stage output stays available when the observation cannot load. */ } }
      if (!container.isConnected || container.dataset.loadVersion !== version) return;
      const signature = JSON.stringify([trial.status, trial.result, trial.error, page.events, caseRecord?.id]);
      if (container.dataset.renderSignature === signature) return;
      container.dataset.renderSignature = signature;
      if (window.RoveTrialOutput) window.RoveTrialOutput.render(container, {...trial, id}, page.events || [], {caseRecord, task});
      else { container.replaceChildren(node("p", "The trial output viewer could not load. Refresh this page, or open the full trial trace.", "error-text")); }
      if (page.total > 200) container.append(node("p", "Showing the latest 200 events plus all saved stage outputs. Open the full trial trace for earlier events.", "muted"));
      container.append(button("Refresh this trial", () => loadTrialProgress(id, container, task)));
    } catch (error) { if (container.isConnected && container.dataset.loadVersion === version) container.replaceChildren(node("p", error.message, "error-text"), button("Retry trial progress", () => loadTrialProgress(id, container, task))); }
  }
  function renderImprovementIntro(seed) {
    const panel=$("improvementIntro");panel.replaceChildren();panel.hidden=state.step!=="cases";
    panel.classList.toggle("trial-seed-note",seed.source.type==="trial");
    if(seed.source.type==="trial") {
      panel.append(node("p","Case, robot and strategy added from your trial."),link("View source trial",`/static/history.html?trial=${encodeURIComponent(seed.source.id)}`));
      if(seed.warnings?.length){const details=node("details");details.append(node("summary","Saved input details"));for(const warning of seed.warnings)details.append(node("p",warning));panel.append(details);}
      for(const blocker of seed.blockers || [])panel.append(node("p",blocker,"error-text"));return;
    }
    panel.append(node("span",seed.source.type==="trial"?"FROM YOUR TRIAL":"IMPROVE A CAMPAIGN","ai-source"),node("h2",seed.source.type==="trial"?"Build on this trial":"Where to improve"));
    panel.append(node("p",seed.source.type==="trial"?"Your case, robot and strategy are ready. Add cases or strategies, then choose success criteria.":"Start from the same cases and settings. Test one change at a time to see what helped.","muted"));
    const advice=node("div"); advice.id="improvementAdvice";
    for(const item of (seed.recommendations || []).slice(0,3)) {
      const card=node("article",null,"improvement-suggestion");card.append(node("p",item.text));
      const sourceStrategy=seed.source_strategies?.find(row=>row.id===item.strategy_id);const selectedStrategy=sourceStrategy?.selected_id || (seed.strategies.includes(item.strategy_id)?item.strategy_id:null);
      if(item.source==="recorded_failure" && selectedStrategy && ["perceive","plan","act","verify"].includes(item.stage)) card.append(button(`Test a ${item.stage} change`,()=>{goStep("configure");$("campaignStrategy").value=selectedStrategy;$("createCampaignStrategyRevision").click();}));
      for(const trial of (item.trial_ids || []).slice(0,2))card.append(link(item.source==="missing_assessment"?"Review trial evidence":"Inspect trial",`/static/history.html?trial=${encodeURIComponent(trial)}`));
      advice.append(card);
    }
    panel.append(advice);
    if(seed.source.type==="campaign") {
      if(seed.baselines?.length) {
        const chosen=select("improvementBaseline",[["","No saved reference"],...seed.baselines.map(item=>[item.revision_id,`${item.name} · revision ${item.revision || 1}`])],state.seedBaselineRevision || "");
        chosen.addEventListener("change",()=>{state.seedBaselineRevision=chosen.value || null;invalidateCampaign();});panel.append(label("Compare against baseline",chosen),node("p","Recommendations use current reviews. Comparisons use the saved baseline assessments.","comparison-context"));
      } else panel.append(node("p","No baseline is saved yet. This campaign will be a new result you can compare later.","muted"));
      const comparison=node("p","Keep cases, scoring and repetitions fixed for a controlled ablation. Adding cases measures expanded coverage.","comparison-context");comparison.id="improvementComparisonNote";panel.append(comparison);
      panel.append(link("View original results",`/static/datasets.html?step=review&campaign=${encodeURIComponent(seed.source.id)}`));
      const contents=node("section",null,"improvement-ai");contents.append(node("p","Preparing an interpretation of the recorded outcomes…","muted"));panel.append(contents);
      post(`/api/campaigns/${encodeURIComponent(seed.source.id)}/outcome-summary`,{}).then(result=>{
        if(!contents.isConnected)return;
        contents.replaceChildren(node("span",result.source==="ai"?"AI recommendations":"Recorded findings","ai-source"),node("h3",result.headline));
        for(const finding of (result.findings || []).slice(0,3)) { const item=node("p",finding.text); for(const id of finding.trial_ids || [])item.append(document.createTextNode(" "),link("Inspect trial",`/static/history.html?trial=${encodeURIComponent(id)}`));contents.append(item); }
        if(result.source==="ai")for(const step of (result.next_steps || []).slice(0,3))contents.append(node("p",step));
        else contents.append(node("p","AI interpretation is unavailable; these findings use saved measurements.","muted"));
      }).catch(()=>{if(contents.isConnected)contents.replaceChildren(node("p","Interpretation could not load. The recorded suggestions above remain available."));});
    }
    for(const warning of seed.warnings || [])panel.append(node("p",warning,"muted"));
    for(const blocker of seed.blockers || [])panel.append(node("p",blocker,"error-text"));
  }
  async function loadSeedDraft(type,id) {
    const panel=$("improvementIntro"); panel.hidden=false;panel.replaceChildren(node("h2","Preparing your campaign…"));
    const version=state.draftVersion || 0, route=location.href, loadVersion=state.seedLoadVersion=(state.seedLoadVersion || 0)+1;
    try {
      let operation;
      if(type==="trial") { const key=`rove:campaign-draft:${id}`;try{operation=sessionStorage.getItem(key);if(!operation){operation=crypto.randomUUID();sessionStorage.setItem(key,operation);}}catch{operation=crypto.randomUUID();} }
      const seed=await post(type==="trial"?`/api/trials/${encodeURIComponent(id)}/campaign-draft`:`/api/campaigns/${encodeURIComponent(id)}/improvement-draft`,type==="trial"?{operation_id:operation}:{});
      const [cases,dataset]=await Promise.all([Promise.all(seed.case_revision_ids.map(revision=>request(`/api/cases/${encodeURIComponent(revision)}`))),seed.dataset_revision_id?request(`/api/datasets/${encodeURIComponent(seed.dataset_revision_id)}`):null]);
      if(loadVersion!==state.seedLoadVersion || route!==location.href || version!==(state.draftVersion || 0))throw new Error("Your draft or navigation changed while loading. Retry when ready to replace it with the saved inputs.");
      const beforeRefresh=state.draftVersion || 0; await refreshStrategyCatalog();
      if(loadVersion!==state.seedLoadVersion || route!==location.href || (state.draftVersion || 0)!==beforeRefresh+1)throw new Error("Your draft changed while loading. Retry to use the saved configuration.");
      document.body.dataset.seeded="true";state.seedDraft=seed;state.seedBaselineRevision=seed.baselines?.length===1?seed.baselines[0].revision_id:null;state.seedSelectionReviewed=false;state.seedCancelled=false;state.campaignId=null;state.confirmedMetricContext=null;state.metricDraftContract=null;state.appliedContractId=null;state.metricsEdited=false;state.selected=new Map(cases.map(item=>[caseId(item),item]));state.expectations=new Map();
      state.selectedStrategyIds=[...seed.strategies];$("campaignStrategy").value=seed.strategies[0] || "";
      $("campaignName").value=seed.defaults.name;$("campaignRepeats").value=seed.defaults.seeds.length;$("campaignTimeout").value=seed.defaults.timeout_s;
      state.frozenDataset=dataset;
      if(seed.contract) { if(!state.contracts.some(item=>item.id===seed.contract.id))state.contracts.push(seed.contract);$("campaignContract").replaceChildren(...select(null,contractOptions(),seed.contract_id).childNodes);$("campaignContract").value=seed.contract_id;applyMetricContract(seed.contract,false);state.appliedContractId=seed.contract_id; }
      else $("campaignContract").value="";
      invalidateCampaign();updateSelected();renderStrategy();goStep("cases");renderImprovementIntro(seed);
      state.seedReady=true;
    } catch(error) { if(loadVersion!==state.seedLoadVersion)return; state.seedReady=false;state.seedCancelled=true;panel.hidden=state.step!=="cases"; panel.replaceChildren(node("h2","Could not prepare this campaign"),node("p",error.message,"error-text"),button("Retry",()=>loadSeedDraft(type,id))); }
  }
  async function initialRoute() {
    const query=new URLSearchParams(location.search);
    if((query.has("trial") || query.has("improve")) && location.href!==initialLocation) {
      state.seedCancelled=true;const panel=$("improvementIntro");panel.hidden=state.step!=="cases";panel.replaceChildren(node("p","Continue from your saved inputs when you are ready."),button("Load saved inputs",()=>loadSeedDraft(query.has("trial")?"trial":"campaign",query.get("trial") || query.get("improve"))));return;
    }
    if(query.has("trial"))return loadSeedDraft("trial",query.get("trial"));
    if(query.has("improve"))return loadSeedDraft("campaign",query.get("improve"));
    return restoreRoute();
  }

  $("refreshLiveTrials").addEventListener("click", refreshLiveTrials);
  document.addEventListener("visibilitychange", () => { if (document.hidden) clearTimeout(liveTimer); else if (state.step === "run") refreshLiveTrials(); });
  window.addEventListener("popstate", () => restoreRoute().catch(error => tell(error.message, true)));
  goStep(new URLSearchParams(location.search).get("step") || (new URLSearchParams(location.search).has("campaign") ? "review" : "cases"), false, false); updateBudget(); loadAssistantStatus();
  Promise.all([loadCases(), loadContracts(), loadDatasets(), loadCampaigns(), loadNamedBaselines(), refreshStrategyCatalog()]).then(initialRoute).catch(error => tell(error.message, true));
})();
