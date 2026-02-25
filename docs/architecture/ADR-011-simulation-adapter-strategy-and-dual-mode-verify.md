# ADR-011: Verification Strategy — From Action Plausibility to Physics Simulation

**Status**: Proposed
**Date**: 2026-02-24
**Authors**: System Architect, Principal Data Scientist, Product Manager
**Supersedes**: Earlier draft of ADR-011 (simulation-only framing)
**Related Docs**:
- `docs/architecture/ADR-003-simulation-and-external-dependency-strategy.md`
- `docs/architecture/ADR-010-pipeline-generalization-agent-adapter.md`
- `src/rove/adapters/protocols.py`

**Trigger**: The verify stage needs meaningful signals about VLA action quality. Without simulation, the VLM verifier cannot interpret raw action arrays (e.g., `[0.02, -0.01, 0.05, 0.0, 0.1, -0.02, 0.8]`). This ADR defines a layered verification strategy — from zero-infrastructure reasoning checks through forward kinematics to full physics simulation — with honest assessment of what each layer requires from the customer and what it delivers.

**Code alignment note**: This ADR was updated on 2026-02-24 to reflect code already implemented in commits `83714e5` (LLM-as-judge verify redesign), `f05881a` (pi0.5 VLA + verify improvements), `32f7b1d` (robotics domain prompts), and `3ea2d61` (3D position estimates). Layers 0 and 1 are partially implemented; Layers 2 and 3 are proposed.

---

## Context

ROVE's 4-stage pipeline (perceive → plan → act → verify) produces an `ActionPrediction` at the act stage. The verify stage is an LLM-as-judge that evaluates whether the pipeline succeeded.

### Current Code State

Several building blocks for layered verification are already implemented:

- **`ActionPlausibility` model** (`src/rove/models/core.py`): `bounds_check`, `smoothness`, `gripper_consistency`, `plan_alignment`, `reasoning`. Already a field on `VerificationResult.action_plausibility`.
- **`ExampleData` model** (`src/rove/models/core.py`): Carries `ground_truth` (eval QA) and `extras` (proprioception, robot type) through the pipeline. The orchestrator seeds `PipelineContext.proprioception` from `example.extras["proprioception"]`.
- **`GroundTruthCheck` model** (`src/rove/models/core.py`): Structured ground truth QA evaluation (question, choices, correct_answer, pipeline_answer, correct, confidence).
- **`PipelineContext.to_dict()`**: Already extracts gripper events (`_extract_gripper_events()`) from VLA trajectories, truncates long action arrays, and summarizes gripper open/close transitions. This is a Layer 1 implementation.
- **`SceneAnalysis`**: Objects now support optional `position: [x,y,z]` (meters) and `environment_distribution` field for domain context. 3D positions are relevant for FK comparison (Layer 2).
- **`TaskPlan`**: Includes `task_repertoire` (domain capabilities), `artifacts` (success markers), and `degradation_profile` (domain challenges) — robotics domain knowledge that enriches verification reasoning.
- **Verify prompt** (`src/rove/prompts/verify_user.txt`): Already instructs the LLM judge to assess action plausibility (bounds, smoothness, gripper consistency, plan alignment) and states the sim/after-image limitation.
- **`GenericVLMAdapter` + provider pattern** (`src/rove/adapters/registry.py`): VLMs can be backed by any provider (Azure Foundry, LM Studio, etc.) via `_build_vlm_provider_adapter()`.
- **`local_lerobot` adapter** registered for VLA inference, `azure_foundry_agent` registered for agent workflows.

### The Action Array Problem

The VLA outputs a trajectory of 7-DOF end-effector deltas:

```
[[0.02, -0.01, 0.05, 0.0, 0.1, -0.02, 0.8],
 [0.03, -0.02, 0.04, 0.0, 0.1, -0.01, 0.9], ...]
```

**No VLM can interpret this.** These numbers are meaningless without knowing the robot's kinematics and workspace. The VLM verifier today hand-waves about action quality because it has nothing spatial to reason about.

**No VLA can self-assess.** VLAs are forward-pass neural networks. They predict actions; they cannot judge whether those actions are correct.

**No LLM can judge raw arrays.** An LLM sees floating-point numbers with no physical grounding.

### The Simulation Constraint

**No physics simulator can take an arbitrary real-world image and simulate actions on it.** All simulators (MuJoCo, Isaac Sim, SAPIEN, Genesis) require pre-defined 3D environments in MJCF, URDF, or USD formats. Reconstructing a physics-compatible 3D scene from a single RGB image is an unsolved problem.

### The Missing Middle: Forward Kinematics

Between "VLM stares at numbers" and "full physics simulation" there is a practical middle ground: **forward kinematics (FK)**. FK only needs a robot URDF (no scene, no objects, no sim) and converts joint angles to end-effector positions in 3D space. This gives the verifier spatial information it can actually reason about.

### Evaluation Data in ROVE

ROVE's manifest (`data/manifest.json`) contains two categories:

**Robo2VLM images (001-020):** Static photos from real robots (Google Robot, Franka Panda via DROID, KUKA). No proprioception, no ground truth actions. Used for perceive and plan evaluation only.

**LIBERO images (000-009):** Rendered frames from LIBERO benchmark tasks. Include:
- `robot: "panda"` — identifies the robot
- `proprioception: [...]` — current 8-DOF joint state
- `ground_truth_action: [...]` — expert human's action at this frame
- `action_dim: 7`, `state_dim: 8` — action space metadata

The LIBERO entries have everything needed for FK verification. The Franka Panda URDF is included at `data/urdf/panda/panda.urdf` with collision and visual meshes.

---

## Decision Drivers

- **Verification must be layered.** Each layer adds value but asks more from the customer. Don't force full sim on users who just need action plausibility checks.
- **FK is the highest-value, lowest-cost improvement.** It needs only a URDF (every robotics team has one), runs in <10ms, requires no GPU, and gives the verifier real spatial information.
- **LIBERO is internal tooling, not a customer feature.** Its 130 pre-built scenes don't match customer environments. It's useful for ROVE's own VLA validation and reproducing published benchmarks.
- **Isaac Lab is the customer-facing sim.** Photorealistic RTX rendering produces images the VLM verifier can meaningfully judge. Customers upload their URDF; Isaac Lab renders their robot in a scene.
- **Gazebo is not the right cloud sim.** Isaac Sim's RTX rendering produces images that match real robot camera output. Gazebo's OGRE2 renderer does not. For a VLM verifier, image quality directly affects verification accuracy.
- **Foundry agents with sim tools are a strategy to evaluate, not infrastructure to build.** An agent-driven sim loop is one evaluation mode ROVE compares against the deterministic pipeline.

---

## Decision 1: Four Verification Layers

### Status: Proposed

Verification is layered by what the customer provides. Each layer adds value without requiring the layers above it.

### Layer 0 — LLM-as-Judge (no URDF, no sim) — **IMPLEMENTED**

**Customer provides:** Images + task descriptions. Optionally, ground truth QA labels.

**What it verifies:** Perceive quality, plan coherence, pipeline reasoning.

**What it cannot verify:** Whether the VLA's predicted actions would physically succeed.

Two sub-modes:

**Mode A — Reasoning only (no ground truth).** The VLM reviews pipeline outputs and reasons about plausibility. Evaluates pipeline coherence. The verify prompt (`src/rove/prompts/verify_user.txt`) instructs the LLM to produce per-stage `StageCheck` assessments and explicitly states the sim/after-image limitation.

**Mode B — Ground truth QA (labeled datasets).** The VLM answers ground truth questions using pipeline outputs as evidence via `GroundTruthCheck` (question, choices, correct_answer, pipeline_answer). `ExampleData.ground_truth` carries eval QA from the manifest; the orchestrator passes it through `PipelineContext.ground_truth` to the verify adapter.

### Layer 1 — Action Sanity Checks (no URDF, no sim) — **PARTIALLY IMPLEMENTED**

**Customer provides:** Nothing beyond what Layer 0 needs.

**What it verifies:** Basic physical plausibility of the action array.

**Already implemented:**

- **`ActionPlausibility` model** (`src/rove/models/core.py`): `bounds_check`, `smoothness`, `gripper_consistency`, `plan_alignment` (0-1), `reasoning`. Already a field on `VerificationResult`.
- **Gripper event extraction** (`PipelineContext.to_dict()` → `_extract_gripper_events()`): Scans the last DOF for open/close transitions and provides a compact event list to the verifier (e.g., `{"step": 5, "action": "close", "grip_value": 0.1}`).
- **Action truncation**: Long trajectories (>10 steps) are summarized as first 5 + last 5 steps with a note, keeping the verify prompt compact.
- **VLM-based assessment**: The verify prompt instructs the LLM to assess bounds, smoothness, gripper consistency, and plan alignment from the action data. This is a reasoning-based check, not deterministic.

**Still needed — deterministic code checks (no VLM):**

| Check | What It Catches | Status |
|-------|-----------------|--------|
| **Excessive deltas** | `abs(delta) > 5cm/step` → physically impossible movements | Not yet implemented |
| **Gripper consistency** | Task says "pick" but gripper never closes | VLM reasoning only (not deterministic) |
| **Trajectory smoothness** | Sudden large jumps between consecutive steps | VLM reasoning only (not deterministic) |
| **Action space bounds** | Values outside `[-1, 1]` normalized range | Not yet implemented |

The deterministic checks would run in microseconds and provide hard pass/fail signals before the VLM judge sees the data. Currently, these assessments rely on the VLM's reasoning, which is slower and less reliable for numerical checks.

### Layer 2 — Forward Kinematics Verification (URDF required, no sim)

**Customer provides:** Robot URDF.

**What it verifies:** Whether the trajectory reaches the right place, stays within joint limits, avoids self-collision, and aligns with the plan.

FK converts the VLA's joint-space actions into Cartesian end-effector positions. This gives the VLM verifier spatial context:

Instead of:
```
actions: [[0.02, -0.01, 0.05, 0.0, 0.1, -0.02, 0.8], ...]
```

The VLM verifier sees:
```
FK Analysis:
- Trajectory endpoint: [0.34, 0.21, 0.46] (1cm from target bin B)
- Grasp point: matches perceived object location within 2cm
- Lift clearance: 13cm above bin wall (sufficient)
- Joint limits: all within bounds
- Self-collision: none detected
- Smoothness: continuous (no discontinuities)
- Plan alignment: trajectory matches 5/5 planned phases
  (approach ✓, descend ✓, grasp ✓, lift ✓, transfer ✓)
```

**FK also enables quantitative VLA comparison** when ground truth actions are available (LIBERO data):

```
SmolVLA endpoint:    [0.42, 0.15, 0.38]  →  1.7cm from ground truth  ← good
OpenVLA endpoint:    [0.50, 0.22, 0.41]  →  12.8cm from ground truth ← bad
```

This is a meaningful VLA ranking metric that requires no simulation.

**Real-world FK examples:**

- **Insufficient lift detection:** FK shows gripper at z=0.47 during transfer, but bin wall is 10cm high (z=0.55). The VLA didn't lift enough — bracket will collide with the wall.
- **Reachability failure:** FK shows joint 1 hits limit (2.87 rad) with end-effector still 9.4cm from target. The target is outside the robot's workspace.
- **Precision check:** FK shows 14mm placement error for a PCB assembly task with 2mm tolerance. The VLA is 7x outside tolerance.
- **Trajectory comparison:** SmolVLA produces smooth 10-step trajectory with max velocity 0.8 rad/s. OpenVLA produces 6-step trajectory with sharp corner at step 5 causing 12 rad/s² acceleration spike — unsafe for handling deformable objects.

**FK Library — MuJoCo:**

MuJoCo computes FK via `mj_forward()` — the same kinematics engine used for physics simulation, but without stepping physics. This means one dependency serves both FK verification (Phase 2) and LIBERO sim (Phase 2 internal).

| Criterion | MuJoCo | roboticstoolbox | pinocchio (`pin`) |
|-----------|--------|-----------------|-------------------|
| Install | `pip install mujoco` (~5MB) | `pip install roboticstoolbox-python` (~100MB) | `pip install pin` (~62MB) |
| Loads any URDF | Yes | Yes | Yes |
| FK API | `mj_forward(model, data)` | `panda.fkine(q)` | `pin.forwardKinematics(model, data, q)` |
| Self-collision | **Yes** (full collision engine) | Broken ([#428](https://github.com/petercorke/robotics-toolbox-python/issues/428)) | Yes (HPP-FCL) |
| Joint limits | From URDF (`model.jnt_range`) | `panda.qlim` | `model.lowerPositionLimit` |
| Apple Silicon | **Native arm64 wheel** | x86 only (Rosetta 2) | Native arm64 |
| Also does sim | **Yes** — same library for LIBERO Phase 2 | No | No |
| Built-in Panda | No (use `data/urdf/panda/panda.urdf`) | Yes | No |

**Decision:** Use MuJoCo for FK. It is the smallest install (5MB), has native arm64 support, includes real collision checking, and the same dependency carries forward to LIBERO sim. No additional FK library needed.

**FK with MuJoCo — any robot, any URDF:**

```python
import mujoco
import numpy as np

# Load any URDF — customer's UR5, Panda, KUKA, anything
model = mujoco.MjModel.from_xml_path("customer_robot.urdf")
data = mujoco.MjData(model)

# Set joint angles from VLA prediction
data.qpos[:7] = np.array(vla_predicted_joints)

# Compute FK (kinematics only, no physics step)
mujoco.mj_forward(model, data)

# End-effector position and orientation
ee_pos = data.site_xpos[0]        # [x, y, z]
ee_rot = data.site_xmat[0]        # 3x3 rotation matrix

# Joint limits from URDF
joint_limits = model.jnt_range     # [n_joints, 2] array of [min, max]
within_limits = np.all(
    (data.qpos >= joint_limits[:, 0]) &
    (data.qpos <= joint_limits[:, 1])
)
```

**URDF assets:** Franka Panda URDF included at `data/urdf/panda/panda.urdf` with collision meshes (STL) and visual meshes (DAE). Source: MoveIt `moveit_resources` (Apache 2.0). Customer robots use their own URDFs — MuJoCo loads any standard URDF.

### Layer 3 — Physics Simulation (URDF + scene description + sim)

**Customer provides:** Robot URDF + workspace scene description (MJCF, USD, or YAML).

**What it verifies:** Whether the actions physically succeed — object moves to target, collisions detected, ground truth success from deterministic checker.

This is the only layer that can answer: **"Did the bracket end up in bin B?"**

See [Decision 2](#decision-2-phased-simulation-integration) for simulator choices.

### Summary: What Each Layer Requires and Delivers

| Layer | Customer Provides | ROVE Delivers | Sim Needed? |
|-------|-------------------|---------------|-------------|
| **0: LLM Judge** | Photos + task text | Perceive/plan quality assessment | No |
| **1: Sanity Checks** | Nothing extra | "Actions are physically plausible" | No |
| **2: FK Analysis** | + Robot URDF | Trajectory in 3D space, joint limits, plan alignment, VLA comparison | No |
| **3: Physics Sim** | + Scene description | Ground truth success, before/after images, collision detection | Yes |

Each layer adds verification depth but asks more from the customer. Layers 0-2 run on any laptop. Layer 3 requires cloud GPU (Isaac Lab) or local MuJoCo.

---

## Decision 2: Phased Simulation Integration

### Status: Proposed

### Who needs what — honest assessment

| User | What They Need | Phase |
|------|---------------|-------|
| **Anyone with robot photos** | Perceive + plan evaluation | Phase 1 (today) |
| **Team with robot URDF** | FK verification of VLA actions | Phase 2 |
| **VLA developers** | Benchmark reproduction (LIBERO numbers) | Phase 2 (internal) |
| **Customer with URDF + workspace** | Full sim verification on their robot | Phase 3 (Isaac Lab) |
| **Customer at scale** | Parallel evaluation + adaptive agent verification | Phase 4 |

### Phase 1 — MockSim + LLM-as-Judge (current) — **IMPLEMENTED**

Works today. The verify stage is an LLM-as-judge (commit `83714e5`) that:
- Produces per-stage `StageCheck` assessments for each completed stage
- Evaluates ground truth QA via `GroundTruthCheck` when `ExampleData.ground_truth` is available
- Assesses `ActionPlausibility` (bounds, smoothness, gripper consistency, plan alignment) when the act stage completes
- Extracts gripper events from trajectories and truncates long action arrays for compact prompts
- Receives `TaskPlan.task_repertoire`, `artifacts`, and `degradation_profile` as domain context
- Uses `SceneAnalysis` 3D position estimates when available

The VLM verifier acknowledges in its prompt that true success assessment requires a simulator or post-execution image — it evaluates plausibility, not success.

### Phase 2 — FK Verification + LIBERO (local, no GPU)

**FK Verification (customer-facing):**

The primary Phase 2 deliverable. Customers provide their robot URDF. ROVE computes FK on VLA-predicted actions using MuJoCo's kinematics engine and provides the VLM verifier with spatial analysis.

Install (optional dependency — same package used for LIBERO sim):
```toml
[project.optional-dependencies]
kinematics = ["mujoco>=3.1"]
```

One dependency serves both FK verification and LIBERO sim. A customer who only wants FK doesn't need robosuite or LIBERO — just `mujoco`.

**LIBERO Benchmark (internal tooling):**

LIBERO is not a customer feature. It doesn't match customer environments. Its value is:
1. Validating ROVE's pipeline works end-to-end with a real sim
2. Reproducing published VLA benchmark numbers for ROVE's credibility
3. Providing ground truth actions for FK comparison (manifest entries `libero_000` through `libero_009`)

Install:
```toml
[project.optional-dependencies]
libero = ["mujoco>=3.1", "robosuite>=1.5", "libero>=0.1"]
```

### Phase 3 — Isaac Lab-Arena on Azure (customer-facing sim)

**Why Isaac Lab, not Gazebo:**

| Dimension | Isaac Lab | Gazebo |
|-----------|-----------|--------|
| Rendering | RTX ray-traced (photorealistic) | OGRE2 (not photorealistic) |
| VLM verify accuracy | High — images match real cameras | Low — sim-to-real visual gap |
| URDF loading | Native (no conversion) | Via URDF→SDF |
| API | Python-first (`SimulationApp`) | ROS2 topics (daemon required) |
| Pre-built tasks | 268 (Isaac Lab-Arena) | None equivalent |
| LeRobot integration | Native (EnvHub) | None |
| Docker | Official NGC container | Lightweight but no RTX |
| Cloud deployment | Official Azure guide + Isaac Automator | Manual |

The rendering quality is the deciding factor. The VLM verifier judges success from post-action images. Photorealistic images from Isaac Lab's RTX renderer produce more accurate VLM judgments than Gazebo's OpenGL output. A RoboCup 2024 study confirmed Gazebo overestimates vision task accuracy while Isaac Sim matches reality.

**Critical GPU constraint — A10, not A100:**

Isaac Sim requires RT Cores for RTX rendering. A100 and H100 are compute GPUs without RT Cores.

| Azure VM | GPU | Isaac Sim RTX? | Cost/hr | Spot |
|----------|-----|----------------|---------|------|
| **NVads A10 v5** | **A10** | **Yes** | **~$0.90** | **~$0.35** |
| NCasT4 v3 | T4 | Physics only (no RTX) | ~$0.53 | ~$0.24 |
| NC A100 v4 | A100 | **No** (no RT Cores) | ~$3.67 | N/A |
| ND H100 v5 | H100 | **No** (no RT Cores) | ~$98/hr | N/A |

**The NVads A10 v5 is the correct Azure VM for ROVE's Isaac Lab deployment.**

**Architecture — ROVE orchestrates, Isaac Lab executes:**

```
ROVE (orchestrator)                     Isaac Lab (GPU subprocess on Azure A10)
───────────────────                     ──────────────────────────────────────

Customer uploads URDF via ROVE API
  POST /api/robots + URDF file
  ROVE stores in blob storage
           │
           ▼
SimAdapter.reset(task)             ──→  Isaac Lab loads URDF + scene
                                        Renders initial image (RTX)
                                   ←──  SimObservation (image + proprioception)
           │
           ▼
perceive: VLM analyzes image from Isaac Lab
plan: VLM/LLM creates strategy
act: VLA predicts actions from Isaac Lab image + proprioception
           │
           ▼
SimAdapter.step(action)            ──→  Isaac Lab applies joint deltas (PhysX)
  (for each step in trajectory)         Renders post-step image (RTX)
                                        Checks success function
                                   ←──  SimObservation (image + success + done)
           │
           ▼
verify: VLM compares before/after
  (photorealistic images from Isaac Lab)
  Returns VerificationResult
```

The customer never interacts with Isaac Lab. They interact with ROVE's API and dashboard. Isaac Lab is invisible infrastructure behind the `SimAdapter` protocol.

**Subprocess architecture:** Isaac Sim runs its own Python interpreter (`kit`), not standard CPython. The adapter communicates via Unix socket (JSON protocol). Isaac Sim must run as a persistent warm process (10-30s startup), not cold-started per evaluation.

**Scene description:** Customers can describe workspaces in ROVE YAML instead of authoring USD files:

```yaml
scene:
  robot:
    urdf: "robots/ur5.urdf"
    position: [0, 0, 0]
  objects:
    - name: "table"
      type: primitive/box
      size: [0.8, 0.6, 0.02]
      position: [0.5, 0, 0.4]
    - name: "bin_A"
      type: primitive/box
      size: [0.2, 0.15, 0.1]
      position: [0.3, -0.2, 0.42]
    - name: "bracket"
      type: mesh
      file: "objects/bracket.obj"
      position: [0.4, 0.1, 0.45]
  camera:
    position: [1.0, 0, 1.2]
    target: [0.5, 0, 0.4]
```

ROVE translates this YAML into Isaac Lab API calls. Simple primitives cover most industrial scenes. Custom objects need a mesh file (.obj/.stl) from CAD.

### Phase 4 — Foundry Agent + Cloud Sim Verification

**Cloud-based verification of edge execution:**

```
┌─── Edge (Robot) ──────────────────────┐
│  VLM (Qwen3-VL) → perceive           │
│  LLM (Phi-4 Mini) → plan             │
│  VLA (SmolVLA) → predict actions      │
│  Send: plan + actions + scene image   │
└───────────────┬───────────────────────┘
                │ HTTPS
                ▼
┌─── Azure Cloud ───────────────────────┐
│  Foundry Agent (GPT-4o vision)        │
│    ├─→ MCP Server → Isaac Lab sim     │
│    │   (Azure Container App + A10 GPU)│
│    │   Has: customer URDF + scene     │
│    ├─→ Observes sim result            │
│    └─→ Diagnostic verdict:            │
│        "Grasp succeeded but place     │
│         overshot by 2cm — VLA's       │
│         z-axis prediction is high"    │
└───────────────────────────────────────┘
```

The Foundry agent adds value here because it's not just checking pass/fail — it's reasoning across multiple sim steps, diagnosing failure modes, and providing actionable feedback.

**Foundry agent constraints:**
- Tool outputs are text/JSON only — the agent cannot see images returned by tools. Post-action images must be sent as new message content blocks.
- Per-step latency: ~5-15 seconds (network round-trip + GPT-4o inference). A 10-step trajectory takes 50-150 seconds.
- Runs expire after 10 minutes.
- MCP server must be hosted at a public HTTPS endpoint (Azure Container Apps).
- GPT-4o or GPT-4.1 required (GPT-5 models don't support function calling).

**Foundry agent as a strategy to evaluate, not infrastructure to replace the pipeline:**

A Foundry agent with sim tools is one more strategy ROVE compares:

```
Strategy A: Qwen3-VL → Qwen3-VL → SmolVLA → Qwen3-VL      (local pipeline, ~8s)
Strategy B: Foundry GPT-4o Agent with sim tools               (cloud agent, ~60s)

ROVE answers: Does Strategy B's adaptive reasoning justify 7x latency and cost?
```

The answer is probably "no for simple pick-and-place, maybe for long-horizon assembly tasks." That's a valuable finding.

**Why not use Foundry agents for perceive/plan?** Using an agent for a single VLM call (perceive or plan) adds ~5-15 seconds of overhead for something a local Qwen3-VL does in 1-2 seconds for free. Agents earn their cost in multi-step reasoning with tool use, not single-stage inference.

---

## Decision 3: World Models Are Not Simulators

### Status: Proposed

Video prediction models (Cosmos-Predict2.5, UniSim) and world models (V-JEPA2) are **not appropriate as the primary verification mechanism**.

**Why not:**
- Generative models produce *plausible-looking* futures, not *physically accurate* ones
- A VLA that predicts a collision trajectory may get a "successful grasp" video because that's statistically likely in the training distribution
- ROVE needs ground truth correctness signals — generative models provide plausibility, not truth

**Where world models fit (Phase 4+):**
- Synthetic data generation for testing edge cases
- Cosmos Transfer can enhance Isaac Lab renders to improve photorealism
- A separate "world model quality" evaluation metric

---

## Decision 4: Dataset-Specific Verification Constraints

### Status: Proposed

FK verification only works when you know the robot. The manifest data determines what's possible:

| Manifest Entries | Robot Known? | Proprioception? | Ground Truth Action? | FK Possible? | Verification Path |
|-----------------|-------------|-----------------|---------------------|-------------|-------------------|
| `robo2vlm_001-020` | No metadata | No | No | **No** | Layer 0 only (LLM judge + QA) |
| `libero_000-009` | `panda` | Yes (8-DOF) | Yes (7-DOF) | **Yes** | Layer 0 + 1 + 2 |

**Robo2VLM images** come from diverse robots (Google Robot via Fractal, Franka Panda via DROID/VIOLA, KUKA via Stanford). The source dataset IDs contain clues (`fractal20220817_` = Google Robot, `droid_` = Panda, `stanford_kuka_` = KUKA IIWA) but the manifest lacks robot type, proprioception, and ground truth actions. These images are used for `perceive-plan` evaluation only.

**LIBERO images** include full robot metadata. FK verification can compare VLA predictions against ground truth actions for these entries today.

**For customer scenarios:** The customer knows their robot and provides the URDF. FK works immediately. No dataset metadata dependency.

---

## Model Changes — Current State and Remaining Work

### Already Implemented

**`ActionPlausibility`** — fully implemented in `src/rove/models/core.py`:

```python
class ActionPlausibility(BaseModel):
    bounds_check: bool = True
    smoothness: bool = True
    gripper_consistency: bool = True
    plan_alignment: float = 0.0    # 0-1
    reasoning: str = ""
```

**`VerificationResult`** — already includes action plausibility:

```python
class VerificationResult(BaseModel):
    success: bool
    confidence: float
    reasoning: str
    raw_response: str = ""
    completed_stages: list[str] = Field(default_factory=list)
    stage_checks: list[StageCheck] = Field(default_factory=list)
    ground_truth: GroundTruthCheck | None = None
    action_plausibility: ActionPlausibility | None = None
```

**`ExampleData`** — carries ground truth and proprioception through the pipeline:

```python
class ExampleData(BaseModel):
    ground_truth: dict | None = None   # eval_qa from manifest
    extras: dict = Field(default_factory=dict)  # proprioception, robot, etc.
```

**`PipelineContext.to_dict()`** — already extracts gripper events and truncates trajectories:

```python
# Gripper events: [{"step": 5, "action": "close", "grip_value": 0.1}, ...]
action_d["gripper_events"] = _extract_gripper_events(actions)
# Trajectory truncation: first 5 + last 5 steps for long trajectories
```

**`SceneAnalysis`** — objects now include optional 3D positions:

```python
objects: list[dict]  # [{..., "position": [x,y,z] (meters, optional), ...}]
environment_distribution: str = ""  # domain context
```

**`TaskPlan`** — includes robotics domain fields:

```python
task_repertoire: list[str]       # domain-specific capabilities required
artifacts: list[str]             # measurable success markers
degradation_profile: list[str]   # domain-specific challenges
```

### Still Required (Phase 2+)

**`SimObservation` — add deterministic success field:**

```python
class SimObservation(BaseModel):
    # ... existing fields ...
    task_success: bool | None = None  # deterministic checker result (LIBERO, Isaac Lab)
```

**`VerificationResult` — add sim and FK signals:**

```python
class VerificationResult(BaseModel):
    # ... existing fields ...
    sim_success: bool | None = None    # deterministic sim result (None if no sim)
    fk_analysis: dict | None = None    # FK spatial analysis (None if no URDF)
```

**`PipelineContext` — carry FK analysis:**

```python
class PipelineContext(BaseModel):
    # ... existing fields ...
    fk_analysis: dict | None = None    # populated after act stage if URDF available
```

---

## Decision 5: UI and Configuration Design for FK

**Date**: 2026-02-24

### UI: "+" Button with Popover

The bottom input bar replaces the single camera button with a "+" button that opens a popover with two upload options:

```
[+] [task input...........................] [send]
     ┌─────────────────┐
     │ 📷 Upload Image │
     │ 📦 Upload URDF  │
     └─────────────────┘
```

- **Upload Image** triggers the existing image file picker
- **Upload URDF** accepts `.urdf`, `.xml`, `.mjcf` files for FK computation
- URDF filename displayed as a preview chip below the input bar with a remove button
- Popover closes on click outside or after file selection

### URDF Preview

When a URDF is attached, a compact preview chip appears:

```
[📦 panda.urdf  ×]
```

### Configuration: `forward_kinematics` per Strategy

```yaml
strategies:
  scene_plan_action:
    perceive: qwen3-vl-8b
    plan: qwen3-vl-8b
    act: pi05-libero
    verify: qwen3-vl-8b
    sim: mock-sim
    forward_kinematics: true   # opt-in per strategy
```

- `forward_kinematics: bool = False` on `StrategyConfig` and `Strategy`
- Only meaningful for strategies with an `act` stage — silently ignored otherwise
- Requires a URDF upload at evaluation time — without URDF, FK silently skips

### FK Output in Act Stage

FK analysis appears as a styled sub-card within the act stage output:

```
[FK Analysis] MuJoCo Forward Kinematics
  Endpoint        [0.340, 0.210, 0.460]m
  Displacement    0.340m
  Joint limits    within bounds
  Self-collision  none
  Smoothness      0.95
  Max velocity    1.20 rad/s
```

### Pipeline Flow

```
perceive → plan → act ──┬──→ verify
                         │
                    FK compute
                   (if enabled +
                    URDF provided)
```

FK computation runs inline within the act stage, after sim stepping but before the act result is yielded. The FK analysis is:
1. Included in the act stage SSE output for dashboard rendering
2. Stored in `PipelineContext.fk_analysis` for the verifier
3. Injected into the verify prompt via `render_verify()` in `prompt_loader.py`

### FormData Change

`POST /api/evaluate` accepts an optional `urdf` file field:

```
Content-Type: multipart/form-data

image: <scene image>
task: "Pick the red bracket..."
strategy_ids: "scene_plan_action,mock"
urdf: <robot.urdf>              # NEW, optional
```

---

## Consequences

### Positive
- Four verification layers make ROVE useful at every level — from "I have photos" to "I have cloud sim"
- FK verification (Layer 2) is the highest-value, lowest-cost improvement — no sim, no GPU, runs in <10ms
- MuJoCo serves double duty: FK in Phase 2, LIBERO sim in Phase 2 internal — one dependency, not two
- Quantitative VLA comparison via FK endpoint distance is a novel metric
- Isaac Lab's photorealistic rendering produces accurate VLM judgments
- LIBERO clearly scoped as internal tooling, not a customer promise
- Foundry agent with sim tools is a strategy to evaluate, preserving ROVE's deterministic orchestration
- Customer never touches Isaac Lab, Gazebo, or ROS2 — ROVE abstracts all sim complexity

### Negative
- FK without scene knowledge cannot detect object collisions or environmental constraints
- Isaac Lab requires A10 GPU (not A100/H100), limiting Azure VM options
- Isaac Sim subprocess architecture adds engineering complexity (warm process, socket communication)
- Four verification layers = four code paths to maintain
- LIBERO doesn't serve customers directly — it's internal-only value

### Risks
- MuJoCo URDF loading has quirks with some non-standard URDFs (mitigated: URDF validation step, MuJoCo's error messages are descriptive)
- Isaac Lab-Arena is pre-alpha (v0.1) — API instability (mitigated: pin version, test against specific releases)
- Customer URDF quality varies — broken inertia tensors, duplicate link names (mitigated: URDF validation step before loading)
- A10 spot instance availability varies by region (mitigated: eastus2, westus2 most reliable)
- Isaac Sim subprocess may crash, requiring restart with 10-30s penalty (mitigated: process supervisor + health checks)

---

## Implementation Sequence

| Step | Scope | Status | Blocked By | Effort | Phase |
|------|-------|--------|------------|--------|-------|
| 0a. LLM-as-judge verify | Per-stage checks, ground truth QA, action plausibility reasoning | **Done** | — | — | 1 |
| 0b. Gripper event extraction | `_extract_gripper_events()` in `PipelineContext.to_dict()` | **Done** | — | — | 1 |
| 0c. ExampleData pipeline | Ground truth + proprioception seeding from manifest | **Done** | — | — | 1 |
| 1. Deterministic sanity checks | Hard pass/fail on bounds, smoothness, gripper (code, not VLM) | **TODO** | Nothing | Small | 2 |
| 2. FK verification adapter | Load URDF, compute FK via MuJoCo, generate spatial analysis | **Done** | Nothing | Medium | 2 |
| 2a. FK UI + config | "+" button, URDF upload, `forward_kinematics` strategy flag | **Done** | Step 2 | Small | 2 |
| 3. FK-aware verify prompts | VLM verifier receives FK analysis as context | **Done** | Step 2 | Small | 2 |
| 4. VLA comparison metric | FK endpoint distance vs ground truth (LIBERO data) | **TODO** | Step 2 | Small | 2 |
| 5. LIBERO sim adapter | MuJoCo + LIBERO for internal VLA benchmarking | **TODO** | Nothing | Medium | 2 |
| 6. `SimObservation.task_success` | Model change + pipeline wiring | **TODO** | Nothing | Small | 2 |
| 7. Isaac Lab adapter | Cloud sim with customer URDF + RTX render | **TODO** | Steps 1-6 | Large | 3 |
| 8. ROVE scene YAML → Isaac Lab | Translate YAML scene descriptions to Isaac Lab API | **TODO** | Step 7 | Medium | 3 |
| 9. Foundry agent + sim MCP | Agent-driven verification with Isaac Lab as tool | **TODO** | Steps 7-8 | Large | 4 |

Steps 0a-0c are **already in the codebase**. Steps 1-4 are the Phase 2 priority — deterministic sanity checks and FK verification. Step 5 runs in parallel as internal tooling. Steps 7-9 are Phase 3-4 cloud features.

---

## References

### Simulation
- [MuJoCo Python API](https://mujoco.readthedocs.io/en/stable/python.html) — physics engine, pip-installable, CPU/MPS/CUDA
- [LIBERO](https://lifelong-robot-learning.github.io/LIBERO/) — 130 manipulation tasks, ICLR 2023
- [Isaac Lab](https://isaac-sim.github.io/IsaacLab/) — GPU-accelerated sim framework
- [Isaac Lab-Arena](https://github.com/isaac-sim/IsaacLab-Arena) — 268 evaluation tasks, LeRobot integration
- [Isaac Sim NGC Container](https://catalog.ngc.nvidia.com/orgs/nvidia/containers/isaac-sim) — Docker deployment
- [Isaac Sim Azure Deployment](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/install_advanced_cloud_setup_azure.html) — official cloud guide
- [Isaac Sim vs Gazebo Vision Comparison](https://dl.acm.org/doi/10.1007/978-3-031-85859-8_29) — RoboCup 2024

### Forward Kinematics
- [MuJoCo Python API](https://mujoco.readthedocs.io/en/stable/python.html) — FK via `mj_forward()`, same library used for sim (~5MB, native arm64)
- [MuJoCo URDF loading](https://mujoco.readthedocs.io/en/stable/modeling.html#curdf) — loads any standard URDF
- [Franka Panda URDF](https://github.com/moveit/moveit_resources/tree/ros2/panda_description) — MoveIt resources (Apache 2.0)
- [Pinocchio](https://github.com/stack-of-tasks/pinocchio) — alternative FK/dynamics library if MuJoCo is insufficient (~4,500 stars)

### Sim Alternatives (evaluated, not selected for Phase 2-3)
- [SimplerEnv](https://github.com/simpler-env/SimplerEnv) — Real2Sim VLA evaluation, ICLR 2025 (Phase 3+ cloud option)
- [Genesis](https://github.com/Genesis-Embodied-AI/Genesis) — GPU-accelerated sim, v0.3 (MPS broken, monitoring only)
- [Cosmos World Foundation Models](https://developer.nvidia.com/cosmos) — video prediction (not suitable for verification)

### Foundry Agents
- [Azure AI Agents SDK](https://learn.microsoft.com/en-us/python/api/overview/azure/ai-agents-readme) — multimodal agents with tool calling
- [MCP in Foundry Agents](https://learn.microsoft.com/en-us/azure/ai-foundry/agents/how-to/tools-classic/model-context-protocol) — remote MCP tool integration
- [Foundry Agent Model Support](https://learn.microsoft.com/en-us/azure/ai-foundry/agents/concepts/model-region-support) — GPT-4o vision + function calling

### ROVE Source Files (Layer 0-1 Implementation)
- `src/rove/models/core.py` — `ActionPlausibility`, `VerificationResult`, `ExampleData`, `GroundTruthCheck`, `StageCheck`, `PipelineContext.to_dict()`, `_extract_gripper_events()`
- `src/rove/orchestrator/pipeline.py` — `EvaluationPipeline.run_trial()` with `ExampleData` integration, proprioception seeding, sim step loop
- `src/rove/prompts/verify_user.txt` — LLM-as-judge prompt with action plausibility assessment
- `src/rove/adapters/protocols.py` — `VLMAdapter`, `VLAAdapter`, `AgentAdapter`, `SimAdapter` protocols
- `src/rove/adapters/registry.py` — `GenericVLMAdapter` provider pattern, `local_lerobot` VLA adapter, `azure_foundry_agent` agent adapter
