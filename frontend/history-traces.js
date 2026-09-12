"use strict";

function laneExtent(lane) {
  return Math.max(1, ...lane.items.map(item => (item.offset_seconds ?? 0) + (item.duration_seconds ?? 0)));
}
function traceEvidenceHref(trialId, referenceId) {
  return `/api/trials/${encodeURIComponent(trialId)}/evidence/${encodeURIComponent(referenceId)}`;
}
function renderTraceLanes(trace, doc = document) {
  const node = (tag, text, cls) => { const el = doc.createElement(tag); if (text != null) el.textContent = text; if (cls) el.className = cls; return el; };
  const root = node("div", null, "trace-lanes");
  root.append(node("p", trace.note, "muted"));
  if (!trace.complete) root.append(node("p", `Partial trace: ${trace.loaded_events} of ${trace.total_events} events. Load the event list for the full record.`, "notice"));
  if (!trace.lanes.length) root.append(node("p", "No trace events were recorded.", "muted"));
  const extents = new Map();
  for (const lane of trace.lanes) extents.set(lane.clock_id, Math.max(extents.get(lane.clock_id) || 1, laneExtent(lane)));
  for (const lane of trace.lanes) {
    const group = node("section", null, "trace-lane");
    group.append(node("h4", lane.stage), node("p", `Clock: ${lane.clock_id} · alignment ${lane.alignment}`, "muted trace-clock"));
    const events = node("ol", null, "trace-items");
    for (const item of lane.items) {
      const row = node("li"), detail = node("details"), label = node("summary", `${item.name} · ${item.duration_seconds == null ? "duration unavailable" : `${item.duration_seconds.toFixed(3)} s`}`);
      detail.append(label);
      const track = node("div", null, "trace-track");
      if (item.offset_seconds != null && Number.isFinite(item.offset_seconds)) {
        const bar = node("span", null, "trace-bar");
        bar.style.marginLeft = `${Math.max(0, item.offset_seconds / extents.get(lane.clock_id) * 100)}%`;
        bar.style.width = `${Math.max(0.4, (item.duration_seconds ?? 0) / extents.get(lane.clock_id) * 100)}%`;
        track.append(bar);
        detail.append(node("p", `Offset ${item.offset_seconds.toFixed(3)} s from this clock's first recorded event.`, "muted"));
      } else track.append(node("span", "Producer timestamp unavailable", "muted"));
      for (const ref of item.evidence_refs || []) {
        const id = typeof ref === "string" ? ref : ref.id;
        if (!id) continue;
        const link = node("a", `Evidence: ${id}`); link.href = traceEvidenceHref(trace.trial_id, id); detail.append(link);
      }
      detail.append(node("pre", JSON.stringify(item, null, 2)));
      row.append(track, detail); events.append(row);
    }
    group.append(events); root.append(group);
  }
  return root;
}
if (typeof module !== "undefined" && module.exports) module.exports = {laneExtent, traceEvidenceHref, renderTraceLanes};
