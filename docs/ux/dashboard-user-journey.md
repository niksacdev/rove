# User Journey: ROVE Dashboard -- Configure, Run, and Compare

**Date**: 2026-02-21 (updated 2026-02-22)
**Author**: UX Designer
**Feature**: Dashboard v1 (Phase 1)
**Related document**: `docs/ux/dashboard-design.md`

---

## User Personas

Three personas use the dashboard to evaluate robotics agent pipelines in different ways.

| Persona | Role | Dashboard Entry Point | Primary Goal |
|---------|------|-----------------------|--------------|
| A: Robotics Researcher | Academic or company lab | After a CLI run, to visualize results | See ranked comparison of agent configurations |
| B: ML Engineer | Manipulation product team | Dashboard-first for ad hoc exploration | Configure pipeline, run inference, see live progress |
| C: Platform Admin | Azure AI Foundry / infrastructure | Checking model health | See which adapters are registered and reachable |

---

## Current State Journey (Before ROVE Dashboard)

### ML Engineer (Persona B) -- Current State

1. **Awareness**: Engineer realizes they need to compare GPT-4o vs Qwen2.5-VL as the VLM in their pick-and-place agent pipeline.
   - Pain point: no tool exists for testing full agent pipelines. Must write custom scripts.
   - Emotion: frustrated at the tooling gap.

2. **Setup**: Write a Python script to call each model API, wire the pipeline, parse the output, run simulation.
   - Pain point: 2-3 days of custom instrumentation per model combination.
   - Drop-off risk: many engineers give up and test just one pipeline configuration informally.

3. **Execution**: Run scripts manually, collect JSONL or CSV output.
   - Pain point: no live progress feedback. No consistent output format.
   - Emotion: uncertain -- did it succeed? Did latency spike?

4. **Analysis**: Paste results into a spreadsheet or Jupyter notebook.
   - Pain point: comparison requires manual column alignment. No side-by-side view.
   - Emotion: tedious, error-prone.

5. **Decision**: Pick a model based on informal comparison.
   - Pain point: decision is not reproducible. Colleague cannot re-run the same evaluation.
   - Risk: wrong model chosen due to incomplete comparison.

---

## Future State Journey (With ROVE Dashboard)

### Journey 1: ML Engineer -- Ad Hoc Run (Most Common)

**Start**: Engineer opens ROVE dashboard at `http://localhost:8000`.

#### Step 1: Understand the tool (5-second test)

- Sees: "Pipeline Configuration" with 4 stage cards (Perceive, Plan, Act, Verify).
- Sees: "Task Input" section below.
- Understands immediately: this is a model selector + task runner.
- Pain point eliminated: no onboarding required.
- Improvement over current state: self-evident layout vs blank script editor.

#### Step 2: Configure models

- Opens the Perceive dropdown. Sees "GPT-4o", "Qwen3-VL-8B", "Mock VLM" with health dots.
- Selects "GPT-4o". Sees "Verify: uses GPT-4o" update automatically.
- Checks "Also compare" checkbox for "Qwen3-VL-8B" to run both VLMs.
- Selects "SmolVLA 450M" in Act. Checks "Also compare: CogACT 7B".
- Result: 4 combinations queued (2 VLMs x 2 VLAs).
- Improvement: no code. Under 60 seconds to configure.

#### Step 3: Upload image and type task

- Drags bracket.jpg into the drop zone. Preview appears.
- Types: "Pick the red bracket and place it in bin A".
- Reads field hint: "Name the object, state the target location..."
- Confirms mode: "Full pipeline".
- Clicks "Run Evaluation".
- Improvement: one button to start. No CLI arguments to remember.

#### Step 4: Watch live progress

- Button disappears. Progress section appears.
- 4 combination cards appear in pending state.
- Within 1 second: GPT-4o + SmolVLA shows perceive step as spinning.
- Within 500ms more: perceive completes, shows "412ms".
- All 4 combinations run in parallel. Cards animate step by step.
- Engineer monitors without needing to poll or tail a log file.
- Improvement: real-time visual feedback vs. silent script execution.

#### Step 5: Read results

- All combinations complete. Ranked table appears.
- Row 1: GPT-4o + SmolVLA -- SUCCESS, sim SUCCESS, calibrated, 1842ms, $0.021.
- Engineer clicks row 1 to see step detail. Reads plan reasoning from GPT-4o.
- Row 3: Qwen3-VL + SmolVLA -- VLM says SUCCESS, sim says NO, calibration FAIL.
- Reads tooltip on Judge Calibration: understands Qwen3-VL misread the outcome.
- Improvement: one ranked view vs. manual spreadsheet assembly.

#### Step 6: Compare with previous run

- Checks checkbox on current run in history sidebar.
- Checks checkbox on a previous run with a different task phrasing.
- Clicks "Compare". Side-by-side table appears.
- Sees: "5 of 8 metrics favor Run A. Run B costs 2.6x less."
- Makes decision: GPT-4o + SmolVLA for this task. Documents in team notes.
- Improvement: instant comparison vs. days of custom analysis.

---

### Journey 2: Robotics Researcher -- Load Preset

**Start**: Researcher has defined `pick_bracket` in `rove.yaml` with 3 VLMs and 2 VLAs.

#### Step 1: Open dashboard, load preset

- Opens "Load Preset" dropdown. Sees "pick_bracket", "bracket_variations", "vlm_only_perception".
- Selects "pick_bracket". Stage selectors auto-fill with GPT-4o, Qwen2.5-VL, Cosmos-Reason2, CogACT, SmolVLA.
- Task text and image path also pre-populate (if image is local, upload required separately).
- Improvement: no manual re-configuration. The YAML is the source of truth.

#### Step 2: Run and monitor

- Clicks "Run Evaluation".
- 6 combination cards appear (3 VLMs x 2 VLAs).
- Monitors live progress. Cosmos-Reason2 is slower (local MLX); cards show it visually.

#### Step 3: Export results

- All combinations complete. Ranked table shows.
- Clicks "Export JSONL". Downloads Foundry-compatible JSONL.
- Uses in Jupyter notebook for further analysis.
- Improvement: structured export vs. manual output parsing.

---

### Journey 3: Platform Admin -- Health Check

**Start**: Admin opens ROVE to verify which models are operational.

#### Step 1: Navigate to Models view

- Clicks "Models" in nav. Models view loads.
- Sees table: VLMs, VLAs, Grounding models, Simulators.
- Each row shows: ID, display name, type, cost, health status (OK / OFF as text + icon).

#### Step 2: Check all health

- Clicks "Check All Health". Health status updates in-place via `GET /api/models` refresh.
- Sees CogACT 7B is OFF (Azure GPU endpoint not running).
- Takes note. Will update `rove.yaml` or start the endpoint.

#### Step 3: Done

- Admin navigates away. No evaluation needed.
- Improvement: single view for all model health. No need to SSH to check endpoints.

---

## Pain Points Resolved

| Current Pain Point | ROVE Dashboard Solution |
|--------------------|------------------------|
| Days of custom script writing per agent pipeline | Pre-configured adapter pattern, one dropdown per pipeline stage |
| No live progress feedback | SSE stream drives per-step card animation |
| Manual result assembly in spreadsheets | Ranked table auto-generated, comparison built-in |
| Non-reproducible evaluations | Named presets in rove.yaml, export to JSONL |
| ML jargon barriers for non-ML users | Field hints, plain-English mode labels, metric tooltips |
| Color-only status indicators | Text label + icon + color always together |
| No keyboard access to model toggles | All dropdowns and checkboxes keyboard accessible |

---

## Drop-Off Risks

| Step | Drop-Off Risk | Mitigation |
|------|---------------|------------|
| Configure models | User does not know which model to pick | Preset dropdown offers working starting points |
| Upload image | User uploads wrong format | Validation message on file type, accepts JPEG/PNG/WebP |
| Run | User clicks Run before uploading image | Validation message appears inline, not as an alert dialog |
| Live progress | SSE drops (network issue) | Error message explains that evaluation may still be running; prompts user to check history |
| Results | User confused by Judge Calibration metric | Tooltip explains in plain English |
| Comparison | User selects only 1 run | Compare button stays disabled with aria-disabled until 2 selected |

---

## Implementation Tasks from This Journey

- [ ] Stage cards with dropdown population from `GET /api/models`
- [ ] Health dot (text + icon) next to each model option in dropdown
- [ ] "Also compare" checkboxes per stage that add combinations
- [ ] Verify stage auto-updates when Perceive selection changes
- [ ] Preset dropdown populates from `GET /api/evaluations/presets`
- [ ] Image drop zone with keyboard access, preview, and format validation
- [ ] Field hints below every stage card and input field
- [ ] Mode radio buttons with plain-English labels
- [ ] Inline validation message on run (not alert dialog)
- [ ] Progress section with combination cards and SSE animation
- [ ] Ranked results table with safe DOM construction
- [ ] Expandable step detail per table row
- [ ] Metric tooltips on Success, Sim Success, Judge Calibration
- [ ] History sidebar with checkbox selection and Compare button
- [ ] Comparison view with metric table and plain-English summary
- [ ] Models view with health table
- [ ] Keyboard navigation on all elements
- [ ] ARIA live regions on progress, results, history

---

*ROVE Dashboard User Journey -- v1.0*
*Author: UX Designer, 2026-02-21*
