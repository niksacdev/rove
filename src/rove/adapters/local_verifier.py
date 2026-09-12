"""Run trusted local evaluators in a cancellable child process."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sys
import tempfile
from contextlib import suppress
from pathlib import Path

from rove.models.verification import EvaluatorContext, EvaluatorResult


def evaluator_identity(config: dict) -> str:
    """Fingerprint the entry module and declared revision; no claim of hermeticity."""
    entrypoint = config.get("entrypoint", "")
    module, separator, function = entrypoint.partition(":")
    if not separator or not module or not function.isidentifier():
        raise ValueError("Verifier entrypoint must be module:function")
    spec = importlib.util.find_spec(module)
    if spec is None or not spec.origin or not Path(spec.origin).is_file():
        raise ValueError(f"Cannot locate verifier module {module}")
    digest = hashlib.sha256(Path(spec.origin).read_bytes()).hexdigest()
    return f"{config.get('revision', 'unversioned')}:{digest}:{function}"


class LocalVerifierAdapter:
    def __init__(self, model_id: str, config: dict):
        self.model_id = model_id
        self.display_name = config.get("display_name", model_id)
        self.config = config
        self.version = evaluator_identity(config)

    async def evaluate(self, context: EvaluatorContext) -> EvaluatorResult:
        if evaluator_identity(self.config) != self.version:
            raise ValueError("Evaluator source changed after initialization")
        with tempfile.TemporaryDirectory(prefix="rove-verifier-") as directory:
            request_path = Path(directory) / "input.json"
            result_path = Path(directory) / "output.json"
            request_path.write_text(
                json.dumps(
                    {"config": self.config, "context": context.model_dump(mode="json")},
                    allow_nan=False,
                )
            )
            # Inherit the trial worker's process group so campaign cancellation also
            # terminates this evaluator. This is trusted code, not a security sandbox.
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "rove.adapters.verifier_worker",
                str(request_path),
                str(result_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await process.wait()
                if process.returncode or not result_path.exists():
                    raise ValueError("Local evaluator failed or returned invalid evidence")
                if result_path.stat().st_size > 8_000_000:
                    raise ValueError("Evaluator evidence exceeded 8 MB")
                if evaluator_identity(self.config) != self.version:
                    raise ValueError("Evaluator source changed during execution")
                return EvaluatorResult.model_validate_json(result_path.read_text())
            finally:
                if process.returncode is None:
                    with suppress(ProcessLookupError):
                        process.kill()
                await process.wait()
