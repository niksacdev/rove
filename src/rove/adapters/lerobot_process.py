"""Bounded JSON bridge to an explicitly configured, isolated local VLA interpreter."""

from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
from contextlib import suppress
from pathlib import Path

MAX_BYTES = 16_000_000


def validate_prediction(result: dict, config: dict) -> None:
    """Reject corrupt or mismatched trajectories before they become trial evidence."""
    actions = result.get("actions")
    dim = result.get("declared_action_dim")
    if not isinstance(actions, list) or not actions or result.get("num_steps") != len(actions):
        raise ValueError("VLA response has an invalid trajectory length")
    if isinstance(dim, bool) or not isinstance(dim, int) or not 1 <= dim <= 256:
        raise ValueError("VLA response has an invalid action dimension")
    if any(
        not isinstance(row, list)
        or len(row) != dim
        or any(
            isinstance(x, bool) or not isinstance(x, int | float) or not math.isfinite(x)
            for x in row
        )
        for row in actions
    ):
        raise ValueError("VLA response contains invalid or nonfinite actions")
    if config.get("input_mode") == "observation_probe":
        if result.get("action_space") is not None:
            raise ValueError("Observation probes cannot claim a physical action space")
        if result.get("execution_eligible") is not False:
            raise ValueError("Observation probes must explicitly disable physical execution")


async def run_worker(python: str, config: dict, request: dict) -> dict:
    interpreter = Path(python).expanduser().absolute()
    if not interpreter.is_file():
        raise ValueError("Configured VLA runtime interpreter does not exist")
    worker = Path(__file__).with_name("lerobot_worker.py")
    allowed = {
        "checkpoint_path",
        "checkpoint_revision",
        "manifest_sha256",
        "runtime_versions",
        "tokenizer_path",
        "device",
        "chunk_size",
        "input_mode",
        "image_key",
        "action_space",
        "model_id",
        "seed",
        "robot_type",
        "proprioception_space",
    }
    payload = {**request, "config": {k: v for k, v in config.items() if k in allowed}}
    encoded = json.dumps(payload, allow_nan=False)
    if len(encoded.encode()) > MAX_BYTES:
        raise ValueError("VLA request exceeds 16 MB")
    timeout = float(config.get("runtime_timeout_s", 300))
    if not 0 < timeout <= 900:
        raise ValueError("VLA runtime timeout must be between 0 and 900 seconds")
    # The child is trusted local code with dependency isolation, not a security sandbox.
    # Do not pass cloud credentials, Python import overrides, or provider configuration.
    env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "SYSTEMROOT") if k in os.environ}
    env.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "PYTHONNOUSERSITE": "1",
            "TOKENIZERS_PARALLELISM": "false",
        }
    )
    with tempfile.TemporaryDirectory(prefix="rove-vla-") as directory:
        source = Path(directory) / "request.json"
        output = Path(directory) / "result.json"
        source.write_text(encoded)
        process = await asyncio.create_subprocess_exec(
            str(interpreter),
            str(worker),
            str(source),
            str(output),
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
            if not output.exists():
                raise RuntimeError("Local VLA worker exited without a result")
            if output.stat().st_size > MAX_BYTES:
                raise ValueError("VLA response exceeds 16 MB")
            result = json.loads(output.read_text())
            if not isinstance(result, dict):
                raise ValueError("Local VLA worker returned an invalid result")
            if result.get("error"):
                raise ValueError(f"Local VLA: {result['error']}")
            if process.returncode:
                raise RuntimeError("Local VLA worker failed")
            if request.get("operation") == "predict":
                validate_prediction(result, config)
            return result
        finally:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
            await process.wait()
