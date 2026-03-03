# ROVE — Robot Observation & Vision Evaluation

**Task-Level Domain Evaluation for Robot Agent Stacks on Azure**

**Proposal for**: [`Azure-Samples/azure-nvidia-robotics-reference-architecture`](https://github.com/Azure-Samples/azure-nvidia-robotics-reference-architecture)
**Module**: `src/evaluation/`
**Date**: 2026-02-21

---

## 1. Problem

Existing robotics evaluation tools answer a model-layer question: **"How does this model score on this benchmark?"**

But the deployment question is different: **"Does my robot agent stack actually solve my task?"**

A real robotics deployment is not a single model. It is an agent stack:

- **Perception**: A VLM interprets the workspace — objects, spatial relationships, task-relevant features. Options range from GPT-4.1 / GPT-4.1-mini (cloud, reliable JSON, SOTA image understanding) to Qwen3-VL (local, native grounding, sub-200ms) to Phi-4-Multimodal (edge, sub-100ms on Jetson Orin).
- **Planning**: A reasoning layer translates perception into a manipulation strategy — approach direction, grasp type, collision avoidance. This might be the same VLM, a separate LLM, or embedded inside a VLA.
- **Control**: A policy produces motor commands — a VLA trained via LeRobot (ACT, diffusion), an RL policy trained via Isaac Lab (SKRL, RSL-RL), or a classical controller using perception outputs.
- **Verification**: Something checks whether the task succeeded — sim ground truth, a VLM comparing before/after images, or a world model predicting expected outcomes.

Today, evaluating this full stack on a team's own task requires weeks of custom engineering per combination.

### A concrete example: automotive parts kitting

An automotive supplier is building a robotic kitting cell. A UR10e arm picks fasteners, brackets, and clips from a shared bin and places them into kit trays for assembly stations. The agent stack needs:

- **Perception**: Identify 15+ part types in a cluttered bin under factory lighting (overhead fluorescent, shadows from the arm). Parts are reflective metal, some are nearly identical in shape but differ in size by 2mm.
- **Planning**: Determine grasp approach — some parts require top-down pinch, others require angled approach to avoid collision with bin walls.
- **Control**: Execute 7-DOF delta movements with 0.5mm precision. Cycle time budget: 4 seconds per pick.
- **Verification**: Confirm the correct part was placed in the correct tray slot. A misplaced M6 bolt where an M8 was expected stops the assembly line.

The team's evaluation questions:

1. **VLM perception accuracy**: Can GPT-4.1-mini reliably distinguish M6 from M8 bolts under factory lighting? Does Phi-4-Multimodal (running on the edge controller) miss the size difference? This is a domain-specific perception question that LIBERO benchmarks cannot answer.
2. **VLM-as-judge reliability**: After each pick-and-place, a VLM compares the tray image to the expected layout. Which VLM catches the M6/M8 swap? The team needs to calibrate this judge in simulation before trusting it on the production line.
3. **Instruction robustness**: The task instruction comes from the MES system. Sometimes it says "pick M8 hex bolt," sometimes "M8 bolt," sometimes just "bolt-08." Which VLM handles all phrasings correctly?
4. **Cost vs accuracy tradeoff**: GPT-4.1-mini costs $0.004/call but runs at 500ms. Phi-4-MM is free but runs locally at 80ms. If Phi-4-MM achieves 94% accuracy vs GPT-4.1-mini's 98%, the team might accept 94% and save $14,400/month in API costs at 100 picks/hour.

This evaluation runs in simulation (Isaac Lab + LeRobot) — not on the production line. But it answers questions that simulation-based RL training alone does not:

- **Baseline before fine-tuning.** Before investing compute fine-tuning a VLM or VLA on task-specific data, which model is the best starting point? Evaluate GPT-4.1-mini vs Phi-4-MM vs Qwen3-VL on your task to establish a baseline, then fine-tune the winner. Without a baseline, teams fine-tune the wrong model and only discover it weeks later.
- **Measurement after fine-tuning.** Fine-tuning improves a model, but by how much? On which task variations? At what cost tradeoff? The same evaluation pipeline that established the baseline measures the improvement — "fine-tuned Phi-4-MM now matches GPT-4.1-mini on M6/M8 discrimination at 1/10th the cost." This is the feedback loop that makes fine-tuning decisions data-driven rather than intuition-driven.
- **VLM-as-judge calibration.** When moving to the real line, teams need an automated success checker (no sim ground truth available). Which VLM should they trust as a judge? Calibrating VLM judge reliability against sim ground truth is what makes the transition from sim evaluation to production monitoring possible.

### Why existing tools don't cover this

| Tool | What it evaluates | What it doesn't |
|------|------------------|-----------------|
| **Isaac Lab / Isaac Lab-Arena** | Single RL policy → reward signal | Focused on policy training validation |
| **LeRobot** | Single VLA with hardcoded VLM backbone | Focused on per-model benchmarking |
| **VLABench** (ICCV 2025) | VLMs and VLAs in separate categories | Confirms pipeline error compounding is the real bottleneck |
| **LIBERO benchmarks** | Per-model leaderboard scores | Population averages, not domain-specific |

These tools serve the model layer well. Isaac Lab validates that a trained policy learned the task ([VLM/LLM integration requested in #374](https://github.com/isaac-sim/IsaacLab/issues/374)). LeRobot benchmarks individual VLAs ([configurable VLM backbone requested in #2104](https://github.com/huggingface/lerobot/issues/2104)). The gap is at the agent layer — domain evaluation applied to robotics, the same pattern used for LLM evaluation in legal, medical, and financial domains. The model is an implementation detail; the task outcome is what matters.

### The deployment questions this answers

- "Does GPT-4.1-mini or Qwen3-VL give better scene understanding for **my** bin-picking task, not LIBERO's?"
- "My VLM says the task succeeded but the sim says it failed — which VLM is the most reliable judge?"
- "Does the cheaper edge VLM (Phi-4-MM) degrade task success by 5% or 50% compared to GPT-4.1-mini?"
- "When I rephrase the instruction from 'pick the bracket' to 'grab the red part,' which configurations stay robust?"
- "What does each evaluation run cost me in API calls?"

---

## 2. What This Adds

A **domain evaluation module** (`src/evaluation/`) that works at the agent layer — parallel to the existing `src/training/` and `src/inference/` modules. The unit under test is the agent stack, not individual models.

### The evaluation pipeline

```text
Input:  Task description + scene image + agent stack configuration
        "Pick the red bracket and place it in bin A"
        bracket_scene.jpg
        perceive: gpt-4.1-mini | plan: gpt-4.1-mini | act: lerobot-act-policy | verify: gpt-4.1

Pipeline:
  1. Perceive   → VLM analyzes scene, produces structured understanding
  2. Plan       → Reasoning layer produces manipulation strategy
  3. Act        → Policy predicts actions, sim executes them
  4. Verify     → Judge assesses outcome (VLM judge calibrated against sim ground truth)

Output: Task success/failure, per-stage latency, cost, VLM judge calibration score
```

The pipeline is **deterministic** — no LLM decides which step runs next. The orchestrator is pure Python (`asyncio`), executing a fixed sequence. This is critical: if the orchestrator made decisions, you couldn't tell whether a poor result came from the agent stack or from the orchestrator's judgment.

### Three evaluation workflows

| Workflow | What it answers | Example |
|----------|----------------|---------|
| **Single task** | "Does this agent stack solve my task?" | Run GPT-4.1-mini + LeRobot ACT on "pick the red bracket" × 10 trials |
| **VLM domain comparison** | "Which VLM understands my workspace best?" | Compare GPT-4.1-mini vs Qwen3-VL vs Phi-4-MM on the same scene, same policy |
| **Variation study** | "How robust is this stack to instruction changes?" | Same stack, 5 phrasings of the same task, measure degradation |

### The novel signal: VLM-as-judge calibration

When a VLM judges "did the task succeed?" by comparing before/after images, how reliable is that judgment? This framework calibrates VLM judges against sim ground truth:

```text
judge_calibration = agreement_rate(vlm_judge_verdict, sim_ground_truth)
```

Result: "GPT-4.1 agrees with physics ground truth 92% of the time on your task. Qwen3-VL agrees 78%."

This is directly useful for deployment — teams need a reliable automated judge for real-robot scenarios where sim ground truth is not available. Knowing which VLM to trust as a judge is a deployment decision.

---

## 3. Azure AI Foundry Integration

This module is designed as a native Azure AI Foundry application — not just an export target, but a first-class integration with Foundry's model hosting, evaluation SDK, and dashboard.

### Model hosting via Foundry endpoints

VLMs used for perception and verification are deployed through Azure AI Foundry's model catalog:

| Model | Foundry deployment | Use in evaluation |
|-------|-------------------|-------------------|
| **GPT-4.1** | Azure OpenAI deployment | VLM-as-judge verification — strongest reasoning over images |
| **GPT-4.1-mini** | Azure OpenAI deployment | Primary VLM for perception — beats GPT-4o on image benchmarks, 83% cheaper |
| **o3 / o4-mini** | Azure OpenAI deployment | Deep visual reasoning — when perception requires multi-step spatial analysis |
| **Phi-4-Multimodal** (5.6B) | Foundry model catalog | Edge-deployable VLM — scene understanding at sub-100ms |
| **Phi-4-Mini** (3.8B) | Foundry Local (on-device) | Offline planning — no cloud dependency |

The module supports models hosted on **Azure AI Foundry** and **Hugging Face**, with adapters for each provider:

- **Azure AI Foundry**: GPT-4.1 family, o3/o4-mini, Phi-4-Multimodal — accessed via Azure AI Inference SDK with multimodal (image + text) input
- **Hugging Face**: Qwen3-VL, SmolVLA, OpenVLA, pi0 — accessed via `transformers` or framework-specific APIs (LeRobot, OpenPI)
- **Foundry Local**: Phi-4-Mini running entirely on-device — OpenAI-compatible API at `localhost:5272`, no Azure subscription needed

Each VLM adapter handles the model-specific multimodal input format (image encoding, structured output parsing, grounding token extraction). Each VLA/policy adapter handles the model-specific action output format (discrete tokens, flow-matching chunks, diffusion denoising). The adapter Protocol abstracts these differences — the orchestrator sees `analyze_scene(image, task) → SceneAnalysis` and `predict_action(image, task, proprio) → ActionPrediction` regardless of provider.

**Key advantage**: Swapping models is a config change, not a code change. The evaluation framework treats Foundry-hosted and Hugging Face-hosted models through the same adapter interface.

### Custom evaluators via Azure AI Evaluation SDK

The module registers domain-specific robotics evaluators with the `azure-ai-evaluation` SDK (v1.15+), so evaluation results surface natively in the Foundry dashboard.

**Code-based evaluators** (deterministic, no LLM needed):

```python
from azure.ai.evaluation import evaluate

class TaskSuccessEvaluator:
    """Binary: did the agent stack solve the task?"""
    def __call__(self, *, response: str, ground_truth: str, **kwargs) -> dict:
        predicted = json.loads(response)["success"]
        actual = ground_truth == "success"
        return {"task_success": 1.0 if predicted == actual else 0.0}

class JudgeCalibrationEvaluator:
    """How well does the VLM judge agree with sim ground truth?"""
    def __call__(self, *, response: str, ground_truth: str, **kwargs) -> dict:
        vlm_verdict = json.loads(response)["vlm_success"]
        sim_verdict = ground_truth == "success"
        return {"judge_calibration": 1.0 if vlm_verdict == sim_verdict else 0.0}

class LatencyCostEvaluator:
    """Per-stage latency and total cost — deterministic from logged data."""
    def __call__(self, *, response: str, **kwargs) -> dict:
        data = json.loads(response)
        return {
            "perceive_latency_ms": data["perceive_latency_ms"],
            "act_latency_ms": data["act_latency_ms"],
            "total_cost_usd": data["total_cost_usd"]
        }
```

**Prompt-based evaluators** (VLM-as-judge, uses Foundry-hosted model):

```yaml
# scene_understanding_evaluator.prompty
---
name: Scene Understanding Quality
model:
  api: chat
  configuration:
    type: azure_openai
    azure_deployment: gpt-4o
  parameters:
    temperature: 0.1
inputs:
  task: string
  scene_analysis: string
  ground_truth_objects: string
outputs:
  score: int
---
system:
You are evaluating a VLM's scene understanding for a robotics task.

Rate the quality of the scene analysis on a 1-5 scale:
5: All task-relevant objects identified with correct spatial relationships
4: Most objects identified, minor spatial errors
3: Key objects identified but spatial reasoning incomplete
2: Missing critical objects or major spatial errors
1: Scene analysis is unusable for the task

Task: {{task}}
VLM Scene Analysis: {{scene_analysis}}
Ground Truth Objects: {{ground_truth_objects}}

Output JSON: {"result": <1-5>, "reason": "..."}
```

**Running evaluation through Foundry SDK:**

```python
from azure.ai.evaluation import evaluate

result = evaluate(
    data="evaluation_results.jsonl",
    evaluators={
        "task_success": TaskSuccessEvaluator(),
        "judge_calibration": JudgeCalibrationEvaluator(),
        "latency_cost": LatencyCostEvaluator(),
        "scene_quality": "scene_understanding_evaluator.prompty"
    },
    evaluator_config={
        "default": {
            "column_mapping": {
                "response": "${data.pipeline_output}",
                "ground_truth": "${data.sim_ground_truth}"
            }
        }
    },
    azure_ai_project={
        "subscription_id": os.environ["AZURE_SUBSCRIPTION_ID"],
        "resource_group_name": os.environ["AZURE_RESOURCE_GROUP"],
        "project_name": os.environ["AZURE_AI_PROJECT_NAME"]
    },
    output_path="./eval_output.json"
)

# Results appear in Foundry dashboard
print(f"View results: {result['studio_url']}")
```

### Results in the Foundry dashboard

Evaluation results surface in Azure AI Foundry's evaluation UI alongside training metrics:

- **Per-task success rates** across agent stack configurations
- **VLM comparison**: GPT-4.1-mini vs Phi-4-MM perception quality on the team's task
- **Judge calibration scores**: which VLM is the most reliable automated judge
- **Latency vs cost tradeoffs**: cloud VLM (reliable, expensive) vs edge VLM (fast, cheaper)
- **Drill-down into failures**: which tasks failed, at which pipeline stage, with what VLM output

The Foundry dashboard link is returned from `evaluate()` — teams share a single URL for the full evaluation report.

### JSONL data contract

Evaluation results export in Foundry-compatible format:

```jsonl
{"query": "Pick the red bracket and place it in bin A", "response": "{\"success\": true, \"vlm_success\": true, \"perceive_latency_ms\": 820, \"act_latency_ms\": 150, \"total_cost_usd\": 0.012}", "context": "{\"vlm\": \"gpt-4.1-mini\", \"policy\": \"lerobot-act\", \"stage_config\": \"perceive:gpt-4.1-mini|plan:gpt-4.1-mini|act:lerobot-act|verify:gpt-4.1\"}", "ground_truth": "success"}
```

This format works directly with `azure-ai-evaluation` SDK's `evaluate()` function and the Foundry dashboard — no format conversion needed.

---

## 4. How This Builds on Existing Infrastructure

### Complete the train → evaluate → deploy loop

The repo has training (Isaac Lab RL + LeRobot IL) and inference (LeRobot ACT PolicyRunner). Evaluation is the missing third pillar:

```text
Train policy (existing)  →  Evaluate on domain task (this proposal)  →  Deploy (existing)
        ↑                            |
        └── Fine-tune on failures ←──┘
```

Policies trained via LeRobot in this repo are evaluated on the team's own task. Failure cases (VLM misidentified object, policy overshot grasp) become targeted fine-tuning data. The loop closes with existing infrastructure.

### Extends `src/training/scripts/policy_evaluation.py`

The existing evaluation file runs: load RL agent → run episodes → compute metrics (mean_reward, success_rate) → pass/fail threshold. This proposal follows the same pattern and adds the perception layer (VLM) and verification layer (VLM-as-judge) around the existing policy execution loop.

### Reuses `src/training/utils/`

- `AzureMLContext` and `bootstrap_azure_ml()` for Azure authentication
- `SystemMetricsCollector` for per-stage latency and resource tracking
- `AzureStorageContext` for persisting evaluation artifacts (scene images, action traces, judge verdicts)

### Wraps `src/inference/`

- `PolicyRunner` from `policy_runner.py` becomes one policy adapter — LeRobot ACT policies evaluated through the existing inference path
- `RobotObservation` and `JointPositionCommand` types from `robot_types.py` used directly
- The existing LeRobot ACT inference path is one option in the Act stage, not replaced

### Complements evaluation issues #250–#267

| Existing work (issues #250–#267) | This proposal |
|----------------------------------|---------------|
| Single-policy offline replay (MSE, DTW) | Task-level agent stack evaluation |
| Isaac Sim validation (one RL policy) | Full perceive → plan → act → verify pipeline |
| Model-layer metrics | Domain-level metrics (task success, robustness, cost) |
| "Did my policy learn?" | "Does my agent stack solve my task?" |

The two approaches are complementary. Single-policy metrics from existing work feed into the broader task-level evaluation as one signal.

---

## 5. Technical Design

### Adapter Protocol pattern

Each component implements a typed Python Protocol. Adding a new model = implement the Protocol + add a config entry. No orchestrator code changes.

```python
from typing import Protocol, runtime_checkable
from dataclasses import dataclass

# --- Data models ---

@dataclass
class SceneAnalysis:
    objects: list[dict]          # detected objects with properties
    spatial_relations: list[str] # "bracket is left of bin A"
    task_relevant: dict          # task-specific observations
    raw_response: str            # original VLM output for debugging

@dataclass
class ActionPrediction:
    actions: list[list[float]]   # [[dx,dy,dz,droll,dpitch,dyaw,gripper], ...]
    num_steps: int
    confidence: float | None     # architecture-dependent (only autoregressive VLAs)

@dataclass
class VerificationResult:
    success: bool
    confidence: float
    reasoning: str
    raw_response: str

@dataclass
class TrialResult:
    task: str
    agent_config: dict           # which models were used at each stage
    sim_success: bool            # physics ground truth
    vlm_success: bool | None     # VLM judge verdict
    judge_calibration: float | None
    perceive_latency_ms: float
    plan_latency_ms: float
    act_latency_ms: float
    verify_latency_ms: float
    total_cost_usd: float

# --- Adapter Protocols ---

@runtime_checkable
class VLMAdapter(Protocol):
    async def analyze_scene(self, image: bytes, task: str) -> SceneAnalysis: ...
    async def verify_success(self, before: bytes, after: bytes, task: str) -> VerificationResult: ...
    async def health_check(self) -> bool: ...

@runtime_checkable
class PolicyAdapter(Protocol):
    async def predict_action(
        self, image: bytes, task: str, proprio: list[float] | None
    ) -> ActionPrediction: ...
    async def health_check(self) -> bool: ...

@runtime_checkable
class SimAdapter(Protocol):
    async def reset(self, task_id: str) -> dict: ...         # → observation
    async def step(self, action: list[float]) -> dict: ...   # → obs, reward, done, info
    async def get_observation(self) -> dict: ...
```

### Deterministic orchestrator

```python
class EvaluationPipeline:
    """Deterministic: perceive → plan → act → verify. No LLM routing."""

    async def run_trial(self, task: str, image: bytes, config: AgentStackConfig) -> TrialResult:
        # 1. Perceive — VLM via Foundry endpoint
        scene = await self.vlm.analyze_scene(image, task)

        # 2. Plan (same VLM, or separate — config-driven)
        plan = await self.planner.plan_grasp(image, task, scene)

        # 3. Act — policy predicts, sim executes
        obs = await self.sim.reset(task_id)
        actions = await self.policy.predict_action(obs["image"], task, obs.get("proprio"))
        for action_step in actions.actions:
            result = await self.sim.step(action_step)
            if result["done"]:
                break

        # 4. Verify — VLM judge + sim ground truth
        after_image = await self.sim.get_observation()
        vlm_verdict = await self.vlm.verify_success(image, after_image["image"], task)
        sim_success = result["info"]["success"]

        # Judge calibration: how well does the VLM agree with physics?
        judge_calibration = float(vlm_verdict.success == sim_success)

        return TrialResult(
            task=task,
            sim_success=sim_success,
            vlm_success=vlm_verdict.success,
            judge_calibration=judge_calibration,
            # ... latency, cost from instrumentation
        )
```

### Configuration

Follows the repo's environment variable + YAML hybrid pattern:

```yaml
# evaluation.yaml
models:
  vlm:
    gpt-4.1-mini:
      adapter: azure_foundry
      config:
        endpoint_env: AZURE_AI_FOUNDRY_ENDPOINT     # Foundry unified endpoint
        deployment_name: gpt-4.1-mini
        auth: entra_id                               # DefaultAzureCredential
    gpt-4.1:
      adapter: azure_foundry
      config:
        endpoint_env: AZURE_AI_FOUNDRY_ENDPOINT
        deployment_name: gpt-4.1
        auth: entra_id
    o4-mini:
      adapter: azure_foundry
      config:
        endpoint_env: AZURE_AI_FOUNDRY_ENDPOINT
        deployment_name: o4-mini
        auth: entra_id
    phi-4-mm:
      adapter: azure_foundry
      config:
        endpoint_env: AZURE_AI_FOUNDRY_ENDPOINT
        deployment_name: phi-4-multimodal-instruct
        auth: entra_id
    phi-4-mini-local:
      adapter: foundry_local
      config:
        base_url: "http://localhost:5272"             # Foundry Local runtime
        model: phi-4-mini
    mock-vlm:
      adapter: mock
      config:
        mock_quality: 0.85

  policy:
    lerobot-act:
      adapter: lerobot_act
      config:
        repo_id: "lerobot/act_aloha_sim_insertion_human"
        device: cuda
    mock-policy:
      adapter: mock
      config:
        mock_success_rate: 0.80

evaluations:
  bin_picking:
    description: "Evaluate bin-picking with VLM perception comparison"
    task: "Pick the red bracket and place it in bin A"
    image: scenes/bracket_scene.jpg
    vlms: [gpt-4.1-mini, phi-4-mm]
    policies: [lerobot-act]
    trials: 10

  edge_vs_cloud:
    description: "Compare edge VLM (Foundry Local) vs cloud VLM (Foundry)"
    task: "Pick the red bracket and place it in bin A"
    vlms: [gpt-4.1-mini, phi-4-mini-local]
    policies: [lerobot-act]
    trials: 10

  instruction_robustness:
    description: "Test robustness to instruction variations"
    tasks:
      - "Pick the red bracket and place it in bin A"
      - "Grab the bracket from the table"
      - "Move the red part to bin A"
    vlms: [gpt-4.1-mini]
    policies: [lerobot-act]
    trials: 10

  mock_test:
    description: "End-to-end test with mocks (no GPU, no API keys)"
    task: "Pick the bracket"
    vlms: [mock-vlm]
    policies: [mock-policy]
    trials: 1
```

### Proposed module structure

```text
src/evaluation/                          # NEW — parallel to training/ and inference/
├── __init__.py
├── adapters/
│   ├── __init__.py
│   ├── protocols.py                     # VLMAdapter, PolicyAdapter, SimAdapter Protocols
│   ├── registry.py                      # YAML config → adapter resolution
│   ├── vlm/
│   │   ├── __init__.py
│   │   ├── mock.py                      # Mock VLM (no GPU, no API keys)
│   │   ├── azure_foundry.py             # GPT-4.1 / GPT-4.1-mini / Phi-4-MM via Foundry endpoint
│   │   └── foundry_local.py             # Phi-4-Mini via Foundry Local runtime
│   └── policy/
│       ├── __init__.py
│       ├── mock.py                      # Mock policy
│       └── lerobot_act.py               # Wraps existing PolicyRunner from src/inference/
├── orchestrator/
│   ├── __init__.py
│   ├── pipeline.py                      # Deterministic perceive → plan → act → verify
│   └── runner.py                        # Multi-trial execution with metrics collection
├── evaluators/
│   ├── __init__.py
│   ├── task_success.py                  # Code-based: binary success evaluator
│   ├── judge_calibration.py             # Code-based: VLM judge vs sim ground truth
│   ├── latency_cost.py                  # Code-based: per-stage latency + total cost
│   └── scene_understanding.prompty      # Prompt-based: VLM rates scene analysis quality
├── models.py                            # SceneAnalysis, ActionPrediction, VerificationResult, TrialResult
├── store.py                             # MLflow logging + Foundry JSONL export + azure-ai-evaluation
├── config.py                            # evaluation.yaml loader
└── cli.py                               # CLI: python -m evaluation.cli evaluate --config evaluation.yaml

tests/evaluation/                        # Mirror structure, follows repo test conventions
├── __init__.py
├── test_pipeline.py                     # End-to-end with mock adapters
├── test_adapters.py                     # Protocol compliance
├── test_evaluators.py                   # Custom evaluator correctness
└── test_store.py                        # MLflow + JSONL + Foundry SDK export
```

---

## 6. Implementation Approach

### Phase 1 — Mock-first foundation (initial PR)

- Mock adapters for VLM, policy, sim (no GPU, no API keys, no external services)
- Deterministic pipeline: perceive → plan → act → verify
- CLI: `python -m evaluation.cli evaluate --config evaluation.yaml`
- Custom evaluators (code-based: task_success, judge_calibration, latency_cost)
- JSONL export in Foundry-compatible format
- MLflow metric logging via existing `AzureMLContext`
- Tests passing: `uv run pytest tests/evaluation/ -m "not slow and not gpu"`
- Follows repo conventions: `uv`, hatchling, ruff (line-length 120), conventional commits
- **Success criteria**: `evaluate --evaluation mock_test` completes in <5s with no external dependencies

### Phase 2 — Azure AI Foundry integration

- VLM adapter calling Foundry-hosted models (GPT-4.1-mini, GPT-4.1, o4-mini, Phi-4-MM) via `azure-ai-inference` SDK
- Foundry Local adapter for edge VLMs (Phi-4-Mini at `localhost:5272`)
- `azure-ai-evaluation` SDK integration — custom evaluators registered, results logged to Foundry project
- Foundry dashboard link returned from evaluation runs
- LeRobot ACT policy adapter wrapping `PolicyRunner` from `src/inference/`
- Prompt-based evaluator (scene understanding quality via `.prompty` file)
- **Success criteria**: Evaluation with Foundry-hosted VLM produces results visible in Foundry dashboard

### Phase 3 — Variation study + feedback loop

- Task variation evaluation (multiple phrasings, conditions)
- Training → evaluation feedback: failure analysis → fine-tuning targets
- Azure ML compute for parallel evaluation runs
- **Success criteria**: Variation study reports per-phrasing success rates and identifies degradation

---

## 7. References

- [VLABench](https://openaccess.thecvf.com/content/ICCV2025/papers/Zhang_VLABench_A_Large-Scale_Benchmark_for_Language-Conditioned_Robotics_Manipulation_with_Long-Horizon_ICCV_2025_paper.pdf) (ICCV 2025) — hierarchical pipeline error compounding
- [RoVer](https://arxiv.org/abs/2510.10975) (arXiv 2510.10975) — reward model as test-time verifier for VLAs
- [Cosmos Policy](https://arxiv.org/abs/2601.16163) (arXiv 2601.16163) — world model VLA, LIBERO SOTA (98.5%)
- [Gemini Robotics + Veo Sim](https://arxiv.org/abs/2512.10675) (arXiv 2512.10675) — world model as evaluation environment
- [State of VLA Research at ICLR 2026](https://mbreuss.github.io/blog_post_iclr_26_vla.html) — 164 papers, zero composition evaluation
- Isaac Lab [#374](https://github.com/isaac-sim/IsaacLab/issues/374) — VLM/LLM integration requested, not implemented
- LeRobot [#2104](https://github.com/huggingface/lerobot/issues/2104) — configurable VLM backbone requested, not shipped
- [Azure AI Foundry Evaluation SDK](https://learn.microsoft.com/en-us/azure/ai-foundry/how-to/develop/evaluate-sdk) — custom evaluators and dashboard integration
- [Azure AI Foundry model endpoints](https://learn.microsoft.com/en-us/azure/ai-foundry/foundry-models/concepts/endpoints) — unified inference endpoint
- [Foundry Local](https://learn.microsoft.com/en-us/azure/ai-foundry/foundry-local/what-is-foundry-local) — on-device model runtime
- [azure-ai-evaluation v1.15.1](https://pypi.org/project/azure-ai-evaluation/) — latest SDK with custom evaluator support
- [NVIDIA Isaac Lab-Arena + LeRobot](https://huggingface.co/blog/nvidia/generalist-robotpolicy-eval-isaaclab-arena-lerobot) — single-policy GPU-parallel evaluation
- AWS Physical AI stack (Bedrock + LeRobot, Dec 2025) — Azure competitive gap

---

*This proposal complements the existing evaluation work in issues #250–#267 of the reference architecture repo. Those issues cover single-policy evaluation (offline replay, Isaac Sim validation). This adds task-level evaluation of the full agent stack — a different dimension that builds on the same Azure infrastructure. Happy to coordinate with the evaluation team on integration.*
