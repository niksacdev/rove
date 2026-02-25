"use strict";
const API_BASE = window.location.origin;

// ---- State ----
let selectedFile = null;
let isRunning = false;
let currentEventSource = null;
let strategies = [];
let selectedStrategyIds = new Set();
let activeTabId = null;
let tabData = {};
let summaryResults = {}; // { [sid]: { status, success, latency_ms, currentStage, stageStatuses: {perceive,plan,act,verify} } }
let summaryEl = null; // DOM element for summary tab content
let currentView = "home"; // "home" | "strategies" | "models" | "settings" | "evaluation"
let runHistory = []; // per-eval data entries
let activeHistoryIndex = -1;
let modelsData = null;
let configData = null; // full rove.yaml as JSON
let sampleIndex = 0;
let historyFilterText = "";
let historyFilterStrategy = "";

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

// ---- Stage metadata ----
const STAGES = {
  perceive: { label: "Scene Analysis",     icon: "eye"          },
  plan:     { label: "Task Planning",       icon: "brain"        },
  act:      { label: "Action Execution",    icon: "bot"          },
  verify:   { label: "Verification",        icon: "check-circle" },
};

const STAGE_COLORS = {
  perceive: { bg: "bg-purple-500/15", text: "text-purple-300" },
  plan:     { bg: "bg-purple-500/15", text: "text-purple-300" },
  act:      { bg: "bg-emerald-500/15", text: "text-emerald-300" },
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
  restoreHistory();
});

// ---- Load config from API ----
async function loadConfig() {
  try {
    var res = await fetch(API_BASE + "/api/config");
    if (!res.ok) throw new Error("HTTP " + res.status);
    configData = await res.json();
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
      activeHistoryIndex = -1;
      if (text === "evaluate") {
        switchView("home");
      } else if (text === "strategies") {
        switchView("strategies");
      } else if (text === "endpoints") {
        switchView("models");
      }
      updateTopNav(text);
    });
  });
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
  tabBar.classList.add("hidden");

  // Clear non-persistent content (eval-content AND tab-content elements)
  var dynamicEls = chatArea.querySelectorAll(".eval-content");
  dynamicEls.forEach(function(el) { el.remove(); });
  var tabEls = chatArea.querySelectorAll("[id^='tab-content-']");
  tabEls.forEach(function(el) { el.remove(); });

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
      // Handled by showHistoryEntry
      break;
  }
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

  // Group by category, then by scene_type
  var categories = {};
  examples.forEach(function(ex) {
    var cat = ex.category || "perceive-plan";
    if (!categories[cat]) categories[cat] = {};
    var key = ex.scene_type || "other";
    if (!categories[cat][key]) categories[cat][key] = [];
    categories[cat][key].push(ex);
  });

  var catMeta = {
    "perceive-plan": { label: "Perceive & Plan", desc: "Robo2VLM-1 \u2014 scene understanding and planning tasks (no VLA)", icon: "eye" },
    "action": { label: "Action (VLA)", desc: "LIBERO-10 \u2014 manipulation tasks with proprioception and ground truth actions", icon: "move-3d" }
  };
  var catKeys = ["perceive-plan", "action"];

  // Layout: left menu + right content
  var layout = document.createElement("div");
  layout.className = "flex gap-6 max-w-4xl mx-auto";

  // Left menu
  var menu = document.createElement("nav");
  menu.className = "shrink-0 w-48 pt-1";

  var menuTitle = document.createElement("h2");
  menuTitle.className = "text-lg font-semibold text-gray-100 mb-1";
  menuTitle.textContent = "Examples";
  menu.appendChild(menuTitle);

  var menuSubtitle = document.createElement("p");
  menuSubtitle.className = "text-[10px] text-gray-500 mb-4";
  menuSubtitle.textContent = "Click to auto-fill evaluation";
  menu.appendChild(menuSubtitle);

  // Right content area
  var content = document.createElement("div");
  content.className = "flex-1 min-w-0";

  var panels = {};
  var menuButtons = {};

  catKeys.forEach(function(cat) {
    var groups = categories[cat];
    if (!groups) return;
    var cm = catMeta[cat] || { label: cat, desc: "", icon: "box" };
    var totalCount = Object.values(groups).reduce(function(s, arr) { return s + arr.length; }, 0);

    // Menu button (safe DOM construction)
    var btn = document.createElement("button");
    btn.className = "flex items-center gap-2.5 w-full text-left px-3 py-2.5 rounded-lg text-sm transition-colors mb-1";
    var btnIcon = document.createElement("i");
    btnIcon.setAttribute("data-lucide", cm.icon);
    btnIcon.className = "w-4 h-4 shrink-0";
    btn.appendChild(btnIcon);
    var btnLabel = document.createElement("span");
    btnLabel.className = "flex-1 truncate";
    btnLabel.textContent = cm.label;
    btn.appendChild(btnLabel);
    var btnCount = document.createElement("span");
    btnCount.className = "text-[10px] text-gray-500";
    btnCount.textContent = String(totalCount);
    btn.appendChild(btnCount);
    menuButtons[cat] = btn;
    menu.appendChild(btn);

    // Content panel (hidden by default)
    var panel = document.createElement("div");
    panel.style.display = "none";

    var panelHeader = document.createElement("div");
    panelHeader.className = "mb-4";
    var panelTitle = document.createElement("h3");
    panelTitle.className = "text-sm font-semibold text-gray-200";
    panelTitle.textContent = cm.label;
    var panelDesc = document.createElement("p");
    panelDesc.className = "text-[10px] text-gray-500 mt-0.5";
    panelDesc.textContent = cm.desc;
    panelHeader.appendChild(panelTitle);
    panelHeader.appendChild(panelDesc);
    panel.appendChild(panelHeader);

    // Scene type groups
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

        var metaParts = [ex.source ? ex.source.dataset : ""];
        if (ex.robot) metaParts.push(ex.robot.toUpperCase());
        if (ex.proprioception) metaParts.push(ex.state_dim + "-DOF state");
        var meta = document.createElement("p");
        meta.className = "text-[10px] text-gray-500 mt-0.5";
        meta.textContent = metaParts.filter(Boolean).join(" \u00b7 ");

        info.appendChild(taskText);
        info.appendChild(meta);
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

    panels[cat] = panel;
    content.appendChild(panel);
  });

  // Activate a category — show its panel, highlight its menu button
  function activateCategory(cat) {
    catKeys.forEach(function(k) {
      if (panels[k]) panels[k].style.display = k === cat ? "" : "none";
      if (menuButtons[k]) {
        menuButtons[k].className = "flex items-center gap-2.5 w-full text-left px-3 py-2.5 rounded-lg text-sm transition-colors mb-1 "
          + (k === cat
            ? "bg-f-surface border border-f-purple/50 text-white"
            : "text-gray-400 hover:text-gray-200 hover:bg-f-surface/50");
      }
    });
  }

  catKeys.forEach(function(cat) {
    if (menuButtons[cat]) {
      menuButtons[cat].addEventListener("click", function() {
        activateCategory(cat);
      });
    }
  });

  activateCategory("perceive-plan");

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

    previewImg.src = URL.createObjectURL(blob);
    imagePreview.classList.remove("hidden");

    taskInput.value = ex.task;
    taskInput.dispatchEvent(new Event("input"));

    // Store proprioception for action examples
    window._selectedProprioception = ex.proprioception || null;
    window._selectedGroundTruth = ex.ground_truth_action || null;
    window._selectedCategory = ex.category || "perceive-plan";

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

function addToHistory(evalId, task, strategyIds) {
  var entry = {
    id: evalId,
    task: task,
    strategyIds: strategyIds,
    timestamp: new Date().toISOString(),
    status: "running",
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
    item.className = "history-item sidebar-item flex items-center gap-2 px-3 py-2 rounded-md cursor-pointer text-[12px]";
    item.setAttribute("role", "button");
    item.setAttribute("tabindex", "0");
    if (idx === activeHistoryIndex) {
      item.classList.add("active", "bg-f-surface");
    }

    // Status dot
    var dot = document.createElement("span");
    var dotColor = entry.status === "running" ? "bg-purple-500 pulse-purple" :
                   entry.status === "completed" ? "bg-green-500" : "bg-red-500";
    dot.className = "w-2 h-2 rounded-full shrink-0 " + dotColor;
    dot.setAttribute("aria-hidden", "true");
    item.appendChild(dot);

    // Task text (truncated)
    var textSpan = document.createElement("span");
    textSpan.className = "text-gray-300 truncate flex-1";
    textSpan.textContent = entry.task.length > 25 ? entry.task.substring(0, 25) + "..." : entry.task;
    item.appendChild(textSpan);

    // Time
    var timeSpan = document.createElement("span");
    timeSpan.className = "text-[10px] text-gray-600 shrink-0";
    var ts = typeof entry.timestamp === "string" ? new Date(entry.timestamp) : entry.timestamp;
    var h = ts.getHours().toString().padStart(2, "0");
    var m = ts.getMinutes().toString().padStart(2, "0");
    timeSpan.textContent = h + ":" + m;
    item.appendChild(timeSpan);

    // Delete button (visible on hover)
    var delBtn = document.createElement("button");
    delBtn.className = "history-delete";
    delBtn.textContent = "\u00d7";
    delBtn.title = "Remove from history";
    delBtn.addEventListener("click", function(e) {
      e.stopPropagation();
      deleteHistoryEntry(idx);
    });
    item.appendChild(delBtn);

    // Hover tooltip
    item.addEventListener("mouseenter", function(e) {
      showHistoryTooltip(item, entry);
    });
    item.addEventListener("mouseleave", function() {
      var tip = item.querySelector(".history-tooltip");
      if (tip) tip.remove();
    });

    item.addEventListener("click", function() {
      showHistoryEntry(idx);
    });
    item.addEventListener("keydown", function(e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); showHistoryEntry(idx); }
    });

    container.appendChild(item);
  });
}

function showHistoryTooltip(itemEl, entry) {
  // Remove any existing tooltip
  var existing = itemEl.querySelector(".history-tooltip");
  if (existing) existing.remove();

  var tip = document.createElement("div");
  tip.className = "history-tooltip";

  // Full task text
  var taskP = document.createElement("p");
  taskP.className = "text-[11px] text-gray-200 mb-2";
  taskP.textContent = entry.task;
  tip.appendChild(taskP);

  // Strategy names + status
  entry.strategyIds.forEach(function(sid) {
    var strat = strategies.find(function(s) { return s.id === sid; });
    var displayName = strat ? strat.display_name : sid;
    var sr = entry.summaryResults && entry.summaryResults[sid];

    var row = document.createElement("div");
    row.className = "flex items-center gap-2 mb-1";

    var dot = document.createElement("span");
    dot.className = "w-1.5 h-1.5 rounded-full shrink-0";
    if (sr && sr.status === "completed") {
      dot.style.background = sr.success ? "#22c55e" : "#ef4444";
    } else if (sr && sr.status === "error") {
      dot.style.background = "#ef4444";
    } else {
      dot.style.background = "#7c3aed";
    }
    row.appendChild(dot);

    var nameSpan = document.createElement("span");
    nameSpan.className = "text-[10px] text-gray-300 flex-1 truncate";
    nameSpan.textContent = displayName;
    row.appendChild(nameSpan);

    if (sr && sr.latency_ms != null) {
      var latSpan = document.createElement("span");
      latSpan.className = "text-[10px] text-gray-500 font-mono";
      latSpan.textContent = Math.round(sr.latency_ms) + "ms";
      row.appendChild(latSpan);
    }

    tip.appendChild(row);
  });

  // Timestamp
  var ts = typeof entry.timestamp === "string" ? new Date(entry.timestamp) : entry.timestamp;
  var timeP = document.createElement("p");
  timeP.className = "text-[10px] text-gray-600 mt-2";
  timeP.textContent = ts.toLocaleString();
  tip.appendChild(timeP);

  itemEl.appendChild(tip);
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

    // Build per-strategy tab content from stored results
    tabData = {};
    ids.forEach(function(sid) {
      var container = createTabContainer(sid);
      tabData[sid] = { el: container, typingEl: null, stages: {}, status: "completed" };

      var stratResults = entry.results && entry.results[sid];
      if (stratResults && stratResults.stages) {
        stratResults.stages.forEach(function(stg, stageIndex) {
          addStageCard(stg.stage, stg.status, stg.latencyMs, stg.output, stg.error, stg.modelId, container, stageIndex);
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
      stratResults.stages.forEach(function(stg, stageIndex) {
        addStageCard(stg.stage, stg.status, stg.latencyMs, stg.output, stg.error, stg.modelId, container, stageIndex);
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
    // Truncate raw_response fields to save space
    var toSave = JSON.parse(JSON.stringify(runHistory));
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
        runHistory = parsed;
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
            Object.keys(se.results).forEach(function(sid) {
              var sr = se.results[sid];
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
                success: sr.success,
                totalLatencyMs: sr.total_latency_ms,
              };
              entry.summaryResults[sid] = {
                status: sr.success != null ? "completed" : "error",
                success: sr.success,
                latency_ms: sr.total_latency_ms,
                currentStage: null,
                stageStatuses: {},
              };
            });
          }
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
function initConfigPanel() {
  var collapsed = true;
  configChevron.style.transform = "rotate(180deg)";
  configToggle.addEventListener("click", function() {
    collapsed = !collapsed;
    configContent.style.display = collapsed ? "none" : "";
    configChevron.style.transform = collapsed ? "rotate(180deg)" : "";
    configToggle.setAttribute("aria-expanded", String(!collapsed));
  });
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
  imageInput.value = "";
  imagePreview.classList.add("hidden");
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
    configContent.style.display = "";
    configChevron.style.transform = "";
    configToggle.setAttribute("aria-expanded", "true");
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

  // Assign distinct colors to each strategy
  strategyColorMap = {};
  strategyIds.forEach(function(sid, i) {
    strategyColorMap[sid] = STRATEGY_COLORS[i % STRATEGY_COLORS.length];
  });

  // Initialize summary results for each strategy
  strategyIds.forEach(function(sid) {
    var strat = strategies.find(function(s) { return s.id === sid; });
    var stageStatuses = { verify: "pending" };
    ["perceive", "plan", "act"].forEach(function(stage) {
      stageStatuses[stage] = (strat && strat[stage]) ? "pending" : "skipped";
    });
    summaryResults[sid] = {
      status: "running",
      success: null,
      latency_ms: null,
      currentStage: null,
      stageStatuses: stageStatuses
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
    tabData[sid] = { el: container, typingEl: null, stages: {}, status: "running" };
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
    tabData[sid] = { el: container, typingEl: null, stages: {}, status: "running" };
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

  // Remove old tab content but keep persistent views
  chatArea.querySelectorAll("[id^='tab-content-']").forEach(function(el) { el.remove(); });

  if (sid === "__summary__" && summaryEl) {
    chatArea.appendChild(summaryEl);
  } else if (tabData[sid]) {
    chatArea.appendChild(tabData[sid].el);
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
      lat.textContent = Math.round(latencyMs) + "ms";
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
  headerRow.className = "grid grid-cols-[1fr_80px_70px_80px_140px] gap-2 px-4 py-2 border-b border-f-border text-[10px] text-gray-500 font-semibold uppercase tracking-wider";
  ["Strategy", "Status", "Result", "Latency", "Stages"].forEach(function(h) {
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
    row.className = "grid grid-cols-[1fr_80px_70px_80px_140px] gap-2 px-4 py-2.5 border-b border-f-border/50 items-center cursor-pointer hover:bg-f-elevated/50 transition-colors";
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
      resultCell.textContent = "\u2014";
    }
    row.appendChild(resultCell);

    // Latency
    var latCell = document.createElement("span");
    latCell.className = "text-[11px] font-mono " + (sr.latency_ms != null ? "text-gray-300" : "text-gray-600");
    latCell.textContent = sr.latency_ms != null ? Math.round(sr.latency_ms) + "ms" : "\u2014";
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
}

// ---- SSE connection ----
function connectSSE(evalId) {
  if (currentEventSource) currentEventSource.close();

  var es = new EventSource(API_BASE + "/api/evaluate/" + evalId + "/stream");
  currentEventSource = es;

  // Find the active history entry for this eval
  var historyEntry = runHistory.find(function(e) { return e.id === evalId; });

  es.addEventListener("stage", function(e) {
    var data = JSON.parse(e.data);
    var sid = data.strategy_id || activeTabId;
    var stage = data.stage;
    var status = data.status;

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
      if (tabData[sid].typingEl) {
        tabData[sid].typingEl.classList.add("animate-fade-out");
        var oldTyping = tabData[sid].typingEl;
        setTimeout(function() { oldTyping.remove(); }, 150);
      }
      tabData[sid].typingEl = addTypingIndicator(stage, tabData[sid].el, sid);
    }

    if (status === "completed" || status === "error") {
      if (tabData[sid].typingEl) {
        tabData[sid].typingEl.classList.add("animate-fade-out");
        var oldEl = tabData[sid].typingEl;
        setTimeout(function() { oldEl.remove(); }, 150);
        tabData[sid].typingEl = null;
      }
      var stageIndex = ["perceive", "plan", "act", "verify"].indexOf(stage);
      addStageCard(stage, status, data.latency_ms, data.output, data.error, data.model_id, tabData[sid].el, stageIndex);
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
        });
      }
    }
  });

  es.addEventListener("strategy_complete", function(e) {
    var data = JSON.parse(e.data);
    var sid = data.strategy_id;
    if (tabData[sid] && tabData[sid].typingEl) {
      tabData[sid].typingEl.remove();
      tabData[sid].typingEl = null;
    }
    updateTabStatus(sid, "completed", data.total_latency_ms);

    // Update summary
    if (summaryResults[sid]) {
      summaryResults[sid].status = "completed";
      summaryResults[sid].latency_ms = data.total_latency_ms;
      summaryResults[sid].success = data.success != null ? data.success : null;
      summaryResults[sid].currentStage = null;
      updateSummaryTable();
    }

    // Store in history entry
    if (historyEntry && historyEntry.results[sid]) {
      historyEntry.results[sid].success = data.success;
      historyEntry.results[sid].totalLatencyMs = data.total_latency_ms;
    }
  });

  es.addEventListener("strategy_error", function(e) {
    var data = JSON.parse(e.data);
    var sid = data.strategy_id;
    if (tabData[sid] && tabData[sid].typingEl) {
      tabData[sid].typingEl.remove();
      tabData[sid].typingEl = null;
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

  es.addEventListener("complete", function() {
    es.close();
    currentEventSource = null;
    setRunning(false);
    updateHistoryStatus(evalId, "completed");
  });

  es.addEventListener("error", function(e) {
    Object.values(tabData).forEach(function(td) {
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
    configContent.style.display = "none";
    configChevron.style.transform = "rotate(180deg)";
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

function addStageCard(stage, status, latencyMs, output, error, modelId, container, stageIndex) {
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
    if (output.success) {
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
    latBadge.className = "text-[10px] bg-f-elevated text-gray-300 px-2 py-0.5 rounded";
    latBadge.textContent = latencyMs + "ms";
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
    case "verify":   renderVerify(container, output);   break;
    default:
      var pre = document.createElement("pre");
      pre.className = "text-xs text-gray-400";
      pre.textContent = JSON.stringify(output, null, 2);
      container.appendChild(pre);
  }
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
    if (o.sim_success != null) {
      var successBadge = document.createElement("span");
      successBadge.className = "text-[10px] px-2 py-0.5 rounded " +
        (o.sim_success ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/15 text-red-300");
      successBadge.textContent = o.sim_success ? "Sim: success" : "Sim: not achieved";
      simRow.appendChild(successBadge);
    }
    if (o.sim_done) {
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

  if (!rendered) {
    var fallback = document.createElement("pre");
    fallback.className = "text-xs text-gray-400";
    fallback.textContent = JSON.stringify(o, null, 2);
    container.appendChild(fallback);
  }
}

function renderVerify(container, o) {
  var rendered = false;

  if (o.confidence != null) {
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
    var reason = document.createElement("p");
    reason.className = "text-xs text-gray-400 italic border-l-2 border-f-border-strong pl-3 mt-2";
    reason.textContent = o.reasoning || o.explanation;
    container.appendChild(reason);
  }

  if (!rendered) {
    var fallback = document.createElement("pre");
    fallback.className = "text-xs text-gray-400";
    fallback.textContent = JSON.stringify(o, null, 2);
    container.appendChild(fallback);
  }
}
