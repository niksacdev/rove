"""Inspect or explicitly execute a pi0.5 adaptation recipe in its isolated runtime.

This prints a validated plan by default. Add --execute only on a CUDA machine.
The base checkpoint must already be downloaded to a local directory. The gated
PaliGemma tokenizer also requires its upstream license/access and local login.
No model weights or dataset are uploaded; training is external to ROVE's engine.
"""

from __future__ import annotations

import argparse
import json
import math
import runpy
import sys
from datetime import UTC, datetime
from pathlib import Path

from dataset import (
    ABC_REVISION,
    CAMERAS,
    JOINTS,
    LEROBOT_REVISION,
    sha256,
    verify_lerobot_revision,
    write_json,
)

PORTABLE_MANIFEST = "rove_training_manifest.json"


def validate_dataset(root):
    root = Path(root).resolve(strict=True)
    manifest = json.loads((root / "rove_dataset_manifest.json").read_text())
    if (
        manifest.get("schema") != "rove.abc-pi05.dataset/v1"
        or manifest.get("status") != "completed"
    ):
        raise ValueError("Requires a successfully converted ABC training dataset")
    if manifest.get("joint_names") != JOINTS or manifest.get("camera_keys") != list(CAMERAS):
        raise ValueError("The dataset's robot/camera contract does not match this recipe")
    episodes = manifest.get("episodes", [])
    if not episodes or any(
        ep.get("source_split") not in ("train_real", "train_sim") for ep in episodes
    ):
        raise ValueError("Training dataset contains missing or forbidden source splits")
    if not manifest.get("output_files"):
        raise ValueError("Dataset output fingerprints are missing")
    for relative, expected in manifest["output_files"].items():
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root) or sha256(path) != expected:
            raise ValueError(f"Dataset changed after conversion: {relative}")
    info = json.loads((root / "meta/info.json").read_text())
    if info.get("fps") != 30 or info.get("total_episodes") != len(episodes):
        raise ValueError("Dataset metadata disagrees with the frozen training manifest")
    for key in ("observation.state", "action"):
        if info.get("features", {}).get(key, {}).get("shape") != [14]:
            raise ValueError(f"Dataset feature {key} must contain 14 values")
    stats = json.loads((root / "meta/stats.json").read_text())
    for key in ("observation.state", "action"):
        for quantile in ("q01", "q99"):
            values = stats.get(key, {}).get(quantile, [])
            if len(values) != 14 or not all(
                isinstance(value, int | float) and math.isfinite(value) for value in values
            ):
                raise ValueError(f"Missing valid {quantile} training-only statistics for {key}")
    return root, manifest


def training_plan(dataset, base_checkpoint, output, *, steps=30000, batch_size=1, seed=42):
    if type(steps) is not int or steps < 1 or type(batch_size) is not int or batch_size < 1:
        raise ValueError("Training steps and batch size must be positive integers")
    root, manifest = validate_dataset(dataset)
    base = Path(base_checkpoint).resolve(strict=True)
    config = json.loads((base / "config.json").read_text())
    if config.get("type") != "pi05":
        raise ValueError("Start from a local pi0.5 base checkpoint")
    weights = base / "model.safetensors"
    if not weights.is_file() or weights.stat().st_size == 0:
        raise ValueError("The local base checkpoint lacks model.safetensors")
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Training output already exists; choose a new checkpoint version")
    args = [
        f"--dataset.repo_id={manifest['repo_id']}",
        f"--dataset.root={root}",
        "--dataset.video_backend=pyav",
        "--dataset.eval_split=0",
        "--dataset.image_transforms.enable=false",
        "--policy.type=pi05",
        f"--policy.pretrained_path={base}",
        "--policy.chunk_size=50",
        "--policy.n_action_steps=15",
        "--policy.empty_cameras=0",
        '--policy.normalization_mapping={"ACTION":"QUANTILES","STATE":"QUANTILES","VISUAL":"IDENTITY"}',
        "--policy.use_relative_actions=false",
        "--policy.use_visual_memory=false",
        "--policy.use_proprioceptive_memory=false",
        "--policy.freeze_vision_encoder=true",
        "--policy.train_expert_only=true",
        "--policy.gradient_checkpointing=true",
        "--policy.dtype=bfloat16",
        "--policy.device=cuda",
        "--policy.push_to_hub=false",
        "--wandb.enable=false",
        "--num_workers=0",
        f"--output_dir={output}",
        "--job_name=abc_pi05",
        f"--batch_size={batch_size}",
        f"--steps={steps}",
        f"--save_freq={min(5000, steps)}",
        "--env_eval_freq=0",
        "--eval_steps=0",
        f"--seed={seed}",
    ]
    return {
        "schema": "rove.abc-pi05.training/v1",
        "status": "planned",
        "abc_revision": ABC_REVISION,
        "lerobot_revision": LEROBOT_REVISION,
        "recipe_source_sha256": {
            "train.py": sha256(Path(__file__)),
            "dataset.py": sha256(Path(__file__).with_name("dataset.py")),
            "pi05_weights.py": sha256(
                Path(__file__).resolve().parents[2] / "src/rove/bimanual/pi05_weights.py"
            ),
        },
        "dataset_manifest_sha256": sha256(root / "rove_dataset_manifest.json"),
        "base_weights_sha256": sha256(weights),
        "base_config_sha256": sha256(base / "config.json"),
        "dataset_episodes": len(manifest["episodes"]),
        "source_splits": sorted({ep["source_split"] for ep in manifest["episodes"]}),
        "prompts": sorted({ep["prompt"] for ep in manifest["episodes"]}),
        "training_steps": steps,
        "native_training_arguments": args,
        "output": str(output),
        "seed": seed,
        "normalization": "LeRobot training-only per-episode quantile bounds, saved with checkpoint processors",
        "evaluation": "Run independent ABC simulator episodes through ROVE after training; no success score is inferred from training loss.",
        "limitations": "Preview data and short runs test the integration; task specialization and relative performance require measured held-out simulation episodes.",
    }


def write_checkpoint_provenance(checkpoint, plan):
    """Make the copied checkpoint independently traceable, without local file paths."""
    checkpoint = Path(checkpoint).resolve(strict=True)
    manifest_path = checkpoint / PORTABLE_MANIFEST
    if manifest_path.exists():
        raise ValueError("Checkpoint already has a training manifest; choose a new version")
    checkpoint_files = {}
    for path in sorted(checkpoint.rglob("*")):
        if path.is_file():
            if not path.resolve().is_relative_to(checkpoint):
                raise ValueError("Checkpoint file points outside its version directory")
            checkpoint_files[str(path.relative_to(checkpoint))] = sha256(path)
    # Explicit fields exclude CLI arguments, output paths and runtime-install paths.
    portable = {
        "schema": "rove.abc-pi05.checkpoint-training/v1",
        **{
            key: plan[key]
            for key in (
                "abc_revision",
                "lerobot_revision",
                "recipe_source_sha256",
                "dataset_manifest_sha256",
                "base_weights_sha256",
                "base_config_sha256",
                "dataset_episodes",
                "source_splits",
                "prompts",
                "training_steps",
                "seed",
            )
        },
        "checkpoint_files": checkpoint_files,
    }
    encoded = json.dumps(portable, indent=2, sort_keys=True, allow_nan=False).encode()
    if len(encoded) > 1_000_000:
        raise ValueError("Portable checkpoint provenance exceeds 1 MB")
    manifest_path.write_bytes(encoded + b"\n")
    # Include the manifest in the outer receipt and ROVE's checkpoint fingerprint.
    return {**checkpoint_files, PORTABLE_MANIFEST: sha256(manifest_path)}


def execute(plan):
    import torch

    # Import only the dependency-light loader, without installing ROVE into this runtime.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/rove/bimanual"))
    from pi05_weights import install_strict_training_loader

    plan["lerobot_runtime"] = verify_lerobot_revision()
    if not torch.cuda.is_available():
        raise RuntimeError("Training requires a CUDA GPU; the plan can be inspected without one")
    output = Path(plan["output"])
    receipt = output.parent / f"{output.name}.training.json"
    if output.exists() or receipt.exists():
        raise ValueError("This training output or receipt already exists; choose a new version")
    output.parent.mkdir(parents=True, exist_ok=True)
    plan["started_at"] = datetime.now(UTC).isoformat()
    plan["status"] = "running"
    write_json(receipt, plan)
    saved_argv = sys.argv[:]
    try:
        install_strict_training_loader()
        sys.argv = ["lerobot-train", *plan["native_training_arguments"]]
        runpy.run_module("lerobot.scripts.lerobot_train", run_name="__main__")
        checkpoint = output / "checkpoints/last/pretrained_model"
        required = [
            "config.json",
            "model.safetensors",
            "policy_preprocessor.json",
            "policy_postprocessor.json",
        ]
        if any(not (checkpoint / file).is_file() for file in required):
            raise RuntimeError(
                "Training returned without the expected model and saved processor checkpoint"
            )
        plan["checkpoint"] = str(checkpoint.resolve())
        plan["checkpoint_files"] = write_checkpoint_provenance(checkpoint, plan)
        plan["status"] = "completed"
    except BaseException as exc:
        plan["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        plan["error_type"] = type(exc).__name__
        raise
    finally:
        sys.argv = saved_argv
        plan["finished_at"] = datetime.now(UTC).isoformat()
        write_json(receipt, plan)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--base-checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=30000)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    plan = training_plan(
        args.dataset,
        args.base_checkpoint,
        args.output,
        steps=args.steps,
        batch_size=args.batch_size,
        seed=args.seed,
    )
    if args.execute:
        execute(plan)
    print(json.dumps(plan, indent=2))


if __name__ == "__main__":
    main()
