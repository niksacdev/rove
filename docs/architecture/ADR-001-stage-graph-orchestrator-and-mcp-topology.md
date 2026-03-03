# ADR-001: Stage-Graph Orchestrator and Revised MCP Topology

**Status**: Accepted (partially superseded by ADR-004 for pipeline implementation)
**Date**: 2026-02-21
**Authors**: System Architect
**Related Docs**: `docs/architecture/high-level-architecture.md`
**Trigger**: Founder review — messages on orchestration rethink, Perceive→Plan→Act model, MCP role, Microsoft Agent Framework consideration

---

## Context

The founder proposed three changes to the current orchestration architecture:

1. Collapse the 5-step pipeline (perceive, ground, plan, execute, verify) into a 3-stage
   Perceive → Plan → Act model where each stage is plug-and-play with different models,
   and a single model (e.g., pi0) can handle all three stages in one call.

2. Use Microsoft Agent Framework (Semantic Kernel or AutoGen) for stage hand-offs instead
   of the current raw asyncio approach.

3. Reposition MCP servers as grounding tools rather than VLM tool wrappers.

The current architecture has a fixed 5-step deterministic pipeline in `RoveOrchestrator`
with the constraint "the orchestrator does NOT use an LLM to decide steps."

This ADR evaluates all three proposals and recommends a concrete path forward.

---

## Decision Drivers

- **Evaluation integrity**: ROVE's value is comparing model combinations fairly. Any
  non-determinism in the orchestrator contaminates the comparison signal.
- **Model heterogeneity**: VLAs like pi0 handle perception, planning, and action in a
  single forward pass with no intermediate outputs. VLMs like GPT-4o expose separate
  perception and planning capabilities. The orchestrator must handle both without special-casing.
- **Per-stage measurement**: ROVE must report latency and cost per stage. Monolithic model
  calls can report total latency but not per-stage breakdown. The architecture must handle
  NULL metrics without treating them as failures.
- **MCP purpose**: MCP servers are for external AI agent integration (Phase 4), not for the
  evaluation engine's internal communication. Their tool surface should match what external
  agents actually need.

---

## Decision

### D1: Retain deterministic orchestration. Do NOT use an LLM or agent framework for pipeline routing

The 5-step pipeline is replaced by a **Stage-Graph Orchestrator** that builds a deterministic
execution graph from adapter capability declarations at initialization time. The graph is static
— built once per combination configuration, no runtime routing decisions. The orchestrator
executes the graph in order with no branching or agent decision-making.

This is pure Python (`asyncio`), not Semantic Kernel, not AutoGen.

**Microsoft Agent Framework is rejected** for ROVE's internal orchestration. It is designed
for LLM agents with conversation history, plugin selection by an LLM, and persona management.
ROVE needs none of these. Using SK/AutoGen for deterministic pipeline execution adds LLM
non-determinism to the orchestrator, breaking the ability to isolate model performance from
orchestrator behavior.

### D2: Collapse to Perceive → Plan → Act + Verify (3 + 1 stages)

"Ground" merges into "Perceive" as a sub-capability. Some adapters produce a bounding box as
part of scene analysis (VLMs with native grounding: Qwen2.5-VL); others require a separate
grounding call (GroundingDINO after VLM perceive). The stage boundary is defined by the
**output contract** (SceneAnalysis + optional Localization), not the model type.

"Verify" is **non-collapsible and always runs as a distinct stage**. Removing it would
eliminate ROVE's primary evaluation signal (VLM judge calibration against sim ground truth).
A pipeline that cannot verify outcome is not an evaluation framework.

The 3+1 stage model:

```text
Stage 1: Perceive     = scene understanding + spatial localization (output: SceneAnalysis + Localization)
Stage 2: Plan         = grasp strategy (output: GraspPlan)
Stage 3: Act          = motor control + sim execution (output: ActionPrediction + SimStepResults)
Stage 4: Verify       = outcome assessment — invariant, always separate (output: VerificationResult)
```

### D3: Add UnifiedAdapter Protocol for VLAs that span Stages 1-3

Models like pi0, pi0-FAST, and (when available) GR00T N1.6 handle perception, planning, and
action in a single forward pass. They implement `UnifiedAdapter`, which has a single
`run_full_pipeline()` method. Intermediate outputs (`SceneAnalysis`, `GraspPlan`) are typed
as `None` in `PipelineContext` — explicitly absent, not missing.

The leaderboard handles NULL metrics by excluding the combination from metric-specific sub-rankings
and labeling it `"no-intermediate-outputs"` in the display. The combination still participates
in success rate, total latency, and cost rankings.

### D4: Add adapter capability declarations to all Protocols

Each adapter declares which stages it covers and whether it produces intermediate outputs:

```python
@dataclass
class StageCapability:
    stage: str                          # "perceive", "ground", "plan", "act"
    produces_intermediate_output: bool  # False for monolithic adapters
    native_grounding: bool              # True for VLMs that return bbox inline
```

The StageGraph builder reads these declarations and constructs the execution graph. No
hardcoded conditionals in the orchestrator — the graph derives entirely from declared
capabilities.

### D5: Revise MCP server topology for Phase 4

**Remove**: VLM MCP server (port 8081). An external LLM agent has its own VLM capability;
it does not need ROVE to proxy VLM calls. The current `analyze_scene`, `plan_grasp`,
`verify_success` tool surface has no natural external agent use case.

**Retain and expand**: Grounding tools (GroundingDINO, SAM2) as MCP tools. External agents
calling `localize("red bracket")` get spatial grounding without knowing which model did the
work. This is a genuine capability abstraction.

**Retain**: Sim tools (sim_reset, sim_step, sim_get_observation). External agents building
robotics pipelines need programmatic sim access.

**Add**: Evaluation tools MCP server. External agents (Claude Desktop, Foundry agents) can
call `run_evaluation(config)` and get ranked results without importing the ROVE Python package.

**Final MCP topology (Phase 4)**:

```text
rove-grounding-tools (port 8083):
  localize(image_b64, query, model_id?)    → Localization
  segment(image_b64, bbox, model_id?)      → SegmentationMask
  list_grounding_models()                  → [model configs]

rove-sim-tools (port 8082):
  sim_reset(task_id)                       → SimObservation
  sim_step(action)                         → SimStepResult
  sim_get_observation()                    → SimObservation
  list_sim_tasks()                         → [LIBERO task list]

rove-eval-tools (port 8081):
  run_evaluation(config)                   → {evaluation_id, status}
  get_evaluation(eval_id)                  → EvaluationResult
  list_evaluations()                       → [EvalSummary]
  get_leaderboard(eval_id)                 → ranked CombinationResults
```

---

## Pipeline Execution: Three Cases

### Case A — Full Multi-Model Chain (GPT-4o + GroundingDINO + SmolVLA)

```text
StageGraph built from:
  vlm = GPT-4o (VLMAdapter, no native_grounding)
  grounding = GroundingDINO (GroundingAdapter)
  vla = SmolVLA (VLAAdapter)

Graph nodes:
  [perceive]  vlm.analyze_scene(image, task)           → SceneAnalysis
  [ground]    grounding.localize(image, target_object) → Localization
  [plan]      vlm.plan_grasp(image, task, scene, bbox) → GraspPlan
  [act]       sim.reset() + vla.predict_action() + sim.step() × N
                                                        → ActionPrediction + SimObservation
  [verify]    vlm.verify_success(before, after, task)  → VerificationResult

Metrics available: ALL
  perceive_latency_ms, ground_latency_ms, plan_latency_ms, act_latency_ms, verify_latency_ms
  total_cost_usd (VLM tokens × 2 + VLA inference)
  sim_success, vlm_success, judge_calibration
```

### Case B — Monolithic VLA (pi0-FAST)

```text
StageGraph built from:
  vla = pi0-FAST (UnifiedAdapter, stages_covered=["perceive","plan","act"])
  verifier_vlm = gpt-4o (optional, from rove.yaml)

Graph nodes:
  [perceive+plan+act]  vla.run_full_pipeline(image, task, proprioception)
                                                        → UnifiedPipelineResult
                                                          .action_prediction: ActionPrediction
                                                          .scene_analysis: None  (pi0 does not expose)
                                                          .grasp_plan: None      (pi0 does not expose)
  [verify]    verifier_vlm.verify_success(before, after, task)  → VerificationResult
              OR: sim_success only if no verifier_vlm declared

Metrics available: PARTIAL
  perceive_latency_ms: NULL     (collapsed — not separately measurable)
  plan_latency_ms:     NULL     (collapsed)
  act_latency_ms:      NULL     (collapsed — only pipeline_latency_ms available)
  pipeline_latency_ms: YES      (total unified call duration)
  verify_latency_ms:   YES (if verifier_vlm) or NULL (sim-only)
  sim_success:         YES
  vlm_success:         YES (if verifier_vlm) or NULL
  judge_calibration:   YES (if verifier_vlm) or NULL

  Leaderboard label: "monolithic-pipeline"
  Intermediate outputs: none — transparently recorded, not an error
```

### Case C — VLM with Native Grounding + VLA (Qwen2.5-VL + SmolVLA)

```text
StageGraph built from:
  vlm = Qwen2.5-VL (VLMAdapter, native_grounding=True)
  vla = SmolVLA (VLAAdapter)

Graph nodes:
  [perceive+ground]  vlm.analyze_scene_with_grounding(image, task)
                                                        → SceneAnalysis + inline Localization
                                                          (single VLM call, bbox returned in response)
  [plan]      vlm.plan_grasp(image, task, scene, bbox) → GraspPlan
  [act]       sim.reset() + vla.predict_action() + sim.step() × N
                                                        → ActionPrediction + SimObservation
  [verify]    vlm.verify_success(before, after, task)  → VerificationResult

Metrics available: NEAR-FULL
  perceive_latency_ms: YES (combined with ground in single call, labeled perceive+ground)
  ground_latency_ms:   NULL (collapsed into perceive — not separately measurable)
  plan_latency_ms:     YES
  act_latency_ms:      YES
  verify_latency_ms:   YES
  sim_success:         YES
  vlm_success:         YES
  judge_calibration:   YES

  Leaderboard label: "native-grounding" for perceive stage
```

---

## Alternatives Considered

### Alternative 1: Keep the 5-step hardcoded pipeline, add special-case for unified VLAs

**Rejected**: The special-casing would grow as more model types are added (GR00T, future cross-embodiment
models). A capability declaration pattern is more maintainable and does not require orchestrator
code changes when new adapter types are introduced.

### Alternative 2: Use Semantic Kernel for stage hand-offs

**Rejected**: SK introduces LLM-based routing decisions into the orchestrator, which breaks evaluation
integrity. The routing problem ROVE has (perceive → plan → act sequencing) is trivially solved
with an ordered list of stage nodes. SK is the right tool for conversational agents with dynamic
tool selection. ROVE is a deterministic pipeline.

### Alternative 3: LLM agent as first point of interaction / orchestrator

**Rejected**: ROVE is a tool that LLM agents CALL. It is not itself an LLM agent. An LLM agent
(Claude Desktop, Foundry agent) calls ROVE's `run_evaluation` MCP tool. ROVE runs a deterministic
pipeline. ROVE returns ranked results to the agent. If ROVE were itself an LLM agent, its
orchestration decisions would contaminate the evaluation signal — you could not tell whether a
poor result came from the evaluated model or from the orchestrator's judgment.

### Alternative 4: VLM MCP server exposing analyze_scene, plan_grasp, verify_success

**Rejected for external exposure**: External agents have their own VLM capability. They do not need
ROVE to proxy VLM calls. The tools have no natural external use case. Grounding and sim capabilities
are genuinely useful as MCP tools because they are specialized capabilities external agents likely
do not have natively.

**Retained internally**: VLMAdapter Protocol and its methods are unchanged. They are called by the
evaluation engine directly. Only the MCP exposure of these methods is removed.

---

## Implementation Steps

### Phase 1 (immediate — before any new adapter is built)

- [ ] Add `StageCapability` dataclass to `rove/models.py`
- [ ] Add `capabilities: list[StageCapability]` to `VLMAdapter`, `VLAAdapter`, `GroundingAdapter` Protocols in `rove/adapters/protocols.py`
- [ ] Add `UnifiedAdapter` Protocol to `rove/adapters/protocols.py`
- [ ] Add `UnifiedPipelineResult` dataclass to `rove/models.py`
- [ ] Implement `StageGraph` class in `rove/orchestrator/stage_graph.py`
- [ ] Refactor `RoveOrchestrator` in `rove/orchestrator/agent.py` to use `StageGraph`
- [ ] Update `PipelineContext` / `CombinationResult` in `rove/models.py`:
  - Add `stages_collapsed: list[str]`
  - Add `stages_with_intermediate_output: list[str]`
  - Add `null_metrics: list[str]`
- [ ] Update `MockVLMAdapter`, `MockVLAAdapter` to declare capabilities
- [ ] Update `Leaderboard.rank()` in `rove/evaluation/leaderboard.py` to handle NULL metrics
- [ ] Update `rove.yaml` schema to support `pipeline_model` and optional `verifier_vlm` fields

### Phase 2 (when VLA adapters are built)

- [ ] Implement `MockUnifiedAdapter` that implements `UnifiedAdapter` Protocol (for testing Case B)
- [ ] Ensure pi0-FAST adapter implements `UnifiedAdapter` when built
- [ ] Add `analyze_scene_with_grounding()` to VLMAdapter Protocol (optional method)
- [ ] Implement native grounding path in Qwen2.5-VL adapter

### Phase 4 (MCP topology)

- [ ] Create `rove/mcp_servers/eval_server.py` (port 8081, tools: run_evaluation, get_evaluation, list_evaluations, get_leaderboard)
- [ ] Create `rove/mcp_servers/grounding_server.py` (port 8083, retaining localize, segment, list_grounding_models)
- [ ] Create `rove/mcp_servers/sim_server.py` (port 8082, tools: sim_reset, sim_step, sim_get_observation, list_sim_tasks)
- [ ] Deprecate `rove/mcp_servers/vlm_server.py`
- [ ] Update `rove/cli.py`: `rove mcp [eval|grounding|sim]`

---

## Consequences

### Positive

- Model heterogeneity is handled via capability declarations, not orchestrator special-casing
- Monolithic VLAs (pi0, GR00T) can be evaluated without breaking the evaluation framework
- NULL metrics are explicit and honest — partial evaluation results are valid, labeled, and preserved
- MCP tools match real external agent use cases (grounding, sim access, evaluation results)
- Orchestration remains fully deterministic — evaluation signal is uncontaminated

### Negative

- Leaderboard comparison between Case A (full chain) and Case B (monolithic) is apples-to-oranges for
  per-stage metrics. Resolution: separate sub-rankings by pipeline type, with clear labeling.
  Cross-type comparison is valid only on outcome metrics (success rate, total latency, total cost).
- Adding `StageCapability` to all existing adapter Protocols is a breaking change. All mock adapters
  must be updated before Phase 1 tests pass. This is a one-time migration cost.
- `analyze_scene_with_grounding()` as an optional Protocol method risks Protocol discipline — some
  adapters will implement it, others won't. Resolution: make it a separate Protocol mixin
  (`NativeGroundingMixin`) rather than an optional method on `VLMAdapter`.

### Risks

- **Stage graph complexity growth**: As more hybrid adapters appear, the graph builder may accumulate
  special cases. Mitigation: enforce that graph construction is entirely data-driven from declared
  capabilities — no `isinstance` checks in the graph builder.
- **NULL metric surfacing in UI**: The dashboard must clearly communicate when a metric is absent
  vs. zero. Latency of 0ms looks like a fast model. NULL must display as "—" not "0". Dashboard
  update required before any monolithic adapter is evaluated publicly.

---

## References

- `docs/architecture/high-level-architecture.md` — Section 4 (5-step pipeline), Section 12 (MCP schemas)
- `rove/adapters/protocols.py` — current Protocol definitions
- `rove/orchestrator/agent.py` — current 5-step implementation
- Founder messages: 2026-02-21 orchestration rethink (MCP role, PPA stages, agent framework question)
- RAI-ADR-003 — VLM-as-judge verification risk (verify stage must remain independent)

---

*ADR-001 — System Architect, 2026-02-21*
*Status: Proposed — awaiting founder review*
