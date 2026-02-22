# ROVE — High-Level Architecture

**Status**: Accepted
**Date**: 2026-02-18
**Authors**: System Architect
**Version**: 1.0 — Post-founder-review

---

## 1. Executive Summary

ROVE evaluates robotics agent pipelines by running real inference through VLM+VLA+LLM combinations on your specific task. The user starts from a task description and a scene image, selects candidate models for each pipeline stage, and ROVE runs the full perceive-ground-plan-execute-verify pipeline across every combination in parallel, producing a ranked comparison of agent configurations.

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
|  |    asyncio.gather(                                                  |
|  |      RoveOrchestrator(vlm_1, vla_1),   <-- combination 1          |
|  |      RoveOrchestrator(vlm_1, vla_2),   <-- combination 2          |
|  |      RoveOrchestrator(vlm_2, vla_1),   <-- combination 3          |
|  |      ...                                                            |
|  |    )                                                                |
|  |                                                                     |
|  |  RoveOrchestrator (per combination, deterministic 5-step)         |
|  |    1. perceive  --> VLMAdapter.analyze_scene()                     |
|  |    2. ground    --> GroundingAdapter.localize()                    |
|  |    3. plan      --> VLMAdapter.plan_grasp()                        |
|  |    4. execute   --> VLAAdapter.predict_action() + SimAdapter.step()|
|  |    5. verify    --> VLMAdapter.verify_success()                    |
|  |                                                                     |
|  +--------+------------------------------------------------------------+
|           |                                                            |
|           v                                                            |
|  +---------------------------------------------------------------------+
|  |                      Adapter Registry                               |
|  |                                                                     |
|  |  models.yaml --> AdapterRegistry --> resolves model_id to adapter  |
|  |                                                                     |
|  |  VLMAdapter Protocol:      VLAAdapter Protocol:                    |
|  |    mock_vlm                  mock_vla                              |
|  |    azure_openai_vlm          local_smolvla                         |
|  |    azure_nim_vlm             local_openvla                         |
|  |    azure_hf_vlm              azure_gpu_cogact                      |
|  |    local_mlx_vlm             azure_gpu_groot                       |
|  |                                                                     |
|  |  GroundingAdapter Protocol:  SimAdapter Protocol:                  |
|  |    mock_grounding             mock_sim                             |
|  |    local_groundingdino        local_mujoco                         |
|  |    local_coreml_sam2                                               |
|  |                                                                     |
|  +--------+------------------------------------------------------------+
|           |                                                            |
|           v                                                            |
|  +---------------------------------------------------------------------+
|  |                    Evaluation Store (SQLite)                        |
|  |                                                                     |
|  |  evaluations table    combination_results table                    |
|  |  step_results table   leaderboard (aggregated view)                |
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
  | plan_grasp()      |  | list_vlas()       |  | segment()         |
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

The evaluation API is fully non-blocking. This is essential because an N×M evaluation (e.g., 3 VLMs × 3 VLAs = 9 combinations) can take 10–120 seconds depending on model latency.

```
Client                      FastAPI                     BackgroundJobRunner
  |                            |                                |
  | POST /api/evaluations      |                                |
  | {task, image, vlms, vlas}  |                                |
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
  |  running + partial results |   completed_combinations: 2,  |
  |                            |   total_combinations: 9}      |
  |                            |                                |
  |                            |         [background work]      |
  |                            |         RunManager runs       |
  |                            |         9 orchestrators       |
  |                            |         asyncio.gather()      |
  |                            |         each completes and    |
  |                            |         writes to SQLite      |
  |                            |                               |
  | OR: subscribe to SSE       |                               |
  | GET /evaluations/{id}/stream                               |
  |-------------------------->|                               |
  |<-- event: step_complete --|<-- SSEQueue.put(event) -------|
  |<-- event: step_complete --|                               |
  |<-- event: combination_done|                               |
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

Each combination within an evaluation has its own state:

```
pending --> running --> success
                   \--> failed (step_name, error_message)
```

Partial failure is non-fatal: if combination 3 of 9 fails, the other 8 complete and are ranked. The failed combination is recorded with its failure step.

---

## 4. The 5-Step Pipeline in Detail

```
INPUT: task_description (str), scene_image (bytes), vlm_id, vla_id

+-------------+     +-------------+     +-------------+
|   perceive  |     |   ground    |     |    plan     |
|             |     |             |     |             |
| VLM sees    | --> | Grounding   | --> | VLM plans   |
| scene image |     | model       |     | grasp       |
| + task      |     | localizes   |     | given bbox  |
| description |     | target      |     | + scene     |
|             |     | object      |     | analysis    |
+------+------+     +------+------+     +------+------+
       |                   |                   |
       v                   v                   v
  SceneAnalysis       Localization         GraspPlan
  (see §5.A)         (see §5.B)           (see §5.C)
       |                   |                   |
       +-------------------+-------------------+
                           |
                           v
              +------------+------------+
              |                         |
              |         execute         |
              |                         |
              |  VLA predicts action    |
              |  chunk given:           |
              |  - scene image          |
              |  - task description     |
              |  - GraspPlan (optional, |
              |    if VLA accepts it)   |
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
                     CombinationResult
                     (saved to SQLite)

OUTPUT: ranked list of CombinationResult across all VLM×VLA combinations
```

### VLM-Only Mode (no sim, no VLA)

When no VLA is specified OR when `--mode vlm-only` is passed, the pipeline runs steps 1-3 only. This is valid for evaluating perception and planning quality without action execution. Step 4 is skipped; step 5 runs a "counterfactual" verify using only the initial image and the plan.

```
perceive --> ground --> plan --> [skip execute] --> verify(plan quality)
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
    # Pixel coordinates come from the Grounding step, not here.

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

**Key constraint**: VLMs operating on a single 2D image WITHOUT depth data cannot provide metric 3D coordinates. `SceneAnalysis` deliberately omits pixel bounding boxes — those are the Grounding model's job. The VLM contributes semantic understanding and object identification; the Grounding model contributes spatial localization.

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

### 5.C What VLMs Return for Planning: GraspPlan

The planning step is where VLMs are asked to reason about HOW to grasp an object. The VLM receives the scene analysis, the bounding box, and the task description. It returns a structured plan.

**The depth problem**: VLMs cannot compute metric 3D grasp poses from a single 2D image. They can reason about approach direction (from above, from the side), grip orientation, and relative positioning. The adapter captures this as:

```python
@dataclass
class GraspApproach:
    direction: str           # "top-down", "side-left", "side-right", "front"
    # This is intentionally NOT a 3D vector here.
    # Metric approach vectors require depth. The sim provides the metric transform.
    # The VLM contributes the semantic choice; the sim converts to robot frame.

@dataclass
class GraspPlan:
    target_object: str            # "red bracket"
    grasp_approach: GraspApproach
    pre_grasp_description: str    # natural language: "move above the bracket"
    grasp_description: str        # "close gripper on the bracket body"
    post_grasp_description: str   # "lift and move to bin A"
    confidence: float             # VLM's self-reported confidence
    bbox_used: BoundingBox        # the bbox from Localization that was used
    raw_response: str
    latency_ms: float
    cost_usd: float
    model_id: str
```

**Design rationale**: The sim adapter translates `GraspApproach.direction` ("top-down") into a metric approach vector in robot base frame using the sim's known camera intrinsics and object depth from the physics engine. This keeps the VLM interface clean while preserving the ability to execute the plan.

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
                    vlms: [gpt-4o, qwen25-vl]
                    vlas: [smolvla, cogact]

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
                      +--------------+
                      |  RunManager  |
                      |  4 combos:   |
                      |  gpt4o+smol  |
                      |  gpt4o+cog   |
                      |  qwen+smol   |
                      |  qwen+cog    |
                      +--------------+
                              |
                   asyncio.gather() -- all 4 run concurrently
                              |
          +-------------------+-------------------+
          |                   |                   |
          v                   v                   v
  Orchestrator(1)     Orchestrator(2)     Orchestrator(3) ...
  gpt4o + smolvla     gpt4o + cogact      qwen + smolvla
          |
          | [STEP 1: perceive]
          | image bytes  -------> VLMAdapter.analyze_scene(image, task)
          |               <------  SceneAnalysis
          |                           objects: [{label, confidence, spatial_hint}]
          |                           task_relevant: ["red bracket"]
          |
          | [STEP 2: ground]
          | image bytes  -------> GroundingAdapter.localize(image, "red bracket")
          |               <------  Localization
          |                           bbox: {x_min, y_min, x_max, y_max} [0,1]
          |                           segmentation: mask (optional)
          |
          | [STEP 3: plan]
          | image+analysis+bbox -> VLMAdapter.plan_grasp(image, task, scene, bbox)
          |               <------  GraspPlan
          |                           approach: "top-down"
          |                           grasp_description: "..."
          |
          | [STEP 4: execute]
          |
          |   sim.reset(task_id)  --------> SimAdapter
          |                        <-------  SimObservation (initial)
          |                                    joint_positions: [q0..q6]
          |                                    rgb_image: bytes
          |
          |   VLAAdapter.predict_action(
          |     image=sim_initial_image,      -- NOTE: uses sim's initial obs,
          |     task=task_description,            not the user's input image
          |     proprioception=joint_positions    (sim is ground truth)
          |   ) --------------------------->  VLAAdapter
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
          | [STEP 5: verify]
          | VLMAdapter.verify_success(
          |   initial_image=user_scene_image,     -- original user image
          |   final_image=final_obs.rgb_image,    -- sim post-execution render
          |   task=task_description
          | ) --------------------------->  VLMAdapter
          |                        <------  VerificationResult
          |                                    vlm_success: bool
          |                                    confidence: float
          |                                    reasoning: str
          |
          | CombinationResult assembled:
          |   eval_id, vlm_id, vla_id
          |   step_results: [perceive, ground, plan, execute, verify]
          |   success: vlm_success (primary)
          |   sim_success: sim_success (ground truth)
          |   judge_calibration: vlm_success == sim_success
          |   total_latency_ms: sum of all step latencies
          |   total_cost_usd: sum of all VLM/VLA costs
          |   action_quality: verification confidence
          |
          v
    +------------+
    |   SQLite   |
    | EvalStore  |
    +------------+
          |
          v
    RunManager collects all CombinationResult objects
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

**Decision**: Dashboard is `rove/dashboard/index.html` + `rove/dashboard/app.js` served by FastAPI's `StaticFiles` mount. No npm, no Vite, no TypeScript, no React.

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

**Why direct adapter calls for evaluation**: Evaluation throughput matters. A 9-combination evaluation running 5 pipeline steps each = 45 adapter calls. Each via HTTP would add ~5-50ms overhead per call = 225-2250ms of pure network overhead. Direct Python calls eliminate this entirely. MCP servers exist for a different use case: an AI agent (Claude Desktop, Copilot) building a robotics pipeline who wants to call `analyze_scene` as a tool in their agent loop. These are interactive, low-volume calls where the MCP tool abstraction has high value. They are architecturally separate concerns.

**Consequences**: MCP servers and the FastAPI backend must both import from the same adapter registry. This is straightforward since they share the same Python package. The registry is stateless (returns adapter instances configured from YAML), so sharing it is safe.

### Decision 4: SimAdapter is gated — evaluation has a sim-required mode and sim-optional mode

**Decision**: If no `SimAdapter` is configured or `--mode vlm-only` is passed, ROVE evaluates steps 1-3 (perceive, ground, plan) and runs a planning-quality verification in step 5. Steps 4 (execute) is skipped entirely. The system does not fail; it produces a valid (but scoped) evaluation.

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

**Decision**: `CombinationResult` includes `judge_calibration: bool` indicating whether the VLM verification result matched the sim ground truth. This is tracked and surfaced in the leaderboard.

**Alternatives considered**:
- Use only sim success: Ignores VLM verification quality, which is part of the pipeline evaluation.
- Use only VLM success: Ignores physics ground truth, making evaluation gameable by a VLM that always says "success."

**Why both + calibration**: The RAI-ADR-003 (VLM-as-judge risk) documents this concern. A VLM that verifies its own plan outputs creates a feedback loop risk. By tracking `sim_success` independently and computing `judge_calibration`, ROVE surfaces when a VLM is a poor judge of its own outputs. This is a system-level evaluation insight that can't be obtained by looking at either metric alone. The primary ranking uses `vlm_success` (because in production without a sim, only VLM verification is available), but `judge_calibration` is displayed and exported.

### Decision 7: models.yaml is the single source of truth; Foundry migration path is additive

**Decision**: Model registration stays in `models.yaml` through Phase 3. Phase 4 adds `AdapterRegistry.from_foundry(project_client)` that can supplement or replace YAML with `project_client.deployments.list()`. Both sources can coexist.

**Alternatives considered**:
- Start with Foundry API directly: Requires Azure credentials from day 1, blocks local/offline development.
- Never support Foundry: Abandons the cloud deployment path.

**Why YAML first, Foundry additive**: Phase 1-2 work must run offline on a laptop with mock adapters. YAML is zero-dependency. The Foundry path is a Phase 4 concern and can be added as a secondary resolver in `AdapterRegistry` without changing the rest of the system. This follows the open/closed principle: extend the registry with a new resolver, don't modify the existing YAML resolver.

---

## 8. Monorepo Directory Structure

```
rove/                                  # git root
├── pyproject.toml                     # package: rove-eval, entry points
├── models.yaml                        # single source of truth: all model configs
├── rove/                              # Python package (import rove)
│   │
│   ├── __init__.py                    # public API: EvaluationEngine, run_evaluation()
│   ├── models.py                      # ALL shared dataclasses + Pydantic models
│   │                                  # SceneAnalysis, GraspPlan, ActionPrediction,
│   │                                  # Localization, SimObservation, CombinationResult,
│   │                                  # EvaluationRequest, EvaluationResult
│   │
│   ├── config.py                      # load models.yaml, read env vars
│   │                                  # no global state: Config is a dataclass
│   │
│   ├── adapters/
│   │   ├── protocols.py               # Protocol definitions ONLY
│   │   │                              # VLMAdapter, VLAAdapter, GroundingAdapter,
│   │   │                              # SimAdapter — all @runtime_checkable
│   │   │                              # CHANGE CAREFULLY: this is the contract
│   │   │
│   │   ├── registry.py                # AdapterRegistry
│   │   │                              # .from_yaml(path) -> AdapterRegistry
│   │   │                              # .get_vlm(model_id) -> VLMAdapter
│   │   │                              # .get_vla(model_id) -> VLAAdapter
│   │   │                              # .get_grounding(model_id) -> GroundingAdapter
│   │   │                              # .get_sim(model_id) -> SimAdapter
│   │   │                              # .health_check(model_id) -> bool
│   │   │
│   │   ├── vlm/
│   │   │   ├── __init__.py
│   │   │   ├── mock.py                # MockVLMAdapter — Phase 1 testing
│   │   │   ├── azure_openai.py        # GPT-4o via Azure OpenAI API
│   │   │   ├── azure_nim.py           # NIM-hosted VLMs (Cosmos-Reason2, etc.)
│   │   │   ├── azure_hf.py            # HF Managed Endpoints (Qwen2.5-VL)
│   │   │   └── local_mlx.py           # Local Apple Silicon MLX inference
│   │   │
│   │   ├── vla/
│   │   │   ├── __init__.py
│   │   │   ├── mock.py                # MockVLAAdapter — Phase 1 testing
│   │   │   ├── local_smolvla.py       # SmolVLA via LeRobot, MPS backend
│   │   │   ├── local_openvla.py       # OpenVLA-OFT via HF Transformers + MLX
│   │   │   ├── azure_gpu_cogact.py    # CogACT on Azure GPU VM via HTTP
│   │   │   └── azure_gpu_groot.py     # GR00T N1.6 (awaiting public weights)
│   │   │
│   │   ├── grounding/
│   │   │   ├── __init__.py
│   │   │   ├── mock.py                # MockGroundingAdapter
│   │   │   ├── local_groundingdino.py # GroundingDINO, PyTorch MPS+CPU fallback
│   │   │   └── local_coreml_sam2.py   # SAM2 via CoreML (NOT PyTorch MPS — broken)
│   │   │
│   │   └── sim/
│   │       ├── __init__.py
│   │       ├── mock.py                # MockSimAdapter
│   │       └── local_mujoco.py        # MuJoCo + LIBERO task suite
│   │
│   ├── orchestrator/
│   │   ├── __init__.py
│   │   ├── agent.py                   # RoveOrchestrator
│   │   │                              # .run(task, image, vlm, vla, grounding, sim)
│   │   │                              #   -> CombinationResult (async)
│   │   │                              # 5-step pipeline: perceive/ground/plan/
│   │   │                              #   execute/verify
│   │   │                              # DETERMINISTIC: no LLM decisions here
│   │   │
│   │   └── run_manager.py             # RunManager
│   │                                  # .run_evaluation(request, registry, store,
│   │                                  #   event_queue) -> EvaluationResult
│   │                                  # asyncio.gather() over N×M combinations
│   │                                  # writes step events to event_queue for SSE
│   │
│   ├── evaluation/
│   │   ├── __init__.py
│   │   ├── store.py                   # EvaluationStore (SQLite)
│   │   │                              # .create_evaluation(request) -> eval_id
│   │   │                              # .save_combination_result(result)
│   │   │                              # .get_evaluation(eval_id) -> EvaluationResult
│   │   │                              # .list_evaluations() -> list[EvalSummary]
│   │   │                              # .export_jsonl(eval_id) -> str (Foundry format)
│   │   │
│   │   └── leaderboard.py             # Leaderboard
│   │                                  # .rank(results) -> list[CombinationResult]
│   │                                  # sort: success > latency > cost
│   │                                  # .aggregate(eval_ids) -> AggregatedLeaderboard
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── app.py                     # FastAPI app factory
│   │   │                              # create_app(config) -> FastAPI
│   │   │                              # mounts: /api router, /static, /
│   │   │                              # no global state: registry/store injected
│   │   │
│   │   ├── routes.py                  # all API route handlers
│   │   │                              # POST /api/evaluations
│   │   │                              # GET  /api/evaluations/{id}
│   │   │                              # GET  /api/evaluations/{id}/stream  (SSE)
│   │   │                              # GET  /api/evaluations
│   │   │                              # GET  /api/models
│   │   │                              # GET  /api/leaderboard
│   │   │                              # POST /api/export
│   │   │
│   │   └── sse.py                     # SSEQueue and StreamingResponse helper
│   │                                  # event types: step_complete,
│   │                                  #   combination_done, evaluation_done,
│   │                                  #   combination_failed
│   │
│   ├── mcp_servers/
│   │   ├── __init__.py
│   │   ├── vlm_server.py              # FastMCP server, port 8081
│   │   │                              # tool: analyze_scene(image_b64, task, model_id)
│   │   │                              # tool: plan_grasp(image_b64, task, scene, bbox)
│   │   │                              # tool: verify_success(before_b64, after_b64, task)
│   │   │                              # tool: list_vlms()
│   │   │
│   │   ├── vla_server.py              # FastMCP server, port 8082
│   │   │                              # tool: predict_action(image_b64, task, model_id,
│   │   │                              #          proprioception?)
│   │   │                              # tool: list_vlas()
│   │   │
│   │   └── grounding_sim_server.py    # FastMCP server, port 8083
│   │                                  # tool: localize(image_b64, query, model_id)
│   │                                  # tool: segment(image_b64, bbox, model_id)
│   │                                  # tool: sim_reset(task_id)
│   │                                  # tool: sim_step(action)
│   │
│   ├── dashboard/                     # Plain HTML+JS static files
│   │   ├── index.html                 # Single page: task input + results table
│   │   ├── app.js                     # fetch() calls to FastAPI, EventSource for SSE
│   │   └── style.css                  # Dark theme (#09090b background)
│   │
│   └── cli.py                         # Click CLI
│                                      # rove evaluate [options]
│                                      # rove models [list|health]
│                                      # rove export [eval_id]
│                                      # rove serve [--port]
│                                      # rove mcp [vlm|vla|grounding]
│
├── tests/
│   ├── conftest.py                    # pytest fixtures: mock registry, temp store
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

The YAML follows the SHIVA pattern: **models** (atomic resources) + **evaluations** (named experiments referencing models by ID) + **defaults**. Everything is declared; evaluations are reproducible by sharing the YAML.

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
      capabilities: [scene_analysis, grasp_planning, verification]
      cost: { type: per_token, input_per_1k: 0.005, output_per_1k: 0.015 }

    qwen25-vl-32b:
      adapter: azure_hf_managed
      display_name: "Qwen2.5-VL 32B"
      deployment_type: azure_managed
      config:
        endpoint_env: AZURE_QWEN_ENDPOINT
        api_key_env: AZURE_QWEN_KEY
      capabilities: [scene_analysis, native_grounding, grasp_planning, verification]
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
      capabilities: [scene_analysis, grasp_planning, verification]

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
# Evaluations — Named evaluation configs (reference models by ID)
# =============================================================================
# Pattern mirrors SHIVA experiments: strategies reference resources by name.
# Each evaluation is reproducible — share the YAML to reproduce.

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
    mode: full
    tags: [pick-place, industrial]

  bracket_variations:
    description: "Test robustness across task variations"
    tasks:
      - "Pick the red bracket and place it in bin A"
      - "Pick the blue bolt and place it in bin B"
      - "Pick the green bracket from the cluttered area"
    image: scenes/multi_part_scene.jpg
    vlms: [gpt-4o, qwen25-vl-32b]
    vlas: [cogact-7b]
    grounding: groundingdino
    sim: mujoco-libero
    trials: 10
    tags: [variation-study, robustness]

  vlm_only_perception:
    description: "Compare VLM scene understanding without sim"
    task: "Identify all graspable objects on the workbench"
    image: scenes/cluttered_bench.jpg
    vlms: [gpt-4o, qwen25-vl-32b, cosmos-reason2-2b]
    vlas: []                               # empty = VLM-only mode
    mode: vlm_only
    trials: 3

  mock_test:
    description: "End-to-end test with mock models"
    task: "Pick the red bracket from bin A"
    vlms: [mock-vlm]
    vlas: [mock-vla]
    grounding: mock-grounding
    sim: mock-sim
    trials: 1
    tags: [test, mock]

# =============================================================================
# Defaults — Global settings
# =============================================================================
defaults:
  grounding: mock-grounding
  sim: mock-sim
  trials: 1
  mode: full                              # full | vlm_only
  max_concurrent_combinations: 9
  timeout_per_step_ms: 30000
  max_retries_per_step: 2
```

### How the YAML is consumed

```python
# CLI reads the YAML and dispatches:
# rove evaluate --config rove.yaml --evaluation pick_bracket

config = RoveConfig.from_yaml("rove.yaml")
eval_config = config.evaluations["pick_bracket"]
registry = AdapterRegistry(config.models)

# The RunManager resolves model IDs from the evaluation config:
vlms = [registry.get_vlm(id) for id in eval_config.vlms]
vlas = [registry.get_vla(id) for id in eval_config.vlas]
grounding = registry.get_grounding(eval_config.grounding or config.defaults.grounding)
sim = registry.get_sim(eval_config.sim or config.defaults.sim)

# RunManager creates N×M orchestrators and runs them:
results = await run_manager.execute(eval_config, vlms, vlas, grounding, sim)
```

The API endpoint accepts either a named `evaluation_id` (referencing `rove.yaml`) or inline parameters (for ad hoc evaluation from the dashboard).

---

## 10. Protocol Definitions (Canonical Reference)

These are the contracts. They live in `rove/adapters/protocols.py`. Every adapter implements one of these Protocols. No adapter inherits from a base class; all use `@runtime_checkable Protocol`.

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

    async def plan_grasp(
        self,
        image: bytes,
        task: str,
        scene: SceneAnalysis,
        bbox: BoundingBox,
    ) -> GraspPlan: ...

    async def verify_success(
        self,
        initial_image: bytes,
        final_image: bytes,
        task: str,
    ) -> VerificationResult: ...

    async def health_check(self) -> bool: ...


@runtime_checkable
class VLAAdapter(Protocol):
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
class GroundingAdapter(Protocol):
    model_id: str

    async def localize(
        self,
        image: bytes,
        query: str,
    ) -> Localization: ...

    async def segment(
        self,
        image: bytes,
        bbox: BoundingBox,
    ) -> SegmentationMask | None: ...

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
        action: ActionStep,
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
  "vlm_ids": ["gpt-4o", "qwen25-vl-32b"],
  "vla_ids": ["smolvla-450m", "cogact-7b"],
  "grounding_id": "groundingdino",
  "sim_id": "mujoco-libero",
  "mode": "full"
}
```

Response (202 Accepted):
```json
{
  "evaluation_id": "eval_abc123",
  "status": "queued",
  "total_combinations": 4,
  "created_at": "2026-02-18T10:00:00Z"
}
```

`mode` values: `"full"` (all 5 steps, requires sim), `"vlm-only"` (steps 1-3 + verify plan quality, no sim required)

### GET /api/evaluations/{id}

Response (200 OK, running):
```json
{
  "evaluation_id": "eval_abc123",
  "status": "running",
  "task": "Pick the red bracket...",
  "completed_combinations": 2,
  "total_combinations": 4,
  "partial_results": [
    {
      "vlm_id": "gpt-4o",
      "vla_id": "smolvla-450m",
      "status": "complete",
      "success": true,
      "sim_success": true,
      "judge_calibration": true,
      "total_latency_ms": 1842,
      "total_cost_usd": 0.021,
      "action_quality": 0.94,
      "step_results": { ... }
    }
  ]
}
```

Response (200 OK, complete) adds `"ranked_results": [...]` sorted by success > latency > cost.

### GET /api/evaluations/{id}/stream

Content-Type: `text/event-stream`

Event types:
```
event: step_complete
data: {"combination": "gpt-4o+smolvla", "step": "perceive", "latency_ms": 412}

event: combination_done
data: {"vlm_id": "gpt-4o", "vla_id": "smolvla-450m", "success": true, "total_latency_ms": 1842}

event: combination_failed
data: {"vlm_id": "qwen25-vl-32b", "vla_id": "cogact-7b", "failed_step": "execute", "error": "timeout"}

event: evaluation_done
data: {"evaluation_id": "eval_abc123", "total_combinations": 4, "successful": 3}
```

---

## 12. MCP Server Tool Schemas

MCP tools use base64-encoded images to stay within the 1MB MCP binary limit. Images must be resized to <750KB before encoding.

### VLM MCP Server (port 8081)

```python
# Tool: analyze_scene
Input:  {"image_b64": str, "task": str, "model_id": str}
Output: SceneAnalysis as JSON

# Tool: plan_grasp
Input:  {"image_b64": str, "task": str, "scene": dict, "bbox": dict, "model_id": str}
Output: GraspPlan as JSON

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
| 1 | Mock adapters, full orchestrator, CLI, FastAPI, dashboard (HTML+JS), SQLite store, leaderboard, SSE streaming | Python stdlib + FastAPI + PyYAML + Pydantic + Click |
| 2 | SmolVLA (LeRobot/MPS), GroundingDINO, CoreML SAM2, MuJoCo+LIBERO | torch, lerobot, coremltools, mujoco |
| 3 | GPT-4o (Azure OpenAI), Qwen2.5-VL (Azure HF), CogACT (Azure GPU HTTP) | azure-ai-inference, openai SDK |
| 4 | MCP servers (FastMCP), Azure Foundry JSONL export, AdapterRegistry.from_foundry() | fastmcp, azure-ai-evaluation |

Phase 1 has zero ML dependencies. The entire evaluation pipeline architecture is testable with mock adapters before any model is connected.

---

## 14. Known Constraints and Architecture Implications

| Constraint | Implication |
|-----------|-------------|
| Cosmos-Reason-1 deprecated 2026-03-18 on Azure NIM | Do not add as a supported model. Cosmos-Reason2-2b is the replacement via local MLX. |
| SAM2: PyTorch MPS broken | `local_coreml_sam2.py` adapter uses `apple/coreml-sam2-large`. No PyTorch path. |
| OpenVLA int4: bitsandbytes no MPS | `local_openvla.py` uses MLX quantization, not bitsandbytes. |
| GR00T N1.6: weights not public | `azure_gpu_groot.py` adapter is a stub until weights release. Protocol compliance enforced but health_check returns False. |
| MCP binary limit: 1MB | All image encoding in MCP tool handlers must resize to <750KB JPEG. The evaluation path (FastAPI → adapters directly) is not subject to this limit. |
| VLM stateless per-call | No persistent context across pipeline steps. The orchestrator explicitly passes SceneAnalysis and GraspPlan as context to subsequent VLM calls. Context accumulates as a dictionary passed through the pipeline. |

---

*Document version 1.0 — System Architect, 2026-02-18*
*Next review: after Phase 1 implementation complete*
