# ROVE
### Robot Observation & Vision Evaluation

> Roving through model space so you don't have to.

**ROVE evaluates robotics agent pipelines — running real inference through VLM+VLA+Agent combinations to find the best agent stack for your specific task.**

![ROVE Dashboard](docs/images/rove-welcome.png)

Upload a scene image, describe a manipulation task, select strategies, and ROVE runs the full inference pipeline — comparing success rate, latency, and cost across agent configurations in real time.

**ROVE is inference-only.** It does not train, fine-tune, or modify models. It runs them in realistic robotics pipelines and measures the outcomes.

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
| **act** | Sim observation + task | Action chunk executed in simulator |
| **verify** | Before/after images | Success/fail + confidence |

Strategies run concurrently, streaming progress via SSE in real time.

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
  qwen3-vl-8b:
    type: vlm
    adapter: lmstudio
    config:
      endpoint: "http://127.0.0.1:1234"
      model_id: "qwen3-vl-8b"

  mock-vlm:
    type: vlm
    adapter: mock_vlm

  mock-vla:
    type: vla
    adapter: mock_vla

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

  full_local:
    display_name: "Full Pipeline (Local)"
    perceive: qwen3-vl-8b
    plan: qwen3-vl-8b
    act: mock-vla
    verify: qwen3-vl-8b
    sim: mock-sim
```

Adding a new model = implement one Protocol + add a YAML entry. No changes to orchestrator, API, or dashboard.

---

## Adding a New Model

**1. Implement the Protocol:**

```python
# src/rove/adapters/vlm/my_model.py
class MyModelAdapter:
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
    adapter: my_model
    config:
      endpoint: http://localhost:8080
```

---

## What ROVE Does Not Do

- **No model training** — inference-only
- **No real robot control** — simulation only
- **No model serving** — calls models, does not host them
- **No general LLM evaluation** — robotics agent pipelines only

---

## License

MIT — See [LICENSE](LICENSE).
