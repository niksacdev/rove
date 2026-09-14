"""Execution boundaries around the shared campaign lifecycle."""

from __future__ import annotations

from rove.benchmarks.models import CampaignSpec, fingerprint
from rove.evaluation.models import EvaluationConfig
from rove.evaluation.protocols import TrialExecutor
from rove.evaluation.registry import resolve


def prepare_evaluation(spec: CampaignSpec, config: EvaluationConfig) -> dict:
    from rove.benchmarks.runner import runtime_fingerprint

    _, identity = resolve(spec.execution)
    if set(spec.strategies) - config.strategies.keys():
        raise ValueError("Selected strategy is absent from the evaluation configuration")
    frozen = config.model_dump(mode="json")
    frozen["strategies"] = {sid: frozen["strategies"][sid] for sid in spec.strategies}
    runtime = runtime_fingerprint()
    comparison = fingerprint(
        {
            "tasks": [t.model_dump(mode="json") for t in spec.tasks],
            "suite_version": spec.suite_version,
            "seeds": spec.seeds,
            "timeout_s": spec.timeout_s,
            "grading": frozen["grading"],
            "environment": frozen["environment"],
            "executor": identity,
            "runtime": runtime,
        }
    )
    return {
        "spec": spec.model_dump(mode="json"),
        "config": frozen,
        "runtime": runtime,
        "executor_identity": identity,
        "local_evaluators": {},
        "config_hash": fingerprint(frozen),
        "comparison_keys": {sid: comparison for sid in spec.strategies},
        "strategy_definitions": frozen["strategies"],
        "model_versions": {},
        "evidence_kind": "executor_assessment",
        "metric_scope": "executor_assessment",
        "grading_note": "Outcomes use the installed executor's independent grader. "
        "Repeated attempts do not by themselves establish statistical independence. "
        "Executor revisions describe local source; remote model identities must be supplied in strategy configuration.",
    }


class StructuredExecutor:
    def validate(self, campaign):
        _, identity = resolve(campaign["spec"]["execution"])
        if identity != campaign.get("executor_identity"):
            raise ValueError("Evaluation executor changed; create a new campaign")

    def prepare_trial(self, campaign, task, strategy, store):
        from rove.trials.snapshots import canonical_json

        reference = store.save_asset(canonical_json(task.reference).encode(), "application/json")
        candidate = task.candidate().model_dump(mode="json")
        input_asset = store.save_asset(canonical_json(candidate).encode(), "application/json")
        snapshot = {**candidate, "input_asset": input_asset, "reference_asset": reference}
        if task.case_revision_id:
            snapshot["case_revision_id"] = task.case_revision_id
        frozen = {
            **campaign["config"],
            "strategies": {strategy: campaign["config"]["strategies"][strategy]},
            "executor_identity": campaign["executor_identity"],
            "content_hashes": {
                "strategy": fingerprint(campaign["config"]["strategies"][strategy]),
                "grading": fingerprint(campaign["config"]["grading"]),
                "environment": fingerprint(campaign["config"]["environment"]),
            },
        }
        return snapshot, frozen, task.model_dump(mode="json")


def executor_for(campaign: dict) -> TrialExecutor:
    if campaign["spec"].get("execution", "robotics") == "robotics":
        from rove.evaluation.robotics import RoboticsExecutor

        return RoboticsExecutor()
    return StructuredExecutor()
