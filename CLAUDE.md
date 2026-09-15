# ROVE — Robot Observation & Vision Evaluation

## Documentation status and feature changes

Use [docs/README.md](docs/README.md) as the documentation entry point and
[current implementation](docs/architecture/current-implementation.md) for
verified behavior. Older architecture descriptions in this file and historical
specifications include proposed paths, commands and integrations; verify them
against source before making implementation claims. Update the relevant product
specification, ADR and diagram with each feature PR, including implementation
status and source/test evidence. The product vocabulary and current roadmap are
in [concepts](docs/product/concepts.md) and
[workflows](docs/product/evaluation-workflows.md).

## Current project overview

**ROVE evaluates robotics agent pipelines on your task.** Compare the same task,
observation and relevant robot input across configured strategies. Quick trials
provide immediate outputs; campaigns add repeated attempts, success contracts,
reviews, baselines and versioned improvement history. Execution, outcome acceptance
and physical completion are separate claims.

ROVE's evaluation engine runs models and measures outcomes. Explicit standalone preparation examples may fine-tune a model before evaluation; training never starts implicitly inside a trial or campaign.

A local VLA action chunk is not a closed-loop robot episode; see
[action and episode scope](docs/product/action-and-episode-evaluation.md).

- **PyPI package:** `rove-eval`; **Python import:** `rove`; **CLI:** `rove`.
- **Shared services:** API and CLI use the same configuration, execution, assessment
  and storage services. Keep product behavior out of frontend-only implementations.
- **Adapters:** `VLMAdapter`, `VLAAdapter`, `AgentAdapter`, `SimAdapter` and
  `VerifierAdapter` protocols define supported contracts. Compatibility still needs
  real tests and cannot be inferred from a provider/model name.
- **Configuration:** `rove.yaml` supplies endpoint and strategy definitions; saved
  strategy revisions and campaign snapshots preserve actual versioned selections.
- **Orchestration:** configured optional perceive/plan/act/verify stages support
  sequential and parallel paths. ROVE schedules them; an LLM does not secretly
  choose the campaign's execution graph.
- **Runtime:** Copilot hosts optional assistant and configured agent roles. Direct
  customer models/agents do not need Copilot planning around them. Local LeRobot
  inference can use the separately locked experimental process runtime.
- **Persistence:** local SQLite stores plus content-addressed assets retain trials,
  cases, reviews, configuration, evidence and lineage. Telemetry is supplementary.
- **Future integrations:** general physical environment lifecycle, hosted monitoring,
  Fabric/Databricks/Delta and MCP exposure are not automatically implemented because
  an older design document names them.

## Technology and structure

Python uses FastAPI, Pydantic, argparse CLI dispatch, YAML configuration and SQLite.
The frontend is static HTML, vanilla JavaScript and shared CSS, with no React build.
Quick execution uses SSE; campaign progress reads durable state. Optional provider,
Copilot, kinematics and local policy dependencies have separate setup/validation
requirements. Python 3.12 is the tested local setup recommendation; package metadata
allows Python 3.11+ where dependencies support it.

```text
src/rove/
  adapters/        # Protocols, registry, model/agent/verifier adapters and workers
  api/             # HTTP services and local application
  benchmarks/      # Campaign planning, execution, assessment and reports
  datasets/        # Cases, reviews, success contracts, ABC imports and frozen sets
  evaluators/      # Configured task/constraint/diagnostic evaluation
  models/          # Shared typed configuration and result models
  orchestrator/    # Stage execution and recording bridge
  runtime/         # Copilot roles, assistant selection and observability
  strategies/      # Immutable strategy catalog
  trials/          # SQLite records, assets, evidence and exchange
  ui/              # Server and CLI entry points; cli.py owns the rove command
frontend/          # Shared navigation, strategy table and evaluation workspaces
tests/             # Python and Node regression suites
```

Current navigation is **Trials** and **Campaigns**, with **New trial** and
**New campaign** creation actions and separate Settings. Follow the
[user journey](docs/product/user-journey.md) and source rather than older navigation
or React/MCP sketches. Keep task/image/URDF comparison, recorded pipeline progress,
CLI parity, private reference boundaries and baseline immutability intact during UI
changes.

## Reusable evaluation core

The 2026-09-14 pivot preserves the robotics workbench and makes its trial/campaign
lifecycle reusable. See [reusable core](docs/product/reusable-evaluation-core.md) and
[ADR-036](docs/architecture/ADR-036-reusable-evaluation-core.md). The default
`robotics` executor retains current image, URDF and stage contracts. Installed
`rove.evaluations` integrations accept structured inputs, run separate candidate
and grader calls, and share SQLite history, evidence, deadlines and reporting.
Generic case authoring is currently manifest/CLI based; do not claim the robotics
case builder or strategy catalog already supports arbitrary domains. The built-in
`abc-bimanual` executor now hosts ABC simulation episodes through this lifecycle;
see [ADR-038](docs/architecture/ADR-038-abc-bimanual-episodes.md). Azure/Fabric
deployment remains future work.

## Changelog

When creating a PR, always update `CHANGELOG.md`:

- Move entries from `## Unreleased` to a dated section (`## YYYY-MM-DD`) matching the PR date
- Or add new entries under `## Unreleased` if the PR hasn't merged yet
- Write a short descriptive title (`###` heading) and 1-3 sentences explaining what capability was added or changed
- Focus on **what the user/system can now do**, not implementation details
- Include the PR number at the end: `*(PR #N)*`
- Keep entries concise — no bullet-point dumps of every file touched
- Entries are ordered newest-first, `## Unreleased` always at the top

## Coding Conventions

### Python

- Python 3.11+ (use modern type hints: `str | None`, `list[str]`)
- Async-first: all adapter methods and orchestrator are `async`
- Dataclasses for domain objects (`SceneAnalysis`, `TaskPlan`, `ActionPrediction`, etc.)
- Pydantic for API request/response models
- No global state — pass dependencies explicitly (registry, store)
- Protocol pattern: adapters implement `@runtime_checkable` Protocols, never inherit from ABC

### Frontend

- Use the existing HTML/JavaScript/CSS architecture and shared components.
- Render authoritative API records; keep evaluation and scoring in shared services.
- Preserve keyboard focus, accessible dialogs, saved theme and draft/saved-state boundaries.
- Run applicable Node behavior tests and inspect meaningful interaction changes in the browser.

### Testing

- Mock adapters are the primary testing tool for Phase 1
- Test the pipeline end-to-end with mocks before adding real adapters
- Use `pytest` with `pytest-asyncio` for async tests

## Important Files

| File | Purpose |
|------|---------|
| `rove.yaml` | Single source of truth for models and strategies |
| `src/rove/adapters/protocols.py` | Adapter contracts — change these carefully |
| `src/rove/orchestrator/pipeline.py` | 4-step pipeline — deterministic, no LLM decisions |
| `src/rove/orchestrator/run_manager.py` | Concurrent multi-strategy execution |
| `src/rove/models/` | Shared data and configuration models |
| `docs/PRODUCT_SPEC.md` | Full product specification and reference |

## What NOT to Build

- No training inside evaluation workers. Standalone preparation recipes may produce checkpoints which Trials and Campaigns then consume explicitly.
- No real robot control (simulation only — output is data, not robot motion)
- No model serving infrastructure (ROVE calls models, does not host them)
- Keep robotics behavior compatible while reusing Trials/Campaigns for other evaluation domains. Domain-specific inputs and grading belong behind execution adapters; do not build a second campaign engine.
- No authentication/RBAC
- No multi-tenant support

## Terminology

Use the [current concepts](docs/product/concepts.md): a task is an instruction,
a case supplies concrete inputs/conditions, a trial is one strategy attempt, and a
campaign organizes selected cases, strategies and repetitions. Baseline is a saved
reference to a completed strategy's assessment; Improve prepares a linked editable
copy. Evaluation and run describe activities rather than extra persisted containers.

An adapter implements a supported integration contract. The orchestrator executes
the configured stage graph. Stage names remain perceive, plan, act and verify;
`task_planning` is the plan capability. Descriptions and AI suggestions are not
executable graders or SME judgments.

## Historical phased build order

This sequence records the original roadmap, not current delivery status. Use
[current implementation](docs/architecture/current-implementation.md) for what exists.

1. **Phase 1**: Mock adapters + orchestrator + CLI + API + dashboard (no external deps)
2. **Phase 2**: Local models (SmolVLA, GroundingDINO, CoreML SAM2, MuJoCo/LIBERO)
3. **Phase 3**: Cloud models (GPT-4o, Qwen2.5-VL, CogACT on Azure GPU)
4. **Phase 4**: MCP servers + Azure AI Foundry integration

## Agent Invocation Guide

Use the specialized agents in `.claude/agents/` at the right moments. Do not skip agent consultation for non-trivial decisions.

### Planning Phase (before writing code)

| Decision | Consult | Why |
|----------|---------|-----|
| Which models to support, feature prioritization | **product-manager-advisor** | Validates against real robotics deployment scenarios, checks model availability |
| API data shapes, input/output formats, latency budgets | **principal-data-scientist** | Knows VLM/VLA tensor shapes, action spaces, image preprocessing constraints |
| System decomposition, adapter boundaries, MCP topology | **system-architecture-reviewer** | Reviews perception-memory-planning-control stack, validates hybrid deployment |
| Evaluation fairness, metric transparency, provider neutrality | **responsible-ai-code** | Ensures no implicit bias toward specific model providers |
| Dashboard layout, CLI UX, multi-access parity | **ux-ui-designer** | Validates workflows for all 3 personas (researcher, ML engineer, platform team) |

### Design & Implementation Phase (while writing code)

| Task | Consult | Why |
|------|---------|-----|
| New adapter implementation | **principal-data-scientist** + **system-architecture-reviewer** | Data scientist knows model-specific quirks (quantization, MPS ops, action clipping); architect validates Protocol compliance |
| Orchestrator changes | **system-architecture-reviewer** | Pipeline must stay deterministic — architect guards against LLM creep in orchestration |
| MCP server tools | **system-architecture-reviewer** + **product-manager-advisor** | Architect validates tool schema; PM validates against Foundry/agent framework use cases |
| API endpoint design | **principal-data-scientist** | Knows how VLM/VLA data differs from LLM-only APIs (image tensors, action arrays, proprioception) |
| Frontend components | **ux-ui-designer** | Ensures dashboard renders model comparison data effectively |
| CI/CD, Docker, deployment | **gitops-ci-specialist** | Handles GPU runners, model caching, environment matrix |

### Review Phase (before merging)

| What to review | Consult | What they check |
|----------------|---------|-----------------|
| Any adapter code | **code-reviewer** | Robotics AI pitfalls: coordinate frames, image preprocessing, action space normalization, sim-to-real gaps |
| Architecture changes | **system-architecture-reviewer** | Adapter pattern integrity, no tight coupling, MCP↔FastAPI separation, async safety |
| Metric calculations | **principal-data-scientist** + **responsible-ai-code** | Statistical validity, fair comparison methodology, no metric gaming |
| User-facing changes | **product-manager-advisor** | Feature aligns with real robotics workflows, not just research demos |
| Documentation | **technical-writer** | Consistent terminology, accurate code examples, API docs match implementation |

### Cross-Platform Sync (optional)

| Task | Consult |
|------|---------|
| Sync agent instructions across Claude/Copilot/AGENTS.md | **sync-coordinator** |

### Invocation Rules

1. **Always consult before implementing** — ask the agent during planning, not after the code is written
2. **Parallel consultation** — when a decision spans multiple domains (e.g., new adapter), invoke relevant agents in parallel
3. **The two core reviewers** — `code-reviewer` and `system-architecture-reviewer` are robotics engineers; they review both code quality AND domain correctness
4. **Data scientist for API design** — whenever designing data contracts that touch model inputs/outputs, consult `principal-data-scientist` first
5. **PM for feature gating** — before adding any model or feature, ask `product-manager-advisor` if it's realistic for production robotics

## Known Constraints

- **Cosmos-Reason-1**: Deprecated 2026-03-18 on Azure NIM
- **SAM2**: PyTorch MPS is broken — must use Apple CoreML version (`apple/coreml-sam2-large`)
- **OpenVLA int4**: bitsandbytes does NOT support MPS — use MLX quantization
- **GR00T N1.6**: Weights not yet publicly released
- **MCP binary limit**: 1MB max — resize images to <750KB before base64 encoding
