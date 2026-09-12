/* Shared presentation for the original runner and campaign trial outputs. No execution or network access. */
(function () {
"use strict";
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

function renderStageOutput(container, stage, output, context = {}) {
  container.classList.add("rich-stage-body");
  if (output == null) { const missing = document.createElement("p"); missing.textContent = "No output recorded."; container.appendChild(missing); return; }
  switch (stage) {
    case "perceive": renderPerceive(container, output); break;
    case "plan":     renderPlan(container, output, context);     break;
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
      tag.className = "text-xs bg-f-purple/10 text-teal-300 border border-f-purple/20 px-2 py-0.5 rounded";
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

function renderPlan(container, o, context = {}) {
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
  if (context.expected_subtasks && Array.isArray(context.expected_subtasks) && context.expected_subtasks.length > 0 && o.steps && o.steps.length > 0) {
    rendered = true;
    var esSection = document.createElement("div");
    esSection.className = "mt-2 space-y-1";
    var esHeading = document.createElement("p");
    esHeading.className = "text-[11px] text-blue-400 font-medium";
    esHeading.textContent = "Expected Subtasks";
    esSection.appendChild(esHeading);
    var stepsLower = o.steps.map(function(s) { return (typeof s === "string" ? s : JSON.stringify(s)).toLowerCase(); }).join(" ");
    context.expected_subtasks.forEach(function(expected) {
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
  if (context.correction) {
    rendered = true;
    var corrSection = document.createElement("div");
    corrSection.className = "mt-2 p-2 rounded border border-amber-500/20 bg-amber-500/5";
    var corrHeading = document.createElement("p");
    corrHeading.className = "text-[11px] text-amber-400 font-medium mb-1";
    corrHeading.textContent = "Situated Correction";
    corrSection.appendChild(corrHeading);
    var corrFeedback = document.createElement("p");
    corrFeedback.className = "text-xs text-amber-200 italic";
    corrFeedback.textContent = "\u201c" + (context.correction.feedback || "") + "\u201d";
    corrSection.appendChild(corrFeedback);
    if (context.correction.timing) {
      var corrTiming = document.createElement("p");
      corrTiming.className = "text-[10px] text-gray-500 mt-0.5";
      corrTiming.textContent = "Timing: " + context.correction.timing;
      corrSection.appendChild(corrTiming);
    }
    container.appendChild(corrSection);
  }

  // Active constraints (constrained tasks)
  if (context.constraints && Array.isArray(context.constraints) && context.constraints.length > 0) {
    // Check which constraints were acknowledged
    var acked = (o.constraints_acknowledged || []).map(function(c) { return c.toLowerCase(); }).join(" ");
    var csSection = document.createElement("div");
    csSection.className = "mt-2 space-y-1";
    var csHeading = document.createElement("p");
    csHeading.className = "text-[11px] text-red-400 font-medium";
    csHeading.textContent = "Task Constraints";
    csSection.appendChild(csHeading);
    context.constraints.forEach(function(constraint) {
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
    table.className = "rich-trajectory-table mt-1 font-mono text-[10px] leading-relaxed overflow-x-auto";

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
        evidenceArrow.className = "text-teal-400 text-[11px] transition-transform duration-200";
        evidenceArrow.style.transform = "rotate(90deg)";
        evidenceArrow.textContent = "\u203a";
        evidenceToggle.appendChild(evidenceArrow);
        var evidenceLabel = document.createElement("span");
        evidenceLabel.className = "text-[10px] font-semibold text-teal-400 group-hover:text-teal-300 transition-colors uppercase tracking-wider";
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
    turnsBadge.className = "text-[10px] bg-teal-500/15 text-teal-300 px-1.5 py-0.5 rounded mt-1 inline-block";
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

const api = {renderStageOutput, renderPerceive, renderPlan, renderAct, renderVerify, renderDynamics};
if (typeof module !== "undefined" && module.exports) module.exports = api;
if (typeof window !== "undefined") window.RoveStageRenderers = api;
})();
