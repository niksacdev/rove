/* Visible campaign execution feedback. Completion here means execution, never an acceptance grade. */
(function () {
  "use strict";
  const stageReader = typeof module !== "undefined" && module.exports ? require("./trial-output.js").stagesFromTrial : window.RoveTrialOutput.stagesFromTrial;
  const terminal = new Set(["completed", "invalid_verdict", "unresolved_evidence", "error", "failed", "timeout", "cancelled", "interrupted", "runtime_changed", "evaluator_changed"]);
  const problems = new Set(["error", "failed", "timeout", "interrupted", "runtime_changed", "evaluator_changed"]);
  const activeCampaign = status => ["running", "pending", "queued", "cancelling"].includes(status);
  function duration(row, now) {
    if (Number.isFinite(row.attempt_wall_ms) && row.execution !== "running") return Math.max(0, row.attempt_wall_ms);
    const start = Date.parse(row.started_at || row.created_at), end = row.execution === "running" ? now : Date.parse(row.finished_at);
    return Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, end - start) : null;
  }
  function elapsedLabel(ms) {
    if (ms == null) return "Elapsed time unavailable";
    const seconds = Math.floor(ms / 1000), minutes = Math.floor(seconds / 60);
    return minutes ? `${minutes}m ${seconds % 60}s elapsed` : `${seconds}s elapsed`;
  }
  function buildStrategyProgress(campaign, rows, eventPages = {}, now = Date.now()) {
    const tasks = campaign.spec?.tasks || [], seeds = campaign.spec?.seeds || [];
    return (campaign.spec?.strategies || []).map(id => {
      const definition = campaign.strategy_definitions?.[id] || campaign.config?.strategies?.[id] || {};
      const unique = new Map();
      for (const row of rows || []) if (row.strategy_id === id) unique.set(`${row.task_id}:${row.seed}`, row);
      const attempts = [...unique.values()], planned = tasks.length * seeds.length;
      const running = attempts.filter(row => row.execution === "running");
      const finished = attempts.filter(row => terminal.has(row.execution)).length;
      const errors = attempts.filter(row => problems.has(row.execution)).length;
      const cancelled = attempts.filter(row => row.execution === "cancelled").length;
      const remaining = Math.max(0, planned - attempts.length);
      let status = running.length ? "running" : finished === planned && planned > 0 ? errors ? "error" : cancelled ? "cancelled" : "completed" : activeCampaign(campaign.status) ? "queued" : campaign.status === "cancelled" ? "cancelled" : "incomplete";
      const recent = [...attempts].sort((a, b) => (Date.parse(b.finished_at || b.started_at) || 0) - (Date.parse(a.finished_at || a.started_at) || 0))[0];
      const visible = running.length ? running : recent ? [recent] : [];
      const executions = visible.map(row => {
        const page = eventPages[row.trial_id] || {}, task = tasks.find(item => item.id === row.task_id);
        const repeatIndex = seeds.indexOf(row.seed);
        return {id: row.trial_id, task: task?.task || task?.name || row.task_id, repetition: repeatIndex >= 0 ? repeatIndex + 1 : null, seed: row.seed, execution: row.execution || "unknown", elapsed: elapsedLabel(duration(row, now)), stages: stageReader({result: row.result || (Array.isArray(row.stages) ? {stages: row.stages} : null)}, page.events || []), stageError: page.error || ""};
      });
      return {id, name: definition.display_name || id, status, planned, finished, errors, cancelled, remaining, remainingLabel: activeCampaign(campaign.status) ? "queued" : "not run", executions};
    });
  }
  const mounted = new WeakMap();
  function render(container, lanes, onInspect) {
    const doc = container.ownerDocument;
    const node = (tag, text, className) => { const el = doc.createElement(tag); if (text != null) el.textContent = text; if (className) el.className = className; return el; };
    const text = (el, value) => { if (el.textContent !== value) el.textContent = value; };
    let entries = mounted.get(container); if (!entries) { entries = new Map(); mounted.set(container, entries); container.replaceChildren(); }
    const ids = new Set();
    for (const [position, lane] of lanes.entries()) {
      ids.add(lane.id); let entry = entries.get(lane.id);
      if (!entry) {
        const card = node("article", null, "strategy-progress-lane"), heading = node("div", null, "strategy-progress-heading"); card.dataset.strategyId = lane.id;
        const name = node("h3"), status = node("span", null, "execution-state"); status.setAttribute("role", "status");
        heading.append(name, status); const counts = node("p", null, "strategy-progress-counts"), activity = node("div", null, "strategy-progress-activity");
        const inspect = node("button", "Inspect output", "secondary"); inspect.type = "button";
        card.append(heading, counts, activity, inspect); entry = {card, name, status, counts, activity, inspect}; entries.set(lane.id, entry);
      }
      text(entry.name, lane.name); text(entry.status, {running: "Running", queued: "Queued", completed: "Completed", error: "Execution error", cancelled: "Cancelled", incomplete: "Incomplete"}[lane.status]);
      entry.status.dataset.state = lane.status;
      text(entry.counts, `${lane.finished} / ${lane.planned} trials finished${lane.remaining ? ` · ${lane.remaining} ${lane.remainingLabel}` : ""}${lane.errors ? ` · ${lane.errors} execution error${lane.errors === 1 ? "" : "s"}` : ""}`);
      const signature = JSON.stringify(lane.executions);
      if (entry.signature !== signature) {
        entry.signature = signature; entry.activity.replaceChildren();
        for (const execution of lane.executions) {
          const row = node("div", null, "strategy-execution");
          row.append(node("p", execution.task, "strategy-execution-task"), node("p", `${execution.execution === "running" ? "Current trial" : "Latest trial"} · ${execution.repetition == null ? `seed ${execution.seed}` : `repetition ${execution.repetition}`} · ${execution.elapsed}`, "strategy-execution-meta"));
          const stages = node("ol", null, "execution-stage-track"); stages.setAttribute("aria-label", "Recorded pipeline stages");
          for (const stage of execution.stages) {
            const state = stage.status || "recorded", item = node("li"); item.dataset.state = state;
            item.append(node("span", state === "completed" ? "✓" : state === "running" ? "●" : state === "error" ? "!" : "·", "stage-state-icon"), node("span", `${stage.stage.charAt(0).toUpperCase() + stage.stage.slice(1)} · ${state}${stage.model_id ? ` · ${stage.model_id}` : ""}`)); stages.append(item);
          }
          row.append(stages);
          if (execution.stageError) row.append(node("p", "Stage updates unavailable. Execution continues; use Refresh to retry.", "stage-progress-note"));
          else if (!execution.stages.length) row.append(node("p", execution.execution === "running" ? "Waiting for the first recorded pipeline stage…" : "Open the trial to inspect its recorded output and trace.", "stage-progress-note"));
          entry.activity.append(row);
        }
        if (!lane.executions.length) entry.activity.append(node("p", lane.remainingLabel === "queued" ? "Waiting for its turn in this campaign." : "No trial was executed for this strategy.", "stage-progress-note"));
      }
      const inspectId = lane.executions.find(item => item.id)?.id;
      entry.inspect.hidden = !inspectId; entry.inspect.onclick = () => onInspect(inspectId);
      entry.inspect.setAttribute("aria-label", `Inspect ${lane.name} trial output`);
      if (container.children[position] !== entry.card) container.insertBefore(entry.card, container.children[position] || null);
    }
    for (const [id, entry] of entries) if (!ids.has(id)) { entry.card.remove(); entries.delete(id); }
  }
  const api = {buildStrategyProgress, render, elapsedLabel};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof window !== "undefined") window.RoveCampaignProgress = api;
})();
