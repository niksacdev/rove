# ROVE

## Robot Observation & Vision Evaluation

> Roving through model space so you don't have to.

**ROVE evaluates robotics agent pipelines — running real inference through VLM+VLA+Agent combinations to find the best agent stack for your specific task.**

![ROVE Dashboard](docs/images/rove-welcome.png)

Upload a scene image, describe a manipulation task, select strategies, and ROVE runs the full inference pipeline — comparing success rate, latency, and cost across agent configurations in real time.

**ROVE is inference-only.** It does not train, fine-tune, or modify models. It runs them in realistic robotics pipelines and measures the outcomes.

---

## Capabilities

- **Multi-strategy parallel evaluation** — run multiple agent pipeline configs concurrently, stream results via SSE
- **4-stage deterministic pipeline** — perceive → plan → act → verify, each stage independently configurable
- **MuJoCo dynamics verification** — joint limits, collision, torque feasibility, gravity compensation, and manipulability checks on VLA trajectories
- **Dynamics sanity checks** — objective physics measurements override VLM plausibility scores when dynamics contradicts the judge
- **URDF support** — upload or auto-detect from dataset robot metadata
- **Per-stage latency budgets** — set time limits per stage with strategy-level overrides
- **Side-by-side strategy comparison** — diff highlighting across pipeline configurations
- **Dataset browser** — robo2VLM and LIBERO examples with ground truth QA
- **Extensible adapter pattern** — add any model by implementing one Protocol + one YAML entry

---

## How It Works

ROVE runs pipelines in two modes, automatically selected based on whether a VLA is assigned.

### Sequential Pipeline (VLM-only)

Each stage feeds the next in a linear chain. Used when no VLA is assigned to the act stage.

```text
 ┌──────────┐    ┌──────────┐    ┌──────────┐
 │ perceive │───>│   plan   │───>│  verify  │
 │   (VLM)  │    │  (VLM)   │    │  (VLM)   │
 └──────────┘    └──────────┘    └──────────┘
```

### VLA Evaluation (Parallel)

VLAs (pi0.5, SmolVLA, etc.) take only `image + task + proprioception` — they never consume perceive/plan outputs (64-200 token context windows). ROVE reflects this honestly:

1. **Execution phase**: VLA runs independently (image + task → actions)
2. **Evaluation phase**: Perceive, plan, dynamics, and verify assess the VLA output

```text
 EXECUTION                    EVALUATION
 ┌──────────┐                ┌──────────┐    ┌──────────┐
 │ VLA Act  │                │ perceive │───>│   plan   │
 │ (image + │                └──────────┘    └────┬─────┘
 │  task)   │   ┌──────────┐                     │
 └────┬─────┘   │ dynamics │─────────────────────┤
      └────────>│ (MuJoCo) │                     │
                └──────────┘              ┌──────┴─────┐
                                          │   verify   │
                                          └────────────┘
```

| Stage | Input | Output |
|-------|-------|--------|
| **perceive** | Scene image + task | Objects, spatial relationships |
| **plan** | Scene analysis + task | Strategy, steps, confidence |
| **act** | Image + task + proprioception | Action trajectory or tool calls |
| **dynamics** | VLA trajectory + URDF | Joint limits, collisions, torques, manipulability |
| **verify** | All evaluation context | Success/fail + confidence; dynamics sanity checks override scores |

Strategies run concurrently, streaming progress via SSE in real time. The dashboard shows "Execution" and "Evaluation" phase headers for VLA strategies.

---

## MuJoCo Dynamics Verification

When a strategy includes VLA action execution, ROVE can optionally compute
full dynamics analysis on the predicted trajectory using MuJoCo. Upload your
robot's URDF alongside the scene image, and ROVE converts raw action arrays
into spatial and physical analysis the verifier can reason about.

```text
 act stage output              Dynamics analysis (MuJoCo)
 ─────────────────             ──────────────────────────
 [[0.02, -0.01, ...], ...]  →  Endpoint: [0.34, 0.21, 0.46]
                                Joint limits: within bounds
                                Torque feasible: yes
                                Gravity hold: feasible (0.5kg)
                                Manipulability: 0.0142
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
    compute_dynamics: true  # MuJoCo dynamics engine
```

Requires: `pip install rove-eval[kinematics]` (adds MuJoCo ~5MB)

### Dynamics Sanity Checks

After the verify stage, ROVE compares dynamics measurements against the VLM judge's
plausibility scores. When physics contradicts the judge, dynamics data wins:

| Check | Condition | Override |
|-------|-----------|----------|
| Joint limits | Any joint outside bounds | `bounds_check=False`, `plan_alignment` capped at 0.3 |
| Self-collision | Collision detected | `bounds_check=False`, `plan_alignment` capped at 0.3 |
| Torque limits | Required torque exceeds actuator limits | `bounds_check=False`, `plan_alignment` capped at 0.3 |
| Gravity comp | Holding + trajectory torques exceed limits | `bounds_check=False`, `plan_alignment` capped at 0.3 |
| Singularity | Min singular value < 0.01 at grasp | `plan_alignment` capped at 0.3 |
| Displacement | Total path > 2.0m | `smoothness=False` |

Overridden scores include `[Dynamics override: ...]` tags in the reasoning field so
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
