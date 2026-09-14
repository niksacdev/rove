# Changelog

All notable changes to ROVE are documented in this file.

Format: each entry includes the date, a short title, and a description of what capability was added or changed. Entries are ordered newest-first. Unreleased changes appear at the top.

---

## Unreleased

### Configure and test the evaluation assistant

Campaign results now link to a real Assistant settings section with persistent endpoint selection, explicit connection testing and a return to the campaign. Missing configuration offers setup instead of a useless retry; summaries request exact evidence citations and retain strict validation. The same controls are available through the CLI. *(PR #31)*

### Follow live strategies into their recorded evidence

Keep all strategies reachable while inspecting one trial, and open its exact trace without interrupting execution. Campaign explanations now use saved stage failures, timing and measurement evidence to distinguish setup repairs from proposed model changes. Explicit local Qwen strategies and an experimental isolated SmolVLA runtime restore real inference while preserving unknown evidence and blocking image-only probes from simulation or dynamics. *(PR #30)*

### Review a trial before execution

Trial setup now progresses from Case to Configure to Review & run, with one next action per step. Review shows the task, image, robot file and strategies together; Run trials is the only execution action. Incomplete steps are disabled, edits invalidate the reviewed configuration, and leaving an unsaved draft warns before navigation. Results remain attached to the completed execution. Existing pipeline execution and recording are preserved.

### Share the trial and campaign workspace

Trials and Campaigns now consistently open their record browsers, with New trial and New campaign actions. Quick comparisons keep their task, image and robot description through Case, Configure, Run and Results. Both workflows use the same strategy selection table and existing execution services. Campaign name, attempts and live trial totals stay above the steps; saved campaigns show their frozen setup. Success criteria identify the pass condition and checking method, configuration checks report their status beside Run, and a new draft shows a clear empty Results state. Active-run history navigation preserves live output.

### Align the workspace with Azure AI Foundry

Use one Foundry-inspired Fluent layer across trials, campaigns, configuration, results and exported HTML reports. Neutral light/dark surfaces, restrained purple actions, flat navigation and aligned campaign columns replace conflicting page styles. Trials use the same full-width browsing pattern, with details opened on selection. Improvement cards identify the strategy, affected trials and direct next actions. A live trial calculator, explicit criteria and assessment methods, campaign/strategy progress, scoped Results tabs and separate Campaign history clarify the workflow. Settings is a global top-right gear. Static asset revalidation prevents mixed renderer/style versions. An optional local Copilot assistant connection supports reviewed drafts. Existing evaluation execution, evidence and approval flows are preserved. *(PR #27)*

### Make the evaluation workspace easier to scan

Campaigns now appear as compact rows with View results, Export and Improve controls. Improvements open a focused, evidence-linked strategy change with preview and explicit approval. The trial workspace uses neutral surfaces, unclipped strategy details, larger sample previews and consistent sidebar actions, with Settings below Sample cases.

Local VLA import failures now expose their actual dependency cause. The model-loading code remains present; the current LeRobot runtime is still incompatible with the secured dependency stack. *(PR #26)*

### Import ABC robotics episodes as versioned cases

Build on XDOF and the ABC project's exported episodes through Cases → Import cases or the CLI. Preview a selected camera observation and robot state, preserve the original episode files and source identity, then compare strategies through existing campaigns. Demonstrations stay separate from candidate outcomes, with missing calibration and robot-model evidence made explicit. *(PR #25)*

### Compare robotics strategies and track evaluation iterations

Start with a task, image and robot description, compare strategies, then reuse a saved trial in a campaign with editable criteria, automatic results and a preserved baseline. Connected timelines show how cases, scoring and strategies change across iterations, while the CLI supports the same evaluation, curation and reporting operations. A GitHub Actions example restores selected history and publishes reports with explicit quality gates. *(PR #24)*

### Success metrics and outcome-first results

The accepted campaign refinement separates Cases, Configure, Success metrics, Run and Results. Reviewable assistant drafts help define assessment; each strategy has its own progress, and charts precede an optional outcome explanation and expandable trial evidence. Existing campaign execution and scoring stay authoritative. An isolated six-trial mock campaign and narrow Results layout verified the new journey; AI generation is tested with a fake runtime. The [journey specification](docs/product/user-journey.md) records evidence and limits.

### Compare strategies and preserve candidate revisions

The campaign workflow supports selecting several strategies for the same cases, with a separate result for every strategy. The strategy-revision addition preserves a changed candidate under a distinct identity for baseline comparisons; [ADR-026](docs/architecture/ADR-026-versioned-strategy-catalog.md) records its configuration boundary and current verification status.

### A campaign workspace with visible cases and trials

The revised workflow connects case selection, configured strategies, success criteria, trial execution and evidence review in one campaign. A searchable image gallery and selected-case cards replace the case dropdown; Settings moves to the right, with warm neutral and teal styling. Exact case expectations are saved with scoring rules, collection revisions retain prior members, and Run connects each trial to its recorded stages and output. Review reads saved evidence; Set as baseline establishes a comparison reference, with a Baseline badge shown only after saving it. The [journey specification](docs/product/user-journey.md) records validation and its limits.

### A coherent evaluation journey

Use consistent Start, Evaluate, Results and Configure navigation across the workspace. Prepare cases, run a campaign and review improvements in connected steps; saved campaigns and their trials share Results, while quick trials and advanced configuration remain available without competing with the main path. *(PR #21)*

### Complete the local robotics evaluation workflow

Inspect clock-aware SDK/tool traces and preserved recording selections, execute action-dependent synthetic trials, and bind frozen SME annotations to graders. Named baseline revisions, confirmed assistant workflows, campaign targets and relational exchange connect evaluation results to reproducible evidence. Live provider/Azure validation remains specification-only. *(PR #20)*

### Sample cases and clearer campaign reports

All 49 bundled gallery samples now become versioned Cases with original source metadata and private reference annotations preserved. Campaign reports use ROVE themes, outcome and coverage cards, reliability bounds and expandable evidence; the implementation plan now distinguishes delivered local capabilities from unfinished acceptance criteria. *(PR #19)*

### Customer evaluation workflow

Import customer cases, define success contracts, review outputs, freeze dataset revisions and compare a candidate with its baseline. The optional assistant previews campaigns and requests confirmed launches. *(PR #18)*

### Durable trials and hosted runtime

Quick evaluations and campaigns retain trial identities, frozen configuration and available trace evidence in durable history. Optional Copilot stages run through the shared runtime while existing customer adapters remain directly evaluable. *(PR #17)*

### Copilot runtime and evaluation-system architecture

Define Copilot SDK as the shared runtime for ROVE-owned agents while preserving direct evaluation of customer systems. New architecture, system and delivery diagrams connect durable trial evidence to optional Azure Monitor, Grafana and future Fabric integrations; all runtime work remains explicitly pending. *(PR #16)*

### Product concepts and evaluation architecture

Update the product specification around the robotics agent-pipeline vision, cases, trials, campaigns and customer-data onboarding, with a provider-neutral platform persona. Linked specifications and architecture decisions explain SME-reviewed datasets, robotics measures, inspectable telemetry and ROVE's own evaluation/execution harness, with diagrams and a dated model assessment. Proposed capabilities remain separate from shipped behavior. *(PR #15)*

### Configured verification stages

Extend stage configuration with local task evaluators, required constraints, optional FK diagnostics, execution deadlines and versioned measurement evidence. *(PR #14)*

### Repeated trials and portable benchmark reports

Saved task suites can now run repeatedly across configured pipelines, with pass@k, pass^k, cross-model coverage and comparable history. The dashboard, CLI and local API share durable campaign records and offline reports; missing verdicts remain unknown and mock results remain explicitly synthetic. *(PR #13)*

### OSS evidence workbench and safer local defaults

Integrates the newer evaluation branch with provenance, stage comparisons and action-space metadata. Missing physics evidence now stays unknown throughout reports, prompts and the dashboard; torque/payload feasibility is explicitly unavailable pending a validated physical contract. Local access restrictions, refreshed dependencies, CI and asset notices prepare the code for an OSS release. Historical repository publication remains a separate cleanup step. *(PR #12)*

## 2026-03-03 (development branch notes)

The following notes describe the earlier development state; current behavior and limits are documented in README.md.

### Agentic Verifier with Multi-Turn Function Calling

The verify stage now runs as a multi-turn agentic loop (up to 4 turns) using OpenAI native function calling via the Responses API. Instead of a single-shot prompt, the verifier can call `perceive` and `compute_dynamics` as tools mid-conversation, building evidence iteratively before producing a structured `VerificationResult`. Each turn streams substep events to the dashboard in real time. When dynamics data is not available, hallucination stripping removes physics-related fields and caps confidence at 0.5.

### MuJoCo Dynamics Verification (Full Physics Stack)

Beyond the existing FK layer, strategies can now enable full MuJoCo dynamics simulation. A single pass computes: joint limit checking, self-collision detection, inverse dynamics torque feasibility, gravity compensation analysis (0.5kg payload at grasp pose), and manipulability/singularity detection via SVD. Dynamics sanity checks override LLM plausibility scores — joint limit violations zero out `bounds_check`, near-singularity caps `plan_alignment`, and torque infeasibility lowers `safety_assessment`. All overrides are tagged in reasoning as `[Dynamics override: ...]`.

### Two-Phase Parallel Pipeline

The pipeline now supports `pipeline_mode="parallel"` where VLA action execution runs concurrently with perceive/plan evaluation context via `asyncio.Queue`. In agent-loop verify mode, perceive and plan run as tool calls inside the verify loop rather than separate stages. Results carry a `phase` field ("execution" or "evaluation") so the UI labels each stage card correctly.

### Enriched Reasoning and Structured Verify Output

Verification results now include `resolution_path` (actionable improvement suggestions), `verify_turns` (loop depth), and `stage_checks` (per-evidence-source records with pass/fail/confidence/reasoning). Action plausibility gained four new fields: `workspace_reachability`, `task_completion_plausibility`, `dynamics_consistency`, and `safety_assessment`. A new insights module computes a `reasoning_trail` across all strategies — extracting plan reasoning, subtask reasoning, action reasoning, constraints acknowledged, and the weakest-stage finding — plus `model_comparison` grouping strategies by model ID with per-model avg confidence and latency.

### Robot Embodiment Verification

The verify prompt now receives a ground-truth robot spec built from both manifest metadata (robot name, action/state dimensions) and URDF parsing (DOF count, joint names, mimic joints). The verifier flags embodiment mismatches — e.g., a VLA producing 6-DOF actions for a 7-DOF Panda — as critical failures capping confidence at 0.1.

### LIBERO Atomic Tasks for VLA Baseline Evaluation

Added 6 single-step LIBERO-Goal tasks (bowl-on-plate, wine-on-rack, cream-cheese-in-bowl, push-plate, turn-on-stove, open-drawer) as `eval_category: "atomic"` baselines. These are unambiguous, one-motion tasks for VLA confidence calibration. The manifest also gained structured evaluation categories: `multi_stage`, `situated_correction`, `constrained`, `open_ended`, and `negative` — each testing a distinct failure mode.

### Bing Grounding Agent for Verify Stage

All VLA evaluation strategies now use Azure's `bing-grounding-agent` (via Foundry Agents) as the verify adapter. The adapter resolves agent metadata from the Foundry project at startup and combines the portal system prompt with ROVE's verify loop instructions. Simulation is now optional rather than required.

### Failure Attribution Extensions

Failure attribution now covers dynamics-specific categories: `action_torque_violation`, `action_singularity`, `action_unsafe`, and `action_out_of_workspace`. Failure metadata extracts `resolution_path` and `verify_turns` for downstream reporting.

See also: [ADR-012 Provenance & Failure Attribution](docs/architecture/ADR-012-evaluation-provenance-and-failure-attribution.md), [ADR-013 Evaluation Categories & Structured Reasoning](docs/architecture/ADR-013-evaluation-categories-and-structured-reasoning.md), [ADR-014 Two-Phase Pipeline](docs/architecture/ADR-014-two-phase-pipeline-execution-vs-evaluation.md), [ADR-015 Action Space Normalization](docs/architecture/ADR-015-action-space-normalization-and-dof-handling.md).

---

## 2026-02-26

### Forward Kinematics Verification

Added physics-based verification of VLA action outputs using MuJoCo forward kinematics. When a URDF is available (uploaded or auto-detected from manifest), FK converts joint-space actions into Cartesian end-effector trajectories and runs sanity checks — joint limit violations, self-collisions, and excessive displacement override VLM plausibility scores with objective measurements. The verify prompt now receives spatial positions instead of disclaimers, enabling cross-referencing with perceived object locations. Includes Panda URDF with collision meshes for LIBERO examples. *(PR #11)*

### Per-Stage Latency Budgets

Strategies can now define latency budgets per pipeline stage with strategy-level overrides, enabling timeout enforcement for real-time robotics constraints. *(PR #11)*

### FK Sanity Checks Override VLM Scores

Objective FK measurements (joint limits, collisions, displacement) now override subjective VLM plausibility scores. Joint limit violations force `bounds_check=False`; displacement >2m forces `smoothness=False`; multiple failures cap confidence at 0.4. *(PR #11)*

## 2026-02-25

### Side-by-Side Strategy Comparison

Added a comparison tab that lets users select any two completed strategies and view per-stage diffs side by side. Differing values are highlighted in amber, unique items in green. Works with both live evaluations and past runs loaded from history. *(PR #10)*

### Robotics Domain Knowledge in Pipeline Prompts

Pipeline prompts now extract and evaluate robotics-specific knowledge: `environment_distribution` in perceive, `task_repertoire`/`artifacts`/`degradation_profile` in plan. The verify stage critiques these fields when present and flags their absence as a weakness. *(PR #9)*

## 2026-02-24

### pi0.5 VLA Support with LIBERO Examples

pi0.5 (3.6B params) running end-to-end on MPS via LeRobot adapter — 50-step, 7-DOF trajectories with gripper actions at ~6s inference. Added `ExampleData` contract for stable example-level data passing, 5 LIBERO dataset examples with proprioception, and improved verify prompts with gripper event extraction and trajectory assessment. *(PR #8)*

### Adapter Architecture Restructure

Flattened adapter modules from nested subdirectories to flat files. Split `adapter` vs `provider` in YAML config — `provider:` for HTTP-backed VLMs (LM Studio, Azure Foundry), `adapter:` for direct adapter classes. Added `GenericVLMAdapter` with pluggable providers and a prompt template system with `.txt` templates per stage. *(PR #7)*

### Image Hover Zoom

CSS-only 3x zoom on image hover for better visibility of robotics workspace images in the dashboard. *(PR #6)*

## 2026-02-23

### Examples Browser with Auto-Fill

Added a browsable Examples tab in the dashboard sidebar. Examples are served from `data/manifest.json`, grouped by scene type (assembly, bin-picking, drawer-cabinet, kitchen, tabletop). Clicking an example auto-fills the image and task input, ready to evaluate. *(PR #5)*

### README Overhaul

Rewrote README to lead with the dashboard UI and visual workflow. Removed unimplemented sections (MCP servers, API reference, stale CLI commands) and duplicate content. Reduced from 510 to 150 lines — only documents what actually works. *(PR #3, #4)*

### CI/CD and Code Quality

Branch protection on `main` with required PR reviews and status checks. Pre-commit hooks (ruff, bandit, detect-secrets, pytest). GitHub Actions CI for lint, security, and test. Claude Code integration for automated PR review and security scanning. *(PR #1, #2)*
