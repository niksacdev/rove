/* Read-only history for an explicitly selected campaign; no default selection. */
(function () {
  "use strict";
  const element = id => document.getElementById(id);
  const requested = new URLSearchParams(location.search).getAll("campaign");
  const identity = requested.length === 1 ? requested[0] : "";
  const timeline = element("campaignHistoryTimeline"), status = element("campaignHistoryStatus"), refresh = element("refreshCampaignHistory");
  let version = 0;
  async function load() {
    const requestVersion = ++version;
    timeline.replaceChildren(); timeline.setAttribute("aria-busy", "true");
    refresh.disabled = true; element("historyViewResults").hidden = true;
    status.textContent = "Loading campaign history…";
    try {
      const response = await fetch(`/api/campaigns/${encodeURIComponent(identity)}/timeline`);
      if (!response.ok) throw new Error(response.status === 404 ? "This campaign was not found. Return to Campaigns to select a saved campaign." : "Campaign history could not load. Try Refresh; your saved results remain available.");
      const data = await response.json();
      if (requestVersion !== version) return;
      const selected = Array.isArray(data.nodes) && data.nodes.find(item => item.id === identity);
      if (data.campaign_id !== identity || !selected || !Array.isArray(data.edges)) throw new Error("The returned history does not match this campaign. Try Refresh or return to Campaigns.");
      element("campaignHistoryName").textContent = selected.name || "Untitled campaign";
      document.title = `ROVE · Campaign history · ${selected.name || "Untitled campaign"}`;
      const results = element("historyViewResults"); results.href = `/static/datasets.html?step=review&campaign=${encodeURIComponent(identity)}`; results.hidden = false;
      window.RoveCampaignTrajectory.render(timeline, data);
      status.textContent = `${data.nodes.length} saved iteration${data.nodes.length === 1 ? "" : "s"} linked to this campaign.`;
    } catch (error) {
      if (requestVersion !== version) return;
      status.textContent = error.message || "Campaign history could not load. Try Refresh.";
    } finally {
      if (requestVersion === version) { timeline.setAttribute("aria-busy", "false"); refresh.disabled = false; }
    }
  }
  if (!identity.trim()) {
    status.textContent = "Choose a campaign’s History action from Campaigns to see its saved iterations.";
    timeline.setAttribute("aria-busy", "false");
    return;
  }
  refresh.addEventListener("click", load);
  load();
})();
