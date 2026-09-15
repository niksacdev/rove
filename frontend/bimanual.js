/* ABC evaluations use the ordinary ROVE trial store, progress and report surfaces. */
(function () {
  "use strict";
  function parseSeeds(value) {
    const parts = value.split(",").map(item => item.trim());
    if (!parts.length || parts.length > 100 || parts.some(item => !/^\d+$/.test(item))) throw Error("Enter up to 100 non-negative seed numbers, separated by commas.");
    const seeds = parts.map(Number);
    if (seeds.some(seed => !Number.isSafeInteger(seed) || seed > 2147483647) || new Set(seeds).size !== seeds.length) throw Error("Seed numbers must be unique and no greater than 2147483647.");
    return seeds;
  }
  function countLabel(cases, strategies, attempts) {
    const total = cases * strategies * attempts;
    return `${cases} ${cases === 1 ? "case" : "cases"} × ${strategies} ${strategies === 1 ? "strategy" : "strategies"} × ${attempts} ${attempts === 1 ? "attempt" : "attempts"} = ${total} planned ${total === 1 ? "trial" : "trials"}`;
  }
  function criterionLabel(grading) {
    const mode = grading?.success_mode || grading?.criterion || grading?.success || "";
    if (["final", "final_success", "goal_at_end"].includes(mode)) return "The simulator reports the goal satisfied at the end of the episode.";
    if (["ever", "episode_success", "goal_reached"].includes(mode)) return "The simulator reports the goal reached at least once during the episode.";
    return `Simulator task evaluator · ${mode || "the frozen campaign success criterion"}`;
  }
  if (typeof module !== "undefined" && module.exports) module.exports = {parseSeeds, countLabel, criterionLabel};
  if (typeof document === "undefined") return;
  const el = id => document.getElementById(id);
  if (!el("comparisonForm")) return;
  const terminal = new Set(["completed", "error", "cancelled", "interrupted", "timeout", "runtime_changed", "evaluator_changed"]);
  let profile = null, preview = null, reviewedRequest = null, active = null, timer = null, dirty = false, busy = false;
  const events = new Map();
  function node(tag, text, className) { const value = document.createElement(tag); if (text != null) value.textContent = text; if (className) value.className = className; return value; }
  function link(text, href, newTab = false) { const value = node("a", text, "secondary"); value.href = href; if (newTab) { value.target = "_blank"; value.rel = "noopener noreferrer"; } return value; }
  function status(text, error = false) { el("pageStatus").textContent = text; el("pageStatus").dataset.error = String(error); }
  async function api(url, options = {}) {
    const response = await fetch(url, options.body ? {...options, headers: {"Content-Type": "application/json"}, body: JSON.stringify(options.body)} : options);
    const value = await response.json();
    if (!response.ok) {
      const detail = value.detail;
      const error = Error(typeof detail === "string" ? detail : detail?.message ? `${detail.message}${detail.issues?.length ? `: ${detail.issues.join("; ")}` : ""}` : "Unable to complete the request. Check the configuration and try again.");
      error.status = response.status; throw error;
    }
    return value;
  }
  function request() {
    const reset = parseSeeds(el("resetSeeds").value), seeds = parseSeeds(el("policySeeds").value);
    if (el("comparisonMode").value === "trial" && (reset.length !== 1 || seeds.length !== 1)) throw Error("A trial comparison uses one scene seed and one model seed. Choose Campaign for repeated comparisons.");
    const strategies = [...el("strategyRows").querySelectorAll("input:checked")].map(input => input.value);
    const maxSteps = Number(el("maxSteps").value);
    if (!Number.isInteger(maxSteps) || maxSteps < 1 || maxSteps > 3540) throw Error("Choose between 1 and 3540 control steps.");
    if (!strategies.length) throw Error("Select at least one ready model.");
    if (reset.length * seeds.length * strategies.length > 2000) throw Error("Use at most 2,000 trials per comparison.");
    if (!el("comparisonName").value.trim()) throw Error("Give this comparison a name.");
    return {name: el("comparisonName").value.trim(), strategy_ids: strategies, reset_seeds: reset, policy_seeds: seeds, max_steps: maxSteps};
  }
  function update() {
    preview = null; reviewedRequest = null;
    let cases = 0, seeds = 0;
    try { cases = parseSeeds(el("resetSeeds").value).length; seeds = parseSeeds(el("policySeeds").value).length; } catch { /* Validation appears before review. */ }
    const strategies = el("strategyRows").querySelectorAll("input:checked").length;
    el("plannedCount").textContent = countLabel(cases, strategies, seeds);
    try { request(); el("reviewButton").disabled = busy; } catch { el("reviewButton").disabled = true; }
  }
  function strategyTable(rows) {
    el("strategyRows").replaceChildren();
    for (const row of rows) {
      const tr = node("tr"), select = node("td"), input = node("input");
      input.type = "checkbox"; input.value = row.id; input.disabled = !row.ready; input.checked = Boolean(row.ready); input.setAttribute("aria-label", `Compare ${row.id}`);
      select.append(input); const readiness = node("td"); readiness.append(node("span", row.ready ? "Ready" : "Setup needed", "bimanual-status"));
      if (row.issues?.length) readiness.append(node("small", row.issues.join(" · ")));
      const kind = row.kind === "pi05" || row.kind === "lerobot-pi05" ? "π0.5 · adapted checkpoint" : ["abc", "abc-vla", "abc_vla"].includes(row.kind) ? "ABC-VLA" : row.kind || "Configured model";
      const identity = node("td", row.label || row.id); if (row.label) identity.append(node("small", row.id));
      tr.append(select, identity, node("td", kind), readiness); el("strategyRows").append(tr);
    }
  }
  async function load() {
    busy = true; el("recheck").disabled = true; status("Checking the configured models…");
    try {
      profile = await api("/api/bimanual/config"); strategyTable(profile.strategies || []);
      el("setup").hidden = profile.configured && Boolean(profile.strategies?.length);
      el("comparisonForm").hidden = !profile.configured;
      el("configurationIssues").replaceChildren(...(profile.issues || []).map(issue => node("p", issue)));
      status(profile.configured ? "" : "Runtime setup is required before a learned model comparison can run.");
    } catch (error) { status(error.message, true); el("setup").hidden = false; }
    finally { busy = false; el("recheck").disabled = false; update(); }
  }
  function showReview(value) {
    el("comparisonForm").hidden = true; el("review").hidden = false;
    el("reviewSummary").textContent = countLabel(value.cases, value.strategies.length, value.attempts_per_case);
    const facts = [["Task", "Put plastic bottles in the bin"], ["Starting scenes", reviewedRequest.reset_seeds.join(", ")], ["Models", value.strategies.join(" · ")], ["Model seeds", reviewedRequest.policy_seeds.join(", ")], ["Action budget", `${value.max_steps} control steps per trial`], ["Success", criterionLabel(value.success_criterion)], ["Evidence", "Simulator outcome, executed trajectory and inference traces"]];
    el("reviewFacts").replaceChildren(...facts.flatMap(([key, val]) => [node("dt", key), node("dd", val)]));
    el("reviewReadiness").textContent = value.ready ? "Configuration verified. Running will use the reviewed settings and save every trial." : value.issues.join(" · ");
    el("launchButton").disabled = !value.ready; el("launchButton").textContent = el("comparisonMode").value === "campaign" ? "Run campaign" : "Run comparison";
    el("reviewHeading").focus();
  }
  el("comparisonForm").addEventListener("submit", async event => {
    event.preventDefault(); if (busy) return;
    busy = true; el("reviewButton").disabled = true; status("Verifying model and simulator configuration…");
    try { reviewedRequest = request(); preview = await api("/api/bimanual/preview", {method: "POST", body: reviewedRequest}); showReview(preview); status(""); }
    catch (error) { status(error.message, true); }
    finally { busy = false; el("reviewButton").disabled = false; }
  });
  el("editButton").addEventListener("click", () => { el("review").hidden = true; el("comparisonForm").hidden = false; update(); el("comparisonName").focus(); });
  el("configurationFields").addEventListener("input", () => { dirty = true; update(); });
  function modeContext(campaign) {
    el("backToBrowser").href = campaign ? "/static/benchmarks.html" : "/static/history.html"; el("backToBrowser").textContent = campaign ? "Back to campaigns" : "Back to trials";
    window.RoveNavigation?.setActive(campaign ? "results" : "evaluate");
  }
  el("comparisonMode").addEventListener("change", () => {
    const campaign = el("comparisonMode").value === "campaign";
    el("resetSeeds").value = campaign ? "0, 1, 2" : "0"; el("policySeeds").value = campaign ? "0, 1, 2" : "0";
    modeContext(campaign);
    dirty = true; update();
  });
  async function fetchEvents(id) {
    const previous = events.get(id) || {events: [], offset: 0};
    try {
      const page = await api(`/api/trials/${encodeURIComponent(id)}/events?limit=200&offset=${previous.offset}`);
      previous.events.push(...page.events); previous.offset += page.events.length;
      if (previous.events.length > 400) previous.events = previous.events.slice(-400);
      previous.error = "";
    } catch { previous.error = "Live trace updates unavailable; retrying."; }
    events.set(id, previous); return previous;
  }
  function activity(rows) {
    const lines = [];
    for (const row of rows.filter(item => item.execution === "running")) {
      const page = events.get(row.trial_id), last = page?.events?.at(-1);
      if (last) { const details = last.data || {}, steps = details.completed_steps ?? details.steps; lines.push(node("p", `${row.strategy_id}: ${last.name || last.stage || "Episode in progress"}${Number.isFinite(steps) ? ` · ${steps}${Number.isFinite(details.max_steps) ? ` / ${details.max_steps}` : ""} control steps` : ""}`)); }
      if (page?.error) lines.push(node("p", page.error));
    }
    el("episodeActivity").replaceChildren(...lines);
  }
  function resultTable(rows) {
    el("resultRows").replaceChildren();
    for (const row of rows.slice(0, 50)) {
      const tr = node("tr"), evidence = node("td");
      const outcome = row.outcome || row.result?.outcome;
      const label = outcome === "pass" ? "Criterion met" : outcome === "fail" ? "Criterion not met" : "Not assessed";
      evidence.append(link("Inspect trial & traces", `/static/history.html?trial=${encodeURIComponent(row.trial_id)}#traces`, true));
      evidence.append(node("small", `Trial ${row.trial_id.slice(0, 8)}`));
      const caseName = /^bottles-scene-\d+$/.test(row.task_id || "") ? `Scene ${row.task_id.replace("bottles-scene-", "")}` : row.task_id || "Case unavailable";
      tr.append(node("td", row.strategy_id), node("td", caseName), node("td", Number.isInteger(row.seed) ? String(row.seed) : "Unavailable"), node("td", label), node("td", row.execution || "unknown"), evidence); el("resultRows").append(tr);
    }
  }
  function complete(campaign, rows) {
    dirty = false; el("cancelButton").hidden = true; el("results").hidden = false;
    el("executionHeading").textContent = "Recorded execution";
    el("resultsSummary").textContent = campaign.status === "completed" ? "The comparison has finished. Outcomes below come from the simulator’s frozen success criterion. Inspect any trial for its measurements and trace." : "The comparison stopped before all work completed. Recorded trials remain available; incomplete attempts do not establish task success.";
    if (rows.length > 50) el("resultsSummary").append(` Showing 50 of ${rows.length} recorded trials. The full report includes every trial.`);
    el("resultActions").replaceChildren();
    if (active.mode === "campaign") {
      el("resultActions").append(link("Open campaign report", `/api/campaigns/${encodeURIComponent(active.id)}/report?format=html`, true), link("Campaign history", `/static/campaign-history.html?campaign=${encodeURIComponent(active.id)}`), link("Export JSON", `/api/campaigns/${encodeURIComponent(active.id)}/report?format=json`));
    } else el("resultActions").append(link("All saved trials", "/static/history.html"));
    resultTable(rows); el("resultsHeading").focus();
  }
  async function poll() {
    if (!active) return;
    try {
      let campaign, rows;
      if (active.mode === "campaign") {
        const [detail, attempts] = await Promise.all([api(`/api/campaigns/${encodeURIComponent(active.id)}`), api(`/api/campaigns/${encodeURIComponent(active.id)}/assessments`)]);
        campaign = detail.campaign; rows = attempts.trials || [];
      } else {
        const job = await api(`/api/bimanual/trials/${encodeURIComponent(active.id)}`);
        campaign = {status: job.status, spec: {tasks: job.tasks, strategies: job.strategy_ids, seeds: job.seeds}};
        rows = await Promise.all(Object.entries(job.trial_ids).map(async ([sid, id]) => {
          const trial = await api(`/api/trials/${encodeURIComponent(id)}`);
          return {trial_id: id, strategy_id: sid, task_id: job.tasks[0].id, seed: job.seeds[0], execution: trial.status, result: trial.result, outcome: trial.result?.outcome, started_at: trial.started_at || trial.created_at, finished_at: trial.finished_at};
        }));
      }
      await Promise.all(rows.filter(row => row.execution === "running").map(row => fetchEvents(row.trial_id)));
      if (window.RoveCampaignProgress) window.RoveCampaignProgress.render(el("executionProgress"), window.RoveCampaignProgress.buildStrategyProgress(campaign, rows, Object.fromEntries(events)), id => window.open(`/static/history.html?trial=${encodeURIComponent(id)}#traces`, "_blank", "noopener,noreferrer"));
      activity(rows); status("");
      if (terminal.has(campaign.status)) { complete(campaign, rows); return; }
    } catch (error) {
      if (error.status === 404) { status(error.message, true); el("cancelButton").hidden = true; el("executionHeading").textContent = "Comparison session unavailable"; return; }
      status(`${error.message} Progress will retry. Saved trials remain available in Trials.`, true);
    }
    timer = setTimeout(poll, 2000);
  }
  el("launchButton").addEventListener("click", async () => {
    if (!preview?.ready || busy) return;
    busy = true; el("launchButton").disabled = true; status("Starting the reviewed comparison…");
    try {
      const mode = el("comparisonMode").value;
      const value = await api(`/api/bimanual/${mode === "campaign" ? "campaigns" : "trials"}`, {method: "POST", body: {...reviewedRequest, preview_hash: preview.preview_hash}});
      active = {mode, id: value.id}; dirty = false; el("review").hidden = true; el("execution").hidden = false; el("cancelButton").hidden = false; el("executionHeading").focus();
      history.replaceState(null, "", `/static/bimanual.html?${mode === "campaign" ? "campaign" : "job"}=${encodeURIComponent(value.id)}`);
      await poll();
    } catch (error) { status(error.message, true); el("launchButton").disabled = false; }
    finally { busy = false; }
  });
  el("cancelButton").addEventListener("click", async () => {
    if (!active) return; el("cancelButton").disabled = true;
    try { await api(active.mode === "campaign" ? `/api/campaigns/${encodeURIComponent(active.id)}/cancel` : `/api/bimanual/trials/${encodeURIComponent(active.id)}/cancel`, {method: "POST"}); clearTimeout(timer); await poll(); }
    catch (error) { status(error.message, true); }
    finally { el("cancelButton").disabled = false; }
  });
  el("newComparison").addEventListener("click", async () => {
    clearTimeout(timer); active = null; events.clear(); el("execution").hidden = true; el("results").hidden = true; el("comparisonForm").hidden = false; el("executionHeading").textContent = "Running your comparison"; history.replaceState(null, "", `/static/bimanual.html${el("comparisonMode").value === "campaign" ? "?mode=campaign" : ""}`);
    if (!profile) await load(); else update();
  });
  el("recheck").addEventListener("click", load);
  window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
  document.addEventListener("click", event => { const anchor = event.target.closest?.("a"); if (dirty && anchor && anchor.target !== "_blank" && (anchor.pathname !== location.pathname || anchor.search !== location.search) && !window.confirm("Leave this comparison? Your unrun changes have not been saved.")) event.preventDefault(); });
  const params = new URLSearchParams(location.search);
  const campaignId = params.get("campaign"), jobId = params.get("job");
  if (campaignId || jobId) {
    active = {mode: campaignId ? "campaign" : "trial", id: campaignId || jobId}; el("comparisonMode").value = active.mode; modeContext(Boolean(campaignId)); el("execution").hidden = false; poll();
  } else {
    if (params.get("mode") === "campaign") { el("comparisonMode").value = "campaign"; el("comparisonMode").dispatchEvent(new Event("change")); dirty = false; }
    load();
  }
})();
