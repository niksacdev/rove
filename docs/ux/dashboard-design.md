# ROVE Dashboard UX Design

**Date**: 2026-02-21 (updated 2026-02-22)
**Author**: UX Designer
**Status**: Design Specification — Updated for Per-Stage Model Configuration
**Related ADRs**: RAI-ADR-006 (Accessibility), RAI-ADR-001 (Evaluation Fairness)

---

## ADDENDUM 2026-02-22: Per-Stage Model Configuration Design

### Problem Identified

The current `frontend/index.html` implements three dropdowns (VLM, Policy, Sim) in the bottom input bar. This is incorrect because it encodes a false assumption: that there is always exactly one VLM and one VLA with fixed roles. The ROVE pipeline requires per-stage model assignment because:

- A VLA (pi0) may handle perceive+plan+act internally.
- A VLM may handle perceive, while an LLM handles plan, while the same VLM handles verify.
- Sim is infrastructure (execution environment), not a pipeline-stage model.

### Design Decision: Collapsible Stage-Card Configuration Panel

Replace the three bottom-bar dropdowns with a **collapsible configuration panel** that sits between the chat area and the input bar. The panel contains four stage cards (Perceive, Plan, Act, Verify) that each own their model assignment independently.

#### Panel States

**Collapsed (default):** A single summary line shows the active assignments.

```text
CONFIG  [Preset: All Mock]  Perceive: Mock VLM  |  Act: Mock VLA  |  Sim: Mock  [Edit] [v]
```

**Expanded:** Four stage cards in a horizontal row (4-column grid, stacks to 2-col below 900px).

#### Stage Card Structure

Each card displays:

- Stage number and name (matches the left sidebar pipeline icons)
- Model type selector where applicable (only Plan stage: VLM or LLM)
- Primary model dropdown
- "Also compare" expandable checklist (Perceive and Act cards only)

#### Special Cases

**Plan card:** Has a "Same as Perceive (recommended)" checkbox checked by default. Unchecking reveals a model type selector (VLM or LLM) and the corresponding model dropdown.

**Verify card:** Read-only. Displays "Auto-assigned: uses Perceive model." Updates its displayed model name when the Perceive dropdown changes. Not a focusable input. Uses `role="status"`.

**VLA end-to-end mode:** When the user selects a VLA that handles perceive+plan+act internally (pi0, SmolVLA), the Perceive and Plan cards dim and display: "Used for VLM evaluation only. [VLA name] runs its own internal pipeline for action." Cards remain visible so users understand ROVE still evaluates the VLM side.

#### Supporting Infrastructure Row

Below the four stage cards, a separate "Supporting Infrastructure" row holds:

- Grounding model dropdown (hint: "Object localization")
- Sim environment dropdown (hint: "Robot physics environment")

Neither has a "Compare others" checklist. You run one sim environment per evaluation.

#### Preset Mechanism

A "Load preset" dropdown at the top of the panel reads from `GET /api/evaluations/presets`. Selecting a preset fills all stage cards. Each card shows a "Preset: [name]" badge and a "Customise" link that unlocks the card for editing without resetting the others.

#### Progressive Disclosure Summary

| User | Entry point | What they see |
|------|-------------|---------------|
| Demo / Robot Manager | Load preset | One dropdown, all cards auto-fill, click Evaluate |
| Researcher | Load preset, tweak one card | Preset fills all, click Customise on one card |
| ML Engineer | No preset, configure manually | Four stage cards, full control |

#### Accessibility Properties

- Collapse/expand toggle: `<button>` with `aria-expanded` and `aria-controls`.
- Each stage card: `role="group"` with `aria-labelledby` pointing to the stage heading.
- "Same as Perceive" checkbox: `aria-describedby` pointing to hint text.
- Dimmed VLA-handles-all cards: `aria-disabled="true"` and visible text (not color alone).
- Verify card: `role="status"`, announces model name change when Perceive changes.
- "Also compare" fieldsets: `<fieldset>` with `<legend>` reading "Also compare against (optional)."
- Preset dropdown: `aria-label="Load a named evaluation from rove.yaml"`.

### Files to Update

- `frontend/index.html`: Remove three bottom-bar dropdowns. Add collapsible config panel with stage cards above the input bar. Update welcome screen step 3 text.
- `docs/ux/dashboard-design.md`: This addendum.

### API Requirement

The new UI requires `GET /api/evaluations/presets` to return named evaluations from `rove.yaml`. The `POST /api/evaluations` body must accept per-stage model assignments:

```json
{
  "task": "Pick the red bracket...",
  "image_b64": "...",
  "stages": {
    "perceive": { "model_id": "gpt-4o", "type": "vlm" },
    "plan":     { "model_id": "gpt-4o", "type": "vlm", "same_as_perceive": true },
    "act":      { "model_id": "smolvla-450m", "type": "vla" },
    "verify":   { "model_id": "gpt-4o", "type": "vlm", "auto_assigned": true }
  },
  "also_compare": {
    "perceive": ["qwen3-vl-8b", "phi-4-multimodal"],
    "act": ["pi0", "openvla-oft-7b"]
  },
  "grounding": "groundingdino",
  "sim": "mujoco-libero",
  "mode": "full"
}
```

This replaces the flat `vlm` / `vla` / `sim` fields in the current request body. Consult Architecture agent before implementation.

---

## 1. User Context (Who Is Actually Using This)

Before any layout decisions, we establish the three personas and their dashboard goals.

### Persona A: Robotics Researcher

- CLI-first. Opens the dashboard when they need visual comparison of results.
- Primary need: Load a named evaluation from rove.yaml and see the ranked results table.
- Secondary need: Compare two runs side-by-side across model configs.
- Context: Focused, desktop, likely running alongside a terminal.

### Persona B: ML Engineer on a Manipulation Team

- Dashboard-first for exploration, CLI for automation.
- Primary need: Configure a quick ad hoc run (select models, upload image, type task), see live progress.
- Secondary need: Export comparison data for a report.
- Context: Desktop, may run multiple evaluations in a session.

### Persona C: Platform Admin

- Rarely runs evaluations. Wants to see which models are available and healthy.
- Primary need: The models panel. Which adapters are registered? Which are healthy?
- Secondary need: Leaderboard aggregate across runs.
- Context: Quick check, may be on a laptop away from a workstation.

---

## 2. User Flow: Configure -> Run -> Compare

The central question is whether this is a single scrolling page or a tabbed layout.

### Decision: Three-Panel Single Page With a Persistent Sidebar

Rationale:

- Tabs force context switching and hide state (e.g., you cannot see history while configuring).
- A three-column layout keeps configure/run/results visible simultaneously on a 1440px desktop.
- On narrower screens (1024px laptop), the history sidebar collapses to an icon rail.
- History is always a click away without losing the current run state.

### Full Flow

```text
User arrives
    |
    +-- Has a named evaluation in rove.yaml?
    |       YES --> Load Preset dropdown --> Stage selectors auto-fill --> Run
    |       NO  --> Manually pick models in each stage card --> Run
    |
    v
Upload image + type task command
    |
    v
Click "Run Evaluation"
    |
    v
POST /api/evaluations -> 202 Accepted -> evaluation_id
    |
    v
SSE stream opens: GET /api/evaluations/{id}/stream
    |
    v
Pipeline stage cards animate: pending -> running (spinner) -> complete / failed
    |
    v
Results panel fills in as combinations complete
    |
    v
All combinations done: ranked table appears
    |
    v
Run saved to history sidebar (auto)
    |
    +-- Want to compare? Click second run in sidebar -> Comparison view overlays
```

---

## 3. Layout: ASCII Mockups

### 3A. Main Layout (1440px Desktop, Configure + Run State)

```text
+------------------------------------------------------------------+-------+
| ROVE  v0.1                          [Models] [Leaderboard] [Docs]| HIST  |
+------------------------------------------------------------------+ SIDE  |
|                                                                   | BAR   |
|  PIPELINE CONFIGURATION                                           |       |
|  +------------------+  +------------------+  +------------------+|  (see |
|  | PERCEIVE         |  | PLAN             |  | ACT              ||  3C)  |
|  | Vision-Language  |  | Task Planning   |  | Action Model     ||       |
|  | Model            |  | Model            |  | (VLA)            ||       |
|  |                  |  |                  |  |                  ||       |
|  | [v] GPT-4o    *  |  | [v] GPT-4o    *  |  | [v] SmolVLA   *  ||       |
|  |                  |  |  (same as        |  |                  ||       |
|  | [Checkbox] Add   |  |   Perceive)      |  | [Checkbox] Add   ||       |
|  | more VLMs to     |  |                  |  | more VLAs to     ||       |
|  | compare          |  | [Checkbox] Use   |  | compare          ||       |
|  |                  |  | a different      |  |                  ||       |
|  +------------------+  | model            |  +------------------+|       |
|                         +------------------+                      |       |
|  VERIFY (auto): uses Perceive VLM -- GPT-4o                      |       |
|                                                                   |       |
|  GROUNDING [v] GroundingDINO *    SIM [v] MuJoCo+LIBERO *        |       |
|                                                                   |       |
|  [Load Preset v]  pick_bracket / bracket_variations / vlm_only.. |       |
|                                                                   |       |
+------------------------------------------------------------------+       |
|                                                                   |       |
|  TASK INPUT                                                       |       |
|  +----------------------------------------------+  +-----------+ |       |
|  |                                              |  |           | |       |
|  |  Pick the red bracket and place it in bin A  |  |  [IMAGE]  | |       |
|  |                                              |  |  bracket  | |       |
|  |  (task command -- be specific about objects  |  |  .jpg     | |       |
|  |   and targets)                               |  |           | |       |
|  +----------------------------------------------+  | Click or  | |       |
|                                                     | drag to   | |       |
|  Mode: (o) Full Pipeline  ( ) VLM Only              | upload    | |       |
|                                                     +-----------+ |       |
|                                     [Run Evaluation]              |       |
|                                                                   |       |
+------------------------------------------------------------------+-------+
```

### 3B. Main Layout (Running State -- Live SSE Progress)

```text
+------------------------------------------------------------------+-------+
| ROVE  v0.1                                                        | HIST  |
+------------------------------------------------------------------+ (run  |
|                                                                   | in    |
|  RUNNING: "Pick the red bracket..."    [Cancel]                   | prog) |
|                                                                   |       |
|  4 combinations running in parallel                               |  2026 |
|  [===========---------------------] 2 / 4 complete               | -02-21|
|                                                                   | 10:42 |
|                                                                   | GPT4o |
|  +-------------------------------+  +---------------------------+ | +SmVL |
|  | GPT-4o + SmolVLA     [done]   |  | GPT-4o + CogACT  [spin]  | | RUNN |
|  |                               |  |                           | |      |
|  |  perceive  [check] 412ms      |  |  perceive  [check]        | |      |
|  |  ground    [check]  89ms      |  |  ground    [check]        | |      |
|  |  plan      [check] 388ms      |  |  plan      [spinner]      | |      |
|  |  execute   [check] 854ms      |  |  execute   [pending]      | |      |
|  |  verify    [check] 199ms      |  |  verify    [pending]      | |      |
|  |                               |  |                           | |      |
|  |  Total: 1942ms  SUCCESS       |  |                           | |      |
|  +--------------------------------+  +---------------------------+ |      |
|                                                                   |       |
|  +-------------------------------+  +---------------------------+ |       |
|  | Qwen3-VL + SmolVLA   [spin]   |  | Qwen3-VL + CogACT [-]    | |       |
|  |  perceive  [spinner]          |  |  perceive  [pending]      | |       |
|  |  ground    [pending]          |  |  ground    [pending]      | |       |
|  |  ...                          |  |  ...                      | |       |
|  +--------------------------------+  +---------------------------+ |       |
+------------------------------------------------------------------+-------+
```

### 3C. History Sidebar (Expanded)

```text
+----------+
| HISTORY  |
|          |
| [Search] |
|          |
| [ ] 2026 |
|     -02- |
|     21   |
|     10:42|
|     GPT4o|
|     +SmVL|
|     pick |
|  [PASS]  |
|          |
| [ ] 2026 |
|     -02- |
|     21   |
|     10:38|
|     Qwen |
|     +Cog |
|     pick |
|  [FAIL]  |
|          |
| 2 sel.   |
| [Compare]|
+----------+
```

Sidebar items are selectable with checkboxes. When 2 or more are checked, the "Compare" button becomes active. Clicking a single item loads its results into the results panel.

### 3D. Results Panel (Complete State, Single Run)

```text
+------------------------------------------------------------------+
|  RESULTS: pick_bracket  |  2026-02-21 10:42  |  [Export JSONL]  |
|                                                                  |
|  Ranked by: success first, then latency, then cost              |
|                                                                  |
|  +------+------------+------+-------+--------+------+----------+ |
|  | Rank | Combination| Succ | Sim   | Judge  | Lat  | Cost     | |
|  |      |            | ess  | Succ  | Calib  | (ms) | (USD)    | |
|  |      |            |      |       |   [?]  |      |          | |
|  +------+------------+------+-------+--------+------+----------+ |
|  |  1   | GPT-4o +   | YES  |  YES  |  YES   | 1842 | $0.021   | |
|  |      | SmolVLA    |      |       |        |      |          | |
|  +------+------------+------+-------+--------+------+----------+ |
|  |  2   | GPT-4o +   | YES  |  YES  |  YES   | 3210 | $0.021   | |
|  |      | CogACT 7B  |      |       |        |      |          | |
|  +------+------------+------+-------+--------+------+----------+ |
|  |  3   | Qwen3-VL + | YES  |  NO   |  NO    | 2104 | $0.008   | |
|  |      | SmolVLA    |      |       |        |      |          | |
|  +------+------------+------+-------+--------+------+----------+ |
|  |  4   | Qwen3-VL + | NO   |  NO   |  YES   | FAIL |  --      | |
|  |      | CogACT 7B  |      |       |        |      |          | |
|  +------+------------+------+-------+--------+------+----------+ |
|                                                                  |
|  [Click any row to expand step-by-step results]                  |
|                                                                  |
|  EXPANDED: GPT-4o + SmolVLA  [collapse]                         |
|  +--Perceive---------------------------------------------+      |
|  |  Scene: "manufacturing workbench, red bracket center  |      |
|  |  left, Bin A right side"                              |      |
|  |  Objects: red bracket (0.94), Bin A (0.91), Bin B (0.87)     |
|  |  Latency: 412ms   Cost: $0.008                        |      |
|  +-------------------------------------------------------+      |
|  +--Ground------------------------------------------------+      |
|  |  Target: "red bracket"  BBox: [0.28, 0.41, 0.44, 0.62]      |
|  |  Latency: 89ms                                        |      |
|  +-------------------------------------------------------+      |
|  +--Plan--------------------------------------------------+      |
|  |  Approach: top-down   Confidence: 0.91                |      |
|  |  "Move above the bracket, close gripper on body,      |      |
|  |   lift and move to Bin A"                             |      |
|  |  Latency: 388ms   Cost: $0.006                        |      |
|  +-------------------------------------------------------+      |
|  +--Execute-----------------------------------------------+      |
|  |  VLA: SmolVLA 450M   Action chunk: 10 steps           |      |
|  |  Sim success: YES    Steps to success: 7               |      |
|  |  Latency: 854ms                                       |      |
|  +-------------------------------------------------------+      |
|  +--Verify------------------------------------------------+      |
|  |  [Before image]      [After image]                    |      |
|  |  VLM says: SUCCESS   Confidence: 0.94                 |      |
|  |  "Bracket is in Bin A, task complete"                 |      |
|  |  Judge calibration: YES (matches sim result)          |      |
|  |  Latency: 199ms   Cost: $0.007                        |      |
|  +-------------------------------------------------------+      |
+------------------------------------------------------------------+
```

### 3E. Comparison View (2 Runs Side-by-Side)

```text
+------------------------------------------------------------------+
|  COMPARE  |  [X] Close comparison                                |
|                                                                  |
|  Run A: 10:42  pick_bracket  GPT-4o + SmolVLA                   |
|  Run B: 10:38  pick_bracket  Qwen3-VL + SmolVLA                 |
|                                                                  |
|  +---------------------+-----------+-----------+----------+     |
|  | Metric              | Run A     | Run B     | Winner   |     |
|  +---------------------+-----------+-----------+----------+     |
|  | Success             | YES       | YES       | Tie      |     |
|  | Sim Success         | YES       | NO        | RUN A    |     |
|  | Judge Calibration   | YES       | NO        | RUN A    |     |
|  | Total Latency       | 1842ms    | 2104ms    | RUN A    |     |
|  | Total Cost          | $0.021    | $0.008    | RUN B    |     |
|  | Perceive Latency    | 412ms     | 387ms     | RUN B    |     |
|  | Plan Confidence     | 0.91      | 0.74      | RUN A    |     |
|  | Verify Confidence   | 0.94      | 0.81      | RUN A    |     |
|  +---------------------+-----------+-----------+----------+     |
|                                                                  |
|  5 of 8 metrics favor Run A. Run B costs 2.6x less.             |
|  Review individual metrics before selecting a model.            |
|                                                                  |
|  [Export comparison as JSONL]                                    |
+------------------------------------------------------------------+
```

Note on comparison summary wording: the summary avoids declaring a definitive "winner" and instead presents the metric count alongside a salient tradeoff (cost difference). The user makes the final decision. This is intentional per RAI-ADR-001 (no automated model selection).

### 3F. Models Panel (Platform Admin View)

```text
+------------------------------------------------------------------+
|  ROVE  v0.1          [Dashboard] [Models] [Leaderboard] [Docs]  |
+------------------------------------------------------------------+
|                                                                  |
|  REGISTERED MODELS                  [Check all health]          |
|                                                                  |
|  VLMs -- Vision-Language Models                                  |
|  (Scene understanding, task planning, verification)            |
|  +-------+---------------+-----------+---------+-------+-------+|
|  | Health| ID            | Name      | Type    | Cost  | Caps  ||
|  +-------+---------------+-----------+---------+-------+-------+|
|  | [OK]  | gpt-4o        | GPT-4o    | Azure   | $0.005| scene ||
|  |       |               |           | Managed | /1k   | plan  ||
|  |       |               |           |         |       | verify||
|  +-------+---------------+-----------+---------+-------+-------+|
|  | [OK]  | qwen25-vl-32b | Qwen2.5-  | Azure   | $0.002| scene ||
|  |       |               | VL 32B    | HF      | /1k   | native||
|  |       |               |           |         |       | ground||
|  +-------+---------------+-----------+---------+-------+-------+|
|  | [OFF] | cosmos-reason | Cosmos-   | Local   | free  | scene ||
|  |       | 2-2b          | Reason2   | MLX     |       | verify||
|  +-------+---------------+-----------+---------+-------+-------+|
|                                                                  |
|  VLAs -- Action Models                                           |
|  (Robot movement prediction from scene images)                  |
|  +-------+---------------+-----------+---------+-------+-------+|
|  | [OK]  | smolvla-450m  | SmolVLA   | Local   | free  | chunk ||
|  |       |               | 450M      | LeRobot |       | 10    ||
|  +-------+---------------+-----------+---------+-------+-------+|
|  | [OFF] | cogact-7b     | CogACT 7B | Azure   | $0.001| chunk ||
|  |       |               |           | GPU     | 2/s   | 16    ||
|  +-------+---------------+-----------+---------+-------+-------+|
|                                                                  |
|  [OK] = reachable and healthy   [OFF] = unavailable             |
|  Health indicators show text labels, not color alone            |
+------------------------------------------------------------------+
```

---

## 4. HTML Structure and Component Hierarchy

### File Structure

```text
rove/dashboard/
    index.html      Single HTML file. All sections present, toggled with CSS class.
    app.js          All event handling, API calls, SSE, DOM updates.
    style.css       Dark theme variables, layout, component styles.
```

### index.html Skeleton

The full page is one HTML document. Views (dashboard, models, leaderboard) are `<div>` elements toggled with a `hidden` attribute. Sections within the dashboard view (config, input, progress, results, comparison) are similarly toggled. No page reloads.

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ROVE -- Robot Evaluation</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>

  <!-- NAV -->
  <nav id="nav" role="navigation" aria-label="Main navigation">
    <span class="brand">ROVE</span>
    <button class="nav-btn active" data-view="dashboard" aria-current="page">
      Dashboard
    </button>
    <button class="nav-btn" data-view="models">Models</button>
    <button class="nav-btn" data-view="leaderboard">Leaderboard</button>
  </nav>

  <!-- DASHBOARD VIEW -->
  <div id="view-dashboard" class="view active">
    <div class="layout-three-col">

      <!-- LEFT/CENTER: configure + results (~75% width) -->
      <main class="main-col" role="main">

        <!-- SECTION: Pipeline Configuration -->
        <section id="section-config" aria-label="Pipeline configuration">
          <div class="section-header">
            <h2>Pipeline Configuration</h2>
            <div class="preset-loader">
              <label for="preset-select">Load preset</label>
              <select id="preset-select"
                      aria-label="Load named evaluation from rove.yaml">
                <option value="">-- select preset --</option>
                <!-- populated by JS from GET /api/evaluations/presets -->
              </select>
            </div>
          </div>

          <!-- Stage Cards Row -->
          <div class="stage-cards" role="group" aria-label="Pipeline stages">

            <!-- PERCEIVE -->
            <div class="stage-card" id="stage-perceive">
              <div class="stage-header">
                <span class="stage-number" aria-hidden="true">1</span>
                <h3 class="stage-name">Perceive</h3>
                <span class="stage-hint">Scene understanding</span>
              </div>
              <div class="stage-body">
                <label for="vlm-perceive-select">Vision-Language Model</label>
                <select id="vlm-perceive-select"
                        aria-label="Select VLM for perceive stage"
                        aria-describedby="vlm-perceive-hint">
                  <!-- options added by JS; each option has data-healthy attribute -->
                </select>
                <p id="vlm-perceive-hint" class="field-hint">
                  Analyzes the scene image and identifies objects
                </p>
                <fieldset id="vlm-compare-group">
                  <legend>Also compare (optional)</legend>
                  <!-- checkboxes for each additional available VLM, added by JS -->
                  <!-- each checkbox: <label><input type="checkbox"> Model Name</label> -->
                </fieldset>
              </div>
            </div>

            <!-- PLAN -->
            <div class="stage-card" id="stage-plan">
              <div class="stage-header">
                <span class="stage-number" aria-hidden="true">2</span>
                <h3 class="stage-name">Plan</h3>
                <span class="stage-hint">Grasp planning</span>
              </div>
              <div class="stage-body">
                <label class="checkbox-label">
                  <input type="checkbox" id="plan-same-as-perceive" checked
                         aria-describedby="plan-same-hint">
                  Use same model as Perceive
                </label>
                <p id="plan-same-hint" class="field-hint">
                  Recommended: same VLM handles scene reasoning end-to-end
                </p>
                <!-- shown only when checkbox unchecked -->
                <div id="plan-model-row" hidden>
                  <label for="vlm-plan-select">Vision-Language Model</label>
                  <select id="vlm-plan-select"
                          aria-label="Select VLM for plan stage">
                  </select>
                </div>
              </div>
            </div>

            <!-- ACT -->
            <div class="stage-card" id="stage-act">
              <div class="stage-header">
                <span class="stage-number" aria-hidden="true">3</span>
                <h3 class="stage-name">Act</h3>
                <span class="stage-hint">Robot action model</span>
              </div>
              <div class="stage-body">
                <label for="vla-select">Action Model (VLA)</label>
                <select id="vla-select"
                        aria-label="Select VLA for act stage"
                        aria-describedby="vla-hint">
                </select>
                <p id="vla-hint" class="field-hint">
                  Predicts robot arm movements to execute the task plan
                </p>
                <fieldset id="vla-compare-group">
                  <legend>Also compare (optional)</legend>
                </fieldset>
              </div>
            </div>

            <!-- VERIFY (read-only, auto-assigned) -->
            <div class="stage-card stage-card--auto" id="stage-verify">
              <div class="stage-header">
                <span class="stage-number" aria-hidden="true">4</span>
                <h3 class="stage-name">Verify</h3>
                <span class="stage-badge">Auto-assigned</span>
              </div>
              <div class="stage-body">
                <p id="verify-model-label" class="auto-assignment">
                  Uses Perceive model:
                  <strong id="verify-vlm-name">GPT-4o</strong>
                </p>
                <p class="field-hint">
                  Compares before and after images to confirm task success
                </p>
              </div>
            </div>
          </div>

          <!-- Supporting models row -->
          <div class="support-models-row">
            <div class="support-model">
              <label for="grounding-select">Grounding Model</label>
              <select id="grounding-select"
                      aria-label="Select grounding model"
                      aria-describedby="grounding-hint">
              </select>
              <p id="grounding-hint" class="field-hint">
                Finds the target object's location in the image
              </p>
            </div>
            <div class="support-model">
              <label for="sim-select">Simulator</label>
              <select id="sim-select"
                      aria-label="Select simulator"
                      aria-describedby="sim-hint">
              </select>
              <p id="sim-hint" class="field-hint">
                Runs robot physics to test the action plan
              </p>
            </div>
          </div>
        </section>

        <!-- SECTION: Task Input -->
        <section id="section-input" aria-label="Task input">
          <h2>Task Input</h2>

          <div class="input-row">
            <div class="task-text-group">
              <label for="task-input">Task Command</label>
              <textarea id="task-input"
                        rows="3"
                        placeholder="e.g. Pick the red bracket and place it in bin A"
                        aria-describedby="task-hint">
              </textarea>
              <p id="task-hint" class="field-hint">
                Name the object, state the target location, use spatial terms
                visible in the scene image.
              </p>
            </div>

            <div class="image-upload-group">
              <span id="image-upload-label" class="field-label">Scene Image</span>
              <!-- Drop zone: keyboard accessible via role=button + tabindex -->
              <div id="image-drop-zone"
                   role="button"
                   tabindex="0"
                   aria-labelledby="image-upload-label"
                   aria-describedby="image-upload-hint"
                   aria-haspopup="dialog">
                <!-- Preview: hidden until image loaded -->
                <img id="image-preview"
                     src=""
                     alt=""
                     hidden>
                <div id="image-upload-prompt" aria-hidden="true">
                  <span class="drop-icon">+</span>
                  <span>Drop image here or click to browse</span>
                  <span class="drop-format">JPEG or PNG</span>
                </div>
              </div>
              <!-- File input hidden from visual display but operable -->
              <input type="file"
                     id="image-file-input"
                     accept="image/jpeg,image/png,image/webp"
                     aria-hidden="true"
                     tabindex="-1">
              <p id="image-upload-hint" class="field-hint">
                Use your robot camera's scene image. JPEG or PNG.
              </p>
            </div>
          </div>

          <!-- Mode selector -->
          <fieldset id="mode-fieldset">
            <legend>Evaluation Mode</legend>
            <label class="radio-label">
              <input type="radio" name="eval-mode" value="full" checked>
              Full pipeline -- runs simulator, produces success rate
            </label>
            <label class="radio-label">
              <input type="radio" name="eval-mode" value="vlm_only">
              VLM only -- skips robot simulator, compares perception and planning
            </label>
          </fieldset>

          <div class="run-bar">
            <!-- Validation message: role=alert fires immediately on error -->
            <div id="run-validation-msg"
                 class="validation-msg"
                 role="alert"
                 aria-live="polite">
            </div>
            <button id="run-btn"
                    class="btn btn-primary"
                    aria-label="Run evaluation with current configuration">
              Run Evaluation
            </button>
          </div>
        </section>

        <!-- SECTION: Live Progress (hidden until run starts) -->
        <section id="section-progress"
                 aria-label="Evaluation progress"
                 hidden>
          <div class="progress-header">
            <h2 id="progress-task-label">Running: ...</h2>
            <button id="cancel-btn" class="btn btn-secondary">Cancel</button>
          </div>
          <div class="progress-bar-wrapper">
            <div id="progress-bar"
                 class="progress-bar"
                 role="progressbar"
                 aria-valuemin="0"
                 aria-valuemax="100"
                 aria-valuenow="0"
                 aria-label="Evaluation progress">
            </div>
            <span id="progress-label" aria-live="polite">
              0 / 0 combinations complete
            </span>
          </div>
          <!-- Combination cards inserted here by JS using safe DOM methods -->
          <div id="combination-cards-grid" class="combination-cards-grid">
          </div>
        </section>

        <!-- SECTION: Results (hidden until complete) -->
        <section id="section-results"
                 aria-label="Evaluation results"
                 hidden>
          <div class="results-header">
            <h2>Results</h2>
            <div class="results-meta" id="results-meta-info" aria-live="polite">
            </div>
            <button id="export-btn" class="btn btn-secondary">
              Export JSONL
            </button>
          </div>
          <p class="ranking-note">
            Ranked by: success first, then latency, then cost
          </p>

          <div class="table-wrapper"
               role="region"
               aria-label="Ranked results table"
               tabindex="0">
            <table id="results-table">
              <caption>
                Model combinations ranked by success rate, latency, and cost
              </caption>
              <thead>
                <tr>
                  <th scope="col">Rank</th>
                  <th scope="col">VLM + VLA</th>
                  <th scope="col">
                    Success
                    <button class="tooltip-trigger"
                            aria-label="What does Success mean?"
                            data-tooltip="success">?</button>
                  </th>
                  <th scope="col">
                    Sim Success
                    <button class="tooltip-trigger"
                            aria-label="What does Sim Success mean?"
                            data-tooltip="sim-success">?</button>
                  </th>
                  <th scope="col">
                    Judge Calibration
                    <button class="tooltip-trigger"
                            aria-label="What does Judge Calibration mean?"
                            data-tooltip="judge-calibration">?</button>
                  </th>
                  <th scope="col">Latency (ms)</th>
                  <th scope="col">Cost (USD)</th>
                </tr>
              </thead>
              <tbody id="results-tbody">
                <!-- rows inserted by JS using safe DOM methods (no innerHTML) -->
              </tbody>
            </table>
          </div>

          <!-- Step detail: shown when a table row is activated -->
          <div id="step-detail-panel"
               class="step-detail-panel"
               hidden
               aria-label="Step-by-step results">
            <!-- step sections inserted by JS -->
          </div>
        </section>

        <!-- SECTION: Comparison (hidden until activated from sidebar) -->
        <section id="section-comparison"
                 aria-label="Run comparison"
                 hidden>
          <div class="comparison-header">
            <h2>Comparison</h2>
            <button id="close-comparison-btn" class="btn btn-ghost">
              Close comparison
            </button>
          </div>
          <div id="comparison-run-labels" class="comparison-run-labels">
          </div>
          <div class="table-wrapper"
               role="region"
               aria-label="Side-by-side metric comparison"
               tabindex="0">
            <table id="comparison-table">
              <caption>Metric comparison across selected evaluation runs</caption>
              <thead id="comparison-thead"></thead>
              <tbody id="comparison-tbody"></tbody>
            </table>
          </div>
          <div id="comparison-summary" class="comparison-summary" aria-live="polite">
          </div>
        </section>

      </main>

      <!-- RIGHT: History sidebar -->
      <aside id="history-sidebar"
             aria-label="Evaluation history">
        <div class="sidebar-header">
          <span class="sidebar-title">History</span>
          <button id="sidebar-toggle"
                  aria-label="Collapse history sidebar"
                  aria-expanded="true"
                  aria-controls="history-sidebar">
            &#8249;
          </button>
        </div>
        <div class="sidebar-search">
          <label for="history-search" class="sr-only">
            Filter evaluation history
          </label>
          <input type="search"
                 id="history-search"
                 placeholder="Search history..."
                 aria-label="Filter evaluation history">
        </div>
        <div id="history-compare-bar" class="history-compare-bar" hidden>
          <span id="compare-count" aria-live="polite">0 selected</span>
          <button id="compare-btn"
                  class="btn btn-secondary btn-sm"
                  disabled
                  aria-disabled="true">
            Compare
          </button>
        </div>
        <ul id="history-list"
            role="list"
            aria-label="Past evaluations"
            aria-live="polite">
          <!-- items inserted by JS using safe DOM methods -->
        </ul>
      </aside>

    </div>
  </div>

  <!-- MODELS VIEW -->
  <div id="view-models" class="view" hidden>
    <main role="main" class="single-col-main">
      <div class="view-header">
        <h1>Registered Models</h1>
        <button id="health-check-all-btn" class="btn btn-secondary">
          Check All Health
        </button>
      </div>
      <!-- Model tables per type inserted by JS from GET /api/models -->
      <div id="models-content"></div>
    </main>
  </div>

  <!-- LEADERBOARD VIEW -->
  <div id="view-leaderboard" class="view" hidden>
    <main role="main" class="single-col-main">
      <h1>Leaderboard</h1>
      <div id="leaderboard-content"></div>
    </main>
  </div>

  <!-- TOOLTIP POPUP (positioned by JS, shared across all triggers) -->
  <div id="tooltip-popup"
       class="tooltip-popup"
       role="tooltip"
       hidden>
  </div>

  <script src="/static/app.js"></script>
</body>
</html>
```

---

## 5. CSS Variables and Theme

```css
/* style.css */

:root {
  /* Background scale */
  --bg-base:    #09090b;   /* page background */
  --bg-card:    #18181b;   /* cards, panels */
  --bg-raised:  #1f1f22;   /* hover, active states */

  /* Border */
  --border:     #27272a;

  /* Text -- all checked for WCAG AA against #09090b */
  --text-primary:   #fafafa;  /* 18.7:1 ratio -- passes AAA */
  --text-secondary: #a1a1aa;  /* 5.9:1 ratio -- passes AA */
  --text-dim:       #71717a;  /* 3.5:1 ratio -- use only for decorative/large text */

  /* Accent */
  --accent:       #3b82f6;   /* blue-500, 4.5:1 on #09090b -- passes AA */
  --accent-hover: #2563eb;

  /* Status -- always paired with text label or icon, never color alone */
  --success:  #22c55e;   /* green-500 */
  --failure:  #ef4444;   /* red-500 */
  --pending:  #71717a;   /* zinc-500 */
  --running:  #f59e0b;   /* amber-500 */

  /* Layout */
  --sidebar-width:       260px;
  --sidebar-collapsed:   48px;
  --stage-card-minwidth: 200px;

  /* Focus ring -- high visibility */
  --focus-ring: 0 0 0 2px #3b82f6;
}

/* Respect user motion preference */
@media (prefers-reduced-motion: reduce) {
  .spinner { animation: none; }
  .progress-bar { transition: none; }
}

/* Screen reader only utility */
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

/* Focus management -- never suppress outline */
:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

/* Layout */
body {
  background: var(--bg-base);
  color: var(--text-primary);
  font-family: system-ui, sans-serif;
  margin: 0;
}

.layout-three-col {
  display: grid;
  grid-template-columns: 1fr var(--sidebar-width);
  gap: 0;
  min-height: calc(100vh - 56px); /* subtract nav height */
}

.stage-cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(var(--stage-card-minwidth), 1fr));
  gap: 12px;
}

/* Responsive: stage cards stack below 900px */
@media (max-width: 900px) {
  .stage-cards {
    grid-template-columns: 1fr 1fr;
  }
}

@media (max-width: 600px) {
  .stage-cards {
    grid-template-columns: 1fr;
  }
  .layout-three-col {
    grid-template-columns: 1fr;
  }
}
```

---

## 6. app.js -- Event Handling and API Integration

### State Model

The dashboard manages all UI state in a plain JS object. No framework. State changes trigger explicit DOM updates.

```javascript
// app.js

const state = {
  models: null,           // response from GET /api/models
  currentEvalId: null,    // active evaluation_id from POST response
  sse: null,              // EventSource instance, closed when eval completes
  history: [],            // array of EvalSummary from GET /api/evaluations
  selectedForCompare: [], // evaluation_id strings checked in sidebar
  activeView: 'dashboard',
  expandedRow: null,      // combination key for expanded step detail
};
```

### Safe DOM Construction Pattern

All dynamic content is built with `document.createElement` and `textContent` for text nodes. This prevents XSS from API responses. No `innerHTML` with server-provided data.

```javascript
// CORRECT: safe DOM construction
function createResultRow(result, rank) {
  const tr = document.createElement('tr');
  tr.tabIndex = 0;
  tr.setAttribute('aria-label', `${result.vlm_id} plus ${result.vla_id}, rank ${rank}`);

  const cells = [
    String(rank),
    `${result.vlm_id} + ${result.vla_id}`,
    result.success ? 'YES' : 'NO',
    result.sim_success ? 'YES' : 'NO',
    result.judge_calibration ? 'YES' : 'NO',
    result.total_latency_ms ? `${result.total_latency_ms}` : 'FAILED',
    result.total_cost_usd != null ? `$${result.total_cost_usd.toFixed(3)}` : '--',
  ];

  cells.forEach(text => {
    const td = document.createElement('td');
    td.textContent = text;  // textContent, never innerHTML
    tr.appendChild(td);
  });

  tr.addEventListener('click', () => expandStepDetail(result));
  tr.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      expandStepDetail(result);
    }
  });

  return tr;
}

// NEVER do this with server data:
// element.innerHTML = `<td>${result.vlm_id}</td>`;
```

### Key Event Handlers

```javascript
// Navigation between views
document.querySelectorAll('[data-view]').forEach(btn => {
  btn.addEventListener('click', () => switchView(btn.dataset.view));
});

function switchView(viewName) {
  document.querySelectorAll('.view').forEach(el => {
    el.hidden = el.id !== `view-${viewName}`;
  });
  document.querySelectorAll('.nav-btn').forEach(btn => {
    const isCurrent = btn.dataset.view === viewName;
    btn.classList.toggle('active', isCurrent);
    btn.setAttribute('aria-current', isCurrent ? 'page' : 'false');
  });
  state.activeView = viewName;
  if (viewName === 'models') loadModelsView();
  if (viewName === 'leaderboard') loadLeaderboardView();
}

// Preset dropdown: fills stage selectors from named evaluation config
document.getElementById('preset-select').addEventListener('change', async (e) => {
  if (!e.target.value) return;
  const resp = await fetch(`/api/evaluations/presets/${e.target.value}`);
  const preset = await resp.json();
  applyPresetToStages(preset);
});

// Perceive VLM change -> update Verify label text
document.getElementById('vlm-perceive-select').addEventListener('change', (e) => {
  const opt = e.target.options[e.target.selectedIndex];
  // opt.textContent is from our own option element, safe to use
  document.getElementById('verify-vlm-name').textContent = opt.textContent;
});

// Plan "same as perceive" checkbox
document.getElementById('plan-same-as-perceive').addEventListener('change', (e) => {
  document.getElementById('plan-model-row').hidden = e.target.checked;
});

// Image drop zone: keyboard + mouse + drag support
const dropZone = document.getElementById('image-drop-zone');

dropZone.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    document.getElementById('image-file-input').click();
  }
});

dropZone.addEventListener('click', () => {
  document.getElementById('image-file-input').click();
});

dropZone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropZone.classList.add('drag-over');
  dropZone.setAttribute('aria-label', 'Release to upload image');
});

dropZone.addEventListener('dragleave', () => {
  dropZone.classList.remove('drag-over');
  dropZone.setAttribute('aria-label',
    'Upload scene image. Press Enter or Space to browse files.');
});

dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file) handleImageFile(file);
});

document.getElementById('image-file-input').addEventListener('change', (e) => {
  if (e.target.files[0]) handleImageFile(e.target.files[0]);
});

function handleImageFile(file) {
  if (!file.type.startsWith('image/')) {
    showValidationError('Please upload a JPEG or PNG image file.');
    return;
  }
  const reader = new FileReader();
  reader.onload = (e) => {
    const preview = document.getElementById('image-preview');
    const prompt = document.getElementById('image-upload-prompt');
    preview.src = e.target.result;
    preview.alt = `Uploaded scene image: ${file.name}`;
    preview.hidden = false;
    prompt.hidden = true;
    dropZone.setAttribute('aria-label',
      `Scene image loaded: ${file.name}. Click to change.`);
  };
  reader.readAsDataURL(file);
}
```

### SSE Handler

```javascript
async function startEvaluation() {
  const validation = validateForm();
  if (!validation.valid) {
    showValidationError(validation.message);
    return;
  }

  const requestBody = buildEvaluationRequest();
  const resp = await fetch('/api/evaluations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(requestBody),
  });

  if (!resp.ok) {
    const err = await resp.json();
    // err.detail is a server-provided string -- display safely
    showValidationError(err.detail || 'Failed to start evaluation.');
    return;
  }

  const data = await resp.json();
  state.currentEvalId = data.evaluation_id;

  showProgressSection(data);
  startSSE(data.evaluation_id);
}

function startSSE(evalId) {
  // Close any existing SSE connection
  if (state.sse) state.sse.close();

  state.sse = new EventSource(`/api/evaluations/${evalId}/stream`);

  state.sse.addEventListener('step_complete', (e) => {
    const data = JSON.parse(e.data);
    // data shape: {combination: "vlm_id+vla_id", step: str, latency_ms: number}
    updateCombinationCardStep(data.combination, data.step, 'complete', data.latency_ms);
  });

  state.sse.addEventListener('combination_done', (e) => {
    const data = JSON.parse(e.data);
    markCombinationDone(data);
    incrementProgress();
  });

  state.sse.addEventListener('combination_failed', (e) => {
    const data = JSON.parse(e.data);
    markCombinationFailed(data);
    incrementProgress();
  });

  state.sse.addEventListener('evaluation_done', (e) => {
    const data = JSON.parse(e.data);
    state.sse.close();
    state.sse = null;
    loadAndDisplayResults(data.evaluation_id);
    refreshHistory();
  });

  state.sse.addEventListener('error', () => {
    if (state.sse.readyState === EventSource.CLOSED) {
      showValidationError(
        'Connection to server lost. Your evaluation may still be running. ' +
        'Refresh the page to check the history sidebar for results.'
      );
    }
    // If readyState is CONNECTING, EventSource will retry automatically.
  });
}
```

### Combination Card Construction (Safe DOM)

```javascript
function createCombinationCard(vlmId, vlaId) {
  const steps = ['perceive', 'ground', 'plan', 'execute', 'verify'];
  const cardKey = makeSafeId(vlmId, vlaId);

  const card = document.createElement('div');
  card.className = 'combination-card combination-card--pending';
  card.id = `card-${cardKey}`;
  card.setAttribute('aria-label', `${vlmId} + ${vlaId}: pending`);

  const header = document.createElement('div');
  header.className = 'combo-card-header';

  const nameSpan = document.createElement('span');
  nameSpan.className = 'combo-name';
  nameSpan.textContent = `${vlmId} + ${vlaId}`;  // textContent: safe

  const statusSpan = document.createElement('span');
  statusSpan.className = 'combo-status-icon';
  statusSpan.setAttribute('aria-label', 'pending');
  statusSpan.textContent = '--';

  header.appendChild(nameSpan);
  header.appendChild(statusSpan);

  const stepList = document.createElement('ol');
  stepList.className = 'step-list';
  stepList.setAttribute('aria-label', 'Pipeline steps');

  steps.forEach(step => {
    const li = document.createElement('li');
    li.id = `${card.id}-${step}`;
    li.className = 'step-item step-item--pending';
    li.setAttribute('aria-label', `${step}: pending`);

    const stepName = document.createElement('span');
    stepName.className = 'step-name';
    stepName.textContent = step;  // safe: step name is from our own constant array

    const stepStatus = document.createElement('span');
    stepStatus.className = 'step-status-icon';
    stepStatus.setAttribute('aria-hidden', 'true');
    stepStatus.textContent = '--';

    const stepLatency = document.createElement('span');
    stepLatency.className = 'step-latency';

    li.appendChild(stepName);
    li.appendChild(stepStatus);
    li.appendChild(stepLatency);
    stepList.appendChild(li);
  });

  const totalDiv = document.createElement('div');
  totalDiv.className = 'combo-total';
  totalDiv.setAttribute('aria-live', 'polite');

  card.appendChild(header);
  card.appendChild(stepList);
  card.appendChild(totalDiv);

  return card;
}

function makeSafeId(vlmId, vlaId) {
  return `${vlmId}-${vlaId}`.replace(/[^a-z0-9-]/g, '-').toLowerCase();
}

function updateCombinationCardStep(combinationKey, stepName, status, latencyMs) {
  // combinationKey from SSE: "vlm_id+vla_id"
  const [vlmId, vlaId] = combinationKey.split('+');
  const cardId = `card-${makeSafeId(vlmId, vlaId)}`;
  const stepEl = document.getElementById(`${cardId}-${stepName}`);
  if (!stepEl) return;

  stepEl.className = `step-item step-item--${status}`;
  stepEl.setAttribute('aria-label', `${stepName}: ${status}, ${latencyMs}ms`);

  const latencyEl = stepEl.querySelector('.step-latency');
  if (latencyEl) latencyEl.textContent = `${latencyMs}ms`;  // safe: latencyMs is a number

  const statusIcon = stepEl.querySelector('.step-status-icon');
  if (statusIcon) {
    statusIcon.textContent = status === 'complete' ? 'done' : status;
  }
}
```

---

## 7. Tooltip Content Definitions

These are the plain-English explanations for metric column headers. Content is stored as a JS constant object (not from the server) so it is safe to set as textContent.

```javascript
const TOOLTIP_TEXT = {
  'success': (
    'Did the AI vision model (VLM) determine the task was completed? ' +
    'This is the VLM\'s opinion -- it may differ from the physics simulation. ' +
    'Compare with Sim Success to see how well the VLM judges its own results.'
  ),
  'sim-success': (
    'Did the robot physics simulation confirm the task was physically completed? ' +
    'This is the objective ground truth: the simulator checked actual object ' +
    'positions after the robot moved.'
  ),
  'judge-calibration': (
    'Did the VLM\'s verdict match the simulator\'s ground truth? ' +
    'YES means the VLM correctly identified success or failure. ' +
    'NO means the VLM was wrong about the outcome. ' +
    'High calibration means you can trust VLM verification even without ' +
    'a simulator -- important for production deployment where you may not ' +
    'always have physics simulation available.'
  ),
};
```

---

## 8. Accessibility Compliance Checklist

Based on RAI-ADR-006 requirements and WCAG 2.1 AA:

### Keyboard Navigation

- All interactive elements reachable by Tab in logical reading order.
- Stage card dropdowns: native `<select>` -- keyboard accessible by default.
- Image drop zone: `role="button"`, `tabindex="0"`, Enter/Space activates file picker.
- History list items: `tabindex="0"`, Enter activates, Space toggles compare checkbox.
- Tooltip triggers: `<button>` elements (not `<div>`), focus shows tooltip, Escape closes.
- Table rows: `tabindex="0"`, Enter/Space expands step detail.
- Sidebar toggle: button with `aria-expanded` and `aria-label` updates on collapse.
- Modal-like sections (comparison view): focus moves to first interactive element on open.

### Screen Reader Annotations

- `aria-live="polite"` on: progress bar label, history list, results meta, comparison summary.
- `aria-label` on all icon-only buttons and status icons.
- `<caption>` on both results tables.
- `role="alert"` on validation message div (fires immediately on error).
- Status indicators in Models view: text labels "OK" / "OFF" alongside visual dot.
- Step state changes in combination cards: `aria-label` on `<li>` updated each SSE event.
- Verify stage "auto-assigned" badge: screen reader reads "Auto-assigned" not just the badge text.

### Color and Contrast

- All text colors verified against `#09090b` background (ratios in CSS token comments).
- Success/failure status: conveyed by text label AND icon AND color, never color alone.
- Results table YES/NO cells: text is the primary carrier; green/red is supplementary.
- Spinner: if `prefers-reduced-motion` is set, replaced with a static "..." text indicator.

### Touch Targets

- Drop zone minimum 44x44px.
- Run button minimum height 44px.
- All sidebar checkboxes minimum 44px touch target via CSS padding.
- Tooltip buttons: minimum 24px, with 10px padding each side.

### Responsive

- Below 1024px: sidebar collapses to narrow icon rail by default.
- Below 900px: stage cards go to 2-column grid.
- Below 600px: stage cards stack vertically; sidebar hidden behind a "History" button.

---

## 9. User Flow Analysis: Pain Points and Mitigations

### Pain Point 1: First-time user does not know model IDs

Mitigation: The `<select>` dropdowns show `display_name` ("GPT-4o", "SmolVLA 450M") from the API, not the internal `model_id`. The field-hint below each stage card explains in one sentence what that stage does. The preset dropdown lets a user start from a known configuration from `rove.yaml`.

### Pain Point 2: User does not understand "VLM only" mode

Mitigation: Radio button labels use plain English: "Full pipeline -- runs simulator, produces success rate" vs "VLM only -- skips robot simulator, compares perception and planning". The word "simulator" is explained as running robot physics.

### Pain Point 3: 60-second evaluation with no visible feedback

Mitigation: SSE stream opens immediately after the 202 response. Combination cards appear in pending state before any work begins. The first `step_complete` event arrives within the latency of the first perceive call (typically under 500ms for cloud VLMs). Progress bar updates with each combination completion.

### Pain Point 4: "Judge Calibration" metric is opaque

Mitigation: Every metric column header has a `?` button with a plain-English tooltip (see section 7). The tooltip explains the metric, why it matters, and what a YES or NO result means for production use.

### Pain Point 5: Comparing two runs requires remembering both results

Mitigation: History sidebar checkboxes allow selecting 2 items. The "Compare" button is disabled (and `aria-disabled`) until exactly 2 are selected. The comparison view presents a side-by-side metric table and a plain-English summary sentence that names the metric count and the main tradeoff (e.g., cost vs calibration). The summary avoids declaring a winner.

### Pain Point 6: Laptop screen loses history sidebar access

Mitigation: The sidebar collapses to an icon rail showing colored status badges only. Clicking any badge expands the full sidebar. Collapse/expand state is saved to `localStorage` so it persists across page refreshes.

### Pain Point 7: User on mobile cannot see the pipeline configuration layout

Mitigation: On screens below 600px, the stage cards stack vertically. The sidebar is hidden behind a "History" floating button at the bottom of the screen. This is not an optimal mobile experience, but the tool is desktop-first. A note in the UI ("Best viewed on a desktop browser") appears on screens below 480px.

---

## 10. Open Questions for Product Manager and Responsible AI

These require cross-team input before final implementation.

### For Product Manager

1. **Saving presets from dashboard**: The "Load Preset" dropdown reads from `rove.yaml`. Should the dashboard also allow saving the current configuration as a new named evaluation back to the YAML? Or is YAML editing always the authoring mechanism for named evaluations?

2. **Comparison: 2 vs N runs**: The current comparison view supports exactly 2 runs. Is there a product case for 3+ run comparison in Phase 1? A different table layout (metric rows, N columns) would be needed.

3. **History persistence**: Should history rebuild from `GET /api/evaluations` on every page load (accurate but slower on large histories), or should a localStorage cache be used for initial render with a background sync?

### For Responsible AI

1. **Judge Calibration tooltip wording**: The current tooltip says "High calibration means you can trust VLM verification even without a simulator -- important for production deployment." Does this overstate the conclusion? RAI-ADR-003 flags the risk of over-trusting VLM-as-judge. Recommend review.

2. **Ranking transparency**: The note "Ranked by: success first, then latency, then cost" appears above the results table. Is this sufficient for RAI-ADR-001 compliance, or should the dashboard link to a fuller explanation of the ranking methodology?

3. **Comparison summary wording**: The current summary ("5 of 8 metrics favor Run A. Run B costs 2.6x less.") avoids declaring a winner. Does this phrasing meet the standard for non-automated model selection (RAI-ADR-001)? Should the summary include an explicit "Review all metrics before selecting a model" advisory?

---

## 11. Implementation Priority for Phase 1

Phase 1 delivers mock adapters only. Dashboard must be fully functional with mock data.

### Must Have (Phase 1)

- Full HTML structure as specified in section 4
- `GET /api/models` populates all dropdowns with display names and health status
- `GET /api/evaluations` returns list for history sidebar
- A new endpoint `GET /api/evaluations/presets` returns named evaluations from `rove.yaml` (config only, no execution)
- `POST /api/evaluations` with 202 response and eval_id
- SSE stream drives combination card animation (pending -> running -> complete / failed)
- Ranked results table with safe DOM construction
- Expandable step detail panel
- History sidebar with checkbox-based comparison selection
- Comparison view for 2 runs
- Models view (populated from `GET /api/models`)
- All keyboard navigation on interactive elements
- All ARIA labels and live regions
- Tooltip on Judge Calibration, Sim Success, and Success columns

### Phase 1 If Time Allows

- Search filter in history sidebar
- Leaderboard aggregate view
- Export JSONL button wiring
- Sidebar collapse state saved to localStorage

### Phase 2 (Requires Real Models / Sim)

- Before/after image thumbnails in verify step detail (requires real sim output)
- Action chunk step visualization (bar per action step in execute detail)
- Latency breakdown stacked bar chart per step
- Dark/light mode toggle (per RAI-ADR-006 factory lighting concern)

---

*ROVE UX Design -- Dashboard v1.0*
*Author: UX Designer, 2026-02-21*
*Review requested: Product Manager (section 10), Responsible AI (section 10)*
