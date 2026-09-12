"use strict";

// Pure presentation helpers are also exercised with Node's built-in test runner.
function trialPresentation(trial) {
  const envelope = trial.result || {};
  const result = envelope.result || envelope;
  const stages = Array.isArray(result.stages) ? result.stages : [];
  const verify = stages.find(stage => stage.stage === "verify" && stage.status === "completed");
  const output = verify?.output || {};
  const assessment = output.evaluator_result;
  const checks = Array.isArray(output.check_results) ? output.check_results : [];
  let verdict = ["pass", "fail", "unknown"].includes(envelope.outcome) ? envelope.outcome : "unknown";
  if (!envelope.outcome && verify && output.verdict_valid !== false) {
    if (["pass", "fail"].includes(assessment?.verdict)) verdict = assessment.verdict;
    else if (!assessment && typeof output.success === "boolean") verdict = output.success ? "pass" : "fail";
  }
  if (output.verdict_valid === false || stages.some(stage => stage.status === "error")) verdict = "unknown";
  if (checks.some(check => check.required && (check.execution !== "completed" || check.result?.verdict === "unknown"))) verdict = "unknown";
  else if (checks.some(check => check.required && check.result?.verdict === "fail") && verdict !== "unknown") verdict = "fail";
  const quality = assessment?.evidence_quality || envelope.evidence_quality || "unknown";
  return {result, stages, output, assessment, checks, verdict, quality};
}

function trialTitle(trial) {
  return trial.task?.task || trial.task?.instruction || trial.task?.name || (typeof trial.task === "string" ? trial.task : "Untitled task");
}

function trialStrategy(trial) {
  return trial.strategy?.name || trial.strategy?.id || trial.strategy?.strategy_id || (typeof trial.strategy === "string" ? trial.strategy : "Configuration unavailable");
}

function usagePresentation(events) {
  const usage = events.filter(event => /usage/.test(event.event_type || ""));
  if (!usage.length) return "Not reported";
  let input = 0, output = 0, hasInput = false, hasOutput = false;
  for (const event of usage) {
    const data = event.data?.usage || event.data || {};
    const inTokens = data.input_tokens ?? data.inputTokens;
    const outTokens = data.output_tokens ?? data.outputTokens;
    if (typeof inTokens === "number" && Number.isFinite(inTokens) && inTokens >= 0) { input += inTokens; hasInput = true; }
    if (typeof outTokens === "number" && Number.isFinite(outTokens) && outTokens >= 0) { output += outTokens; hasOutput = true; }
  }
  if (!hasInput && !hasOutput) return "Token counts not reported";
  return `${hasInput ? input.toLocaleString() : "Unknown"} input / ${hasOutput ? output.toLocaleString() : "unknown"} output tokens`;
}

function eventIsError(event) {
  const type = event.event_type || "";
  const data = event.data || {};
  return /error|failed/.test(type) || data.status === "error" || (type.startsWith("tool.") && data.success === false) || Boolean(data.error);
}

if (typeof module !== "undefined" && module.exports) module.exports = {trialPresentation, trialTitle, trialStrategy, usagePresentation, eventIsError};

if (typeof document !== "undefined") (() => {
  const $ = id => document.getElementById(id);
  const state = {offset: 0, limit: 25, total: 0, listVersion: 0, detailVersion: 0, selected: null, events: [], eventOffset: 0, eventLimit: 100, stage: "all"};
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined && text !== null) element.textContent = String(text);
    if (className) element.className = className;
    return element;
  };
  const badge = (text, kind = "") => node("span", text, `badge ${["pass", "fail", "error"].includes(kind) ? kind : ""}`);
  const date = value => {
    const parsed = new Date(value);
    return value && Number.isFinite(parsed.getTime()) ? parsed.toLocaleString() : "Time not recorded";
  };
  function details(title, data) {
    const element = node("details");
    element.append(node("summary", title), node("pre", JSON.stringify(data ?? null, null, 2)));
    return element;
  }
  function link(text, url) {
    const element = node("a", text); element.href = url; return element;
  }
  function section(title) {
    const element = node("section", null, "inspector-section");
    element.append(node("h3", title)); return element;
  }
  async function request(url) {
    const response = await fetch(url, {headers: {Accept: "application/json"}});
    if (!response.ok) throw new Error(response.status === 404 ? "This saved trial could not be found." : `Unable to load saved evidence (HTTP ${response.status}). Try again.`);
    return response.json();
  }
  function markSelected() {
    for (const item of $("trialList").querySelectorAll("a")) {
      if (item.dataset.trialId === state.selected) item.setAttribute("aria-current", "true");
      else item.removeAttribute("aria-current");
    }
  }
  async function loadList() {
    const version = ++state.listVersion;
    $("listStatus").textContent = "Loading saved trials…";
    $("trialList").setAttribute("aria-busy", "true");
    $("previousPage").disabled = true; $("nextPage").disabled = true;
    const query = new URLSearchParams({limit: state.limit, offset: state.offset});
    if ($("sourceFilter").value) query.set("source", $("sourceFilter").value);
    try {
      const page = await request(`/api/trials?${query}`);
      if (version !== state.listVersion) return;
      state.total = page.total;
      $("trialList").replaceChildren();
      for (const trial of page.trials) {
        const item = node("li");
        const anchor = link("", `/static/history.html?trial=${encodeURIComponent(trial.id)}`);
        anchor.className = "trial-link"; anchor.dataset.trialId = trial.id;
        const view = trialPresentation(trial);
        anchor.append(node("span", trialTitle(trial), "trial-title"), node("span", trialStrategy(trial), "muted trial-meta"), badge(view.verdict, view.verdict), node("span", ` · ${trial.status || "Unknown execution"}`, "muted"), node("span", `${trial.source || "Unknown source"} · ${date(trial.created_at)}`, "muted trial-meta"));
        anchor.addEventListener("click", event => {
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          event.preventDefault(); selectTrial(trial.id, true, true);
        });
        item.append(anchor); $("trialList").append(item);
      }
      $("listStatus").textContent = state.total ? `${state.total.toLocaleString()} saved trial${state.total === 1 ? "" : "s"}` : "No saved trials yet. Run an evaluation to start your history.";
      $("pageLabel").textContent = page.trials.length ? `${state.offset + 1}–${state.offset + page.trials.length}` : "0 trials";
      $("previousPage").disabled = state.offset === 0;
      $("nextPage").disabled = state.offset + state.limit >= state.total;
      markSelected();
    } catch (error) {
      if (version !== state.listVersion) return;
      $("trialList").replaceChildren(); $("pageLabel").textContent = "";
      $("listStatus").textContent = error.message;
    } finally { if (version === state.listVersion) $("trialList").setAttribute("aria-busy", "false"); }
  }
  function summaryItem(label, value, id) {
    const element = node("div", null, "summary-item");
    const content = node("span", value, "summary-value");
    if (id) content.id = id;
    element.append(node("span", label, "summary-label"), content); return element;
  }
  function renderMeasurements(container, view) {
    const checks = [];
    if (view.assessment) checks.push({endpoint: "Task assessment", role: "assessment", result: view.assessment, evaluator_version: view.output.evaluator_version});
    checks.push(...view.checks);
    const content = section("Assessment and measurements");
    const refs = new Set();
    if (!checks.length) {
      content.append(node("p", view.output.reasoning || "No structured assessment was recorded. A completed pipeline alone does not establish task success.", "muted"));
    }
    for (const check of checks) {
      const assessment = check.result || {};
      const card = node("div", null, "check");
      const heading = node("div", null, "check-heading");
      heading.append(node("h4", check.endpoint || "Unnamed check"), badge(assessment.verdict || "unknown", assessment.verdict), badge(check.required ? "Required constraint" : check.role || "Diagnostic"));
      card.append(heading, node("p", assessment.reasoning || "No rationale recorded.", "muted"));
      card.append(node("p", `${assessment.evidence_quality || "unknown"} evidence · ${check.execution || "Assessment recorded"} · revision ${check.evaluator_version || "not recorded"}`, "muted"));
      for (const ref of assessment.evidence_refs || []) refs.add(String(ref));
      if (assessment.measurements?.length) {
        const scroll = node("div", null, "table-scroll"), table = node("table"), head = node("thead"), headRow = node("tr"), body = node("tbody");
        for (const title of ["Measure", "Value", "Evidence", "References"]) { const cell = node("th", title); cell.scope = "col"; headRow.append(cell); }
        head.append(headRow);
        for (const measurement of assessment.measurements) {
          const row = node("tr");
          row.append(node("td", measurement.name), node("td", measurement.value == null ? "Unknown" : `${measurement.value} ${measurement.unit || ""}`), node("td", measurement.quality || "unknown"));
          const references = node("td");
          for (const ref of measurement.evidence_refs || []) { refs.add(String(ref)); references.append(link(String(ref), `#evidence-${encodeURIComponent(String(ref))}`), node("br")); }
          if (!references.childNodes.length) references.textContent = "Not supplied";
          row.append(references); body.append(row);
        }
        table.append(head, body); scroll.append(table); card.append(scroll);
      }
      card.append(details("Criterion and recorded assessment", check)); content.append(card);
    }
    container.append(content);
    if (refs.size) {
      const evidence = section("Evidence references"), list = node("ul", null, "evidence-list");
      evidence.append(node("p", "References are preserved as recorded. This view does not fetch remote assets or infer an observed outcome from an asset name.", "muted"));
      for (const ref of refs) {
        const item = node("li"); item.id = `evidence-${encodeURIComponent(ref)}`; item.tabIndex = -1;
        item.append(node("span", ref, "evidence-ref"), node("span", "Recorded reference · asset preview unavailable", "muted")); list.append(item);
      }
      evidence.append(list); container.append(evidence);
    }
  }
  function renderTrial(trial) {
    const view = trialPresentation(trial), container = $("inspector");
    container.replaceChildren();
    const heading = node("h2", trialTitle(trial), "detail-title"); heading.id = "inspectorHeading"; heading.tabIndex = -1;
    container.append(heading, node("p", `${trialStrategy(trial)} · Trial ${trial.id}`, "muted trial-id"));
    const grid = node("div", null, "summary-grid");
    grid.append(summaryItem("Assessment verdict", view.verdict), summaryItem("Execution", trial.status || "Unknown"), summaryItem("Evidence quality", view.quality), summaryItem("Started", date(trial.created_at)), summaryItem("Finished", trial.finished_at ? date(trial.finished_at) : "Not recorded"), summaryItem("Reported usage (loaded events)", "Loading…", "usageSummary"));
    container.append(grid);
    const meaning = view.quality === "observed" ? "Observed evidence is reported by the configured evaluator. Inspect its required criteria and measurements to understand the verdict." : `This verdict uses ${view.quality} evidence. It does not establish observed robot task success.`;
    container.append(node("p", meaning, "notice"));
    if (trial.source === "legacy") container.append(node("p", "Imported history may lack configuration or live events. Missing provenance remains unknown.", "notice"));
    if (trial.error) container.append(node("p", typeof trial.error === "string" ? trial.error : JSON.stringify(trial.error), "error-text"));
    if (["cancelled", "interrupted"].includes(trial.status)) container.append(node("p", "Execution ended before normal completion. Process cancellation does not confirm that a robot stopped.", "notice"));
    if (trial.campaign_id) container.append(link("Open campaign report", `/api/campaigns/${encodeURIComponent(trial.campaign_id)}/report?format=html`));
    const asset = trial.task?.image_asset;
    if (asset && /^[a-f0-9]{64}$/.test(asset.sha256 || "")) {
      const observation = section("Input observation");
      const url = `/api/trial-assets/${asset.sha256}`;
      observation.append(node("p", `${asset.media_type || "Unknown type"} · ${asset.size_bytes == null ? "Size not recorded" : `${asset.size_bytes.toLocaleString()} bytes`}`, "muted"));
      if (["image/png", "image/jpeg"].includes(asset.media_type)) {
        const preview = node("button", "Load observation image", "secondary"); preview.type = "button";
        preview.addEventListener("click", () => {
          preview.disabled = true; preview.textContent = "Loading observation…";
          const image = node("img"); image.className = "observation-image"; image.alt = `Input observation for ${trialTitle(trial)}`;
          image.addEventListener("load", () => { preview.remove(); });
          image.addEventListener("error", () => { image.remove(); preview.disabled = false; preview.textContent = "Image unavailable — retry"; });
          image.src = url; observation.append(image);
        });
        observation.append(preview, document.createTextNode(" "));
      }
      const download = link("Download recorded asset", url); download.setAttribute("download", asset.sha256);
      observation.append(download); container.append(observation);
    }
    renderMeasurements(container, view);
    if (view.stages.length) {
      const stages = section("Pipeline stages");
      for (const stage of view.stages) {
        const card = node("div", null, "stage-result");
        const line = node("div", null, "check-heading");
        line.append(node("h4", stage.stage), badge(stage.status || "unknown", stage.status), node("span", stage.model_id || "Model not recorded", "muted"));
        card.append(line);
        if (stage.error) card.append(node("p", stage.error, "error-text"));
        card.append(details("Recorded stage output", stage)); stages.append(card);
      }
      container.append(stages);
    }
    const activity = section("Recorded activity");
    activity.append(node("p", "Filter activity by stage. Timestamps and trace identifiers reflect recorded events; missing events are not reconstructed.", "muted"));
    const status = node("p", "Loading events…", "muted"); status.id = "eventStatus"; status.setAttribute("role", "status");
    const controls = node("div", null, "lane-controls"); controls.id = "laneControls"; controls.setAttribute("aria-label", "Filter activity by stage");
    const timeline = node("ol", null, "timeline"); timeline.id = "eventTimeline";
    const more = node("button", "Load more events", "secondary event-more"); more.id = "moreEvents"; more.type = "button"; more.hidden = true; more.addEventListener("click", () => loadEvents(state.detailVersion));
    activity.append(status, controls, timeline, more); container.append(activity);
    const config = section("Frozen configuration");
    config.append(node("p", `Snapshot ${trial.snapshot_id || "not recorded"} · Seed ${trial.seed == null ? "not recorded" : trial.seed}. These are the settings recorded for this attempt. Remote model reproducibility is not guaranteed.`, "muted"));
    config.append(details("Inspect saved configuration", trial.snapshot || {unavailable: "No frozen configuration was recorded."}), details("Complete trial record", trial)); container.append(config);
  }
  function renderEvents() {
    const stages = [...new Set(state.events.map(event => event.stage || "unassigned"))];
    $("laneControls").replaceChildren();
    for (const name of ["all", ...stages]) {
      const count = state.events.filter(event => name === "all" || (event.stage || "unassigned") === name).length;
      const button = node("button", `${name === "all" ? "All stages" : name} (${count})`, "secondary");
      button.type = "button"; button.setAttribute("aria-pressed", String(state.stage === name));
      button.addEventListener("click", () => { state.stage = name; renderEvents(); }); $("laneControls").append(button);
    }
    $("eventTimeline").replaceChildren();
    for (const event of state.events.filter(item => state.stage === "all" || (item.stage || "unassigned") === state.stage)) {
      const type = event.event_type || "Unknown event", data = event.data || {};
      const isError = eventIsError(event);
      const item = node("li", null, `timeline-event${isError ? " event-error" : ""}`), heading = node("div", null, "event-header");
      heading.append(node("strong", type), badge(event.stage || "unassigned"), badge(event.role || "role not recorded"), node("span", date(event.timestamp || event.recorded_at), "event-time"));
      item.append(heading);
      if (data.tool_name || data.toolName) item.append(node("p", data.tool_name || data.toolName, "muted event-data"));
      if (isError) item.append(node("p", typeof data.error === "string" ? data.error : data.message || "The recorded event reports an error. Expand its details.", "error-text event-data"));
      if (data.truncated || data.dropped_events) item.append(node("p", "This event reports incomplete telemetry. Inspect the recorded details.", "notice"));
      item.append(details("Event details and trace context", event)); $("eventTimeline").append(item);
    }
    $("usageSummary").textContent = usagePresentation(state.events);
  }
  async function loadEvents(version) {
    const id = state.selected;
    $("moreEvents").disabled = true;
    try {
      const page = await request(`/api/trials/${encodeURIComponent(id)}/events?limit=${state.eventLimit}&offset=${state.eventOffset}`);
      if (version !== state.detailVersion) return;
      state.events.push(...page.events); state.eventOffset += page.events.length;
      renderEvents();
      $("eventStatus").textContent = state.events.length ? `${state.events.length} events loaded. Usage below reflects only these events; unavailable usage is never counted as zero.` : "No events were recorded for this trial. Usage and trace coverage are unavailable.";
      $("moreEvents").hidden = page.total != null ? state.eventOffset >= page.total : page.events.length < state.eventLimit;
    } catch (error) {
      if (version !== state.detailVersion) return;
      $("eventStatus").textContent = `${error.message} The saved trial is still available.`;
      $("usageSummary").textContent = state.events.length ? usagePresentation(state.events) : "Unavailable";
      $("moreEvents").hidden = false; $("moreEvents").textContent = "Retry loading events";
    } finally { if (version === state.detailVersion) $("moreEvents").disabled = false; }
  }
  async function selectTrial(id, push = false, focus = false) {
    state.selected = id; state.events = []; state.eventOffset = 0; state.stage = "all";
    const version = ++state.detailVersion;
    if (push) { const url = new URL(location.href); url.searchParams.set("trial", id); history.pushState({}, "", url); }
    markSelected();
    $("inspector").setAttribute("aria-busy", "true");
    $("inspector").replaceChildren(node("p", "Loading saved trial…", "muted"));
    try {
      const trial = await request(`/api/trials/${encodeURIComponent(id)}`);
      if (version !== state.detailVersion) return;
      renderTrial(trial); if (focus) $("inspectorHeading").focus();
      await loadEvents(version);
    } catch (error) {
      if (version !== state.detailVersion) return;
      const title = node("h2", "Trial unavailable"); title.id = "inspectorHeading";
      const retry = node("button", "Try again", "secondary"); retry.type = "button"; retry.addEventListener("click", () => selectTrial(id));
      $("inspector").replaceChildren(title, node("p", error.message, "error-text"), retry);
    } finally { if (version === state.detailVersion) $("inspector").setAttribute("aria-busy", "false"); }
  }
  function themeLabel() { $("themeToggle").textContent = document.documentElement.dataset.theme === "dark" ? "Light mode" : "Dark mode"; }
  $("themeToggle").addEventListener("click", () => {
    const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("rove-theme", theme); } catch { /* In-page switching remains available. */ }
    themeLabel();
  });
  $("sourceFilter").addEventListener("change", () => { state.offset = 0; loadList(); });
  $("refreshTrials").addEventListener("click", () => { loadList(); if (state.selected) selectTrial(state.selected); });
  $("previousPage").addEventListener("click", () => { state.offset = Math.max(0, state.offset - state.limit); loadList(); });
  $("nextPage").addEventListener("click", () => { state.offset += state.limit; loadList(); });
  window.addEventListener("popstate", () => {
    const id = new URLSearchParams(location.search).get("trial");
    if (id) selectTrial(id);
    else {
      state.selected = null; ++state.detailVersion; markSelected();
      const title = node("h2", "Inspect a trial"); title.id = "inspectorHeading";
      $("inspector").replaceChildren(title, node("p", "Select an attempt to inspect its saved evidence.", "muted"));
      $("inspector").setAttribute("aria-busy", "false");
    }
  });
  themeLabel(); loadList();
  const selected = new URLSearchParams(location.search).get("trial");
  if (selected) selectTrial(selected);
})();
