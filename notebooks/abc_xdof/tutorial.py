"""Small, testable file/process helpers for the standalone ABC notebook."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess  # nosec B404 - trusted notebook commands, argument lists, no shell
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

ABC_REVISION = (
    "d0e987b61b376f1a1b8777b19149f190b2ba6939"  # pragma: allowlist secret - public Git commit
)
ABC_REPOSITORY = "https://github.com/amazon-far/abc.git"
TASK = "put_plastic_bottles_in_bin"
PARENT_NAME = "vla_abc130k_200000_v2.pt"


@contextmanager
def tracked_stage(statuses: dict, name: str):
    """A failed or interrupted rerun must not inherit an earlier completed state."""
    statuses[name] = "running"
    try:
        yield
    except BaseException as exc:
        statuses[name] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        raise
    else:
        statuses[name] = "completed"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def run_command(argv, *, cwd: Path, log: Path, env=None) -> None:
    """Stream output, preserve exit status, and stop the process tree on interrupt.

    Commands are argument lists, never shell expressions. No credentials belong
    in arguments; the notebook uses public prepared data and disables W&B.
    """
    log.parent.mkdir(parents=True, exist_ok=True)
    record = {"argv": [str(a) for a in argv], "started": datetime.now(UTC).isoformat()}
    command_env = {**os.environ, "PYTHONUNBUFFERED": "1", "WANDB_MODE": "disabled"}
    command_env.update(env or {})
    with log.open("w") as output:
        process = subprocess.Popen(  # nosec B603 - caller is the trusted notebook, never a remote request
            record["argv"],
            cwd=cwd,
            env=command_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            for line in process.stdout:
                output.write(line)
                output.flush()
                print(line, end="", flush=True)
            code = process.wait()
            record["status"] = "completed" if code == 0 else "failed"
            record["returncode"] = code
        except BaseException:
            record["status"] = "interrupted"
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            raise
        finally:
            process.stdout.close()
            record["finished"] = datetime.now(UTC).isoformat()
            write_json(log.with_suffix(".command.json"), record)
    if code:
        raise RuntimeError(f"Command failed (exit {code}). Read {log}")


def read_episode(episode: Path):
    """Read only the pinned ABC prepared export, with strict shape validation."""
    import numpy as np

    metadata = json.loads((episode / "episode_metadata.json").read_text())
    binary = episode / "states_actions.bin"
    if binary.stat().st_size == 0 or binary.stat().st_size % (28 * 8):
        raise ValueError("Expected complete float64 rows: 14 states + 14 actions")
    rows = np.fromfile(binary, dtype=np.float64).reshape(-1, 28)
    if metadata.get("num_steps", len(rows)) != len(rows):
        raise ValueError("Video-grid row count does not match episode metadata")
    if not np.isfinite(rows).all():
        raise ValueError("Episode contains non-finite state/action values")
    cameras = metadata.get("cameras")
    if not isinstance(cameras, list) or not cameras or len(set(cameras)) != len(cameras):
        raise ValueError("Explicit, unique camera order is required in metadata")
    return metadata, rows[:, :14], rows[:, 14:]


def data_manifest(cache: Path) -> list[dict]:
    """Fingerprint the exact prepared files, not a claimed raw HF revision."""
    records = []
    for split in ("train_real", "val_real", "train_sim", "val_sim"):
        for file in sorted((cache / split).glob("episode_*/*")):
            if file.is_file():
                records.append(
                    {
                        "path": str(file.relative_to(cache)),
                        "bytes": file.stat().st_size,
                        "sha256": sha256(file),
                    }
                )
    return records


def evaluation_args(checkpoint: Path, output: Path, *, seed: int, prompt: str, chunks=236):
    """One fresh process per world avoids carrying arm state between episodes."""
    if chunks < 1:
        raise ValueError("The action-chunk budget must be positive")
    return [
        "eval_policy.py",
        "--checkpoint",
        str(checkpoint),
        "--policy",
        "vla",
        "--task",
        TASK,
        "--prompt",
        prompt,
        "--num-worlds",
        "1",
        "--seed",
        str(seed),
        "--policy-seed",
        "0",
        "--num-chunks",
        str(chunks),
        "--execute-chunk-dim",
        "15",
        "--parallel-worlds",
        "0",
        "--camera-backend",
        "mjwarp",
        "--device",
        "cuda",
        "--no-fast-inference",
        "--no-rtc",
        "--save-video",
        "--video-every-n-actions",
        "15",
        "--output-dir",
        str(output),
    ]


def read_result(output: Path) -> dict:
    receipt = json.loads((output / "execution.command.json").read_text())
    if receipt.get("status") != "completed" or receipt.get("returncode") != 0:
        raise ValueError("An unsuccessful command cannot supply evaluation results")
    summary = json.loads((output / "summary.json").read_text())
    if summary.get("format") != "abc_minimal_sim_eval/v1" or summary.get("task") != TASK:
        raise ValueError("Not the expected ABC bottles evaluation")
    worlds = summary.get("worlds", [])
    if summary.get("num_worlds") != 1 or len(worlds) != 1:
        raise ValueError("Expected exactly one independently reset world")
    world = worlds[0]
    if type(world.get("success")) is not bool or type(world.get("final_success")) is not bool:
        raise ValueError("Missing measured success values; do not substitute zero")
    if not isinstance(world.get("world_seed"), int) or not isinstance(
        world.get("randomization"), dict
    ):
        raise ValueError("Missing actual reset provenance")
    return summary


def comparison_notes(baseline: list[dict], candidate: list[dict]) -> list[str]:
    """Report changed conditions instead of silently presenting a paired gain."""
    if not baseline or len(baseline) != len(candidate):
        return ["Different or missing episode counts; no paired comparison."]
    notes = []
    for left, right in zip(baseline, candidate, strict=True):
        for key in ("task", "prompt", "resolved_physics"):
            if key not in left or key not in right or left[key] != right[key]:
                notes.append(f"Changed or missing {key}.")
        for key in (
            "num_chunks",
            "execute_chunk_dim",
            "policy_seed",
            "camera_backend",
            "parallel_worlds",
            "rtc",
        ):
            if (
                key not in left["config"]
                or key not in right["config"]
                or left["config"][key] != right["config"][key]
            ):
                notes.append(f"Changed or missing execution setting: {key}.")
        for key in ("world_seed", "randomization"):
            if left["worlds"][0].get(key) != right["worlds"][0].get(key):
                notes.append(f"Changed initial conditions: {key}.")
    return sorted(set(notes))
