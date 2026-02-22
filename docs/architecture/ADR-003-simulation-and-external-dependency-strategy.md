# ADR-003: Simulation Environment and External Dependency Strategy

**Status**: Accepted
**Date**: 2026-02-22
**Authors**: System Architect, Principal Data Scientist, Product Manager
**Related Docs**:
- `docs/architecture/ADR-002-lerobot-plugin-architecture.md`
- `docs/architecture/high-level-architecture.md`
**Trigger**: Evaluation of LeRobot and NVIDIA Isaac Lab Arena as potential dependencies for ROVE's simulation and VLA inference layers.

---

## Context

ROVE is an **inference and evaluation** framework, not a training or fine-tuning tool. It orchestrates existing models via adapters to solve robotics manipulation tasks through a 4-stage pipeline (perceive, plan, act, verify). The core value is comparing model combinations, not building models.

Two external frameworks were evaluated for potential integration:

1. **HuggingFace LeRobot** (v0.4.x) — robotics library providing VLA model loading, inference, dataset management, and sim environment wrappers.
2. **NVIDIA Isaac Lab Arena** (v0.1.1) — GPU-accelerated photorealistic simulation for VLA policy evaluation, built on Isaac Sim and Omniverse.

The evaluation was scoped to ROVE's inference-only needs:
- Load a VLA checkpoint and call `predict_action()`
- Reset a sim environment, step actions, capture observations
- Serve scene images to VLMs for perception and verification

Training, RL, fine-tuning, data collection, and domain randomization are explicitly out of scope.

---

## Decision Drivers

- **ROVE is a model consumer, not a model producer.** The adapter pattern wraps inference APIs. ROVE should not own model weights, training loops, or environment curricula.
- **Azure AI Foundry is the cloud constraint.** Cloud VLM inference goes through Azure. External dependencies must not conflict with this.
- **Accessibility matters.** Target users include researchers on MacBooks, ML engineers with mixed hardware, and platform teams using cloud-only deployments. Hardware-gated dependencies reduce adoption.
- **Mock-first development.** Phase 1 runs entirely on mock adapters. Real model and sim adapters are Phase 2+.
- **Demo next week.** No destabilizing changes to the working pipeline.

---

## Decision 1: LeRobot as Optional Dependency (SmolVLA Only)

### Status: Accepted

### Analysis

LeRobot was evaluated for unified VLA inference across ROVE's target models:

| VLA in rove.yaml | LeRobot native? | Actual inference framework |
|------------------|----------------|---------------------------|
| pi0 / pi0-FAST | No | `openpi` (JAX-native) |
| OpenVLA-OFT 7B | No | HuggingFace `transformers` |
| SmolVLA 450M | **Yes** | LeRobot `PreTrainedPolicy` |
| CogACT 7B | No | CogACT repo (diffusion, CUDA) |
| Octo-Base 93M | No | `octo` repo (JAX) |

LeRobot natively supports **1 of 6 target VLAs**. It does not provide a unified inference API across ROVE's model roster. Each VLA requires a separate adapter regardless of LeRobot adoption.

**What LeRobot provides for SmolVLA:**
- `PreTrainedPolicy.from_pretrained(hub_id)` — model loading from HuggingFace Hub
- `policy.select_action(obs_dict)` — inference with built-in preprocessing
- Per-model image normalization and action denormalization from checkpoint stats

**What LeRobot provides that ROVE does NOT need:**
- Training loops, RL environments, data collection utilities
- `wandb` integration, `rerun-sdk` visualization
- `pynput` / `pyserial` hardware I/O libraries
- `lerobot-eval` CLI (assumes policy-in-loop Gymnasium — wrong abstraction for ROVE's 4-stage pipeline)

**Core dependency cost if adopted:** ~20 additional packages including `torch`, `torchvision`, `gymnasium`, `wandb`, `pynput`, `pyserial`. A cloud-only user (Azure VLMs, mock sim) would need to install hardware I/O libraries.

### Decision

LeRobot is an **optional dependency**, scoped to SmolVLA via an extras group:

```toml
[project.optional-dependencies]
smolvla = ["lerobot[smolvla]"]
```

A single `LeRobotPolicyAdapter` wraps LeRobot's `PreTrainedPolicy` interface inside ROVE's `PolicyAdapter` Protocol. Import is guarded with a graceful error:

```python
try:
    from lerobot.policies.pretrained import PreTrainedPolicy
    _LEROBOT_AVAILABLE = True
except ImportError:
    _LEROBOT_AVAILABLE = False

class LeRobotPolicyAdapter:
    def __init__(self, model_id, config):
        if not _LEROBOT_AVAILABLE:
            raise ImportError(
                f"LeRobot required for '{model_id}'. "
                "Install: pip install 'rove-eval[smolvla]'"
            )
```

### Phase 2 VLA Adapter Plan

Five separate adapter implementations, each with its own optional dependency group:

| Adapter file | Install extra | Models served | Framework |
|-------------|--------------|---------------|-----------|
| `lerobot_adapter.py` | `[smolvla]` | SmolVLA 450M | LeRobot |
| `openpi_adapter.py` | `[pi0]` | pi0, pi0-FAST | openpi (JAX) |
| `openvla_adapter.py` | `[local]` | OpenVLA-OFT 7B | transformers |
| `cogact_adapter.py` | `[cogact]` | CogACT 7B | diffusers (CUDA) |
| `octo_adapter.py` | `[octo]` | Octo-Base 93M | octo (JAX) |

This keeps `rove-eval` installable with zero ML dependencies for cloud-only users.

---

## Decision 2: MuJoCo + LIBERO as Primary Sim, Isaac Lab Arena as Future Optional Adapter

### Status: Accepted

### Analysis

| Factor | MuJoCo + LIBERO | Isaac Lab Arena |
|--------|----------------|-----------------|
| ROVE's sim needs | reset/step/observe — sufficient | reset/step/observe — works but massive overhead |
| Install | `pip install mujoco` | Isaac Sim (~GB), CUDA driver, Omniverse runtime |
| Hardware | CPU, MPS, CUDA — any machine | RTX 4080 minimum. No A100/H100. No macOS. |
| Maturity | Stable, battle-tested | Pre-alpha (v0.1.1) |
| Rendering | Basic OpenGL / offscreen | Photorealistic RTX ray tracing |
| Speed (single env) | ~20x faster per instance | Slower per instance, scales via GPU parallelism |
| LIBERO tasks | Native (130 tasks) | Ported via Lightwheel (250+ tasks) |
| VLA ecosystem | Manual integration | Native LeRobot + HuggingFace integration |
| Relevant for training | Not primary use | Designed for it (not our use case) |

**Isaac Lab Arena's value proposition for ROVE:**
- Photorealistic rendering gives VLMs better scene images — closer to real robot camera input
- Lower sim-to-real gap for perception evaluation
- GR00T N model benchmarking requires Arena

**Isaac Lab Arena's blockers for ROVE (now):**
- Pre-alpha maturity (v0.1.1) — risk of breakage under demo deadline
- RTX GPU requirement excludes macOS development and standard cloud GPU instances (A100/H100 not supported)
- Multi-GB install with known dependency conflicts (LeRobot `packaging>=24.2` vs isaacsim-core `packaging==23.0`)
- GPU parallelism (Arena's strength) is irrelevant for ROVE's evaluation model — ROVE runs N model combinations sequentially per task, not thousands of parallel rollouts

### Decision

**MuJoCo + LIBERO remains the primary `SimAdapter` for Phase 1-3.**

Isaac Lab Arena is added as a **second optional `SimAdapter`** in Phase 4, when:
- Arena reaches stable release (post-v0.1)
- ROVE has a CUDA-equipped CI environment
- Photorealistic perception evaluation becomes a priority

The `SimAdapter` Protocol already supports this without architecture changes:

```yaml
# rove.yaml — Phase 4 addition
sim:
  mujoco-libero:
    adapter: local_mujoco
    display_name: "MuJoCo + LIBERO"
    config:
      task_suite: libero_spatial
      render_size: [640, 480]
    notes: "Default sim. CPU/MPS compatible."

  isaac-arena-libero:
    adapter: local_isaac_arena
    display_name: "Isaac Lab Arena + LIBERO"
    config:
      task_suite: lightwheel_libero_tasks
      render_size: [1280, 720]
      device: cuda
    notes: "High-fidelity photorealistic sim. Requires NVIDIA RTX GPU + Isaac Sim 5.x."

  mock-sim:
    adapter: mock_sim
    display_name: "Mock Sim"
```

LIBERO tasks overlap between both sims, enabling cross-validation of results.

---

## Decision 3: No CUDA Requirement for Core ROVE

### Status: Accepted

ROVE's core package (`pip install rove-eval`) must remain installable and functional on:
- macOS (Apple Silicon / MPS)
- Linux (CPU-only, CUDA optional)
- Cloud VMs (no GPU, Azure VLMs only)

CUDA is required only for specific optional adapters:
- `cogact-7b` (diffusion inference, CUDA-only)
- `isaac-arena-libero` (Isaac Sim, RTX GPU)
- Any future adapter that genuinely requires it

This is enforced by the existing pattern: adapters declare `device: cuda` in `rove.yaml` config, and the registry marks them `available: false` when CUDA is not present.

---

## Consequences

### Positive
- Cloud-only users can run full pipeline evaluations with zero ML dependencies (Azure VLMs + mock sim)
- Researchers on MacBooks can evaluate with MuJoCo without GPU
- Each VLA gets purpose-built adapter code instead of forcing everything through one framework's abstractions
- Isaac Lab Arena is a clean upgrade path — one YAML entry + one adapter file, no architecture changes

### Negative
- Five separate VLA adapter implementations means more maintenance (mitigated: each is ~100-200 lines)
- SmolVLA users must install LeRobot's full dependency tree including unused packages (wandb, pynput)
- No photorealistic perception evaluation until Phase 4

### Risks
- LeRobot API may change (mitigated: `PreTrainedPolicy.from_pretrained()` and `select_action()` are stable core API)
- Isaac Lab Arena may not reach stable release on expected timeline (mitigated: MuJoCo works indefinitely)
- `openpi` (JAX) on Apple Silicon may have compatibility issues (mitigated: `jax[metal]` is officially supported)

---

## References

- [LeRobot Documentation](https://huggingface.co/docs/lerobot/index) — HuggingFace robotics library
- [LeRobot GitHub](https://github.com/huggingface/lerobot) — source, supported policies, install
- [Isaac Lab Arena (NVIDIA Developer)](https://developer.nvidia.com/isaac/lab-arena) — product page
- [Isaac Lab Arena GitHub](https://github.com/isaac-sim/IsaacLab-Arena) — pre-alpha source
- [Generalist Robot Policy Evaluation with Isaac Lab-Arena and LeRobot](https://huggingface.co/blog/nvidia/generalist-robotpolicy-eval-isaaclab-arena-lerobot) — joint blog post
- [Isaac Sim 5.1 Requirements](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/requirements.html) — hardware requirements (RTX 4080 min)
- [Isaac Sim vs MuJoCo comparison](https://pub.towardsai.net/isaac-sim-vs-mujoco-the-4-000-question-that-will-define-robotics-in-2025-4c41a2984c2c) — speed and feature comparison
