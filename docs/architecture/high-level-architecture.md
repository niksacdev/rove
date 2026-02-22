# ROVE — High-Level Architecture

**Status**: Accepted
**Date**: 2026-02-18
**Authors**: System Architect
**Version**: 1.1 — 4-stage pipeline, strategies, src layout

---

## 1. Executive Summary

ROVE evaluates robotics agent pipelines by running real inference through VLM+VLA+LLM+Agent combinations on your specific task. The user starts from a task description and a scene image, selects candidate models for each pipeline stage, and ROVE runs the full perceive-plan-act-verify pipeline across every strategy in parallel, producing a ranked comparison of agent configurations.

ROVE is inference-only — it does not train, fine-tune, or modify models. It orchestrates them into agent pipelines and measures the outcomes.

The system is a Python monorepo. There is no frontend build step. The dashboard is plain HTML + vanilla JS served as static files by FastAPI. The CLI calls the same Python functions as the API. MCP servers expose adapters as tools for AI agents but are not in the evaluation critical path.

---

## 2. High-Level Architecture Diagram

```
+-----------------------------------------------------------------------+
|                        ROVE Python Monorepo                           |
|                                                                       |
|  +-------------------+   +-------------------+   +-----------------+ |
|  |   CLI (Click)     |   | Python Library    |   | Dashboard       | |
|  |   rove evaluate   |   | import rove       |   | HTML + JS + CSS | |
|  |   rove models     |   | EvaluationEngine  |   | (static files)  | |
|  |   rove export     |   |                   |   |                 | |
|  +--------+----------+   +--------+----------+   +--------+--------+ |
|           |                       |                        |          |
|           |  (direct Python call) |                  fetch() / SSE   |
|           |                       |                        |          |
|           v                       v                        v          |
|  +---------------------------------------------------------------------+
|  |                        FastAPI Application                          |
|  |                                                                     |
|  |  POST /api/evaluations        (create + queue job)                 |
|  |  GET  /api/evaluations/{id}   (poll status + results)              |
|  |  GET  /api/evaluations/{id}/stream  (SSE live progress)            |
|  |  GET  /api/evaluations        (list history)                       |
|  |  GET  /api/models             (registry contents)                  |
|  |  GET  /api/leaderboard        (aggregated rankings)                |
|  |  POST /api/export             (JSONL download)                     |
|  |  GET  /                       (serves dashboard/index.html)        |
|  |  GET  /static/*               (serves dashboard assets)            |
|  |                                                                     |
|  |  asyncio.create_task() -----> BackgroundJobRunner                  |
|  |                                                                     |
|  +--------+------------------------------------------------------------+
|           |                                                            |
|           | direct Python calls (no HTTP)                             |
|           v                                                            |
|  +---------------------------------------------------------------------+
|  |                     EvaluationEngine                                |
|  |                                                                     |
|  |  RunManager                                                         |
|  |    asyncio.TaskGroup(                                               |
|  |      EvaluationPipeline(strategy_1),   <-- strategy 1             |
|  |      EvaluationPipeline(strategy_2),   <-- strategy 2             |
|  |      EvaluationPipeline(strategy_3),   <-- strategy 3             |
|  |      ...                                                            |
|  |    )                                                                |
|  |                                                                     |
|  |  EvaluationPipeline (per strategy, deterministic 4-stage)          |
|  |    1. perceive  --> VLMAdapter.analyze_scene()                     |
|  |    2. plan      --> VLMAdapter.plan_task()                         |
|  |    3. act       --> PolicyAdapter.predict_action() + SimAdapter.step()|
|  |    4. verify    --> VLMAdapter.verify_success()                    |
|  |                                                                     |
|  +--------+------------------------------------------------------------+
|           |                                                            |
|           v                                                            |
|  +---------------------------------------------------------------------+
|  |                      Adapter Registry                               |
|  |                                                                     |
|  |  rove.yaml --> AdapterRegistry --> resolves model_id to adapter    |
|  |                                                                     |
|  |  VLMAdapter Protocol:      PolicyAdapter Protocol:                 |
|  |    mock_vlm                  mock_vla                              |
|  |    azure_openai_vlm          local_smolvla                         |
|  |    azure_nim_vlm             local_openvla                         |
|  |    azure_hf_vlm              azure_gpu_cogact                      |
|  |    local_mlx_vlm             azure_gpu_groot                       |
|  |                                                                     |
|  |  AgentAdapter Protocol:     SimAdapter Protocol:                   |
|  |    mock_agent                 mock_sim                             |
|  |    azure_foundry_agent        local_mujoco                         |
|  |                                                                     |
|  +--------+------------------------------------------------------------+
|           |                                                            |
|           v                                                            |
|  +---------------------------------------------------------------------+
|  |                    Evaluation Store (SQLite)                        |
|  |                                                                     |
|  |  evaluations table    strategy_results table                       |
|  |  stage_results table  leaderboard (aggregated view)                |
|  |  JSONL export (Foundry-compatible: query/response/context)         |
|  |                                                                     |
|  +---------------------------------------------------------------------+
|                                                                        |
+-----------------------------------------------------------------------+

         EXTERNAL: MCP servers (not in evaluation critical path)

  +-------------------+  +-------------------+  +-------------------+
  | VLM MCP Server    |  | VLA MCP Server    |  | Grounding+Sim     |
  | Port 8081         |  | Port 8082         |  | MCP Server        |
  |                   |  |                   |  | Port 8083         |
  | analyze_scene()   |  | predict_action()  |  | localize()        |
  | plan_task()       |  | list_vlas()       |  | segment()         |
  | verify_success()  |  |                   |  | sim_reset()       |
  | list_vlms()       |  |                   |  | sim_step()        |
  +--------+----------+  +--------+----------+  +--------+----------+
           |                      |                       |
           +----------------------+-----------------------+
                                  |
                    imports from AdapterRegistry
                    (same adapters, different entry point)
                                  |
           +----------------------+-----------------------+
           |                      |                       |
     Claude Desktop          Copilot             Azure AI Foundry
     (agent uses MCP          (agent uses MCP     (agent uses MCP
      tools to call            tools to call       tools to call
      ROVE adapters)           ROVE adapters)      ROVE adapters)
```

---

## 3. Async Job Pattern

The evaluation API is fully non-blocking. This is essential because an evaluation with multiple strategies can take 10-120 seconds depending on model latency.

```
Client                      FastAPI                     BackgroundJobRunner
  |                            |                                |
  | POST /api/evaluations      |                                |
  | {task, image, strategies}  |                                |
  |-------------------------->|                                |
  |                            | 1. validate request           |
  |                            | 2. create EvalRecord          |
  |                            |    status=queued              |
  |                            | 3. persist to SQLite          |
  |                            | 4. asyncio.create_task(run)   |
  |                            |    --------------------------->|
  |                            | 5. return immediately         |
  |<--------------------------|                                |
  | 202 Accepted               |                                |
  | {evaluation_id, status:    |                                |
  |   "queued"}                |                                |
  |                            |                                |
  | GET /evaluations/{id}      |                                |
  | (poll every 2s)            |                                |
  |-------------------------->|                                |
  |<--------------------------|  {status: "running",           |
  |  running + partial results |   completed_strategies: 2,    |
  |                            |   total_strategies: 3}        |
  |                            |                                |
  |                            |         [background work]      |
  |                            |         RunManager runs       |
  |                            |         3 pipelines           |
  |                            |         asyncio.TaskGroup     |
  |                            |         each completes and    |
  |                            |         writes to SQLite      |
  |                            |                               |
  | OR: subscribe to SSE       |                               |
  | GET /evaluations/{id}/stream                               |
  |-------------------------->|                               |
  |<-- event: stage_complete --|<-- SSEQueue.put(event) ------|
  |<-- event: stage_complete --|                              |
  |<-- event: strategy_done   |                               |
  |<-- event: evaluation_done-|                               |
  |                            |                               |
  | GET /evaluations/{id}      |                               |
  |-------------------------->|                               |
  |<--------------------------|                               |
  | 200 OK                     |                               |
  | {status: "complete",       |                               |
  |  results: [...ranked...]}  |                               |
```

**State machine for an evaluation:**

```
queued --> running --> complete
                  \--> failed
```

Each strategy within an evaluation has its own state:

```
pending --> running --> success
                   \--> failed (stage_name, error_message)
```

Partial failure is non-fatal: if strategy 2 of 3 fails, the other 2 complete and are ranked. The failed strategy is recorded with its failure stage.

---

## 4. The 4-Stage Pipeline in Detail

```
INPUT: task_description (str), scene_image (bytes), strategy config (perceive_model_id, plan_model_id, act_model_id, verify_model_id, sim_id)

+-------------+                +-------------+
|   perceive  |                |    plan     |
|             |                |             |
| VLM sees    | ------------>  | VLM plans   |
| scene image |                | task given  |
| + task      |                | scene       |
| description |                | analysis    |
|             |                |             |
+------+------+                +------+------+
       |                              |
       v                              v
  SceneAnalysis                   TaskPlan
  (see §5.A)                     (see §5.C)
       |                              |
       +------------------------------+
                           |
                           v
              +------------+------------+
              |                         |
              |          act            |
              |                         |
              |  PolicyAdapter predicts |
              |  action chunk given:    |
              |  - scene image          |
              |  - task description     |
              |  - proprioception       |
              |                         |
              |  Sim steps through      |
              |  each action in chunk   |
              |  sim.step(action) x N   |
              |                         |
              |  Outputs:               |
              |  - ActionPrediction     |
              |  - post_exec_obs (image)|
              |  - sim_success (bool)   |
              |                         |
              +------------+------------+
                           |
                           v
              +------------+------------+
              |                         |
              |         verify          |
              |                         |
              |  VLM compares:          |
              |  - initial scene image  |
              |  - post_exec_obs image  |
              |  - task description     |
              |  - context (dict)       |
              |                         |
              |  Returns:               |
              |  - vlm_success (bool)   |
              |  - confidence (float)   |
              |  - reasoning (str)      |
              |                         |
              |  ALSO: compare          |
              |  vlm_success vs         |
              |  sim_success for        |
              |  "judge calibration"    |
              |  metric                 |
              |                         |
              +------------+------------+
                           |
                           v
                     StrategyResult
                     (saved to SQLite)

OUTPUT: ranked list of StrategyResult across all strategies
```

### VLM-Only Mode (no sim, no PolicyAdapter)

When no PolicyAdapter is specified OR when `--mode vlm-only` is passed, the pipeline runs stages 1-2 only (perceive, plan). This is valid for evaluating perception and planning quality without action execution. Stage 3 (act) is skipped; stage 4 runs a "counterfactual" verify using only the initial image and the plan.

```
perceive --> plan --> [skip act] --> verify(plan quality)
```

---

## 5. Data Shapes — The Tricky Parts

### 5.A What VLMs Return: SceneAnalysis

VLMs return natural language with embedded structured data. Different VLMs structure it differently. The adapter normalizes to `SceneAnalysis`.

**Raw VLM output** (example, GPT-4o):
```
The scene shows a manufacturing workbench. I can identify:
- A red bracket (approx. center-left, metallic, rectangular)
- Bin A (right side, blue container, 200mm × 150mm)
- Bin B (far right, green container)
The target object for the task "pick red bracket" is the red bracket.
```

**Adapter normalization to SceneAnalysis:**
```python
@dataclass
class DetectedObject:
    label: str                    # "red bracket"
    confidence: float             # 0.0 - 1.0
    description: str | None       # "metallic, rectangular"
    spatial_hint: str | None      # "center-left" — natural language, not coordinates
    # NOTE: VLMs cannot give metric coordinates from a single 2D image.
    # Pixel coordinates come from grounding models (folded into perceive), not here.

@dataclass
class SceneAnalysis:
    objects: list[DetectedObject]
    scene_description: str        # full natural language summary
    task_relevant_objects: list[str]  # labels of objects relevant to the task
    raw_response: str             # original VLM output, preserved for export
    latency_ms: float
    cost_usd: float
    model_id: str
```

**Key constraint**: VLMs operating on a single 2D image WITHOUT depth data cannot provide metric 3D coordinates. `SceneAnalysis` captures semantic understanding and object identification. Grounding (bounding boxes, segmentation) is folded into the perceive stage when the VLM or a dedicated grounding model supports it, rather than being a separate pipeline stage.

### 5.B What Grounding Models Return: Localization

```python
@dataclass
class BoundingBox:
    x_min: float   # normalized [0, 1] relative to image width
    y_min: float   # normalized [0, 1] relative to image height
    x_max: float
    y_max: float
    confidence: float

@dataclass
class SegmentationMask:
    mask_rle: str | None          # run-length encoded binary mask (optional)
    mask_polygon: list[list[float]] | None  # polygon approximation (optional)

@dataclass
class Localization:
    query: str                    # what was searched for ("red bracket")
    bbox: BoundingBox             # pixel-normalized bounding box
    segmentation: SegmentationMask | None  # SAM2 output if available
    image_width_px: int
    image_height_px: int
    raw_response: dict            # model-specific output, preserved
    latency_ms: float
    model_id: str
```

**Coordinate convention**: All bounding boxes are normalized [0,1] relative to image dimensions. Adapters are responsible for converting from model-native formats (e.g., GroundingDINO outputs absolute pixel coordinates — the adapter normalizes them). This is a hard invariant enforced by the Protocol type annotations.

### 5.C What VLMs Return for Planning: TaskPlan

The planning stage is where VLMs are asked to reason about HOW to accomplish a task. The VLM receives the scene analysis and the task description. It returns a structured plan.

```python
@dataclass
class TaskPlan:
    target_object: str            # "red bracket"
    strategy: str                 # "top-down-grasp", "push-slide", etc.
    reasoning: str                # VLM's chain-of-thought reasoning
    steps: list[str]              # ordered natural language steps
    confidence: float             # VLM's self-reported confidence
    raw_response: str
    latency_ms: float
    cost_usd: float
    model_id: str
```

**Design rationale**: `TaskPlan` is deliberately higher-level than a grasp pose. VLMs reason about strategy and sequencing; the PolicyAdapter and sim handle metric execution. Grounding (bounding boxes, segmentation) is folded into the perceive stage when the VLM or a grounding model supports it, rather than being a separate pipeline stage.

### 5.D What VLAs Return: ActionPrediction

This is the most heterogeneous part of the system. VLA architectures differ fundamentally in their output structure. The adapter must normalize everything to `ActionPrediction` while preserving raw output.

**Action space convention**: ROVE uses end-effector delta format as the canonical action space:
```
[dx, dy, dz, droll, dpitch, dyaw, gripper_command]
```
- `dx, dy, dz`: end-effector position delta in meters, robot base frame, right-hand coordinate system
- `droll, dpitch, dyaw`: end-effector orientation delta in radians
- `gripper_command`: continuous [0.0=open, 1.0=closed]

**Per-VLA normalization responsibility**:

| VLA | Raw Output | Adapter Normalization |
|-----|------------|----------------------|
| SmolVLA (flow matching) | action chunks, LeRobot-normalized floats [-1,1] | denormalize using LeRobot stats, convert to metric EE delta |
| OpenVLA (autoregressive) | discrete tokens (vocab ~32000) → continuous via bin detokenization | detokenize, apply linear mapping to EE delta space |
| CogACT (diffusion) | denoised trajectory, 10-100 denoising steps, may be joint positions | if joint-space: forward kinematics via sim to get EE delta |
| GR00T N1.6 | embodiment-specific action dict (awaiting public weights) | TBD, embodiment tag required |

```python
@dataclass
class ActionStep:
    dx: float          # meters
    dy: float          # meters
    dz: float          # meters
    droll: float       # radians
    dpitch: float      # radians
    dyaw: float        # radians
    gripper: float     # [0.0, 1.0]

@dataclass
class ActionPrediction:
    actions: list[ActionStep]     # normalized action chunk
    chunk_size: int               # number of steps in chunk
    action_space: str             # "ee_delta" | "ee_absolute" | "joint_position"
                                  # always "ee_delta" after normalization,
                                  # but records original for debugging
    proprioception_used: bool     # did the VLA receive robot state?
    raw_output: dict              # model-specific: tokens, chunk floats, denoised traj
    latency_ms: float
    cost_usd: float
    model_id: str
```

**Proprioception handling**: SmolVLA optionally accepts current joint state. The sim provides this. The Protocol signature is:

```python
async def predict_action(
    self,
    image: bytes,
    task: str,
    proprioception: list[float] | None = None,
) -> ActionPrediction: ...
```

Adapters that don't use proprioception ignore it. Adapters that need it (OpenVLA-OFT) accept it. The orchestrator always queries the sim for current joint state and passes it; whether it's used is the adapter's decision.

### 5.E What the Sim Returns

```python
@dataclass
class SimObservation:
    rgb_image: bytes              # rendered camera view (JPEG, <750KB for MCP compat)
    depth_image: bytes | None     # optional depth map
    object_states: dict[str, ObjectState]  # ground-truth object positions
    joint_positions: list[float]  # current robot joint angles (7-DOF)
    ee_pose: tuple[float, ...]    # end-effector pose (x,y,z,qw,qx,qy,qz)
    timestep: int
    task_id: str

@dataclass
class ObjectState:
    position: tuple[float, float, float]   # x,y,z in robot base frame (meters)
    orientation: tuple[float, float, float, float]  # quaternion (qw,qx,qy,qz)
    in_target_container: bool | None       # for bin-placement tasks

@dataclass
class SimStepResult:
    observation: SimObservation
    success: bool                  # physics ground truth: did task succeed?
    terminated: bool               # episode ended (success or fall/collision)
    truncated: bool                # max steps reached
```

---

## 6. Data Flow Diagram

```
                          USER INPUT
                    task: "Pick red bracket, place in bin A"
                    image: scene.jpg (bytes)
                    strategies: [mock, cloud-fast, cloud-accurate]

                              |
                              | HTTP POST or CLI call
                              v
                      +--------------+
                      |   FastAPI    |
                      |  creates job |
                      |  eval_id=abc |
                      +--------------+
                              |
                              | asyncio.create_task()
                              v
                      +------------------+
                      |   RunManager     |
                      |   3 strategies:  |
                      |   mock           |
                      |   cloud-fast     |
                      |   cloud-accurate |
                      +------------------+
                              |
                   asyncio.TaskGroup -- all 3 run concurrently
                              |
          +-------------------+-------------------+
          |                   |                   |
          v                   v                   v
  Pipeline(mock)      Pipeline(cloud-fast) Pipeline(cloud-accurate) ...
          |
          | [STAGE 1: perceive]
          | image bytes  -------> VLMAdapter.analyze_scene(image, task)
          |               <------  SceneAnalysis
          |                           objects: [{label, confidence, spatial_hint}]
          |                           task_relevant: ["red bracket"]
          |
          | [STAGE 2: plan]
          | image+analysis ------> VLMAdapter.plan_task(image, task, scene)
          |               <------  TaskPlan
          |                           strategy: "top-down-grasp"
          |                           steps: ["move above", "descend", "grasp", "lift"]
          |
          | [STAGE 3: act]
          |
          |   sim.reset(task_id)  --------> SimAdapter
          |                        <-------  SimObservation (initial)
          |                                    joint_positions: [q0..q6]
          |                                    rgb_image: bytes
          |
          |   PolicyAdapter.predict_action(
          |     image=sim_initial_image,      -- NOTE: uses sim's initial obs,
          |     task=task_description,            not the user's input image
          |     proprioception=joint_positions    (sim is ground truth)
          |   ) --------------------------->  PolicyAdapter
          |                        <--------  ActionPrediction
          |                                    actions: [ActionStep x 8]
          |
          |   for action in actions:
          |     sim.step(action)  ----------> SimAdapter
          |                        <--------  SimStepResult
          |                                    success: bool
          |                                    observation: SimObservation
          |
          |   final_obs = last SimObservation
          |   sim_success = any(step.success for step in steps)
          |
          | [STAGE 4: verify]
          | VLMAdapter.verify_success(
          |   initial_image=user_scene_image,     -- original user image
          |   final_image=final_obs.rgb_image,    -- sim post-execution render
          |   task=task_description,
          |   context=context                     -- accumulated pipeline context
          | ) --------------------------->  VLMAdapter
          |                        <------  VerificationResult
          |                                    vlm_success: bool
          |                                    confidence: float
          |                                    reasoning: str
          |
          | StrategyResult assembled:
          |   eval_id, strategy_id
          |   stage_results: [perceive, plan, act, verify]
          |   success: vlm_success (primary)
          |   sim_success: sim_success (ground truth)
          |   judge_calibration: vlm_success == sim_success
          |   total_latency_ms: sum of all stage latencies
          |   total_cost_usd: sum of all VLM/PolicyAdapter costs
          |
          v
    +------------+
    |   SQLite   |
    | EvalStore  |
    +------------+
          |
          v
    RunManager collects all StrategyResult objects
    Leaderboard.rank(results) sorts by:
      1. success (True > False)
      2. total_latency_ms (ascending)
      3. total_cost_usd (ascending)
    Returns EvaluationResult with ranked list
          |
          v
    EvaluationRecord updated: status=complete, results=ranked_list
          |
          | SSE events pushed throughout for live dashboard updates
          v
    GET /api/evaluations/{id} returns final EvaluationResult
```

---

## 7. Key Design Decisions

### Decision 1: Plain HTML+JS dashboard, no frontend build

**Decision**: Dashboard is `frontend/index.html` + `frontend/app.js` served by FastAPI's `StaticFiles` mount. No npm, no Vite, no TypeScript, no React.

**Alternatives considered**:
- React + Vite + TypeScript (as in original CLAUDE.md): Richer component model, type safety, better ecosystem for complex UIs.
- HTMX: Simpler than React but still adds a JS framework dependency.
- Server-side Jinja2 templates: No JS at all, but can't do live SSE updates without JS.

**Why plain HTML+JS**: The dashboard is a read-mostly display of structured JSON data from a well-defined API. It does not need a component framework. The SSE stream for live updates is natively supported by the browser `EventSource` API without any library. Eliminating the frontend build step removes: `node_modules/`, `package.json`, `tsconfig.json`, the Vite config, and the entire npm toolchain. This is a 0-dependency frontend that any Python developer can modify by editing a single HTML file. For a tool used by ML engineers (not frontend developers), this is the right tradeoff. The dashboard can always be replaced later with a richer SPA without changing the API.

**Consequences**: No TypeScript type checking on frontend code. No component reuse patterns. Acceptable given the dashboard's read-mostly, display-focused nature.

### Decision 2: SSE for live progress, not WebSocket

**Decision**: Live evaluation progress uses Server-Sent Events (`GET /api/evaluations/{id}/stream`) not WebSocket.

**Alternatives considered**:
- WebSocket: Bidirectional, supports push from server AND messages from client. More complex server-side state management.
- Long polling: Simpler but wastes connections and adds latency between completions.
- WebSocket: Higher protocol complexity, requires specific ASGI handling, more complex reconnect logic.

**Why SSE**: Evaluation streaming is unidirectional — the server pushes events, the client only reads. SSE is exactly this pattern. It uses plain HTTP, works through proxies and load balancers without special handling, has native browser support via `EventSource`, and reconnects automatically on drop. FastAPI supports SSE via `StreamingResponse` with `text/event-stream` content type. No additional library needed. The client HTML uses `new EventSource(url)` — 3 lines of JS.

**Consequences**: Cannot push messages from client to server over the stream. That's acceptable because clients send control messages (cancel, etc.) via separate REST calls.

### Decision 3: FastAPI calls adapters directly; MCP is for external agents

**Decision**: The FastAPI backend imports and calls adapter instances directly from Python. MCP servers are separate processes that also import the same adapters. MCP is NOT a transport layer for the evaluation backend.

**Alternatives considered**:
- FastAPI calls MCP servers over HTTP: Adds network hop, adds latency, creates availability dependency.
- MCP servers run the evaluations: MCP tool calls have token/response limits that make streaming evaluation results awkward.
- Single process with MCP embedded: Mixing request/response semantics of MCP with the streaming job model of evaluations.

**Why direct adapter calls for evaluation**: Evaluation throughput matters. An evaluation running 3 strategies with 4 pipeline stages each = 12 adapter calls. Each via HTTP would add ~5-50ms overhead per call = 60-600ms of pure network overhead. Direct Python calls eliminate this entirely. MCP servers exist for a different use case: an AI agent (Claude Desktop, Copilot) building a robotics pipeline who wants to call `analyze_scene` as a tool in their agent loop. These are interactive, low-volume calls where the MCP tool abstraction has high value. They are architecturally separate concerns.

**Consequences**: MCP servers and the FastAPI backend must both import from the same adapter registry. This is straightforward since they share the same Python package. The registry is stateless (returns adapter instances configured from YAML), so sharing it is safe.

### Decision 4: SimAdapter is gated — evaluation has a sim-required mode and sim-optional mode

**Decision**: If no `SimAdapter` is configured or `--mode vlm-only` is passed, ROVE evaluates stages 1-2 (perceive, plan) and runs a planning-quality verification in stage 4. Stage 3 (act) is skipped entirely. The system does not fail; it produces a valid (but scoped) evaluation.

**Alternatives considered**:
- Always require sim: Forces users to install MuJoCo/LIBERO even for pure VLM evaluation tasks.
- Fake sim results when sim unavailable: Produces misleading success metrics.

**Why two modes**: Many users want to evaluate VLM perception and planning quality without a full simulation loop. Installing MuJoCo + LIBERO + a task suite is a significant dependency that blocks early adoption. A `vlm-only` evaluation is a genuinely useful product: you can compare how GPT-4o vs Qwen2.5-VL interpret the same scene and plan the same task without any robot simulation. The distinction between modes is explicit in `EvaluationRequest.mode` and reflected in the results with clear labeling.

### Decision 5: Canonical action space is EE delta; adapters normalize

**Decision**: The `ActionPrediction.actions` list always contains `ActionStep` values in end-effector delta format in robot base frame. Adapters that produce joint positions or absolute poses do the conversion internally.

**Alternatives considered**:
- Pass-through raw action format: Each consumer of `ActionPrediction` handles normalization. Creates coupling between the sim and VLA specifics.
- Multiple canonical formats with type tag: ActionPrediction.action_space selects the format. Sim adapter must handle all formats.

**Why EE delta is canonical**: The SimAdapter needs a single, predictable format to step through actions. EE delta is the most VLA-agnostic format: it doesn't require knowledge of robot kinematics to apply (the sim handles that), and all current VLAs can be normalized to it. SmolVLA and CogACT natively output this; OpenVLA needs token detokenization; GR00T needs an embodiment-specific transform. The raw output is preserved in `ActionPrediction.raw_output` for debugging and the action_space field records what the model originally produced.

### Decision 6: VLM judge calibration as a first-class metric

**Decision**: `StrategyResult` includes `judge_calibration: bool` indicating whether the VLM verification result matched the sim ground truth. This is tracked and surfaced in the leaderboard.

**Alternatives considered**:
- Use only sim success: Ignores VLM verification quality, which is part of the pipeline evaluation.
- Use only VLM success: Ignores physics ground truth, making evaluation gameable by a VLM that always says "success."

**Why both + calibration**: The RAI-ADR-003 (VLM-as-judge risk) documents this concern. A VLM that verifies its own plan outputs creates a feedback loop risk. By tracking `sim_success` independently and computing `judge_calibration`, ROVE surfaces when a VLM is a poor judge of its own outputs. This is a system-level evaluation insight that can't be obtained by looking at either metric alone. The primary ranking uses `vlm_success` (because in production without a sim, only VLM verification is available), but `judge_calibration` is displayed and exported.

### Decision 7: rove.yaml is the single source of truth; Foundry migration path is additive

**Decision**: Model and strategy registration stays in `rove.yaml` through Phase 3. Phase 4 adds `AdapterRegistry.from_foundry(project_client)` that can supplement or replace YAML with `project_client.deployments.list()`. Both sources can coexist.

**Alternatives considered**:
- Start with Foundry API directly: Requires Azure credentials from day 1, blocks local/offline development.
- Never support Foundry: Abandons the cloud deployment path.

**Why YAML first, Foundry additive**: Phase 1-2 work must run offline on a laptop with mock adapters. YAML is zero-dependency. The Foundry path is a Phase 4 concern and can be added as a secondary resolver in `AdapterRegistry` without changing the rest of the system. This follows the open/closed principle: extend the registry with a new resolver, don't modify the existing YAML resolver.

---

## 8. Monorepo Directory Structure

```
rove/                                  # git root
├── pyproject.toml                     # package: rove-eval, entry points
├── rove.yaml                          # single source of truth: all model + strategy configs
├── src/
│   └── rove/                          # Python package (import rove)
│       │
│       ├── __init__.py                # public API: EvaluationEngine, run_evaluation()
│       ├── models.py                  # ALL shared dataclasses + Pydantic models
│       │                              # SceneAnalysis, TaskPlan, ActionPrediction,
│       │                              # SimObservation, StrategyResult,
│       │                              # EvaluationRequest, EvaluationResult
│       │
│       ├── config.py                  # load rove.yaml, read env vars
│       │                              # no global state: Config is a dataclass
│       │
│       ├── adapters/
│       │   ├── protocols.py           # Protocol definitions ONLY
│       │   │                          # VLMAdapter, PolicyAdapter, AgentAdapter,
│       │   │                          # SimAdapter — all @runtime_checkable
│       │   │                          # CHANGE CAREFULLY: this is the contract
│       │   │
│       │   ├── registry.py            # AdapterRegistry
│       │   │                          # .from_yaml(path) -> AdapterRegistry
│       │   │                          # .get_vlm(model_id) -> VLMAdapter
│       │   │                          # .get_policy(model_id) -> PolicyAdapter
│       │   │                          # .get_agent(model_id) -> AgentAdapter
│       │   │                          # .get_sim(model_id) -> SimAdapter
│       │   │                          # .health_check(model_id) -> bool
│       │   │
│       │   ├── vlm/
│       │   │   ├── __init__.py
│       │   │   ├── mock.py            # MockVLMAdapter — Phase 1 testing
│       │   │   ├── azure_openai.py    # GPT-4o via Azure OpenAI API
│       │   │   ├── azure_nim.py       # NIM-hosted VLMs (Cosmos-Reason2, etc.)
│       │   │   ├── azure_hf.py        # HF Managed Endpoints (Qwen2.5-VL)
│       │   │   └── local_mlx.py       # Local Apple Silicon MLX inference
│       │   │
│       │   ├── vla/
│       │   │   ├── __init__.py
│       │   │   ├── mock.py            # MockPolicyAdapter — Phase 1 testing
│       │   │   ├── local_smolvla.py   # SmolVLA via LeRobot, MPS backend
│       │   │   ├── local_openvla.py   # OpenVLA-OFT via HF Transformers + MLX
│       │   │   ├── azure_gpu_cogact.py # CogACT on Azure GPU VM via HTTP
│       │   │   └── azure_gpu_groot.py # GR00T N1.6 (awaiting public weights)
│       │   │
│       │   ├── agent/
│       │   │   ├── __init__.py
│       │   │   ├── mock.py            # MockAgentAdapter — Phase 1 testing
│       │   │   └── azure_foundry.py   # Azure Foundry agent adapter (stubbed)
│       │   │
│       │   ├── grounding/
│       │   │   ├── __init__.py
│       │   │   ├── mock.py            # MockGroundingAdapter
│       │   │   ├── local_groundingdino.py # GroundingDINO, PyTorch MPS+CPU fallback
│       │   │   └── local_coreml_sam2.py   # SAM2 via CoreML (NOT PyTorch MPS — broken)
│       │   │
│       │   └── sim/
│       │       ├── __init__.py
│       │       ├── mock.py            # MockSimAdapter
│       │       └── local_mujoco.py    # MuJoCo + LIBERO task suite
│       │
│       ├── orchestrator/
│       │   ├── __init__.py
│       │   ├── pipeline.py            # EvaluationPipeline
│       │   │                          # .run(task, image, adapters, sim)
│       │   │                          #   -> StrategyResult (async)
│       │   │                          # 4-stage pipeline: perceive/plan/act/verify
│       │   │                          # DETERMINISTIC: no LLM decisions here
│       │   │
│       │   └── run_manager.py         # RunManager — concurrent multi-strategy
│       │                              #   execution with TaskGroup + Semaphore
│       │                              # .run_evaluation(request, registry, store,
│       │                              #   event_queue) -> EvaluationResult
│       │                              # asyncio.TaskGroup over strategies
│       │                              # writes stage events to event_queue for SSE
│       │
│       ├── evaluation/
│       │   ├── __init__.py
│       │   ├── store.py               # EvaluationStore (SQLite)
│       │   │                          # .create_evaluation(request) -> eval_id
│       │   │                          # .save_strategy_result(result)
│       │   │                          # .get_evaluation(eval_id) -> EvaluationResult
│       │   │                          # .list_evaluations() -> list[EvalSummary]
│       │   │                          # .export_jsonl(eval_id) -> str (Foundry format)
│       │   │
│       │   └── leaderboard.py         # Leaderboard
│       │                              # .rank(results) -> list[StrategyResult]
│       │                              # sort: success > latency > cost
│       │                              # .aggregate(eval_ids) -> AggregatedLeaderboard
│       │
│       ├── api/
│       │   ├── __init__.py
│       │   ├── app.py                 # FastAPI app factory
│       │   │                          # create_app(config) -> FastAPI
│       │   │                          # mounts: /api router, /static, /
│       │   │                          # no global state: registry/store injected
│       │   │
│       │   ├── routes.py              # all API route handlers
│       │   │                          # POST /api/evaluations
│       │   │                          # GET  /api/evaluations/{id}
│       │   │                          # GET  /api/evaluations/{id}/stream  (SSE)
│       │   │                          # GET  /api/evaluations
│       │   │                          # GET  /api/models
│       │   │                          # GET  /api/strategies
│       │   │                          # GET  /api/leaderboard
│       │   │                          # POST /api/export
│       │   │
│       │   └── sse.py                 # SSEQueue and StreamingResponse helper
│       │                              # event types: stage_complete,
│       │                              #   strategy_done, evaluation_done,
│       │                              #   strategy_failed
│       │
│       ├── mcp_servers/
│       │   ├── __init__.py
│       │   ├── vlm_server.py          # FastMCP server, port 8081
│       │   │                          # tool: analyze_scene(image_b64, task, model_id)
│       │   │                          # tool: plan_task(image_b64, task, scene, model_id)
│       │   │                          # tool: verify_success(before_b64, after_b64, task)
│       │   │                          # tool: list_vlms()
│       │   │
│       │   ├── vla_server.py          # FastMCP server, port 8082
│       │   │                          # tool: predict_action(image_b64, task, model_id,
│       │   │                          #          proprioception?)
│       │   │                          # tool: list_vlas()
│       │   │
│       │   └── grounding_sim_server.py  # FastMCP server, port 8083
│       │                              # tool: localize(image_b64, query, model_id)
│       │                              # tool: segment(image_b64, bbox, model_id)
│       │                              # tool: sim_reset(task_id)
│       │                              # tool: sim_step(action)
│       │
│       └── cli.py                     # Click CLI
│                                      # rove evaluate [options]
│                                      # rove models [list|health]
│                                      # rove export [eval_id]
│                                      # rove serve [--port]
│                                      # rove mcp [vlm|vla|grounding]
│
├── frontend/                          # Plain HTML+JS static files
│   ├── index.html                     # Single page: task input + results table
│   ├── app.js                         # fetch() calls to FastAPI, EventSource for SSE
│   └── style.css                      # Dark theme (#09090b background)
│
├── tests/
│   ├── conftest.py                    # pytest fixtures: mock registry, temp store
│   ├── test_models.py                 # shared model tests
│   ├── test_orchestrator.py           # end-to-end pipeline with mock adapters
│   ├── test_adapters/                 # per-adapter unit tests
│   ├── test_api.py                    # FastAPI route tests (TestClient)
│   └── test_evaluation/               # store + leaderboard tests
│
└── docs/
    ├── architecture/
    │   └── high-level-architecture.md  # THIS DOCUMENT
    └── responsible-ai/                 # RAI ADRs
```

---

## 9. rove.yaml Structure (SHIVA-Inspired Configuration)

The YAML follows the SHIVA pattern: **models** (atomic resources) + **strategies** (named pipeline configurations referencing models by ID) + **defaults**. Everything is declared; evaluations are reproducible by sharing the YAML.

```yaml
# rove.yaml — single source of truth for models AND evaluations
version: "1.0"

# =============================================================================
# Models — Atomic model configurations (one model per entry)
# =============================================================================
# Each model maps to one adapter class. Secrets live in environment variables.
# Pattern mirrors SHIVA resources: provider_type → adapter, endpoint → config.

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
      capabilities: [scene_analysis, task_planning, verification]
      cost: { type: per_token, input_per_1k: 0.005, output_per_1k: 0.015 }

    qwen25-vl-32b:
      adapter: azure_hf_managed
      display_name: "Qwen2.5-VL 32B"
      deployment_type: azure_managed
      config:
        endpoint_env: AZURE_QWEN_ENDPOINT
        api_key_env: AZURE_QWEN_KEY
      capabilities: [scene_analysis, native_grounding, task_planning, verification]
      cost: { type: per_token, input_per_1k: 0.002, output_per_1k: 0.006 }

    cosmos-reason2-2b:
      adapter: local_mlx
      display_name: "Cosmos-Reason2 2B"
      deployment_type: local
      config:
        model_path: "mlx-community/cosmos-reason2-2b-4bit"
        max_tokens: 1024
      capabilities: [scene_analysis, verification]
      cost: { type: fixed, per_call: 0.00 }

    mock-vlm:
      adapter: mock_vlm
      display_name: "Mock VLM"
      deployment_type: local
      config:
        mock_quality: 0.85
        mock_latency_ms: [200, 500]
      capabilities: [scene_analysis, task_planning, verification]

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
        requires_proprio: true            # needs joint state input
      cost: { type: fixed, per_call: 0.00 }
      max_concurrent: 1                   # MPS single device queue

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
      max_concurrent: 4

    mock-vla:
      adapter: mock_vla
      deployment_type: local
      config:
        mock_success_rate: 0.80
        mock_latency_ms: [50, 150]

  grounding:
    groundingdino:
      adapter: local_groundingdino
      config: { device: mps, prefer_device: cpu }  # CPU fallback for MPS issues
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

# =============================================================================
# Strategies — Named pipeline configurations (reference models by ID)
# =============================================================================
# Each strategy defines which model handles each pipeline stage.
# Evaluations reference strategies by name.

strategies:
  mock:
    display_name: "Mock (Simulation)"
    perceive: mock-vlm
    plan: mock-vlm
    act: mock-vla
    verify: mock-vlm
    sim: mock-sim
    tags: [test, mock]

  cloud-fast:
    display_name: "Cloud Fast (GPT-4o + SmolVLA)"
    perceive: gpt-4o
    plan: gpt-4o
    act: smolvla-450m
    verify: gpt-4o
    sim: mujoco-libero
    tags: [cloud, low-latency]

  cloud-accurate:
    display_name: "Cloud Accurate (GPT-4o + CogACT)"
    perceive: gpt-4o
    plan: gpt-4o
    act: cogact-7b
    verify: gpt-4o
    sim: mujoco-libero
    tags: [cloud, high-accuracy]

  local-only:
    display_name: "Local Only (Qwen + SmolVLA)"
    perceive: qwen25-vl-32b
    plan: qwen25-vl-32b
    act: smolvla-450m
    verify: qwen25-vl-32b
    sim: mujoco-libero
    tags: [local, offline]

# =============================================================================
# Defaults — Global settings
# =============================================================================
defaults:
  sim: mock-sim
  trials: 1
  mode: full                              # full | vlm_only
  max_concurrent_strategies: 9
  timeout_per_stage_ms: 30000
  max_retries_per_stage: 2
```

### How the YAML is consumed

```python
# CLI reads the YAML and dispatches:
# rove evaluate --config rove.yaml --strategies mock,cloud-fast

config = RoveConfig.from_yaml("rove.yaml")
strategies = [config.strategies[s] for s in ["mock", "cloud-fast"]]
registry = AdapterRegistry(config.models)

# The RunManager resolves model IDs from each strategy config:
# Each strategy specifies perceive, plan, act, verify, sim model IDs
# RunManager creates one EvaluationPipeline per strategy and runs them:
results = await run_manager.execute(strategies, registry)
```

The API endpoint accepts either named `strategy_ids` (referencing `rove.yaml`) or inline parameters (for ad hoc evaluation from the dashboard).

---

## 10. Protocol Definitions (Canonical Reference)

These are the contracts. They live in `src/rove/adapters/protocols.py`. Every adapter implements one of these Protocols. No adapter inherits from a base class; all use `@runtime_checkable Protocol`.

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class VLMAdapter(Protocol):
    model_id: str

    async def analyze_scene(
        self,
        image: bytes,
        task: str,
    ) -> SceneAnalysis: ...

    async def plan_task(
        self,
        image: bytes,
        task: str,
        scene: SceneAnalysis,
    ) -> TaskPlan: ...

    async def verify_success(
        self,
        initial_image: bytes,
        final_image: bytes,
        task: str,
        context: dict | None = None,
    ) -> VerificationResult: ...

    async def health_check(self) -> bool: ...


@runtime_checkable
class PolicyAdapter(Protocol):
    model_id: str
    action_space: str              # "ee_delta" after normalization

    async def predict_action(
        self,
        image: bytes,
        task: str,
        proprioception: list[float] | None = None,
    ) -> ActionPrediction: ...

    async def health_check(self) -> bool: ...


@runtime_checkable
class AgentAdapter(Protocol):
    model_id: str

    async def run_stage(
        self,
        stage: str,
        image_base64: str,
        task: str,
        context: dict | None = None,
    ) -> dict: ...

    async def health_check(self) -> bool: ...


@runtime_checkable
class SimAdapter(Protocol):
    model_id: str

    async def reset(
        self,
        task_id: str,
    ) -> SimObservation: ...

    async def step(
        self,
        action: list[float],
    ) -> SimStepResult: ...

    async def get_observation(self) -> SimObservation: ...

    async def health_check(self) -> bool: ...
```

---

## 11. API Reference (Key Endpoints)

### POST /api/evaluations

Request:
```json
{
  "task": "Pick the red bracket and place it in bin A",
  "image_b64": "<base64-encoded JPEG>",
  "strategy_ids": ["mock", "cloud-fast", "cloud-accurate"],
  "mode": "full"
}
```

Response (202 Accepted):
```json
{
  "evaluation_id": "eval_abc123",
  "status": "queued",
  "total_strategies": 3,
  "created_at": "2026-02-18T10:00:00Z"
}
```

`mode` values: `"full"` (all 4 stages, requires sim), `"vlm-only"` (stages 1-2 + verify plan quality, no sim required)

### GET /api/evaluations/{id}

Response (200 OK, running):
```json
{
  "evaluation_id": "eval_abc123",
  "status": "running",
  "task": "Pick the red bracket...",
  "completed_strategies": 1,
  "total_strategies": 3,
  "partial_results": [
    {
      "strategy_id": "mock",
      "status": "complete",
      "success": true,
      "sim_success": true,
      "judge_calibration": true,
      "total_latency_ms": 1842,
      "total_cost_usd": 0.021,
      "stage_results": { ... }
    }
  ]
}
```

Response (200 OK, complete) adds `"ranked_results": [...]` sorted by success > latency > cost.

### GET /api/strategies

Response (200 OK):
```json
{
  "strategies": [
    {
      "id": "mock",
      "display_name": "Mock (Simulation)",
      "perceive": "mock-vlm",
      "plan": "mock-vlm",
      "act": "mock-vla",
      "verify": "mock-vlm",
      "sim": "mock-sim",
      "tags": ["test", "mock"]
    }
  ]
}
```

### GET /api/evaluations/{id}/stream

Content-Type: `text/event-stream`

Event types:
```
event: stage_complete
data: {"strategy_id": "mock", "stage": "perceive", "latency_ms": 412}

event: strategy_done
data: {"strategy_id": "mock", "success": true, "total_latency_ms": 1842}

event: strategy_failed
data: {"strategy_id": "cloud-fast", "failed_stage": "act", "error": "timeout"}

event: evaluation_done
data: {"evaluation_id": "eval_abc123", "total_strategies": 3, "successful": 2}
```

---

## 12. MCP Server Tool Schemas

MCP tools use base64-encoded images to stay within the 1MB MCP binary limit. Images must be resized to <750KB before encoding.

### VLM MCP Server (port 8081)

```python
# Tool: analyze_scene
Input:  {"image_b64": str, "task": str, "model_id": str}
Output: SceneAnalysis as JSON

# Tool: plan_task
Input:  {"image_b64": str, "task": str, "scene": dict, "model_id": str}
Output: TaskPlan as JSON

# Tool: verify_success
Input:  {"before_b64": str, "after_b64": str, "task": str, "model_id": str}
Output: VerificationResult as JSON

# Tool: list_vlms
Input:  {}
Output: [{"id": str, "name": str, "enabled": bool}]
```

### VLA MCP Server (port 8082)

```python
# Tool: predict_action
Input:  {"image_b64": str, "task": str, "model_id": str, "proprioception": list[float] | null}
Output: ActionPrediction as JSON (actions normalized to ee_delta)

# Tool: list_vlas
Input:  {}
Output: [{"id": str, "name": str, "action_space": str, "enabled": bool}]
```

### Grounding+Sim MCP Server (port 8083)

```python
# Tool: localize
Input:  {"image_b64": str, "query": str, "model_id": str}
Output: Localization as JSON (bbox normalized [0,1])

# Tool: segment
Input:  {"image_b64": str, "bbox": dict, "model_id": str}
Output: SegmentationMask as JSON | null

# Tool: sim_reset
Input:  {"task_id": str}
Output: SimObservation as JSON (rgb_image field is base64 JPEG)

# Tool: sim_step
Input:  {"action": {"dx":f,"dy":f,"dz":f,"droll":f,"dpitch":f,"dyaw":f,"gripper":f}}
Output: SimStepResult as JSON
```

---

## 13. Phase Build Order

| Phase | What Ships | Dependencies |
|-------|-----------|--------------|
| 1 | Mock adapters, full 4-stage pipeline, CLI, FastAPI, dashboard (HTML+JS), SQLite store, leaderboard, SSE streaming | Python stdlib + FastAPI + PyYAML + Pydantic + Click |
| 2 | SmolVLA (LeRobot/MPS), GroundingDINO, CoreML SAM2, MuJoCo+LIBERO | torch, lerobot, coremltools, mujoco |
| 3 | GPT-4o (Azure OpenAI), Qwen2.5-VL (Azure HF), CogACT (Azure GPU HTTP) | azure-ai-inference, openai SDK |
| 4 | MCP servers (FastMCP), Azure Foundry JSONL export, AdapterRegistry.from_foundry() | fastmcp, azure-ai-evaluation |

Phase 1 has zero ML dependencies. The entire 4-stage evaluation pipeline is testable with mock adapters before any model is connected.

---

## 14. Known Constraints and Architecture Implications

| Constraint | Implication |
|-----------|-------------|
| Cosmos-Reason-1 deprecated 2026-03-18 on Azure NIM | Do not add as a supported model. Cosmos-Reason2-2b is the replacement via local MLX. |
| SAM2: PyTorch MPS broken | `local_coreml_sam2.py` adapter uses `apple/coreml-sam2-large`. No PyTorch path. |
| OpenVLA int4: bitsandbytes no MPS | `local_openvla.py` uses MLX quantization, not bitsandbytes. |
| GR00T N1.6: weights not public | `azure_gpu_groot.py` adapter is a stub until weights release. Protocol compliance enforced but health_check returns False. |
| MCP binary limit: 1MB | All image encoding in MCP tool handlers must resize to <750KB JPEG. The evaluation path (FastAPI → adapters directly) is not subject to this limit. |
| VLM stateless per-call | No persistent context across pipeline stages. The pipeline explicitly passes SceneAnalysis and TaskPlan as context to subsequent VLM calls. Context accumulates as a dictionary passed through the pipeline. |

---

*Document version 1.1 — System Architect, 2026-02-22*
*Next review: after Phase 1 implementation complete*
