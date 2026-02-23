# ADR-008: Optional Pipeline Stages

**Date**: 2026-02-22
**Status**: Accepted

## Context

ROVE's evaluation pipeline runs a fixed 4-stage sequence: perceive → plan → act → verify. Every strategy in `rove.yaml` must define all four stage assignments plus a sim environment.

This rigidity prevents common evaluation scenarios:

1. **VLM comparison** — comparing scene analysis quality across VLMs requires only perceive + verify. Forcing users to assign plan/act models adds noise and cost.
2. **Planning evaluation** — evaluating task decomposition quality needs perceive + plan + verify, without executing actions in simulation.
3. **Action-only evaluation** — testing a VLA with a fixed plan needs act + verify, without re-running perception.

Users requested the ability to omit stages from strategy configs to run partial pipelines.

## Decision

### Optional stages via YAML omission

`perceive`, `plan`, and `act` are now optional in strategy configs. Omitting a field skips that stage entirely:

```yaml
strategies:
  vlm-compare:
    display_name: "VLM Compare (Scene Only)"
    perceive: mock-vlm
    verify: mock-vlm
    sim: mock-sim
    # plan and act omitted — skipped at runtime
```

### Rules

- **`verify` and `sim` remain required** — verify is the evaluation gate that produces success/failure. `sim` is required structurally but only used if `act` is present.
- **At least one of perceive/plan/act must be present** — a verify-only strategy is invalid (nothing to evaluate).
- **Skipped stages pass `None` context downstream** — the pipeline and adapters handle `None` scene/plan/action gracefully since `PipelineContext` already uses `Optional` fields.

### Implementation

1. **`StrategyConfig` and `Strategy`** — `perceive`, `plan`, `act` fields changed from `str` to `str | None = None`.
2. **Config validation** — `_validate_strategy_refs` skips `None` refs, validates at least one optional stage is present.
3. **`EvaluationPipeline`** — constructor accepts `None` adapters. Each optional stage wrapped in `if self.<stage>_adapter is not None:` guard. Sim reset only runs when `act` is present. Verify always runs with whatever context accumulated.
4. **`RunManager._build_pipeline`** — only resolves adapters for non-None strategy stages. Sim created only if `act` is present.
5. **Frontend** — summary table dots omit skipped stages instead of showing gray dots.

## Consequences

### Positive

- Users can evaluate VLM quality without VLA noise (perceive + verify)
- Planning evaluation without simulation overhead (perceive + plan + verify)
- Reduced cost and latency for targeted evaluations
- Backward compatible — existing full-pipeline strategies work unchanged

### Negative

- Verify adapters must handle `None` scene/plan/action in their context — mock adapters already do, but real adapters need to be tested
- Comparison across strategies with different active stages requires care (latency of a 2-stage pipeline is not comparable to a 4-stage one)

### Risks

- Users may misinterpret partial pipeline results as full pipeline evaluations — the dashboard should clearly indicate which stages ran
