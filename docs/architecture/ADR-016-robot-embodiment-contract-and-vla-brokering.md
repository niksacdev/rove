# ADR-016: Robot Embodiment Contract and VLA Input/Output Brokering

**Status:** Accepted
**Date:** 2026-03-02
**Related:** ADR-015 (Action Space Normalization), ADR-010 (Pipeline Generalization), ADR-002 (LeRobot Plugin Architecture)

## Context

ADR-015 established that VLA models have different output dimensions and coordinate frames, and that adapters must normalize their output. But it didn't specify the mechanism for how the pipeline and adapters coordinate. The prior implementation had two problems:

1. **Robot metadata existed but never reached the VLA.** The pipeline extracted robot info from URDF (`_extract_urdf_robot_info()`) and manifest metadata (`task_metadata`), then formatted it as text for the verifier. The VLA adapter — which actually needs to know the target robot to produce correct actions — received none of it.

2. **VLA models don't accept robot descriptors at inference time.** Passing robot metadata to `predict_action()` wouldn't help most models because their output dimension is locked at the checkpoint level. Research across target models confirmed:
   - **Pi0.5**: Always outputs 32-dim. Requires post-hoc slicing.
   - **SmolVLA**: Output dim locked at fine-tune time (6 for SO-100, 7 for Panda).
   - **OpenVLA/CogACT**: Fixed 7-dim baked into architecture.
   - **GR00T N1**: Takes `EmbodimentTag` at **init**, not per-call.

So the question isn't "how do we pass robot info to VLAs" but "how does the pipeline know whether a given VLA is compatible with a given task's robot, and how does the adapter know what to slice to."

## Decision

### Principle: The Pipeline Brokers, Adapters Transform

The pipeline knows the task's robot (from manifest/URDF) and the adapter's capabilities (self-declared). It validates compatibility before inference starts. The adapter owns all model-specific preprocessing — normalization, slicing, padding. `ActionPrediction` carries provenance so metrics know what coordinate frame they're in.

### 1. `RobotEmbodiment` — Typed Robot Descriptor

A Pydantic model built from existing URDF + manifest data. Not a full URDF parser — only the metadata needed to broker VLA inference.

```python
class RobotEmbodiment(BaseModel):
    robot_type: str          # "panda", "ur5e", "so100"
    arm_dof: int             # independent revolute/prismatic joints
    gripper_dof: int = 1     # 0 for suction/fixed-tool
    action_space: ActionSpace = ActionSpace.EEF_DELTA
    proprioception_space: ActionSpace = ActionSpace.JOINT_POSITION
    gripper_index: int | None = None
    state_dim_override: int | None = None
    urdf_path: str | None = None
    embodiment_tag: str | None = None  # for GR00T-style models

    @property
    def action_dim(self) -> int: return self.arm_dof + self.gripper_dof

    @property
    def state_dim(self) -> int: return self.state_dim_override or self.action_dim
```

Stored on `PipelineContext.robot_embodiment`. Built by `_build_robot_embodiment()` which reads both the legacy flat manifest format (`robot`, `action_dim`, `state_dim`, `control_space`) and the new nested `robot_descriptor` format.

### 2. `VLACapabilities` — Adapter Self-Declaration

Each VLA adapter declares what its checkpoint can do:

```python
class VLACapabilities(BaseModel):
    native_action_dim: int              # raw output dim
    native_action_space: ActionSpace = ActionSpace.EEF_DELTA
    supported_robot_types: list[str] | None = None  # None = cross-embodiment
    max_action_dim: int | None = None   # for padded models (pi0.5 = 32)
    requires_embodiment_tag: bool = False
```

Added as a required property on `VLAAdapter` protocol.

### 3. Config-Time Validation

`_validate_embodiment_compat()` runs at `_prepare_context()` time — before any inference call. It enforces three hard errors and one soft warning:

| Check | Severity | Basis |
|-------|----------|-------|
| Checkpoint-bound model on wrong robot | **Error** | Model literally outputs wrong number of dimensions. SmolVLA-base (trained on SO-100, 6-dim) on Panda (7-dim) produces structurally invalid actions. |
| Target action_dim exceeds model's max | **Error** | Pi0.5 outputs 32-dim max. If robot needs >32 dims, there aren't enough values to slice. Physically impossible to recover. |
| Embodiment tag required but missing | **Error** | GR00T N1 requires `EmbodimentTag` at init per NVIDIA's API. Without it, model can't be initialized. |
| Action space mismatch | **Warning** | Running joint-delta VLA on EEF-delta task is a valid research config. Recorded in provenance for metric honesty. |

Errors raise `ValueError`. There is no custom exception hierarchy yet. These are configuration errors — the user picked an incompatible VLA+task combination in `rove.yaml`. The fix is to change the config, not handle the exception.

### 4. `ActionPrediction` Provenance

Adapters set provenance fields on every prediction:

- `declared_action_dim` — after slicing (what metrics should validate against)
- `raw_action_dim` — before slicing (32 for pi0.5, useful for debugging)
- `action_space` — coordinate frame the actions are in
- `gripper_index` — which dimension is gripper

This makes metric computation auditable. Ground truth comparison can assert `declared_action_dim == len(ground_truth_action)` before computing L2 error.

### 5. `embodiment` Parameter on `predict_action()`

The `VLAAdapter.predict_action()` signature gains `embodiment: RobotEmbodiment | None = None`. This is intentionally optional — models that don't need it ignore it. Models that do (pi0.5 for slicing target dim, GR00T for tag lookup) can use it. This is better than multiple separate parameters and is extensible without protocol changes.

### 6. `ActionNormalizer` Utility

Stateless helpers in `src/rove/adapters/normalizer.py`:

- `slice_to_target_dim(raw_actions, target_dim)` — pi0.5's 32→N
- `normalize_proprioception(state, mean, std)` — z-score
- `denormalize_actions(actions, mean, std)` — reverse z-score

Adapters call these explicitly. The pipeline never touches action values.

### 7. Manifest `robot_descriptor` Format

LIBERO entries gain a nested `robot_descriptor` block alongside legacy flat fields:

```json
{
  "robot_descriptor": {
    "robot_type": "panda",
    "arm_dof": 6,
    "gripper_dof": 1,
    "action_space": "eef_delta",
    "proprioception_space": "joint_position",
    "gripper_index": 6
  },
  "robot": "panda",
  "action_dim": 7,
  ...
}
```

`_build_robot_embodiment()` reads nested format first, falls back to flat. Legacy fields kept for backward compat.

## What This Does NOT Do

- **No FK/IK conversion** — comparing joint-space vs EEF-delta VLAs requires FK (Phase 3). We flag the mismatch in provenance.
- **No auto-checkpoint selection** — ROVE doesn't pick `smolvla_libero` vs `smolvla_base`. The user configures it. ROVE validates the choice.
- **No norm_stats loading** — that's adapter-specific. The normalizer provides math; adapters provide data.
- **No custom exception hierarchy** — `ValueError` is sufficient for config errors in Phase 1. May add `rove.errors.ConfigError` later.

## Consequences

### Positive

- Fail-fast validation catches misconfigured VLA+task combinations before wasting GPU time on inference
- Typed `RobotEmbodiment` replaces ad-hoc dict assembly — single source of truth for robot metadata
- Provenance fields on `ActionPrediction` make metric computation auditable
- Adapters own slicing/normalization — pipeline stays model-agnostic
- New `robot_descriptor` manifest format maps directly to `RobotEmbodiment` — no parsing logic

### Negative

- Every VLA adapter must implement `capabilities` property — adds per-adapter boilerplate
- `VLAAdapter` protocol now has a required `capabilities` attribute — existing third-party adapters (if any) would break
- Two manifest formats to support (nested + flat) until migration is complete

### Risks

- `VLACapabilities` is declared statically but some properties (like `native_action_dim`) depend on which checkpoint is loaded. LeRobot adapter handles this by reading from policy config post-load, but pre-load it returns defaults. Config-time validation runs pre-load for real adapters, potentially using stale defaults. Mitigation: validate again at first `predict_action()` call if capabilities changed after load.

### Amendment: YAML-Driven Capabilities (2026-03-02)

**Problem:** The original design required each VLA adapter to hardcode a `capabilities` property. Adding a new VLA model required Python code changes just to declare static facts like "I output 7 dims in EEF-delta space" — information that belongs in configuration, not code.

**Change:** `VLACapabilities` are now constructed from `rove.yaml` config, not hardcoded in adapter classes.

Each VLA endpoint in `rove.yaml` gains a `vla_capabilities` block:

```yaml
smolvla-450m:
  type: vla
  adapter: local_lerobot
  config:
    model_id: "lerobot/smolvla_base"
    vla_capabilities:
      native_action_dim: 6
      native_action_space: eef_delta
      supported_robot_types: ["so100"]
```

Adapters read this from their `config` dict at `__init__`:

```python
caps_data = (config or {}).get("vla_capabilities", {})
self._capabilities = VLACapabilities(**caps_data) if caps_data else VLACapabilities(native_action_dim=7)
```

**Refinement post-load:** For adapters like `LeRobotVLAAdapter`, the YAML value is the pre-load default. After the policy loads, `_refine_capabilities()` reads the actual checkpoint config (e.g., `output_features["action"].shape[0]`) and updates `_capabilities`. This handles the pre-load vs post-load gap without requiring two validation passes.

**Fallback:** If `vla_capabilities` is missing from config, adapters fall back to `VLACapabilities(native_action_dim=7)`. The pipeline's `_prepare_context()` already uses `getattr(adapter, "capabilities", None)` defensively, so third-party adapters without capabilities still work — they just skip config-time validation.

**Effect on negative consequences:** "Every VLA adapter must implement `capabilities` property — adds per-adapter boilerplate" is now mitigated. Adding a new VLA model with a known adapter type requires only a YAML entry. The adapter reads capabilities from config automatically.
