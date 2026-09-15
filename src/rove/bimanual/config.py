"""Local model profiles and frozen comparison plans; no model downloads on launch."""

from __future__ import annotations

import hashlib
import json
import subprocess  # nosec B404 - local setup and fixed runtime probes only
from pathlib import Path
from typing import Literal

from pydantic import Field

from rove.benchmarks.models import CampaignSpec, fingerprint
from rove.evaluation.models import BoundedModel, EvaluationCase, EvaluationConfig

ABC_REVISION = "d0e987b61b376f1a1b8777b19149f190b2ba6939"  # pragma: allowlist secret
TASK = "put_plastic_bottles_in_bin"
PROMPT = "sim throw plastic bottles in bin"
HERE = Path(__file__).resolve().parent


class Environment(BoundedModel):
    type: Literal["abc-bimanual"] = "abc-bimanual"
    source: str = Field(min_length=1)
    source_revision: Literal[ABC_REVISION] = ABC_REVISION
    source_digest: str = ""
    python: str = Field(min_length=1)
    runtime_digest: str = ""
    task: Literal["put_plastic_bottles_in_bin"] = TASK
    prompt: str = Field(default=PROMPT, min_length=1, max_length=1000)
    max_steps: int = Field(default=3540, ge=1, le=10000)
    execute_steps: int = Field(default=15, ge=1, le=30)
    camera_height: Literal[168] = 168
    camera_width: Literal[224] = 224
    physics_dt: Literal[0.002] = 0.002
    control_decimation: Literal[17] = 17
    success_mode: Literal["ever", "final"] = "ever"


class Strategy(BoundedModel):
    kind: Literal["abc_vla", "pi05"]
    label: str = Field(min_length=1, max_length=160)
    checkpoint: str = Field(min_length=1)
    checkpoint_digest: str = ""
    python: str = Field(min_length=1)
    runtime_digest: str = ""
    device: Literal["cuda"] = "cuda"


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_digest(path: Path) -> str:
    if path.is_file():
        return file_digest(path)
    if not path.is_dir():
        raise ValueError("Checkpoint/source directory is missing")
    # Include processor stats, configuration, weights and model-specific assets.
    entries = {
        str(item.relative_to(path)): file_digest(item)
        for item in sorted(path.rglob("*"))
        if item.is_file() and "__pycache__" not in item.parts and ".git" not in item.parts
    }
    if not entries:
        raise ValueError("Checkpoint/source directory is empty")
    return fingerprint(entries)


def source_digest(source: str) -> str:
    path = Path(source)
    result = subprocess.run(  # nosec B603 B607 -- trusted local repository; no shell
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    if result.stdout.strip() != ABC_REVISION:
        raise ValueError("ABC checkout must use the documented pinned revision")
    return fingerprint({name: tree_digest(path / name) for name in ("abc_sim", "abc_minimal")})


def runtime_digest(python: str) -> str:
    path = Path(python)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("Configure an absolute path to an installed Python interpreter")
    result = subprocess.run(  # nosec B603 -- explicitly configured local interpreter
        [str(path), str(HERE / "runtime_probe.py")],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return fingerprint(json.loads(result.stdout))


def read_profile(root: Path) -> EvaluationConfig:
    path = root / "bimanual.json"
    if path.stat().st_size > 1_000_000:
        raise ValueError("Bimanual profile exceeds 1 MB")
    return EvaluationConfig.model_validate_json(path.read_text())


def runtime_health(python: str, kind: str, source: str) -> list[str]:
    result = subprocess.run(  # nosec B603 -- registered local runtime, fixed probe
        [python, str(HERE / "runtime_probe.py"), "health", kind, source],
        capture_output=True,
        text=True,
        check=True,
        timeout=90,
    )
    return json.loads(result.stdout)["issues"]


def freeze_profile(config: EvaluationConfig) -> EvaluationConfig:
    """Explicit local setup fingerprints installed assets, not browser supplied paths."""
    env = Environment.model_validate(config.environment)
    env.source = str(Path(env.source).expanduser().resolve())
    env.python = str(Path(env.python).expanduser().absolute())
    env.source_digest = source_digest(env.source)
    env.runtime_digest = runtime_digest(env.python)
    strategies = {}
    for sid, value in config.strategies.items():
        strategy = Strategy.model_validate(value)
        strategy.checkpoint = str(Path(strategy.checkpoint).expanduser().resolve())
        strategy.python = str(Path(strategy.python).expanduser().absolute())
        strategy.checkpoint_digest = tree_digest(Path(strategy.checkpoint))
        strategy.runtime_digest = runtime_digest(strategy.python)
        strategies[sid] = strategy.model_dump(mode="json")
    return EvaluationConfig(
        environment=env.model_dump(mode="json"),
        strategies=strategies,
        grading={"success_mode": env.success_mode},
    )


def validate_profile(config: EvaluationConfig, verify_assets: bool = True) -> dict:
    env = Environment.model_validate(config.environment)
    issues = []
    try:
        if not env.source_digest or not env.runtime_digest:
            raise ValueError("Register installed runtimes and checkpoints with rove bimanual setup")
        if verify_assets and (
            source_digest(env.source) != env.source_digest
            or runtime_digest(env.python) != env.runtime_digest
        ):
            raise ValueError("ABC source or runtime changed; register a new profile")
        if verify_assets:
            issues.extend(runtime_health(env.python, "abc_vla", env.source))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        issues.append(str(error) if isinstance(error, ValueError) else "ABC runtime is unavailable")
    rows = []
    for sid, value in config.strategies.items():
        strategy = Strategy.model_validate(value)
        problems = []
        try:
            checkpoint = Path(strategy.checkpoint)
            if not strategy.checkpoint_digest or not strategy.runtime_digest:
                raise ValueError("Checkpoint and runtime have not been registered")
            if strategy.kind == "pi05":
                for name in ("config.json", "model.safetensors"):
                    if not (checkpoint / name).is_file():
                        raise ValueError("Use a locally adapted pi0.5 checkpoint with 14 actions")
                model = json.loads((checkpoint / "config.json").read_text())
                if model.get("output_features", {}).get("action", {}).get("shape") != [14]:
                    raise ValueError("pi0.5 checkpoint must be trained for ABC's 14 actions")
            elif not checkpoint.is_file():
                raise ValueError("ABC-VLA requires a local checkpoint file")
            if strategy.kind == "abc_vla" and strategy.python != env.python:
                raise ValueError("ABC-VLA must use the shared ABC interpreter")
            if verify_assets and (
                tree_digest(checkpoint) != strategy.checkpoint_digest
                or runtime_digest(strategy.python) != strategy.runtime_digest
            ):
                raise ValueError("Checkpoint or runtime changed; register a new profile")
            if verify_assets and strategy.kind == "pi05":
                problems.extend(runtime_health(strategy.python, "pi05", env.source))
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            problems.append(
                str(error) if isinstance(error, ValueError) else "Model runtime is unavailable"
            )
        rows.append(
            {
                "id": sid,
                "label": strategy.label,
                "kind": strategy.kind,
                "checkpoint_revision": strategy.checkpoint_digest,
                "ready": not problems and not issues,
                "issues": problems + issues,
            }
        )
    return {
        "ready": not issues and all(r["ready"] for r in rows),
        "strategies": rows,
        "issues": issues,
    }


def build_spec(
    config: EvaluationConfig,
    *,
    name: str,
    strategy_ids: list[str],
    reset_seeds: list[int],
    policy_seeds: list[int],
    max_steps: int,
) -> tuple[CampaignSpec, EvaluationConfig]:
    env = Environment.model_validate(config.environment)
    env.max_steps = max_steps
    # Assignment is deliberately revalidated before freezing the public request.
    env = Environment.model_validate(env.model_dump())
    if len(reset_seeds) != len(set(reset_seeds)) or any(
        type(seed) is not int or not 0 <= seed <= 2**32 - 1 for seed in reset_seeds
    ):
        raise ValueError("Scene seeds must be unique unsigned 32-bit integers")
    if any(sid not in config.strategies for sid in strategy_ids):
        raise ValueError("Select registered strategies")
    selected = EvaluationConfig(
        strategies={sid: config.strategies[sid] for sid in strategy_ids},
        environment=env.model_dump(mode="json"),
        grading={"success_mode": env.success_mode},
    )
    spec = CampaignSpec(
        name=name,
        suite_version=f"abc-{ABC_REVISION[:12]}",
        revision="1",
        execution="abc-bimanual",
        grading="executor_assessment",
        strategies=strategy_ids,
        tasks=[
            EvaluationCase(
                id=f"bottles-scene-{seed}",
                task=env.prompt,
                inputs={"task": env.task, "reset_seed": seed},
            )
            for seed in reset_seeds
        ],
        seeds=policy_seeds,
        ks=[k for k in (1, 3, 5) if k <= len(policy_seeds)],
        timeout_s=3600,
    )
    return spec, selected
