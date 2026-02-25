# ROVE
### Robot Observation & Vision Evaluation

> Roving through model space so you don't have to.

**ROVE evaluates robotics agent pipelines — running real inference through VLM+VLA+Agent combinations to find the best agent stack for your specific task.**

![ROVE Dashboard](docs/images/rove-welcome.png)

Upload a scene image, describe a manipulation task, select strategies, and ROVE runs the full inference pipeline — comparing success rate, latency, and cost across agent configurations in real time.

**ROVE is inference-only.** It does not train, fine-tune, or modify models. It runs them in realistic robotics pipelines and measures the outcomes.

---

## Capabilities

- **Multi-strategy parallel evaluation** — run multiple agent pipeline configs concurrently, stream results via SSE
- **4-stage deterministic pipeline** — perceive → plan → act → verify, each stage independently configurable
- **Forward kinematics verification** — MuJoCo-based joint limit, collision, displacement, and smoothness checks on VLA trajectories
- **FK sanity checks** — objective FK measurements override VLM plausibility scores when physics contradicts the judge
- **URDF support** — upload or auto-detect from dataset robot metadata
- **Per-stage latency budgets** — set time limits per stage with strategy-level overrides
- **Side-by-side strategy comparison** — diff highlighting across pipeline configurations
- **Dataset browser** — robo2VLM and LIBERO examples with ground truth QA
- **Extensible adapter pattern** — add any model by implementing one Protocol + one YAML entry

---

## How It Works

Every evaluation runs a fixed four-stage pipeline. Stages are optional — a strategy defines which stages to run and which model handles each.

```
 ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
 │ perceive │───>│   plan   │───>│   act    │───>│  verify  │
 │   (VLM)  │    │  (VLM)   │    │  (VLA +  │    │  (VLM)   │
 │          │    │          │    │   Sim)   │    │          │
 └──────────┘    └──────────┘    └──────────┘    └──────────┘
```

| Stage | Input | Output |
|-------|-------|--------|
| **perceive** | Scene image + task | Objects, spatial relationships |
| **plan** | Scene analysis + task | Strategy, steps, confidence |
| **act** | Sim observation + task | Action chunk executed in simulator; FK analysis when enabled |
| **verify** | Before/after images | Success/fail + confidence; FK sanity checks override scores |

Strategies run concurrently, streaming progress via SSE in real time.

---

## Forward Kinematics Verification

When a strategy includes VLA action execution, ROVE can optionally compute
forward kinematics on the predicted trajectory using MuJoCo. Upload your
robot's URDF alongside the scene image, and ROVE converts raw action arrays
into spatial analysis the verifier can reason about.

```
 act stage output              FK analysis (MuJoCo)
 ─────────────────             ────────────────────
 [[0.02, -0.01, ...], ...]  →  Endpoint: [0.34, 0.21, 0.46]
                                Joint limits: within bounds
                                Self-collision: none detected
                                Smoothness: 0.95
```

Enable per strategy in `rove.yaml`:

```yaml
strategies:
  scene_plan_action:
    perceive: qwen3-vl-8b
    plan: qwen3-vl-8b
    act: pi05-libero
    verify: qwen3-vl-8b
    sim: mock-sim
    forward_kinematics: true   # enable FK verification
```

Requires: `pip install rove-eval[kinematics]` (adds MuJoCo ~5MB)

### FK Sanity Checks

After the verify stage, ROVE compares FK measurements against the VLM judge's
plausibility scores. When physics contradicts the judge, FK data wins:

| FK Check | Condition | Override |
|----------|-----------|----------|
| Joint limits | Any joint outside bounds | `plan_alignment` capped at 0.3, `confidence` capped at 0.4 |
| Self-collision | Collision detected | `plan_alignment` capped at 0.3, `confidence` capped at 0.4 |
| Displacement | Net displacement < 2cm | `plan_alignment` capped at 0.5, `confidence` capped at 0.5 |
| Smoothness | Smoothness score < 0.3 | `plan_alignment` capped at 0.6, `confidence` capped at 0.6 |

Overridden scores include `[FK override: ...]` tags in the reasoning field so
you can see exactly what triggered the correction.

URDF can be uploaded manually or auto-detected from dataset robot metadata
(e.g., LIBERO datasets include Panda robot type).

---

## Quick Start

```bash
pip install rove-eval
```

### Dashboard (interactive)

```bash
rove serve
# Open http://localhost:5001
```

1. Upload a workspace image
2. Describe a manipulation task
3. Select strategies to evaluate
4. Compare pipeline results across strategies

### CLI (batch processing)

```bash
rove evaluate --strategies mock
rove evaluate --strategies scene_detect,scene_plan
```

### Python library

```python
import rove

results = await rove.evaluate(
    config_path="rove.yaml",
    strategies=["mock", "scene_plan"],
)
```

---

## Configuration

`rove.yaml` is the single source of truth — models, strategies, and defaults.

```yaml
# Endpoints — one model per entry
endpoints:
  # Cloud VLMs
  gpt-4o:
    type: vlm
    adapter: azure_openai
    config: { deployment_name: gpt-4o, api_version: "2025-04-01-preview" }

  # Local VLMs (via LM Studio, Ollama, etc.)
  qwen3-vl-8b:
    type: vlm
    provider: lmstudio
    config: { model_id: "qwen/qwen3-vl-8b", endpoint: "http://127.0.0.1:1234" }

  # Local VLAs (via LeRobot)
  pi05-libero:
    type: vla
    adapter: local_lerobot
    config: { model_id: "lerobot/pi05_libero_finetuned", device: mps }

  smolvla-450m:
    type: vla
    adapter: local_lerobot
    config: { model_id: "lerobot/smolvla_base", device: mps }

  # Mock adapters for testing
  mock-vlm:
    type: vlm
    adapter: mock_vlm

# Strategies — named pipeline configurations
strategies:
  mock:
    display_name: "Mock (Test)"
    perceive: mock-vlm
    plan: mock-vlm
    act: mock-vla
    verify: mock-vlm
    sim: mock-sim

  scene_plan:
    display_name: "Scene + Plan"
    perceive: qwen3-vl-8b
    plan: qwen3-vl-8b
    verify: qwen3-vl-8b
    sim: mock-sim

  scene_plan_action:
    display_name: "Scene + Plan + Action (pi0.5)"
    perceive: qwen3-vl-8b
    plan: qwen3-vl-8b
    act: pi05-libero
    verify: qwen3-vl-8b
    sim: mock-sim
```

Adding a new endpoint = implement one Protocol + add a YAML entry. No changes to orchestrator, API, or dashboard.

---

## Extensibility

ROVE uses a Protocol-based adapter pattern with provider-level flexibility.
Adapters connect to **providers** — inference backends that can host any
compatible model. Point ROVE at a provider, swap the `model_id`, and evaluate
a different model with zero code changes.

- **LM Studio** — any vision-language model with an OpenAI-compatible API
  becomes a VLM adapter. Switch from Qwen3-VL to Phi-4 by changing one YAML
  field.
- **Azure AI Foundry** — agents built in Foundry can serve any pipeline stage.
  ROVE calls them through the Foundry SDK, so any agent you deploy there is
  immediately evaluable.
- **LeRobot** — any VLA checkpoint hosted on HuggingFace and loadable by
  LeRobot (pi0, SmolVLA, OpenVLA) works as an act-stage adapter.

Four Protocol types cover the pipeline:

| Protocol | Role | Providers |
|----------|------|-----------|
| `VLMAdapter` | perceive, plan, verify | LM Studio, Azure OpenAI |
| `VLAAdapter` | act (VLA) | LeRobot (pi0, SmolVLA, OpenVLA) |
| `AgentAdapter` | any stage via LLM agent | Azure AI Foundry |
| `SimAdapter` *(future)* | simulation environment | MuJoCo / LIBERO (planned) |

**1. Implement the Protocol:**

```python
# src/rove/adapters/my_adapter.py
class MyVLMAdapter:
    async def analyze_scene(self, image_base64, task, **kwargs): ...
    async def plan_task(self, image_base64, task, scene_analysis, **kwargs): ...
    async def verify_success(self, initial_image, final_image, task, **kwargs): ...
    async def health_check(self) -> bool: ...
```

**2. Add to `rove.yaml`:**

```yaml
endpoints:
  my-model-7b:
    type: vlm
    adapter: my_adapter
    config:
      model_id: "org/my-model-7b"
      endpoint: http://localhost:8080
```

The same pattern applies to VLA, agent, and sim adapters. Once registered in
`rove.yaml`, new models are immediately available in any strategy — just
reference them by ID.

---

## Design Philosophy

ROVE is **inference-only by design**. It calls models through their standard APIs
and measures what comes back — it never trains, fine-tunes, or modifies weights. This means:

- **Reproducible** — same inputs produce same outputs; no training state to drift
- **Safe for production endpoints** — read-only access to your deployed models
- **Hardware-agnostic measurement** — ROVE measures inference latency, not deployment
  overhead (sensor readout, network hops, controller processing). You set a latency
  budget; ROVE tells you which pipelines fit it
- **Robotics-specific** — evaluates complete agent pipelines (perceive → plan → act
  → verify), not isolated model capabilities

---

## License

MIT — See [LICENSE](LICENSE).
