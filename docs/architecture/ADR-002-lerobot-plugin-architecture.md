# ADR-002: ROVE as a LeRobot Plugin — Feasibility, Interface Bridging, and Dual-Mode Architecture

**Status**: Proposed
**Date**: 2026-02-21
**Authors**: System Architect
**Related Docs**:
- `docs/architecture/high-level-architecture.md`
- `docs/architecture/ADR-001-stage-graph-orchestrator-and-mcp-topology.md`
**Trigger**: Founder strategy revision — ROVE as `lerobot_policy_rove` plugin with standalone enterprise path retained

---

## Context

The founder has revised ROVE's product strategy around three pillars:

1. **LeRobot plugin** (`lerobot_policy_rove`): wrap VLM perception + grounding into LeRobot's policy interface so LeRobot handles VLA execution and ROVE adds the VLM composition layers on top.
2. **Azure enterprise path**: Foundry JSONL export, Azure AI model access (GPT-4o, Phi-4), cost tracking — none of which LeRobot provides.
3. **Composition evaluation**: "Is GPT-4o perception + SmolVLA action worth the extra latency vs pi0 alone?" Cross-model pipeline comparison is the differentiating feature.

This ADR evaluates whether the plugin approach is technically feasible, where the interfaces are incompatible, and how the codebase should be structured to support both modes without becoming two separate products.

---

## Decision Drivers

- **Interface reality**: LeRobot's `PreTrainedPolicy` is a strict `observation dict → action tensor` interface. ROVE's pipeline is `image bytes + task string → structured analysis chain → action prediction`. These are not the same abstraction.
- **Evaluation integrity** (from ADR-001): The orchestrator must stay deterministic. Any adapter used inside a LeRobot policy must not introduce non-determinism that contaminates the evaluation signal.
- **Dependency isolation**: The standalone mode must work without LeRobot installed (Phase 1 and Phase 2 local dev, cloud-only deployments). The plugin mode adds LeRobot as an optional dependency.
- **No code duplication**: The VLM adapter layer, Azure adapters, cost tracking, and evaluation store are shared between plugin and standalone modes. They must not be forked.
- **LeRobot researcher adoption**: If ROVE ships as a `lerobot_policy_rove` package, a LeRobot user installs it with `pip install lerobot-policy-rove` and passes it to LeRobot's eval harness. No FastAPI server, no dashboard, no YAML knowledge required.

---

## Technical Feasibility Analysis

### Question 1: Can ROVE's VLM pipeline fit into `PreTrainedPolicy`?

**LeRobot's `PreTrainedPolicy` interface (simplified)**:

```python
class PreTrainedPolicy(nn.Module):
    def select_action(self, batch: dict[str, Tensor]) -> Tensor:
        # batch["observation.images.top"]: shape (B, C, H, W)
        # batch["observation.state"]: shape (B, state_dim)   — proprioception
        # Returns: action tensor shape (B, action_dim) or (B, chunk_size, action_dim)
        ...

    def reset(self) -> None:
        # Reset any recurrent state between episodes
        ...
```

**ROVE's pipeline call**:

```python
async def run(task: str, image: bytes, vlm, grounding, vla, sim) -> CombinationResult:
    scene = await vlm.analyze_scene(image, task)
    loc   = await grounding.localize(image, scene.task_relevant_objects[0])
    plan  = await vlm.plan_grasp(image, task, scene, loc.bbox)
    obs   = await sim.reset(task_id)
    pred  = await vla.predict_action(obs.rgb_image, task, obs.joint_positions)
    ...
```

**The mismatch has three dimensions**:

**A. Sync vs async**: LeRobot calls `select_action` synchronously (it is a PyTorch forward pass in an evaluation loop). ROVE's VLM calls are `async` (Azure API calls, HTTP endpoints). These cannot be directly composed without either wrapping the async pipeline in `asyncio.run()` inside `select_action`, or making the LeRobot wrapper synchronous with an event loop. `asyncio.run()` inside a PyTorch module method works but is architecturally ugly and has known issues in Jupyter environments where an event loop is already running.

**Verdict**: Bridgeable but not elegant. The standard solution is `asyncio.get_event_loop().run_until_complete()` with a guarded event loop reference, or converting the ROVE pipeline to sync for the LeRobot path. The cleaner solution is to maintain a synchronous wrapper `RoveCompositionPolicy.select_action()` that internally calls `asyncio.run(self._async_pipeline(...))`.

**B. Input format — observation dict vs image bytes**: LeRobot passes a Tensor dict. ROVE's VLM adapters take `bytes`. The bridge must decode `batch["observation.images.top"]` (a `(B, C, H, W)` normalized float tensor) back to JPEG bytes for the VLM API call, and decode `batch["observation.state"]` to a Python list for proprioception. This is lossy (re-encoding float tensors to JPEG introduces compression artifacts) and architecturally wrong (the pipeline works around LeRobot's data format rather than with it).

**Better approach**: Accept the observation dict at the LeRobot boundary and convert immediately to ROVE's native types before the pipeline starts. The conversion is a one-time operation in the plugin wrapper, not scattered through the adapter code. Tensor → bytes conversion uses `torchvision.transforms.ToPILImage` then `io.BytesIO`.

**C. Output format — action tensor vs ActionPrediction dataclass**: LeRobot expects a raw `(B, action_dim)` tensor. ROVE's `ActionPrediction` is a rich dataclass with latency, cost, and raw output preserved. The LeRobot wrapper must extract `ActionPrediction.actions` as a tensor and discard the rich metadata.

**This is a real loss**: when ROVE runs inside LeRobot's eval harness, the per-call cost and latency data that ROVE's evaluation store captures will not be accessible to LeRobot's metrics. The metadata must be captured elsewhere — in ROVE's own side-channel store that runs alongside the LeRobot evaluation.

**Verdict**: Feasible with a clear wrapping strategy. The interface bridge is a dedicated class (`RoveCompositionPolicy`) that handles all three translation concerns at the LeRobot boundary. The adapters themselves are not changed.

---

### Question 2: Does the composition (VLM+Grounding+VLA) wrap naturally as a single LeRobot policy?

**Yes, but with a critical distinction**: The `lerobot_policy_rove` package wraps a ROVE composition — a VLM perception stage plus a VLA action stage — as a single callable policy. From LeRobot's perspective this looks like any other policy. From ROVE's perspective it is a `RoveOrchestrator` configured with a VLM adapter and a VLA adapter, running inside `select_action`.

What this enables:

```python
# LeRobot user's perspective:
from lerobot_policy_rove import RoveCompositionPolicy

policy = RoveCompositionPolicy.from_pretrained(
    "rove://gpt-4o+smolvla-450m",  # composition identifier
    rove_config="rove.yaml"
)
# policy behaves identically to any LeRobot PreTrainedPolicy
# LeRobot's eval loop calls policy.select_action(batch) without knowing about VLMs
```

What this does not enable directly:

- LeRobot's standard metrics (action error, trajectory tracking) do not apply to ROVE compositions — ROVE compositions are not trained end-to-end, they have no ground-truth action trajectory to compare against. The meaningful metric is task success rate, which LeRobot measures via environment reward, not action error.
- LeRobot's model card / HuggingFace hub integration assumes a single `model_id` with saved weights. A ROVE composition is a configuration of two models, not a single model with weights. The "pretrained" metaphor is a stretch.

**Verdict**: The LeRobot plugin is best described as ROVE compositions registered as LeRobot-compatible policies for the purpose of running through LeRobot's task evaluation loop and getting task success metrics. It is not a pretrained model in the traditional sense. The plugin should be honest about this in its documentation.

---

### Question 3: What does the standalone mode retain?

Standalone mode is everything LeRobot cannot do:

| Capability | LeRobot Plugin Mode | Standalone Mode |
|-----------|---------------------|-----------------|
| VLM composition evaluation | Via `select_action` wrapper | Direct via `RoveOrchestrator` |
| Azure model access (GPT-4o, Phi-4) | Yes (ROVE adapter) | Yes (ROVE adapter) |
| Cost tracking per API call | Side-channel only | First-class, in SQLite |
| Foundry JSONL export | Not available | `rove export` / API endpoint |
| Cross-combination parallel ranking | Not available (LeRobot runs one policy at a time) | Core feature — `asyncio.gather` N×M |
| Dashboard | Not available | FastAPI + static HTML |
| CLI | `rove evaluate` | `rove evaluate` |
| MCP servers (Phase 4) | Not available | `rove mcp [eval|grounding|sim]` |
| VLM-only mode (no sim) | Not available (LeRobot needs action output) | Supported |
| pi0 / GR00T as UnifiedAdapter | Via LeRobot's own pi0 policy | Via `UnifiedAdapter` Protocol |
| Simulation (MuJoCo/LIBERO) | LeRobot's sim integration | ROVE `SimAdapter` |

The standalone mode is the **evaluation platform**. The LeRobot plugin mode is a **distribution channel** — a way for researchers already in the LeRobot ecosystem to try ROVE compositions without leaving their existing tooling.

---

## Decision

### D1: ROVE core is the standalone product. The LeRobot plugin is a thin wrapper package.

ROVE's evaluation engine, adapter layer, Azure adapters, cost tracking, evaluation store, and CLI do not depend on LeRobot. LeRobot is an optional dependency gated behind the plugin package.

The package structure:

```
rove-eval          (PyPI)    — ROVE core: no LeRobot dependency
lerobot-policy-rove (PyPI)  — thin wrapper: depends on rove-eval + lerobot
```

`lerobot-policy-rove` is a separate package, not a module inside `rove-eval`. This keeps the core installation clean for cloud/enterprise users who have no LeRobot and do not want PyTorch pulled in through a transitive dependency.

### D2: `RoveCompositionPolicy` is the sole bridge class. All interface translation lives here.

```python
# lerobot_policy_rove/policy.py

import asyncio
import io
from typing import Any
from lerobot.common.policies.pretrained import PreTrainedPolicy
import torch
from PIL import Image

class RoveCompositionPolicy(PreTrainedPolicy):
    """
    A LeRobot-compatible policy wrapper around a ROVE VLM+VLA composition.

    This class bridges LeRobot's observation dict → action tensor interface
    with ROVE's async image bytes + task → ActionPrediction pipeline.

    It is NOT a trained neural network. It has no learnable parameters.
    It is a composition of two models (VLM + VLA) that together produce
    actions LeRobot can execute.

    Side effects:
        - Records per-call latency and cost to a ROVE EvaluationStore
          if `rove_store` is provided at construction time.
        - Does NOT produce the intermediate SceneAnalysis and GraspPlan
          in the LeRobot action output — these are captured in the store only.
    """

    name = "RoveCompositionPolicy"

    def __init__(
        self,
        vlm_id: str,
        vla_id: str,
        task: str,
        rove_config_path: str = "rove.yaml",
        rove_store: Any | None = None,   # EvaluationStore, optional
    ) -> None:
        super().__init__(config=None)    # PreTrainedPolicy requires config arg
        from rove.config import RoveConfig
        from rove.adapters.registry import AdapterRegistry
        from rove.orchestrator.agent import RoveOrchestrator

        config = RoveConfig.from_yaml(rove_config_path)
        registry = AdapterRegistry(config.models)
        self._vlm = registry.get_vlm(vlm_id)
        self._vla = registry.get_vla(vla_id)
        self._grounding = registry.get_grounding(
            config.defaults.grounding
        )
        self._task = task
        self._store = rove_store
        self._orchestrator = RoveOrchestrator(
            vlm=self._vlm,
            vla=self._vla,
            grounding=self._grounding,
            sim=None,    # sim is LeRobot's env, not ROVE's SimAdapter
        )
        self._loop = asyncio.new_event_loop()

    def reset(self) -> None:
        # VLMs are stateless per-call. VLA recurrent state (if any) is in the adapter.
        pass

    def select_action(self, batch: dict[str, torch.Tensor]) -> torch.Tensor:
        """
        Bridge LeRobot's observation dict to ROVE's async pipeline.

        Translation contract:
          batch["observation.images.top"]  → image bytes (JPEG)
          batch["observation.state"]       → proprioception list[float]
          ROVE ActionPrediction.actions    → action tensor (chunk_size, action_dim)
        """
        image_bytes = self._tensor_to_jpeg(batch["observation.images.top"][0])
        proprio = batch.get("observation.state")
        proprio_list = proprio[0].tolist() if proprio is not None else None

        action_pred = self._loop.run_until_complete(
            self._orchestrator.run_action_only(
                image=image_bytes,
                task=self._task,
                proprioception=proprio_list,
            )
        )

        if self._store is not None:
            self._loop.run_until_complete(
                self._store.record_plugin_step(action_pred)
            )

        # Extract first action from chunk as (1, action_dim) tensor
        step = action_pred.actions[0]
        action_vec = torch.tensor(
            [step.dx, step.dy, step.dz, step.droll, step.dpitch, step.dyaw, step.gripper],
            dtype=torch.float32,
        ).unsqueeze(0)   # (1, 7)
        return action_vec

    @staticmethod
    def _tensor_to_jpeg(img_tensor: torch.Tensor) -> bytes:
        """
        Convert a (C, H, W) normalized float tensor to JPEG bytes.
        Assumes values in [0.0, 1.0].
        """
        pil_img = Image.fromarray(
            (img_tensor.permute(1, 2, 0).cpu().numpy() * 255).astype("uint8")
        )
        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()
```

**Critical design constraint**: `run_action_only` is a new method on `RoveOrchestrator` that runs only the perceive → ground → plan → act stages, skipping the sim reset/step and verify stages. In plugin mode, LeRobot's environment IS the sim. ROVE does not control it. Verify is also skipped — LeRobot records success via environment reward. ROVE's VLM verification would require a post-execution observation that LeRobot does not pass back to the policy.

This means the plugin mode evaluates a subset of ROVE's pipeline:

```
Plugin mode:  perceive → ground → plan → [act via VLA only, no sim step loop]
LeRobot env:  executes the returned action tensor, records reward
Standalone:   perceive → ground → plan → execute (sim) → verify
```

The `judge_calibration` metric is only available in standalone mode. This is a genuine capability difference that must be documented clearly.

### D3: The VLA adapter inside `RoveCompositionPolicy` is ROVE's `VLAAdapter`, not LeRobot's policy directly.

This is the correct layering. The ROVE `local_lerobot.py` VLA adapter wraps a LeRobot policy internally. The LeRobot `RoveCompositionPolicy` wrapper calls ROVE's `VLAAdapter.predict_action()`, which internally calls the LeRobot policy. The nesting is:

```
LeRobot eval loop
  └── RoveCompositionPolicy.select_action()      [lerobot_policy_rove package]
        └── RoveOrchestrator.run_action_only()   [rove-eval package]
              ├── VLMAdapter.analyze_scene()      [rove-eval, calls Azure]
              ├── GroundingAdapter.localize()     [rove-eval, local PyTorch]
              ├── VLMAdapter.plan_grasp()         [rove-eval, calls Azure]
              └── VLAAdapter.predict_action()     [rove-eval]
                    └── LeRobotSmolVLAPolicy.select_action()  [lerobot package]
```

This nesting means LeRobot is called twice in the plugin path: once as the outer eval loop and once as the inner VLA. This is intentional and correct. The outer LeRobot call provides the evaluation harness (env stepping, reward recording, multi-episode management). The inner LeRobot call runs the VLA model.

**Risk**: If LeRobot's `SmolVLA` policy has internal state that is reset by `policy.reset()` between episodes, ROVE's VLA adapter must also call reset. The `VLAAdapter` Protocol should have an optional `reset()` method for stateful adapters.

### D4: Azure integration lives in ROVE core, not in the plugin.

The Azure adapters (`azure_openai.py`, `azure_hf.py`), cost tracking in `ActionPrediction.cost_usd`, and Foundry JSONL export are all in `rove-eval`. The `lerobot-policy-rove` plugin uses them by importing from `rove`. No Azure code lives in the plugin package.

This means: an enterprise user who wants Azure cost tracking while running LeRobot evaluations can pass a `rove_store` to `RoveCompositionPolicy` and get full cost and latency records, even though LeRobot's own eval harness does not surface this data.

### D5: Parallel N×M combination evaluation is standalone-only.

LeRobot runs one policy at a time. ROVE's `asyncio.gather(N×M orchestrators)` pattern is a standalone feature. The plugin exposes one composition as one policy. If a researcher wants to compare `gpt-4o+smolvla` vs `qwen+smolvla`, they run LeRobot twice (once per policy) or use ROVE standalone.

This is acceptable. The target user for the plugin is someone who already has a LeRobot eval script and wants to drop in a VLM-augmented policy as a comparison point. ROVE standalone is for systematic cross-combination evaluation. They are complementary workflows.

---

## Architecture

```
+=====================================================================+
|                        rove-eval  (PyPI)                           |
|                   Core: no LeRobot dependency                      |
|                                                                     |
|  +------------------+   +------------------+   +-----------------+ |
|  |   CLI (Click)    |   | Python Library   |   | Dashboard       | |
|  |   rove evaluate  |   | import rove      |   | HTML + JS       | |
|  +--------+---------+   +--------+---------+   +--------+--------+ |
|           |                      |                       |          |
|           +----------+-----------+           fetch()/SSE           |
|                      |                                   |          |
|                      v                                   v          |
|  +-------------------------------------------------------------------+
|  |                     FastAPI Application                           |
|  |  POST /api/evaluations  GET /api/leaderboard  POST /api/export   |
|  +--------+----------------------------------------------------------+
|           |                                                          |
|           | asyncio.create_task()                                    |
|           v                                                          |
|  +-------------------------------------------------------------------+
|  |                     EvaluationEngine                              |
|  |                                                                   |
|  |  RunManager: asyncio.gather(N×M RoveOrchestrators)               |
|  |                                                                   |
|  |  RoveOrchestrator.run()         RoveOrchestrator.run_action_only()|
|  |  (standalone: all 5 stages)     (plugin mode: perceive→act only)  |
|  |                                                                   |
|  |  StageGraph: built from adapter capability declarations           |
|  +--------+----------------------------------------------------------+
|           |                                                          |
|           v                                                          |
|  +-------------------------------------------------------------------+
|  |                     Adapter Registry                              |
|  |  rove.yaml → AdapterRegistry → resolves model_id to adapter      |
|  |                                                                   |
|  |  VLMAdapter:          VLAAdapter:          GroundingAdapter:      |
|  |    mock_vlm             mock_vla             mock_grounding        |
|  |    azure_openai         local_lerobot  <---+  local_groundingdino  |
|  |    azure_hf_managed     azure_gpu_http     |  local_coreml_sam2    |
|  |    local_mlx            local_openvla      |                       |
|  |                         azure_gpu_groot    | (wraps LeRobot        |
|  |  SimAdapter:                               |  internally)          |
|  |    mock_sim             <-- standalone only|                       |
|  |    local_mujoco                            |                       |
|  +-------------------------------------------------------------------+
|           |                                                          |
|           v                                                          |
|  +-------------------------------------------------------------------+
|  |  EvaluationStore (SQLite)                                         |
|  |  Cost tracking: cost_usd per step                                 |
|  |  Foundry JSONL export                                             |
|  +-------------------------------------------------------------------+
|                                                                     |
|  Azure Integration Layer:                                           |
|  azure_openai.py, azure_hf.py  -- GPT-4o, Phi-4, Qwen2.5-VL       |
|  azure_gpu_cogact.py, azure_gpu_groot.py  -- VLA via HTTP           |
|  CostTracker: per-token billing from adapter response headers       |
|                                                                     |
+=====================================================================+

         +=====================================================+
         |         lerobot-policy-rove  (PyPI)               |
         |  Depends: rove-eval + lerobot                     |
         |                                                   |
         |  RoveCompositionPolicy(PreTrainedPolicy):         |
         |    __init__(vlm_id, vla_id, task, rove_config)    |
         |    select_action(batch) -> action_tensor          |
         |    reset()                                        |
         |                                                   |
         |  Interface bridge (all format translation here):  |
         |    Tensor (C,H,W) -> JPEG bytes                   |
         |    state Tensor   -> proprioception list[float]   |
         |    ActionPrediction.actions[0] -> action Tensor   |
         |                                                   |
         |  Calls: RoveOrchestrator.run_action_only()        |
         |  Side-channel: EvaluationStore.record_plugin_step |
         |                                                   |
         +===========+=======================================+
                     |
                     | LeRobot eval loop calls select_action()
                     v
         +=====================================================+
         |              LeRobot Ecosystem                    |
         |                                                   |
         |  lerobot eval --policy rove://gpt-4o+smolvla      |
         |  Env: LIBERO gym environments                     |
         |  Metrics: task success rate, episode reward       |
         |  Comparison: native VLA policies vs ROVE comps   |
         |                                                   |
         +=====================================================+

         +=====================================================+
         |              Azure Integration                    |
         |  (in rove-eval, used by both modes)               |
         |                                                   |
         |  Azure OpenAI: GPT-4o, Phi-4                     |
         |  Azure HF Managed: Qwen2.5-VL-32B                |
         |  Azure GPU HTTP: CogACT, GR00T                   |
         |  Azure AI Foundry: JSONL export, eval SDK        |
         |                                                   |
         +=====================================================+

         +=====================================================+
         |           MCP Servers (Phase 4, standalone)       |
         |                                                   |
         |  rove-eval-tools (8081)                           |
         |  rove-grounding-tools (8083)                      |
         |  rove-sim-tools (8082)                            |
         |                                                   |
         +=====================================================+
```

---

## What ROVE Keeps As Its Own (Even in Plugin Mode)

| Component | Reason it stays in rove-eval |
|-----------|------------------------------|
| Azure adapters (GPT-4o, Phi-4, Qwen2.5-VL) | LeRobot has no Azure integration. These are ROVE's competitive differentiation for enterprise. |
| Cost tracking (`cost_usd` per call) | LeRobot has no cost model. This is required for the enterprise path (Foundry export). |
| Foundry JSONL export | Azure-specific format. Not relevant to LeRobot's use case. |
| `StageGraph` orchestrator | The deterministic graph is ROVE's guarantee of evaluation reproducibility. LeRobot's eval harness replaces the sim+verify stages; it does not replace the perception-planning stages. |
| Parallel N×M execution | A standalone capability. Plugin mode is single-policy. |
| `EvaluationStore` (SQLite) | Persists all results including plugin-mode side-channel data. |
| Dashboard (HTML+JS) | Enterprise UI. LeRobot researchers use Jupyter. |
| MCP servers | Phase 4, standalone only. |

---

## Risks and Mitigations

### Risk 1: `asyncio.run()` inside `select_action()` in a Jupyter environment

**Problem**: Jupyter runs its own event loop (`tornado`). Calling `asyncio.run()` from inside an existing event loop raises `RuntimeError: This event loop is already running`.

**Mitigation**: `RoveCompositionPolicy` detects the environment at construction time. In Jupyter, use `nest_asyncio.apply()` (standard workaround) or maintain a dedicated thread with its own event loop for async calls:

```python
import threading
self._loop = asyncio.new_event_loop()
self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
self._thread.start()
# In select_action: asyncio.run_coroutine_threadsafe(coro, self._loop).result()
```

The thread-based approach is the correct production solution. `nest_asyncio` is acceptable for research use but is not recommended for production.

### Risk 2: JPEG re-encoding loses information

**Problem**: LeRobot passes normalized float tensors. Converting to JPEG introduces compression artifacts before the VLM call. High-frequency scene detail (small text on labels, fine object geometry) may be degraded.

**Mitigation**: Use PNG encoding for lossless round-trip when image size permits. Cap at 750KB (MCP binary limit constraint, though this path does not go through MCP). Document the encoding choice in `RoveCompositionPolicy` clearly so researchers understand what the VLM actually sees differs from what the VLA sees.

**Longer-term fix**: VLM adapters should accept PIL Images or numpy arrays directly, with `bytes` as one accepted input type. This is a `VLMAdapter` Protocol revision for Phase 2.

### Risk 3: VLA adapter state management in multi-episode evaluation

**Problem**: LeRobot resets the policy between episodes via `policy.reset()`. ROVE's `VLAAdapter` Protocol currently has no `reset()` method. VLAs with recurrent state (GR00T uses a transformer with KV cache) will carry state across episodes in plugin mode.

**Mitigation**: Add `reset() -> None` as an optional method to `VLAAdapter` Protocol. The `local_lerobot.py` adapter calls the underlying LeRobot policy's `reset()`. Adapters that are stateless (SmolVLA, CogACT) implement `reset()` as a no-op.

### Risk 4: `lerobot_policy_rove` naming and PyPI registration

**Problem**: LeRobot's plugin naming convention is `lerobot_policy_*`. The PyPI package name must be `lerobot-policy-rove`. The Python import is `from lerobot_policy_rove import RoveCompositionPolicy`. This requires registering `lerobot-policy-rove` on PyPI and maintaining it as a separate package with its own versioning, changelog, and CI pipeline.

**Mitigation**: Until the plugin reaches Phase 2 stability, the plugin is distributed as an extras install of `rove-eval`:

```
pip install rove-eval[lerobot]
```

This installs LeRobot as a dependency and makes `RoveCompositionPolicy` available. The separate `lerobot-policy-rove` PyPI package can be created when the plugin is stable enough to warrant independent versioning.

### Risk 5: LeRobot version pinning

**Problem**: LeRobot's `PreTrainedPolicy` interface has changed between versions and is not guaranteed stable. Pinning to a specific LeRobot version constrains users who may already have a different version installed.

**Mitigation**: Pin to `lerobot>=0.3.0` (the version that introduced the stable `PreTrainedPolicy` interface) and test against the current stable release in CI. Document the minimum version requirement clearly. Do not pin to an exact version — use a lower bound.

---

## Alternatives Considered

### Alternative 1: ROVE standalone only. No LeRobot plugin.

**Rejected**: The LeRobot ecosystem is where most VLA researchers work today. SmolVLA, pi0, OpenVLA-OFT are all distributed with LeRobot support. A tool that cannot integrate with LeRobot is asking researchers to rewrite their evaluation setup. The plugin lowers the adoption barrier from "rewrite everything" to "swap one policy class."

### Alternative 2: Replace ROVE's VLA adapter layer with LeRobot directly.

**Rejected**: ROVE's `VLAAdapter` Protocol is the abstraction that makes the standalone path work with cloud VLAs (CogACT on Azure GPU HTTP), non-LeRobot VLAs (future models), and mock adapters. Collapsing this layer to "just use LeRobot" would eliminate the standalone mode's ability to evaluate any VLA that is not in LeRobot's policy registry. The `local_lerobot.py` adapter wraps LeRobot; it does not replace ROVE's abstraction.

### Alternative 3: ROVE as a LeRobot environment (`lerobot_env_rove`).

**Considered but rejected**: LeRobot environments are gym-compatible wrappers around physical or simulated environments. ROVE is not an environment — it is an evaluation pipeline that contains a sim. The "environment" equivalent in ROVE is the `SimAdapter` (MuJoCo+LIBERO). An `lerobot_env_rove` package would expose ROVE's MuJoCo integration as a gym environment, but this only covers the sim, not the VLM composition. This is a narrower integration that does not achieve the composition evaluation goal.

**However**: It may be worth building `lerobot_env_rove` alongside `lerobot_policy_rove` in Phase 2+ to expose ROVE's LIBERO tasks as LeRobot-compatible environments. This would allow pure VLA policies (SmolVLA, pi0) to be evaluated in ROVE's sim without going through the full ROVE pipeline. Not in scope for this ADR.

### Alternative 4: Share the same package. LeRobot is a conditional import.

**Partially accepted**: For distribution, `rove-eval[lerobot]` extras is the short-term approach (Risk 4 mitigation). For the long term, a separate `lerobot-policy-rove` package is cleaner. The plugin package is thin enough (one class, one file) that the maintenance overhead of a separate package is low.

---

## Implementation Steps

### Phase 1 (before Phase 2 VLA adapters are built)

- [ ] Add `reset() -> None` as optional method to `VLAAdapter` Protocol in `rove/adapters/protocols.py`
- [ ] Add `run_action_only(image, task, proprioception)` method to `RoveOrchestrator` in `rove/orchestrator/agent.py`
  - Runs perceive → ground → plan → predict_action stages only
  - Returns `ActionPrediction` (no sim, no verify)
  - Accepts `proprioception: list[float] | None`
- [ ] Add `[lerobot]` optional dependency group to `pyproject.toml`
- [ ] Create `rove/integrations/lerobot_policy.py` with `RoveCompositionPolicy`
  - Guarded import: `try: from lerobot... except ImportError: raise ImportError("Install rove-eval[lerobot]")`
  - Thread-based async event loop (not `asyncio.run()`)
- [ ] Add `record_plugin_step(action_pred: ActionPrediction) -> None` to `EvaluationStore`

### Phase 2 (when SmolVLA local adapter is built)

- [ ] Implement `local_lerobot.py` VLA adapter with `reset()` method
- [ ] Write integration test: `RoveCompositionPolicy` with mock VLM + local SmolVLA
- [ ] Document the JPEG encoding behavior in adapter docstring
- [ ] Test in Jupyter environment — verify thread-based event loop handles Jupyter's event loop correctly

### Phase 3 (when Azure VLM adapters are built)

- [ ] End-to-end test: GPT-4o (Azure) + SmolVLA (local) via `RoveCompositionPolicy`
- [ ] Measure VLM API latency impact on LeRobot eval throughput — VLM calls add ~500-2000ms per step; this is acceptable for research evaluation but not for real-time control
- [ ] Add cost reporting: `rove export --evaluation <eval_id> --include-plugin-steps`

### Phase 4 (separate package, if warranted)

- [ ] Extract `rove/integrations/lerobot_policy.py` to separate `lerobot-policy-rove` PyPI package
- [ ] Set up independent CI for `lerobot-policy-rove`
- [ ] Register on PyPI

---

## Consequences

### Positive

- LeRobot researchers can evaluate VLM-augmented compositions alongside native VLA policies using their existing eval scripts
- ROVE core has zero LeRobot dependency — cloud-only deployments and Phase 1 development are unaffected
- Azure adapters, cost tracking, and Foundry export are available in both modes
- Interface bridge is isolated to one class — VLM adapters and VLA adapters are not changed

### Negative

- `run_action_only` is a new code path in `RoveOrchestrator` that must be maintained alongside `run()`. Coverage required.
- Plugin mode cannot produce `judge_calibration` metric — the strongest evidence of VLM verification quality is lost when LeRobot controls the environment. This must be documented clearly so researchers do not over-interpret plugin-mode success rates.
- JPEG re-encoding degrades image quality for VLM input in plugin mode. The VLM and VLA may see slightly different image representations, which is a systematic difference between plugin and standalone modes. This is a known limitation, not a bug.
- `asyncio` event loop management in `select_action` is non-trivial and environment-dependent (Jupyter vs script vs server). The thread-based approach works but adds complexity to the plugin.

### What This Is Not

The LeRobot plugin does not make ROVE a robot control system. `RoveCompositionPolicy` is for research evaluation inside LeRobot's simulator harness. It is not intended for deployment on physical hardware. The latency budget for VLM API calls (500–2000ms) is incompatible with real-time robot control. ROVE's own documentation and the plugin's README must state this explicitly.

---

## References

- `docs/architecture/high-level-architecture.md` — current architecture
- `docs/architecture/ADR-001-stage-graph-orchestrator-and-mcp-topology.md` — stage graph and UnifiedAdapter
- `rove/adapters/protocols.py` — VLMAdapter, VLAAdapter, GroundingAdapter Protocols
- `rove/orchestrator/agent.py` — RoveOrchestrator (run() method)
- LeRobot `PreTrainedPolicy`: `lerobot/common/policies/pretrained.py`
- Founder messages: 2026-02-21 strategy revision (LeRobot plugin, Azure path, composition evaluation)

---

*ADR-002 — System Architect, 2026-02-21*
*Status: Proposed — awaiting founder review*
