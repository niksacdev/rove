"use strict";

function parseObject(text, label) {
  if (!text.trim()) return {};
  let value;
  try { value = JSON.parse(text); } catch { throw new Error(`${label} must be valid JSON.`); }
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} must be a JSON object.`);
  return value;
}

function buildContract(input) {
  if (input.json?.trim()) return parseObject(input.json, "Success contract");
  const criterion = {id: "task_acceptance", description: input.criteria.trim(), assessment: input.assessment, required: true};
  if (input.assessment === "configured_verifier") {
    if (!input.endpoint?.trim()) throw new Error("Choose the configured verifier endpoint for this criterion.");
    criterion.endpoint = input.endpoint.trim();
  }
  if (!criterion.description) throw new Error("Describe the acceptance criterion before saving.");
  return {name: input.name.trim(), scope: input.scope, evidence_mode: input.evidenceMode, criteria: [criterion], metrics: ["task_success", "pass_at_k", "pass_pow_k", "pipeline_latency"]};
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
  if (!input.caseIds.length && !input.datasetId) throw new Error("Select at least one case or a frozen dataset.");
  if (!input.contractId || !input.strategyId) throw new Error("Choose a success contract and strategy.");
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

if (typeof module !== "undefined" && module.exports) module.exports = {parseObject, buildContract, buildReview, buildCampaign, metricLabel, activeReviewIds, assessedCounts, metricValue, pairedTrialId, evidenceHref};

if (typeof document !== "undefined") (() => {
  const $ = id => document.getElementById(id);
  const state = {cases: [], selected: new Map(), contracts: [], datasets: [], caseId: null, detail: null, reviews: [], trials: [], offset: 0, limit: 25, listVersion: 0, detailVersion: 0, contractId: "", frozenDataset: null, campaignPreview: null, campaignOperation: null, freezePreview: null, freezeMembers: null, reviewEdit: null};
  Object.assign(state, {campaigns: [], campaignId: null, campaignVersion: 0, ablationPreview: null, ablationOperation: null, caseEdit: null});
  const node = (tag, text, className) => { const element = document.createElement(tag); if (text != null) element.textContent = String(text); if (className) element.className = className; return element; };
  const objectDetails = (title, data) => { const element = node("details"); element.append(node("summary", title), node("pre", JSON.stringify(data ?? null, null, 2))); return element; };
  const link = (text, href) => { const element = node("a", text); element.href = href; return element; };
  const date = value => value && Number.isFinite(new Date(value).getTime()) ? new Date(value).toLocaleString() : "Time not recorded";
  const caseId = item => item.case_revision_id || item.id;
  const select = (id, options, value) => { const element = node("select"); if (id) element.id = id; for (const [key, text] of options) { const option = node("option", text); option.value = key; element.append(option); } if (value != null) element.value = value; return element; };
  const label = (text, control) => { const element = node("label", text); element.append(control); return element; };
  const button = (text, action, style = "secondary") => { const element = node("button", text, style); element.type = "button"; element.addEventListener("click", action); return element; };
  function tell(text, error = false) { $("message").textContent = text; $("message").className = error ? "error-text" : "notice"; }
  function contractOptions() { return [["", "Choose a success contract"], ...state.contracts.map(item => [item.id, item.name])]; }
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
      $("assistantStatus").textContent = status.available ? "Uses your configured Copilot model to inspect and preview evaluations. Launch proposals require your explicit confirmation." : status.reason || "The optional assistant is not configured. All evaluation controls below remain available.";
      $("assistantQuestion").disabled = !status.available; $("askAssistant").disabled = !status.available;
    } catch { $("assistantStatus").textContent = "The optional assistant is unavailable. Use the case, campaign and review controls below."; }
  }
  $("assistantForm").addEventListener("submit", async event => {
    event.preventDefault(); $("askAssistant").disabled = true; $("assistantResponse").replaceChildren(node("p", "Inspecting the selected evaluation context…", "muted"));
    try {
      const context = {};
      if (state.selected.size > 100) throw new Error("The assistant accepts up to 100 selected cases. Narrow the selection or use a frozen dataset.");
      if (state.selected.size) context.case_revision_ids = [...state.selected.keys()];
      if (state.frozenDataset) context.dataset_revision_id = state.frozenDataset.id;
      if ($("campaignContract").value) context.contract_id = $("campaignContract").value;
      if (state.campaignId) context.campaign_id = state.campaignId;
      const result = await post("/api/assistant/ask", {message: $("assistantQuestion").value.trim(), context});
      $("assistantResponse").replaceChildren(node("p", result.answer || "No answer was returned.", "assistant-answer"));
      const evidence = node("div", null, "assistant-evidence");
      for (const item of result.evidence || []) { const href = evidenceHref(item.url); if (href) evidence.append(link(item.label || "Inspect evidence", href)); }
      $("assistantResponse").append(evidence);
      for (const proposal of result.proposals || []) {
        const card = node("div", null, "review-card"); card.append(node("h4", proposal.request?.name || "Proposed evaluation"));
        if (proposal.kind !== "launch_campaign") { card.append(node("p", "This proposal type is not supported by this interface.", "muted")); $("assistantResponse").append(card); continue; }
        card.append(node("p", `${proposal.preview?.planned_trials ?? "Unknown number of"} planned attempts. Review the exact cases, strategy and success contract before confirming.`, "notice"));
        const metrics = node("div", null, "metric-preview"); metricsPreview(metrics, proposal.preview || {}); card.append(metrics, objectDetails("Exact proposed configuration", proposal.request));
        for (const blocker of proposal.preview?.blockers || []) card.append(node("p", blocker, "error-text"));
        const confirm = button("Confirm campaign launch", async () => {
          confirm.disabled = true;
          try {
            const launched = await post("/api/assistant/confirm", {operation_id: proposal.operation_id, confirmation_token: proposal.confirmation_token, confirmed: true});
            confirm.remove(); card.append(node("p", `Campaign created: ${launched.planned_trials} planned attempts.`), link("Open report", `/api/campaigns/${encodeURIComponent(launched.id)}/report?format=html`)); await loadCampaigns(launched.id);
          } catch (error) { tell(`${error.message} If the preview changed, ask the assistant to prepare it again.`, true); confirm.disabled = false; }
        }, "");
        confirm.disabled = !proposal.preview?.ready || !proposal.operation_id || !proposal.confirmation_token; card.append(confirm); $("assistantResponse").append(card);
      }
    } catch (error) { $("assistantResponse").replaceChildren(node("p", error.message, "error-text")); }
    finally { $("askAssistant").disabled = false; }
  });
  function invalidateCampaign() { state.campaignPreview = null; state.campaignOperation = null; $("startBaseline").disabled = true; $("campaignMeasures").replaceChildren(); updateBudget(); }
  function invalidateFreeze() { state.freezePreview = null; $("freezeDataset").disabled = true; }
  function updateBudget() {
    const count = state.frozenDataset ? state.frozenDataset.members.filter(item => item.disposition === "included").length : state.selected.size;
    $("selectionCount").textContent = `${state.selected.size} selected`;
    const repeats = Number($("campaignRepeats").value);
    $("campaignBudget").textContent = `${count} cases × ${repeats || 0} repeats = ${count * (repeats || 0)} planned attempts${state.frozenDataset ? ` from frozen dataset ${state.frozenDataset.name}` : ""}. Preview the available measures before launch. Configured model calls may incur charges.`;
  }
  function selectionChanged() { state.frozenDataset = null; state.freezeMembers = null; invalidateCampaign(); invalidateFreeze(); $("freezePreview").textContent = "Selection changed. Preview exact case and review revisions again."; updateSelected(); }
  function updateSelected() {
    for (const input of $("caseList").querySelectorAll("input")) input.checked = state.selected.has(input.dataset.caseId);
    const onPage = state.cases.filter(item => state.selected.has(caseId(item))).length;
    $("selectPage").checked = state.cases.length > 0 && onPage === state.cases.length;
    $("selectPage").indeterminate = onPage > 0 && onPage < state.cases.length;
    for (const anchor of $("caseList").querySelectorAll("a")) {
      if (anchor.dataset.caseId === state.caseId) anchor.setAttribute("aria-current", "true"); else anchor.removeAttribute("aria-current");
    }
    updateBudget();
  }
  async function loadCases() {
    const version = ++state.listVersion;
    $("caseListStatus").textContent = "Loading cases…"; $("previousCases").disabled = true; $("nextCases").disabled = true;
    try {
      const page = await request(`/api/cases?limit=${state.limit}&offset=${state.offset}`);
      if (version !== state.listVersion) return;
      state.cases = page.cases; $("caseList").replaceChildren();
      for (const item of state.cases) {
        const row = node("li", null, "case-list-item"), checkbox = node("input"); checkbox.type = "checkbox"; checkbox.dataset.caseId = caseId(item); checkbox.setAttribute("aria-label", `Select ${item.name}`);
        checkbox.addEventListener("change", () => { if (checkbox.checked) state.selected.set(caseId(item), item); else state.selected.delete(caseId(item)); selectionChanged(); });
        const anchor = link("", `/static/datasets.html?case=${encodeURIComponent(caseId(item))}`); anchor.className = "case-link"; anchor.dataset.caseId = caseId(item);
        anchor.append(node("strong", item.name), node("span", item.task, "muted"), node("span", `Revision ${item.revision || "unknown"} · ${item.readiness?.assessment || "Unreviewed"}`, "badge"));
        anchor.addEventListener("click", event => { if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; event.preventDefault(); openCase(caseId(item), true); });
        row.append(checkbox, anchor); $("caseList").append(row);
      }
      $("caseListStatus").textContent = state.cases.length ? "Cases retain their original inputs and revision identity." : "No cases yet. Add an image and task or try the sample cases.";
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
      $("campaignBudget").textContent = `${preview.planned_trials} planned attempts. ${preview.ready ? "Configuration is ready." : "Resolve the blockers before running."}`;
      for (const blocker of preview.blockers || []) $("campaignMeasures").append(node("p", typeof blocker === "string" ? blocker : JSON.stringify(blocker), "error-text full-width"));
      $("startBaseline").disabled = !preview.ready;
    } catch (error) { tell(error.message, true); } finally { $("previewCampaign").disabled = false; }
  }
  function renderCase(item) {
    const container = $("caseInspector"); container.replaceChildren();
    const title = node("h2", item.name); title.id = "caseDetailHeading"; title.tabIndex = -1;
    container.append(title, node("p", item.task, "detail-instruction"), node("p", `Revision ${item.revision || "unknown"} · ${date(item.created_at)}`, "muted"));
    if (item.image_asset && /^[a-f0-9]{64}$/.test(item.image_asset.sha256 || "")) {
      const load = button("Load observation", () => { load.disabled = true; const image = node("img"); image.className = "observation-image"; image.alt = `Observation for ${item.name}`; image.addEventListener("load", () => load.remove()); image.addEventListener("error", () => { image.remove(); load.disabled = false; load.textContent = "Image unavailable — retry"; }); image.src = `/api/trial-assets/${item.image_asset.sha256}`; load.after(image); }); container.append(load);
    }
    container.append(objectDetails("Inputs, evidence and provenance", item));
    container.append(button("Create case revision", () => {
      state.caseEdit = item; $("caseName").value = item.name; $("caseTask").value = item.task; $("candidateContext").value = JSON.stringify(item.candidate_context || {}, null, 2); $("caseConditions").value = JSON.stringify(item.conditions || {}, null, 2); $("episodeEvidence").value = JSON.stringify(item.recorded_evidence || {}, null, 2); $("caseImage").value = ""; $("caseImage").required = false;
      $("caseEditStatus").textContent = `Creating a new revision from ${caseId(item)}. Existing trials and frozen datasets keep their original inputs. A replacement image is optional.`;
      $("saveCase").textContent = "Save new revision"; $("cancelCaseRevision").hidden = false; $("importPanel").open = true; $("caseName").focus();
    }));
    const outputSection = node("section", null, "inspector-section"); outputSection.append(node("h3", "Baseline and candidate outputs"));
    if (!state.trials.length) outputSection.append(node("p", "No recorded trial outputs for this case yet. Select it and run a baseline below.", "muted"));
    for (const trial of state.trials) {
      const card = node("div", null, "output-card"); card.append(node("h4", trial.strategy?.name || trial.strategy?.id || "Strategy attempt"), node("p", `${trial.status} · ${date(trial.created_at)}`, "muted"), link("Inspect output and evidence", `/static/history.html?trial=${encodeURIComponent(trial.id)}`), objectDetails("Saved output", trial.result));
      if (trial.status !== "running" && trial.result != null) card.append(button("Rate this output", () => { resetReview(); $("reviewTarget").value = "trial_output"; reviewTargetChanged(); $("reviewTrial").value = trial.id; $("reviewerName").focus(); }));
      outputSection.append(card);
    }
    if (state.trials.length === 100) outputSection.append(node("p", "Showing the first 100 recorded trials. Use Trial history for older attempts.", "muted"));
    container.append(outputSection);
    const reviewSection = node("section", null, "inspector-section"); reviewSection.append(node("h3", "Expert review"), node("p", "Case validity, reusable annotations and ratings of particular outputs are separate records. A failed output can still be a useful case.", "muted"));
    const form = node("form"); form.id = "reviewForm";
    const target = select("reviewTarget", [["case_validity", "Case validity — is this test usable?"], ["case_annotation", "Reusable annotation — expected properties"], ["trial_output", "Trial output — rate this agent's result"]]);
    form.append(label("Review target", target), label("Success contract", select("reviewContract", contractOptions(), $("campaignContract").value)));
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
    const reviews = node("div", null, "review-list"); reviews.id = "reviewList"; reviewSection.append(reviews); container.append(reviewSection);
    $("reviewContract").addEventListener("change", renderReviewCriteria); renderReviewCriteria(); renderReviews();
  }
  function reviewTargetChanged() { $("reviewTrialLabel").hidden = $("reviewTarget").value !== "trial_output"; $("reviewAnnotationsLabel").hidden = $("reviewTarget").value !== "case_annotation"; renderReviewCriteria(); }
  function renderReviewCriteria(values = {}) {
    if (!$("reviewCriteria")) return;
    $("reviewCriteria").replaceChildren();
    if ($("reviewTarget").value !== "trial_output") return;
    const contract = state.contracts.find(item => item.id === $("reviewContract").value);
    for (const criterion of contract?.criteria || []) {
      const input = select(null, [["unknown", "Unknown"], ["accepted", "Accepted"], ["rejected", "Rejected"]], values[criterion.id] || "unknown"); input.dataset.criterionId = criterion.id;
      $("reviewCriteria").append(label(`${criterion.description}${criterion.required ? " (required)" : ""}`, input));
    }
  }
  function resetReview() { state.reviewEdit = null; $("reviewForm").reset(); for (const id of ["reviewTarget", "reviewContract", "reviewTrial", "reviewerName"]) $(id).disabled = false; $("reviewContract").value = $("campaignContract").value; $("reviewEditStatus").textContent = "New review. Reviewer names record attribution, not authenticated identity."; reviewTargetChanged(); }
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
    if (push) { const url = new URL(location.href); url.searchParams.set("case", id); history.pushState({}, "", url); }
    updateSelected(); $("caseInspector").setAttribute("aria-busy", "true"); $("caseInspector").replaceChildren(node("p", "Loading case and review history…", "muted"));
    try {
      const [item, reviews, trials] = await Promise.all([request(`/api/cases/${encodeURIComponent(id)}`), request(`/api/reviews?case_revision_id=${encodeURIComponent(id)}`), request(`/api/trials?case_revision_id=${encodeURIComponent(id)}&limit=100`)]);
      if (version !== state.detailVersion) return;
      state.detail = item; state.reviews = reviews.reviews; state.trials = trials.trials; renderCase(item);
      if (push) $("caseDetailHeading").focus();
    } catch (error) { if (version === state.detailVersion) $("caseInspector").replaceChildren(node("h2", "Case unavailable"), node("p", error.message, "error-text"), button("Try again", () => openCase(id))); }
    finally { if (version === state.detailVersion) $("caseInspector").setAttribute("aria-busy", "false"); }
  }
  async function loadDatasets() {
    const page = await request("/api/datasets?limit=100"); state.datasets = page.datasets; $("datasetList").replaceChildren();
    const previous = $("datasetParent").value; $("datasetParent").replaceChildren(...select(null, [["", "First revision"], ...state.datasets.map(item => [item.id, `${item.name} · ${item.id.slice(0, 10)}`])], previous).childNodes); $("datasetParent").value = previous;
    if (!state.datasets.length) $("datasetList").append(node("p", "No frozen datasets yet. Each revision preserves exact case and review membership.", "muted"));
    for (const dataset of state.datasets) {
      const card = node("div", null, "dataset-card"); card.append(node("h4", dataset.name), node("p", `${dataset.readiness || "Unknown readiness"} · ${date(dataset.created_at)}`, "muted"), node("p", dataset.sha256, "record-id"), objectDetails("Frozen membership and provenance", dataset), button("Use for next campaign", () => { state.frozenDataset = dataset; $("campaignContract").value = dataset.contract_id; invalidateCampaign(); $("baselinePanel").scrollIntoView({behavior: "auto", block: "start"}); tell(`Using frozen dataset ${dataset.name}. Preview the next campaign before launch.`); })); $("datasetList").append(card);
    }
  }
  async function loadCampaigns(selectedId) {
    const campaigns = await request("/api/campaigns"); state.campaigns = campaigns;
    const selected = selectedId || $("resultCampaign").value;
    $("resultCampaign").replaceChildren(...select(null, [["", "Choose a campaign"], ...campaigns.map(item => [item.id, `${item.name} · ${item.status}`])], selected).childNodes); $("resultCampaign").value = selected;
    if (selectedId) await openCampaign(selectedId);
  }
  function renderSummary(summary, container, heading) {
    if (heading) container.append(node("h3", heading));
    const counts = assessedCounts(summary), grid = node("div", null, "summary-grid");
    for (const [name, count] of [["Passed assessment", counts.passed], ["Failed assessment", counts.failed], ["Unknown or pending", counts.unknown]]) { const card = node("div", null, "summary-item"); card.append(node("span", name, "summary-label"), node("strong", count, "summary-value")); grid.append(card); } container.append(grid);
    container.append(node("p", `${summary.completed_trials ?? 0} recorded / ${summary.planned_trials ?? 0} planned attempts. Unknown outcomes remain in the planned denominator.`, "muted"));
    const metrics = node("details"); metrics.append(node("summary", "Reliability, latency and metric details"), node("p", "pass@k estimates at least one success; pass^k estimates all k succeeding. Unresolved ranges are bounds from missing outcomes, not confidence intervals or robot deployment guarantees.", "muted"));
    for (const strategy of summary.strategies || []) {
      metrics.append(node("h4", strategy.strategy_id));
      const table = node("table"), head = node("tr"); for (const text of ["Measure", "k", "Estimate or unresolved range"]) head.append(node("th", text)); const thead = node("thead"); thead.append(head); const body = node("tbody");
      for (const measure of ["pass_at_k", "pass_pow_k"]) for (const point of strategy[measure] || []) { const row = node("tr"); row.append(node("td", metricLabel(measure)), node("td", point.k), node("td", metricValue(point))); body.append(row); }
      table.append(thead, body); const scroll = node("div", null, "table-scroll"); scroll.append(table); metrics.append(scroll, node("p", `Pipeline latency p95: ${strategy.latency_p95_ms == null ? "unavailable" : `${strategy.latency_p95_ms} ms`}. This is not robot task completion time.`, "muted"));
    }
    container.append(metrics);
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
      const card = node("div", null, "output-card"); card.append(link(`Case ${item.case_id}`, `/static/datasets.html?case=${encodeURIComponent(item.case_id)}`), node("p", `${item.status.replaceAll("_", " ")} · ${item.planned_pairs} planned pairs · ${((item.paired_coverage || 0) * 100).toFixed(1)}% assessed pairing coverage`, "muted"));
      const rows = node("details"); rows.append(node("summary", "Paired attempts and evidence"));
      for (const attempt of item.attempts || []) { const line = node("p", `Seed ${attempt.seed}: ${attempt.baseline} → ${attempt.candidate}. `, "muted"); const baselineId = pairedTrialId(comparison, item, attempt, "baseline"), candidateId = pairedTrialId(comparison, item, attempt, "candidate"); if (baselineId) line.append(link("Baseline trial", `/static/history.html?trial=${encodeURIComponent(baselineId)}`), document.createTextNode(" · ")); if (candidateId) line.append(link("Candidate trial", `/static/history.html?trial=${encodeURIComponent(candidateId)}`)); rows.append(line); }
      card.append(rows); container.append(card);
    }
    container.append(objectDetails("Exact comparison and assessment revisions", comparison));
  }
  async function openCampaign(id) {
    const version = ++state.campaignVersion; state.campaignId = id; invalidateAblation(); $("comparisonResults").replaceChildren(); $("ablationPanel").hidden = true;
    if (!id) { $("campaignResults").replaceChildren(node("p", "Choose a campaign to inspect its assessed outcomes.", "muted")); return; }
    $("campaignResults").replaceChildren(node("p", "Loading authoritative campaign assessments…", "muted"));
    try {
      const [detail, result] = await Promise.all([request(`/api/campaigns/${encodeURIComponent(id)}`), request(`/api/campaigns/${encodeURIComponent(id)}/assessments`)]);
      if (version !== state.campaignVersion) return;
      const campaign = detail.campaign; $("campaignResults").replaceChildren();
      $("campaignResults").append(node("p", `${campaign.spec.name} · ${campaign.status} · ${date(campaign.created_at)}`, "muted"));
      renderSummary(result.summary, $("campaignResults"));
      $("campaignResults").append(link("Open full report", `/api/campaigns/${encodeURIComponent(id)}/report?format=html`), objectDetails("Assessment source, coverage and review revisions", result.assessments));
      if (campaign.contract?.criteria?.some(criterion => criterion.assessment === "human_review")) $("campaignResults").append(node("p", "Human-review criteria use expert ratings of each output. A model's success statement does not fill missing reviews.", "notice"));
      for (const trial of result.trials || []) {
        const item = node("div", null, "compact-actions"); item.append(node("span", `${trial.task_id} · ${trial.strategy_id} · seed ${trial.seed}: ${trial.outcome}`, "muted")); if (trial.trial_id) item.append(link("Inspect trial", `/static/history.html?trial=${encodeURIComponent(trial.trial_id)}`));
        const task = campaign.spec.tasks.find(entry => entry.id === trial.task_id); const revision = campaign.case_revision_ids?.find(value => value === trial.task_id) || task?.case_revision_id;
        if (revision) item.append(link("Review case", `/static/datasets.html?case=${encodeURIComponent(revision)}`)); $("campaignResults").append(item);
      }
      $("baselineStrategy").replaceChildren(...select(null, (campaign.spec.strategies || []).map(value => [value, value])).childNodes);
      $("baselineStrategy").value = campaign.spec.strategies?.[0] || "";
      $("ablationPanel").hidden = !campaign.contract_id;
      if (campaign.baseline) { const comparison = await request(`/api/campaigns/${encodeURIComponent(id)}/comparison`); if (version === state.campaignVersion) renderComparison(comparison); }
    } catch (error) { if (version === state.campaignVersion) $("campaignResults").replaceChildren(node("p", error.message, "error-text"), button("Retry results", () => openCampaign(id))); }
  }
  function invalidateAblation() { state.ablationPreview = null; state.ablationOperation = null; $("runAblation").disabled = true; $("ablationPreview").textContent = "Preview to confirm that conditions match and inspect recorded differences."; }
  function ablationPayload() { if (!state.campaignId || !$("baselineStrategy").value || !$("candidateStrategy").value) throw new Error("Choose the baseline and candidate strategies."); return {baseline_strategy_id: $("baselineStrategy").value, candidate_strategy_id: $("candidateStrategy").value, name: $("ablationName").value.trim(), intended_change: $("intendedChange").value.trim()}; }
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
      const result = await post(`/api/campaigns/${encodeURIComponent(saved.id)}/ablation`, {...saved.payload, operation_id: state.ablationOperation}); await loadCampaigns(result.id); tell(`Candidate campaign started: ${result.planned_trials} planned attempts. Refresh results after execution or expert review.`);
    } catch (error) { tell(error.message, true); $("runAblation").disabled = !state.ablationPreview?.preview.ready || !state.ablationPreview?.preview.comparison.comparable; }
  });
  for (const id of ["baselineStrategy", "candidateStrategy", "ablationName", "intendedChange"]) $(id).addEventListener("input", invalidateAblation);
  $("resultCampaign").addEventListener("change", () => openCampaign($("resultCampaign").value));
  $("refreshCampaigns").addEventListener("click", async () => { try { await loadCampaigns(); if ($("resultCampaign").value) await openCampaign($("resultCampaign").value); } catch (error) { tell(error.message, true); } });
  async function loadFreezeMembers() {
    const contractId = $("campaignContract").value, ids = [...state.selected.keys()];
    if (!ids.length || !contractId) throw new Error("Select cases and the success contract before freezing.");
    const members = await Promise.all(ids.map(async id => { const page = await request(`/api/reviews?case_revision_id=${encodeURIComponent(id)}&limit=1000`); const reviews = page.reviews.filter(review => review.status === "final" && review.contract_id === contractId); return {case_revision_id: id, disposition: "included", reason: "", reviews, review_ids: activeReviewIds(reviews)}; }));
    if (contractId !== $("campaignContract").value || JSON.stringify(ids) !== JSON.stringify([...state.selected.keys()])) throw new Error("Selection changed. Preview the new selection again.");
    state.freezeMembers = members; renderFreezeMembers();
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
  function freezePayload() { return {name: $("datasetName").value.trim(), parent_id: $("datasetParent").value || null, contract_id: $("campaignContract").value, members: (state.freezeMembers || []).map(({reviews, ...member}) => member)}; }
  async function previewFreeze() {
    $("previewFreeze").disabled = true; invalidateFreeze();
    try {
      if (!state.freezeMembers) await loadFreezeMembers();
      const payload = freezePayload(), signature = JSON.stringify(payload), preview = await post("/api/datasets/preview", payload);
      if (JSON.stringify(freezePayload()) !== signature) return;
      state.freezePreview = {payload, signature, hash: preview.preview_hash};
      $("freezeResult").replaceChildren(node("p", `Readiness: ${preview.readiness}. Review the preserved coverage and any issues before freezing.`), objectDetails("Coverage and exact preview", preview));
      for (const issue of preview.issues || []) $("freezeResult").append(node("p", typeof issue === "string" ? issue : JSON.stringify(issue), "muted"));
      $("freezeDataset").disabled = !preview.preview_hash;
    } catch (error) { tell(error.message, true); } finally { $("previewFreeze").disabled = false; }
  }
  $("caseForm").addEventListener("submit", async event => {
    event.preventDefault(); $("saveCase").disabled = true;
    try {
      const image = $("caseImage").files[0]; if (!image && !state.caseEdit) throw new Error("Select an observation image."); if (image?.size > 16 * 1024 * 1024) throw new Error("Choose an image no larger than 16 MiB.");
      const metadata = {name: $("caseName").value.trim(), task: $("caseTask").value.trim(), candidate_context: parseObject($("candidateContext").value, "Candidate context"), conditions: parseObject($("caseConditions").value, "Conditions"), recorded_evidence: parseObject($("episodeEvidence").value, "Recorded evidence")};
      if (state.caseEdit) metadata.expected_head_revision_id = state.caseEdit.head_revision_id;
      const data = new FormData(); if (image) data.append("image", image); data.append("metadata", JSON.stringify(metadata)); const saved = await request(state.caseEdit ? `/api/cases/${encodeURIComponent(state.caseEdit.case_id)}/revisions` : "/api/cases", {method: "POST", body: data});
      for (const [id, item] of state.selected) if (item.case_id === saved.case_id) state.selected.delete(id);
      state.selected.set(caseId(saved), saved); selectionChanged(); $("importPanel").open = false; resetCaseForm(); await loadCases(); await openCase(caseId(saved), true); tell("Case saved and selected. Define success and preview a baseline when ready.");
    } catch (error) { tell(error.message, true); } finally { $("saveCase").disabled = false; }
  });
  $("contractForm").addEventListener("submit", async event => {
    event.preventDefault(); $("saveContract").disabled = true;
    try {
      const payload = buildContract({json: $("contractJson").value, name: $("contractName").value, scope: $("successScope").value, evidenceMode: $("evidenceMode").value, assessment: $("assessmentMethod").value, criteria: $("successCriteria").value, endpoint: $("verifierEndpoint").value});
      const contract = await post("/api/success-contracts", payload); await loadContracts(contract.id); invalidateCampaign(); invalidateFreeze(); state.freezeMembers = null; $("contractPanel").open = false; tell("Success contract saved. Preview shows which measures this data and strategy can support.");
    } catch (error) { tell(error.message, true); } finally { $("saveContract").disabled = false; }
  });
  $("baselineForm").addEventListener("submit", async event => {
    event.preventDefault(); $("startBaseline").disabled = true;
    try {
      const preview = state.campaignPreview; if (!preview || preview.signature !== JSON.stringify(currentCampaign()) || !preview.preview.ready) throw new Error("Preview the current configuration before launching.");
      const campaign = await post("/api/campaigns/from-cases", {...preview.payload, operation_id: state.campaignOperation});
      $("campaignStatus").replaceChildren(node("span", `Campaign started: ${campaign.planned_trials} planned trials. `), link("Open report", `/api/campaigns/${encodeURIComponent(campaign.id)}/report?format=html`), document.createTextNode(" · "), link("Inspect recorded trials", "/static/history.html"));
      state.campaignPreview = null; tell("Baseline launched. Refresh the case after execution to review its recorded outputs.");
      await loadCampaigns(campaign.id);
    } catch (error) { tell(error.message, true); $("startBaseline").disabled = !state.campaignPreview?.preview.ready; }
  });
  $("freezeForm").addEventListener("submit", async event => {
    event.preventDefault(); $("freezeDataset").disabled = true;
    try {
      const preview = state.freezePreview; if (!preview || preview.signature !== JSON.stringify(freezePayload())) throw new Error("Preview the current case and review selection before freezing.");
      const dataset = await post("/api/datasets", {...preview.payload, expected_preview_hash: preview.hash});
      state.freezePreview = null; await loadDatasets(); tell(`Frozen dataset ${dataset.name} saved with exact case and review references. Later corrections require another revision.`);
    } catch (error) { tell(error.message, true); invalidateFreeze(); }
  });
  function resetCaseForm() { state.caseEdit = null; $("caseForm").reset(); $("caseImage").required = true; $("saveCase").textContent = "Save case"; $("cancelCaseRevision").hidden = true; $("caseEditStatus").textContent = "New case. Saved inputs receive an immutable revision."; }
  $("loadExamples").addEventListener("click", async () => { $("loadExamples").disabled = true; try { const result = await post("/api/cases/examples", {}); for (const item of result.cases) state.selected.set(caseId(item), item); selectionChanged(); await loadCases(); if (result.cases.length) await openCase(caseId(result.cases[0]), true); $("importPanel").open = false; tell("Synthetic robotics examples imported and selected. Their observations demonstrate evaluation behavior, not physical performance."); } catch (error) { tell(error.message, true); } finally { $("loadExamples").disabled = false; } });
  $("showImport").addEventListener("click", () => { if (state.caseEdit) resetCaseForm(); $("importPanel").open = true; $("caseName").focus(); });
  $("cancelCaseRevision").addEventListener("click", resetCaseForm);
  $("refreshCases").addEventListener("click", () => { loadCases(); if (state.caseId) openCase(state.caseId); });
  $("selectPage").addEventListener("change", () => { for (const item of state.cases) { if ($("selectPage").checked) state.selected.set(caseId(item), item); else state.selected.delete(caseId(item)); } selectionChanged(); });
  $("previousCases").addEventListener("click", () => { state.offset = Math.max(0, state.offset - state.limit); loadCases(); });
  $("nextCases").addEventListener("click", () => { state.offset += state.limit; loadCases(); });
  $("previewCampaign").addEventListener("click", previewCampaign); $("previewFreeze").addEventListener("click", previewFreeze);
  for (const id of ["campaignName", "campaignStrategy", "campaignRepeats", "campaignTimeout"]) $(id).addEventListener("input", invalidateCampaign);
  $("campaignContract").addEventListener("change", () => { state.frozenDataset = null; state.freezeMembers = null; invalidateCampaign(); invalidateFreeze(); if ($("reviewContract") && !state.reviewEdit) { $("reviewContract").value = $("campaignContract").value; renderReviewCriteria(); } });
  for (const id of ["datasetName", "datasetParent"]) $(id).addEventListener("input", invalidateFreeze);
  $("assessmentMethod").addEventListener("change", () => { $("verifierEndpointLabel").hidden = $("assessmentMethod").value !== "configured_verifier"; });
  $("contractJson").addEventListener("input", () => { $("successCriteria").required = !$("contractJson").value.trim(); });
  function scopeHint() { $("scopeHint").textContent = $("evidenceMode").value === "candidate_output" ? "Output acceptance does not establish observed robot task success." : $("evidenceMode").value === "recorded_episode" ? "This assesses supplied episode evidence from its producing system. Replaying a recording cannot prove a new candidate executed successfully." : "Synthetic rollout evidence demonstrates the evaluation workflow; it is not measured robot performance."; }
  $("evidenceMode").addEventListener("change", scopeHint);
  function themeLabel() { $("themeToggle").textContent = document.documentElement.dataset.theme === "dark" ? "Light mode" : "Dark mode"; }
  $("themeToggle").addEventListener("click", () => { const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; document.documentElement.dataset.theme = theme; try { localStorage.setItem("rove-theme", theme); } catch { /* Keep in-page theming available. */ } themeLabel(); });
  window.addEventListener("popstate", () => { const id = new URLSearchParams(location.search).get("case"); if (id) openCase(id); });
  themeLabel(); updateBudget(); loadAssistantStatus();
  Promise.all([loadCases(), loadContracts(), loadDatasets(), loadCampaigns(), request("/api/strategies").then(page => { const options = [["", "Choose a strategy"], ...page.strategies.map(item => [item.id, item.display_name || item.id])]; $("campaignStrategy").replaceChildren(...select(null, options).childNodes); $("candidateStrategy").replaceChildren(...select(null, options).childNodes); $("campaignStrategy").value = ""; $("candidateStrategy").value = ""; })]).then(() => { const id = new URLSearchParams(location.search).get("case"); if (id) openCase(id); }).catch(error => tell(error.message, true));
})();
