# ADR-013: Evaluation Categories and Structured Reasoning

**Date**: 2026-03-01
**Status**: Accepted
**Related**: ADR-010 (pipeline generalization), ADR-012 (failure attribution)

## Context

ROVE's initial dataset consisted of 25 examples — all single-step atomic manipulation tasks (e.g., "pick up the red cup"). While sufficient for validating the pipeline, this dataset could not evaluate the capabilities that differentiate strong robotics agent pipelines from weak ones:

- Can the pipeline decompose multi-step tasks into ordered subtasks?
- Can it adapt to mid-task corrections from a human operator?
- Does it respect safety and preference constraints?
- Can it handle ambiguous, open-ended instructions?
- Can it exclude objects or actions when told "don't" or "not"?

Two external references shaped the category taxonomy:

1. **Hi Robot** (Physical Intelligence, 2025) — demonstrated that VLM+VLA systems can be evaluated across instruction complexity categories (multi-stage, situated corrections, user constraints, open-ended prompts, negative exclusions). Their taxonomy maps directly to real-world deployment scenarios.
2. **LIBERO** (2023) — 130 manipulation tasks across 5 suites. Useful for action-level ground truth but limited to max 2 sub-steps with no corrections, constraints, or open-ended tasks.

Additionally, the plan stage produced flat reasoning — a single `reasoning` string with no structure. Modern embodied AI research shows that decomposing planning into explicit subtask reasoning (why each subtask is needed) and action/spatial reasoning (how to execute each movement) produces more interpretable and evaluable plans.

## Decision

### 1. Six evaluation categories

Each example in `data/manifest.json` carries an `eval_category` field:

| Category | Purpose | Key Fields |
|----------|---------|------------|
| `atomic` | Single-step manipulation | — |
| `multi_stage` | Sequential multi-step tasks | `expected_subtasks` |
| `situated_correction` | Mid-task human feedback | `correction`, `turns` |
| `constrained` | Safety/preference constraints | `constraints` |
| `open_ended` | Ambiguous semantic instructions | `acceptable_interpretations` |
| `negative` | Exclusion filtering | `constraints` (with "not"/"don't") |

Categories are independently testable — the API supports `GET /api/examples?eval_category=constrained` and the dashboard has a category filter sidebar.

### 2. Structured reasoning fields in TaskPlan

Three new fields on `TaskPlan`:

```python
subtask_reasoning: str = ""    # WHY each subtask is needed and its ordering
action_reasoning: str = ""     # spatial/motion reasoning for key movements
constraints_acknowledged: list[str] = Field(default_factory=list)
```

These fields are:
- **Requested in prompts** — `plan_user.txt` asks models to produce structured decomposition
- **Rendered in the dashboard** — plan stage bubble shows subtask reasoning, action reasoning, and constraint acknowledgment as separate labeled sections
- **Compared across strategies** — the comparison view shows structured reasoning fields side-by-side
- **Included in the reasoning trail** — insights module extracts these for per-strategy analysis

### 3. Task metadata flow through the pipeline

Evaluation metadata flows from manifest to model prompts:

```
manifest.json → ExampleData.extras → PipelineContext.task_metadata → prompt templates → VLM/Agent adapters
```

Specifically:
- `constraints` are injected into plan prompts so models can acknowledge them
- `correction` feedback is injected so models can demonstrate adaptation
- `expected_subtasks` are used by the verify prompt to check plan completeness
- The dashboard uses stored metadata to render checklists and indicators

### 4. Naming: "Structured Reasoning" (not ECoT)

The pattern of subtask decomposition + action reasoning is inspired by academic work on embodied chain-of-thought reasoning, but ROVE uses the general term **"Structured Reasoning"** throughout the codebase and UI. This avoids attribution to any specific paper and reflects that the pattern is a general planning decomposition technique used across robotics.

## Consequences

**Positive:**
- Pipeline evaluation now covers 6 distinct capability dimensions, not just "can it pick things up"
- Structured reasoning fields make plan quality evaluable beyond binary success/fail
- Category filtering enables targeted testing (e.g., "how does GPT-4o handle constraints vs Phi-4?")
- task_metadata flow ensures real VLM/LLM strategies see constraints and corrections in their prompts

**Negative:**
- 18 new ROVE-curated manifest entries need placeholder images (not yet created)
- Structured reasoning fields are optional — models that don't produce them get empty strings, which the verify stage notes as a weakness
- Expected subtask matching in the UI is substring-based, which may produce false positives/negatives

**Risks:**
- Category taxonomy may need refinement as real-world usage reveals gaps (e.g., "temporal" tasks, "collaborative" tasks)
- Constraint acknowledgment is self-reported by the model — a model can claim to respect a constraint while its plan violates it. FK analysis and sim verification (Phase 2+) provide ground truth

## Alternatives Considered

1. **Use LIBERO suites as categories** — Rejected. LIBERO's 5 suites (Spatial, Object, Goal, Long, LIBERO-90) don't map to instruction complexity. They test spatial/object generalization, not planning depth.
2. **Use Hi Robot categories directly** — Partially adopted. Hi Robot's taxonomy was the primary inspiration, but ROVE adds "negative" as a distinct category and renames some categories for clarity.
3. **Embed reasoning structure in a separate model** — Rejected. The structured reasoning fields are part of the plan output, not a separate analysis step. This keeps the pipeline deterministic and avoids adding another model call.
