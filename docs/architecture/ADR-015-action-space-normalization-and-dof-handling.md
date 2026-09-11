# ADR-015: Action Space Normalization and DOF Handling

**Status:** Accepted (Implemented)
**Date:** 2026-03-02
**Related:** ADR-014 (Two-Phase Pipeline), ADR-010 (Pipeline Generalization), ADR-016 (Robot Embodiment Contract — implementation companion)

## Context

VLA models output actions in different dimensionalities and coordinate frames. When ROVE evaluates VLA predictions against ground truth (e.g., LIBERO data with `action_dim=7`, `state_dim=8`), it must correctly interpret what each dimension means and handle mismatches between model outputs and dataset expectations.

Key issues discovered during data analysis:

1. **state_dim vs action_dim mismatch.** LIBERO's Panda data uses `state_dim=8` (7 joint angles + 1 gripper width in joint space) but `action_dim=7` (6 EEF deltas + 1 gripper command in Cartesian space). These are different coordinate frames — proprioception is joint-space, actions are Cartesian-space.

2. **DOF counting ambiguity.** The Panda URDF (`data/urdf/panda/panda.urdf`) contains 9 non-fixed joints (7 revolute + 2 prismatic fingers), but only 8 are independent — `panda_finger_joint2` has a `<mimic>` tag coupling it to `panda_finger_joint1`. Naive joint counting yields 9; correct independent DOF is 8 (7 arm + 1 gripper). An LLM asked to parse the URDF will likely report 9 DOF unless explicitly instructed to check for mimic joints.

3. **VLA output dimension varies by model:**

   | Model | Output Dim | Format | Padding |
   |-------|-----------|--------|---------|
   | pi0.5 | 32 (fixed) | Zero-padded to max across training embodiments | Slice first N active dims |
   | pi0-FAST | Configurable | Per-dataset `action_dim` | None needed |
   | SmolVLA (base) | 6 | SO-100 pretrained | Retrain projection for other DOF |
   | SmolVLA (LIBERO) | 7 | Fine-tuned for Panda | Use `lerobot/smolvla_libero` checkpoint |
   | OpenVLA | 7 | Discrete tokens → continuous | Detokenize per model spec |
   | CogACT | 7 | Continuous (diffusion) | None |

4. **Silent truncation risk.** pi0.5 outputs 32 dimensions regardless of the target robot. If the adapter doesn't slice correctly, extra zero-padded dimensions either get silently sent to the robot or corrupt evaluation metrics. This was confirmed as a known issue in [openpi#580](https://github.com/Physical-Intelligence/openpi/issues/580).

## Decision

### 1. Canonical Action Representation

All VLA adapters normalize their output to a canonical `ActionPrediction` with explicit metadata:

- `action: list[float]` — the raw action vector, length = `action_dim` of the target robot
- `action_dim: int` — declared dimensionality (must match `len(action)`)
- `action_space: "cartesian" | "joint"` — coordinate frame
- `gripper_index: int | None` — which dimension is gripper command (typically last)

Adapters are responsible for slicing/padding their model's raw output down to the target robot's `action_dim` before returning.

### 2. DOF Metadata from URDF

When a URDF is available, the pipeline extracts DOF metadata at initialization:

- Count non-fixed joints, excluding `<mimic>` joints
- Report `independent_dof` (not raw joint count) in evaluation provenance
- Validate that `state_dim` in the dataset matches `independent_dof` from the URDF

This prevents the 9-vs-8 DOF confusion for Panda and similar robots with coupled joints.

### 3. Per-Model Slicing Rules

Each VLA adapter declares its raw output dimensionality and the slicing logic to extract the target action:

- **pi0.5**: Always outputs 32-dim. Adapter slices `[:action_dim]` based on target robot config.
- **pi0-FAST**: Outputs match configured `action_dim`. No slicing needed.
- **SmolVLA**: Output matches the checkpoint's trained `action_dim`. Adapter validates checkpoint matches target robot.
- **OpenVLA/CogACT**: Output matches target `action_dim` natively.

### 4. Evaluation Metric Safety

When computing action error metrics (L2, cosine similarity) between predicted and ground truth:

- Assert `len(predicted) == len(ground_truth)` before computation — never silently truncate or pad
- Log a warning if `action_space` differs between prediction and ground truth (e.g., joint-space prediction vs Cartesian-space ground truth)
- Record `action_dim` and `action_space` in evaluation provenance for reproducibility

## Consequences

### Positive

- Eliminates silent dimension mismatches that corrupt evaluation metrics
- Makes DOF handling explicit and auditable in provenance records
- Adapters own their model-specific output quirks; the pipeline sees a uniform interface
- URDF-derived DOF validation catches misconfigured datasets early

### Negative

- Every VLA adapter must implement slicing logic, adding per-adapter complexity
- URDF parsing adds a dependency on XML processing at initialization time
- Models trained in joint space vs Cartesian space cannot be directly compared without an IK/FK conversion step (out of scope for Phase 2)

### Risks

- New VLA models may use novel action representations (e.g., residual actions, multi-step chunks) that don't fit the `list[float]` canonical form. The `ActionPrediction` dataclass may need extension.
- pi0.5's 32-dim cap may be insufficient for high-DOF humanoids (e.g., GR00T N1 with 52 joints). This is not a ROVE limitation but a model limitation to document.
