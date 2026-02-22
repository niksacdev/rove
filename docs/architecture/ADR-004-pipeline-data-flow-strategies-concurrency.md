# ADR-004: Pipeline Data Flow, Strategies, and Concurrent Execution

**Status**: Accepted
**Date**: 2026-02-22
**Deciders**: @niksac
**Supersedes**: `evaluations` section of rove.yaml

## Context

Three problems identified in the pipeline implementation:

1. **Broken data flow** — Stage outputs did not flow downstream. Perceive results didn't inform plan, plan didn't inform act, and verify had zero knowledge of what was planned or executed. The sim's `get_observation()` returned fresh defaults instead of actual post-step state.

2. **No named configurations** — Users had to manually select models for each of the 4 stages every time. The old `evaluations` section in rove.yaml defined model *sweeps* (all combinations of VLMs x VLAs), not complete pipeline configurations.

3. **No concurrent execution** — Running the same task against multiple configurations was sequential. No way to compare strategies side-by-side.

Additionally evaluated: **Microsoft Agent Framework** (formerly Semantic Kernel Agents). Deferred to Phase 4 — it's RC (not GA), its message model is LLM-chat-centric (not typed domain objects), and our deterministic 4-stage pipeline is ~100 lines of async Python.

## Decisions

### D1: PipelineContext accumulates stage outputs

A `PipelineContext` dataclass accumulates results as the pipeline progresses:

```
ctx.scene      ← set after perceive
ctx.proprioception ← set after sim.reset()
ctx.plan       ← set after plan
ctx.action     ← set after act
ctx.after_image_base64 ← set after sim observation
```

Each downstream stage receives upstream data:
- **plan** receives `scene` (perceive output)
- **act** receives `plan` + `proprioception` (from sim reset)
- **verify** receives full `ctx.to_dict()` (scene, plan, action, proprioception)

`VLMAdapter.verify_success()` gains an optional `context: dict | None` parameter (backward compatible). `AgentAdapter.run_stage()` already had `context: dict`.

### D2: Strategies replace evaluations

**Strategies** are named, complete pipeline configurations — one model per stage:

```yaml
strategies:
  mock:
    display_name: "Mock (Simulation)"
    perceive: mock-vlm
    plan: mock-vlm
    act: mock-vla
    verify: mock-vlm
    sim: mock-sim
    tags: [test, mock]
```

vs. the old `evaluations` which defined *sweeps*:
```yaml
evaluations:
  pick_bracket:
    vlms: [gpt-4o, qwen3-vl-8b]  # cross-product
    vlas: [pi0, smolvla-450m]
```

**Rationale**: Users think in terms of "which pipeline configuration works best for my task", not "which cross-product cell". Strategies are the unit of comparison in the dashboard — each strategy gets its own tab.

### D3: RunManager for concurrent multi-strategy execution

`RunManager` uses `asyncio.TaskGroup` (Python 3.11+) for structured concurrency and `asyncio.Semaphore` for bounded parallelism.

**Within a strategy**: 4 stages run sequentially (data dependencies).
**Across strategies**: Strategies interleave on the event loop — while strategy A awaits a cloud VLM response, strategy B's mock adapter runs.

**Sim isolation**: `RunManager._build_pipeline()` calls `registry.create_sim()` (uncached) instead of `registry.get_sim()` (cached). VLM/VLA/Agent adapters remain cached since they're stateless.

### D4: Dashboard shows strategy selector + tabbed results

- Strategy cards replace per-stage model dropdowns
- Multi-select: click strategies to toggle on/off
- Results shown in tabs (one per strategy)
- SSE events include `strategy_id` for routing
- Sidebar pipeline icons reflect active tab's state

### D5: MockSimAdapter tracks state

`MockSimAdapter` now tracks `_last_success`, `_last_proprioception`, `_last_image_base64` across `step()` calls. `get_observation()` returns actual last state (not fresh random values). Uses a 1x1 PNG base64 constant for mock observation images.

## Consequences

- **Positive**: Plan references perceived objects, verify knows what was planned. Rich, contextual mock outputs for testing.
- **Positive**: Users compare named strategies, not model matrices. Dashboard UX is simpler.
- **Positive**: N strategies execute concurrently with bounded parallelism. Wall-clock time ≈ slowest strategy, not sum.
- **Negative**: Old `evaluations` YAML format removed. No migration path (acceptable — Phase 1, no external users).
- **Negative**: `strategy_ids` is a comma-separated string in form data (FormData limitation). Could be JSON body in future.

## Files

| File | Change |
|------|--------|
| `src/rove/models.py` | `PipelineContext`, `Strategy` dataclasses |
| `src/rove/adapters/protocols.py` | `verify_success()` + `context` param |
| `src/rove/orchestrator/pipeline.py` | Wire PipelineContext through `run_trial()` |
| `src/rove/orchestrator/run_manager.py` | NEW — RunManager with TaskGroup + Semaphore |
| `src/rove/adapters/registry.py` | `create_sim()` for fresh uncached instances |
| `src/rove/adapters/sim/mock.py` | Stateful observation tracking |
| `src/rove/adapters/vlm/mock.py` | Uses scene target in plan, context in verify |
| `src/rove/adapters/policy/mock.py` | Uses proprioception + plan steps |
| `src/rove/adapters/agent/mock.py` | Uses upstream context in all handlers |
| `src/rove/config.py` | `get_strategies()` |
| `src/rove/api/app.py` | `GET /api/strategies`, multi-strategy evaluate, SSE routing |
| `rove.yaml` | `strategies` section replaces `evaluations` |
| `frontend/index.html` | Strategy selector cards, tabbed results |
