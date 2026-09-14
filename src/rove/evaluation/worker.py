"""One isolated non-robotics attempt, using the same deadline and storage lifecycle."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
import time
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from rove.evaluation.models import EvaluationCase
from rove.evaluation.registry import resolve
from rove.evaluation.results import EvaluatorResult
from rove.trials.snapshots import canonical_json, sanitize
from rove.trials.store import TrialStore


async def execute(request: dict) -> dict:
    factory, identity = resolve(request["execution"])
    if identity != request["executor_identity"]:
        raise ValueError("Evaluation executor changed before execution")
    adapter = factory()
    case = EvaluationCase.model_validate(request["task"])
    store, trial_id = TrialStore(Path(request["trial_root"])), request["trial_id"]
    config = request["config"]
    started = time.monotonic()

    def event(stage, phase):
        store.append_event(
            trial_id,
            {
                "event_type": f"evaluation.{phase}",
                "stage": stage,
                "name": f"{stage.title()} {phase}",
                "source": "rove.evaluation",
                "source_clock_id": f"worker:{trial_id}",
                "time_unit": "ns",
                "source_timestamp": time.monotonic_ns(),
                "span_id": f"{trial_id}:{stage}",
            },
        )

    event("candidate", "started")
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
    asset = store.save_asset(encoded, "application/json")
    store.attach_evidence(
        trial_id,
        {
            "id": "candidate.output",
            "asset_sha256": asset["sha256"],
            "kind": "tool_output",
            "units": {},
        },
    )
    event("candidate", "completed")
    reference_asset = store.save_asset(canonical_json(case.reference).encode(), "application/json")
    store.attach_evidence(
        trial_id,
        {
            "id": "case.reference",
            "asset_sha256": reference_asset["sha256"],
            "kind": "tool_output",
            "units": {},
        },
    )
    event("assessment", "started")
    # Fresh validated copies prevent candidate-side mutation from changing the grader inputs.
    case = EvaluationCase.model_validate(request["task"])
    graded = await adapter.grade(
        case.candidate(), copy.deepcopy(output), case.reference, copy.deepcopy(config["grading"])
    )
    assessment = EvaluatorResult.model_validate(
        graded.model_dump(mode="json") if isinstance(graded, EvaluatorResult) else graded
    )
    refs = set(assessment.evidence_refs)
    refs.update(ref for m in assessment.measurements for ref in m.evidence_refs)
    if refs - {"candidate.output", "case.reference"}:
        raise ValueError("Assessment refers to evidence that was not recorded")
    assessment_payload = assessment.model_dump(mode="json")
    # Round-trip prohibits NaN in free-form details and validates bounded storage.
    encoded_assessment = canonical_json(assessment_payload).encode()
    if len(encoded_assessment) > 1_000_000:
        raise ValueError("Assessment exceeds 1 MB")
    assessment_asset = store.save_asset(encoded_assessment, "application/json")
    store.attach_evidence(
        trial_id,
        {
            "id": "assessment.output",
            "asset_sha256": assessment_asset["sha256"],
            "kind": "tool_output",
            "units": {},
        },
    )
    event("assessment", "completed")
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
