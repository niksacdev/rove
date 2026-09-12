"use strict";

function sampleCaseHref(record) {
  const revision = record.case_revision_id;
  if (record.import_status !== "imported" || typeof revision !== "string" || !/^[A-Za-z0-9_.-]{1,128}$/.test(revision)) return null;
  return `/static/datasets.html?step=cases&pick=existing&case=${encodeURIComponent(revision)}`;
}

function createSampleCaseCard(record, apiBase = "", doc = document) {
  const node = (tag, text, className) => { const element = doc.createElement(tag); if (text != null) element.textContent = String(text); if (className) element.className = className; return element; };
  const card = node("article", null, "rounded-lg border border-f-border bg-f-surface");
  const href = sampleCaseHref(record);
  const selection = node(href ? "a" : "div", null, "flex items-center gap-3 w-full p-2 rounded-lg hover:bg-f-surface/80 transition-colors text-left group");
  if (href) { selection.href = href; selection.setAttribute("aria-label", `Select case: ${record.task}`); }
  const image = node("img");
  image.src = apiBase + "/data/" + record.filename;
  image.alt = "";
  image.loading = "lazy";
  image.className = "w-14 h-14 rounded-md object-cover border border-f-border shrink-0";
  const info = node("div", null, "flex-1 min-w-0");
  info.appendChild(node("p", record.task, "text-xs text-gray-200 group-hover:text-white line-clamp-2"));
  const source = [record.source?.dataset, record.robot ? String(record.robot).toUpperCase() : "", record.proprioception && record.state_dim ? `${record.state_dim}-DOF state` : "", record.difficulty].filter(Boolean).join(" · ");
  info.appendChild(node("p", source, "text-[10px] text-gray-500 mt-0.5"));
  if (record.category === "action") info.appendChild(node("span", "Action input", "text-[10px] text-gray-400"));
  if (href) info.appendChild(node("span", "Select case →", "text-[10px] text-f-purple block mt-1"));
  selection.appendChild(image); selection.appendChild(info); card.appendChild(selection);
  if (!href) {
    const footer = node("div", null, "px-3 py-2 border-t border-f-border text-[10px]");
    footer.appendChild(node("span", "Case unavailable", "text-gray-500"));
    if (record.import_error) {
      const reason = node("details", null, "mt-1 text-gray-400");
      reason.appendChild(node("summary", "Import details"));
      reason.appendChild(node("p", record.import_error));
      footer.appendChild(reason);
    }
    card.appendChild(footer);
  }
  return card;
}

if (typeof module !== "undefined" && module.exports) module.exports = {sampleCaseHref, createSampleCaseCard};
