# ROVE Product Specification

**Version**: 0.3
**Status**: Active
**Last updated**: 2026-02-22

---

## 1. First Principles

A robot that manipulates objects in the real world needs to:

1. **See** (perceive) — understand what is in the scene, including object localization (VLM, with grounding folded in)
2. **Plan** — decide how to accomplish the task (VLM, LLM, or Foundry agent)
3. **Act** — predict physical movements to execute the plan (VLA)
4. **Verify** — confirm the task succeeded (VLM)

This is not one model's job. It is an agent pipeline — multiple models orchestrated together. The right combination of VLM, VLA, LLM, and grounding model depends entirely on the task, the environment, and the deployment constraints (latency, cost, hardware).

Today, robotics teams have no systematic way to test these agent pipelines. They pick models based on paper benchmarks, vendor recommendations, or ad hoc scripts. Benchmark scores measure isolated model performance on curated tasks — they do not tell you how a VLM+VLA combination performs on *your* bin-picking task under *your* warehouse lighting.

ROVE evaluates agent pipelines end-to-end. You define your task, select candidate models for each pipeline stage, and ROVE runs the full inference pipeline across every combination in simulation — measuring success rate, latency, and cost. The result is a ranked comparison of agent configurations for your specific scenario.

**ROVE is inference-only.** It does not train, fine-tune, or modify models. It runs them in realistic robotics pipelines and measures the outcomes.

---

## 2. Core Value Proposition

**ROVE evaluates robotics agent pipelines on your task.**

The unit of evaluation is not a model — it is a complete pipeline configuration: which VLM perceives the scene, which model plans the task, which VLA or agent executes the actions, running in simulation against your task description and scene image.

Three concrete implications:

1. **You bring your task, not a benchmark.** ROVE evaluates agent pipelines against what you actually need to deploy.
2. **Rankings are task-specific.** The VLM+VLA combination that tops LIBERO-Spatial may rank third on your industrial bin-picking task.
3. **Variation study is a first-class workflow.** Define task variations in YAML. ROVE reports which agent configurations are robust and which degrade.

---

## 3. Target Users

### Persona 1: Robotics Researcher

Works at a robotics company, academic lab, or AI lab on manipulation tasks. Familiar with Python, LIBERO, and at least one VLA framework.

**Today**: Tests each VLM+VLA combination manually with custom scripts. No systematic way to compare agent pipeline configurations.

**With ROVE**: Single CLI command runs the full pipeline across all model combinations in parallel. Python API for notebook analysis. JSONL export for downstream tools.

### Persona 2: ML Engineer on a Manipulation Team

Building a specific product: bin picking, assembly, kitting. Has production requirements — success rate targets (>95%), cycle time budgets, cost constraints.

**Today**: No structured way to justify agent configuration choices. Pipeline testing is ad hoc and not reproducible.

**With ROVE**: Controlled pipeline evaluation with per-step latency, cost tracking, and Foundry-compatible export. Data-driven agent configuration decisions.

### Persona 3: Platform Team / Azure AI Foundry Administrator

Responsible for AI infrastructure. Registers tools in Foundry for development teams.

**Today**: Agent frameworks hardcode model calls. Changing models requires agent code changes.

**With ROVE**: MCP servers expose pipeline capabilities as tools. Agent calls `analyze_scene`; ROVE routes to the configured model. Swap models via YAML, no agent code changes.

---

## 4. Configuration-Driven Design (Inspired by SHIVA Pattern)

ROVE uses a single YAML file (`rove.yaml`) that defines:

- **Models** (like SHIVA resources): Atomic model configurations — one entry per model with adapter type, credentials, cost, and capabilities.
- **Strategies** (like SHIVA experiments): Named pipeline configurations that map one model per stage (perceive, plan, act, verify, sim). Each strategy is a complete agent pipeline configuration ready to run.
- **Defaults**: Global settings for concurrency, timeouts, retries.

This pattern means:
- Adding a model = one YAML entry + one adapter class
- Creating a strategy = one YAML block mapping models to pipeline stages
- Running an evaluation = `rove evaluate --strategy mock`
- Reproducing an evaluation = share the YAML file

The YAML is the single source of truth. CLI, Python library, API, and dashboard all read from it.

---

## 5. What Models Return — The Unification Challenge

### VLMs Return Structured Scene Understanding

VLMs are text-in, text-out systems with no robotics-specific output format. The adapter is responsible for: (a) prompting for structure, (b) parsing the output, (c) normalizing to ROVE's data models.

**GPT-4o**: Supports `response_format={"type": "json_schema"}` for guaranteed valid JSON. Most reliable structured output.

**Qwen2.5-VL-32B**: No native JSON mode. Adapter prompts for JSON and uses fallback parsing. Has native grounding — can return bounding boxes directly via `<|box_start|>(x,y),(x,y)<|box_end|>` tokens in 1000x1000 normalized space.

**Cosmos-Reason2 2B**: Removed from rove.yaml due to unreliable JSON output (20-30% malformed responses from a 2B model). Not recommended for pipeline evaluation.

**Key constraint**: VLMs operating on a single 2D image cannot provide metric 3D coordinates. `SceneAnalysis` uses simple structured fields (`objects: list[dict]`, `spatial_relations: list[str]`, `task_relevant: list[str]`). VLMs with native grounding (e.g., Qwen) can return bounding boxes directly, folding localization into the perceive stage.

### VLAs Return Actions in Incompatible Formats

Every VLA architecture produces different output. The adapter normalizes to a common `ActionPrediction` format:

| VLA | Architecture | Steps/Call | Action Space | Normalization Required |
|-----|-------------|------------|--------------|----------------------|
| SmolVLA 450M | Flow matching | 10 (chunk) | EE delta, LeRobot-normalized | Denormalize using dataset stats |
| OpenVLA-OFT 7B | Autoregressive | 1 | Discrete tokens (256 bins/dim) | Detokenize + denormalize |
| CogACT 7B | Diffusion | 16 (chunk) | EE delta, may need FK | Denoising steps config |
| GR00T N1.6 3B | Cross-embodiment | 10-16 | Embodiment-dependent | Requires embodiment_id |

**Canonical action space**: ROVE normalizes to end-effector delta format: `[dx, dy, dz, droll, dpitch, dyaw, gripper]` in meters/radians/[0,1]. The adapter performs the conversion; raw model output is preserved for debugging.

**Proprioception**: SmolVLA requires current joint state as input. OpenVLA does not. The Protocol accepts optional `proprioception: list[float] | None`. The orchestrator always queries the sim for current state and passes it; whether it's used is the adapter's decision.

**Confidence is architecture-dependent**: Only OpenVLA (autoregressive) has principled per-token logprobs. Flow matching and diffusion models have no natural confidence score. The `confidence` field is optional and adapter-defined — not a standardized cross-model metric.

### The Gripper Convention Problem

VLAs use gripper values in [0, 1] (0=open, 1=closed). LIBERO expects [-1, 1] (-1=open, 1=closed). The sim adapter performs this conversion. Every VLA adapter documents its gripper convention.

---

## 6. Where Simulators Fit

### What the Simulator Provides

1. **Initial observation**: `sim.reset(task_id)` → RGB image + depth + joint positions + object poses
2. **Action execution**: `sim.step(action)` for each action in the VLA's predicted chunk
3. **Ground truth success**: LIBERO provides per-task success functions that check object poses against target conditions
4. **Post-execution observation**: The rendered image after actions execute — this is the "after" image for the verify step
5. **Controlled variation**: Reset to known state between trials, vary object placement

### The Execute Step in Detail

```
VLA.predict_action(sim_image, task, proprio) → ActionPrediction (e.g., 10 steps)
    ↓
for each action_step in ActionPrediction.actions:
    action_libero = convert_to_libero_format(action_step)  # gripper [0,1]→[-1,1], clip to bounds
    obs, reward, done, info = sim.step(action_libero)
    if info['success'] or done: break
    ↓
final_observation = obs['agentview_image']  # post-execution RGB
sim_success = info['success']               # physics ground truth
```

### VLM-Only Mode (No Simulator)

When no sim is configured, ROVE evaluates stages 1-2 only (perceive and plan). This produces:
- VLM scene understanding quality (object detection, spatial reasoning)
- Planning quality (strategy, reasoning, confidence)
- Latency and cost comparisons
- **NOT** success rate (requires execution)

VLM-only mode is valid for: comparing VLM perception across models, evaluating on real robot before/after images, Phase 1 development.

### LIBERO Specifics

- **Action space**: 7-DOF delta EE in robot base frame
- **Workspace bounds**: position delta max 5cm/step, rotation max 0.2 rad/step
- **Observation**: 256x256 RGB (agentview + optional wrist cam), depth, full robot state
- **Success**: Binary, checked via BDDL task conditions on object poses
- **Tasks**: 130+ across LIBERO-Spatial, LIBERO-Object, LIBERO-Goal, LIBERO-Long

---

## 7. MCP Server Role

### The Model-Agnostic Agent Pattern

MCP servers expose ROVE's adapters as callable tools for AI agents. An agent built in Claude, Copilot, or Foundry calls `analyze_scene` and receives structured scene analysis without being coupled to any specific model.

```
Agent:     "analyze this scene for pick-and-place"
  → MCP:   analyze_scene(image, task, model_id="gpt-4o")
  → ROVE:  AdapterRegistry.get_vlm("gpt-4o").analyze_scene(image, task)
  → Agent: receives SceneAnalysis JSON
```

Change `model_id` in the MCP call (or change the default in `rove.yaml`) — agent code stays the same.

### Why MCP Is Separate from the Evaluation Path

The evaluation engine calls adapters directly (in-process Python). Routing adapter calls through HTTP for multi-strategy evaluation would add significant overhead. MCP servers are for external agent integration only — a different use case with different latency tolerance.

### Image Size Constraint

MCP has a 1MB binary payload limit. MCP server handlers resize images to <750KB before base64 encoding. The evaluation path (FastAPI → adapters) is not subject to this limit.

---

## 8. Key Workflows

### Workflow 1: Single Strategy Evaluation

```yaml
# In rove.yaml
strategies:
  cloud-fast:
    display_name: "Cloud Fast"
    perceive: gpt-4o
    plan: gpt-4o
    act: pi0-fast
    verify: gpt-4o
    sim: mujoco-libero
```

```bash
rove evaluate --strategy cloud-fast --task "Pick the red bracket and place it in bin A" --image scenes/bracket.jpg
```

### Workflow 2: Multi-Strategy Comparison

```yaml
strategies:
  cloud-fast:
    display_name: "Cloud Fast"
    perceive: gpt-4o
    plan: gpt-4o
    act: pi0-fast
    verify: gpt-4o
    sim: mujoco-libero

  local-only:
    display_name: "Local Only"
    perceive: qwen3-vl-8b
    plan: qwen3-8b
    act: smolvla-450m
    verify: qwen3-vl-8b
    sim: mujoco-libero

  mock:
    display_name: "Mock (Testing)"
    perceive: mock-vlm
    plan: mock-vlm
    act: mock-vla
    verify: mock-vlm
    sim: mock-sim
```

```bash
rove evaluate --strategy cloud-fast,local-only --task "Pick the red bracket" --trials 5
```

Reports which strategy performs best on your task, with per-stage latency and cost breakdowns.

### Workflow 3: Task Variation Study

```bash
rove evaluate --strategy cloud-fast --tasks tasks/bracket_variations.yaml --trials 10
```

Runs a single strategy against multiple task descriptions. Reports which tasks the strategy handles well and where it degrades.

---

## 9. Phased Delivery

### Phase 1: Mock-First Foundation
- Mock adapters for VLM, VLA, Agent, Sim
- Full 4-stage pipeline (perceive, plan, act, verify)
- CLI: `rove evaluate`, `rove models`, `rove export`, `rove serve`
- FastAPI with async job queue + SSE streaming
- Static HTML+JS dashboard (no npm)
- SQLite persistence + JSONL export
- Named strategies from `rove.yaml`

**Success criteria**: `rove evaluate --strategy mock` completes in <5s. CLI, library, and dashboard produce identical results.

### Phase 2: Local Models
- SmolVLA (LeRobot/MPS), OpenVLA-OFT (MLX), GroundingDINO, SAM2 (CoreML)
- MuJoCo + LIBERO integration
- Real success rate from sim ground truth

**Success criteria**: Full 4-stage evaluation with real models on Apple Silicon. Sim-based success rate operational.

### Phase 3: Cloud Models
- GPT-4o (Azure OpenAI), Qwen2.5-VL (Azure HF), CogACT (Azure GPU)
- Per-call cost tracking

**Success criteria**: Cloud vs local comparison produces meaningful cost/latency tradeoffs.

### Phase 4: MCP + Foundry
- Three MCP servers (FastMCP v2)
- Foundry JSONL export validation
- Foundry evaluator registration docs

**Success criteria**: Claude agent calls ROVE MCP tools. Model swap via YAML without agent code changes.

---

## 10. Non-Goals

These will not be built:

- **Model training or fine-tuning** — ROVE is inference-only. It consumes models, it does not produce or modify them.
- **Real robot control** — Evaluation runs in simulation. Output is data, not robot motion.
- **Model serving** — ROVE calls models (local or cloud), it does not host them.
- **General LLM evaluation** — ROVE evaluates robotics agent pipelines (VLM+VLA+LLM combinations), not chatbots or text generation.
- **Autonomous model selection** — ROVE produces ranked comparisons. Humans decide which agent configuration to deploy.
- **Authentication/RBAC** — Single-tenant tool
- **Multi-tenant SaaS** — Single team/organization
- **Streaming video input** — Single images or image pairs

---

## 11. Known Constraints

| Constraint | Impact | Mitigation |
|-----------|--------|------------|
| Cosmos-Reason-1 deprecated 2026-03-18 | Not supported | Removed from rove.yaml. Cosmos-Reason2 2B also removed due to 20-30% malformed JSON output |
| SAM2 PyTorch MPS broken | Cannot use PyTorch path | CoreML version only (`apple/coreml-sam2-large`) |
| OpenVLA int4: bitsandbytes incompatible with MPS | Cannot use standard quantization | MLX quantization required |
| GR00T N1.6 non-commercial license | N1/N1.5 weights ARE public (`nvidia/GR00T-N1-2B`, `nvidia/GR00T-N1.5-3B`). N1.6 available on GitHub (non-commercial) | Implement adapter for N1/N1.5 first |
| MCP 1MB binary limit | Large images fail | Resize to <750KB in MCP server layer |
| VLM confidence scores not calibrated across models | Cannot compare GPT-4o confidence to Qwen confidence | Report as model-specific, track judge calibration vs sim |
| Local model concurrency on MPS | Single device queue | Per-adapter semaphores, serialize MPS calls |

---

## Appendix: Terminology

| Term | Definition |
|------|-----------|
| adapter | Model-specific implementation of a Protocol |
| orchestrator | The 4-stage pipeline executor |
| evaluation | Top-level assessment session with one or more strategies over N trials |
| strategy | A named pipeline configuration mapping models to stages (perceive, plan, act, verify, sim) |
| trial | A single execution of a strategy on a task |
| variation study | Evaluation across related but distinct task descriptions |
| perceive, plan, act, verify | The four pipeline stages (always lowercase) |
