/* Chronology shows saved iterations, not a continuous improvement score. */
(function () {
  "use strict";
  const labels = {cases: "Cases changed", success_metrics: "Scoring changed", strategies: "Strategies changed", repetitions: "Attempts changed", environment: "Environment changed"};
  function render(container, data) {
    const doc = container.ownerDocument;
    const node = (tag, text, cls) => { const el = doc.createElement(tag); if (text != null) el.textContent = String(text); if (cls) el.className = cls; return el; };
    const link = (text, url) => { const el = node("a", text, "trajectory-action"); el.href = url; return el; };
    container.replaceChildren(node("h2", "Evaluation timeline"), node("p", "See what changed between saved iterations; changed test conditions are kept separate from improvement.", "muted"));
    const list = node("ol", null, "campaign-trajectory");
    for (const item of data.nodes || []) {
      const card = node("li", null, "trajectory-iteration"); card.dataset.campaignId = item.id;
      if (item.id === data.campaign_id) card.classList.add("trajectory-current");
      const heading = node("div", null, "trajectory-heading"), title = node("div");
      const date = node("time", new Date(item.created_at).toLocaleString(), "trajectory-date"); date.dateTime = item.created_at;
      title.append(date, node("h3", item.name)); heading.append(title, node("span", item.id === data.campaign_id ? "Current campaign" : item.status, "badge"));
      card.append(heading, node("p", `${item.case_count} cases · ${item.repetitions} repetitions${item.revision ? ` · ${item.revision}` : ""}`, "trajectory-meta"));
      if (item.evidence_kind === "synthetic_or_mixed") card.append(node("span", "Synthetic / mock evidence", "badge trajectory-evidence"));
      else if (!item.evidence_kind || item.evidence_kind === "unknown") card.append(node("span", "Evidence scope unknown", "badge trajectory-evidence"));
      const incoming = (data.edges || []).filter(edge => edge.campaign_id === item.id);
      const details = node("details", null, "trajectory-details"); details.append(node("summary", "Comparison details"));
      details.append(node("p", `Outcomes reflect current assessments as of ${data.as_of || "this request"}. Saved baseline comparisons retain frozen assessments.`));
      for (const edge of incoming) {
        const from = (data.nodes || []).find(entry => entry.id === edge.parent_id);
        const badges = node("div", null, "trajectory-badges");
        for (const change of edge.changes || []) badges.append(node("span", labels[change] || change, "badge"));
        if (!edge.changes?.length) badges.append(node("span", "Same recorded configuration", "badge"));
        if (!edge.comparable) badges.append(node("span", "Different conditions", "badge trajectory-condition"));
        card.append(badges);
        const relation = node("section", null, "trajectory-change"); relation.append(node("strong", `From ${from?.name || edge.parent_id}`));
        if (edge.baseline_revision_id) relation.append(node("p", `Frozen baseline revision ${edge.baseline_revision_id}`, "trajectory-reference"));
        relation.append(node("p", edge.comparable ? "Matching assessment conditions; paired outcomes are descriptive." : "Comparison conditions differ or cannot be established. No improvement claim."));
        for (const reason of edge.reasons || []) relation.append(node("p", reason));
        for (const comparison of edge.comparisons || []) {
          const counts = comparison.paired_counts;
          if (!comparison.comparable || !counts) continue;
          relation.append(node("p", `${comparison.baseline_strategy_id} → ${comparison.candidate_strategy_id}: ${counts.fail_to_pass || 0} failed → accepted, ${counts.pass_to_fail || 0} accepted → failed, ${counts.unknown || 0} unresolved pairs.`, "trajectory-pairs"));
          if (edge.baseline_revision_id) for (const outcome of comparison.baseline_outcomes || []) relation.append(node("p", `Frozen reference: ${outcome.passed} accepted · ${outcome.failed} rejected · ${outcome.unknown} unassessed.`));
        }
        details.append(relation);
      }
      for (const outcome of item.outcomes || []) {
        const version = (item.strategy_versions || []).find(strategy => strategy.id === outcome.strategy_id), row = node("div", null, "trajectory-outcome"), name = version?.name || outcome.strategy_id;
        const title = node("div", null, "trajectory-outcome-heading"); title.append(node("strong", name), node("span", Number.isFinite(outcome.latency_p95_ms) ? `${Math.round(outcome.latency_p95_ms)} ms p95` : "Timing unavailable", "trajectory-timing"));
        const bar = node("div", null, "trajectory-outcome-bar"), total = outcome.passed + outcome.failed + outcome.unknown;
        bar.setAttribute("role", "img"); bar.setAttribute("aria-label", `${name}: ${outcome.passed} accepted, ${outcome.failed} rejected, ${outcome.unknown} unassessed`);
        for (const key of ["passed", "failed", "unknown"]) { const part = node("span", null, `trajectory-${key}`); part.style.width = `${total ? outcome[key] / total * 100 : 0}%`; bar.append(part); }
        row.append(title, bar, node("p", `${outcome.passed} accepted · ${outcome.failed} rejected · ${outcome.unknown} unassessed`, "trajectory-counts")); card.append(row);
      }
      details.append(node("p", `Scoring contract: ${item.contract_id || "Legacy configured verification"}`, "trajectory-reference"));
      for (const strategy of item.strategy_versions || []) details.append(node("p", `${strategy.name} · ${strategy.id} · resolved configuration ${strategy.fingerprint || "unavailable"}`, "trajectory-reference"));
      if (item.case_revision_ids?.length) details.append(node("p", `Case revisions: ${item.case_revision_ids.join(", ")}`, "trajectory-reference"));
      for (const warning of item.warnings || []) details.append(node("p", warning));
      if (item.warnings?.length) card.append(node("span", "Source link unavailable", "badge"));
      details.append(node("p", "Pipeline latency is not physical task completion time. Paired outcomes do not establish statistical significance or hardware performance."));
      card.append(details);
      const actions = node("div", null, "trajectory-actions"); actions.append(link("Open results", `/static/datasets.html?step=review&campaign=${encodeURIComponent(item.id)}`), link("Download report", `/api/campaigns/${encodeURIComponent(item.id)}/report?format=html`)); card.append(actions); list.append(card);
    }
    container.append(list);
    if ((data.nodes || []).length === 1 && !(data.edges || []).length) container.append(node("p", "Improve this campaign to add another linked iteration.", "muted"));
  }
  async function mount(container, campaignId, fetcher = fetch) {
    const version = container.dataset.trajectoryRequest = String(Number(container.dataset.trajectoryRequest || 0) + 1);
    container.replaceChildren(); const loading = container.ownerDocument.createElement("p"); loading.textContent = "Loading connected evaluation history…"; loading.setAttribute("role", "status"); container.append(loading);
    try {
      const response = await fetcher(`/api/campaigns/${encodeURIComponent(campaignId)}/timeline`);
      if (!response.ok) throw new Error("Timeline unavailable");
      const data = await response.json();
      if (container.dataset.trajectoryRequest !== version || !container.isConnected) return;
      render(container, data);
    } catch {
      if (container.dataset.trajectoryRequest !== version || !container.isConnected) return;
      loading.textContent = "Evaluation timeline could not load. Saved campaign results remain available.";
    }
  }
  const api = {render, mount};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof window !== "undefined") window.RoveCampaignTrajectory = api;
})();
