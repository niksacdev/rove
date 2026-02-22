# ROVE
### Robot Observation & Vision Evaluation

> Roving through model space so you don't have to.

**ROVE evaluates robotics AI pipelines — running real inference through VLM+VLA+LLM combinations to find the best agent stack for your specific task.**

---

A robot that can pick, place, or assemble needs multiple AI models working together as an agent system. A Vision-Language Model sees the scene. A grounding model locates the target object. An LLM or VLM plans the approach. A Vision-Language-Action model predicts the physical movements. And a VLM verifies the result.

No single model does all of this. The right combination depends on your task, your environment, and your constraints — latency, cost, accuracy. ROVE evaluates these combinations by running the full inference pipeline end-to-end, on your task, measuring what actually works before you deploy to a real robot.

```bash
rove evaluate --config rove.yaml --evaluation pick_bracket

# ROVE: Running evaluation 'pick_bracket'
# Task: Pick the red bracket and place it in bin A
# Models: 3 VLMs x 2 VLAs = 6 combinations x 5 trials = 30 runs
#
# Results (ranked by success rate > latency > cost):
#
#  1. gpt-4o + cogact-7b
#     92% success (46/50) | Avg: 1842ms | Cost: $0.021/run | Quality: 0.94
#
#  2. qwen25-vl-32b + cogact-7b
#     88% success (44/50) | Avg: 2105ms | Cost: $0.008/run | Quality: 0.91
#
#  3. gpt-4o + smolvla-450m
#     78% success (39/50) | Avg: 1203ms | Cost: $0.020/run | Quality: 0.87
#
#  4. cosmos-reason2-2b + cogact-7b
#     72% success (36/50) | Avg:  890ms | Cost: $0.000/run | Quality: 0.85
#  ...
#
# Results saved to results.jsonl
# Dashboard: http://localhost:8000/evaluations/eval_20260218_001
```

---

## Configuration-Driven Pipeline

ROVE uses a single YAML configuration file — inspired by infrastructure-as-code patterns — to define models, tasks, and pipeline runs. Everything is declared, nothing is hardcoded.

```yaml
# rove.yaml — single source of truth
version: "1.0"

# Models — atomic model configurations (one model per entry)
models:
  vlm:
    gpt-4o:
      adapter: azure_openai
      display_name: "GPT-4o"
      deployment_type: azure_managed
      config:
        endpoint_env: AZURE_OPENAI_ENDPOINT
        api_key_env: AZURE_OPENAI_KEY
        deployment_name: gpt-4o
        api_version: "2025-04-01-preview"
      cost: { type: per_token, input_per_1k: 0.005, output_per_1k: 0.015 }

    qwen25-vl-32b:
      adapter: azure_hf_managed
      display_name: "Qwen2.5-VL 32B"
      deployment_type: azure_managed
      config:
        endpoint_env: AZURE_QWEN_ENDPOINT
        api_key_env: AZURE_QWEN_KEY
      capabilities: [scene_analysis, native_grounding, verification]
      cost: { type: per_token, input_per_1k: 0.002, output_per_1k: 0.006 }

    cosmos-reason2-2b:
      adapter: local_mlx
      display_name: "Cosmos-Reason2 2B"
      deployment_type: local
      config:
        model_path: "mlx-community/cosmos-reason2-2b-4bit"
        max_tokens: 1024
      cost: { type: fixed, per_call: 0.00 }

    mock-vlm:
      adapter: mock_vlm
      display_name: "Mock VLM"
      deployment_type: local
      config:
        mock_quality: 0.85
        mock_latency_ms: [200, 500]

  vla:
    smolvla-450m:
      adapter: local_lerobot
      display_name: "SmolVLA 450M"
      deployment_type: local
      config:
        model_id: "HuggingFaceTB/SmolVLA-base"
        device: mps
        action_space: ee_delta
        chunk_size: 10
      cost: { type: fixed, per_call: 0.00 }

    cogact-7b:
      adapter: azure_gpu_http
      display_name: "CogACT 7B"
      deployment_type: azure_gpu
      config:
        endpoint_env: AZURE_GPU_COGACT_ENDPOINT
        denoising_steps: 10
        action_space: ee_delta
        chunk_size: 16
      cost: { type: per_second, rate: 0.0012 }

    mock-vla:
      adapter: mock_vla
      deployment_type: local
      config:
        mock_success_rate: 0.80
        mock_latency_ms: [50, 150]

  grounding:
    groundingdino:
      adapter: local_groundingdino
      config: { device: mps }
    mock-grounding:
      adapter: mock_grounding

  sim:
    mujoco-libero:
      adapter: local_mujoco
      config:
        task_suite: libero_spatial
        render_size: [640, 480]
    mock-sim:
      adapter: mock_sim

# Evaluations — named evaluation configurations (reference models by ID)
evaluations:
  pick_bracket:
    description: "Evaluate pick-and-place for red bracket"
    task: "Pick the red bracket and place it in bin A"
    image: scenes/bracket_scene.jpg
    vlms: [gpt-4o, qwen25-vl-32b, cosmos-reason2-2b]
    vlas: [cogact-7b, smolvla-450m]
    grounding: groundingdino
    sim: mujoco-libero
    trials: 5
    mode: full  # full | vlm_only
    tags: [pick-place, industrial]

  bracket_variations:
    description: "Test robustness across task variations"
    tasks:
      - "Pick the red bracket and place it in bin A"
      - "Pick the blue bolt and place it in bin B"
      - "Pick the green bracket and place it in bin C"
    image: scenes/multi_part_scene.jpg
    vlms: [gpt-4o, qwen25-vl-32b]
    vlas: [cogact-7b]
    grounding: groundingdino
    sim: mujoco-libero
    trials: 10
    tags: [variation-study, robustness]

  vlm_perception_only:
    description: "Compare VLM scene understanding (no sim needed)"
    task: "Identify all graspable objects on the workbench"
    image: scenes/cluttered_bench.jpg
    vlms: [gpt-4o, qwen25-vl-32b, cosmos-reason2-2b]
    vlas: []
    mode: vlm_only
    trials: 3
    tags: [perception, vlm-only]

  mock_test:
    description: "End-to-end test with mock models"
    task: "Pick the red bracket from bin A"
    vlms: [mock-vlm]
    vlas: [mock-vla]
    grounding: mock-grounding
    sim: mock-sim
    trials: 1
    tags: [test, mock]

# Defaults
defaults:
  grounding: mock-grounding
  sim: mock-sim
  trials: 1
  mode: full
  max_concurrent_combinations: 9
  timeout_per_step_ms: 30000
  max_retries_per_step: 2
```

Run any named evaluation:

```bash
rove evaluate --config rove.yaml --evaluation pick_bracket
rove evaluate --config rove.yaml --evaluation bracket_variations
rove evaluate --config rove.yaml --evaluation mock_test
```

Or override from the command line:

```bash
rove evaluate \
  --config rove.yaml \
  --task "Pick the red bracket" \
  --vlms gpt-4o,qwen25-vl-32b \
  --vlas cogact-7b \
  --trials 10
```

---

## Why ROVE Exists

Robots that interact with the physical world need a stack of AI models working together as an agent system: perception, reasoning, planning, and action. Today, building and testing these agent pipelines is manual work — writing custom scripts for each model combination, wiring them together, running a simulator, measuring what happened.

For a single VLM+VLA combination, this means coordinating five inference steps, a simulator, and repeated trials. For three VLMs and three VLAs, that is nine complete agent configurations. Nobody does this systematically.

ROVE evaluates these agent pipelines. You describe your task, select candidate models for each pipeline stage, and ROVE runs the full inference pipeline across every combination — measuring success rate, latency, and cost on *your* task, in *your* environment.

**ROVE is inference-only.** It does not train, fine-tune, or modify models. It runs them in realistic robotics pipelines and measures the results. The question ROVE answers is not "how good is this model?" but "how well does this agent stack — this specific combination of VLM, VLA, LLM, and grounding model — perform on this robotic task?"

---

## How It Works

Every pipeline run follows a fixed five-step inference sequence. The pipeline is deterministic — no LLM decides which steps to run. Models only participate inside their designated steps.

```
 ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
 │ perceive │───>│  ground  │───>│   plan   │───>│ execute  │───>│  verify  │
 │   (VLM)  │    │(Grounding│    │  (VLM)   │    │  (VLA +  │    │  (VLM)   │
 │          │    │  Model)  │    │          │    │   Sim)   │    │          │
 └──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
  Scene image     Bounding box    Grasp plan      Action chunk    Before/after
  → objects,      + segmentation  approach,       7-DOF deltas    comparison
  spatial         mask for        orientation,    executed in     → success/fail
  relationships   target object   confidence      simulator       + confidence
```

| Step | Model Type | Input | Output |
|------|-----------|-------|--------|
| perceive | VLM | Scene image + task description | Objects, spatial relationships, task-relevant observations |
| ground | Grounding | Image + target object name | Bounding box (normalized 0-1) + segmentation mask |
| plan | VLM | Image + scene analysis + bounding box | Grasp approach direction, strategy, confidence |
| execute | VLA + Sim | Sim observation + task + proprioception | 7-DOF action chunk → sim execution → post-execution observation |
| verify | VLM | Initial image vs post-execution image | Success (bool), confidence (0-1), reasoning |

When you specify 3 VLMs and 2 VLAs, ROVE creates 6 orchestrator instances and runs them concurrently via `asyncio.gather()`. Each streams progress in real time.

### VLM-Only Mode

When no simulator is configured, ROVE runs steps 1-3 only (perceive, ground, plan). This tests VLM scene understanding and planning quality without action execution. Useful for comparing VLM perception before investing in sim setup.

```bash
rove evaluate --config rove.yaml --evaluation vlm_perception_only
```

---

## What ROVE Reports

For every model combination on every task, across N trials:

| Metric | Description | Source |
|--------|-------------|--------|
| **Success rate** | Fraction of trials where the task completed successfully (e.g., 46/50 = 92%) | VLM verify step + sim ground truth |
| **Latency breakdown** | Per-step and total pipeline inference time in milliseconds | Measured at adapter boundary |
| **Cost** | API inference cost per step and total per run in USD | From `rove.yaml` cost config |
| **VLM verification confidence** | VLM self-reported confidence on verify step (0.0-1.0) | VLM verify output |
| **Judge calibration** | Does VLM verification agree with sim ground truth? | VLM success vs sim success |
| **Action safety** (Phase 2+) | Trajectory smoothness, workspace bounds compliance | Computed from action predictions |

Results are ranked by: success rate (descending) → latency (ascending) → cost (ascending).

---

## Supported Models

Phase 1 ships with mock adapters for pipeline development. Real model adapters are added in Phases 2 and 3.

### Vision-Language Models (VLMs)

| Model | Parameters | Hosting | Phase |
|-------|-----------|---------|-------|
| GPT-4o | — | Azure OpenAI | 3 |
| Qwen2.5-VL | 32B | Azure HF Managed Endpoints | 3 |
| Cosmos-Reason2 | 2B | Local (MLX, Apple Silicon) | 2 |
| Mock VLM | — | In-process | 1 (now) |

### Vision-Language-Action Models (VLAs)

| Model | Parameters | Hosting | Phase | Notes |
|-------|-----------|---------|-------|-------|
| SmolVLA | 450M | Local (LeRobot, MPS) | 2 | Flow matching, 10-step chunks |
| OpenVLA-OFT | 7B | Local (MLX quantization) | 2 | Autoregressive, 1 step/call. MLX required (bitsandbytes incompatible with MPS) |
| CogACT | 7B | Azure GPU VM | 3 | Diffusion, 10-100 denoising steps, 16-step chunks |
| GR00T N1.6 | 3B | Azure GPU VM | Blocked | Weights not yet public |
| Mock VLA | — | In-process | 1 (now) | |

### Grounding and Simulation

| Component | Hosting | Phase | Notes |
|-----------|---------|-------|-------|
| GroundingDINO | Local (PyTorch, CPU fallback) | 2 | MPS partially supported |
| SAM2 | Local (CoreML) | 2 | PyTorch MPS broken — CoreML only (`apple/coreml-sam2-large`) |
| MuJoCo + LIBERO | Local (native ARM) | 2 | 130+ manipulation tasks |
| Mock Grounding / Sim | In-process | 1 (now) | |

Adding a new model = implement one Protocol + add a YAML entry. No changes to orchestrator, API, or dashboard.

---

## Quick Start

### Install

```bash
pip install rove-eval
```

### Run with mocks (zero dependencies)

```bash
rove evaluate --task "Pick the red bracket" --vlms mock-vlm --vlas mock-vla
```

### Run a named evaluation from config

```bash
rove evaluate --config rove.yaml --evaluation pick_bracket
```

### List models and check health

```bash
rove models --config rove.yaml
rove models --config rove.yaml --check-health
```

### Launch the dashboard

```bash
rove serve --config rove.yaml
# Open http://localhost:8000
```

### Use as a Python library

```python
import rove

results = await rove.evaluate(
    config_path="rove.yaml",
    evaluation_id="pick_bracket",
)

for r in results.ranked:
    print(f"{r.vlm_id} + {r.vla_id}: {r.success_rate:.0%} success, {r.avg_latency_ms:.0f}ms")
```

### Export results

```bash
rove export --evaluation-id eval_20260218_001 --format jsonl --output results.jsonl
```

Exported JSONL uses Foundry-compatible field names (`query`, `response`, `context`) for `azure-ai-evaluation` SDK compatibility.

---

## Access Modes

ROVE has three equal access modes sharing the same pipeline engine.

| Mode | Best For | How |
|------|----------|-----|
| **CLI** | Scripted runs, CI pipelines, batch processing | `rove evaluate --config rove.yaml --evaluation pick_bracket` |
| **Python library** | Jupyter notebooks, programmatic analysis | `await rove.evaluate(config_path="rove.yaml", ...)` |
| **Web dashboard** | Interactive exploration, team demos | `rove serve` → `http://localhost:8000` |

The dashboard is **static HTML + vanilla JavaScript** served by FastAPI. No React, no npm, no build step. It calls the API via `fetch()` and streams progress via `EventSource` (SSE). Zero frontend dependencies — works anywhere FastAPI runs.

---

## API Reference

Evaluations are asynchronous: `POST /api/evaluations` returns immediately with a job ID.

```bash
# Submit an evaluation
curl -X POST http://localhost:8000/api/evaluations \
  -H "Content-Type: application/json" \
  -d '{"task": "Pick the red bracket", "vlm_ids": ["gpt-4o"], "vla_ids": ["cogact-7b"], "trials": 5}'
# → {"evaluation_id": "eval_abc123", "status": "queued"}

# Poll for results
curl http://localhost:8000/api/evaluations/eval_abc123

# Stream live progress (SSE)
curl -N http://localhost:8000/api/evaluations/eval_abc123/stream

# List all evaluations
curl http://localhost:8000/api/evaluations

# Get leaderboard
curl http://localhost:8000/api/leaderboard

# Export
curl http://localhost:8000/api/evaluations/eval_abc123/export?format=jsonl
```

Full OpenAPI spec at `http://localhost:8000/docs`.

---

## Adding a New Model

Two steps. No changes to any existing code.

**Step 1: Implement the Protocol.**

```python
# rove/adapters/vlm/my_model.py
from rove.adapters.protocols import VLMAdapter

class MyModelAdapter:
    """Implements VLMAdapter protocol."""

    def __init__(self, model_id: str, config: dict):
        self._model_id = model_id
        self._endpoint = config["endpoint"]

    @property
    def model_id(self) -> str:
        return self._model_id

    async def analyze_scene(self, image: bytes, task: str) -> SceneAnalysis:
        # Call your model, normalize output to SceneAnalysis
        ...

    async def plan_grasp(self, image: bytes, task: str,
                         scene: SceneAnalysis, bbox: BoundingBox) -> GraspPlan:
        ...

    async def verify_success(self, initial_image: bytes,
                             final_image: bytes, task: str) -> VerificationResult:
        ...

    async def health_check(self) -> bool:
        ...
```

**Step 2: Add to `rove.yaml`.**

```yaml
models:
  vlm:
    my-model-7b:
      adapter: my_model            # maps to rove.adapters.vlm.my_model
      display_name: "My Model 7B"
      deployment_type: self_hosted
      config:
        endpoint: http://localhost:8080
      cost: { type: fixed, per_call: 0.0 }
```

The model appears in `rove models` and is available in all evaluations.

---

## MCP Servers — Model-Agnostic Agent Building

ROVE exposes three MCP (Model Context Protocol) servers that let AI agents use ROVE's adapters as tools without coupling to specific models.

| Server | Port | Tools |
|--------|------|-------|
| VLM Server | 8081 | `analyze_scene`, `plan_grasp`, `verify_success` |
| VLA Server | 8082 | `predict_action` |
| Grounding + Sim | 8083 | `localize`, `segment`, `sim_reset`, `sim_step` |

**Why this matters**: An agent built in Claude Desktop, Copilot, or Azure AI Foundry calls `analyze_scene(image, task, model_id="gpt-4o")` through MCP. It gets structured scene analysis back. When you change the model in `rove.yaml`, the agent's behavior updates — no agent code changes. This is the "model-agnostic agent" pattern: the agent works at the task level, ROVE handles model routing.

MCP servers ship in Phase 4. The evaluation pipeline calls adapters directly (not through MCP) for zero-overhead performance.

---

## Architecture

```
                     ┌──────────────────────────────────────┐
                     │           Access Layer                │
                     │  CLI  │  Python Library  │  Dashboard │
                     │       │  (import rove)   │  (HTML+JS) │
                     └──────────────┬───────────────────────┘
                                    │
                     ┌──────────────▼───────────────────────┐
                     │         FastAPI Backend               │
                     │  POST /api/evaluations → async job   │
                     │  GET  /api/evaluations/:id → poll    │
                     │  GET  /api/evaluations/:id/stream    │
                     │  Serves static dashboard at /        │
                     └──────────────┬───────────────────────┘
                                    │ asyncio.create_task()
                     ┌──────────────▼───────────────────────┐
                     │       Evaluation Engine               │
                     │  RunManager → asyncio.gather(         │
                     │    Orchestrator(vlm_1, vla_1),        │
                     │    Orchestrator(vlm_1, vla_2),        │
                     │    Orchestrator(vlm_2, vla_1), ...    │
                     │  )                                    │
                     └──────┬──────────┬──────────┬─────────┘
                            │          │          │
                     ┌──────▼──┐ ┌─────▼────┐ ┌──▼────────┐
                     │   VLM   │ │   VLA    │ │ Grounding │
                     │ Adapter │ │ Adapter  │ │ + Sim     │
                     └─────────┘ └──────────┘ └───────────┘
                            ▲          ▲          ▲
                            │          │          │
                     ┌──────┴──────────┴──────────┴─────────┐
                     │         Adapter Registry              │
                     │  rove.yaml → model_id → adapter      │
                     │  Lazy loading, health checks          │
                     └──────────────────────────────────────┘

    External: MCP Servers (Phase 4, not in evaluation path)
    ┌──────────────┐ ┌──────────────┐ ┌────────────────────┐
    │ VLM :8081    │ │ VLA :8082    │ │ Grounding+Sim:8083 │
    └──────┬───────┘ └──────┬───────┘ └──────┬─────────────┘
           └────────────────┴────────────────┘
              Same adapters, different entry point
              Used by: Claude, Copilot, Foundry agents
```

Key decisions:
- **The API is the product.** Delete the dashboard — evaluation still works.
- **Adapters are Protocols, not base classes.** Structural typing via `@runtime_checkable`.
- **The orchestrator is deterministic.** Five fixed steps. LLMs execute inside adapters only.
- **Configuration over code.** `rove.yaml` is the single source of truth.
- **Async jobs.** `POST /api/evaluations` returns immediately. SSE streams progress.
- **Plain HTML+JS dashboard.** No npm, no build step, no frontend dependencies.

---

## Project Structure

```
rove/                              # Python package (import rove)
├── __init__.py                    # Public API: evaluate(), serve()
├── models.py                      # Shared dataclasses: SceneAnalysis, ActionPrediction, etc.
├── config.py                      # Load rove.yaml, resolve env vars
├── cli.py                         # Click CLI: evaluate, models, export, serve
├── adapters/
│   ├── protocols.py               # THE contracts: VLMAdapter, VLAAdapter, etc.
│   ├── registry.py                # YAML → adapter resolution + health checks
│   ├── vlm/                       # mock, azure_openai, azure_hf, local_mlx
│   ├── vla/                       # mock, local_lerobot, local_openvla, azure_gpu
│   ├── grounding/                 # mock, local_groundingdino, local_coreml_sam2
│   └── sim/                       # mock, local_mujoco
├── orchestrator/
│   ├── agent.py                   # RoveOrchestrator — 5-step pipeline
│   └── run_manager.py             # Parallel VLM×VLA execution
├── evaluation/
│   ├── store.py                   # SQLite + JSONL export (Foundry-compatible)
│   └── leaderboard.py             # Ranking and aggregation
├── api/
│   ├── app.py                     # FastAPI app factory
│   ├── routes.py                  # All API endpoints
│   └── sse.py                     # Server-Sent Events helper
├── mcp_servers/                   # Phase 4: FastMCP servers
│   ├── vlm_server.py              # Port 8081
│   ├── vla_server.py              # Port 8082
│   └── grounding_sim_server.py    # Port 8083
└── dashboard/                     # Static HTML+JS (no build step)
    ├── index.html
    ├── app.js
    └── style.css

rove.yaml                          # Model registry + evaluation configs
pyproject.toml                     # Package: rove-eval
```

---

## Roadmap

**Phase 1 — Mock-first foundation** (current)
Complete pipeline with mock adapters. CLI, API, dashboard. Zero external dependencies. Named evaluations from `rove.yaml`.

**Phase 2 — Local models**
SmolVLA (LeRobot/MPS), OpenVLA-OFT (MLX), GroundingDINO, SAM2 (CoreML), MuJoCo + LIBERO, Cosmos-Reason2 (MLX). Runs entirely on Apple Silicon.

**Phase 3 — Cloud models**
GPT-4o (Azure OpenAI), Qwen2.5-VL (Azure HF), CogACT (Azure GPU). Full cost tracking.

**Phase 4 — MCP + Azure AI Foundry**
Three MCP servers. Foundry-compatible JSONL export validated. ROVE evaluators registerable in Foundry.

---

## What ROVE Does Not Do

- **No model training or fine-tuning** — ROVE is inference-only. It consumes models, it does not produce or modify them.
- **No real robot control** — evaluation runs in simulation (MuJoCo + LIBERO). Output is data, not robot motion.
- **No model serving infrastructure** — ROVE calls models (local or cloud), it does not host them.
- **No general-purpose LLM evaluation** — ROVE evaluates robotics agent pipelines, not chatbots or text generation.
- No authentication or access control
- No multi-tenant support
- No streaming video input

---

## Name

ROVE is an anagram of VERO — Latin for "truly." The goal is honest, reproducible evaluation of robotics agent pipelines on real tasks — not cherry-picked benchmark results, not isolated model scores, but end-to-end pipeline performance on the task you actually need to deploy.

*Roving through model space so you don't have to.*

---

## License

MIT — See [LICENSE](LICENSE).
