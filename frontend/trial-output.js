/* Recorded trial presentation. Rendering never launches an evaluation or assigns its grade. */
(function () {
  "use strict";
  function stagesFromTrial(trial, events = []) {
    const records = new Map();
    function record(value) {
      if (!value || typeof value.stage !== "string") return;
      let key = `${value.phase || ""}:${value.stage}`;
      const unknown = `:${value.stage}`;
      if (value.phase && records.has(unknown)) { records.set(key, {...records.get(unknown), ...records.get(key)}); records.delete(unknown); }
      else if (!value.phase) {
        const matches = [...records.keys()].filter(item => records.get(item).stage === value.stage);
        if (matches.length === 1) key = matches[0];
      }
      records.set(key, {...records.get(key), ...value});
    }
    const result = trial.result?.result || trial.result || {};
    // The orchestrator's saved stage list is authoritative. Adapter spans can include
    // reset operations or perception called internally by a verification agent.
    if (Array.isArray(result.stages)) {
      for (const stage of result.stages) record(stage);
      return [...records.values()];
    }
    for (const event of events) if (event.event_type === "stage" && event.data?.stage) record(event.data);
    // Legacy outputs can supply a stage without a saved stage list. Instrumentation
    // may enrich those existing records, but cannot invent additional pipeline steps.
    for (const stage of ["perceive", "plan", "act", "dynamics", "verify"]) {
      if (result[stage] != null) record({stage, status: [...records.values()].find(item => item.stage === stage)?.status || "recorded", output: typeof result[stage] === "object" ? result[stage] : {reasoning: String(result[stage])}});
    }
    for (const event of events) if (event.event_type?.startsWith("rove.stage.") && event.stage) {
      const existing = [...records.values()].filter(item => item.stage === event.stage);
      if (existing.length !== 1) continue;
      const stage = existing[0];
      if (!stage.model_id && event.endpoint_id) stage.model_id = event.endpoint_id;
      const status = event.event_type.split(".").at(-1);
      if (stage.status === "recorded" && ["completed", "error", "failed"].includes(status)) stage.status = status;
    }
    return [...records.values()];
  }
  function render(container, trial, events, {caseRecord, task} = {}) {
    const doc = container.ownerDocument;
    const node = (tag, text, cls) => { const el = doc.createElement(tag); if (text != null) el.textContent = String(text); if (cls) el.className = cls; return el; };
    const closed = new Set([...container.querySelectorAll("details[data-stage-key]")].filter(el => !el.open).map(el => el.dataset.stageKey));
    const rawExpanded = new Set([...container.querySelectorAll("details[data-raw-stage][open]")].map(el => el.dataset.rawStage));
    container.replaceChildren();
    const observation = node("div", null, "trial-observation");
    const digest = caseRecord?.image_asset?.sha256 || trial.task?.image_asset?.sha256;
    if (/^[a-f0-9]{64}$/.test(digest || "")) {
      const image = node("img"); image.src = `/api/trial-assets/${digest}`; image.alt = `Observation for ${caseRecord?.name || task?.task || trial.task?.task || "this trial"}`;
      image.addEventListener("error", () => image.replaceWith(node("p", "The recorded observation could not be loaded.", "muted")));
      observation.append(image);
    }
    const request = node("div"); request.append(node("h4", "Observation and task"), node("p", task?.task || caseRecord?.task || trial.task?.task || "See this trial’s recorded input in its full trace."));
    observation.append(request); container.append(observation);
    container.append(node("p", `Execution: ${trial.status || "unknown"}. Stage execution and task acceptance are separate measures.`, "muted"));
    const records = stagesFromTrial(trial, events);
    const timeline = node("div", null, "pipeline-stages");
    for (const stage of records) timeline.append(node("span", `${stage.stage} · ${stage.status || "recorded"}${stage.model_id ? ` · ${stage.model_id}` : ""}`, "badge"));
    container.append(timeline);
    if (!records.length) container.append(node("p", "No stage events recorded yet. This trial will show its pipeline outputs as they are recorded.", "muted"));
    const context = {expected_subtasks: caseRecord?.reference_data?.expected_subtasks, correction: caseRecord?.candidate_context?.correction, constraints: caseRecord?.candidate_context?.constraints};
    const grid = node("div", null, "rich-trial-stages");
    for (const [index, stage] of records.entries()) {
      const panel = node("details", null, "rich-trial-stage"); panel.dataset.stageKey = `${stage.phase || ""}:${stage.stage}`; panel.open = !closed.has(panel.dataset.stageKey);
      const title = node("summary"); title.append(node("strong", `${index + 1}. ${stage.stage.charAt(0).toUpperCase() + stage.stage.slice(1)}`), node("span", `${stage.status || "recorded"}${stage.model_id ? ` · ${stage.model_id}` : ""}${Number.isFinite(stage.latency_ms) ? ` · ${Math.round(stage.latency_ms)} ms` : ""}`, "muted"));
      panel.append(title);
      if (stage.phase) panel.append(node("p", `Phase: ${stage.phase}`, "muted"));
      const body = node("div", null, "rich-stage-body");
      if (stage.error) body.append(node("p", stage.error, "error-text"));
      if (stage.output != null) {
        try { window.RoveStageRenderers.renderStageOutput(body, stage.stage, stage.output, context); }
        catch { body.replaceChildren(node("p", "This recorded output has an unfamiliar shape. Its exact data remains available below.", "muted")); }
        const raw = node("details", null, "stage-raw-output"); raw.dataset.rawStage = panel.dataset.stageKey; raw.open = rawExpanded.has(panel.dataset.stageKey); raw.append(node("summary", "Raw stage output"), node("pre", JSON.stringify(stage.output, null, 2))); body.append(raw);
      } else body.append(node("p", stage.status === "running" ? "This stage is running. Output appears when the stage records it." : "No output was recorded for this stage.", "muted"));
      panel.append(body); grid.append(panel);
    }
    container.append(grid);
    if (trial.error) container.append(node("p", trial.error, "error-text"));
    if (trial.result) { const raw = node("details"); raw.append(node("summary", "Recorded output"), node("pre", JSON.stringify(trial.result, null, 2))); container.append(raw); }
    const trace = node("a", "Open full trial trace and evidence →"); trace.href = `/static/history.html?trial=${encodeURIComponent(trial.id)}`; container.append(trace);
  }
  const api = {stagesFromTrial, render};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof window !== "undefined") window.RoveTrialOutput = api;
})();
