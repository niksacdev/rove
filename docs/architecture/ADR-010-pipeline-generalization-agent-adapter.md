# ADR-010: Pipeline Generalization and AgentAdapter Protocol

**Date**: 2026-02-22
**Status**: Accepted
**Supersedes**: ADR-001 (stage-graph orchestrator, partially)

## Context

ROVE's original pipeline was grasp-specific: 5 stages (perceive → ground → plan_grasp → execute → verify) with grasp-centric data models (`GraspPlan`, `grasp_planning` capability). This limited ROVE to pick-and-place evaluation.

Two forces demanded generalization:

1. **Broader task types** — navigation, assembly, tool use, and multi-step manipulation all follow a perceive → plan → act → verify pattern, but "grasp planning" is too narrow.
2. **Agent frameworks** — Azure AI Foundry agents, LangChain agents, and similar frameworks bundle multiple capabilities (planning + action) into a single callable. They don't fit the one-model-per-stage assumption.

## Decision

### 1. Collapse 5 stages to 4: perceive → plan → act → verify

Grounding was folded into the perceive stage. Modern VLMs (Qwen3-VL, GPT-4o) produce grounded bounding boxes natively — a separate grounding stage added latency without value for most configurations. The `grounding` endpoint type remains in `rove.yaml` for standalone grounding models, but the orchestrator no longer has a dedicated grounding step.

### 2. Rename grasp-specific terminology

| Before | After |
|--------|-------|
| `GraspPlan` | `TaskPlan` (`GraspPlan` kept as alias) |
| `grasp_planning` capability | `task_planning` capability |
| `plan_grasp` stage | `plan` stage |
| `execute` stage | `act` stage |

### 3. Introduce `AgentAdapter` protocol

```python
@runtime_checkable
class AgentAdapter(Protocol):
    model_id: str
    async def run_stage(
        self, stage: str, image_base64: str, task: str, context: dict | None
    ) -> dict: ...
```

Key properties:
- **One adapter, multiple stages** — an agent can serve `plan` and `act` (or all four stages). The orchestrator calls `run_stage("plan", ...)` and `run_stage("act", ...)` on the same adapter instance.
- **Dict in, dict out** — agents return raw dicts; the pipeline converts to typed dataclasses (`_dict_to_plan`, `_dict_to_action`, etc.).
- **`StageAdapter` union type** — `VLMAdapter | PolicyAdapter | AgentAdapter`. The pipeline dispatches by `isinstance` check.

### 4. Extend `ActionPrediction` for agent tool calls

```python
@dataclass
class ActionPrediction:
    actions: list[list[float]]      # VLA trajectory
    tool_calls: list[dict]          # agent tool invocations
    action_type: str = "trajectory" # "trajectory" | "tool_calls"
```

VLAs produce trajectories (7-DOF deltas). Agents produce tool calls. The `action_type` field disambiguates, and the sim adapter handles both.

### 5. Pipeline dispatch via `_call_adapter`

Single dispatch method replaces per-stage adapter calls:
- `isinstance(adapter, AgentAdapter)` → call `run_stage()`, convert dict to dataclass
- Otherwise → call the type-specific method (`analyze_scene`, `plan_task`, `predict_action`, `verify_success`)

## Consequences

### Positive

- ROVE can evaluate any robotics task type, not just grasping
- Agent frameworks (Foundry, LangChain) integrate naturally via `AgentAdapter`
- `TaskPlan` is more general — works for navigation, assembly, manipulation
- Pipeline dispatch is DRY — one method handles all adapter types

### Negative

- `AgentAdapter.run_stage()` returns untyped dicts — less safety than typed Protocol methods
- `GraspPlan` alias adds a name that may confuse new contributors
- The `isinstance` dispatch in `_call_adapter` is a code smell (visitor pattern would be cleaner) but is pragmatic for 3 adapter types
