# ADR-012: Evaluation Provenance, Failure Attribution, and Seed Control

**Status**: Accepted
**Date**: 2026-02-25
**Authors**: Product Advisor, System Architect
**Related Docs**:
- `docs/architecture/ADR-004-pipeline-data-flow-strategies-concurrency.md`
- `docs/architecture/ADR-011-simulation-adapter-strategy-and-dual-mode-verify.md`
- `src/rove/models/core.py`

**Trigger**: ROVE captures evaluation results but lacks the metadata needed to reproduce them or systematically learn from failures. Without provenance, two runs of the same config are not provably identical. Without failure attribution, "failed" evaluations provide no signal about *which stage* caused the failure or *why*.

---

## Context

ROVE's evaluation pipeline produces `TrialResult` records persisted as JSONL (`data/output/history.jsonl`). Current records capture stage outputs, latency, and success/failure — but not:

- **What ran**: config hash, model versions, package versions, seed
- **Why it failed**: which stage was the root cause, what category of failure

This ADR introduces three Phase 1 features that address these gaps using only data already available in the pipeline, with zero external dependencies.

### Design Constraint: JSONL, Not SQLite

ROVE uses append-only JSONL for persistence. This is correct for Phase 1: human-readable, grep-able, zero dependencies. SQLite adds value only when we need indexed queries across thousands of evaluations — a Phase 2+ concern. All features in this ADR work with JSONL.

---

## Decision

### 1. EvaluationProvenance — Mandatory Metadata on Every Run

Every persisted evaluation record includes a `provenance` object:

```python
class EvaluationProvenance(BaseModel):
    timestamp: str              # ISO 8601 UTC
    hostname: str               # machine identifier
    rove_version: str           # from importlib.metadata
    python_version: str         # sys.version
    config_hash: str            # SHA256 of rove.yaml contents
    seed: int | None            # random seed if provided
    image_sha256: str           # fingerprint of input image (not the image itself)
    strategy_ids: list[str]     # which strategies were evaluated
    resolved_models: dict       # {stage: {model_id, adapter_class}} for each strategy
    package_versions: dict      # key deps: fastapi, pydantic, torch (if installed)
```

**Key decisions**:
- Provenance is **mandatory and always on**. If it's optional, 90% of runs won't have it.
- Images are stored as **SHA256 fingerprints**, not raw bytes. A single LIBERO trial is ~2MB of base64; storing raw images in JSONL would hit storage limits quickly.
- `resolved_models` captures the actual adapter class used, not just the model_id string. This catches cases where the same model_id maps to different adapters across ROVE versions.

### 2. Failure Stage Attribution — Derive Root Cause from Existing Data

When a pipeline fails (either via stage error or verify `success=False`), we compute:

```python
failure_stage: str | None     # "perceive" | "plan" | "act" | "verify" | None (if success)
failure_category: str | None  # specific failure type
```

**Attribution rules** (applied in order, first match wins):

| Condition | failure_stage | failure_category |
|-----------|---------------|------------------|
| Stage status == ERROR | that stage | `"{stage}_error"` |
| `SceneAnalysis.task_relevant` is empty | `"perceive"` | `"perceive_miss"` |
| `TaskPlan.confidence < 0.4` | `"plan"` | `"plan_low_confidence"` |
| FK: `joint_limits_ok=False` or `self_collision=True` | `"act"` | `"action_fk_violation"` |
| `ActionPlausibility.bounds_check=False` | `"act"` | `"action_infeasible"` |
| Verify `success=False` but all stages passed | `"verify"` | `"verification_mismatch"` |

**Key decisions**:
- Attribution is **deterministic and rules-based**, not LLM-generated. It uses data already in the pipeline.
- Attribution is computed **after the pipeline completes**, in the run manager, not inside the pipeline itself.
- `failure_stage=None` and `failure_category=None` when the evaluation succeeds.

### 3. Seed Control — Deterministic Mock Outputs

A `seed` parameter propagates from CLI/API through to all mock adapters:

- `rove serve --seed 42` sets a global seed for the server session
- `POST /api/evaluate` accepts an optional `seed` form field
- Mock adapters accept `seed` in their config and use `random.Random(seed)` instead of the global `random` module
- The seed is stored in `EvaluationProvenance`

**Key decisions**:
- Each mock adapter gets its own `random.Random` instance to avoid cross-adapter state leakage.
- Real adapters that support seed/temperature parameters will receive them in Phase 2+ via adapter config. This ADR only covers mocks.
- Without a seed, mock behavior remains stochastic (current behavior).

---

## Implementation

### Models (`src/rove/models/core.py`)

Add `EvaluationProvenance` Pydantic model. Add `failure_stage` and `failure_category` fields to the persisted record (computed post-pipeline, not on `TrialResult` itself — since attribution needs the full result set).

### Provenance capture (`src/rove/api/app.py`)

`_build_provenance()` collects all metadata at evaluation start time. Called in both `_run_evaluation()` and `_run_multi_strategy()`. Injected into the JSONL record via `_persist_evaluation()`.

### Failure attribution (`src/rove/orchestrator/failure_attribution.py`)

New module with a pure function: `attribute_failure(stages: list[dict]) -> tuple[str | None, str | None]`. Takes the list of completed stage dicts, returns `(failure_stage, failure_category)`. Called in `run_manager.py` after pipeline completes.

### Mock adapters

`MockVLMAdapter` and `MockVLAAdapter` accept an optional `seed` in config. When set, they use a seeded `random.Random` instance instead of `random.random()`.

### CLI

Add `--seed` flag to the `serve` command. Passed through to the app as an environment variable or startup config.

---

## Consequences

**Positive**:
- Every evaluation is reproducible (given the same config + seed + image)
- Failure analysis becomes actionable: "65% of failures are at the plan stage with low confidence" instead of "65% of evaluations fail"
- Seed control enables regression testing: mock results are deterministic and diffable
- Zero new dependencies — all features use stdlib + existing Pydantic models

**Negative**:
- JSONL records grow by ~500 bytes per evaluation (provenance JSON). Negligible.
- Failure attribution rules are heuristic — they may misattribute in edge cases. This is acceptable: rules can be refined, and wrong attribution is better than no attribution.

**Future extensions** (Phase 2+):
- `raw_prompt` capture per stage (needed for fine-tuning dataset construction)
- Adapter metadata protocol (model_version, token_counts from real adapters)
- LeRobot dataset export using provenance + failure data
- W&B/MLflow push with provenance as run config
