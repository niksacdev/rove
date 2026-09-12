"use strict";
const API_BASE = window.location.origin;

// ---- State ----
let selectedFile = null;
let selectedUrdfFile = null;
let isRunning = false;
let currentEventSource = null;
let strategies = [];
let selectedStrategyIds = new Set();
let activeTabId = null;
let tabData = {};
let summaryResults = {}; // { [sid]: { status, success, latency_ms, currentStage, stageStatuses: {perceive,plan,act,verify} } }
let summaryEl = null; // DOM element for summary tab content
let compareEl = null; // DOM element for comparison tab content
let currentView = "home"; // "home" | "strategies" | "models" | "settings" | "evaluation"
let runHistory = []; // per-eval data entries
let activeHistoryIndex = -1;
let modelsData = null;
let configData = null; // full rove.yaml as JSON
let sampleIndex = 0;
let historyFilterText = "";
let historyFilterStrategy = "";
let latencyBudgetMs = null; // loaded from /api/config
let stageBudgets = {}; // per-stage budgets { perceive: 3000, plan: 3000, act: 3000, verify: 1000 }

function renderNarrativeSection(sec) {
  var secDiv = document.createElement("div");
  secDiv.className = "border-l-2 pl-3 py-1 " + sec.borderClass;
  var secLabel = document.createElement("span");
  secLabel.className = "text-[10px] font-semibold block mb-0.5 " + sec.labelClass;
  secLabel.textContent = sec.label;
  secDiv.appendChild(secLabel);
  var secText = document.createElement("p");
  secText.className = "text-xs text-gray-400";
  secText.textContent = sec.text;
  secDiv.appendChild(secText);
  return secDiv;
}

function parseVerifyNarrative(text) {
  // All known section headers the LLM may produce (old and new prompt formats)
  var allHeaders = [
    { key: "Verdict", group: "top", borderClass: "border-emerald-500/40", labelClass: "text-emerald-400" },
    { key: "VLA Output Analysis", group: "top", borderClass: "border-violet-500/40", labelClass: "text-violet-400" },
    { key: "VLA Action Output", group: "top", borderClass: "border-violet-500/40", labelClass: "text-violet-400", displayAs: "VLA Output Analysis" },
    { key: "Evidence — Scene Analysis", group: "evidence", borderClass: "border-blue-500/40", labelClass: "text-blue-400", displayAs: "Scene Analysis" },
    { key: "Scene Analysis", group: "evidence", borderClass: "border-blue-500/40", labelClass: "text-blue-400" },
    { key: "Evidence — Planned Approach", group: "evidence", borderClass: "border-cyan-500/40", labelClass: "text-cyan-400", displayAs: "Planned Approach" },
    { key: "Planned Approach", group: "evidence", borderClass: "border-cyan-500/40", labelClass: "text-cyan-400" },
    { key: "Evidence — Dynamics Verification", group: "evidence", borderClass: "border-orange-500/40", labelClass: "text-orange-400", displayAs: "Dynamics Verification" },
    { key: "Dynamics Verification", group: "evidence", borderClass: "border-orange-500/40", labelClass: "text-orange-400" },
  ];

  // Build regex to find all section boundaries
  var headerPatterns = allHeaders.map(function(h) {
    return { def: h, re: new RegExp("\\*{0,2}" + h.key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\*{0,2}\\s*:?\\s*", "i") };
  });

  // Find all section start positions
  var found = [];
  headerPatterns.forEach(function(hp) {
    var m = text.match(hp.re);
    if (m) {
      found.push({ def: hp.def, idx: text.indexOf(m[0]), matchLen: m[0].length });
    }
  });
  // Sort by position, deduplicate by display name
  found.sort(function(a, b) { return a.idx - b.idx; });
  var seen = {};
  found = found.filter(function(f) {
    var name = f.def.displayAs || f.def.key;
    if (seen[name]) return false;
    seen[name] = true;
    return true;
  });

  if (found.length < 2) {
    // Fallback: try paragraph-based detection
    return parseVerifyNarrativeFallback(text);
  }

  // Extract section texts
  var topSections = [];
  var evidenceSections = [];
  for (var i = 0; i < found.length; i++) {
    var start = found[i].idx + found[i].matchLen;
    var end = (i + 1 < found.length) ? found[i + 1].idx : text.length;
    var sectionText = text.substring(start, end).trim();
    if (!sectionText) continue;
    var entry = {
      label: found[i].def.displayAs || found[i].def.key,
      text: sectionText,
      borderClass: found[i].def.borderClass,
      labelClass: found[i].def.labelClass,
      group: found[i].def.group,
    };
    if (entry.group === "evidence") {
      evidenceSections.push(entry);
    } else {
      topSections.push(entry);
    }
  }

  return { top: topSections, evidence: evidenceSections };
}

function parseVerifyNarrativeFallback(text) {
  var altPatterns = [
    { pattern: /(?:Based on|verdict|evaluation.*trajectory)/i, label: "Verdict", group: "top", borderClass: "border-emerald-500/40", labelClass: "text-emerald-400" },
    { pattern: /(?:VLA produced|VLA output|action.*steps|trajectory.*steps)/i, label: "VLA Output Analysis", group: "top", borderClass: "border-violet-500/40", labelClass: "text-violet-400" },
    { pattern: /(?:Our analysis|scene.*found|detected.*objects)/i, label: "Scene Analysis", group: "evidence", borderClass: "border-blue-500/40", labelClass: "text-blue-400" },
    { pattern: /(?:expected.*sequence|planned|For.*task.*should)/i, label: "Planned Approach", group: "evidence", borderClass: "border-cyan-500/40", labelClass: "text-cyan-400" },
    { pattern: /(?:MuJoCo dynamics|dynamics.*shows|dynamics.*analysis|joint limit)/i, label: "Dynamics Verification", group: "evidence", borderClass: "border-orange-500/40", labelClass: "text-orange-400" },
  ];
  var paragraphs = text.split(/\n\n+/).filter(function(p) { return p.trim().length > 0; });
  if (paragraphs.length < 2) return null;
  var topSections = [];
  var evidenceSections = [];
  for (var pi = 0; pi < paragraphs.length; pi++) {
    var matched = false;
    for (var ai = 0; ai < altPatterns.length; ai++) {
      var ap = altPatterns[ai];
      var alreadyUsed = (ap.group === "top" ? topSections : evidenceSections).some(function(s) { return s.label === ap.label; });
      if (!alreadyUsed && ap.pattern.test(paragraphs[pi])) {
        var entry = { label: ap.label, text: paragraphs[pi].trim(), borderClass: ap.borderClass, labelClass: ap.labelClass, group: ap.group };
        if (ap.group === "evidence") evidenceSections.push(entry);
        else topSections.push(entry);
        matched = true;
        break;
      }
    }
    if (!matched) {
      // Append to last section
      var lastArr = evidenceSections.length > 0 ? evidenceSections : topSections;
      if (lastArr.length > 0) lastArr[lastArr.length - 1].text += "\n\n" + paragraphs[pi].trim();
    }
  }
  if (topSections.length + evidenceSections.length < 2) return null;
  return { top: topSections, evidence: evidenceSections };
}

function formatLatency(ms) {
  if (ms == null) return "\u2014";
  return Math.round(ms) + "ms";
}

function latencyColorClass(ms, budgetMs) {
  if (budgetMs === undefined) budgetMs = latencyBudgetMs;
  if (ms == null || budgetMs == null) return "text-gray-300";
  var ratio = ms / budgetMs;
  if (ratio <= 0.8) return "text-green-400";
  if (ratio <= 1.0) return "text-yellow-400";
  return "text-red-400";
}

function getStageBudget(stage, strategyId) {
  // Strategy-level override
  if (strategyId && configData && configData.strategies && configData.strategies[strategyId]) {
    var sb = configData.strategies[strategyId].latency_budget;
    if (sb && sb[stage] != null) return sb[stage];
  }
  // Default fallback
  if (stageBudgets[stage] != null) return stageBudgets[stage];
  return null;
}

const SAMPLE_QUESTIONS = [
  "Pick up the red bracket from the table and place it in bin A",
  "Stack the blue cube on top of the green cylinder",
  "Move the wrench from the left bin to the right bin",
  "Grasp the bolt and insert it into the threaded hole",
];

// ---- DOM refs ----
const chatArea      = document.getElementById("chatArea");
const welcomeMsg    = document.getElementById("welcomeMsg");
const imageInput    = document.getElementById("imageInput");
const imagePreview  = document.getElementById("imagePreview");
const previewImg    = document.getElementById("previewImg");
const removeImageBtn = document.getElementById("removeImage");
const taskInput     = document.getElementById("taskInput");
const evalBtn       = document.getElementById("evalBtn");
const connDot       = document.getElementById("connectionDot");
const connLabel     = document.getElementById("connectionLabel");
const configToggle  = document.getElementById("configToggle");
const configContent = document.getElementById("configContent");
const configChevron = document.getElementById("configChevron");
const strategyGrid  = document.getElementById("strategyGrid");
const selectedCount = document.getElementById("selectedCount");
const tabBar        = document.getElementById("tabBar");
const historyList   = document.getElementById("historyList");
const strategiesView = document.getElementById("strategiesView");
const modelsView    = document.getElementById("modelsView");
const examplesView  = document.getElementById("examplesView");
const settingsView  = document.getElementById("settingsView");
const plusBtn       = document.getElementById("plusBtn");
const plusPopover   = document.getElementById("plusPopover");
const urdfInput     = document.getElementById("urdfInput");
const urdfPreview   = document.getElementById("urdfPreview");
const urdfName      = document.getElementById("urdfName");
const removeUrdfBtn = document.getElementById("removeUrdf");

// ---- Stage metadata ----
const STAGES = {
  perceive: { label: "Scene Analysis",     icon: "eye"          },
  plan:     { label: "Task Planning",       icon: "brain"        },
  act:      { label: "Actions",    icon: "bot"          },
  dynamics: { label: "Dynamics Analysis",   icon: "activity"     },
  verify:   { label: "Verification",  icon: "shield-check" },
};

const STAGE_COLORS = {
  perceive: { bg: "bg-purple-500/15", text: "text-purple-300" },
  plan:     { bg: "bg-purple-500/15", text: "text-purple-300" },
  act:      { bg: "bg-sky-500/15",     text: "text-sky-300"     },
  dynamics: { bg: "bg-blue-500/15",   text: "text-blue-300"    },
  verify:   { bg: "bg-amber-500/15", text: "text-amber-300" },
};

// Distinct colors for strategy tabs (up to 8 strategies)
const STRATEGY_COLORS = [
  "#7c3aed", // purple
  "#3b82f6", // blue
  "#10b981", // emerald
  "#f59e0b", // amber
  "#ef4444", // red
  "#ec4899", // pink
  "#06b6d4", // cyan
  "#84cc16", // lime
];
var strategyColorMap = {}; // strategy_id → color hex

// Failure category labels and colors
var FAILURE_LABELS = {
  "perceive_error": "Perception Error",
  "perceive_miss": "Perception Miss",
  "plan_error": "Planning Error",
  "plan_low_confidence": "Low Confidence",
  "act_error": "Action Error",
  "dynamics_error": "Dynamics Error",
  "action_dynamics_violation": "Dynamics Violation",
  "action_torque_violation": "Torque Violation",
  "action_singularity": "Near Singularity",
  "action_infeasible": "Infeasible Action",
  "verify_error": "Verify Error",
  "verification_mismatch": "Verify Mismatch",
};

function getFailureLabel(cat) {
  return FAILURE_LABELS[cat] || cat;
}

function getFailureBadgeClass(cat) {
  if (cat && cat.indexOf("error") !== -1) return "bg-red-500/15 text-red-400";
  if (cat === "perceive_miss" || cat === "plan_low_confidence") return "bg-amber-500/15 text-amber-400";
  if (cat && cat.indexOf("action") !== -1) return "bg-orange-500/15 text-orange-400";
  if (cat && cat.indexOf("verification") !== -1) return "bg-yellow-500/15 text-yellow-400";
  return "bg-gray-500/15 text-gray-400";
}

// ---- Provenance Card ----
function renderProvenanceCard(prov, parentEl) {
  var wrap = document.createElement("div");
  wrap.className = "bg-f-surface border border-f-border rounded-xl overflow-hidden mt-3";

  var header = document.createElement("button");
  header.className = "w-full flex items-center justify-between px-4 py-2.5 text-xs text-gray-400 hover:text-gray-300 transition-colors cursor-pointer";
  var headerLabel = document.createElement("span");
  headerLabel.className = "font-semibold uppercase tracking-wider text-[10px]";
  headerLabel.textContent = "Run Info";
  var headerToggle = document.createElement("span");
  headerToggle.className = "text-[10px]";
  headerToggle.textContent = "click to expand";
  header.appendChild(headerLabel);
  header.appendChild(headerToggle);

  var body = document.createElement("div");
  body.className = "px-4 pb-3 border-t border-f-border/50";
  body.style.display = "none";

  header.addEventListener("click", function() {
    var shown = body.style.display !== "none";
    body.style.display = shown ? "none" : "";
    headerToggle.textContent = shown ? "click to expand" : "click to collapse";
  });

  var grid = document.createElement("div");
  grid.className = "grid grid-cols-2 gap-x-6 gap-y-1.5 pt-2.5 text-[11px]";

  var fields = [
    ["Timestamp", prov.timestamp ? new Date(prov.timestamp).toLocaleString() : "\u2014"],
    ["Hostname", prov.hostname || "\u2014"],
    ["ROVE Version", prov.rove_version || "\u2014"],
    ["Python", prov.python_version || "\u2014"],
    ["Image Hash", prov.image_sha256 ? prov.image_sha256.substring(0, 12) + "\u2026" : "\u2014"],
    ["Config Hash", prov.config_hash ? prov.config_hash.substring(0, 12) + "\u2026" : "\u2014"],
    ["Seed", prov.seed != null ? String(prov.seed) : "none"],
    ["Strategies", (prov.strategy_ids || []).join(", ") || "\u2014"],
  ];

  fields.forEach(function(pair) {
    var label = document.createElement("span");
    label.className = "text-gray-500";
    label.textContent = pair[0];
    var value = document.createElement("span");
    value.className = "text-gray-300 font-mono truncate";
    value.textContent = pair[1];
    grid.appendChild(label);
    grid.appendChild(value);
  });

  body.appendChild(grid);
  wrap.appendChild(header);
  wrap.appendChild(body);
  parentEl.appendChild(wrap);
}

// ---- Insights Card ----
function renderInsightsCard(insights, parentEl) {
  if (!insights) return;

  var card = document.createElement("div");
  card.className = "bg-f-surface border border-f-border rounded-xl p-4 mt-3 space-y-4";

  // Section header
  var headerSpan = document.createElement("span");
  headerSpan.className = "text-[10px] font-semibold text-gray-500 uppercase tracking-wider";
  headerSpan.textContent = "Run Insights";
  card.appendChild(headerSpan);

  // Top Finding banner
  if (insights.top_finding) {
    var banner = document.createElement("div");
    banner.className = "px-3 py-2 rounded-lg text-xs font-medium bg-purple-500/10 text-purple-300 border border-purple-500/20";
    banner.textContent = insights.top_finding;
    card.appendChild(banner);
  }

  // Confidence Scores — per-strategy confidence bars
  if (insights.confidence_scores && insights.confidence_scores.length > 0) {
    var csSection = document.createElement("div");
    csSection.className = "space-y-1.5";
    var csTitle = document.createElement("span");
    csTitle.className = "text-[10px] font-semibold text-gray-500 uppercase tracking-wider";
    csTitle.textContent = "Confidence Scores";
    csSection.appendChild(csTitle);

    insights.confidence_scores.forEach(function(entry) {
      var score = entry.confidence || 0;
      var pct = (score * 100).toFixed(1);
      var color = score >= 0.7 ? "green" : (score >= 0.4 ? "yellow" : "red");

      var barWrap = document.createElement("div");
      barWrap.className = "flex items-center gap-2";

      var label = document.createElement("span");
      label.className = "text-[11px] text-gray-400 w-40 shrink-0 truncate";
      label.textContent = entry.display_name || entry.strategy_id;

      var barBg = document.createElement("div");
      barBg.className = "flex-1 h-1.5 bg-f-elevated rounded-full overflow-hidden";
      var bar = document.createElement("div");
      bar.className = "h-full bg-" + color + "-500 rounded-full transition-all duration-500";
      bar.style.width = Math.max(parseFloat(pct), 2) + "%";
      barBg.appendChild(bar);

      var pctLabel = document.createElement("span");
      pctLabel.className = "text-[11px] font-mono w-12 text-right text-" + color + "-400";
      pctLabel.textContent = pct + "%";

      barWrap.appendChild(label);
      barWrap.appendChild(barBg);
      barWrap.appendChild(pctLabel);
      csSection.appendChild(barWrap);
    });
    card.appendChild(csSection);
  }

  // Reasoning Trail — collapsible per-strategy with rich stage rendering
  if (insights.reasoning_trail && Object.keys(insights.reasoning_trail).length > 0) {
    var rtSection = document.createElement("div");
    rtSection.className = "space-y-1.5";
    var rtTitle = document.createElement("span");
    rtTitle.className = "text-[10px] font-semibold text-gray-500 uppercase tracking-wider";
    rtTitle.textContent = "Reasoning Trail";
    rtSection.appendChild(rtTitle);

    Object.keys(insights.reasoning_trail).forEach(function(sid) {
      var trail = insights.reasoning_trail[sid];
      var strat = strategies.find(function(s) { return s.id === sid; });
      var displayName = strat ? strat.display_name : sid;

      var details = document.createElement("details");
      details.className = "border border-f-border/50 rounded-lg overflow-hidden";
      var summary = document.createElement("summary");
      summary.className = "px-3 py-2 text-[11px] text-gray-400 cursor-pointer hover:text-gray-300 transition-colors";
      summary.textContent = displayName;
      details.appendChild(summary);

      var body = document.createElement("div");
      body.className = "px-3 pb-3 space-y-3";

      var outputs = trail.stage_outputs || {};

      // Perceive — rich rendering via renderPerceive
      if (outputs.perceive) {
        var percSection = document.createElement("div");
        percSection.className = "space-y-1";
        var percLabel = document.createElement("span");
        percLabel.className = "text-[10px] font-semibold text-blue-400 uppercase";
        percLabel.textContent = "Perceive";
        percSection.appendChild(percLabel);
        var percBody = document.createElement("div");
        renderPerceive(percBody, outputs.perceive);
        percSection.appendChild(percBody);
        body.appendChild(percSection);
      }

      // Plan — rich rendering via renderPlan
      if (outputs.plan) {
        var planSection = document.createElement("div");
        planSection.className = "space-y-1";
        var planLabel = document.createElement("span");
        planLabel.className = "text-[10px] font-semibold text-purple-400 uppercase";
        planLabel.textContent = "Plan";
        planSection.appendChild(planLabel);
        var planBody = document.createElement("div");
        renderPlan(planBody, outputs.plan);
        planSection.appendChild(planBody);
        body.appendChild(planSection);
      }

      // Act — rich rendering via renderAct
      if (outputs.act) {
        var actSection = document.createElement("div");
        actSection.className = "space-y-1";
        var actLabel = document.createElement("span");
        actLabel.className = "text-[10px] font-semibold text-emerald-400 uppercase";
        actLabel.textContent = "Act";
        actSection.appendChild(actLabel);
        var actBody = document.createElement("div");
        renderAct(actBody, outputs.act);
        actSection.appendChild(actBody);
        body.appendChild(actSection);
      }

      // Dynamics — rich rendering via renderDynamics (if not embedded in act)
      if (outputs.dynamics && !outputs.dynamics.skipped) {
        var dynSection = document.createElement("div");
        dynSection.className = "space-y-1";
        var dynLabel = document.createElement("span");
        dynLabel.className = "text-[10px] font-semibold text-blue-400 uppercase";
        dynLabel.textContent = "Dynamics";
        dynSection.appendChild(dynLabel);
        var dynBody = document.createElement("div");
        renderDynamics(dynBody, outputs.dynamics);
        dynSection.appendChild(dynBody);
        body.appendChild(dynSection);
      }

      // Verify — rich rendering via renderVerify
      if (outputs.verify) {
        var verifySection = document.createElement("div");
        verifySection.className = "space-y-1";
        var verifyLabel = document.createElement("span");
        verifyLabel.className = "text-[10px] font-semibold text-amber-400 uppercase";
        verifyLabel.textContent = "Verify";
        verifySection.appendChild(verifyLabel);
        var verifyBody = document.createElement("div");
        renderVerify(verifyBody, outputs.verify);
        verifySection.appendChild(verifyBody);
        body.appendChild(verifySection);
      }

      // Raw JSON toggle per stage
      if (outputs && Object.keys(outputs).length > 0) {
        var rawToggleBtn = document.createElement("button");
        rawToggleBtn.className = "flex items-center gap-1 text-[10px] text-gray-500 hover:text-gray-300 transition-colors mt-1";
        rawToggleBtn.type = "button";
        var rawChevron = document.createElement("span");
        rawChevron.className = "text-[10px]";
        rawChevron.textContent = "\u25B6";
        rawToggleBtn.appendChild(rawChevron);
        var rawToggleText = document.createElement("span");
        rawToggleText.textContent = "Raw output";
        rawToggleBtn.appendChild(rawToggleText);
        body.appendChild(rawToggleBtn);

        var rawPanel = document.createElement("div");
        rawPanel.className = "json-expand";
        var rawPre = document.createElement("pre");
        rawPre.className = "text-[11px] text-gray-500 bg-black/30 rounded-lg p-3 overflow-x-auto";
        rawPre.textContent = JSON.stringify(outputs, null, 2);
        rawPanel.appendChild(rawPre);
        body.appendChild(rawPanel);

        (function(panel, chevron) {
          rawToggleBtn.addEventListener("click", function() {
            panel.classList.toggle("open");
            chevron.textContent = panel.classList.contains("open") ? "\u25BC" : "\u25B6";
          });
        })(rawPanel, rawChevron);
      }

      details.appendChild(body);
      rtSection.appendChild(details);
    });
    card.appendChild(rtSection);
  }

  parentEl.appendChild(card);
}

// ---- Theme ----
function initTheme() {
  var saved = localStorage.getItem("rove-theme");
  var theme = saved || "dark";
  applyTheme(theme);

  document.getElementById("themeToggle").addEventListener("click", function() {
    var current = document.documentElement.getAttribute("data-theme") || "dark";
    var next = current === "dark" ? "light" : "dark";
    applyTheme(next);
    localStorage.setItem("rove-theme", next);
  });
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  var sunIcon = document.getElementById("themeIconSun");
  var moonIcon = document.getElementById("themeIconMoon");
  if (theme === "light") {
    sunIcon.classList.add("hidden");
    moonIcon.classList.remove("hidden");
  } else {
    moonIcon.classList.add("hidden");
    sunIcon.classList.remove("hidden");
  }
}

// ---- Initialize ----
document.addEventListener("DOMContentLoaded", async function() {
  initTheme();
  lucide.createIcons();
  await loadStrategies();
  loadModels();
  loadConfig();
  autoResizeTextarea();
  initConfigPanel();
  initSidebarNav();
  initTopNav();
  initGettingStarted();
  restoreHistory();
});

// ---- Load config from API ----
async function loadConfig() {
  try {
    var res = await fetch(API_BASE + "/api/config");
    if (!res.ok) throw new Error("HTTP " + res.status);
    configData = await res.json();
    if (configData && configData.defaults && configData.defaults.latency_budget_ms) {
      latencyBudgetMs = configData.defaults.latency_budget_ms;
    }
    if (configData && configData.defaults && configData.defaults.latency_budget) {
      stageBudgets = configData.defaults.latency_budget;
    }
  } catch (e) {
    console.error("Failed to load config:", e);
  }
}

// ---- Top nav click handlers ----
function initTopNav() {
  document.querySelectorAll("header .nav-link").forEach(function(link) {
    link.addEventListener("click", function(e) {
      e.preventDefault();
      var text = link.textContent.trim().toLowerCase();
      if (text === "evaluate") {
        // If a run is active, restore the evaluation view instead of going home
        if (isRunning || Object.keys(tabData).length > 0) {
          restoreEvaluation();
        } else {
          activeHistoryIndex = -1;
          switchView("home");
        }
      } else if (text === "strategies") {
        activeHistoryIndex = -1;
        switchView("strategies");
      } else if (text === "endpoints") {
        activeHistoryIndex = -1;
        switchView("models");
      }
      updateTopNav(text);
    });
  });
}

function restoreEvaluation() {
  currentView = "evaluation";

  // Hide all non-eval views
  welcomeMsg.classList.add("hidden");
  strategiesView.classList.add("hidden");
  modelsView.classList.add("hidden");
  examplesView.classList.add("hidden");
  settingsView.classList.add("hidden");

  // Unhide evaluation elements
  chatArea.querySelectorAll(".eval-content").forEach(function(el) { el.classList.remove("hidden"); });

  // Show tabBar if multi-strategy
  if (Object.keys(tabData).length > 1 || summaryEl) {
    tabBar.classList.remove("hidden");
  }

  // Show the active tab content
  chatArea.querySelectorAll("[id^='tab-content-']").forEach(function(el) { el.classList.add("hidden"); });
  var targetEl = null;
  if (activeTabId === "__summary__" && summaryEl) {
    targetEl = summaryEl;
  } else if (activeTabId === "__compare__" && compareEl) {
    targetEl = compareEl;
  } else if (tabData[activeTabId]) {
    targetEl = tabData[activeTabId].el;
  }
  if (targetEl) {
    if (!targetEl.parentNode) {
      chatArea.appendChild(targetEl);
    }
    targetEl.classList.remove("hidden");
  }

  // Update sidebar/topnav state
  document.querySelectorAll(".sidebar-nav-link").forEach(function(l) { l.classList.remove("active"); });
  updateTopNav("evaluate");

  scrollToBottom();
}

function updateTopNav(activeText) {
  document.querySelectorAll("header .nav-link").forEach(function(link) {
    var t = link.textContent.trim().toLowerCase();
    if (t === activeText) {
      link.classList.add("active");
      link.setAttribute("aria-current", "page");
    } else {
      link.classList.remove("active");
      link.removeAttribute("aria-current");
    }
  });
}

// ---- View switching ----
function switchView(view) {
  currentView = view;

  // Hide all views
  welcomeMsg.classList.add("hidden");
  strategiesView.classList.add("hidden");
  modelsView.classList.add("hidden");
  examplesView.classList.add("hidden");
  settingsView.classList.add("hidden");

  // Hide (not destroy) evaluation elements so SSE can keep writing to them
  chatArea.querySelectorAll(".eval-content").forEach(function(el) { el.classList.add("hidden"); });
  chatArea.querySelectorAll("[id^='tab-content-']").forEach(function(el) { el.classList.add("hidden"); });
  tabBar.classList.add("hidden");

  // Update sidebar nav active state
  document.querySelectorAll(".sidebar-nav-link").forEach(function(link) {
    if (link.getAttribute("data-view") === view) {
      link.classList.add("active");
    } else {
      link.classList.remove("active");
    }
  });

  // Update top nav active state
  var navMap = { home: "evaluate", strategies: "strategies", models: "endpoints", examples: "evaluate", settings: "evaluate" };
  updateTopNav(navMap[view] || "evaluate");

  // Update history active state
  document.querySelectorAll(".history-item").forEach(function(item) {
    item.classList.remove("active", "bg-f-surface");
  });

  switch (view) {
    case "home":
      welcomeMsg.classList.remove("hidden");
      break;
    case "strategies":
      renderStrategiesView();
      strategiesView.classList.remove("hidden");
      break;
    case "models":
      renderModelsView();
      modelsView.classList.remove("hidden");
      break;
    case "examples":
      renderExamplesView();
      examplesView.classList.remove("hidden");
      break;
    case "settings":
      renderSettingsView();
      settingsView.classList.remove("hidden");
      break;
    case "evaluation":
      // Handled by showHistoryEntry or restoreEvaluation
      break;
  }
}

function initGettingStarted() {
  var toggle = document.getElementById("gettingStartedToggle");
  var content = document.getElementById("gettingStartedContent");
  var arrow = document.getElementById("gettingStartedArrow");
  if (!toggle || !content || !arrow) return;
  toggle.addEventListener("click", function() {
    var expanded = toggle.getAttribute("aria-expanded") === "true";
    toggle.setAttribute("aria-expanded", String(!expanded));
    if (expanded) {
      content.classList.add("hidden");
      arrow.style.transform = "rotate(0deg)";
    } else {
      content.classList.remove("hidden");
      arrow.style.transform = "rotate(90deg)";
    }
  });
}

function initSidebarNav() {
  document.querySelectorAll(".sidebar-nav-link").forEach(function(link) {
    link.addEventListener("click", function(e) {
      e.preventDefault();
      var view = link.getAttribute("data-view");
      activeHistoryIndex = -1;
      switchView(view);
    });
  });
}

// ---- Strategies view (from configData) ----
function renderStrategiesView() {
  strategiesView.textContent = "";
  var wrapper = document.createElement("div");
  wrapper.className = "max-w-4xl mx-auto";

  var heading = document.createElement("h2");
  heading.className = "text-lg font-semibold mb-1";
  heading.textContent = "Strategies";
  wrapper.appendChild(heading);

  var stratList = configData && configData.strategies ? Object.entries(configData.strategies) : [];
  var subtitle = document.createElement("p");
  subtitle.className = "text-[13px] text-gray-500 mb-4";
  subtitle.textContent = stratList.length + " pipeline configurations defined in rove.yaml";
  wrapper.appendChild(subtitle);

  if (stratList.length === 0) {
    var empty = document.createElement("p");
    empty.className = "text-sm text-gray-500";
    empty.textContent = configData ? "No strategies defined" : "Loading config...";
    wrapper.appendChild(empty);
    strategiesView.appendChild(wrapper);
    return;
  }

  var grid = document.createElement("div");
  grid.className = "grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3";

  stratList.forEach(function(entry) {
    var sid = entry[0];
    var s = entry[1];
    var card = document.createElement("div");
    card.className = "bg-f-surface border border-f-border rounded-xl p-4 space-y-2";

    var header = document.createElement("div");
    header.className = "flex items-center justify-between";
    var name = document.createElement("span");
    name.className = "text-sm font-semibold text-gray-200";
    name.textContent = s.display_name || sid;
    header.appendChild(name);

    if (s.tags && s.tags.length) {
      var tagWrap = document.createElement("div");
      tagWrap.className = "flex gap-1";
      s.tags.forEach(function(t) {
        var tag = document.createElement("span");
        tag.className = "text-[10px] bg-f-elevated text-gray-400 px-1.5 py-0.5 rounded";
        tag.textContent = t;
        tagWrap.appendChild(tag);
      });
      header.appendChild(tagWrap);
    }
    card.appendChild(header);

    if (s.description) {
      var desc = document.createElement("p");
      desc.className = "text-xs text-gray-500";
      desc.textContent = s.description;
      card.appendChild(desc);
    }

    var badges = document.createElement("div");
    badges.className = "flex flex-wrap gap-1";
    ["perceive", "plan", "act", "verify"].forEach(function(stage) {
      if (!s[stage]) return;
      var colors = STAGE_COLORS[stage];
      var badge = document.createElement("span");
      badge.className = "text-[10px] " + colors.bg + " " + colors.text + " px-1.5 py-0.5 rounded";
      badge.textContent = stage.charAt(0).toUpperCase() + ": " + s[stage];
      badges.appendChild(badge);
    });
    if (s.sim) {
      var simBadge = document.createElement("span");
      simBadge.className = "text-[10px] bg-blue-500/15 text-blue-300 px-1.5 py-0.5 rounded";
      simBadge.textContent = "Sim: " + s.sim;
      badges.appendChild(simBadge);
    }
    card.appendChild(badges);

    grid.appendChild(card);
  });

  wrapper.appendChild(grid);
  strategiesView.appendChild(wrapper);
}

// ---- Models view (from configData, grouped by type) ----
async function loadModels() {
  try {
    var res = await fetch(API_BASE + "/api/models");
    if (!res.ok) throw new Error("HTTP " + res.status);
    modelsData = await res.json();
  } catch (e) {
    console.error("Failed to load models:", e);
  }
}

function renderModelsView() {
  modelsView.textContent = "";
  var wrapper = document.createElement("div");
  wrapper.className = "max-w-4xl mx-auto";

  var heading = document.createElement("h2");
  heading.className = "text-lg font-semibold mb-1";
  heading.textContent = "Endpoint Registry";
  wrapper.appendChild(heading);

  var endpoints = configData && configData.endpoints ? configData.endpoints : null;
  if (!endpoints) {
    var subtitle = document.createElement("p");
    subtitle.className = "text-[13px] text-gray-500 mb-4";
    subtitle.textContent = "Loading config...";
    wrapper.appendChild(subtitle);
    modelsView.appendChild(wrapper);
    return;
  }

  // Regroup flat endpoints by type
  var grouped = {};
  Object.entries(endpoints).forEach(function(e) {
    var id = e[0], ep = e[1], t = ep.type || "unknown";
    if (!grouped[t]) grouped[t] = {};
    grouped[t][id] = ep;
  });

  var totalCount = Object.keys(endpoints).length;
  var subtitle2 = document.createElement("p");
  subtitle2.className = "text-[13px] text-gray-500 mb-4";
  subtitle2.textContent = totalCount + " endpoints defined in rove.yaml";
  wrapper.appendChild(subtitle2);

  var typeLabels = { vlm: "VLM (Vision-Language)", vla: "VLA (Vision-Language-Action)", llm: "LLM (Language)", grounding: "Grounding", agent: "Agent", sim: "Simulation" };
  var typeIcons = { vlm: "eye", vla: "bot", llm: "brain", grounding: "crosshair", agent: "workflow", sim: "box" };

  Object.keys(typeLabels).forEach(function(type) {
    var group = grouped[type];
    if (!group || typeof group !== "object") return;
    var entries = Object.entries(group);
    if (entries.length === 0) return;

    var section = document.createElement("div");
    section.className = "mb-6";

    var sectionHeader = document.createElement("h3");
    sectionHeader.className = "text-sm font-semibold text-gray-300 mb-2 flex items-center gap-2";
    var icon = document.createElement("i");
    icon.setAttribute("data-lucide", typeIcons[type] || "cpu");
    icon.className = "w-4 h-4 text-gray-500";
    sectionHeader.appendChild(icon);
    var labelSpan = document.createElement("span");
    labelSpan.textContent = typeLabels[type] || type;
    sectionHeader.appendChild(labelSpan);
    var countSpan = document.createElement("span");
    countSpan.className = "text-[10px] text-gray-600";
    countSpan.textContent = "(" + entries.length + ")";
    sectionHeader.appendChild(countSpan);
    section.appendChild(sectionHeader);

    var grid = document.createElement("div");
    grid.className = "grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2";

    entries.forEach(function(entry) {
      var mid = entry[0];
      var m = entry[1];
      var card = document.createElement("div");
      card.className = "bg-f-surface border border-f-border rounded-lg p-3 space-y-1.5";

      var row = document.createElement("div");
      row.className = "flex items-center justify-between";
      var nameSpan = document.createElement("span");
      nameSpan.className = "text-xs font-semibold text-gray-200";
      nameSpan.textContent = m.display_name || mid;
      row.appendChild(nameSpan);

      var statusDot = document.createElement("span");
      var isAvail = m.adapter && m.adapter.startsWith("mock");
      statusDot.className = "inline-block w-2 h-2 rounded-full " + (isAvail ? "bg-green-500" : "bg-gray-600");
      statusDot.title = isAvail ? "Available (mock)" : "Requires setup";
      row.appendChild(statusDot);
      card.appendChild(row);

      var idSpan = document.createElement("span");
      idSpan.className = "text-[10px] text-gray-600 font-mono block";
      idSpan.textContent = mid;
      card.appendChild(idSpan);

      if (m.adapter) {
        var adapterBadge = document.createElement("span");
        adapterBadge.className = "text-[10px] bg-f-elevated text-gray-400 px-1.5 py-0.5 rounded inline-block";
        adapterBadge.textContent = m.adapter;
        card.appendChild(adapterBadge);
      }

      grid.appendChild(card);
    });

    section.appendChild(grid);
    wrapper.appendChild(section);
  });

  modelsView.appendChild(wrapper);
  lucide.createIcons({ nodes: [wrapper] });
}

// ---- Settings view (full rove.yaml pretty render) ----
function makeSectionHeader(iconName, text) {
  var h = document.createElement("h3");
  h.className = "text-sm font-semibold text-gray-300 mb-2 flex items-center gap-2";
  var icon = document.createElement("i");
  icon.setAttribute("data-lucide", iconName);
  icon.className = "w-4 h-4 text-gray-500";
  h.appendChild(icon);
  var span = document.createElement("span");
  span.textContent = text;
  h.appendChild(span);
  return h;
}

// ---- Examples view ----
var _examplesCache = null;

async function renderExamplesView() {
  examplesView.textContent = "";

  if (!_examplesCache) {
    try {
      var resp = await fetch(API_BASE + "/api/examples");
      _examplesCache = await resp.json();
    } catch (e) {
      var errP = document.createElement("p");
      errP.className = "text-gray-400 text-sm p-4";
      errP.textContent = "Failed to load examples.";
      examplesView.appendChild(errP);
      return;
    }
  }

  var examples = Array.isArray(_examplesCache) ? _examplesCache : (_examplesCache.examples || []);
  if (!examples.length) {
    var emptyP = document.createElement("p");
    emptyP.className = "text-gray-400 text-sm p-4";
    emptyP.textContent = "No examples available.";
    examplesView.appendChild(emptyP);
    return;
  }

  // Eval category metadata — primary grouping
  var evalCatMeta = {
    "scene_analysis":       { label: "Scene Analysis", icon: "eye",         color: "text-cyan-400",   badgeCls: "bg-cyan-500/10 text-cyan-300 border-cyan-500/20",    desc: "VLM-only evaluation — no robot or action execution required. Tests scene understanding, object detection, spatial reasoning, and plan quality using diverse real-world images from multiple robot platforms." },
    "atomic":               { label: "Atomic",       icon: "target",      color: "text-emerald-400", badgeCls: "bg-emerald-500/10 text-emerald-300 border-emerald-500/20", desc: "Single-instruction tasks that a VLA should accomplish in one motion sequence. Simple pick-place, push, turn, or open actions on the Panda robot. Good baseline for VLA confidence calibration." },
    "multi_stage":          { label: "Multi-Stage",  icon: "layers",      color: "text-blue-400",   badgeCls: "bg-blue-500/10 text-blue-300 border-blue-500/20",    desc: "Sequential tasks requiring ordered subtask decomposition. Tests whether the pipeline breaks complex instructions into correctly ordered steps." },
    "situated_correction":  { label: "Correction",   icon: "message-circle", color: "text-amber-400", badgeCls: "bg-amber-500/10 text-amber-300 border-amber-500/20", desc: "Mid-task human feedback that changes the plan. Tests whether the pipeline adapts to corrections like \"not that one\" or \"use the other hand.\"" },
    "constrained":          { label: "Constrained",  icon: "shield-alert", color: "text-red-400",    badgeCls: "bg-red-500/10 text-red-300 border-red-500/20",      desc: "Tasks with safety or preference constraints. Tests whether the pipeline acknowledges and respects rules like \"keep it flat\" or \"don't close the door.\"" },
    "open_ended":           { label: "Open-Ended",   icon: "sparkles",    color: "text-purple-400", badgeCls: "bg-purple-500/10 text-purple-300 border-purple-500/20", desc: "Ambiguous or semantic instructions. Tests whether the pipeline produces a reasonable interpretation of vague prompts like \"tidy up\" or \"get ready for dinner.\"" },
    "negative":             { label: "Negative",     icon: "filter",      color: "text-orange-400", badgeCls: "bg-orange-500/10 text-orange-300 border-orange-500/20", desc: "Tasks requiring exclusion filtering. Tests whether the pipeline correctly skips objects or actions when told \"except\", \"not\", or \"don't touch.\"" }
  };
  var evalCatOrder = ["atomic", "multi_stage", "situated_correction", "constrained", "open_ended", "negative", "scene_analysis"];

  // Group examples by eval_category, then by scene_type
  var catGroups = {};
  examples.forEach(function(ex) {
    var ec = ex.eval_category || "atomic";
    if (!catGroups[ec]) catGroups[ec] = {};
    var scene = ex.scene_type || "other";
    if (!catGroups[ec][scene]) catGroups[ec][scene] = [];
    catGroups[ec][scene].push(ex);
  });

  // Layout: left menu + right content
  var layout = document.createElement("div");
  layout.className = "flex gap-6 max-w-4xl mx-auto";

  // Left menu
  var menu = document.createElement("nav");
  menu.className = "shrink-0 w-52 pt-1";

  var menuTitle = document.createElement("h2");
  menuTitle.className = "text-lg font-semibold text-gray-100 mb-1";
  menuTitle.textContent = "Examples";
  menu.appendChild(menuTitle);

  var menuSubtitle = document.createElement("p");
  menuSubtitle.className = "text-[10px] text-gray-500 mb-4";
  menuSubtitle.textContent = "Select a category, then click an example to auto-fill evaluation";
  menu.appendChild(menuSubtitle);

  // Right content area
  var content = document.createElement("div");
  content.className = "flex-1 min-w-0";

  var panels = {};
  var menuButtons = {};
  var activeCatKeys = [];

  evalCatOrder.forEach(function(ec) {
    var groups = catGroups[ec];
    if (!groups) return;
    activeCatKeys.push(ec);
    var meta = evalCatMeta[ec];
    var totalCount = Object.values(groups).reduce(function(s, arr) { return s + arr.length; }, 0);

    // Menu button
    var btn = document.createElement("button");
    btn.className = "flex items-center gap-2.5 w-full text-left px-3 py-2.5 rounded-lg text-sm transition-colors mb-1 text-gray-400 hover:text-gray-200 hover:bg-f-surface/50";
    var btnIcon = document.createElement("i");
    btnIcon.setAttribute("data-lucide", meta.icon);
    btnIcon.className = "w-4 h-4 shrink-0 " + meta.color;
    btn.appendChild(btnIcon);
    var btnLabel = document.createElement("span");
    btnLabel.className = "flex-1 truncate";
    btnLabel.textContent = meta.label;
    btn.appendChild(btnLabel);
    var btnCount = document.createElement("span");
    btnCount.className = "text-[10px] text-gray-500";
    btnCount.textContent = String(totalCount);
    btn.appendChild(btnCount);
    menuButtons[ec] = btn;
    menu.appendChild(btn);

    // Content panel (hidden by default)
    var panel = document.createElement("div");
    panel.style.display = "none";

    // Panel header with description
    var panelHeader = document.createElement("div");
    panelHeader.className = "mb-5";
    var panelTitleRow = document.createElement("div");
    panelTitleRow.className = "flex items-center gap-2 mb-1.5";
    var panelIcon = document.createElement("i");
    panelIcon.setAttribute("data-lucide", meta.icon);
    panelIcon.className = "w-5 h-5 " + meta.color;
    panelTitleRow.appendChild(panelIcon);
    var panelTitle = document.createElement("h3");
    panelTitle.className = "text-sm font-semibold text-gray-200";
    panelTitle.textContent = meta.label;
    panelTitleRow.appendChild(panelTitle);
    var panelCount = document.createElement("span");
    panelCount.className = "text-[10px] text-gray-500 ml-auto";
    panelCount.textContent = totalCount + " example" + (totalCount !== 1 ? "s" : "");
    panelTitleRow.appendChild(panelCount);
    panelHeader.appendChild(panelTitleRow);
    var panelDesc = document.createElement("p");
    panelDesc.className = "text-xs text-gray-400 leading-relaxed";
    panelDesc.textContent = meta.desc;
    panelHeader.appendChild(panelDesc);
    panel.appendChild(panelHeader);

    // Scene type groups within this eval category
    Object.keys(groups).sort().forEach(function(sceneType) {
      var section = document.createElement("div");
      section.className = "mb-4";

      var header = document.createElement("button");
      header.className = "flex items-center gap-2 w-full text-left text-xs font-semibold text-gray-400 mb-2 hover:text-white transition-colors";

      var chevronIcon = document.createElement("i");
      chevronIcon.setAttribute("data-lucide", "chevron-down");
      chevronIcon.className = "w-3.5 h-3.5 transition-transform";
      header.appendChild(chevronIcon);

      var label = document.createElement("span");
      label.textContent = sceneType.replace(/-/g, " ").replace(/\b\w/g, function(c) { return c.toUpperCase(); });
      header.appendChild(label);

      var countSpan = document.createElement("span");
      countSpan.className = "text-[10px] text-gray-600 font-normal ml-1";
      countSpan.textContent = "(" + groups[sceneType].length + ")";
      header.appendChild(countSpan);

      var grid = document.createElement("div");
      grid.className = "grid grid-cols-1 sm:grid-cols-2 gap-2 ml-5";

      header.addEventListener("click", function() {
        var isHidden = grid.style.display === "none";
        grid.style.display = isHidden ? "" : "none";
        chevronIcon.style.transform = isHidden ? "" : "rotate(-90deg)";
      });

      groups[sceneType].forEach(function(ex) {
        var card = document.createElement("button");
        card.className = "flex items-center gap-3 p-2 rounded-lg border border-f-border bg-f-surface hover:border-f-purple hover:bg-f-surface/80 transition-colors text-left group";

        var thumb = document.createElement("img");
        thumb.src = API_BASE + "/data/" + ex.filename;
        thumb.alt = ex.task;
        thumb.className = "w-14 h-14 rounded-md object-cover border border-f-border shrink-0";
        thumb.loading = "lazy";

        var info = document.createElement("div");
        info.className = "flex-1 min-w-0";

        var taskText = document.createElement("p");
        taskText.className = "text-xs text-gray-200 group-hover:text-white line-clamp-2";
        taskText.textContent = ex.task;

        // Meta row: source + pipeline scope badge
        var metaRow = document.createElement("div");
        metaRow.className = "flex items-center gap-1.5 mt-0.5";

        // Pipeline scope badge (VLA vs perceive-plan)
        if (ex.category === "action") {
          var vlaBadge = document.createElement("span");
          vlaBadge.className = "text-[9px] px-1.5 py-0.5 rounded border bg-indigo-500/10 text-indigo-300 border-indigo-500/20";
          vlaBadge.textContent = "VLA";
          metaRow.appendChild(vlaBadge);
        }

        var metaParts = [ex.source ? ex.source.dataset : ""];
        if (ex.robot) metaParts.push(ex.robot.toUpperCase());
        if (ex.proprioception) metaParts.push(ex.state_dim + "-DOF state");
        if (ex.difficulty) metaParts.push(ex.difficulty);
        var metaSpan = document.createElement("span");
        metaSpan.className = "text-[10px] text-gray-500";
        metaSpan.textContent = metaParts.filter(Boolean).join(" \u00b7 ");
        metaRow.appendChild(metaSpan);

        info.appendChild(taskText);
        info.appendChild(metaRow);
        card.appendChild(thumb);
        card.appendChild(info);

        card.addEventListener("click", function() {
          loadExample(ex);
        });

        grid.appendChild(card);
      });

      section.appendChild(header);
      section.appendChild(grid);
      panel.appendChild(section);
    });

    panels[ec] = panel;
    content.appendChild(panel);
  });

  // Activate a category — show its panel, highlight its menu button
  function activateCategory(cat) {
    activeCatKeys.forEach(function(k) {
      if (panels[k]) panels[k].style.display = k === cat ? "" : "none";
      if (menuButtons[k]) {
        menuButtons[k].className = "flex items-center gap-2.5 w-full text-left px-3 py-2.5 rounded-lg text-sm transition-colors mb-1 "
          + (k === cat
            ? "bg-f-surface border border-f-purple/50 text-white"
            : "text-gray-400 hover:text-gray-200 hover:bg-f-surface/50");
      }
    });
  }

  activeCatKeys.forEach(function(cat) {
    if (menuButtons[cat]) {
      menuButtons[cat].addEventListener("click", function() {
        activateCategory(cat);
      });
    }
  });

  // Default to first available category
  if (activeCatKeys.length > 0) activateCategory(activeCatKeys[0]);

  layout.appendChild(menu);
  layout.appendChild(content);
  examplesView.appendChild(layout);
  if (typeof lucide !== "undefined") lucide.createIcons();
}

async function loadExample(ex) {
  try {
    var resp = await fetch(API_BASE + "/data/" + ex.filename);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    var blob = await resp.blob();
    var name = ex.filename.split("/").pop();
    selectedFile = new File([blob], name, { type: blob.type });
    window._selectedExampleFilename = ex.filename;

    previewImg.src = URL.createObjectURL(blob);
    imagePreview.classList.remove("hidden");

    taskInput.value = ex.task;
    taskInput.dispatchEvent(new Event("input"));

    // Store proprioception for action examples
    window._selectedProprioception = ex.proprioception || null;
    window._selectedGroundTruth = ex.ground_truth_action || null;
    window._selectedCategory = ex.category || "perceive-plan";
    window._selectedEvalCategory = ex.eval_category || null;
    window._selectedExpectedSubtasks = ex.expected_subtasks || null;
    window._selectedCorrection = ex.correction || null;
    window._selectedConstraints = ex.constraints || null;

    switchView("home");
  } catch (e) {
    console.error("Failed to load example:", e);
    alert("Failed to load example image: " + e.message);
  }
}

function renderSettingsView() {
  settingsView.textContent = "";
  var wrapper = document.createElement("div");
  wrapper.className = "max-w-3xl mx-auto";

  var heading = document.createElement("h2");
  heading.className = "text-lg font-semibold mb-1";
  heading.textContent = "Configuration";
  wrapper.appendChild(heading);

  var subtitle = document.createElement("p");
  subtitle.className = "text-[13px] text-gray-500 mb-4";
  subtitle.textContent = "Full rove.yaml rendered from /api/config";
  wrapper.appendChild(subtitle);

  if (!configData) {
    var loading = document.createElement("p");
    loading.className = "text-sm text-gray-500";
    loading.textContent = "Loading config...";
    wrapper.appendChild(loading);
    settingsView.appendChild(wrapper);
    return;
  }

  // --- Defaults section ---
  if (configData.defaults) {
    var defSection = document.createElement("div");
    defSection.className = "mb-6";
    defSection.appendChild(makeSectionHeader("sliders", "Defaults"));
    var defCard = document.createElement("div");
    defCard.className = "bg-f-surface border border-f-border rounded-lg p-3 space-y-1";
    Object.entries(configData.defaults).forEach(function(entry) {
      var row = document.createElement("div");
      row.className = "flex items-center gap-2";
      var lbl = document.createElement("span");
      lbl.className = "text-xs text-gray-400 w-36 shrink-0 font-mono";
      lbl.textContent = entry[0];
      row.appendChild(lbl);
      var val = document.createElement("span");
      val.className = "text-xs text-gray-200";
      val.textContent = typeof entry[1] === "object" ? JSON.stringify(entry[1]) : String(entry[1]);
      row.appendChild(val);
      defCard.appendChild(row);
    });
    defSection.appendChild(defCard);
    wrapper.appendChild(defSection);
  }

  // --- Endpoints section (compact rows grouped by type) ---
  if (configData.endpoints) {
    var modSection = document.createElement("div");
    modSection.className = "mb-6";
    modSection.appendChild(makeSectionHeader("cpu", "Endpoints"));

    // Regroup flat endpoints by type for settings display
    var settingsGrouped = {};
    Object.entries(configData.endpoints).forEach(function(e) {
      var eid = e[0], ep = e[1], t = ep.type || "unknown";
      if (!settingsGrouped[t]) settingsGrouped[t] = {};
      settingsGrouped[t][eid] = ep;
    });

    Object.entries(settingsGrouped).forEach(function(typeEntry) {
      var type = typeEntry[0];
      var group = typeEntry[1];
      if (!group || typeof group !== "object") return;
      var entries = Object.entries(group);
      if (entries.length === 0) return;

      var typeLabel = document.createElement("p");
      typeLabel.className = "text-[11px] text-gray-500 font-semibold mt-3 mb-1 uppercase tracking-wide";
      typeLabel.textContent = type + " (" + entries.length + ")";
      modSection.appendChild(typeLabel);

      entries.forEach(function(mEntry) {
        var mid = mEntry[0];
        var m = mEntry[1];
        var row = document.createElement("div");
        row.className = "bg-f-surface border border-f-border rounded-md px-3 py-1.5 flex items-center gap-3 mb-1";
        var idSpan = document.createElement("span");
        idSpan.className = "text-xs font-mono text-gray-200 w-44 shrink-0 truncate";
        idSpan.textContent = mid;
        idSpan.title = mid;
        row.appendChild(idSpan);
        var nameSpan = document.createElement("span");
        nameSpan.className = "text-xs text-gray-400 flex-1 truncate";
        nameSpan.textContent = m.display_name || "";
        row.appendChild(nameSpan);
        if (m.adapter) {
          var adBadge = document.createElement("span");
          adBadge.className = "text-[10px] bg-f-elevated text-gray-400 px-1.5 py-0.5 rounded shrink-0";
          adBadge.textContent = m.adapter;
          row.appendChild(adBadge);
        }
        var dot = document.createElement("span");
        var isAvail = m.adapter && m.adapter.startsWith("mock");
        dot.className = "inline-block w-2 h-2 rounded-full shrink-0 " + (isAvail ? "bg-green-500" : "bg-gray-600");
        row.appendChild(dot);
        modSection.appendChild(row);
      });
    });
    wrapper.appendChild(modSection);
  }

  // --- Strategies section (compact cards) ---
  if (configData.strategies) {
    var stratSection = document.createElement("div");
    stratSection.className = "mb-6";
    stratSection.appendChild(makeSectionHeader("layers", "Strategies"));

    Object.entries(configData.strategies).forEach(function(sEntry) {
      var sid = sEntry[0];
      var s = sEntry[1];
      var row = document.createElement("div");
      row.className = "bg-f-surface border border-f-border rounded-md px-3 py-2 mb-1";
      var top = document.createElement("div");
      top.className = "flex items-center gap-2 mb-1";
      var nameSpan = document.createElement("span");
      nameSpan.className = "text-xs font-semibold text-gray-200";
      nameSpan.textContent = s.display_name || sid;
      top.appendChild(nameSpan);
      if (s.description) {
        var descSpan = document.createElement("span");
        descSpan.className = "text-[10px] text-gray-500 truncate flex-1";
        descSpan.textContent = "\u2014 " + s.description;
        top.appendChild(descSpan);
      }
      row.appendChild(top);

      var badges = document.createElement("div");
      badges.className = "flex flex-wrap gap-1";
      ["perceive", "plan", "act", "verify", "sim"].forEach(function(stage) {
        if (!s[stage]) return;
        var colors = STAGE_COLORS[stage] || { bg: "bg-blue-500/15", text: "text-blue-300" };
        var badge = document.createElement("span");
        badge.className = "text-[10px] " + colors.bg + " " + colors.text + " px-1.5 py-0.5 rounded";
        badge.textContent = stage.charAt(0).toUpperCase() + ": " + s[stage];
        badges.appendChild(badge);
      });
      row.appendChild(badges);
      stratSection.appendChild(row);
    });
    wrapper.appendChild(stratSection);
  }

  // --- Connection info ---
  var connSection = document.createElement("div");
  connSection.className = "mb-6";
  connSection.appendChild(makeSectionHeader("wifi", "Session"));
  var connCard = document.createElement("div");
  connCard.className = "bg-f-surface border border-f-border rounded-lg p-3 space-y-1";
  [
    ["API endpoint", API_BASE],
    ["Connection", connLabel.textContent],
    ["Evaluations", runHistory.length + " this session"],
  ].forEach(function(pair) {
    var row = document.createElement("div");
    row.className = "flex items-center gap-2";
    var lbl = document.createElement("span");
    lbl.className = "text-xs text-gray-400 w-36 shrink-0";
    lbl.textContent = pair[0];
    row.appendChild(lbl);
    var val = document.createElement("span");
    val.className = "text-xs text-gray-200";
    val.textContent = pair[1];
    row.appendChild(val);
    connCard.appendChild(row);
  });
  connSection.appendChild(connCard);
  wrapper.appendChild(connSection);

  settingsView.appendChild(wrapper);
  lucide.createIcons({ nodes: [wrapper] });
}

// ============================================================
// ---- Run history (fixed: per-evaluation data isolation) ----
// ============================================================

var HISTORY_MAX = 50;
var HISTORY_KEY = "rove-history";

function isMockEval(strategyIds) {
  // An evaluation is mock-only if every strategy ID contains "mock"
  return strategyIds.length > 0 && strategyIds.every(function(sid) {
    return sid.toLowerCase().indexOf("mock") !== -1;
  });
}

function addToHistory(evalId, task, strategyIds) {
  var entry = {
    id: evalId,
    task: task,
    strategyIds: strategyIds,
    timestamp: new Date().toISOString(),
    status: "running",
    mock: isMockEval(strategyIds), // flag for filtering
    results: {},        // per-strategy result data
    summaryResults: {}, // snapshot of summaryResults for this eval
  };
  // Initialize results structure per strategy
  strategyIds.forEach(function(sid) {
    entry.results[sid] = { stages: [], success: null, totalLatencyMs: null };
  });
  runHistory.push(entry);
  activeHistoryIndex = runHistory.length - 1;
  renderHistory();
}

function updateHistoryStatus(evalId, status) {
  var entry = runHistory.find(function(e) { return e.id === evalId; });
  if (entry) {
    entry.status = status;
    if (status === "completed") {
      // Snapshot summaryResults into the entry
      entry.summaryResults = JSON.parse(JSON.stringify(summaryResults));
    }
    saveHistory();
    renderHistory();
  }
}

function getFilteredHistory() {
  return runHistory.filter(function(entry) {
    // Hide mock-only evaluations from history sidebar
    if (entry.mock) return false;
    if (historyFilterText) {
      var q = historyFilterText.toLowerCase();
      if (entry.task.toLowerCase().indexOf(q) === -1) return false;
    }
    if (historyFilterStrategy) {
      if (entry.strategyIds.indexOf(historyFilterStrategy) === -1) return false;
    }
    return true;
  });
}

function renderHistory() {
  historyList.textContent = "";

  // Filter bar
  var filterBar = document.createElement("div");
  filterBar.className = "px-2 pb-2 space-y-1.5";

  // Search input
  var searchWrap = document.createElement("div");
  searchWrap.className = "relative";
  var searchIcon = document.createElement("span");
  searchIcon.className = "absolute left-2 top-1/2 -translate-y-1/2 text-[10px] text-gray-500";
  searchIcon.textContent = "\uD83D\uDD0D";
  searchWrap.appendChild(searchIcon);
  var searchInput = document.createElement("input");
  searchInput.type = "text";
  searchInput.placeholder = "Search...";
  searchInput.className = "history-filter-input";
  searchInput.value = historyFilterText;
  searchInput.addEventListener("input", function() {
    historyFilterText = searchInput.value;
    renderHistoryItems();
  });
  searchWrap.appendChild(searchInput);
  filterBar.appendChild(searchWrap);

  // Strategy dropdown
  var stratSelect = document.createElement("select");
  stratSelect.className = "history-filter-select";
  var allOpt = document.createElement("option");
  allOpt.value = "";
  allOpt.textContent = "All strategies";
  stratSelect.appendChild(allOpt);
  // Collect unique strategy IDs from history
  var uniqueStrategies = new Set();
  runHistory.forEach(function(e) { e.strategyIds.forEach(function(s) { uniqueStrategies.add(s); }); });
  uniqueStrategies.forEach(function(sid) {
    var strat = strategies.find(function(s) { return s.id === sid; });
    var opt = document.createElement("option");
    opt.value = sid;
    opt.textContent = strat ? strat.display_name : sid;
    if (sid === historyFilterStrategy) opt.selected = true;
    stratSelect.appendChild(opt);
  });
  stratSelect.addEventListener("change", function() {
    historyFilterStrategy = stratSelect.value;
    renderHistoryItems();
  });
  filterBar.appendChild(stratSelect);
  historyList.appendChild(filterBar);

  // Items container
  var itemsContainer = document.createElement("div");
  itemsContainer.id = "historyItems";
  itemsContainer.className = "flex flex-col gap-0.5";
  historyList.appendChild(itemsContainer);

  renderHistoryItems();
}

function renderHistoryItems() {
  var container = document.getElementById("historyItems");
  if (!container) return;
  container.textContent = "";

  var filtered = getFilteredHistory();

  if (filtered.length === 0) {
    var empty = document.createElement("div");
    empty.className = "px-3 py-2 text-[11px] text-gray-600 italic";
    empty.textContent = runHistory.length === 0 ? "No evaluations yet" : "No matches";
    container.appendChild(empty);
    return;
  }

  filtered.slice().reverse().forEach(function(entry) {
    var idx = runHistory.indexOf(entry);
    var item = document.createElement("div");
    item.className = "history-item sidebar-item flex flex-col gap-1 px-3 py-2 rounded-md cursor-pointer text-[12px]";
    item.setAttribute("role", "button");
    item.setAttribute("tabindex", "0");
    if (idx === activeHistoryIndex) {
      item.classList.add("active", "bg-f-surface");
    }

    // Top row: dot + task + time + delete
    var topRow = document.createElement("div");
    topRow.className = "flex items-center gap-2";

    // Status dot
    var dot = document.createElement("span");
    var dotColor = entry.status === "running" ? "bg-purple-500 pulse-purple" :
                   entry.status === "completed" ? "bg-green-500" : "bg-red-500";
    dot.className = "w-2 h-2 rounded-full shrink-0 " + dotColor;
    dot.setAttribute("aria-hidden", "true");
    topRow.appendChild(dot);

    // Task text (truncated)
    var textSpan = document.createElement("span");
    textSpan.className = "text-gray-300 truncate flex-1";
    textSpan.textContent = entry.task.length > 25 ? entry.task.substring(0, 25) + "..." : entry.task;
    textSpan.title = entry.task;
    topRow.appendChild(textSpan);

    // Time
    var timeSpan = document.createElement("span");
    timeSpan.className = "text-[10px] text-gray-600 shrink-0";
    var ts = typeof entry.timestamp === "string" ? new Date(entry.timestamp) : entry.timestamp;
    var h = ts.getHours().toString().padStart(2, "0");
    var m = ts.getMinutes().toString().padStart(2, "0");
    timeSpan.textContent = h + ":" + m;
    topRow.appendChild(timeSpan);

    // Delete button (visible on hover)
    var delBtn = document.createElement("button");
    delBtn.className = "history-delete";
    delBtn.textContent = "\u00d7";
    delBtn.title = "Remove from history";
    delBtn.addEventListener("click", function(e) {
      e.stopPropagation();
      deleteHistoryEntry(idx);
    });
    topRow.appendChild(delBtn);

    item.appendChild(topRow);

    // Strategy tags row
    var tagsRow = document.createElement("div");
    tagsRow.className = "flex flex-wrap gap-1 ml-4";
    entry.strategyIds.forEach(function(sid) {
      var strat = strategies.find(function(s) { return s.id === sid; });
      var displayName = strat ? strat.display_name : sid;
      var tag = document.createElement("span");
      tag.className = "text-[9px] px-1.5 py-0.5 rounded bg-f-elevated text-gray-500 truncate max-w-[80px]";
      tag.textContent = displayName;
      tag.title = displayName;
      tagsRow.appendChild(tag);
    });
    item.appendChild(tagsRow);

    item.addEventListener("click", function() {
      showHistoryEntry(idx);
    });
    item.addEventListener("keydown", function(e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); showHistoryEntry(idx); }
    });

    container.appendChild(item);
  });
}

function deleteHistoryEntry(idx) {
  var entry = runHistory[idx];
  if (!entry) return;

  // Remove from server
  fetch(API_BASE + "/api/history/" + entry.id, { method: "DELETE" }).catch(function() {});

  runHistory.splice(idx, 1);
  saveHistory();

  // If currently viewing this entry, go home
  if (activeHistoryIndex === idx) {
    activeHistoryIndex = -1;
    switchView("home");
  } else if (activeHistoryIndex > idx) {
    activeHistoryIndex--;
  }
  renderHistory();
}

function showHistoryEntry(idx) {
  activeHistoryIndex = idx;
  var entry = runHistory[idx];
  if (!entry) return;

  // If this is the currently running/active evaluation, just restore the view
  if (entry.status === "running" && Object.keys(tabData).length > 0) {
    restoreEvaluation();
    renderHistoryItems();
    return;
  }

  // Clear sidebar nav active state
  document.querySelectorAll(".sidebar-nav-link").forEach(function(l) { l.classList.remove("active"); });

  // Update history active state
  renderHistoryItems();

  // Show evaluation content
  currentView = "evaluation";
  welcomeMsg.classList.add("hidden");
  strategiesView.classList.add("hidden");
  modelsView.classList.add("hidden");
  examplesView.classList.add("hidden");
  settingsView.classList.add("hidden");

  // Clear existing tab content
  chatArea.querySelectorAll("[id^='tab-content-']").forEach(function(el) { el.remove(); });
  compareEl = null;

  var ids = entry.strategyIds;

  // Assign colors
  strategyColorMap = {};
  ids.forEach(function(sid, i) {
    strategyColorMap[sid] = STRATEGY_COLORS[i % STRATEGY_COLORS.length];
  });

  if (ids.length > 1) {
    tabBar.classList.remove("hidden");
    tabBar.textContent = "";

    // Summary tab
    var summaryTab = document.createElement("button");
    summaryTab.className = "strategy-tab active px-4 py-2.5 text-[13px] text-gray-400 whitespace-nowrap";
    summaryTab.setAttribute("data-tab-id", "__summary__");
    summaryTab.setAttribute("role", "tab");
    summaryTab.setAttribute("aria-selected", "true");
    summaryTab.setAttribute("aria-controls", "tab-content-__summary__");
    var sIcon = document.createElement("i");
    sIcon.setAttribute("data-lucide", "layout-grid");
    sIcon.className = "w-3.5 h-3.5 inline-block mr-1 align-middle";
    summaryTab.appendChild(sIcon);
    var sLabel = document.createElement("span");
    sLabel.textContent = "Summary";
    summaryTab.appendChild(sLabel);
    summaryTab.addEventListener("click", function() { switchTab("__summary__"); });
    tabBar.appendChild(summaryTab);

    // Build per-strategy tabs
    ids.forEach(function(sid, i) {
      var color = strategyColorMap[sid] || STRATEGY_COLORS[i % STRATEGY_COLORS.length];
      var tab = document.createElement("button");
      tab.className = "strategy-tab px-4 py-2.5 text-[13px] text-gray-400 whitespace-nowrap flex items-center";
      tab.setAttribute("data-tab-id", sid);
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-selected", "false");
      tab.setAttribute("aria-controls", "tab-content-" + sid);
      tab.style.setProperty("--tab-color", color);

      var strat = strategies.find(function(s) { return s.id === sid; });
      var displayName = strat ? strat.display_name : sid;

      var colorDot = document.createElement("span");
      colorDot.className = "tab-color-dot";
      colorDot.style.background = color;
      tab.appendChild(colorDot);

      var nameSpan = document.createElement("span");
      nameSpan.textContent = displayName;
      tab.appendChild(nameSpan);

      // Status badge from stored data
      var sr = entry.summaryResults && entry.summaryResults[sid];
      if (sr) {
        var statusBadge = document.createElement("span");
        statusBadge.className = "ml-2 text-[10px] px-1.5 py-0.5 rounded";
        if (sr.status === "completed") {
          statusBadge.className += " bg-green-500/15 text-green-400";
          statusBadge.textContent = "completed";
        } else if (sr.status === "error") {
          statusBadge.className += " bg-red-500/15 text-red-400";
          statusBadge.textContent = "error";
        } else {
          statusBadge.style.background = color + "20";
          statusBadge.style.color = color;
          statusBadge.textContent = "running";
        }
        tab.appendChild(statusBadge);
      }

      tab.addEventListener("click", function() { switchTab(sid); });
      tabBar.appendChild(tab);
    });

    // Build summary from stored data
    summaryResults = entry.summaryResults || {};
    summaryEl = document.createElement("div");
    summaryEl.className = "space-y-3";
    summaryEl.id = "tab-content-__summary__";
    summaryEl.setAttribute("role", "tabpanel");
    summaryEl.setAttribute("tabindex", "0");
    updateSummaryTable(ids);

    // Provenance card (collapsible)
    if (entry.provenance) {
      renderProvenanceCard(entry.provenance, summaryEl);
    }

    // Insights card
    if (entry.insights) {
      renderInsightsCard(entry.insights, summaryEl);
    }

    // Build per-strategy tab content from stored results
    tabData = {};
    ids.forEach(function(sid) {
      var container = createTabContainer(sid);
      tabData[sid] = { el: container, typingEl: null, stages: {}, status: "completed" };

      var stratResults = entry.results && entry.results[sid];
      if (stratResults && stratResults.stages) {
        // Detect parallel mode from phase labels on stages
        var hasPhases = stratResults.stages.some(function(stg) { return stg.phase; });
        stratResults.stages.forEach(function(stg, stageIndex) {
          var cardTarget = container;
          if (hasPhases && stg.phase) {
            addPhaseHeader(stg.phase, stg.stage, container, sid);
            cardTarget = getPhaseContent(stg.phase, container, sid) || container;
          }
          addStageCard(stg.stage, stg.status, stg.latencyMs, stg.output, stg.error, stg.modelId, cardTarget, stageIndex, sid);
        });
      }
    });

    // Show summary tab
    activeTabId = "__summary__";
    chatArea.appendChild(summaryEl);
    lucide.createIcons({ nodes: [tabBar] });
  } else {
    tabBar.classList.add("hidden");
    var showSid = ids[0];

    tabData = {};
    var container = createTabContainer(showSid);
    tabData[showSid] = { el: container, typingEl: null, stages: {}, status: "completed" };

    var stratResults = entry.results && entry.results[showSid];
    if (stratResults && stratResults.stages) {
      var hasPhases = stratResults.stages.some(function(stg) { return stg.phase; });
      stratResults.stages.forEach(function(stg, stageIndex) {
        var cardTarget = container;
        if (hasPhases && stg.phase) {
          addPhaseHeader(stg.phase, stg.stage, container, showSid);
          cardTarget = getPhaseContent(stg.phase, container, showSid) || container;
        }
        addStageCard(stg.stage, stg.status, stg.latencyMs, stg.output, stg.error, stg.modelId, cardTarget, stageIndex, showSid);
      });
    }

    chatArea.appendChild(container);
    activeTabId = showSid;
  }
}

// ---- localStorage persistence ----

function saveHistory() {
  try {
    // Trim to max entries
    while (runHistory.length > HISTORY_MAX) {
      runHistory.shift();
      if (activeHistoryIndex > 0) activeHistoryIndex--;
    }
    // Filter out mock-only evaluations — don't persist test runs
    var nonMock = runHistory.filter(function(e) { return !e.mock; });
    // Truncate raw_response fields to save space
    var toSave = JSON.parse(JSON.stringify(nonMock));
    toSave.forEach(function(entry) {
      if (entry.results) {
        Object.values(entry.results).forEach(function(r) {
          if (r.stages) {
            r.stages.forEach(function(stg) {
              if (stg.output && stg.output.raw_response && typeof stg.output.raw_response === "string" && stg.output.raw_response.length > 500) {
                stg.output.raw_response = stg.output.raw_response.substring(0, 500) + "...";
              }
            });
          }
        });
      }
    });
    localStorage.setItem(HISTORY_KEY, JSON.stringify(toSave));
  } catch (e) {
    console.warn("Failed to save history to localStorage:", e);
  }
}

function restoreHistory() {
  // Restore from localStorage
  try {
    var stored = localStorage.getItem(HISTORY_KEY);
    if (stored) {
      var parsed = JSON.parse(stored);
      if (Array.isArray(parsed)) {
        // Filter out any legacy mock entries and tag existing ones
        runHistory = parsed.filter(function(e) {
          if (e.mock) return false;
          if (isMockEval(e.strategyIds || [])) return false;
          return true;
        });
      }
    }
  } catch (e) {
    console.warn("Failed to restore history from localStorage:", e);
  }

  // Merge from server
  fetch(API_BASE + "/api/history")
    .then(function(res) { return res.ok ? res.json() : []; })
    .then(function(serverHistory) {
      if (!Array.isArray(serverHistory) || serverHistory.length === 0) return;
      var existingIds = new Set(runHistory.map(function(e) { return e.id; }));
      var added = false;
      serverHistory.forEach(function(se) {
        if (!existingIds.has(se.eval_id)) {
          // Convert server format to client format
          var entry = {
            id: se.eval_id,
            task: se.task,
            strategyIds: se.strategy_ids || [],
            timestamp: se.timestamp,
            status: se.status || "completed",
            results: {},
            summaryResults: {},
          };
          // Convert server results to client format
          if (se.results) {
            // Handle both list and dict formats
            var resultItems = Array.isArray(se.results)
              ? se.results
              : Object.values(se.results);
            resultItems.forEach(function(sr) {
              var sid = sr.strategy_id || sr.display_name || "default";
              entry.results[sid] = {
                stages: (sr.stages || []).map(function(stg) {
                  return {
                    stage: stg.stage,
                    status: stg.status,
                    latencyMs: stg.latency_ms,
                    output: stg.output,
                    error: stg.error,
                    modelId: stg.model_id,
                  };
                }),
                success: sr.verdict_valid === false ? null : sr.success,
                totalLatencyMs: sr.total_latency_ms,
                failureStage: sr.failure_stage || null,
                failureCategory: sr.failure_category || null,
              };
              entry.summaryResults[sid] = {
                status: sr.success != null ? "completed" : "error",
                success: sr.verdict_valid === false ? null : sr.success,
                latency_ms: sr.total_latency_ms,
                currentStage: null,
                stageStatuses: {},
                failureCategory: sr.failure_category || null,
              };
              // Ensure strategy_ids includes this sid
              if (entry.strategyIds.indexOf(sid) === -1) {
                entry.strategyIds.push(sid);
              }
            });
          }
          // Pass through provenance and insights
          if (se.provenance) entry.provenance = se.provenance;
          if (se.insights) entry.insights = se.insights;
          runHistory.push(entry);
          added = true;
        }
      });
      if (added) {
        saveHistory();
        renderHistory();
      }
    })
    .catch(function() {});

  renderHistory();
}

// ---- Config panel toggle ----
var _configCollapsed = true;
function initConfigPanel() {
  _configCollapsed = true;
  configChevron.style.transform = "rotate(180deg)";
  configToggle.addEventListener("click", function() {
    setConfigCollapsed(!_configCollapsed);
  });
}

function setConfigCollapsed(collapsed) {
  _configCollapsed = collapsed;
  configContent.style.display = collapsed ? "none" : "";
  configChevron.style.transform = collapsed ? "rotate(180deg)" : "";
  configToggle.setAttribute("aria-expanded", String(!collapsed));
  // Also toggle image and URDF previews
  if (collapsed) {
    imagePreview.classList.add("hidden");
    urdfPreview.classList.add("hidden");
  } else {
    // Only show if files are actually selected
    if (selectedFile) imagePreview.classList.remove("hidden");
    if (selectedUrdfFile) urdfPreview.classList.remove("hidden");
  }
}

// ---- Live announcer helper (M3) ----
var liveAnnouncer = document.getElementById("liveAnnouncer");
function announce(message) {
  if (liveAnnouncer) {
    liveAnnouncer.textContent = "";
    requestAnimationFrame(function() { liveAnnouncer.textContent = message; });
  }
}

// ---- Load strategies from API ----
async function loadStrategies() {
  try {
    var res = await fetch(API_BASE + "/api/strategies");
    if (!res.ok) throw new Error("HTTP " + res.status);
    var data = await res.json();
    strategies = data.strategies || [];
    setConnection(true);
    renderStrategyCards();
    updateSelectedCount();
  } catch (e) {
    console.error("Failed to load strategies:", e);
    setConnection(false);
    strategyGrid.textContent = "";
    var msg = document.createElement("div");
    msg.className = "text-xs text-red-400 col-span-4";
    msg.textContent = "Failed to load strategies";
    strategyGrid.appendChild(msg);
  }
}

function renderStrategyCards() {
  strategyGrid.textContent = "";
  strategies.forEach(function(s) {
    var chip = document.createElement("div");
    chip.className = "strategy-chip bg-f-surface border border-f-border rounded-full px-3 py-1.5 cursor-pointer flex items-center gap-1.5";
    chip.setAttribute("data-strategy-id", s.id);
    chip.setAttribute("role", "checkbox");
    chip.setAttribute("tabindex", "0");
    chip.setAttribute("aria-checked", "false");
    chip.setAttribute("title", s.description || s.display_name);

    // Checkmark (hidden when not selected)
    var checkWrap = document.createElement("span");
    checkWrap.className = "check-icon w-3.5 h-3.5 rounded-full bg-f-purple items-center justify-center shrink-0";
    var checkSvg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    checkSvg.setAttribute("width", "8");
    checkSvg.setAttribute("height", "8");
    checkSvg.setAttribute("viewBox", "0 0 24 24");
    checkSvg.setAttribute("fill", "none");
    checkSvg.setAttribute("stroke", "white");
    checkSvg.setAttribute("stroke-width", "3");
    checkSvg.setAttribute("stroke-linecap", "round");
    checkSvg.setAttribute("stroke-linejoin", "round");
    var checkPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
    checkPath.setAttribute("d", "M20 6L9 17l-5-5");
    checkSvg.appendChild(checkPath);
    checkWrap.appendChild(checkSvg);
    chip.appendChild(checkWrap);

    var nameSpan = document.createElement("span");
    nameSpan.className = "text-[11px] font-medium text-gray-200 whitespace-nowrap";
    nameSpan.textContent = s.display_name;
    chip.appendChild(nameSpan);

    // Tag count (subtle)
    if (s.tags && s.tags.length) {
      var tagDot = document.createElement("span");
      tagDot.className = "w-1.5 h-1.5 rounded-full bg-gray-600 shrink-0";
      chip.appendChild(tagDot);
      var tagSpan = document.createElement("span");
      tagSpan.className = "text-[10px] text-gray-500";
      tagSpan.textContent = s.tags[0];
      chip.appendChild(tagSpan);
    }

    // Hover popover with stage->model detail
    var popover = document.createElement("div");
    popover.className = "chip-popover";

    var popName = document.createElement("div");
    popName.className = "text-[11px] font-semibold text-gray-200 mb-1";
    popName.textContent = s.display_name;
    popover.appendChild(popName);

    if (s.description) {
      var popDesc = document.createElement("div");
      popDesc.className = "text-[10px] text-gray-500 mb-2";
      popDesc.textContent = s.description;
      popover.appendChild(popDesc);
    }

    var stageList = document.createElement("div");
    stageList.className = "space-y-1";
    ["perceive", "plan", "act", "verify"].forEach(function(stage) {
      if (!s[stage]) return;
      var colors = STAGE_COLORS[stage];
      var row = document.createElement("div");
      row.className = "flex items-center gap-2";
      var dot = document.createElement("span");
      dot.className = "w-1.5 h-1.5 rounded-full shrink-0";
      if (stage === "perceive" || stage === "plan") dot.style.background = "#a855f7";
      else if (stage === "act") dot.style.background = "#10b981";
      else dot.style.background = "#f59e0b";
      row.appendChild(dot);
      var label = document.createElement("span");
      label.className = "text-[10px] uppercase tracking-wider w-[38px] shrink-0 " + colors.text;
      label.textContent = stage.slice(0, 4);
      row.appendChild(label);
      var model = document.createElement("span");
      model.className = "text-[10px] text-gray-300 font-mono truncate";
      model.textContent = s[stage];
      row.appendChild(model);
      stageList.appendChild(row);
    });
    if (s.sim) {
      var simRow = document.createElement("div");
      simRow.className = "flex items-center gap-2";
      var simDot = document.createElement("span");
      simDot.className = "w-1.5 h-1.5 rounded-full shrink-0";
      simDot.style.background = "#3b82f6";
      simRow.appendChild(simDot);
      var simLabel = document.createElement("span");
      simLabel.className = "text-[10px] uppercase tracking-wider w-[38px] shrink-0 text-blue-300";
      simLabel.textContent = "sim";
      simRow.appendChild(simLabel);
      var simModel = document.createElement("span");
      simModel.className = "text-[10px] text-gray-300 font-mono truncate";
      simModel.textContent = s.sim;
      simRow.appendChild(simModel);
      stageList.appendChild(simRow);
    }
    popover.appendChild(stageList);

    chip.appendChild(popover);

    // Click handler
    function toggleStrategy() {
      if (selectedStrategyIds.has(s.id)) {
        selectedStrategyIds.delete(s.id);
      } else {
        selectedStrategyIds.add(s.id);
      }
      updateStrategyCards();
      updateSelectedCount();
    }
    chip.addEventListener("click", toggleStrategy);
    chip.addEventListener("keydown", function(e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleStrategy(); }
    });

    strategyGrid.appendChild(chip);
  });
}

function updateStrategyCards() {
  document.querySelectorAll(".strategy-chip").forEach(function(chip) {
    var sid = chip.getAttribute("data-strategy-id");
    var isSelected = selectedStrategyIds.has(sid);
    if (isSelected) {
      chip.classList.add("selected");
    } else {
      chip.classList.remove("selected");
    }
    chip.setAttribute("aria-checked", String(isSelected));
  });
}

function updateSelectedCount() {
  selectedCount.textContent = "(" + selectedStrategyIds.size + " selected)";
}

function setConnection(ok) {
  connDot.className = "inline-block w-2 h-2 rounded-full " + (ok ? "bg-green-500" : "bg-red-500");
  connLabel.textContent = ok ? "Connected" : "Disconnected";
}

// ---- Image handling ----
imageInput.addEventListener("change", function(e) {
  var file = e.target.files[0];
  if (!file) return;
  selectedFile = file;
  window._selectedExampleFilename = null;
  var reader = new FileReader();
  reader.onload = function(ev) {
    previewImg.src = ev.target.result;
    previewImg.alt = "Preview of uploaded image";
    imagePreview.classList.remove("hidden");
  };
  reader.readAsDataURL(file);
});

removeImageBtn.addEventListener("click", function() {
  selectedFile = null;
  window._selectedExampleFilename = null;
  imageInput.value = "";
  imagePreview.classList.add("hidden");
});

// ---- Plus button popover ----
plusBtn.addEventListener("click", function(e) {
  e.stopPropagation();
  plusPopover.classList.toggle("hidden");
});

document.addEventListener("click", function(e) {
  if (!plusPopover.contains(e.target) && e.target !== plusBtn) {
    plusPopover.classList.add("hidden");
  }
});

// Hide popover when a file is selected
imageInput.addEventListener("click", function() { plusPopover.classList.add("hidden"); });

// ---- URDF handling ----
urdfInput.addEventListener("change", function(e) {
  var file = e.target.files[0];
  if (!file) return;
  selectedUrdfFile = file;
  urdfName.textContent = file.name;
  urdfPreview.classList.remove("hidden");
  plusPopover.classList.add("hidden");
});

removeUrdfBtn.addEventListener("click", function() {
  selectedUrdfFile = null;
  urdfInput.value = "";
  urdfPreview.classList.add("hidden");
});

// ---- Auto-resize textarea + keyboard shortcuts ----
function autoResizeTextarea() {
  taskInput.addEventListener("input", function() {
    taskInput.style.height = "auto";
    taskInput.style.height = Math.min(taskInput.scrollHeight, 120) + "px";
  });
  taskInput.addEventListener("keydown", function(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      evalBtn.click();
    }
    // Tab to fill sample question when input is empty
    if (e.key === "Tab" && taskInput.value.trim() === "") {
      e.preventDefault();
      taskInput.value = SAMPLE_QUESTIONS[sampleIndex % SAMPLE_QUESTIONS.length];
      sampleIndex++;
      taskInput.style.height = "auto";
      taskInput.style.height = Math.min(taskInput.scrollHeight, 120) + "px";
    }
  });
}

// ---- Evaluate ----
evalBtn.addEventListener("click", async function() {
  var task = taskInput.value.trim();
  if (isRunning) return;

  // Prompt user to select a strategy if none selected
  if (selectedStrategyIds.size === 0) {
    setConfigCollapsed(false);
    announce("Please select at least one strategy before evaluating");
    strategyGrid.style.outline = "2px solid #7c3aed";
    setTimeout(function() { strategyGrid.style.outline = ""; }, 1500);
    return;
  }

  if (!task || !selectedFile) return;

  setRunning(true);

  var ids = Array.from(selectedStrategyIds);

  // Switch to evaluation view
  currentView = "evaluation";
  welcomeMsg.classList.add("hidden");
  strategiesView.classList.add("hidden");
  modelsView.classList.add("hidden");
  examplesView.classList.add("hidden");
  settingsView.classList.add("hidden");
  document.querySelectorAll(".sidebar-nav-link").forEach(function(l) { l.classList.remove("active"); });

  setupTabs(ids);
  addUserCard(previewImg.src, task, ids);

  taskInput.value = "";
  taskInput.style.height = "38px";

  var form = new FormData();
  form.append("image", selectedFile);
  form.append("task", task);
  form.append("strategy_ids", ids.join(","));
  if (window._selectedExampleFilename) form.append("example_filename", window._selectedExampleFilename);
  if (selectedUrdfFile) {
    form.append("urdf", selectedUrdfFile);
  }

  try {
    var res = await fetch(API_BASE + "/api/evaluate", { method: "POST", body: form });
    if (!res.ok) throw new Error("HTTP " + res.status);
    var body = await res.json();

    // Track in history
    addToHistory(body.eval_id, task, ids);

    connectSSE(body.eval_id);
  } catch (e) {
    console.error("Evaluate failed:", e);
    addErrorToActiveTab("Failed to start evaluation: " + e.message);
    setRunning(false);
  }
});

// ---- Tab management ----
function setupTabs(strategyIds) {
  tabBar.textContent = "";
  tabData = {};
  activeTabId = null;
  summaryResults = {};
  summaryEl = null;
  compareEl = null;

  // Assign distinct colors to each strategy
  strategyColorMap = {};
  strategyIds.forEach(function(sid, i) {
    strategyColorMap[sid] = STRATEGY_COLORS[i % STRATEGY_COLORS.length];
  });

  // Initialize summary results for each strategy
  strategyIds.forEach(function(sid) {
    var strat = strategies.find(function(s) { return s.id === sid; });
    var stageStatuses = { verify: "pending" };
    ["perceive", "plan", "act", "dynamics"].forEach(function(stage) {
      if (stage === "dynamics") {
        stageStatuses[stage] = (strat && strat.compute_dynamics) ? "pending" : "skipped";
      } else {
        stageStatuses[stage] = (strat && strat[stage]) ? "pending" : "skipped";
      }
    });
    summaryResults[sid] = {
      status: "running",
      success: null,
      latency_ms: null,
      currentStage: null,
      stageStatuses: stageStatuses,
      pipelineMode: (strat && strat.pipeline_mode) || "sequential"
    };
  });

  // Clear chat area but keep persistent views
  chatArea.textContent = "";
  chatArea.appendChild(welcomeMsg);
  chatArea.appendChild(strategiesView);
  chatArea.appendChild(modelsView);
  chatArea.appendChild(examplesView);
  chatArea.appendChild(settingsView);

  if (strategyIds.length <= 1) {
    tabBar.classList.add("hidden");
    var sid = strategyIds[0];
    var container = createTabContainer(sid);
    chatArea.appendChild(container);
    var strat1 = strategies.find(function(s) { return s.id === sid; });
    tabData[sid] = { el: container, typingEl: null, stages: {}, status: "running", pipelineMode: (strat1 && strat1.pipeline_mode) || "sequential" };
    activeTabId = sid;
    return;
  }

  tabBar.classList.remove("hidden");

  // Create Summary tab (first)
  var summaryTab = document.createElement("button");
  summaryTab.className = "strategy-tab active px-4 py-2.5 text-[13px] text-gray-400 whitespace-nowrap";
  summaryTab.setAttribute("data-tab-id", "__summary__");
  summaryTab.setAttribute("role", "tab");
  summaryTab.setAttribute("aria-selected", "true");
  summaryTab.setAttribute("aria-controls", "tab-content-__summary__");
  var summaryIcon = document.createElement("i");
  summaryIcon.setAttribute("data-lucide", "layout-grid");
  summaryIcon.className = "w-3.5 h-3.5 inline-block mr-1 align-middle";
  summaryTab.appendChild(summaryIcon);
  var summaryLabel = document.createElement("span");
  summaryLabel.textContent = "Summary";
  summaryTab.appendChild(summaryLabel);
  summaryTab.addEventListener("click", function() { switchTab("__summary__"); });
  tabBar.appendChild(summaryTab);

  // Create summary container
  summaryEl = document.createElement("div");
  summaryEl.className = "space-y-3";
  summaryEl.id = "tab-content-__summary__";
  summaryEl.setAttribute("role", "tabpanel");
  summaryEl.setAttribute("tabindex", "0");

  // Strategy tabs
  strategyIds.forEach(function(sid) {
    var color = strategyColorMap[sid] || "#7c3aed";
    var tab = document.createElement("button");
    tab.className = "strategy-tab px-4 py-2.5 text-[13px] text-gray-400 whitespace-nowrap flex items-center";
    tab.setAttribute("data-tab-id", sid);
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", "false");
    tab.setAttribute("aria-controls", "tab-content-" + sid);
    tab.style.setProperty("--tab-color", color);

    var strat = strategies.find(function(s) { return s.id === sid; });
    var displayName = strat ? strat.display_name : sid;

    var colorDot = document.createElement("span");
    colorDot.className = "tab-color-dot";
    colorDot.style.background = color;
    tab.appendChild(colorDot);

    var nameSpan = document.createElement("span");
    nameSpan.textContent = displayName;
    tab.appendChild(nameSpan);

    var statusBadge = document.createElement("span");
    statusBadge.className = "ml-2 text-[10px] px-1.5 py-0.5 rounded";
    statusBadge.style.background = color + "20";
    statusBadge.style.color = color;
    statusBadge.textContent = "running";
    statusBadge.id = "tab-status-" + sid;
    tab.appendChild(statusBadge);

    var latencyBadge = document.createElement("span");
    latencyBadge.className = "ml-1 text-[10px] text-gray-500 hidden";
    latencyBadge.id = "tab-latency-" + sid;
    tab.appendChild(latencyBadge);

    tab.addEventListener("click", function() { switchTab(sid); });
    tabBar.appendChild(tab);

    var container = createTabContainer(sid);
    var strat2 = strategies.find(function(s) { return s.id === sid; });
    tabData[sid] = { el: container, typingEl: null, stages: {}, status: "running", pipelineMode: (strat2 && strat2.pipeline_mode) || "sequential" };
  });

  // Show summary tab by default
  activeTabId = "__summary__";
  chatArea.appendChild(summaryEl);
  updateSummaryTable(strategyIds);
  lucide.createIcons({ nodes: [tabBar] });
}

function createTabContainer(sid) {
  var div = document.createElement("div");
  div.className = "space-y-3";
  div.id = "tab-content-" + sid;
  div.setAttribute("role", "tabpanel");
  div.setAttribute("tabindex", "0");
  var color = strategyColorMap[sid];
  if (color) {
    div.style.borderTop = "2px solid " + color;
    div.style.paddingTop = "12px";
  }
  return div;
}

function switchTab(sid) {
  if (sid === activeTabId) return;

  document.querySelectorAll(".strategy-tab").forEach(function(t) {
    var isActive = t.getAttribute("data-tab-id") === sid;
    if (isActive) {
      t.classList.add("active");
      t.setAttribute("aria-selected", "true");
    } else {
      t.classList.remove("active");
      t.setAttribute("aria-selected", "false");
    }
  });

  // Hide all tab content panels
  chatArea.querySelectorAll("[id^='tab-content-']").forEach(function(el) { el.classList.add("hidden"); });

  // Show the target panel (append if not yet in DOM, unhide if already there)
  var targetEl = null;
  if (sid === "__summary__" && summaryEl) {
    targetEl = summaryEl;
  } else if (sid === "__compare__" && compareEl) {
    targetEl = compareEl;
  } else if (tabData[sid]) {
    targetEl = tabData[sid].el;
  }

  if (targetEl) {
    if (!targetEl.parentNode) {
      chatArea.appendChild(targetEl);
    }
    targetEl.classList.remove("hidden");
  }
  activeTabId = sid;

  scrollToBottom();
}

function updateTabStatus(sid, status, latencyMs) {
  var badge = document.getElementById("tab-status-" + sid);
  if (badge) {
    badge.textContent = status;
    if (status === "completed") {
      badge.className = "ml-2 text-[10px] bg-green-500/15 text-green-400 px-1.5 py-0.5 rounded";
    } else if (status === "error" || status === "failed") {
      badge.className = "ml-2 text-[10px] bg-red-500/15 text-red-400 px-1.5 py-0.5 rounded";
    }
  }
  if (latencyMs != null) {
    var lat = document.getElementById("tab-latency-" + sid);
    if (lat) {
      lat.textContent = formatLatency(latencyMs);
      lat.className = "ml-1 text-[10px] font-mono hidden " + latencyColorClass(latencyMs);
      lat.classList.remove("hidden");
    }
  }
  if (tabData[sid]) tabData[sid].status = status;
}

// ---- Summary table ----
function updateSummaryTable(strategyIds) {
  if (!summaryEl) return;
  var ids = strategyIds || Object.keys(summaryResults);

  // Find or create summary table wrapper
  var tableWrap = summaryEl.querySelector(".summary-table-wrap");
  if (!tableWrap) {
    tableWrap = document.createElement("div");
    tableWrap.className = "summary-table-wrap bg-f-surface border border-f-border rounded-xl overflow-hidden";
    summaryEl.appendChild(tableWrap);
  }

  tableWrap.textContent = "";

  // Table header
  var headerRow = document.createElement("div");
  headerRow.className = "grid grid-cols-[1fr_80px_70px_110px_80px_140px] gap-2 px-4 py-2 border-b border-f-border text-[10px] text-gray-500 font-semibold uppercase tracking-wider";
  var latencyHeader = "Latency" + (latencyBudgetMs ? " (/" + formatLatency(latencyBudgetMs) + ")" : "");
  ["Strategy", "Status", "Result", "Failure", latencyHeader, "Stages"].forEach(function(h) {
    var cell = document.createElement("span");
    cell.textContent = h;
    headerRow.appendChild(cell);
  });
  tableWrap.appendChild(headerRow);

  // Table rows
  ids.forEach(function(sid) {
    var sr = summaryResults[sid];
    if (!sr) return;
    var strat = strategies.find(function(s) { return s.id === sid; });
    var displayName = strat ? strat.display_name : sid;

    var row = document.createElement("div");
    var rowColor = strategyColorMap[sid] || "#7c3aed";
    row.className = "grid grid-cols-[1fr_80px_70px_110px_80px_140px] gap-2 px-4 py-2.5 border-b border-f-border/50 items-center cursor-pointer hover:bg-f-elevated/50 transition-colors";
    row.style.borderLeft = "3px solid " + rowColor;
    row.addEventListener("click", function() { switchTab(sid); });

    // Strategy name with color dot
    var nameCell = document.createElement("div");
    nameCell.className = "flex items-center gap-2 min-w-0";
    var nameDot = document.createElement("span");
    nameDot.style.cssText = "width:8px;height:8px;border-radius:50%;flex-shrink:0;background:" + rowColor;
    nameCell.appendChild(nameDot);
    var nameText = document.createElement("span");
    nameText.className = "text-xs text-gray-200 font-medium truncate";
    nameText.textContent = displayName;
    nameCell.appendChild(nameText);
    row.appendChild(nameCell);

    // Status with colored dot
    var statusCell = document.createElement("div");
    statusCell.className = "flex items-center gap-1.5";
    var statusDot = document.createElement("span");
    var statusText = document.createElement("span");
    statusText.className = "text-[11px]";
    if (sr.status === "completed") {
      statusDot.className = "w-2 h-2 rounded-full bg-green-500 shrink-0";
      statusText.className += " text-green-400";
      statusText.textContent = "done";
    } else if (sr.status === "error") {
      statusDot.className = "w-2 h-2 rounded-full bg-red-500 shrink-0";
      statusText.className += " text-red-400";
      statusText.textContent = "error";
    } else {
      statusDot.className = "w-2 h-2 rounded-full bg-purple-500 pulse-purple shrink-0";
      statusText.className += " text-purple-300";
      statusText.textContent = sr.currentStage ? sr.currentStage + "..." : "waiting";
    }
    statusCell.appendChild(statusDot);
    statusCell.appendChild(statusText);
    row.appendChild(statusCell);

    // Result (success/fail)
    var resultCell = document.createElement("span");
    if (sr.success === true) {
      resultCell.className = "text-[11px] text-green-400 font-semibold";
      resultCell.textContent = "Pass";
    } else if (sr.success === false) {
      resultCell.className = "text-[11px] text-red-400 font-semibold";
      resultCell.textContent = "Fail";
    } else {
      resultCell.className = "text-[11px] text-gray-600";
      resultCell.textContent = sr.status === "completed" ? "Unknown" : "\u2014";
    }
    row.appendChild(resultCell);

    // Failure category badge
    var failCell = document.createElement("span");
    var failCat = sr.failureCategory || null;
    if (failCat) {
      failCell.className = "text-[10px] px-1.5 py-0.5 rounded font-medium " + getFailureBadgeClass(failCat);
      failCell.textContent = getFailureLabel(failCat);
    } else {
      failCell.className = "text-[10px] text-gray-600";
      failCell.textContent = "\u2014";
    }
    row.appendChild(failCell);

    // Latency
    var latCell = document.createElement("span");
    latCell.className = "text-[11px] font-mono " + (sr.latency_ms != null ? latencyColorClass(sr.latency_ms) : "text-gray-600");
    latCell.textContent = formatLatency(sr.latency_ms);
    row.appendChild(latCell);

    // Stage progress dots
    var dotsCell = document.createElement("div");
    dotsCell.className = "flex items-center gap-2";
    ["perceive", "plan", "act", "verify"].forEach(function(stage) {
      var stageStatus = sr.stageStatuses ? sr.stageStatuses[stage] : undefined;
      if (stageStatus === "skipped") return;
      var dotWrap = document.createElement("div");
      dotWrap.className = "flex items-center gap-1";
      var dot = document.createElement("span");
      dot.className = "stage-dot";
      if (stageStatus === "running") dot.classList.add("running");
      else if (stageStatus === "completed") dot.classList.add("done");
      else if (stageStatus === "error") dot.classList.add("error");
      dot.setAttribute("title", stage + ": " + (stageStatus || "pending"));
      dot.setAttribute("aria-label", stage + " " + (stageStatus || "pending"));
      dotWrap.appendChild(dot);
      var dotLabel = document.createElement("span");
      dotLabel.className = "text-[10px] text-gray-600";
      dotLabel.textContent = stage.charAt(0).toUpperCase();
      dotWrap.appendChild(dotLabel);
      dotsCell.appendChild(dotWrap);
    });
    row.appendChild(dotsCell);

    tableWrap.appendChild(row);
  });

  // Compare button — only when 2+ strategies completed
  var completedIds = ids.filter(function(sid) {
    return summaryResults[sid] && summaryResults[sid].status === "completed";
  });
  if (completedIds.length >= 2) {
    var compareWrap = summaryEl.querySelector(".compare-wrap");
    if (!compareWrap) {
      compareWrap = document.createElement("div");
      compareWrap.className = "compare-wrap mt-3";
      summaryEl.appendChild(compareWrap);
    }
    compareWrap.textContent = "";
    var compareBtn = document.createElement("button");
    compareBtn.className = "flex items-center gap-2 px-4 py-2 text-xs font-medium text-white bg-f-purple hover:bg-f-purple-hover rounded-lg transition-colors shadow-sm";
    var cIcon = document.createElement("i");
    cIcon.setAttribute("data-lucide", "columns-2");
    cIcon.className = "w-4 h-4";
    compareBtn.appendChild(cIcon);
    compareBtn.appendChild(document.createTextNode(" Compare strategies"));
    compareBtn.addEventListener("click", function() {
      enterCompareSelectionMode(completedIds, compareWrap);
    });
    compareWrap.appendChild(compareBtn);
    lucide.createIcons({ nodes: [compareWrap] });
  }
}

function enterCompareSelectionMode(completedIds, container) {
  var selected = new Set();
  container.textContent = "";

  var instruction = document.createElement("p");
  instruction.className = "text-xs text-gray-400 mb-2";
  instruction.textContent = "Select 2 completed strategies to compare";
  container.appendChild(instruction);

  var chipWrap = document.createElement("div");
  chipWrap.className = "flex flex-wrap gap-2 mb-3";
  container.appendChild(chipWrap);

  completedIds.forEach(function(sid) {
    var strat = strategies.find(function(s) { return s.id === sid; });
    var displayName = strat ? strat.display_name : sid;
    var color = strategyColorMap[sid] || "#7c3aed";

    var chip = document.createElement("button");
    chip.className = "compare-chip flex items-center gap-2 px-3 py-1.5 text-xs font-medium border border-f-border rounded-lg transition-all cursor-pointer";
    chip.style.borderLeftWidth = "3px";
    chip.style.borderLeftColor = color;
    var chipLabel = document.createElement("span");
    chipLabel.className = "text-gray-300";
    chipLabel.textContent = displayName;
    chip.appendChild(chipLabel);
    chip.addEventListener("click", function() {
      if (selected.has(sid)) {
        selected.delete(sid);
        chip.classList.remove("selected");
      } else if (selected.size < 2) {
        selected.add(sid);
        chip.classList.add("selected");
      }
      openBtn.disabled = selected.size !== 2;
      openBtn.classList.toggle("opacity-50", selected.size !== 2);
    });
    chipWrap.appendChild(chip);
  });

  var btnWrap = document.createElement("div");
  btnWrap.className = "flex items-center gap-2";
  container.appendChild(btnWrap);

  var openBtn = document.createElement("button");
  openBtn.className = "px-4 py-1.5 text-xs font-medium text-white bg-f-purple rounded-lg opacity-50 transition-opacity";
  openBtn.textContent = "Open comparison";
  openBtn.disabled = true;
  openBtn.addEventListener("click", function() {
    var ids = Array.from(selected);
    openCompareTab(ids[0], ids[1]);
  });
  btnWrap.appendChild(openBtn);

  var cancelBtn = document.createElement("button");
  cancelBtn.className = "px-4 py-1.5 text-xs font-medium text-gray-400 border border-f-border rounded-lg hover:text-white transition-colors";
  cancelBtn.textContent = "Cancel";
  cancelBtn.addEventListener("click", function() {
    container.textContent = "";
    updateSummaryTable();
  });
  btnWrap.appendChild(cancelBtn);
}

// ---- Compare tab ----

function openCompareTab(sidA, sidB) {
  // Get strategy results from current history entry or live run
  var entry = activeHistoryIndex >= 0 ? runHistory[activeHistoryIndex] : null;
  var resultsA = entry && entry.results ? entry.results[sidA] : null;
  var resultsB = entry && entry.results ? entry.results[sidB] : null;
  if (!resultsA && !resultsB) return;

  // Remove existing compare tab button if present
  var oldTab = tabBar.querySelector("[data-tab-id='__compare__']");
  if (oldTab) oldTab.remove();

  // Create compare tab button
  var cmpTab = document.createElement("button");
  cmpTab.className = "strategy-tab px-4 py-2.5 text-[13px] text-gray-400 whitespace-nowrap flex items-center";
  cmpTab.setAttribute("data-tab-id", "__compare__");
  cmpTab.setAttribute("role", "tab");
  cmpTab.setAttribute("aria-selected", "false");
  cmpTab.setAttribute("aria-controls", "tab-content-__compare__");
  cmpTab.style.setProperty("--tab-color", "#f59e0b");
  var cmpIcon = document.createElement("i");
  cmpIcon.setAttribute("data-lucide", "columns-2");
  cmpIcon.className = "w-3.5 h-3.5 inline-block mr-1.5 align-middle";
  cmpTab.appendChild(cmpIcon);
  var cmpLabel = document.createElement("span");
  cmpLabel.textContent = "Comparison";
  cmpTab.appendChild(cmpLabel);
  cmpTab.addEventListener("click", function() { switchTab("__compare__"); });
  tabBar.appendChild(cmpTab);
  lucide.createIcons({ nodes: [cmpTab] });

  // Build compare content
  compareEl = document.createElement("div");
  compareEl.className = "space-y-4";
  compareEl.id = "tab-content-__compare__";
  compareEl.setAttribute("role", "tabpanel");
  compareEl.setAttribute("tabindex", "0");

  buildCompareContent(compareEl, sidA, sidB, resultsA, resultsB);

  // Switch to compare tab
  switchTab("__compare__");
}

function buildCompareContent(el, sidA, sidB, resultsA, resultsB) {
  var stratA = strategies.find(function(s) { return s.id === sidA; });
  var stratB = strategies.find(function(s) { return s.id === sidB; });
  var nameA = stratA ? stratA.display_name : sidA;
  var nameB = stratB ? stratB.display_name : sidB;
  var colorA = strategyColorMap[sidA] || "#7c3aed";
  var colorB = strategyColorMap[sidB] || "#3b82f6";

  // Summary badges
  var summaryRow = document.createElement("div");
  summaryRow.className = "grid grid-cols-2 gap-4";
  [{sid: sidA, r: resultsA, name: nameA, color: colorA}, {sid: sidB, r: resultsB, name: nameB, color: colorB}].forEach(function(item) {
    var card = document.createElement("div");
    card.className = "bg-f-surface border border-f-border rounded-xl p-4 flex items-center gap-3";
    card.style.borderLeftWidth = "3px";
    card.style.borderLeftColor = item.color;
    var nm = document.createElement("span");
    nm.className = "text-sm font-semibold text-gray-200 truncate";
    nm.textContent = item.name;
    card.appendChild(nm);
    if (item.r) {
      var badge = document.createElement("span");
      badge.className = "text-[11px] font-semibold px-2 py-0.5 rounded ml-auto ";
      if (item.r.success === true) {
        badge.className += "bg-green-500/15 text-green-400";
        badge.textContent = "Pass";
      } else if (item.r.success === false) {
        badge.className += "bg-red-500/15 text-red-400";
        badge.textContent = "Fail";
      } else {
        badge.className += "bg-gray-500/15 text-gray-400";
        badge.textContent = "Unknown";
      }
      card.appendChild(badge);
      var lat = item.r.totalLatencyMs;
      if (lat != null) {
        var latEl = document.createElement("span");
        latEl.className = "text-[11px] font-mono " + latencyColorClass(lat);
        latEl.textContent = formatLatency(lat);
        card.appendChild(latEl);
      }
    }
    summaryRow.appendChild(card);
  });

  // Latency delta
  if (resultsA && resultsB && resultsA.totalLatencyMs != null && resultsB.totalLatencyMs != null) {
    var delta = resultsA.totalLatencyMs - resultsB.totalLatencyMs;
    var deltaEl = document.createElement("div");
    deltaEl.className = "col-span-2 text-center text-[11px] font-mono " + (delta < 0 ? "text-green-400" : delta > 0 ? "text-red-400" : "text-gray-500");
    var fasterLabel = delta < 0 ? nameA + " faster" : delta > 0 ? nameB + " faster" : "equal";
    deltaEl.textContent = "Latency delta: " + (delta > 0 ? "+" : "") + Math.round(delta) + "ms (" + fasterLabel + ")";
    summaryRow.appendChild(deltaEl);
  }
  el.appendChild(summaryRow);

  // Per-stage comparison cards
  var STAGE_NAMES = ["perceive", "plan", "act", "verify"];
  var STAGE_ICONS = { perceive: "eye", plan: "brain", act: "bot", verify: "shield-check" };
  var STAGE_LABELS = { perceive: "Scene Analysis", plan: "Task Planning", act: "Actions", verify: "Verification" };

  STAGE_NAMES.forEach(function(stageName) {
    var stageA = findStageInResults(resultsA, stageName);
    var stageB = findStageInResults(resultsB, stageName);
    if (!stageA && !stageB) return;

    var card = document.createElement("div");
    card.className = "bg-f-surface border border-f-border rounded-xl overflow-hidden";

    // Stage header
    var hdr = document.createElement("div");
    hdr.className = "flex items-center gap-2 px-4 py-3 border-b border-f-border bg-f-elevated/50";
    var icon = document.createElement("i");
    icon.setAttribute("data-lucide", STAGE_ICONS[stageName]);
    icon.className = "w-4 h-4 text-purple-400";
    hdr.appendChild(icon);
    var hdrText = document.createElement("span");
    hdrText.className = "text-sm font-semibold text-gray-200";
    hdrText.textContent = STAGE_LABELS[stageName];
    hdr.appendChild(hdrText);

    // Latency in header — color-coded per stage budget
    var latA = stageA ? (stageA.latencyMs || stageA.latency_ms) : null;
    var latB = stageB ? (stageB.latencyMs || stageB.latency_ms) : null;
    if (latA != null || latB != null) {
      var latComp = document.createElement("span");
      latComp.className = "text-[10px] font-mono ml-auto";
      var budgetA = getStageBudget(stageName, sidA);
      var budgetB = getStageBudget(stageName, sidB);
      var spanA = document.createElement("span");
      spanA.className = latA != null ? latencyColorClass(latA, budgetA) : "text-gray-500";
      spanA.textContent = formatLatency(latA);
      var spanVs = document.createElement("span");
      spanVs.className = "text-gray-500";
      spanVs.textContent = " vs ";
      var spanB = document.createElement("span");
      spanB.className = latB != null ? latencyColorClass(latB, budgetB) : "text-gray-500";
      spanB.textContent = formatLatency(latB);
      latComp.appendChild(spanA);
      latComp.appendChild(spanVs);
      latComp.appendChild(spanB);
      hdr.appendChild(latComp);
    }
    card.appendChild(hdr);

    // Column labels
    var labelRow = document.createElement("div");
    labelRow.className = "grid grid-cols-2 divide-x divide-f-border border-b border-f-border/50";
    [{ name: nameA, color: colorA }, { name: nameB, color: colorB }].forEach(function(item) {
      var lbl = document.createElement("div");
      lbl.className = "px-4 py-1.5 text-[10px] uppercase tracking-wider text-gray-500 font-semibold flex items-center gap-1.5";
      var dot = document.createElement("span");
      dot.style.cssText = "width:6px;height:6px;border-radius:50%;background:" + item.color;
      lbl.appendChild(dot);
      lbl.appendChild(document.createTextNode(item.name));
      labelRow.appendChild(lbl);
    });
    card.appendChild(labelRow);

    // Side-by-side content
    var content = document.createElement("div");
    content.className = "grid grid-cols-2 divide-x divide-f-border";
    var colA = document.createElement("div");
    colA.className = "p-4 min-w-0";
    var colB = document.createElement("div");
    colB.className = "p-4 min-w-0";

    var outputA = stageA ? (stageA.output || {}) : null;
    var outputB = stageB ? (stageB.output || {}) : null;

    var renderer = COMPARE_RENDERERS[stageName];
    renderStageColumn(colA, stageA, outputA, outputB, renderer);
    renderStageColumn(colB, stageB, outputB, outputA, renderer);

    content.appendChild(colA);
    content.appendChild(colB);

    // Model IDs footer
    var modelA = stageA ? (stageA.modelId || stageA.model_id) : null;
    var modelB = stageB ? (stageB.modelId || stageB.model_id) : null;
    if (modelA || modelB) {
      var modelRow = document.createElement("div");
      modelRow.className = "grid grid-cols-2 divide-x divide-f-border border-t border-f-border/50";
      [modelA, modelB].forEach(function(mid) {
        var cell = document.createElement("div");
        cell.className = "px-4 py-1.5 text-[10px] font-mono text-gray-500";
        cell.textContent = mid || "\u2014";
        if (modelA && modelB && modelA !== modelB) cell.classList.add("diff-changed");
        modelRow.appendChild(cell);
      });
      content.appendChild(modelRow);
    }

    card.appendChild(content);
    el.appendChild(card);
  });

  lucide.createIcons({ nodes: [el] });
}

function findStageInResults(resultObj, stageName) {
  if (!resultObj || !resultObj.stages) return null;
  for (var i = 0; i < resultObj.stages.length; i++) {
    if (resultObj.stages[i].stage === stageName) return resultObj.stages[i];
  }
  return null;
}

function renderStageColumn(col, stageData, output, otherOutput, renderer) {
  if (!stageData) {
    var skip = document.createElement("div");
    skip.className = "text-xs text-gray-600 italic";
    skip.textContent = "Stage skipped";
    col.appendChild(skip);
  } else if (stageData.status === "error") {
    var errEl = document.createElement("div");
    errEl.className = "text-xs text-red-400";
    errEl.textContent = stageData.error || "Error";
    col.appendChild(errEl);
  } else if (renderer) {
    renderer(col, output || {}, otherOutput || {});
  }
}

// ---- Compare diff helpers ----

function cmpLabel(parent, text) {
  var lbl = document.createElement("div");
  lbl.className = "text-[10px] uppercase tracking-wider text-gray-500 font-semibold mb-1";
  lbl.textContent = text;
  parent.appendChild(lbl);
}

function cmpStringDiff(parent, label, val, otherVal) {
  var wrap = document.createElement("div");
  wrap.className = "mb-3";
  if (val !== otherVal && val && otherVal) wrap.classList.add("diff-changed");
  cmpLabel(wrap, label);
  var v = document.createElement("div");
  v.className = "text-sm text-gray-200";
  v.textContent = val || "\u2014";
  wrap.appendChild(v);
  parent.appendChild(wrap);
}

function cmpNumberDiff(parent, label, val, otherVal, fmt) {
  var wrap = document.createElement("div");
  wrap.className = "mb-3";
  cmpLabel(wrap, label);
  var row = document.createElement("div");
  row.className = "flex items-center gap-2";
  var v = document.createElement("span");
  v.className = "text-sm font-mono text-gray-200";
  v.textContent = val != null ? (fmt ? fmt(val) : String(val)) : "\u2014";
  row.appendChild(v);
  if (val != null && otherVal != null && val !== otherVal) {
    var delta = val - otherVal;
    var deltaEl = document.createElement("span");
    deltaEl.className = "text-[11px] font-mono " + (delta > 0 ? "text-green-400" : "text-red-400");
    deltaEl.textContent = (delta > 0 ? "+" : "") + (fmt ? fmt(delta) : String(delta));
    row.appendChild(deltaEl);
    wrap.classList.add("diff-changed");
  }
  wrap.appendChild(row);
  parent.appendChild(wrap);
}

function cmpBoolDiff(parent, label, val, otherVal) {
  var wrap = document.createElement("div");
  wrap.className = "mb-3";
  cmpLabel(wrap, label);
  var badge = document.createElement("span");
  badge.className = "text-xs font-semibold px-2 py-0.5 rounded ";
  if (val === true) {
    badge.className += "bg-green-500/15 text-green-400";
    badge.textContent = "Pass";
  } else if (val === false) {
    badge.className += "bg-red-500/15 text-red-400";
    badge.textContent = "Fail";
  } else {
    badge.className += "bg-gray-500/15 text-gray-400";
    badge.textContent = "\u2014";
  }
  wrap.appendChild(badge);
  if (val !== otherVal) {
    var marker = document.createElement("span");
    marker.className = "text-[10px] text-amber-400 ml-2";
    marker.textContent = "(differs)";
    wrap.appendChild(marker);
    wrap.classList.add("diff-changed");
  }
  parent.appendChild(wrap);
}

function cmpStringListDiff(parent, label, myList, otherList) {
  if (!myList && !otherList) return;
  var mine = myList || [];
  var other = new Set(otherList || []);
  var wrap = document.createElement("div");
  wrap.className = "mb-3";
  cmpLabel(wrap, label);
  var tagWrap = document.createElement("div");
  tagWrap.className = "flex flex-wrap gap-1";
  mine.forEach(function(item) {
    var tag = document.createElement("span");
    tag.className = "text-[11px] px-2 py-0.5 rounded ";
    if (other.has(item)) {
      tag.className += "bg-purple-500/15 text-purple-300";
    } else {
      tag.className += "bg-emerald-500/15 text-emerald-300";
      wrap.classList.add("diff-unique");
    }
    tag.textContent = item;
    tagWrap.appendChild(tag);
  });
  if (mine.length === 0) {
    var empty = document.createElement("span");
    empty.className = "text-xs text-gray-600";
    empty.textContent = "None";
    tagWrap.appendChild(empty);
  }
  wrap.appendChild(tagWrap);
  parent.appendChild(wrap);
}

function cmpOrderedListDiff(parent, label, myList, otherList) {
  if (!myList && !otherList) return;
  var mine = myList || [];
  var other = otherList || [];
  var wrap = document.createElement("div");
  wrap.className = "mb-3";
  cmpLabel(wrap, label);
  var ol = document.createElement("ol");
  ol.className = "list-decimal list-inside space-y-1 text-sm text-gray-200";
  mine.forEach(function(item, i) {
    var li = document.createElement("li");
    var text = typeof item === "string" ? item : (item.description || item.action || JSON.stringify(item));
    li.textContent = text;
    if (i >= other.length) {
      li.className = "bg-emerald-500/5 border-l-2 border-emerald-500 pl-2";
    } else {
      var otherText = typeof other[i] === "string" ? other[i] : (other[i].description || other[i].action || JSON.stringify(other[i]));
      if (text !== otherText) {
        li.className = "bg-amber-500/5 border-l-2 border-amber-500 pl-2";
      }
    }
    ol.appendChild(li);
  });
  wrap.appendChild(ol);
  parent.appendChild(wrap);
}

function cmpConfidenceBar(parent, label, val, otherVal) {
  var wrap = document.createElement("div");
  wrap.className = "mb-3";
  if (val !== otherVal) wrap.classList.add("diff-changed");
  cmpLabel(wrap, label);
  var barBg = document.createElement("div");
  barBg.className = "w-full h-2 rounded-full bg-gray-700 mt-1";
  var barFill = document.createElement("div");
  var pct = (val != null ? val : 0) * 100;
  barFill.className = "h-full rounded-full transition-all";
  barFill.style.width = Math.max(0, Math.min(100, pct)) + "%";
  barFill.style.background = pct >= 70 ? "#22c55e" : pct >= 40 ? "#f59e0b" : "#ef4444";
  barBg.appendChild(barFill);
  wrap.appendChild(barBg);
  var valRow = document.createElement("div");
  valRow.className = "flex items-center gap-2 mt-1";
  var num = document.createElement("span");
  num.className = "text-xs font-mono text-gray-300";
  num.textContent = val != null ? (val * 100).toFixed(1) + "%" : "\u2014";
  valRow.appendChild(num);
  if (val != null && otherVal != null && val !== otherVal) {
    var d = (val - otherVal) * 100;
    var deltaEl = document.createElement("span");
    deltaEl.className = "text-[11px] font-mono " + (d > 0 ? "text-green-400" : "text-red-400");
    deltaEl.textContent = (d > 0 ? "+" : "") + d.toFixed(1) + "%";
    valRow.appendChild(deltaEl);
  }
  wrap.appendChild(valRow);
  parent.appendChild(wrap);
}

// ---- Per-stage compare renderers ----

function cmpRenderPerceive(col, output, otherOutput) {
  var o = output || {};
  var oo = otherOutput || {};
  cmpStringDiff(col, "Environment", o.environment_distribution, oo.environment_distribution);

  var myObjs = o.objects || [];
  var otherObjs = oo.objects || [];
  var otherNames = {};
  otherObjs.forEach(function(ob) { otherNames[ob.name || ob.label || ""] = ob; });

  if (myObjs.length > 0 || otherObjs.length > 0) {
    var wrap = document.createElement("div");
    wrap.className = "mb-3";
    cmpLabel(wrap, "Detected Objects");
    myObjs.forEach(function(obj) {
      var name = obj.name || obj.label || "unknown";
      var conf = obj.confidence;
      var matched = otherNames[name];
      var row = document.createElement("div");
      row.className = "flex items-center gap-2 text-sm py-0.5";
      var tag = document.createElement("span");
      tag.className = "text-[11px] px-2 py-0.5 rounded ";
      tag.className += matched ? "bg-purple-500/15 text-purple-300" : "bg-emerald-500/15 text-emerald-300";
      tag.textContent = name;
      row.appendChild(tag);
      if (conf != null) {
        var confEl = document.createElement("span");
        confEl.className = "text-[10px] font-mono text-gray-500";
        confEl.textContent = (conf * 100).toFixed(0) + "%";
        row.appendChild(confEl);
        if (matched && matched.confidence != null && matched.confidence !== conf) {
          var d = (conf - matched.confidence) * 100;
          var delta = document.createElement("span");
          delta.className = "text-[10px] font-mono " + (d > 0 ? "text-green-400" : "text-red-400");
          delta.textContent = (d > 0 ? "+" : "") + d.toFixed(0) + "%";
          row.appendChild(delta);
        }
      }
      wrap.appendChild(row);
    });
    col.appendChild(wrap);
  }

  cmpStringListDiff(col, "Spatial Relations", o.spatial_relations, oo.spatial_relations);
}

function cmpRenderPlan(col, output, otherOutput) {
  var o = output || {};
  var oo = otherOutput || {};
  cmpStringDiff(col, "Strategy", o.strategy, oo.strategy);
  cmpStringDiff(col, "Target Object", o.target_object, oo.target_object);
  cmpStringDiff(col, "Reasoning", o.reasoning, oo.reasoning);
  cmpStringDiff(col, "Subtask Reasoning", o.subtask_reasoning, oo.subtask_reasoning);
  cmpStringDiff(col, "Action Reasoning", o.action_reasoning, oo.action_reasoning);
  cmpStringListDiff(col, "Constraints Acknowledged", o.constraints_acknowledged, oo.constraints_acknowledged);
  cmpOrderedListDiff(col, "Steps", o.steps, oo.steps);
  cmpConfidenceBar(col, "Confidence", o.confidence, oo.confidence);
  cmpStringListDiff(col, "Task Repertoire", o.task_repertoire, oo.task_repertoire);
  cmpStringListDiff(col, "Artifacts", o.artifacts, oo.artifacts);
  cmpStringListDiff(col, "Degradation Profile", o.degradation_profile, oo.degradation_profile);
}

function cmpRenderAct(col, output, otherOutput) {
  var o = output || {};
  var oo = otherOutput || {};
  cmpStringDiff(col, "Action Type", o.action_type, oo.action_type);
  cmpNumberDiff(col, "Steps", o.num_steps, oo.num_steps);
  cmpConfidenceBar(col, "Confidence", o.confidence, oo.confidence);

  if (o.action_type === "tool_calls" && o.tool_calls) {
    cmpOrderedListDiff(col, "Tool Calls", o.tool_calls, oo.tool_calls);
  }

  var actions = o.actions || o.trajectory;
  if (Array.isArray(actions) && actions.length > 0) {
    var wrap = document.createElement("div");
    wrap.className = "mb-3";
    cmpLabel(wrap, "Trajectory (" + actions.length + " steps)");
    var preview = document.createElement("div");
    preview.className = "text-[11px] font-mono text-gray-400 space-y-0.5";
    var show = actions.slice(0, 3);
    show.forEach(function(a, i) {
      var line = document.createElement("div");
      line.textContent = "[" + i + "] " + JSON.stringify(a).substring(0, 80);
      preview.appendChild(line);
    });
    if (actions.length > 6) {
      var ellipsis = document.createElement("div");
      ellipsis.className = "text-gray-600";
      ellipsis.textContent = "... " + (actions.length - 6) + " more steps ...";
      preview.appendChild(ellipsis);
    }
    if (actions.length > 3) {
      actions.slice(-3).forEach(function(a, i) {
        var line = document.createElement("div");
        line.textContent = "[" + (actions.length - 3 + i) + "] " + JSON.stringify(a).substring(0, 80);
        preview.appendChild(line);
      });
    }
    wrap.appendChild(preview);
    col.appendChild(wrap);
  }
}

function cmpRenderVerify(col, output, otherOutput) {
  var o = output || {};
  var oo = otherOutput || {};
  cmpBoolDiff(col, "Success", o.success, oo.success);
  cmpConfidenceBar(col, "Confidence", o.confidence, oo.confidence);
  cmpStringDiff(col, "Reasoning", o.reasoning, oo.reasoning);

  // Stage checks — skip for agent_loop mode (same logic as renderVerify)
  var isAgentLoop = (o.verify_turns && o.verify_turns > 1) || o.resolution_path;
  var myChecks = o.stage_checks || [];
  var otherChecks = oo.stage_checks || [];
  if (!isAgentLoop && (myChecks.length > 0 || otherChecks.length > 0)) {
    var otherMap = {};
    otherChecks.forEach(function(c) { otherMap[c.stage || c.name || ""] = c; });
    var wrap = document.createElement("div");
    wrap.className = "mb-3";
    cmpLabel(wrap, "Stage Checks");
    myChecks.forEach(function(check) {
      var name = check.stage || check.name || "unknown";
      var matched = otherMap[name];
      var row = document.createElement("div");
      row.className = "flex items-center gap-2 py-1 text-sm";
      if (matched && (matched.passed !== check.passed || matched.reasoning !== check.reasoning)) {
        row.classList.add("diff-changed");
      }
      var badge = document.createElement("span");
      badge.className = "text-[10px] font-semibold px-1.5 py-0.5 rounded ";
      badge.className += check.passed ? "bg-green-500/15 text-green-400" : "bg-red-500/15 text-red-400";
      badge.textContent = check.passed ? "PASS" : "FAIL";
      row.appendChild(badge);
      var nameEl = document.createElement("span");
      nameEl.className = "text-xs text-gray-300 font-medium";
      nameEl.textContent = name;
      row.appendChild(nameEl);
      if (check.reasoning) {
        var reason = document.createElement("span");
        reason.className = "text-[11px] text-gray-500 ml-1";
        reason.textContent = "\u2014 " + check.reasoning;
        row.appendChild(reason);
      }
      wrap.appendChild(row);
    });
    col.appendChild(wrap);
  }

  // Ground truth
  var gt = o.ground_truth;
  var ogt = oo.ground_truth;
  if (gt || ogt) {
    var wrap = document.createElement("div");
    wrap.className = "mb-3";
    if (JSON.stringify(gt) !== JSON.stringify(ogt)) wrap.classList.add("diff-changed");
    cmpLabel(wrap, "Ground Truth");
    if (gt) {
      var badge = document.createElement("span");
      badge.className = "text-xs font-semibold px-2 py-0.5 rounded ";
      badge.className += gt.correct ? "bg-green-500/15 text-green-400" : "bg-red-500/15 text-red-400";
      badge.textContent = gt.correct ? "Correct" : "Incorrect";
      wrap.appendChild(badge);
      if (gt.expected) {
        var exp = document.createElement("div");
        exp.className = "text-[11px] text-gray-500 mt-1";
        exp.textContent = "Expected: " + gt.expected;
        wrap.appendChild(exp);
      }
      if (gt.actual) {
        var act = document.createElement("div");
        act.className = "text-[11px] text-gray-500";
        act.textContent = "Actual: " + gt.actual;
        wrap.appendChild(act);
      }
    } else {
      var none = document.createElement("span");
      none.className = "text-xs text-gray-600";
      none.textContent = "\u2014";
      wrap.appendChild(none);
    }
    col.appendChild(wrap);
  }

  // Action plausibility
  var ap = o.action_plausibility;
  var oap = oo.action_plausibility;
  if (ap || oap) {
    var apWrap = document.createElement("div");
    apWrap.className = "mb-3";
    cmpLabel(apWrap, "Action Plausibility");
    if (ap) {
      if (ap.bounds_check != null) cmpBoolDiff(apWrap, "Bounds Check", ap.bounds_check, oap ? oap.bounds_check : undefined);
      if (ap.smoothness != null) cmpBoolDiff(apWrap, "Smoothness", ap.smoothness, oap ? oap.smoothness : undefined);
      if (ap.gripper_consistency != null) cmpBoolDiff(apWrap, "Gripper Consistency", ap.gripper_consistency, oap ? oap.gripper_consistency : undefined);
      if (ap.plan_alignment != null) cmpConfidenceBar(apWrap, "Plan Alignment", ap.plan_alignment, oap ? oap.plan_alignment : undefined);
      if (ap.reasoning) {
        cmpStringDiff(apWrap, "Plausibility Reasoning", ap.reasoning, oap ? oap.reasoning : undefined);
      }
    } else {
      var none = document.createElement("span");
      none.className = "text-xs text-gray-600";
      none.textContent = "\u2014";
      apWrap.appendChild(none);
    }
    col.appendChild(apWrap);
  }
}

var COMPARE_RENDERERS = {
  perceive: cmpRenderPerceive,
  plan: cmpRenderPlan,
  act: cmpRenderAct,
  dynamics: null,
  verify: cmpRenderVerify,
};

// ---- SSE connection ----
function connectSSE(evalId) {
  if (currentEventSource) currentEventSource.close();

  var es = new EventSource(API_BASE + "/api/evaluate/" + evalId + "/stream");
  currentEventSource = es;

  // Find the active history entry for this eval
  var historyEntry = runHistory.find(function(e) { return e.id === evalId; });

  es.addEventListener("strategy_started", function(e) {
    var data = JSON.parse(e.data);
    var sid = data.strategy_id;
    if (sid && data.pipeline_mode && summaryResults[sid]) {
      summaryResults[sid].pipelineMode = data.pipeline_mode;
    }
    if (sid && data.pipeline_mode && tabData[sid]) {
      tabData[sid].pipelineMode = data.pipeline_mode;
    }
  });

  es.addEventListener("stage", function(e) {
    var data = JSON.parse(e.data);
    var sid = data.strategy_id || activeTabId;
    var stage = data.stage;
    var status = data.status;
    var phase = data.phase || "";

    if (!tabData[sid]) return;

    tabData[sid].stages[stage] = status;

    // Update summary results
    if (summaryResults[sid]) {
      summaryResults[sid].stageStatuses[stage] = status;
      if (status === "running") {
        summaryResults[sid].currentStage = stage;
      }
      updateSummaryTable();
    }

    if (status === "running") {
      var output = data.output;
      // Handle verify sub-steps (agent loop mode)
      if (stage === "verify" && output && output.substep) {
        var mode3 = (tabData[sid] && tabData[sid].pipelineMode) || "sequential";
        var subContainer = tabData[sid].el;
        if (mode3 === "parallel" && phase) {
          subContainer = getPhaseContent(phase, subContainer, sid) || subContainer;
        }
        // Find or create verify sub-steps container
        var subStepsId = "verify-substeps-" + sid;
        var subStepsEl = document.getElementById(subStepsId);
        if (!subStepsEl) {
          subStepsEl = document.createElement("div");
          subStepsEl.id = subStepsId;
          subStepsEl.className = "ml-6 mt-1 space-y-1 text-xs text-gray-400";
          subContainer.appendChild(subStepsEl);
        }
        var subItem = document.createElement("div");
        subItem.className = "flex items-center gap-2 animate-fade-in";
        if (output.substep === "turn") {
          var turnLabel = document.createElement("span");
          turnLabel.className = "text-gray-500";
          turnLabel.textContent = "Turn " + output.turn;
          subItem.appendChild(turnLabel);
          (output.tools_requested || []).forEach(function(t) {
            var badge = document.createElement("span");
            badge.className = "px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-300 text-[10px]";
            badge.textContent = t;
            subItem.appendChild(badge);
          });
        } else if (output.substep === "check") {
          subItem.textContent = "Checking " + output.check;
        } else if (output.substep === "tool") {
          var arrow = document.createElement("span");
          arrow.className = "text-green-400";
          arrow.textContent = "\u2192";
          subItem.appendChild(arrow);
          var toolName = document.createElement("span");
          toolName.className = "text-gray-300";
          toolName.textContent = output.tool;
          subItem.appendChild(toolName);
          if (output.latency_ms != null) {
            var toolLat = document.createElement("span");
            toolLat.className = "text-gray-500";
            toolLat.textContent = " (" + Math.round(output.latency_ms) + "ms)";
            subItem.appendChild(toolLat);
          }
        }
        subStepsEl.appendChild(subItem);
        return; // Don't create a new typing indicator for sub-steps
      }

      // Add phase header before first stage of each phase (parallel mode)
      var mode = (tabData[sid] && tabData[sid].pipelineMode) || (summaryResults[sid] && summaryResults[sid].pipelineMode) || "sequential";
      var stageContainer = tabData[sid].el;
      if (mode === "parallel" && phase) {
        addPhaseHeader(phase, stage, tabData[sid].el, sid);
        stageContainer = getPhaseContent(phase, tabData[sid].el, sid) || tabData[sid].el;
      } else if (mode !== "parallel") {
        // Sequential mode: remove previous typing indicator (only one stage active)
        if (tabData[sid].typingEl) {
          tabData[sid].typingEl.classList.add("animate-fade-out");
          var oldTyping = tabData[sid].typingEl;
          setTimeout(function() { oldTyping.remove(); }, 150);
        }
      }
      // In parallel mode, each stage gets its own typing indicator
      var newTyping = addTypingIndicator(stage, stageContainer, sid);
      tabData[sid].typingEl = newTyping;
      if (!tabData[sid].typingEls) tabData[sid].typingEls = {};
      tabData[sid].typingEls[stage] = newTyping;
    }

    if (status === "completed" || status === "error") {
      // Remove typing indicator for this specific stage
      if (tabData[sid].typingEls && tabData[sid].typingEls[stage]) {
        tabData[sid].typingEls[stage].classList.add("animate-fade-out");
        var oldStageEl = tabData[sid].typingEls[stage];
        setTimeout(function() { oldStageEl.remove(); }, 150);
        delete tabData[sid].typingEls[stage];
      } else if (tabData[sid].typingEl) {
        tabData[sid].typingEl.classList.add("animate-fade-out");
        var oldEl = tabData[sid].typingEl;
        setTimeout(function() { oldEl.remove(); }, 150);
      }
      tabData[sid].typingEl = null;
      // In parallel mode, add stage card to the phase content area
      var mode2 = (tabData[sid] && tabData[sid].pipelineMode) || (summaryResults[sid] && summaryResults[sid].pipelineMode) || "sequential";
      var cardContainer = tabData[sid].el;
      if (mode2 === "parallel" && phase) {
        // Ensure phase header exists (may not have been created if RUNNING event was missed)
        addPhaseHeader(phase, stage, tabData[sid].el, sid);
        cardContainer = getPhaseContent(phase, tabData[sid].el, sid) || tabData[sid].el;
      }
      var stageIndex = ["perceive", "plan", "act", "dynamics", "verify"].indexOf(stage);
      addStageCard(stage, status, data.latency_ms, data.output, data.error, data.model_id, cardContainer, stageIndex, sid);
      var stageLabel = STAGES[stage] ? STAGES[stage].label : stage;
      announce(stageLabel + " " + status);

      // Store stage data in history entry
      if (historyEntry && historyEntry.results[sid]) {
        historyEntry.results[sid].stages.push({
          stage: stage,
          status: status,
          latencyMs: data.latency_ms,
          output: data.output,
          error: data.error,
          modelId: data.model_id,
          phase: phase,
        });
      }
    }
  });

  es.addEventListener("strategy_complete", function(e) {
    var data = JSON.parse(e.data);
    var sid = data.strategy_id;
    if (tabData[sid]) {
      // Remove all remaining typing indicators
      if (tabData[sid].typingEls) {
        Object.keys(tabData[sid].typingEls).forEach(function(s) {
          if (tabData[sid].typingEls[s]) tabData[sid].typingEls[s].remove();
        });
        tabData[sid].typingEls = {};
      }
      if (tabData[sid].typingEl) {
        tabData[sid].typingEl.remove();
        tabData[sid].typingEl = null;
      }
    }
    updateTabStatus(sid, "completed", data.total_latency_ms);

    // Update summary
    if (summaryResults[sid]) {
      summaryResults[sid].status = "completed";
      summaryResults[sid].latency_ms = data.total_latency_ms;
      summaryResults[sid].success = data.verdict_valid === false ? null : (data.success != null ? data.success : null);
      summaryResults[sid].currentStage = null;
      summaryResults[sid].failureCategory = data.failure_category || null;
      updateSummaryTable();
    }

    // Store in history entry
    if (historyEntry && historyEntry.results[sid]) {
      historyEntry.results[sid].success = data.verdict_valid === false ? null : data.success;
      historyEntry.results[sid].totalLatencyMs = data.total_latency_ms;
      historyEntry.results[sid].failureStage = data.failure_stage || null;
      historyEntry.results[sid].failureCategory = data.failure_category || null;
    }
  });

  es.addEventListener("strategy_error", function(e) {
    var data = JSON.parse(e.data);
    var sid = data.strategy_id;
    if (tabData[sid]) {
      if (tabData[sid].typingEls) {
        Object.keys(tabData[sid].typingEls).forEach(function(s) {
          if (tabData[sid].typingEls[s]) tabData[sid].typingEls[s].remove();
        });
        tabData[sid].typingEls = {};
      }
      if (tabData[sid].typingEl) {
        tabData[sid].typingEl.remove();
        tabData[sid].typingEl = null;
      }
    }
    updateTabStatus(sid, "error");
    addErrorCard(data.error || "Strategy failed", tabData[sid] ? tabData[sid].el : chatArea);

    // Update summary
    if (summaryResults[sid]) {
      summaryResults[sid].status = "error";
      summaryResults[sid].currentStage = null;
      updateSummaryTable();
    }
  });

  es.addEventListener("complete", function(e) {
    es.close();
    currentEventSource = null;
    setRunning(false);
    // Store provenance and insights from complete event, then render
    if (e.data) {
      try {
        var completeData = JSON.parse(e.data);
        if (historyEntry) {
          if (completeData.provenance) historyEntry.provenance = completeData.provenance;
          if (completeData.insights) historyEntry.insights = completeData.insights;
        }
        // Render provenance + insights into summary tab or active container
        var insightsTarget = summaryEl;
        if (!insightsTarget && activeTabId && tabData[activeTabId]) {
          insightsTarget = tabData[activeTabId].el;
        }
        if (insightsTarget) {
          if (completeData.provenance) {
            renderProvenanceCard(completeData.provenance, insightsTarget);
          }
          if (completeData.insights) {
            renderInsightsCard(completeData.insights, insightsTarget);
          }
        }
      } catch (_) {}
    }
    updateHistoryStatus(evalId, "completed");
  });

  es.addEventListener("error", function(e) {
    Object.values(tabData).forEach(function(td) {
      if (td.typingEls) {
        Object.keys(td.typingEls).forEach(function(s) {
          if (td.typingEls[s]) td.typingEls[s].remove();
        });
        td.typingEls = {};
      }
      if (td.typingEl) { td.typingEl.remove(); td.typingEl = null; }
    });
    if (es.readyState === EventSource.CLOSED) {
      setRunning(false);
      return;
    }
    var errMsg = "Connection lost";
    try {
      if (e.data) {
        var parsed = JSON.parse(e.data);
        if (parsed.message) errMsg = parsed.message;
      }
    } catch (_) {}
    addErrorCard(errMsg, chatArea);
    es.close();
    currentEventSource = null;
    setRunning(false);
    updateHistoryStatus(evalId, "error");
  });
}

// ---- UI helpers ----
function setRunning(val) {
  isRunning = val;
  evalBtn.disabled = val;
  if (val) {
    evalBtn.classList.add("opacity-50");
    setConfigCollapsed(true);
  } else {
    evalBtn.classList.remove("opacity-50");
  }
}

function scrollToBottom() {
  requestAnimationFrame(function() {
    chatArea.scrollTop = chatArea.scrollHeight;
  });
}

function addErrorToActiveTab(message) {
  var container = tabData[activeTabId] ? tabData[activeTabId].el : chatArea;
  addErrorCard(message, container);
}

// ---- Card builders (structured cards, not chat bubbles) ----

function addUserCard(imgSrc, task, strategyIds) {
  var card = document.createElement("div");
  card.className = "animate-slide-in bg-f-surface border border-f-border rounded-xl p-3 space-y-2.5";

  // Header
  var headerRow = document.createElement("div");
  headerRow.className = "flex items-center gap-2";
  var userIcon = document.createElement("i");
  userIcon.setAttribute("data-lucide", "user");
  userIcon.className = "w-3.5 h-3.5 text-gray-400";
  headerRow.appendChild(userIcon);
  var headerLabel = document.createElement("span");
  headerLabel.className = "text-[10px] font-semibold text-gray-500 uppercase tracking-wider";
  headerLabel.textContent = "Evaluation Request";
  headerRow.appendChild(headerLabel);
  card.appendChild(headerRow);

  var img = document.createElement("img");
  img.src = imgSrc;
  img.alt = "Uploaded workspace image";
  img.className = "w-full max-h-56 object-contain w-auto mx-auto rounded-lg img-zoom origin-top-left";
  card.appendChild(img);

  var taskP = document.createElement("p");
  taskP.className = "text-sm text-gray-100";
  taskP.textContent = task;
  card.appendChild(taskP);

  // Strategy badges
  var badges = document.createElement("div");
  badges.className = "flex flex-wrap gap-1.5";
  strategyIds.forEach(function(sid) {
    var strat = strategies.find(function(s) { return s.id === sid; });
    var badge = document.createElement("span");
    badge.className = "text-[10px] bg-f-purple/15 text-purple-300 px-2 py-0.5 rounded";
    badge.textContent = strat ? strat.display_name : sid;
    badges.appendChild(badge);
  });
  card.appendChild(badges);

  // Add only to summary tab (multi-strategy) or the single tab
  if (summaryEl) {
    summaryEl.insertBefore(card, summaryEl.firstChild);
  } else {
    var container = tabData[activeTabId] ? tabData[activeTabId].el : chatArea;
    container.appendChild(card);
  }

  lucide.createIcons({ nodes: [card] });
  scrollToBottom();
}

function addTypingIndicator(stage, container, sid) {
  var meta = STAGES[stage];
  var card = document.createElement("div");
  card.className = "animate-slide-in bg-f-surface border border-f-border rounded-xl p-4 stage-card-" + stage;

  var header = document.createElement("div");
  header.className = "flex items-center gap-2 mb-2";
  var icon = document.createElement("i");
  icon.setAttribute("data-lucide", meta.icon);
  icon.className = "w-4 h-4 text-purple-400";
  header.appendChild(icon);
  var label = document.createElement("span");
  label.className = "text-xs font-semibold text-purple-400";
  var strat = sid ? strategies.find(function(s) { return s.id === sid; }) : null;
  var prefix = (strat && Object.keys(tabData).length > 1) ? strat.display_name + " \u2014 " : "";
  label.textContent = prefix + meta.label;
  header.appendChild(label);
  var chip = document.createElement("span");
  chip.className = "text-[10px] bg-f-purple/20 text-purple-300 px-1.5 py-0.5 rounded ml-auto";
  chip.textContent = "running";
  header.appendChild(chip);
  card.appendChild(header);

  var dots = document.createElement("div");
  dots.className = "flex items-center gap-1.5 py-1";
  for (var i = 0; i < 3; i++) {
    var dot = document.createElement("div");
    dot.className = "w-2 h-2 bg-purple-400 rounded-full typing-dot";
    dots.appendChild(dot);
  }
  card.appendChild(dots);

  container.appendChild(card);
  lucide.createIcons({ nodes: [card] });
  scrollToBottom();
  return card;
}

function addPhaseHeader(phase, stage, container, strategyId) {
  // Only add the header once per phase — use container.querySelector (works even if not in DOM)
  var headerId = "phase-header-" + (strategyId || "") + "-" + phase;
  if (container.querySelector("#" + CSS.escape(headerId))) return;

  // Colors: execution = sky (object under test), evaluation = purple
  var colorText = phase === "execution" ? "text-sky-400" : "text-purple-400";
  var colorBorder = phase === "execution" ? "border-sky-500/30" : "border-purple-500/30";

  // Collapsible wrapper — contains header + content area
  var wrapper = document.createElement("div");
  wrapper.id = headerId;
  wrapper.className = "animate-slide-in mt-4";

  // Clickable header row
  var header = document.createElement("button");
  header.className = "flex items-center gap-2 mb-2 w-full group cursor-pointer";
  header.setAttribute("aria-expanded", "true");
  var chevron = document.createElement("i");
  chevron.setAttribute("data-lucide", "chevron-down");
  chevron.className = "w-3.5 h-3.5 " + colorText + " transition-transform duration-200 phase-chevron";
  header.appendChild(chevron);
  var icon = document.createElement("i");
  icon.setAttribute("data-lucide", phase === "execution" ? "play" : "clipboard-check");
  icon.className = "w-4 h-4 " + colorText;
  header.appendChild(icon);
  var label = document.createElement("span");
  label.className = "text-xs font-bold uppercase tracking-widest " + colorText;
  label.textContent = phase === "execution" ? "Execution" : "Evaluation";
  header.appendChild(label);
  var line = document.createElement("div");
  line.className = "flex-1 border-t " + colorBorder;
  header.appendChild(line);
  wrapper.appendChild(header);

  // Content area for stage cards
  var contentId = "phase-content-" + (strategyId || "") + "-" + phase;
  var content = document.createElement("div");
  content.id = contentId;
  content.className = "space-y-3 pl-1 border-l-2 " + colorBorder + " ml-1.5 transition-all duration-200";
  wrapper.appendChild(content);

  // Toggle collapse/expand
  header.addEventListener("click", function() {
    var isExpanded = header.getAttribute("aria-expanded") === "true";
    header.setAttribute("aria-expanded", String(!isExpanded));
    if (isExpanded) {
      content.style.maxHeight = content.scrollHeight + "px";
      // Force reflow then collapse
      content.offsetHeight;
      content.style.maxHeight = "0px";
      content.style.overflow = "hidden";
      content.style.opacity = "0.4";
      chevron.style.transform = "rotate(-90deg)";
    } else {
      content.style.maxHeight = content.scrollHeight + "px";
      content.style.overflow = "";
      content.style.opacity = "1";
      chevron.style.transform = "";
      // After transition, remove maxHeight constraint
      setTimeout(function() {
        if (header.getAttribute("aria-expanded") === "true") {
          content.style.maxHeight = "";
        }
      }, 200);
    }
  });

  // Ensure execution phase is always before evaluation phase in DOM
  var otherPhase = phase === "execution" ? "evaluation" : "execution";
  var otherHeaderId = "phase-header-" + (strategyId || "") + "-" + otherPhase;
  var otherWrapper = container.querySelector("#" + CSS.escape(otherHeaderId));
  if (phase === "execution" && otherWrapper) {
    // Execution goes before evaluation
    container.insertBefore(wrapper, otherWrapper);
  } else if (phase === "evaluation" && otherWrapper && otherWrapper.nextSibling) {
    // Evaluation goes after execution
    container.insertBefore(wrapper, otherWrapper.nextSibling);
  } else {
    container.appendChild(wrapper);
  }

  lucide.createIcons({ nodes: [wrapper] });
}

function getPhaseContent(phase, container, strategyId) {
  // Get the content area for a phase section — use container.querySelector (works even if not in DOM)
  var contentId = "phase-content-" + (strategyId || "") + "-" + phase;
  return container.querySelector("#" + CSS.escape(contentId));
}

function addStageCard(stage, status, latencyMs, output, error, modelId, container, stageIndex, strategyId) {
  var meta = STAGES[stage];
  var isOk = status === "completed";
  var accentColor = isOk ? "green" : "red";
  var cardId = "card-" + stage + "-" + Date.now();

  var card = document.createElement("div");
  card.className = "animate-slide-in bg-f-surface border border-f-border rounded-xl p-4 space-y-3 w-full stage-card-" + stage;
  if (stageIndex != null) {
    card.style.animationDelay = (stageIndex * 50) + "ms";
  }

  // Header row
  var headerRow = document.createElement("div");
  headerRow.className = "flex items-center justify-between";

  var headerLeft = document.createElement("div");
  headerLeft.className = "flex items-center gap-2";
  var icon = document.createElement("i");
  icon.setAttribute("data-lucide", meta.icon);
  icon.className = "w-4 h-4 text-" + accentColor + "-400";
  headerLeft.appendChild(icon);
  var labelSpan = document.createElement("span");
  labelSpan.className = "text-xs font-semibold text-" + accentColor + "-400";
  labelSpan.textContent = meta.label;
  headerLeft.appendChild(labelSpan);

  // Status chip
  var statusChip = document.createElement("span");
  if (isOk) {
    statusChip.className = "text-[10px] bg-green-500/15 text-green-400 px-1.5 py-0.5 rounded ml-2";
    statusChip.textContent = "completed";
  } else {
    statusChip.className = "text-[10px] bg-red-500/15 text-red-400 px-1.5 py-0.5 rounded ml-2";
    statusChip.textContent = "error";
  }
  headerLeft.appendChild(statusChip);

  // Success/fail badge for verify
  if (stage === "verify" && output && output.success != null) {
    var badge = document.createElement("span");
    if (output.verdict_valid === false) {
      badge.className = "text-[10px] font-bold bg-yellow-500/15 text-yellow-400 px-2 py-0.5 rounded ml-2";
      badge.textContent = "Unknown";
    } else if (output.success) {
      badge.className = "text-[10px] font-bold bg-green-500/15 text-green-400 border border-green-500/30 px-2 py-0.5 rounded ml-2";
      badge.textContent = "Success";
    } else {
      badge.className = "text-[10px] font-bold bg-red-500/15 text-red-400 border border-red-500/30 px-2 py-0.5 rounded ml-2";
      badge.textContent = "Failed";
    }
    headerLeft.appendChild(badge);
  }

  headerRow.appendChild(headerLeft);

  var headerRight = document.createElement("div");
  headerRight.className = "flex items-center gap-1.5";
  if (latencyMs != null) {
    var latBadge = document.createElement("span");
    var stageBudget = getStageBudget(stage, strategyId);
    var latColor = latencyColorClass(latencyMs, stageBudget);
    latBadge.className = "text-[10px] bg-f-elevated px-2 py-0.5 rounded " + latColor;
    latBadge.textContent = formatLatency(latencyMs);
    headerRight.appendChild(latBadge);
  }
  if (modelId) {
    var modBadge = document.createElement("span");
    modBadge.className = "text-[10px] bg-f-purple/15 text-purple-300 px-2 py-0.5 rounded";
    modBadge.textContent = modelId;
    headerRight.appendChild(modBadge);
  }
  headerRow.appendChild(headerRight);
  card.appendChild(headerRow);

  // Divider
  var divider = document.createElement("div");
  divider.className = "border-t border-f-border";
  card.appendChild(divider);

  // Body content
  var body = document.createElement("div");
  body.className = "text-sm text-gray-300 space-y-1.5 max-w-full";
  body.style.wordBreak = "break-word";
  if (error) {
    var errP = document.createElement("p");
    errP.className = "text-sm text-red-400";
    errP.textContent = error;
    body.appendChild(errP);
  } else if (output) {
    renderStageOutput(body, stage, output);
  }
  card.appendChild(body);

  // Expandable raw JSON
  if (output) {
    var toggleBtn = document.createElement("button");
    toggleBtn.className = "flex items-center gap-1 text-[10px] text-gray-500 hover:text-gray-300 transition-colors";
    var chevron = document.createElement("i");
    chevron.setAttribute("data-lucide", "chevron-down");
    chevron.className = "w-3 h-3";
    toggleBtn.appendChild(chevron);
    var toggleText = document.createElement("span");
    toggleText.textContent = "Raw output";
    toggleBtn.appendChild(toggleText);
    card.appendChild(toggleBtn);

    var jsonPanel = document.createElement("div");
    jsonPanel.id = cardId;
    jsonPanel.className = "json-expand";
    var pre = document.createElement("pre");
    pre.className = "text-[11px] text-gray-500 bg-black/30 rounded-lg p-3 overflow-x-auto";
    pre.textContent = JSON.stringify(output, null, 2);
    jsonPanel.appendChild(pre);
    card.appendChild(jsonPanel);

    toggleBtn.addEventListener("click", function() {
      jsonPanel.classList.toggle("open");
    });
  }

  container.appendChild(card);
  lucide.createIcons({ nodes: [card] });
  scrollToBottom();
}

function addErrorCard(message, container) {
  var card = document.createElement("div");
  card.className = "animate-slide-in bg-red-500/10 border border-red-500/20 rounded-xl p-4";

  var inner = document.createElement("div");
  inner.className = "flex items-center gap-2";
  var icon = document.createElement("i");
  icon.setAttribute("data-lucide", "alert-circle");
  icon.className = "w-4 h-4 text-red-400";
  inner.appendChild(icon);
  var msg = document.createElement("span");
  msg.className = "text-sm text-red-400";
  msg.textContent = message;
  inner.appendChild(msg);

  card.appendChild(inner);
  container.appendChild(card);
  lucide.createIcons({ nodes: [card] });
  scrollToBottom();
}

// ---- Stage-specific rendering (DOM-based) ----

function renderStageOutput(container, stage, output) {
  switch (stage) {
    case "perceive": renderPerceive(container, output); break;
    case "plan":     renderPlan(container, output);     break;
    case "act":      renderAct(container, output);      break;
    case "dynamics": renderDynamics(container, output);  break;
    case "verify":   renderVerify(container, output);   break;
    default:
      var pre = document.createElement("pre");
      pre.className = "text-xs text-gray-400";
      pre.textContent = JSON.stringify(output, null, 2);
      container.appendChild(pre);
  }
}

function renderDynamics(container, o) {
  if (o.skipped) {
    var skipP = document.createElement("p");
    skipP.className = "text-xs text-gray-500 italic";
    skipP.textContent = "Skipped: " + o.skipped;
    container.appendChild(skipP);
    return;
  }
  if (o.evidence) {
    o.evidence.forEach(function(e) {
      var line = document.createElement("p");
      line.className = "text-xs text-gray-400 mb-1";
      var value = e.value == null ? "Unknown / not computed" :
        (e.field === "endpoint_trajectory" ? e.value.length + " points" : String(e.value));
      line.textContent = e.field.replace(/_/g, " ") + ": " + value +
        " [" + e.confidence + "]" + (e.detail ? " — " + e.detail : "");
      container.appendChild(line);
    });
    var assumptions = document.createElement("p");
    assumptions.className = "text-xs text-gray-500 mt-2";
    var modelAssumptions = o.assumptions || {};
    assumptions.textContent = "Model diagnostics only. Initial joint state: " +
      (modelAssumptions.initial_state === "provided" ? "provided" : "model default") +
      ". Action timing: " + (modelAssumptions.action_timing || "unknown") +
      ". End effector: last model body. " +
      (modelAssumptions.model_simplified ? "Geometry was simplified. " : "") +
      "This is not observed robot execution.";
    container.appendChild(assumptions);
    return;
  }
  var items = [
    ["Joint Limits", o.joint_limits_ok != null ? (o.joint_limits_ok ? "OK" : "Exceeded") : "\u2014"],
    ["Self Collision", o.self_collision != null ? (o.self_collision ? "Detected" : "None") : "\u2014"],
    ["Torque Feasible", o.torque_feasible != null ? (o.torque_feasible ? "Yes" : "No") : "\u2014"],
    ["Near Singularity", o.near_singularity != null ? (o.near_singularity ? "Yes" : "No") : "\u2014"],
    ["Steps Analyzed", o.steps_analyzed || "\u2014"],
  ];
  if (o.total_displacement_m != null) {
    items.push(["Displacement", o.total_displacement_m.toFixed(3) + "m"]);
  }
  if (o.smoothness_score != null) {
    items.push(["Smoothness", (o.smoothness_score * 100).toFixed(0) + "%"]);
  }
  var grid = document.createElement("div");
  grid.className = "grid grid-cols-2 gap-x-4 gap-y-1 text-xs";
  items.forEach(function(pair) {
    var label = document.createElement("span");
    label.className = "text-gray-500";
    label.textContent = pair[0];
    grid.appendChild(label);
    var val = document.createElement("span");
    val.className = "text-gray-300 font-mono";
    val.textContent = String(pair[1]);
    grid.appendChild(val);
  });
  container.appendChild(grid);
}

function renderPerceive(container, o) {
  var rendered = false;
  if (o.objects && o.objects.length) {
    rendered = true;
    var section = document.createElement("div");
    section.className = "space-y-1";
    var heading = document.createElement("p");
    heading.className = "text-[11px] text-gray-500 font-medium";
    heading.textContent = "Detected objects";
    section.appendChild(heading);

    var tagWrap = document.createElement("div");
    tagWrap.className = "flex flex-wrap gap-1.5";
    o.objects.forEach(function(obj) {
      var name = typeof obj === "string" ? obj : (obj.label || obj.name || JSON.stringify(obj));
      var conf = (typeof obj === "object" && obj.confidence != null) ? " (" + (obj.confidence * 100).toFixed(0) + "%)" : "";
      var tag = document.createElement("span");
      tag.className = "text-xs bg-f-purple/10 text-purple-300 border border-f-purple/20 px-2 py-0.5 rounded";
      tag.textContent = name + conf;
      tagWrap.appendChild(tag);
    });
    section.appendChild(tagWrap);
    container.appendChild(section);
  }

  var rels = o.spatial_relations || o.relations;
  if (rels) {
    rendered = true;
    var relSection = document.createElement("div");
    relSection.className = "mt-2 space-y-1";
    var relHeading = document.createElement("p");
    relHeading.className = "text-[11px] text-gray-500 font-medium";
    relHeading.textContent = "Spatial relations";
    relSection.appendChild(relHeading);

    if (Array.isArray(rels)) {
      rels.forEach(function(r) {
        var text = typeof r === "string" ? r : JSON.stringify(r);
        var p = document.createElement("p");
        p.className = "text-xs text-gray-400 ml-2";
        p.textContent = "- " + text;
        relSection.appendChild(p);
      });
    } else if (typeof rels === "string") {
      var p2 = document.createElement("p");
      p2.className = "text-xs text-gray-400 ml-2";
      p2.textContent = rels;
      relSection.appendChild(p2);
    }
    container.appendChild(relSection);
  }

  if (o.environment_distribution) {
    rendered = true;
    var envSection = document.createElement("div");
    envSection.className = "mt-2 space-y-1";
    var envHeading = document.createElement("p");
    envHeading.className = "text-[11px] text-gray-500 font-medium";
    envHeading.textContent = "Environment";
    envSection.appendChild(envHeading);
    var envText = document.createElement("p");
    envText.className = "text-xs text-gray-400 ml-2";
    envText.textContent = o.environment_distribution;
    envSection.appendChild(envText);
    container.appendChild(envSection);
  }

  if (o.scene_description || o.description) {
    rendered = true;
    var desc = document.createElement("p");
    desc.className = "text-xs text-gray-400 mt-2 italic";
    desc.textContent = o.scene_description || o.description;
    container.appendChild(desc);
  }

  if (!rendered) {
    var fallback = document.createElement("pre");
    fallback.className = "text-xs text-gray-400";
    fallback.textContent = JSON.stringify(o, null, 2);
    container.appendChild(fallback);
  }
}

function renderPlan(container, o) {
  var rendered = false;

  if (o.strategy) {
    rendered = true;
    var row = document.createElement("div");
    row.className = "flex items-center gap-2";
    var lbl = document.createElement("span");
    lbl.className = "text-[11px] text-gray-500 w-28 shrink-0";
    lbl.textContent = "Strategy";
    row.appendChild(lbl);
    var val = document.createElement("span");
    val.className = "text-xs text-gray-200";
    val.textContent = o.strategy;
    row.appendChild(val);
    container.appendChild(row);
  }

  if (o.target_object) {
    rendered = true;
    var row2 = document.createElement("div");
    row2.className = "flex items-center gap-2";
    var lbl2 = document.createElement("span");
    lbl2.className = "text-[11px] text-gray-500 w-28 shrink-0";
    lbl2.textContent = "Target";
    row2.appendChild(lbl2);
    var val2 = document.createElement("span");
    val2.className = "text-xs text-gray-200";
    val2.textContent = o.target_object;
    row2.appendChild(val2);
    container.appendChild(row2);
  }

  if (o.reasoning) {
    rendered = true;
    var reason = document.createElement("div");
    reason.className = "mt-2 text-xs text-gray-400 italic border-l-2 border-f-border-strong pl-3";
    reason.textContent = o.reasoning;
    container.appendChild(reason);
  }

  // Structured Reasoning: Subtask Reasoning
  if (o.subtask_reasoning) {
    rendered = true;
    var srBlock = document.createElement("div");
    srBlock.className = "mt-2 space-y-0.5";
    var srLabel = document.createElement("p");
    srLabel.className = "text-[11px] text-cyan-400 font-medium";
    srLabel.textContent = "Subtask Reasoning";
    srBlock.appendChild(srLabel);
    var srText = document.createElement("p");
    srText.className = "text-xs text-gray-400 italic border-l-2 border-cyan-500/30 pl-3";
    srText.textContent = o.subtask_reasoning;
    srBlock.appendChild(srText);
    container.appendChild(srBlock);
  }

  // Structured Reasoning: Action Reasoning
  if (o.action_reasoning) {
    rendered = true;
    var arBlock = document.createElement("div");
    arBlock.className = "mt-2 space-y-0.5";
    var arLabel = document.createElement("p");
    arLabel.className = "text-[11px] text-violet-400 font-medium";
    arLabel.textContent = "Action Reasoning";
    arBlock.appendChild(arLabel);
    var arText = document.createElement("p");
    arText.className = "text-xs text-gray-400 italic border-l-2 border-violet-500/30 pl-3";
    arText.textContent = o.action_reasoning;
    arBlock.appendChild(arText);
    container.appendChild(arBlock);
  }

  // Constraints Acknowledged
  if (o.constraints_acknowledged && Array.isArray(o.constraints_acknowledged) && o.constraints_acknowledged.length > 0) {
    rendered = true;
    var caSection = document.createElement("div");
    caSection.className = "mt-2 space-y-1";
    var caHeading = document.createElement("p");
    caHeading.className = "text-[11px] text-red-400 font-medium";
    caHeading.textContent = "Constraints Acknowledged";
    caSection.appendChild(caHeading);
    var caWrap = document.createElement("div");
    caWrap.className = "flex flex-wrap gap-1.5";
    o.constraints_acknowledged.forEach(function(c) {
      var tag = document.createElement("span");
      tag.className = "text-xs bg-red-500/10 text-red-300 border border-red-500/20 px-2 py-0.5 rounded";
      tag.textContent = c;
      caWrap.appendChild(tag);
    });
    caSection.appendChild(caWrap);
    container.appendChild(caSection);
  }

  if (o.steps && Array.isArray(o.steps) && o.steps.length > 0) {
    rendered = true;
    var stepsSection = document.createElement("div");
    stepsSection.className = "mt-2 space-y-1";
    var stepsHeading = document.createElement("p");
    stepsHeading.className = "text-[11px] text-gray-500 font-medium";
    stepsHeading.textContent = "Steps";
    stepsSection.appendChild(stepsHeading);

    o.steps.forEach(function(step, i) {
      var text = typeof step === "string" ? step : (step.description || step.action || JSON.stringify(step));
      var stepRow = document.createElement("div");
      stepRow.className = "flex items-start gap-2 ml-1";
      var num = document.createElement("span");
      num.className = "text-[10px] bg-f-elevated text-gray-300 w-5 h-5 rounded flex items-center justify-center shrink-0 mt-0.5";
      num.textContent = String(i + 1);
      stepRow.appendChild(num);
      var stepText = document.createElement("span");
      stepText.className = "text-xs text-gray-300";
      stepText.textContent = text;
      stepRow.appendChild(stepText);
      stepsSection.appendChild(stepRow);
    });
    container.appendChild(stepsSection);
  }

  // Expected subtasks checklist (multi_stage tasks)
  if (window._selectedExpectedSubtasks && Array.isArray(window._selectedExpectedSubtasks) && window._selectedExpectedSubtasks.length > 0 && o.steps && o.steps.length > 0) {
    rendered = true;
    var esSection = document.createElement("div");
    esSection.className = "mt-2 space-y-1";
    var esHeading = document.createElement("p");
    esHeading.className = "text-[11px] text-blue-400 font-medium";
    esHeading.textContent = "Expected Subtasks";
    esSection.appendChild(esHeading);
    var stepsLower = o.steps.map(function(s) { return (typeof s === "string" ? s : JSON.stringify(s)).toLowerCase(); }).join(" ");
    window._selectedExpectedSubtasks.forEach(function(expected) {
      var found = stepsLower.indexOf(expected.toLowerCase()) >= 0;
      var esRow = document.createElement("div");
      esRow.className = "flex items-center gap-2 ml-1";
      var esIcon = document.createElement("span");
      esIcon.className = found ? "text-[11px] text-green-400" : "text-[11px] text-red-400";
      esIcon.textContent = found ? "\u2713" : "\u2717";
      esRow.appendChild(esIcon);
      var esText = document.createElement("span");
      esText.className = "text-xs " + (found ? "text-gray-300" : "text-gray-500 line-through");
      esText.textContent = expected;
      esRow.appendChild(esText);
      esSection.appendChild(esRow);
    });
    container.appendChild(esSection);
  }

  // Correction indicator (situated_correction tasks)
  if (window._selectedCorrection) {
    rendered = true;
    var corrSection = document.createElement("div");
    corrSection.className = "mt-2 p-2 rounded border border-amber-500/20 bg-amber-500/5";
    var corrHeading = document.createElement("p");
    corrHeading.className = "text-[11px] text-amber-400 font-medium mb-1";
    corrHeading.textContent = "Situated Correction";
    corrSection.appendChild(corrHeading);
    var corrFeedback = document.createElement("p");
    corrFeedback.className = "text-xs text-amber-200 italic";
    corrFeedback.textContent = "\u201c" + (window._selectedCorrection.feedback || "") + "\u201d";
    corrSection.appendChild(corrFeedback);
    if (window._selectedCorrection.timing) {
      var corrTiming = document.createElement("p");
      corrTiming.className = "text-[10px] text-gray-500 mt-0.5";
      corrTiming.textContent = "Timing: " + window._selectedCorrection.timing;
      corrSection.appendChild(corrTiming);
    }
    container.appendChild(corrSection);
  }

  // Active constraints (constrained tasks)
  if (window._selectedConstraints && Array.isArray(window._selectedConstraints) && window._selectedConstraints.length > 0) {
    // Check which constraints were acknowledged
    var acked = (o.constraints_acknowledged || []).map(function(c) { return c.toLowerCase(); }).join(" ");
    var csSection = document.createElement("div");
    csSection.className = "mt-2 space-y-1";
    var csHeading = document.createElement("p");
    csHeading.className = "text-[11px] text-red-400 font-medium";
    csHeading.textContent = "Task Constraints";
    csSection.appendChild(csHeading);
    window._selectedConstraints.forEach(function(constraint) {
      var matched = acked.indexOf(constraint.toLowerCase().substring(0, 15)) >= 0;
      var csRow = document.createElement("div");
      csRow.className = "flex items-center gap-2 ml-1";
      var csIcon = document.createElement("span");
      csIcon.className = matched ? "text-[11px] text-green-400" : "text-[11px] text-yellow-400";
      csIcon.textContent = matched ? "\u2713" : "\u26a0";
      csRow.appendChild(csIcon);
      var csText = document.createElement("span");
      csText.className = "text-xs " + (matched ? "text-gray-300" : "text-yellow-300");
      csText.textContent = constraint;
      csRow.appendChild(csText);
      csSection.appendChild(csRow);
    });
    container.appendChild(csSection);
    rendered = true;
  }

  if (o.task_repertoire && Array.isArray(o.task_repertoire) && o.task_repertoire.length > 0) {
    rendered = true;
    var repSection = document.createElement("div");
    repSection.className = "mt-2 space-y-1";
    var repHeading = document.createElement("p");
    repHeading.className = "text-[11px] text-gray-500 font-medium";
    repHeading.textContent = "Task Repertoire";
    repSection.appendChild(repHeading);
    var repWrap = document.createElement("div");
    repWrap.className = "flex flex-wrap gap-1.5";
    o.task_repertoire.forEach(function(cap) {
      var tag = document.createElement("span");
      tag.className = "text-xs bg-blue-500/10 text-blue-300 border border-blue-500/20 px-2 py-0.5 rounded";
      tag.textContent = cap;
      repWrap.appendChild(tag);
    });
    repSection.appendChild(repWrap);
    container.appendChild(repSection);
  }

  if (o.artifacts && Array.isArray(o.artifacts) && o.artifacts.length > 0) {
    rendered = true;
    var artSection = document.createElement("div");
    artSection.className = "mt-2 space-y-1";
    var artHeading = document.createElement("p");
    artHeading.className = "text-[11px] text-gray-500 font-medium";
    artHeading.textContent = "Artifacts";
    artSection.appendChild(artHeading);
    o.artifacts.forEach(function(a) {
      var p = document.createElement("p");
      p.className = "text-xs text-emerald-300 ml-2";
      p.textContent = "\u2713 " + a;
      artSection.appendChild(p);
    });
    container.appendChild(artSection);
  }

  if (o.degradation_profile && Array.isArray(o.degradation_profile) && o.degradation_profile.length > 0) {
    rendered = true;
    var degSection = document.createElement("div");
    degSection.className = "mt-2 space-y-1";
    var degHeading = document.createElement("p");
    degHeading.className = "text-[11px] text-gray-500 font-medium";
    degHeading.textContent = "Degradation Profile";
    degSection.appendChild(degHeading);
    o.degradation_profile.forEach(function(d) {
      var p = document.createElement("p");
      p.className = "text-xs text-amber-400 ml-2";
      p.textContent = "\u26A0 " + d;
      degSection.appendChild(p);
    });
    container.appendChild(degSection);
  }

  if (!rendered) {
    var fallback = document.createElement("pre");
    fallback.className = "text-xs text-gray-400";
    fallback.textContent = JSON.stringify(o, null, 2);
    container.appendChild(fallback);
  }
}

function renderAct(container, o) {
  var rendered = false;

  if (o.action_type) {
    rendered = true;
    var typeBadge = document.createElement("span");
    typeBadge.className = "text-[10px] bg-emerald-500/15 text-emerald-300 px-2 py-0.5 rounded mb-2 inline-block";
    typeBadge.textContent = o.action_type === "tool_calls" ? "Tool calls" : "Trajectory";
    container.appendChild(typeBadge);
  }

  if (o.action_type === "tool_calls" && o.tool_calls && Array.isArray(o.tool_calls)) {
    rendered = true;
    var heading = document.createElement("p");
    heading.className = "text-[11px] text-gray-500 font-medium mt-1";
    heading.textContent = "Tool invocations";
    container.appendChild(heading);

    o.tool_calls.forEach(function(tc, i) {
      var card = document.createElement("div");
      card.className = "flex items-center gap-2 ml-1 mt-1 bg-black/20 rounded-md px-2 py-1.5";
      var num = document.createElement("span");
      num.className = "text-[10px] bg-f-elevated text-gray-300 w-5 h-5 rounded flex items-center justify-center shrink-0";
      num.textContent = String(i + 1);
      card.appendChild(num);
      var toolName = document.createElement("span");
      toolName.className = "text-xs text-emerald-300 font-mono";
      toolName.textContent = tc.tool || "unknown";
      card.appendChild(toolName);
      if (tc.args) {
        var argsSpan = document.createElement("span");
        argsSpan.className = "text-[10px] text-gray-500 font-mono";
        argsSpan.textContent = JSON.stringify(tc.args);
        card.appendChild(argsSpan);
      }
      container.appendChild(card);
    });
  }

  var actions = o.actions || o.action_steps;
  if (o.action_type !== "tool_calls" && actions && Array.isArray(actions) && actions.length > 0) {
    rendered = true;
    var heading2 = document.createElement("p");
    heading2.className = "text-[11px] text-gray-500 font-medium";
    heading2.textContent = "Trajectory (" + actions.length + " step" + (actions.length > 1 ? "s" : "") + ")";
    container.appendChild(heading2);

    // DOF labels for 6/7-DOF end-effector deltas
    var dofLabels = ["dx", "dy", "dz", "rx", "ry", "rz", "grip"];

    var table = document.createElement("div");
    table.className = "mt-1 font-mono text-[10px] leading-relaxed overflow-x-auto";

    // Header row
    if (Array.isArray(actions[0])) {
      var hdr = document.createElement("div");
      hdr.className = "flex gap-1 text-gray-600 mb-0.5";
      var stepHdr = document.createElement("span");
      stepHdr.className = "w-6 text-right shrink-0";
      stepHdr.textContent = "#";
      hdr.appendChild(stepHdr);
      var numDof = actions[0].length;
      for (var d = 0; d < numDof; d++) {
        var lbl = document.createElement("span");
        lbl.className = "w-16 text-right shrink-0";
        lbl.textContent = d < dofLabels.length ? dofLabels[d] : "d" + d;
        hdr.appendChild(lbl);
      }
      table.appendChild(hdr);
    }

    actions.forEach(function(a, i) {
      var row = document.createElement("div");
      row.className = "flex gap-1 text-gray-300";
      var stepNum = document.createElement("span");
      stepNum.className = "w-6 text-right text-gray-600 shrink-0";
      stepNum.textContent = String(i + 1);
      row.appendChild(stepNum);

      if (Array.isArray(a)) {
        a.forEach(function(v) {
          var cell = document.createElement("span");
          cell.className = "w-16 text-right shrink-0" + (v < 0 ? " text-red-400" : "");
          cell.textContent = typeof v === "number" ? v.toFixed(4) : String(v);
          row.appendChild(cell);
        });
      } else {
        var text = document.createElement("span");
        text.className = "text-gray-400";
        text.textContent = typeof a === "string" ? a : JSON.stringify(a);
        row.appendChild(text);
      }
      table.appendChild(row);
    });
    container.appendChild(table);
  }

  // Sim status badges
  if (o.sim_done != null || o.sim_success != null) {
    rendered = true;
    var simRow = document.createElement("div");
    simRow.className = "mt-2 flex items-center gap-2";
    var isMock = o.sim_is_mock === true;
    if (o.sim_success != null && !isMock) {
      var successBadge = document.createElement("span");
      successBadge.className = "text-[10px] px-2 py-0.5 rounded " +
        (o.sim_success ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/15 text-red-300");
      successBadge.textContent = o.sim_success ? "Sim: success" : "Sim: not achieved";
      simRow.appendChild(successBadge);
    }
    if (isMock) {
      var mockBadge = document.createElement("span");
      mockBadge.className = "text-[10px] bg-gray-500/15 text-gray-400 px-2 py-0.5 rounded";
      mockBadge.textContent = "Sim: mock (no real physics)";
      simRow.appendChild(mockBadge);
    }
    if (o.sim_done && !isMock) {
      var doneBadge = document.createElement("span");
      doneBadge.className = "text-[10px] bg-blue-500/15 text-blue-300 px-2 py-0.5 rounded";
      doneBadge.textContent = "Episode done";
      simRow.appendChild(doneBadge);
    }
    if (o.actions_executed != null) {
      var countBadge = document.createElement("span");
      countBadge.className = "text-[10px] text-gray-500";
      countBadge.textContent = o.actions_executed + " step" + (o.actions_executed !== 1 ? "s" : "") + " executed";
      simRow.appendChild(countBadge);
    }
    container.appendChild(simRow);
  } else if (o.actions_executed != null) {
    rendered = true;
    var traj = document.createElement("p");
    traj.className = "text-xs text-gray-500 mt-1";
    traj.textContent = o.actions_executed + " step" + (o.actions_executed !== 1 ? "s" : "") + " executed";
    container.appendChild(traj);
  }

  // Dynamics analysis card
  if (o.dynamics_analysis) {
    rendered = true;
    var dyn = o.dynamics_analysis.summary || o.dynamics_analysis;
    var dynCard = document.createElement("div");
    dynCard.className = "mt-3 bg-blue-500/5 border border-blue-500/20 rounded-lg p-3 space-y-2";

    var dynTitle = document.createElement("div");
    dynTitle.className = "flex items-center gap-2 mb-2";
    var dynBadge = document.createElement("span");
    dynBadge.className = "text-[10px] bg-blue-500/15 text-blue-300 px-2 py-0.5 rounded";
    dynBadge.textContent = "Dynamics";
    dynTitle.appendChild(dynBadge);
    var dynSubtitle = document.createElement("span");
    dynSubtitle.className = "text-[10px] text-gray-500";
    dynSubtitle.textContent = "MuJoCo model diagnostics — assumptions and missing checks below";
    dynTitle.appendChild(dynSubtitle);
    dynCard.appendChild(dynTitle);

    var dynGrid = document.createElement("div");
    dynGrid.className = "grid grid-cols-2 gap-x-4 gap-y-1 text-xs";

    var ep = dyn.final_endpoint || [0, 0, 0];
    var dynItems = [
      ["Endpoint", "[" + ep.map(function(v) { return v.toFixed(3); }).join(", ") + "]m"],
      ["Displacement", (dyn.total_displacement_m || 0).toFixed(3) + "m"],
      ["Joint limits", dyn.joint_limits_ok == null ? "Unknown" : (dyn.joint_limits_ok ? "within bounds" : "EXCEEDED")],
      ["Self-collision", dyn.self_collision == null ? "Unknown" : (dyn.self_collision ? "DETECTED" : "not detected")],
      ["Smoothness", (dyn.smoothness_score || 0).toFixed(2)],
      ["Max joint delta / step", dyn.max_joint_delta_per_step == null ? "Unknown" : dyn.max_joint_delta_per_step.toFixed(3)],
      ["Torque feasible", dyn.torque_feasible == null ? "Not computed" : (dyn.torque_feasible ? "yes" : "VIOLATED")],
      ["Gravity hold", dyn.gravity_feasible == null ? "Not computed" : (dyn.gravity_feasible ? "feasible" : "INFEASIBLE")],
      ["Manipulability", (dyn.manipulability || 0).toFixed(4) + (dyn.near_singularity ? " (SINGULARITY)" : "")],
    ];
    dynItems.forEach(function(item) {
      var label = document.createElement("span");
      label.className = "text-gray-500";
      label.textContent = item[0];
      dynGrid.appendChild(label);
      var value = document.createElement("span");
      var isWarning = (item[0] === "Joint limits" && dyn.joint_limits_ok === false) ||
                      (item[0] === "Self-collision" && dyn.self_collision) ||
                      (item[0] === "Torque feasible" && dyn.torque_feasible === false) ||
                      (item[0] === "Gravity hold" && dyn.gravity_feasible === false) ||
                      (item[0] === "Manipulability" && dyn.near_singularity);
      value.className = isWarning ? "text-red-400 font-medium" : "text-gray-300";
      value.textContent = item[1];
      dynGrid.appendChild(value);
    });
    dynCard.appendChild(dynGrid);
    (o.dynamics_analysis.evidence || []).forEach(function(e) {
      var line = document.createElement("p");
      line.className = "text-xs text-gray-400";
      line.textContent = e.field + ": " + e.confidence + (e.detail ? " — " + e.detail : "");
      dynCard.appendChild(line);
    });


    // Trajectory sample
    var dynTraj = dyn.endpoint_trajectory || [];
    if (dynTraj.length > 0) {
      var trajDiv = document.createElement("div");
      trajDiv.className = "mt-2 font-mono text-[10px] text-gray-400";
      var trajTitle2 = document.createElement("p");
      trajTitle2.className = "text-gray-500 mb-0.5";
      trajTitle2.textContent = "End-effector trajectory (" + dynTraj.length + " points):";
      trajDiv.appendChild(trajTitle2);
      var showCount = Math.min(dynTraj.length, 5);
      for (var ti = 0; ti < showCount; ti++) {
        var pt = dynTraj[ti];
        var ptEl = document.createElement("p");
        ptEl.textContent = "  " + (ti + 1) + ": [" + pt.map(function(v) { return v.toFixed(3); }).join(", ") + "]m";
        trajDiv.appendChild(ptEl);
      }
      if (dynTraj.length > 5) {
        var more = document.createElement("p");
        more.className = "text-gray-600";
        more.textContent = "  ... (" + dynTraj.length + " total)";
        trajDiv.appendChild(more);
      }
      dynCard.appendChild(trajDiv);
    }

    container.appendChild(dynCard);
  }

  // Dynamics skipped warning
  if (o.dynamics_skipped) {
    rendered = true;
    var dynWarn = document.createElement("div");
    dynWarn.className = "mt-2 flex items-center gap-2 text-[10px] text-yellow-400/80";
    var warnIcon = document.createElement("i");
    warnIcon.setAttribute("data-lucide", "alert-triangle");
    warnIcon.className = "w-3 h-3";
    dynWarn.appendChild(warnIcon);
    var warnText = document.createElement("span");
    warnText.textContent = "Dynamics skipped: " + o.dynamics_skipped;
    dynWarn.appendChild(warnText);
    container.appendChild(dynWarn);
  }

  if (!rendered) {
    var fallback = document.createElement("pre");
    fallback.className = "text-xs text-gray-400";
    fallback.textContent = JSON.stringify(o, null, 2);
    container.appendChild(fallback);
  }
}

function renderVerify(container, o) {
  var configured = [];
  if (o.evaluator_result) configured.push({label: "Task evaluator", result: o.evaluator_result, version: o.evaluator_version});
  (o.check_results || []).forEach(function(check) {
    configured.push({label: check.endpoint + " · " + check.role + (check.required ? " · required" : " · optional") + " · " + check.execution, result: check.result, version: check.evaluator_version});
  });
  configured.forEach(function(item) {
    var panel = document.createElement("details");
    panel.className = "my-3 p-3 border border-f-border rounded-lg";
    var heading = document.createElement("summary");
    heading.className = "text-xs cursor-pointer";
    heading.textContent = item.label + ": " + item.result.verdict + " · " + item.result.evidence_quality;
    panel.appendChild(heading);
    var reason = document.createElement("p");
    reason.className = "text-xs text-gray-400 mt-2";
    reason.textContent = item.result.reasoning;
    panel.appendChild(reason);
    (item.result.measurements || []).forEach(function(measurement) {
      var line = document.createElement("p");
      line.className = "text-xs mt-1";
      line.textContent = measurement.name + ": " + (measurement.value == null ? "Unavailable" : measurement.value + " " + measurement.unit) + " · " + measurement.quality;
      panel.appendChild(line);
    });
    var provenance = document.createElement("p");
    provenance.className = "text-xs text-gray-500 mt-2 break-all";
    provenance.textContent = "Evaluator version: " + item.version + " · Evidence: " + (item.result.evidence_refs || []).join(", ");
    panel.appendChild(provenance);
    container.appendChild(panel);
  });
  if (o.dynamics_analysis) {
    var evidencePanel = document.createElement("details");
    evidencePanel.className = "my-3 p-3 border border-blue-500/20 rounded-lg";
    var evidenceHeading = document.createElement("summary");
    evidenceHeading.className = "text-xs text-blue-300 cursor-pointer";
    evidenceHeading.textContent = "Computed evidence and missing checks";
    evidencePanel.appendChild(evidenceHeading);
    renderDynamics(evidencePanel, o.dynamics_analysis);
    container.appendChild(evidencePanel);
  }

  var rendered = false;

  if (o.confidence != null && !o.evaluator_result && o.verdict_valid !== false) {
    rendered = true;
    var pct = (o.confidence * 100).toFixed(1);
    var color = o.confidence >= 0.7 ? "green" : (o.confidence >= 0.4 ? "yellow" : "red");

    var row = document.createElement("div");
    row.className = "flex items-center gap-3";
    var lbl = document.createElement("span");
    lbl.className = "text-[11px] text-gray-500";
    lbl.textContent = "Confidence";
    row.appendChild(lbl);

    var barOuter = document.createElement("div");
    barOuter.className = "flex-1 h-1.5 bg-f-elevated rounded-full overflow-hidden max-w-[200px]";
    var barInner = document.createElement("div");
    barInner.className = "h-full bg-" + color + "-500 rounded-full transition-all duration-500";
    barInner.style.width = pct + "%";
    barOuter.appendChild(barInner);
    row.appendChild(barOuter);

    var pctSpan = document.createElement("span");
    pctSpan.className = "text-xs font-mono text-" + color + "-400";
    pctSpan.textContent = pct + "%";
    row.appendChild(pctSpan);
    container.appendChild(row);
  }

  if (o.reasoning || o.explanation) {
    rendered = true;
    var rawReasoning = o.reasoning || o.explanation;
    // Try to parse narrative sections from agent loop reasoning
    var parsed = parseVerifyNarrative(rawReasoning);
    if (parsed) {
      var narWrap = document.createElement("div");
      narWrap.className = "mt-2 space-y-2";

      // Top-level sections (Verdict, VLA Output Analysis) — full width
      (parsed.top || []).forEach(function(sec) {
        narWrap.appendChild(renderNarrativeSection(sec));
      });

      // Evidence sections — nested under a collapsible "Evidence" group
      if (parsed.evidence && parsed.evidence.length > 0) {
        var evidenceWrap = document.createElement("div");
        evidenceWrap.className = "mt-1";
        var evidenceToggle = document.createElement("button");
        evidenceToggle.type = "button";
        evidenceToggle.className = "flex items-center gap-1.5 group cursor-pointer bg-transparent border-0 p-0 mb-0";
        var evidenceArrow = document.createElement("span");
        evidenceArrow.className = "text-purple-400 text-[11px] transition-transform duration-200";
        evidenceArrow.style.transform = "rotate(90deg)";
        evidenceArrow.textContent = "\u203a";
        evidenceToggle.appendChild(evidenceArrow);
        var evidenceLabel = document.createElement("span");
        evidenceLabel.className = "text-[10px] font-semibold text-purple-400 group-hover:text-purple-300 transition-colors uppercase tracking-wider";
        evidenceLabel.textContent = "Reasoning Trajectory";
        evidenceToggle.appendChild(evidenceLabel);
        var evidenceCount = document.createElement("span");
        evidenceCount.className = "text-[9px] text-gray-600";
        evidenceCount.textContent = parsed.evidence.length + " sources";
        evidenceToggle.appendChild(evidenceCount);
        evidenceWrap.appendChild(evidenceToggle);

        var evidenceContent = document.createElement("div");
        evidenceContent.className = "mt-1.5 ml-3 space-y-1.5";
        parsed.evidence.forEach(function(sec) {
          evidenceContent.appendChild(renderNarrativeSection(sec));
        });
        evidenceWrap.appendChild(evidenceContent);

        evidenceToggle.addEventListener("click", function() {
          var open = !evidenceContent.classList.contains("hidden");
          if (open) {
            evidenceContent.classList.add("hidden");
            evidenceArrow.style.transform = "rotate(0deg)";
          } else {
            evidenceContent.classList.remove("hidden");
            evidenceArrow.style.transform = "rotate(90deg)";
          }
        });

        narWrap.appendChild(evidenceWrap);
      }

      container.appendChild(narWrap);
    } else {
      var reason = document.createElement("p");
      reason.className = "text-xs text-gray-400 italic border-l-2 border-f-border-strong pl-3 mt-2";
      reason.textContent = rawReasoning;
      container.appendChild(reason);
    }
  }

  // Resolution path (agent loop mode)
  if (o.resolution_path) {
    rendered = true;
    var resPath = document.createElement("div");
    resPath.className = "mt-2 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2";
    var resLabel = document.createElement("span");
    resLabel.className = "text-[10px] font-semibold text-amber-400";
    resLabel.textContent = "Resolution Path";
    resPath.appendChild(resLabel);
    var resText = document.createElement("p");
    resText.className = "text-xs text-amber-300/80 mt-1";
    resText.textContent = o.resolution_path;
    resPath.appendChild(resText);
    container.appendChild(resPath);
  }

  // Verify turns indicator (agent loop mode)
  if (o.verify_turns && o.verify_turns > 1) {
    var turnsBadge = document.createElement("span");
    turnsBadge.className = "text-[10px] bg-purple-500/15 text-purple-300 px-1.5 py-0.5 rounded mt-1 inline-block";
    turnsBadge.textContent = o.verify_turns + " verify turns";
    container.appendChild(turnsBadge);
  }

  // Stage checks — only show for precompute mode (not agent_loop where they're meaningless)
  // Detect agent_loop: verify_turns > 1 or resolution_path present means agent loop was used
  var isAgentLoop = (o.verify_turns && o.verify_turns > 1) || o.resolution_path;
  if (!isAgentLoop && o.stage_checks && Array.isArray(o.stage_checks) && o.stage_checks.length > 0) {
    rendered = true;
    var checksSection = document.createElement("div");
    checksSection.className = "mt-3 space-y-2";
    var checksHeading = document.createElement("p");
    checksHeading.className = "text-[11px] text-gray-500 font-medium";
    checksHeading.textContent = "Stage checks";
    checksSection.appendChild(checksHeading);

    o.stage_checks.forEach(function(sc) {
      var card = document.createElement("div");
      card.className = "bg-black/20 rounded-md px-3 py-2 space-y-1";

      var header = document.createElement("div");
      header.className = "flex items-center gap-2";
      var badge = document.createElement("span");
      badge.className = sc.passed
        ? "text-[10px] bg-emerald-500/15 text-emerald-300 px-1.5 py-0.5 rounded"
        : "text-[10px] bg-red-500/15 text-red-300 px-1.5 py-0.5 rounded";
      badge.textContent = sc.passed ? "PASS" : "FAIL";
      header.appendChild(badge);
      var stageName = document.createElement("span");
      stageName.className = "text-xs text-gray-200 font-medium";
      stageName.textContent = sc.stage;
      header.appendChild(stageName);
      if (sc.confidence != null && sc.confidence > 0) {
        var confSpan = document.createElement("span");
        confSpan.className = "text-[10px] text-gray-500 ml-auto";
        confSpan.textContent = (sc.confidence * 100).toFixed(0) + "% confidence";
        header.appendChild(confSpan);
      }
      card.appendChild(header);

      if (sc.reasoning) {
        var scReason = document.createElement("p");
        scReason.className = "text-xs text-gray-400 ml-1";
        scReason.textContent = sc.reasoning;
        card.appendChild(scReason);
      }
      checksSection.appendChild(card);
    });
    container.appendChild(checksSection);
  }

  if (o.ground_truth) {
    rendered = true;
    var gt = o.ground_truth;
    var gtSection = document.createElement("div");
    gtSection.className = "mt-3 space-y-1";
    var gtHeading = document.createElement("p");
    gtHeading.className = "text-[11px] text-gray-500 font-medium";
    gtHeading.textContent = "Ground truth QA";
    gtSection.appendChild(gtHeading);

    var gtCard = document.createElement("div");
    gtCard.className = "bg-black/20 rounded-md px-3 py-2 space-y-1";

    if (gt.question) {
      var q = document.createElement("p");
      q.className = "text-xs text-gray-200";
      q.textContent = gt.question;
      gtCard.appendChild(q);
    }

    var answerRow = document.createElement("div");
    answerRow.className = "flex items-center gap-2 mt-1";
    var correctBadge = document.createElement("span");
    correctBadge.className = gt.correct
      ? "text-[10px] bg-emerald-500/15 text-emerald-300 px-1.5 py-0.5 rounded"
      : "text-[10px] bg-red-500/15 text-red-300 px-1.5 py-0.5 rounded";
    correctBadge.textContent = gt.correct ? "CORRECT" : "INCORRECT";
    answerRow.appendChild(correctBadge);
    var ansText = document.createElement("span");
    ansText.className = "text-xs text-gray-300";
    ansText.textContent = "Pipeline: " + (gt.pipeline_answer || "N/A");
    answerRow.appendChild(ansText);
    gtCard.appendChild(answerRow);

    if (gt.correct_answer && !gt.correct) {
      var expected = document.createElement("p");
      expected.className = "text-[10px] text-gray-500 ml-1";
      expected.textContent = "Expected: " + gt.correct_answer;
      gtCard.appendChild(expected);
    }

    gtSection.appendChild(gtCard);
    container.appendChild(gtSection);
  }

  if (o.action_plausibility) {
    rendered = true;
    var ap = o.action_plausibility;
    var apSection = document.createElement("div");
    apSection.className = "mt-3 space-y-2";
    var apHeading = document.createElement("div");
    apHeading.className = "flex items-center gap-1.5";
    var apTitle = document.createElement("p");
    apTitle.className = "text-[11px] text-gray-500 font-medium";
    apTitle.textContent = "Action Plausibility";
    apHeading.appendChild(apTitle);
    // Show evidence quality badge
    var evidenceQuality = ap.evidence_quality || "";
    var hasDynamics = evidenceQuality === "hard" || evidenceQuality === "estimated"
      || ap.bounds_check != null || ap.dynamics_consistency != null
      || ap.safety_assessment != null || ap.workspace_reachability != null;
    var apProvider = document.createElement("span");
    if (evidenceQuality === "hard") {
      apProvider.className = "text-[9px] bg-blue-500/15 text-blue-400 px-1.5 py-0.5 rounded";
      apProvider.textContent = "Hard Evidence";
    } else if (evidenceQuality === "estimated") {
      apProvider.className = "text-[9px] bg-yellow-500/15 text-yellow-400 px-1.5 py-0.5 rounded";
      apProvider.textContent = "Estimated";
    } else if (evidenceQuality === "mock" || evidenceQuality === "partial") {
      apProvider.className = "text-[9px] bg-yellow-500/15 text-yellow-400 px-1.5 py-0.5 rounded";
      apProvider.textContent = evidenceQuality === "mock" ? "Synthetic mock scores" : "Partial model evidence";
    } else if (hasDynamics) {
      apProvider.className = "text-[9px] bg-blue-500/15 text-blue-400 px-1.5 py-0.5 rounded";
      apProvider.textContent = "MuJoCo Dynamics";
    } else {
      apProvider.className = "text-[9px] bg-gray-500/15 text-gray-400 px-1.5 py-0.5 rounded";
      apProvider.textContent = "Perception Only";
    }
    apHeading.appendChild(apProvider);
    apSection.appendChild(apHeading);

    // Warning banner when dynamics is not available
    if (!hasDynamics) {
      var noPhysWarn = document.createElement("div");
      noPhysWarn.className = "mt-1 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded-md";
      var noPhysText = document.createElement("p");
      noPhysText.className = "text-[11px] text-red-400/90";
      noPhysText.textContent = "\u26A0 No dynamics data — scores are perception-only estimates. Physics-dependent fields show as 'Not assessed'. Enable MuJoCo dynamics for physics-grounded assessment.";
      noPhysWarn.appendChild(noPhysText);
      apSection.appendChild(noPhysWarn);
    }

    var apCard = document.createElement("div");
    apCard.className = "bg-black/20 rounded-md px-3 py-2 space-y-2";

    // All scores rendered as bars (0-1 float scores)
    var scoreChecks = [
      { key: "bounds_check", label: "Bounds Check", tooltip: "Are all joint angles within mechanical limits? Does MuJoCo dynamics report any joint limit violations or self-collisions?" },
      { key: "smoothness", label: "Smoothness", tooltip: "Is the trajectory continuous without sudden jumps? Measures jerk and acceleration consistency between consecutive action steps." },
      { key: "gripper_consistency", label: "Gripper Consistency", tooltip: "Does the gripper open/close pattern match the task type? E.g., pick tasks should have close→open, place tasks open→close." },
      { key: "plan_alignment", label: "Plan Alignment", tooltip: "How well does the VLA trajectory follow the expected manipulation plan? Compares actual motion against the ideal step sequence." },
      { key: "workspace_reachability", label: "Workspace Reachability", tooltip: "Does the trajectory stay within the robot's reachable workspace? Based on manipulability index and singular value analysis." },
      { key: "dynamics_consistency", label: "Dynamics Consistency", tooltip: "Does the LLM's assessment agree with MuJoCo physics data? Lower score means the verifier and physics engine disagree." },
      { key: "task_completion_plausibility", label: "Task Completion", tooltip: "Does the endpoint displacement match the task intent? E.g., a pick-and-place should show object moved from source to target." },
      { key: "safety_assessment", label: "Safety", tooltip: "Composite safety score from torque feasibility, collision avoidance, singularity distance, and gravity compensation." },
    ];
    scoreChecks.forEach(function(sc) {
      var val = ap[sc.key];
      var row = document.createElement("div");
      row.className = "flex items-center gap-2";
      var lbl = document.createElement("span");
      lbl.className = "text-[10px] text-gray-400 w-[130px] shrink-0";
      lbl.textContent = sc.label;
      row.appendChild(lbl);

      if (val == null) {
        // Null field — show "Not assessed"
        var naSpan = document.createElement("span");
        naSpan.className = "text-[10px] text-gray-600 italic";
        naSpan.textContent = "Not assessed";
        row.appendChild(naSpan);
      } else {
        // Coerce booleans to float for backward compat
        if (val === true) val = 1.0;
        if (val === false) val = 0.0;
        var pct = (val * 100).toFixed(0);
        var color = val >= 0.7 ? "emerald" : (val >= 0.4 ? "yellow" : "red");

        var barOuter = document.createElement("div");
        barOuter.className = "flex-1 h-1.5 bg-f-elevated rounded-full overflow-hidden max-w-[140px]";
        var barInner = document.createElement("div");
        barInner.className = "h-full bg-" + color + "-500 rounded-full transition-all duration-500";
        barInner.style.width = pct + "%";
        barOuter.appendChild(barInner);
        row.appendChild(barOuter);
        var pctSpan = document.createElement("span");
        pctSpan.className = "text-[10px] font-mono text-" + color + "-400 w-[36px] text-right";
        pctSpan.textContent = pct + "%";
        row.appendChild(pctSpan);
      }
      apCard.appendChild(row);
    });

    // Plausibility reasoning
    if (ap.reasoning) {
      var apReason = document.createElement("p");
      apReason.className = "text-xs text-gray-400 italic border-l-2 border-f-border-strong pl-3 mt-2";
      apReason.textContent = ap.reasoning;
      apCard.appendChild(apReason);
    }

    apSection.appendChild(apCard);

    // Disclaimer banner
    var disclaimer = document.createElement("div");
    disclaimer.className = "mt-2 px-3 py-2 bg-amber-500/10 border border-amber-500/20 rounded-md";
    var disclaimerText = document.createElement("p");
    disclaimerText.className = "text-[11px] text-amber-400/80";
    disclaimerText.textContent = "\u26A0 Action plausibility only \u2014 true success requires a simulator or post-execution observation.";
    disclaimer.appendChild(disclaimerText);
    apSection.appendChild(disclaimer);

    // Collapsed glossary
    var glossaryWrap = document.createElement("div");
    glossaryWrap.className = "mt-2";
    var glossaryToggle = document.createElement("button");
    glossaryToggle.type = "button";
    glossaryToggle.className = "flex items-center gap-1.5 group cursor-pointer bg-transparent border-0 p-0";
    var glossaryArrow = document.createElement("span");
    glossaryArrow.className = "text-gray-500 text-[11px] transition-transform duration-200";
    glossaryArrow.textContent = "\u203a";
    glossaryToggle.appendChild(glossaryArrow);
    var glossaryLabel = document.createElement("span");
    glossaryLabel.className = "text-[10px] text-gray-500 group-hover:text-gray-400 transition-colors";
    glossaryLabel.textContent = "Metric Glossary";
    glossaryToggle.appendChild(glossaryLabel);
    glossaryWrap.appendChild(glossaryToggle);

    var glossaryContent = document.createElement("div");
    glossaryContent.className = "hidden mt-1.5 ml-3 space-y-1";
    scoreChecks.forEach(function(sc) {
      if (ap[sc.key] == null) return;
      var gRow = document.createElement("div");
      var gLabel = document.createElement("span");
      gLabel.className = "text-[10px] text-gray-400 font-medium";
      gLabel.textContent = sc.label + ": ";
      gRow.appendChild(gLabel);
      var gDesc = document.createElement("span");
      gDesc.className = "text-[10px] text-gray-500";
      gDesc.textContent = sc.tooltip;
      gRow.appendChild(gDesc);
      glossaryContent.appendChild(gRow);
    });
    glossaryWrap.appendChild(glossaryContent);

    glossaryToggle.addEventListener("click", function() {
      var open = !glossaryContent.classList.contains("hidden");
      if (open) {
        glossaryContent.classList.add("hidden");
        glossaryArrow.style.transform = "rotate(0deg)";
      } else {
        glossaryContent.classList.remove("hidden");
        glossaryArrow.style.transform = "rotate(90deg)";
      }
    });

    apSection.appendChild(glossaryWrap);

    container.appendChild(apSection);
  }

  if (!rendered) {
    var fallback = document.createElement("pre");
    fallback.className = "text-xs text-gray-400";
    fallback.textContent = JSON.stringify(o, null, 2);
    container.appendChild(fallback);
  }
}
