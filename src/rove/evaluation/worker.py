"""One isolated non-robotics attempt, using the same deadline and storage lifecycle."""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import sys
import time
from contextlib import suppress
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from rove.evaluation.context import EvaluationContext
from rove.evaluation.models import EvaluationCase
from rove.evaluation.registry import resolve
from rove.evaluation.results import EvaluatorResult
from rove.trials.snapshots import canonical_json, sanitize
from rove.trials.store import TrialStore


async def execute(request: dict) -> dict:
    factory, identity = resolve(request["execution"])
    if identity != request["executor_identity"]:
        raise ValueError("Evaluation executor changed before execution")
    case = EvaluationCase.model_validate(request["task"])
    store, trial_id = TrialStore(Path(request["trial_root"])), request["trial_id"]
    config = request["config"]
    started = time.monotonic()
    context = EvaluationContext(store, trial_id, config.get("environment", {}))
    stage = "setup"
    try:
        adapter = factory()
        if binder := getattr(adapter, "bind_context", None):
            bound = binder(context)
            if inspect.isawaitable(bound):
                await bound
        stage = "candidate"
        context.emit(stage, "started")
        output = await adapter.predict(
            case.candidate(),
            copy.deepcopy(config["strategies"][request["strategy_id"]]),
            request["seed"],
        )
        output = TypeAdapter(JsonValue).validate_python(output)
        encoded = canonical_json(output).encode()
        if len(encoded) > 4_000_000:
            raise ValueError("Candidate output exceeds 4 MB")
        # Store raw output once as managed evidence; sanitize the public record below.
        context._record("candidate.output", encoded, "application/json")
        context.emit(stage, "completed")
        context._record(
            "case.reference", canonical_json(case.reference).encode(), "application/json"
        )
        stage = "assessment"
        context.emit(stage, "started")
        # Fresh copies prevent candidate mutation from changing the grader inputs.
        case = EvaluationCase.model_validate(request["task"])
        graded = await adapter.grade(
            case.candidate(),
            copy.deepcopy(output),
            case.reference,
            copy.deepcopy(config["grading"]),
        )
        assessment = EvaluatorResult.model_validate(
            graded.model_dump(mode="json") if isinstance(graded, EvaluatorResult) else graded
        )
        refs = set(assessment.evidence_refs)
        refs.update(ref for m in assessment.measurements for ref in m.evidence_refs)
        if refs - context.evidence_ids:
            raise ValueError("Assessment refers to evidence that was not recorded")
        assessment_payload = assessment.model_dump(mode="json")
        # Round-trip prohibits NaN in free-form details and validates bounded storage.
        encoded_assessment = canonical_json(assessment_payload).encode()
        if len(encoded_assessment) > 1_000_000:
            raise ValueError("Assessment exceeds 1 MB")
        context._record("assessment.output", encoded_assessment, "application/json")
        context.emit(stage, "completed")
    except BaseException as error:
        # Provider exceptions often contain URLs, credentials or customer inputs.
        # Persist the stage and type only, while keeping already-written evidence.
        with suppress(Exception):
            context._record(
                "evaluation.error",
                canonical_json(
                    {
                        "stage": stage,
                        "exception_type": type(error).__name__,
                        "message": "Evaluation stage did not complete",
                    }
                ).encode(),
                "application/json",
            )
            context.emit(
                stage,
                "cancelled" if isinstance(error, asyncio.CancelledError) else "failed",
                data={"exception_type": type(error).__name__},
                evidence_refs=["evaluation.error"],
            )
        raise
    return {
        "outcome": assessment.verdict,
        "execution": "completed",
        "metric_scope": "executor_assessment",
        "result": sanitize({"output": output, "assessment": assessment_payload}),
        "seed_support": {identity["name"]: "supplied_to_executor"},
        "latency_ms": (time.monotonic() - started) * 1000,
    }


def main():
    request_path, output_path = map(Path, sys.argv[1:])
    result = asyncio.run(execute(json.loads(request_path.read_text())))
    output_path.write_text(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
