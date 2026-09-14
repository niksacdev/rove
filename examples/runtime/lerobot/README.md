# Experimental local LeRobot runtime

Run a trusted local SmolVLA or Pi0.5 checkpoint through ROVE's existing VLA adapter, with its own Python dependencies. Trials and campaigns still use ROVE's execution, SQLite persistence, evidence and scoring. Each prediction starts a fresh offline worker, resets the policy, and returns native action values and provenance.

This runtime is **experimental and has known dependency advisories**. It is not a patched production deployment or a security sandbox. Keep it separate from ROVE's main environment. Only load checkpoints and native processor definitions you trust; LeRobot processor configuration selects executable processor classes.

## Install separately

From the repository root, with Python 3.12 and `uv` available:

```sh
uv venv --python 3.12 .rove/runtimes/lerobot/.venv
uv pip sync --python .rove/runtimes/lerobot/.venv/bin/python --require-hashes examples/runtime/lerobot/requirements.txt
```

This installs the independently resolved dependency set. It does not install ROVE, alter the root `.venv`, or download model weights. The lock was resolved for Python 3.12 and validated on native macOS Apple silicon. Native MPS works there; an ordinary Linux Docker container on a Mac does not provide the same MPS device. CUDA and CPU choices are explicit and have not been exercised in this integration.

## Prepare trusted assets

Use a separate directory for each checkpoint containing:

- `config.json` and `model.safetensors` from the same checkpoint revision.
- `policy_preprocessor.json` and `policy_postprocessor.json` from that revision.
- Every processor statistics file referenced by those JSON definitions.

Use a local directory containing the checkpoint-compatible tokenizer and its configuration. SmolVLA additionally uses its VLM configuration and processor assets. Existing Hugging Face cache files can be linked into these directories; the preparation tool hashes symlink contents. No remote model code is enabled. Downloads, license acceptance and access grants are separate preparation steps; inference is offline.

The locally validated assets were:

| Policy | Checkpoint | Native dimensions | Compatible tokenizer |
| --- | --- | --- | --- |
| SmolVLA | `lerobot/smolvla_base` at `c83c3163b8ca9b7e67c509fffd9121e66cb96205` | 6 state, 6 action | `HuggingFaceTB/SmolVLM2-500M-Video-Instruct` at `7b375e1b73b11138ff12fe22c8f2822d8fe03467` |
| Pi0.5 | `lerobot/pi05_libero_finetuned_v044` at `dbf8a3f794a9c4297b44f40b752712f50073d945` | 8 state, 7 action | Official gated `google/paligemma-3b-pt-224` |

SmolVLA uses `policy_preprocessor_step_5_normalizer_processor.safetensors`; Pi0.5 uses `policy_preprocessor_step_2_normalizer_processor.safetensors`. Both use `policy_postprocessor_step_0_unnormalizer_processor.safetensors`. Consult the actual JSON definitions when preparing another checkpoint.

After reviewing the assets, freeze their contents:

```sh
python3 examples/runtime/lerobot/freeze_manifest.py \
  .rove/runtimes/lerobot/checkpoints/smolvla \
  .rove/runtimes/lerobot/tokenizers/smolvla
```

The tool creates `rove-manifest.json` with SHA256 hashes and sizes for all checkpoint, native processor and tokenizer files. The worker rehashes and checks these files on every request. Changed, missing or added assets fail before inference; review and explicitly freeze a new manifest for an intended revision. The returned manifest digest identifies actual contents. Pin that digest in the endpoint as `manifest_sha256` and retain it in the frozen campaign configuration: the worker then rejects even deliberately refrozen assets when replaying an older configuration. Omitting this optional pin checks current local integrity but does not freeze future replays. `checkpoint_revision` is separately recorded as a declared upstream revision, not proof of its origin. These hashes provide local content identity, not publisher authentication or protection against a malicious local process modifying files during inference.

## Configure an endpoint

Use the existing `local_lerobot` adapter with explicit local paths. Paths below are relative to the ROVE process's working directory:

```yaml
adapter: local_lerobot
config:
  runtime_python: .rove/runtimes/lerobot/.venv/bin/python
  checkpoint_path: .rove/runtimes/lerobot/checkpoints/smolvla
  checkpoint_revision: c83c3163b8ca9b7e67c509fffd9121e66cb96205
  manifest_sha256: "<digest printed by freeze_manifest.py>"
  runtime_versions:
    torch: "2.11.0"
    lerobot: "0.6.1"
    transformers: "5.5.4"
    safetensors: "0.8.0"
  tokenizer_path: .rove/runtimes/lerobot/tokenizers/smolvla
  device: mps
  chunk_size: 2
  input_mode: strict
  runtime_timeout_s: 300
```

`runtime_versions` is optional for exploratory configurations, but when present must pin all four critical packages. The worker checks installed distribution versions before importing the ML runtime and records the actual versions in its evidence. Retain these pins in frozen campaign configurations so upgrading the isolated environment cannot silently change those dependencies during replay. These checks do not pin every transitive package, detect edits to installed package source, or freeze Python, operating system, accelerator drivers and nondeterministic kernels. Use the independent hash lock and record environment changes when comparing results; equal pins alone do not guarantee identical execution.

Keep the endpoint in an ordinary configured strategy and select that strategy in a trial or campaign. The CLI and UI use the same adapter. A health check verifies assets, dependencies and device availability; it does **not** load weights or prove successful inference.

`strict` requires the checkpoint's exact finite state vector. It never pads or truncates measured state to make an incompatible robot appear compatible. The current ROVE input bridge supplies one actual image to `image_key` (the first native camera by default); other views follow native missing-view behavior and are recorded as warnings. This bridge therefore does not yet provide a complete multi-camera observation. Choose a checkpoint and case whose robot, state ordering, action units and camera semantics actually match before interpreting action quality.

When an embodiment is supplied in strict mode, its state and action dimensions must match the native checkpoint. Optional endpoint declarations `robot_type`, `proprioception_space` and `action_space` must also agree with that embodiment. A dimensional match alone does not establish matching units or coordinate frames. Requests cannot exceed either the native `chunk_size` or `n_action_steps`; the same static image must not silently start another prediction chunk.

An optional endpoint `seed` seeds PyTorch in the worker and is recorded as `endpoint_seed`. Campaign repetition seeds are currently recorded as requested seeds and are not automatically propagated to this worker. Neither path claims deterministic execution: reports retain `seed_guarantee: requested_only`. Use repeated trials to measure variation; do not interpret matching requested seeds as proof of paired deterministic inference.

For an explicitly named **image-only observation probe**, set `input_mode: observation_probe`. Missing state is then replaced with labeled zeros; supplying a wrong-sized state still fails. The output includes all assumptions, `execution_eligible: false`, unknown action space and `physical_execution: false`. Such a probe proves the model produces actions from that input, not that it completed the task or can safely control the depicted robot. It must not be used for FK or physical-success claims. Confidence is unavailable and is recorded as such, rather than invented from flow-matching outputs.

## Validation and current limits

Native SmolVLA inference completed on MPS with cached safetensors, checkpoint-native normalization/denormalization and a supplied ROVE sample image. The result contained finite native 6D actions. No model weight download was needed. The generic SmolVLA checkpoint is a base policy; this check does not establish task success or suitability for a different embodiment.

Pi0.5 weights and native statistics are available locally, but actual inference remains unvalidated because the current Hugging Face credentials receive HTTP 403 for the official gated tokenizer. The account needs authorized tokenizer access. ROVE's Pi loader directly loads safetensors, applies native key remapping and requires a strict state-dictionary match; it propagates loading errors because the upstream loader can catch them and return an incompletely initialized model.

The worker avoids training, dataset export, tokenizer saving, `torch.jit.script`, compilation, remote code and robot execution. It receives a filtered environment without cloud-provider credentials, but can still access files available to its OS user. Offline environment settings are not an OS network sandbox.

## Known dependency advisories

The independently audited lock contains these unresolved upstream constraints:

| Locked dependency | Advisory | Patched floor | Relevant operation |
| --- | --- | --- | --- |
| `datasets==4.8.5` | CVE-2026-66007 | 5.0.1 | Folder-based dataset metadata paths during export |
| `transformers==5.5.4` | CVE-2026-9856 | 5.10.0 | Saving a tokenizer with malicious template names |
| `torch==2.11.0` | CVE-2025-3000 | 2.13.0 | `torch.jit.script` memory corruption |
| `setuptools==81.0.0` | CVE-2026-59890 | 83.0.0 | Source-distribution manifest exclusion handling |

The intended inference path does not invoke those operations. That limits their reachability; it does not patch the packages or establish that no other vulnerabilities exist. LeRobot's declared compatible ranges prevent raising every dependency to those patched floors in this lock. Reassess upstream compatibility and rerun an advisory audit before using a future runtime revision or expanding its responsibilities. Do not copy these constraints into ROVE's main environment or use this runtime to train, package or export customer data.

Upstream references: [SmolVLA checkpoint](https://huggingface.co/lerobot/smolvla_base), [Pi0.5 integration and input contract](https://huggingface.co/docs/lerobot/pi05), [LeRobot dependency declarations](https://github.com/huggingface/lerobot/blob/v0.6.1/pyproject.toml).
