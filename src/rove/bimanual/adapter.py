"""Built-in structured evaluation adapter for independently graded ABC episodes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from contextlib import suppress
from pathlib import Path

from rove.bimanual.config import HERE, Environment, Strategy, validate_profile
from rove.evaluation.context import EvaluationContext
from rove.evaluation.models import CandidateInput, EvaluationConfig
from rove.evaluation.results import EvaluatorResult, Measurement
from rove.trials.snapshots import canonical_json


class ABCBimanualEvaluation:
    revision = "abc-pi05-episode-v1"

    def bind_context(self, context: EvaluationContext):
        self.context = context

    async def predict(self, case: CandidateInput, strategy: dict, seed: int):
        environment = Environment.model_validate(self.context.environment)
        candidate = Strategy.model_validate(strategy)
        if case.inputs.get("task") != environment.task or case.task != environment.prompt:
            raise ValueError("Case and frozen simulator task/prompt differ")
        reset_seed = case.inputs.get("reset_seed")
        if type(reset_seed) is not int or not 0 <= reset_seed <= 2**32 - 1:
            raise ValueError("Case must declare an unsigned scene seed")
        config = EvaluationConfig(
            environment=environment.model_dump(), strategies={"model": strategy}
        )
        readiness = await asyncio.to_thread(validate_profile, config)
        if not readiness["ready"]:
            self.context.emit(
                "preflight", "failed", data={"issues": readiness["strategies"][0]["issues"]}
            )
            raise ValueError("Registered checkpoint or runtime is unavailable or changed")
        self.context.emit("preflight", "completed")
        with tempfile.TemporaryDirectory(prefix="rove-abc-") as temporary:
            directory = Path(temporary)
            request = {
                "environment": environment.model_dump(),
                "strategy": candidate.model_dump(),
                "case": case.model_dump(),
                "seed": seed,
            }
            request_path = directory / "request.json"
            request_path.write_text(json.dumps(request))
            process = await asyncio.create_subprocess_exec(
                environment.python,
                str(HERE / "abc_process.py"),
                str(request_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=1_000_000,
                env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
            )
            summary = None
            try:
                while line := await process.stdout.readline():
                    event = json.loads(line)
                    if event["type"] == "progress":
                        self.context.emit(
                            event["stage"],
                            event["phase"],
                            data=event["data"],
                            span_id=event.get("span_id"),
                        )
                    elif event["type"] == "artifact":
                        self._artifact(directory, event)
                    elif event["type"] == "result":
                        if summary is not None:
                            raise ValueError("Duplicate episode result")
                        summary = event["summary"]
                    elif event["type"] == "error":
                        self.context.emit(
                            "episode",
                            "failed",
                            data={
                                "exception_type": event["error_type"],
                                "message": "ABC episode could not complete; check runtime and checkpoint compatibility",
                            },
                        )
                    else:
                        raise ValueError("Invalid episode runtime response")
                if await process.wait() != 0 or summary is None:
                    raise ValueError("ABC episode failed before producing outcome evidence")
                if summary.get("reset_seed") != reset_seed or summary.get("policy_seed") != seed:
                    raise ValueError("Episode runtime returned different scene or policy seeds")
                if not (await asyncio.to_thread(validate_profile, config))["ready"]:
                    raise ValueError("Model or simulator changed during execution")
                self.context.record_json("episode.summary", summary)
                return summary
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                # Preserve interrupted trajectory segments too, without grading them.
                if "episode.trajectory" not in self.context.evidence_ids:
                    with suppress(OSError, ValueError):
                        self.context.record_file(
                            "episode.partial_trajectory",
                            directory / "trajectory.jsonl",
                            "application/x-ndjson",
                            kind="trajectory",
                        )

    def _artifact(self, directory, event):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", event["file"]):
            raise ValueError("Runtime artifact must remain within its episode directory")
        if not re.fullmatch(r"episode\.[A-Za-z0-9_.-]+", event["id"]):
            raise ValueError("Runtime artifact must use the episode namespace")
        path = directory / event["file"]
        if path.resolve().parent != directory.resolve():
            raise ValueError("Runtime artifact escaped its episode directory")
        kind = (
            "image"
            if event["media_type"] == "image/png"
            else ("trajectory" if event["id"].startswith("episode.trajectory") else "state")
        )
        self.context.record_file(event["id"], path, event["media_type"], kind=kind)

    async def grade(self, case, output, reference, grading):
        env = Environment.model_validate(self.context.environment)
        mode = grading.get("success_mode", env.success_mode)
        if mode != env.success_mode:
            raise ValueError("Grading and simulator success mode differ")
        refs = ["episode.summary", "episode.trajectory", "episode.initial_state"]
        if set(refs) - self.context.evidence_ids:
            raise ValueError("Measured episode evidence is missing")
        if hashlib.sha256(
            canonical_json(output).encode()
        ).hexdigest() != self.context.evidence_digest("episode.summary"):
            raise ValueError("Assessment input differs from the recorded episode summary")
        if output.get("reset_seed") != case.inputs.get("reset_seed"):
            raise ValueError("Episode evidence belongs to a different scene")
        if output.get("scope") != "abc_simulation" or output.get("episode_steps") != env.max_steps:
            raise ValueError("Simulator did not complete the declared budget")
        if (
            type(output.get("task_success")) is not bool
            or type(output.get("final_success")) is not bool
        ):
            raise ValueError("Simulator outcome was not measured")
        success = output["task_success" if mode == "ever" else "final_success"]
        units = {
            "episode_steps": "step",
            "simulated_time_s": "s",
            "replans": "count",
            "gripper_out_of_range_steps": "step",
            "inference_latency_mean_s": "s",
            "inference_latency_p95_s": "s",
            "inference_wall_s": "s",
        }
        values = {key: output[key] for key in units}
        values.update(
            episode_success=float(output["task_success"]),
            final_success=float(output["final_success"]),
        )
        units.update(episode_success="fraction", final_success="fraction")
        return EvaluatorResult(
            verdict="pass" if success else "fail",
            evidence_quality="observed",
            reasoning=(
                f"ABC's task evaluator {'observed' if success else 'did not observe'} task success "
                f"({'at least once' if mode == 'ever' else 'at the final step'}) within {env.max_steps} simulated control steps."
            ),
            evidence_refs=refs,
            measurements=[
                Measurement(
                    name=k,
                    value=v,
                    unit=units[k],
                    quality="observed",
                    evidence_refs=["episode.summary"],
                )
                for k, v in values.items()
            ],
            details={
                "scope": "abc_simulation",
                "success_mode": mode,
                "physical_robot_success": "not_measured",
                "collision_rate": "not_measured",
            },
        )
