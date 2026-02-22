# ROVE — Robot Observation & Vision Evaluation

## Project Overview

ROVE evaluates robotics agent pipelines by running real inference through VLM+VLA+LLM+Agent combinations. It orchestrates a standardized 4-step pipeline (perceive → plan → act → verify) across multiple model combinations in parallel, producing ranked comparisons of success rate, latency, and cost for each agent configuration.

ROVE is inference-only. It does not train, fine-tune, or modify models. It runs them in realistic robotics pipelines and measures the outcomes.

- **PyPI package**: `rove-eval`
- **Python import**: `import rove`
- **CLI command**: `rove`

## Architecture Principles

1. **The API is the product.** CLI, Python library, Jupyter, and dashboard all use the same engine. If you delete `frontend/`, the evaluation system still works.
2. **Adapter pattern via Python Protocols.** `VLMAdapter`, `PolicyAdapter`, `AgentAdapter`, `SimAdapter` are `@runtime_checkable` Protocols. Adding a new model = implement the Protocol + add a YAML config entry. No other code changes.
3. **Configuration over code.** `rove.yaml` is the single source of truth for all model configurations and evaluation definitions. Both backend and frontend read from it.
4. **Deterministic orchestrator.** The 5-step pipeline is fixed. The orchestrator does NOT use an LLM to decide steps. LLMs are only used by adapters.
5. **Azure AI Foundry alignment.** Evaluation data uses Foundry-compatible field names (`query`, `response`, `context`). JSONL export works with `azure-ai-evaluation` SDK.

## Key Design Decisions

- **3 MCP servers** (VLM:8081, VLA:8082, Grounding+Sim:8083) — thin wrappers routing to adapters via `model_id`. FastAPI backend calls adapters directly for low latency. MCP servers expose tools externally for Claude, Copilot, and agent frameworks.
- **Hybrid deployment** — Cloud VLMs (Azure OpenAI, NIM, HF), local VLAs (LeRobot, MPS), local grounding (PyTorch/CoreML), local sim (MuJoCo).
- **Mock-first development** — Phase 1 runs entirely on mock adapters. All real model adapters come in Phase 2+.

## Technology Stack

### Backend (Python)
- **FastAPI** — async REST + WebSocket + OpenAPI spec generation
- **Click** — CLI (`rove evaluate`, `rove models`, `rove export`)
- **PyYAML** — model registry configuration
- **SQLite** — evaluation persistence (stdlib)
- **FastMCP v2.x** — MCP server framework
- **Pydantic** — request/response models

### Frontend (Plain HTML+JS)
- **Static HTML + vanilla JS** — no React, no npm, no build step
- Served by FastAPI as static files
- Dark theme (`#09090b` background)
- **SSE (Server-Sent Events)** — real-time streaming via `EventSource`

### ML Libraries (Phase 2+)
- `torch` (MPS backend), `transformers`, `mlx`, `lerobot`, `mujoco`, `coremltools`

## Project Structure

```
rove/                              # Python package (import rove)
├── adapters/
│   ├── protocols.py               # THE contracts — VLMAdapter, PolicyAdapter, AgentAdapter, SimAdapter
│   ├── registry.py                # YAML → adapter resolution + health checks
│   ├── vlm/                       # mock, azure_openai, azure_nim, azure_hf, local_mlx
│   ├── vla/                       # mock, local_lerobot, local_hf, azure_gpu_http
│   ├── agent/                     # mock, azure_foundry (Foundry agents)
│   ├── grounding/                 # mock, local_groundingdino, local_coreml_sam2
│   └── sim/                       # mock, local_mujoco
├── orchestrator/
│   ├── agent.py                   # RoveOrchestrator (5-step pipeline)
│   └── run_manager.py             # Parallel VLM × VLA execution
├── evaluation/
│   ├── store.py                   # SQLite + JSONL export (Foundry-compatible)
│   └── leaderboard.py             # Ranking aggregation
├── mcp_servers/                   # 3 MCP servers (FastMCP)
│   ├── vlm_server.py              # Port 8081
│   ├── vla_server.py              # Port 8082
│   └── grounding_sim_server.py    # Port 8083
├── api/
│   ├── app.py                     # FastAPI app factory
│   └── websocket.py               # WebSocket handler
├── models.py                      # Pydantic + dataclass shared models
├── config.py                      # Load models.yaml, env vars
└── cli.py                         # Click CLI entry point
```

## Coding Conventions

### Python
- Python 3.11+ (use modern type hints: `str | None`, `list[str]`)
- Async-first: all adapter methods and orchestrator are `async`
- Dataclasses for domain objects (`SceneAnalysis`, `TaskPlan`, `ActionPrediction`, etc.)
- Pydantic for API request/response models
- No global state — pass dependencies explicitly (registry, store)
- Protocol pattern: adapters implement `@runtime_checkable` Protocols, never inherit from ABC

### TypeScript/React
- Strict mode TypeScript
- Types auto-generated from FastAPI OpenAPI spec
- Functional components with hooks
- No business logic in frontend — render what API tells it, send user actions to API

### Testing
- Mock adapters are the primary testing tool for Phase 1
- Test the pipeline end-to-end with mocks before adding real adapters
- Use `pytest` with `pytest-asyncio` for async tests

## Important Files

| File | Purpose |
|------|---------|
| `rove.yaml` | Single source of truth for models and evaluation configurations |
| `rove/adapters/protocols.py` | Adapter contracts — change these carefully |
| `rove/orchestrator/pipeline.py` | 4-step pipeline — deterministic, no LLM decisions |
| `rove/models.py` | Shared data models (Pydantic + dataclass) |
| `docs/PRODUCT_SPEC.md` | Full product specification and reference |

## What NOT to Build

- No model training or fine-tuning (ROVE is inference-only — it consumes models, not produces them)
- No real robot control (simulation only — output is data, not robot motion)
- No model serving infrastructure (ROVE calls models, does not host them)
- No general-purpose LLM evaluation (robotics agent pipelines only)
- No authentication/RBAC
- No multi-tenant support

## Terminology

- **adapter** (not connector/driver) — model-specific implementation of a Protocol
- **orchestrator** (not runner/agent) — the 4-step pipeline executor
- **evaluation** (not run/experiment) — a top-level assessment of agent pipeline configurations on a task
- **combination** (not pair/config) — a specific agent pipeline configuration (VLM+VLA+LLM+Agent pairing)
- Pipeline steps: **perceive**, **plan**, **act**, **verify** (always lowercase in code/prose)
- **task_planning** (not grasp_planning) — the capability for the plan stage

## Phased Build Order

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
