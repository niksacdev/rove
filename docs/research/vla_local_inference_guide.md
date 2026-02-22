# VLA Local Inference Guide

**Scope**: pi0/pi0-FAST, OpenVLA-OFT 7B, SmolVLA 450M, CogACT 7B, Octo-Base 93M
**Purpose**: Adapter implementation reference for ROVE evaluation framework
**Last updated**: 2026-02-21

---

## Quick Reference: Hardware Compatibility Matrix

| Model          | Params | Framework                         | MPS (Apple Silicon)          | CUDA | CPU        | Min VRAM         |
|----------------|--------|-----------------------------------|------------------------------|------|------------|------------------|
| pi0 / pi0-FAST | ~3B    | JAX (native), PyTorch (HF port)   | No (JAX-Metal experimental)  | Yes  | Yes (slow) | 8 GB             |
| OpenVLA-OFT 7B | 7B     | PyTorch (HF Transformers)         | Untested, not official       | Yes  | Yes (slow) | 16 GB (bf16)     |
| SmolVLA 450M   | 450M   | PyTorch (LeRobot)                 | Yes (explicitly supported)   | Yes  | Yes        | ~2 GB            |
| CogACT 7B      | ~7B    | PyTorch                           | No (CUDA >= 12.0 required)   | Yes  | No         | 30 GB fp32 / 15 GB bf16 |
| Octo-Base 93M  | 93M    | JAX                               | Experimental (jax-metal)     | Yes  | Yes        | ~2 GB            |

**Bottom line for ROVE on Apple Silicon M-series:**
- SmolVLA 450M: works on MPS, best option for local dev
- OpenVLA-OFT 7B: run on CPU with bf16, expect ~0.1 Hz — use mock for dev
- pi0 / pi0-FAST: JAX-Metal is too experimental; use mock or remote endpoint
- CogACT 7B: CUDA only; use mock or Azure GPU endpoint
- Octo-Base 93M: small enough for CPU-only on Mac (~4 GB RAM), ~2-3 Hz

---

## 1. pi0 / pi0-FAST (Physical Intelligence)

### Architecture

- **pi0**: Flow-matching VLA. PaliGemma (3B) as vision-language backbone. Predicts
  action chunks (10-50 steps) in a single forward pass. ~50 Hz inference on a 4090.
- **pi0-FAST**: Same backbone, but uses the FAST tokenizer. Actions are discretized
  into tokens and generated autoregressively. Approximately 4-5x slower than pi0,
  but with better language instruction following.

### Installation

The native openpi repo uses JAX and the `uv` package manager:

```bash
git clone --recurse-submodules https://github.com/Physical-Intelligence/openpi.git
cd openpi
# GIT_LFS_SKIP_SMUDGE=1 avoids pulling large binary blobs from LeRobot submodules
GIT_LFS_SKIP_SMUDGE=1 uv sync
GIT_LFS_SKIP_SMUDGE=1 uv pip install -e .
```

For the HuggingFace PyTorch port (easier for ROVE adapters, CUDA only):

```bash
pip install lerobot
# The HF port is distributed through the lerobot package
# Model: lerobot/pi0 on HuggingFace
```

### Inference Code (JAX / native openpi)

```python
"""
pi0 inference using the official openpi JAX implementation.
Requires: uv, JAX with CUDA, >8 GB VRAM.
Does NOT work on Apple Silicon (JAX-Metal is experimental and version-locked).
"""
from openpi.training import config as _config
from openpi.policies import policy_config
from openpi.shared import download
import numpy as np

# Load config and download checkpoint from GCS
# Available configs: pi0_base, pi0_fast_droid, pi0_aloha_sim, pi05_droid
config = _config.get_config("pi0_base")
checkpoint_dir = download.maybe_download(
    "gs://openpi-assets/checkpoints/pi0_base"
)

# Build the policy (handles tokenizer, model, normalization stats)
policy = policy_config.create_trained_policy(config, checkpoint_dir)

# Observation format depends on the config.
# pi0_base uses DROID-style keys (exterior + wrist images).
# Always check config.model.fake_obs() to see expected keys.
example = {
    "observation/exterior_image_1_left": np.zeros((224, 224, 3), dtype=np.uint8),
    "observation/wrist_image_left":      np.zeros((224, 224, 3), dtype=np.uint8),
    "observation/joint_position":        np.zeros(7, dtype=np.float32),
    "observation/gripper_position":      np.zeros(1, dtype=np.float32),
    "prompt": "pick up the red bracket",
}

# Inference: returns dict with "actions" key
result = policy.infer(example)
actions = result["actions"]  # shape: [chunk_size, action_dim] e.g. [10, 7]
# action_dim = 7 for DROID (x, y, z, roll, pitch, yaw, gripper)
```

### Inference Code (PyTorch port via LeRobot)

```python
"""
pi0 inference using the HuggingFace PyTorch port.
More compatible with standard HF tooling.
Memory requirements are similar to the JAX version.
Validated on the LIBERO benchmark.
"""
from lerobot.common.policies.pi0.modeling_pi0 import PI0Policy
import torch
import numpy as np

device = torch.device("cuda")  # MPS not supported for this port

# Load from HuggingFace hub (downloads approximately 6 GB checkpoint)
policy = PI0Policy.from_pretrained("lerobot/pi0")
policy.to(device)
policy.eval()

# Observation format for PI0Policy.
# The exact keys depend on the policy config — check policy.config.input_features.
observation = {
    "observation.image":  torch.zeros(1, 3, 224, 224).to(device),  # [B, C, H, W]
    "observation.state":  torch.zeros(1, 8).to(device),             # [B, joints+gripper]
}
task = ["pick up the red bracket"]  # plain text instruction

with torch.no_grad():
    action = policy.select_action(observation)

# action shape: [1, action_dim] single-step or [1, chunk_size, action_dim] chunk
print(action.shape)
```

### Memory Requirements

| Mode                  | VRAM Needed | Notes                          |
|-----------------------|------------|--------------------------------|
| Inference (bf16)      | ~8 GB      | Minimum viable on 4090         |
| Inference (fp32)      | 12-16 GB   | JAX default                    |
| Fine-tuning (LoRA)    | >22.5 GB   | RTX 4090                       |
| Fine-tuning (full)    | >70 GB     | A100 80 GB / H100              |

### Apple Silicon Status

JAX has a Metal plugin (`jax-metal`), but as of early 2026 it is explicitly labeled
experimental by Apple. The JAX version requirements for openpi (JAX 0.4.x) frequently
conflict with jax-metal's supported version range.

**Recommendation for ROVE**: Do not attempt pi0/pi0-FAST on Apple Silicon.
Use the mock adapter for local development. For real inference without a CUDA
machine, openpi supports a gRPC remote server mode — run the server on a CUDA
machine and point the adapter at its endpoint.

### Known Issues and Gotchas

1. **uv vs pip**: openpi uses `uv` as its package manager. Running plain `pip install -e .`
   will likely produce incorrect dependency resolution. Always use `uv sync` first.

2. **GCS checkpoint download**: Checkpoints live on Google Cloud Storage, not
   HuggingFace Hub. `download.maybe_download()` caches them in `~/.cache/openpi/`.
   The first run requires GCP network access (no auth needed for public checkpoints,
   but GCP egress rules can block certain networks).

3. **Observation key naming is platform-specific**: `pi0_base` expects
   `observation/exterior_image_1_left`, while `pi0_aloha_sim` expects
   `observation/image_top`. Always derive expected keys from `config.model.fake_obs()`.

4. **pi0-FAST latency**: Because FAST tokenizes actions autoregressively, inference
   cost scales with sequence length. Budget 4-5x the pi0 latency per call.

5. **Normalization stats are embedded in checkpoint**: `policy.infer()` handles
   normalization internally. If you fine-tune, recompute stats on your dataset
   and rebuild the checkpoint correctly — do not mix normalization stats from the
   base checkpoint with a fine-tuned checkpoint.

---

## 2. OpenVLA-OFT 7B

### Architecture

OpenVLA-OFT (Optimized Fine-Tuning) extends the base OpenVLA 7B model. The base
model is autoregressive: it discretizes each action dimension into 256 vocabulary
bins and generates 7 token IDs sequentially. One inference call equals 7 sequential
forward passes through the 7B LLM backbone.

OFT adds an L1 regression head and optional diffusion head (replacing discretization)
for better continuous action quality, plus proprioception input and multi-image support.

**The base `openvla/openvla-7b` on HuggingFace uses standard `AutoModelForVision2Seq`.**
**The OFT variant requires the separate `moojink/openvla-oft` codebase.**

### Installation

For base OpenVLA (standard HF Transformers, autoregressive):

```bash
pip install -r https://raw.githubusercontent.com/openvla/openvla/main/requirements-min.txt
# Core deps: torch, transformers, timm, tokenizers, Pillow
```

For OpenVLA-OFT (recommended for ROVE — better action quality):

```bash
git clone https://github.com/moojink/openvla-oft.git
cd openvla-oft
conda create -n openvla-oft python=3.10
conda activate openvla-oft
# See SETUP.md in the repo for full environment setup
pip install -e .
```

### Inference Code (Base OpenVLA — Autoregressive)

```python
"""
Base OpenVLA 7B inference via standard HuggingFace Transformers.
Autoregressive: 7 tokens generated sequentially (one per action dimension).
Target device: CUDA (tested). MPS: possible in theory, not officially supported.
Memory: approximately 14 GB VRAM in bf16.
"""
from transformers import AutoModelForVision2Seq, AutoProcessor
from PIL import Image
import torch
import io

device = "cuda:0"
dtype = torch.bfloat16

processor = AutoProcessor.from_pretrained(
    "openvla/openvla-7b",
    trust_remote_code=True,
)
vla = AutoModelForVision2Seq.from_pretrained(
    "openvla/openvla-7b",
    # flash_attention_2: CUDA only — remove this line on non-CUDA machines
    attn_implementation="flash_attention_2",
    torch_dtype=dtype,
    low_cpu_mem_usage=True,
    trust_remote_code=True,
).to(device)
vla.eval()

# image_bytes is what ROVE passes in from the Protocol
image_bytes: bytes = open("scene.jpg", "rb").read()
image = Image.open(io.BytesIO(image_bytes))

# Prompt format is fixed — do not change the "In:/Out:" wrapper
prompt = "In: What action should the robot take to pick the red bracket?\nOut:"

inputs = processor(prompt, image).to(device, dtype=dtype)

with torch.no_grad():
    # predict_action: decodes 7 token IDs to continuous floats
    # unnorm_key: selects per-robot normalization statistics
    # Available keys: "bridge_orig", "fractal20220817_data", "libero_spatial", etc.
    action = vla.predict_action(
        **inputs,
        unnorm_key="bridge_orig",
        do_sample=False,  # greedy decoding for determinism
    )
# action: numpy array, shape [7,] — (x, y, z, roll, pitch, yaw, gripper)
```

### Inference Code (OpenVLA-OFT — L1 Regression Head, Recommended)

```python
"""
OpenVLA-OFT inference with L1 regression head.
Requires the openvla-oft repo cloned locally.
Memory: approximately 16 GB VRAM in bf16.
"""
import pickle
import sys
sys.path.insert(0, "/path/to/openvla-oft")

from experiments.robot.libero.run_libero_eval import GenerateConfig
from experiments.robot.openvla_utils import (
    get_action_head,
    get_processor,
    get_proprio_projector,
    get_vla,
    get_vla_action,
)

cfg = GenerateConfig(
    pretrained_checkpoint="moojink/openvla-7b-oft-finetuned-libero-spatial",
    use_l1_regression=True,    # L1 head: fast continuous actions
    use_diffusion=False,        # diffusion head: higher quality, slower
    num_images_in_input=2,      # primary + wrist camera
    use_proprio=True,           # include joint state as input
)

vla              = get_vla(cfg)
processor        = get_processor(cfg)
action_head      = get_action_head(cfg, llm_dim=vla.llm_dim)
proprio_projector = get_proprio_projector(cfg, llm_dim=vla.llm_dim)

observation = {
    "full_image":        ...,    # PIL Image
    "wrist_image":       ...,    # PIL Image
    "proprio":           ...,    # np.ndarray [PROPRIO_DIM]
    "task_description":  "pick up the red bracket",
}

# Returns: np.ndarray, shape [NUM_ACTIONS_CHUNK, 7]
actions = get_vla_action(
    cfg, vla, processor, observation,
    observation["task_description"],
    action_head, proprio_projector,
)
```

### Memory Requirements

| Precision                          | VRAM     | Notes                        |
|------------------------------------|----------|------------------------------|
| fp32                               | ~28 GB   | Not practical for inference  |
| bf16                               | ~14-16 GB | Recommended for CUDA         |
| int4 via MLX (Apple Silicon only)  | ~4 GB    | Custom port required         |

### Apple Silicon Status

`bitsandbytes` int4/int8 quantization does NOT work on MPS. This is a hard blocker
for running a 7B model comfortably on Apple Silicon without a custom MLX port.

Options for Apple Silicon:
- Run in bf16 on CPU: ~14 GB RAM, ~0.05 Hz (20+ seconds per action)
- Convert to MLX format via `mlx-lm`: requires significant engineering effort
- Use the mock adapter for local dev; run real inference on a remote CUDA endpoint

**Recommendation for ROVE**: Use mock for Phase 1-2. For Phase 3, run OpenVLA-OFT
on an Azure GPU VM (A100/A10G) and expose it via the `azure_gpu_http` adapter.

### Known Issues and Gotchas

1. **`unnorm_key` is required and non-obvious**: If you pass the wrong key or omit it,
   actions will be wrong by orders of magnitude. For LIBERO tasks use `"libero_spatial"`.
   Check the model card for a full list of available keys.

2. **`flash_attention_2` is CUDA-only**: Remove `attn_implementation="flash_attention_2"`
   on any non-CUDA machine or the model will raise an import error at load time.

3. **OFT repo is not a clean library**: `openvla-oft` imports from `experiments.robot.*`.
   Your ROVE adapter must either vendor the needed utility functions or invoke them
   via a subprocess call. Do not try to `pip install` the OFT inference utilities
   independently.

4. **Autoregressive latency**: 7 sequential token generations through a 7B model.
   On an A100 this takes approximately 200-400 ms per single action step.

5. **`trust_remote_code=True`**: Required because OpenVLA ships a custom
   `PrismaticForConditionalGeneration` class. Pin the commit hash in production.

---

## 3. SmolVLA 450M

### Architecture

SmolVLA uses flow matching (not diffusion, not autoregressive). It predicts action
chunks in a single forward pass. The VLM backbone is SmolVLM2-500M (SigLIP vision
encoder + SmolLM2-1.7B with layer skipping that halves compute). Images are compressed
to 64 tokens via PixelShuffle.

This is the best model for Apple Silicon local development:
- Small enough to run comfortably (2-4 GB RAM)
- Flow matching = one forward pass per chunk (not iterative like diffusion)
- LeRobot explicitly tests and supports MPS

### Installation

```bash
git clone https://github.com/huggingface/lerobot.git
cd lerobot
pip install -e ".[smolvla]"
```

### Inference Code

```python
"""
SmolVLA 450M inference via LeRobot.
Supports: CUDA, MPS (Apple Silicon), CPU.
Memory: 2-4 GB (fits on any M-series Mac).
Architecture: Flow matching, single forward pass per action chunk.
"""
import torch
import io
import numpy as np
from PIL import Image
from lerobot.common.policies.smolvla.modeling_smolvla import SmolVLAPolicy
from lerobot.common.policies.factory import make_pre_post_processors

# MPS is explicitly supported — no workaround needed
if torch.cuda.is_available():
    device = torch.device("cuda")
elif torch.backends.mps.is_available():
    device = torch.device("mps")
else:
    device = torch.device("cpu")

# Load from HuggingFace Hub
policy = SmolVLAPolicy.from_pretrained("lerobot/smolvla_base")
policy.to(device)
policy.eval()

# Build pre/post processors
# preprocessor_overrides moves image processing to the correct device
pre, post = make_pre_post_processors(
    policy.config,
    preprocessor_overrides={"device_processor": {"device": str(device)}},
)

# image_bytes comes from ROVE Protocol.predict_action signature
image_bytes: bytes = open("scene.jpg", "rb").read()
image_np = np.array(Image.open(io.BytesIO(image_bytes)))  # [H, W, 3] uint8

raw_obs = {
    "observation.image":  image_np,             # primary camera
    "observation.state":  np.zeros(7, dtype=np.float32),  # joint state
    "task":               "pick the red bracket",
}

model_obs = pre(raw_obs)

with torch.no_grad():
    # select_action returns ONE action from the internal chunk buffer.
    # Internally, every chunk_size calls triggers a new forward pass.
    action_tensor = policy.select_action(model_obs)

# postprocess converts from normalized model space to robot command space
action = post(action_tensor)
# action: numpy array, shape [action_dim] — typically [7,]
print(action)
```

### Asynchronous Inference Pattern (for real-time control)

SmolVLA supports async inference where action prediction runs in a background thread
while the robot executes the current chunk, giving roughly 2x throughput:

```python
import threading
import queue
from lerobot.common.policies.smolvla.modeling_smolvla import SmolVLAPolicy

policy = SmolVLAPolicy.from_pretrained("lerobot/smolvla_base")
policy.to(device).eval()

action_queue = queue.Queue(maxsize=2)
obs_queue = queue.Queue(maxsize=1)


def inference_worker():
    while True:
        obs = obs_queue.get()
        if obs is None:
            break
        with torch.no_grad():
            action = policy.select_action(obs)
        action_queue.put(action)


worker = threading.Thread(target=inference_worker, daemon=True)
worker.start()

for step in range(1000):
    obs = get_robot_observation()
    obs_queue.put(preprocess(obs))
    action = action_queue.get()
    robot.execute(postprocess(action))
```

### Memory Requirements

| Device            | Memory   | Inference Speed       |
|-------------------|----------|-----------------------|
| CUDA (A100)       | ~2 GB VRAM | ~100 Hz              |
| MPS (M2 Max)      | ~3 GB unified RAM | ~15-30 Hz est.|
| CPU               | ~4 GB RAM  | ~2-5 Hz              |

### Apple Silicon Status

SmolVLA explicitly supports MPS. The LeRobot team tests on Mac hardware.
The `make_pre_post_processors` with `device_processor` override correctly
moves image preprocessing to MPS.

Some attention variants in the SmolLM2 backbone may fall back to CPU for
unsupported MPS operations on older M1/M2 chips. M3/M4 chips have broader
MPS op coverage and will run faster.

### Known Issues and Gotchas

1. **Proprioception is required**: SmolVLA was trained with joint state input.
   Passing zeros works for testing but degrades action quality significantly.

2. **Action chunk buffer semantics**: `select_action` maintains an internal buffer.
   It returns one action per call. Every `chunk_size` calls triggers a new GPU
   forward pass. The remaining calls are buffer reads with no GPU work.

3. **Image resolution**: The model accepts variable resolution and uses PixelShuffle
   to compress to 64 tokens. Inputs larger than approximately 1280 px on any side
   will be resized by the preprocessor.

4. **lerobot version**: SmolVLA was added in LeRobot 0.2+. Install from the cloned
   main branch with `pip install -e ".[smolvla]"`. A stale pip install will not
   include `SmolVLAPolicy`.

5. **Task string token limit**: The task instruction is encoded by the SmolLM2
   tokenizer with a 256-token cap. Keep instructions concise.

---

## 4. CogACT 7B

### Architecture

CogACT (Microsoft Research) uses a componentized VLA design:
- **VLM backbone**: CogVLM (LLaMA-2 + DINOv2 ViT-L/14 + SigLIP ViT-So400M/14 dual encoder)
- **Action module**: DiT (Diffusion Transformer) conditioned on VLM output

The DiT generates actions through iterative DDIM denoising. With `num_ddim_steps=10`,
this means 10 forward passes through the DiT per action chunk. This is the source
of the 2-10 second inference latency in `rove.yaml`.

Available sizes: CogACT-Small (DiT-S), CogACT-Base (DiT-B), CogACT-Large (DiT-L).

### Installation

```bash
conda create --name cogact python=3.10
conda activate cogact
git clone https://github.com/microsoft/CogACT.git
cd CogACT
pip install -e .
# CUDA >= 12.0 is required — this constraint is not optional
```

### Inference Code

```python
"""
CogACT-Base inference.
CUDA ONLY — requires CUDA >= 12.0.
Memory: ~30 GB fp32, ~15 GB with VLM in bf16.
Architecture: Diffusion Transformer. 10 DDIM steps = 10 forward passes through DiT.
Latency: 2-10 seconds per 16-step action chunk on A100.
"""
from PIL import Image
from vla import load_vla  # installed from CogACT repo
import torch
import io

# Load model
# action_model_type: 'DiT-S' (small), 'DiT-B' (base), 'DiT-L' (large)
model = load_vla(
    "CogACT/CogACT-Base",
    load_for_training=False,
    action_model_type="DiT-B",
    future_action_window_size=15,
)

# Load VLM backbone in bf16 to reduce VRAM from ~30 GB to ~15 GB.
# Keep the DiT action module in fp32 for numerical stability.
model.vlm = model.vlm.to(torch.bfloat16)

model.to("cuda:0").eval()

# image_bytes comes from ROVE Protocol.predict_action signature
image_bytes: bytes = open("scene.jpg", "rb").read()
image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

prompt = "pick the red bracket and place it in bin A"

# Inference via DDIM sampling
# cfg_scale: classifier-free guidance scale; 1.5-7.0 range; 1.5 is the safe default
# num_ddim_steps: 10 is fast (2-4 s), 50 is higher quality (10-20 s)
actions, _ = model.predict_action(
    image,
    prompt,
    unnorm_key="fractal20220817_data",
    cfg_scale=1.5,
    use_ddim=True,
    num_ddim_steps=10,
)

# actions: torch.Tensor or np.ndarray, shape [16, 7]
# 16 timesteps, 7-DOF: (x, y, z, roll, pitch, yaw, gripper)
next_action = actions[0]   # first action of chunk, shape [7,]
full_chunk   = actions      # list[list[float]] for ROVE ActionPrediction
```

### Latency by DDIM Step Count

| DDIM Steps | A100 (approx) | A10G (approx) | Action Quality |
|------------|---------------|---------------|----------------|
| 10         | 2-3 seconds   | 4-6 seconds   | Good           |
| 25         | 5-8 seconds   | 8-15 seconds  | Better         |
| 50         | 10-20 seconds | 20-40 seconds | Best           |

For ROVE evaluations, use `num_ddim_steps=10` to stay within the 30-second
step timeout configured in `rove.yaml`.

### Memory Requirements

| Config                             | VRAM     |
|------------------------------------|----------|
| CogACT-Small (DiT-S, fp32)         | ~20 GB   |
| CogACT-Base (DiT-B, fp32)          | ~30 GB   |
| CogACT-Base (VLM bf16 + DiT fp32)  | ~15-18 GB|
| CogACT-Large (DiT-L, fp32)         | >40 GB   |

An A100 80 GB is comfortable. An RTX 4090 (24 GB) can run CogACT-Base with bf16 VLM.

### Apple Silicon Status

Not supported. The repo requires CUDA >= 12.0. At ~15 GB in bf16, an M-series
Mac would also need at least 32 GB unified memory to avoid constant swapping.

**For ROVE**: CogACT runs on an Azure GPU VM (NC24ads A100). Use the `azure_gpu_http`
adapter — the adapter sends an HTTP request to a running CogACT inference server
and receives the action array in the response body.

### Known Issues and Gotchas

1. **CUDA 12.0 is a hard requirement**: CUDA 11.x installs appear to succeed but
   fail with cryptic kernel errors at inference time.

2. **`unnorm_key` selection**: `fractal20220817_data` is the Open X-Embodiment
   default. For LIBERO tasks, you need a LIBERO-finetuned CogACT checkpoint that
   includes LIBERO normalization statistics.

3. **DiT-B vs DiT-L for ROVE**: `CogACT-Base` with `DiT-B` is the correct choice
   for evaluation — good quality, feasible memory footprint. Do not use CogACT-Large
   for automated evals without an 80 GB A100.

4. **`cfg_scale` sensitivity**: Below 1.5, actions become incoherent. Above 7.0,
   the robot overshoots targets. Default 1.5 is safe for most tasks.

5. **Single image only**: Base CogACT does not support multi-view cameras or
   proprioception input. This is a meaningful capability gap compared to SmolVLA
   and OpenVLA-OFT.

---

## 5. Octo-Base 93M

### Architecture

Octo is a transformer policy trained on 800K+ robot trajectories from Open X-Embodiment.
It uses a diffusion policy as its action decoder. Observations are tokenized using
learned encoders and attended to by a causal transformer.

Key properties:
- 93M parameters (Octo-Base) or 27M (Octo-Small)
- Predicts 4 future actions (chunk_size=4) per call
- Supports both language-conditioned and goal-image-conditioned inference
- ~13 iterations/sec on a 4090
- Implemented in JAX, not PyTorch

### Installation

```bash
conda create -n octo python=3.10
conda activate octo
git clone https://github.com/octo-models/octo.git
cd octo
pip install -e .
pip install -r requirements.txt

# CUDA GPU (recommended for reasonable speed):
pip install --upgrade "jax[cuda12_pip]" \
    -f https://storage.googleapis.com/jax-releases/jax_cuda_releases.html

# CPU-only (works, slower):
pip install --upgrade jax

# Apple Silicon (experimental — see Apple Silicon Status below):
pip install jax-metal
# WARNING: version conflicts between jax-metal and Octo's pinned JAX version
# are common. Verify with: python -c "import jax; print(jax.default_backend())"
```

### Inference Code

```python
"""
Octo-Base 93M inference.
Framework: JAX (not PyTorch). No HuggingFace Transformers dependency.
Memory: approximately 2-4 GB (fits on any modern GPU or Mac with enough RAM).
Apple Silicon: works in CPU mode; jax-metal acceleration is experimental.
"""
import jax
import numpy as np
from octo.model.octo_model import OctoModel

# Load from HuggingFace Hub — caches locally on first run
model = OctoModel.load_pretrained("hf://rail-berkeley/octo-base-1.5")

# IMPORTANT: JAX JIT-compiles the model on the first call.
# That first call takes 10-30 seconds. Run a warm-up step before the eval loop.
dummy_obs = {
    "image_primary":     np.zeros((1, 2, 256, 256, 3), dtype=np.uint8),
    "timestep_pad_mask": np.array([[True, True]]),
}
dummy_task = model.create_tasks(texts=["warm up"])
_ = model.sample_actions(dummy_obs, dummy_task, rng=jax.random.PRNGKey(0))

# --- Task definition ---
task = model.create_tasks(texts=["pick up the red bracket"])

# --- Observation format ---
# Octo-base-1.5 requires a 2-timestep observation window.
# At episode start, duplicate the initial observation for both timesteps.
WINDOW_SIZE = 2
image_np = np.zeros((256, 256, 3), dtype=np.uint8)  # replace with real image
images   = [image_np] * WINDOW_SIZE
input_images = np.stack(images)[None]  # shape: [1, 2, 256, 256, 3]

observation = {
    "image_primary":     input_images,
    "timestep_pad_mask": np.array([[True, True]]),
}

# --- Action sampling ---
actions = model.sample_actions(
    observation,
    task,
    unnormalization_statistics=model.dataset_statistics["bridge_dataset"]["action"],
    rng=jax.random.PRNGKey(0),
)

# actions shape: [batch, action_chunk, action_dim]
# For bridge_dataset: action_dim=7, action_chunk=4
next_action = np.array(actions[0, 0])  # shape [7,], first step of first batch
full_chunk   = np.array(actions[0])    # shape [4, 7], for ROVE ActionPrediction
```

### Memory Requirements

| Model       | Parameters | GPU VRAM | RAM (CPU mode) |
|-------------|-----------|----------|----------------|
| Octo-Small  | 27M       | ~1 GB    | ~2 GB          |
| Octo-Base   | 93M       | ~2-3 GB  | ~4 GB          |

### Apple Silicon Status

JAX has a Metal plugin (`pip install jax-metal`) maintained by Apple.
Status as of early 2026:

- jax-metal is labeled experimental; not all JAX operations are supported
- Octo pins `jax==0.4.20`; jax-metal may require a different JAX version
- Version mismatch causes silent CPU fallback with no error message

**How to confirm Metal acceleration is active:**
```python
import jax
print(jax.default_backend())  # "METAL" = GPU, "cpu" = no acceleration
print(jax.devices())
```

**Recommendation for ROVE**: Given Octo's small size (93M, ~4 GB RAM), running
on CPU is viable for local development at approximately 2-3 Hz. Use `device: cuda`
in `rove.yaml` for evaluation runs; use CPU mode on your Mac for mock-free dev
without JAX-Metal version wrestling.

### Known Issues and Gotchas

1. **JAX random key semantics**: JAX uses explicit functional randomness.
   Pass `jax.random.PRNGKey(seed)` to `sample_actions`. Use
   `jax.random.split(rng)` to derive new keys for different calls. Do not reuse
   the same key across calls if you want diverse actions.

2. **2-timestep window is mandatory**: Passing a 1-timestep observation raises a
   shape error. At episode start, duplicate `obs_t0` as `[obs_t0, obs_t0]`.

3. **First-call JIT compilation**: Budget 10-30 seconds for the first call.
   Always add a warm-up call before the evaluation loop. The JIT cache persists
   for the process lifetime but does not persist across Python restarts.

4. **`dataset_statistics` key selection**: `model.dataset_statistics` contains
   stats for every training dataset. For LIBERO tabletop tasks, `"bridge_dataset"`
   is a reasonable proxy (Franka arm, similar workspace scale).

5. **JAX arrays are not numpy/torch arrays**: The ROVE adapter must call
   `np.array(actions)` before building the `ActionPrediction` dataclass.
   JAX DeviceArrays serialize differently and will break the Pydantic model.

6. **Community PyTorch port exists**: `github.com/emb-ai/octo-pytorch` is an
   unofficial PyTorch port. It is not kept in sync with the main JAX repo and
   should only be considered if JAX is completely unavailable in your environment.

---

## ROVE Adapter Implementation Notes

### Translation Layer per Model

The ROVE `VLAAdapter` Protocol requires:
```python
async def predict_action(
    image: bytes,
    task: str,
    proprioception: list[float] | None,
) -> ActionPrediction
```

Each adapter needs to bridge that interface to the model's actual API:

**pi0 adapter (`local_openpi`)**
- On init: `policy_config.create_trained_policy(config, checkpoint_dir)`
- On call: `bytes` → `np.uint8 array` → observation dict → `policy.infer(obs)["actions"][0].tolist()`
- Return: `[[a1..a7]]` (first step wrapped in list for `list[list[float]]`)

**OpenVLA-OFT adapter (`local_openvla`)**
- On init: `AutoModelForVision2Seq.from_pretrained(...)` + `AutoProcessor.from_pretrained(...)`
- On call: `bytes` → `PIL.Image` → `processor(prompt, image)` → `vla.predict_action(**inputs, unnorm_key=...)`
- Return: `[[float(v) for v in action]]` — single step wrapped in list

**SmolVLA adapter (`local_lerobot`)**
- On init: `SmolVLAPolicy.from_pretrained("lerobot/smolvla_base")` + `make_pre_post_processors`
- On call: `bytes` → `np.uint8` → obs dict → `pre(obs)` → `policy.select_action(obs)` → `post(action)`
- Return: `[[float(v) for v in action]]`

**CogACT adapter (`local_cogact`)**
- On init: `load_vla("CogACT/CogACT-Base", ...)` with `model.vlm.to(torch.bfloat16)`
- On call: `bytes` → `PIL.Image` → `model.predict_action(image, prompt, num_ddim_steps=10)`
- Return: `actions.tolist()` — full chunk, shape `[16, 7]` as `list[list[float]]`

**Octo adapter (`local_octo`)**
- On init: `OctoModel.load_pretrained("hf://rail-berkeley/octo-base-1.5")` + warm-up call
- On call: maintain 2-step history buffer; `bytes` → `np.uint8 [256,256,3]` → stack into `[1,2,256,256,3]` → `model.sample_actions(obs, task, rng=...)`
- Return: `np.array(actions[0]).tolist()` — shape `[4, 7]` as `list[list[float]]`

### Realistic Latency Reference (for mock calibration)

The `rove.yaml` mock VLA uses `mock_latency_ms: [50, 150]`. Real model latencies
for evaluation planning (these should drive your mock distributions):

| Model          | MPS / CPU (Mac dev) | A100 80 GB | A10G 24 GB |
|----------------|---------------------|------------|------------|
| pi0            | Not recommended     | 20-50 ms/chunk | 40-100 ms/chunk |
| pi0-FAST       | Not recommended     | 80-200 ms/chunk | 150-400 ms/chunk |
| OpenVLA-OFT 7B | 10-20 s (CPU)       | 200-400 ms | 400-800 ms |
| SmolVLA 450M   | 30-70 ms (MPS)      | 10-30 ms   | 15-50 ms   |
| CogACT 7B      | Not supported       | 2000-5000 ms | 4000-10000 ms |
| Octo-Base 93M  | 300-800 ms (CPU)    | 75 ms      | 120 ms     |

For Phase 1 mock adapters, set latency distributions to match the target
GPU deployment latency, not local dev latency. The ROVE leaderboard ranks
on success rate first, then latency — mock latency drives that ranking.

---

## Sources

- [GitHub - Physical-Intelligence/openpi](https://github.com/Physical-Intelligence/openpi)
- [openpi examples/inference.ipynb](https://github.com/Physical-Intelligence/openpi/blob/main/examples/inference.ipynb)
- [openvla/openvla-7b on HuggingFace](https://huggingface.co/openvla/openvla-7b)
- [GitHub - moojink/openvla-oft](https://github.com/moojink/openvla-oft)
- [SmolVLA blog post - HuggingFace](https://huggingface.co/blog/smolvla)
- [SmolVLA docs - LeRobot](https://huggingface.co/docs/lerobot/smolvla)
- [lerobot using_smolvla_example.py](https://github.com/huggingface/lerobot/blob/main/examples/tutorial/smolvla/using_smolvla_example.py)
- [GitHub - microsoft/CogACT](https://github.com/microsoft/CogACT)
- [CogACT/CogACT-Base on HuggingFace](https://huggingface.co/CogACT/CogACT-Base)
- [GitHub - octo-models/octo](https://github.com/octo-models/octo)
- [rail-berkeley/octo-base-1.5 on HuggingFace](https://huggingface.co/rail-berkeley/octo-base-1.5)
- [octo examples/01_inference_pretrained.ipynb](https://github.com/octo-models/octo/blob/main/examples/01_inference_pretrained.ipynb)
- [jax-metal on PyPI](https://pypi.org/project/jax-metal/)
- [Accelerated JAX on Mac - Apple Developer](https://developer.apple.com/metal/jax/)
- [GitHub - ZibinDong/openpi_pytorch](https://github.com/ZibinDong/openpi_pytorch)
- [Production-Grade Local LLM Inference on Apple Silicon (arXiv 2511.05502)](https://arxiv.org/abs/2511.05502)
