"""One offline inference request in an isolated LeRobot environment.

This script intentionally imports no ROVE modules. Its interpreter owns a separate
dependency lock. It uses trusted local safetensors and checkpoint-native processors;
it does not train, export datasets, save tokenizers, compile, or execute robot actions.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import sys
import time
from importlib.metadata import version
from pathlib import Path

RUNTIME_PACKAGES = ("torch", "lerobot", "transformers", "safetensors")


def verify_runtime_versions(expected: dict | None = None) -> dict[str, str]:
    """Reject upgrades behind a frozen endpoint before importing the ML runtime."""
    if expected is not None and (
        not isinstance(expected, dict)
        or set(expected) != set(RUNTIME_PACKAGES)
        or any(not isinstance(value, str) or not value for value in expected.values())
    ):
        raise ValueError("runtime_versions must pin torch, lerobot, transformers and safetensors")
    actual = {name: version(name) for name in RUNTIME_PACKAGES}
    if expected is not None:
        for name, installed in actual.items():
            if expected[name] != installed:
                raise ValueError(
                    f"VLA runtime {name} version {installed} differs from pinned {expected[name]}"
                )
    return actual


def asset_manifest(checkpoint: Path, tokenizer: Path) -> dict:
    """Hash all local model/processor/tokenizer assets without embedding host paths."""
    roots = {"checkpoint": checkpoint, "tokenizer": tokenizer}
    assets = {}
    for role, root in roots.items():
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name == "rove-manifest.json":
                continue
            before = path.stat()
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                    digest.update(block)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ino,
            ):
                raise ValueError("VLA asset changed while its digest was calculated")
            assets[f"{role}/{path.relative_to(root).as_posix()}"] = {
                "size": after.st_size,
                "sha256": digest.hexdigest(),
            }
    return {"version": 1, "assets": assets}


def verify_asset_manifest(
    checkpoint: Path, tokenizer: Path, expected_sha256: str | None = None
) -> str:
    manifest = checkpoint / "rove-manifest.json"
    if not manifest.is_file() or manifest.stat().st_size > 1_000_000:
        raise ValueError("Create rove-manifest.json with the local VLA preparation tool first")
    declared = json.loads(manifest.read_text())
    actual = asset_manifest(checkpoint, tokenizer)
    if declared != actual:
        raise ValueError("Local VLA assets changed; inspect and explicitly freeze a new manifest")
    canonical = json.dumps(actual, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    if expected_sha256 is not None and expected_sha256 != digest:
        raise ValueError("VLA asset manifest differs from the pinned endpoint manifest_sha256")
    return digest


def load_policy(policy_class, policy_config, checkpoint: Path):
    """Fail closed if any Pi weights fail to load; upstream catches those errors."""
    if policy_config.type != "pi05":
        return policy_class.from_pretrained(
            checkpoint, config=policy_config, local_files_only=True, strict=True
        )
    from safetensors.torch import load_file

    policy = policy_class(policy_config)
    tensors = load_file(str(checkpoint / "model.safetensors"))
    fixed = policy._fix_pytorch_state_dict_keys(tensors, policy_config)
    remapped = {
        (key if key.startswith("model.") else f"model.{key}"): value for key, value in fixed.items()
    }
    policy.load_state_dict(remapped, strict=True)
    return policy


def observation_contract(
    config: dict, request: dict, policy_config: dict
) -> tuple[list, str, list]:
    """Validate actual state; zero state is allowed only for an explicit input probe."""
    mode = config.get("input_mode", "strict")
    if mode not in {"strict", "observation_probe"}:
        raise ValueError("input_mode must be strict or observation_probe")
    features = policy_config["input_features"]
    expected = features["observation.state"]["shape"][0]
    state = request.get("proprioception")
    warnings = []
    if state is None or state == []:
        if mode != "observation_probe":
            raise ValueError(f"This checkpoint requires {expected} measured state values")
        state = [0.0] * expected
        warnings.append(
            "Zero state supplied for an observation probe; it is not measured robot state."
        )
    if not isinstance(state, list) or len(state) != expected:
        raise ValueError(
            f"Checkpoint expects exactly {expected} state values; no truncation or padding is applied"
        )
    if any(
        isinstance(x, bool) or not isinstance(x, int | float) or not math.isfinite(x) for x in state
    ):
        raise ValueError("State must contain finite numbers")
    embodiment = request.get("embodiment")
    if embodiment and mode == "strict":
        arm_dof = embodiment.get("arm_dof")
        gripper_dof = embodiment.get("gripper_dof", 1)
        if any(
            isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in (arm_dof, gripper_dof)
        ):
            raise ValueError("Embodiment must declare valid arm and gripper dimensions")
        state_dim = embodiment.get("state_dim_override") or arm_dof + gripper_dof
        if state_dim != expected:
            raise ValueError("Supplied embodiment state dimension does not match the checkpoint")
        action_dim = policy_config["output_features"]["action"]["shape"][0]
        if arm_dof + gripper_dof != action_dim:
            raise ValueError("Supplied embodiment action dimension does not match the checkpoint")
        for key in ("robot_type", "proprioception_space", "action_space"):
            declared = config.get(key)
            if key == "action_space" and declared == "ee_delta":
                declared = "eef_delta"
            if declared and embodiment.get(key) != declared:
                raise ValueError(f"Supplied embodiment {key} does not match endpoint contract")
        warnings.append(
            "Embodiment dimensions were checked; robot identity, units and frames require "
            "a compatible checkpoint and explicit endpoint declarations."
        )
    cameras = [
        key
        for key, value in features.items()
        if value.get("type") == "VISUAL" and "empty_camera" not in key
    ]
    if not cameras:
        raise ValueError("Checkpoint declares no camera input")
    key = config.get("image_key", cameras[0])
    if key not in cameras:
        raise ValueError("Configured image_key is not a checkpoint camera")
    missing = [camera for camera in cameras if camera != key]
    if missing:
        warnings.append(
            "Unsupplied camera views use the policy's native missing-view behavior: "
            + ", ".join(missing)
        )
    if mode == "observation_probe":
        warnings.append(
            "Observation probe: model outputs are not evidence of successful physical execution."
        )
    return state, key, warnings


def action_horizon(config: dict, policy_config: dict) -> int:
    """Never ask a static observation to generate a second native action chunk."""
    steps = config.get("chunk_size", 1)
    limit = min(policy_config.get("chunk_size", 50), policy_config.get("n_action_steps", 50))
    if isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= limit:
        raise ValueError("chunk_size must be within both checkpoint chunk_size and n_action_steps")
    return steps


def run(request: dict) -> dict:
    config = request["config"]
    runtime_versions = verify_runtime_versions(config.get("runtime_versions"))
    checkpoint = Path(config.get("checkpoint_path", "")).expanduser().absolute()
    if not checkpoint.is_dir() or not (checkpoint / "model.safetensors").is_file():
        raise ValueError("A local checkpoint directory with model.safetensors is required")
    config_path = checkpoint / "config.json"
    if config_path.stat().st_size > 256_000:
        raise ValueError("Checkpoint configuration is too large")
    raw_config = json.loads(config_path.read_text())
    if raw_config.get("type") not in {"smolvla", "pi05"}:
        raise ValueError("This isolated worker supports smolvla and pi05 checkpoints")
    for name in ("policy_preprocessor.json", "policy_postprocessor.json"):
        if not (checkpoint / name).is_file():
            raise ValueError(f"Checkpoint-native {name} is missing")
    tokenizer = Path(config.get("tokenizer_path", "")).expanduser().absolute()
    if not (tokenizer / "tokenizer_config.json").is_file():
        raise ValueError("A local checkpoint-compatible tokenizer_path is required")

    manifest_sha256 = verify_asset_manifest(checkpoint, tokenizer, config.get("manifest_sha256"))

    import torch
    from lerobot.configs import PreTrainedConfig
    from lerobot.policies.factory import get_policy_class, make_pre_post_processors

    device = config.get("device", "mps")
    if device not in {"cpu", "mps", "cuda"}:
        raise ValueError("Unsupported VLA device")
    if device == "mps" and not torch.backends.mps.is_available():
        raise ValueError(
            "Apple MPS is unavailable to this process; select cpu explicitly if intended"
        )
    if device == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA is unavailable to this process")
    if request.get("operation") == "health":
        get_policy_class(raw_config["type"])
        return {
            "ready": True,
            "device": device,
            "policy_type": raw_config["type"],
            "weights_loaded": False,
            "packages": runtime_versions,
        }
    if request.get("operation") != "predict":
        raise ValueError("Unsupported worker operation")
    state, image_key, warnings = observation_contract(config, request, raw_config)
    requested_steps = action_horizon(config, raw_config)

    import numpy as np
    from PIL import Image

    encoded = request.get("image_base64", "")
    raw_image = base64.b64decode(encoded, validate=True)
    with Image.open(io.BytesIO(raw_image)) as source:
        if source.width * source.height > 16_000_000:
            raise ValueError("Image exceeds 16 million pixels")
        pixels = np.asarray(source.convert("RGB")).copy()
    policy_config = PreTrainedConfig.from_pretrained(checkpoint, local_files_only=True)
    policy_config.device = device
    if hasattr(policy_config, "compile_model"):
        policy_config.compile_model = False
    if hasattr(policy_config, "dtype"):
        policy_config.dtype = "float32" if device in {"cpu", "mps"} else policy_config.dtype
    if policy_config.type == "smolvla":
        # The full policy checkpoint already contains its vision-language weights.
        policy_config.load_vlm_weights = False
        policy_config.vlm_model_name = str(tokenizer)
    seed = config.get("seed")
    if seed is not None:
        torch.manual_seed(int(seed))
    started = time.monotonic()
    policy = load_policy(get_policy_class(policy_config.type), policy_config, checkpoint)
    if device in {"cpu", "mps"}:
        policy = policy.to(dtype=torch.float32)
    policy.eval()
    preprocessor, postprocessor = make_pre_post_processors(
        policy_config,
        str(checkpoint),
        preprocessor_overrides={
            "device_processor": {"device": device},
            "tokenizer_processor": {"tokenizer_name": str(tokenizer)},
        },
        postprocessor_overrides={"device_processor": {"device": "cpu"}},
    )
    observation = {
        image_key: torch.from_numpy(pixels).permute(2, 0, 1).float() / 255,
        "observation.state": torch.tensor(state, dtype=torch.float32),
        "task": request["task"],
    }
    batch = preprocessor(observation)
    policy.reset()
    actions = []
    with torch.inference_mode():
        for _ in range(requested_steps):
            action = postprocessor(policy.select_action(batch)).detach().cpu().reshape(-1)
            if not torch.isfinite(action).all():
                raise ValueError("Policy returned nonfinite actions")
            actions.append(action.tolist())
    action_dim = raw_config["output_features"]["action"]["shape"][0]
    if any(len(action) != action_dim for action in actions):
        raise ValueError("Policy output dimension differs from the checkpoint contract")
    metadata = {
        "runtime": "isolated_lerobot",
        "device": device,
        "policy_type": policy_config.type,
        "checkpoint_revision": config.get("checkpoint_revision"),
        "checkpoint_revision_verified": False,
        "asset_manifest_sha256": manifest_sha256,
        "asset_manifest_verified": True,
        "checkpoint_config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "packages": runtime_versions,
        "input_mode": config.get("input_mode", "strict"),
        "warnings": warnings,
        "native_processors": True,
        "reset_per_trial": True,
        "confidence_available": False,
        "elapsed_s": time.monotonic() - started,
        "physical_execution": False,
        "endpoint_seed": seed,
        "seed_guarantee": "requested_only",
    }
    action_space = config.get("action_space")
    if config.get("input_mode") == "observation_probe":
        # A user-facing probe must not accidentally enable physical grading through
        # a copied endpoint's action-space declaration.
        action_space = None
    if action_space == "ee_delta":
        action_space = "eef_delta"
    if action_space not in {None, "eef_delta", "eef_absolute", "joint_position", "joint_delta"}:
        raise ValueError("Unknown configured action space")
    return {
        "actions": actions,
        "action_type": "trajectory",
        "execution_eligible": config.get("input_mode", "strict") != "observation_probe",
        "input_assumptions": warnings,
        "num_steps": len(actions),
        "confidence": 0.0,
        "raw_response": json.dumps(metadata, allow_nan=False),
        "declared_action_dim": action_dim,
        "raw_action_dim": action_dim,
        "action_space": action_space,
        "gripper_index": None,
    }


def main() -> None:
    source, output = map(Path, sys.argv[1:])
    try:
        if source.stat().st_size > 16_000_000:
            raise ValueError("VLA request exceeds 16 MB")
        result = run(json.loads(source.read_text()))
        output.write_text(json.dumps(result, allow_nan=False))
    except Exception as exc:
        output.write_text(json.dumps({"error": f"{type(exc).__name__}: {str(exc)[:2000]}"}))
        raise


if __name__ == "__main__":
    main()
