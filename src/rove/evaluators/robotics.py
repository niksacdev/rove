"""Explicit robotics evidence and frozen-label graders for configured verify steps."""

from rove.datasets.robotics import EpisodeRecording
from rove.models.verification import EvaluatorResult, Measurement
from rove.trials.snapshots import canonical_json


def episode_outcome(context, config):
    try:
        episode = EpisodeRecording.model_validate(context.episode)
    except ValueError:
        return EvaluatorResult(
            verdict="unknown",
            reasoning="A validated versioned episode recording is required",
            evidence_quality="unknown",
        )
    if episode.task_completed is None or episode.constraint_violations is None:
        return EvaluatorResult(
            verdict="unknown",
            reasoning="Episode completion and constraint coverage are required",
            evidence_quality="unknown",
        )
    success = episode.task_completed and episode.constraint_violations == 0
    fields = {
        "episode_completion_time": (episode.completion_time_s, "s"),
        "intervention_count": (episode.intervention_count, "count"),
        "recovery_opportunities": (episode.recovery_opportunities, "count"),
        "recoveries": (episode.recoveries, "count"),
        "constraint_violations": (episode.constraint_violations, "count"),
    }
    measurements = [
        Measurement(
            name=name, value=value, unit=unit, quality=episode.quality, evidence_refs=["episode"]
        )
        for name, (value, unit) in fields.items()
        if value is not None
    ]
    return EvaluatorResult(
        verdict="pass" if success else "fail",
        reasoning="Episode completion and supplied constraints evaluated in the declared environment",
        evidence_quality=episode.quality,
        evidence_refs=["episode"],
        measurements=measurements,
        details={
            "episode_origin": episode.origin,
            "case_id": episode.case_id,
            "reset_id": episode.reset_id,
            "producing_system": episode.producing_system,
            "source_clock_id": episode.source_clock_id,
            "frame": episode.frame,
        },
    )


def annotation_match(context, config):
    """Compare one explicit structured output field with a frozen SME annotation."""
    key = config.get("annotation_key", "expected")
    if key not in context.annotations or not context.annotation_review_ids:
        return EvaluatorResult(
            verdict="unknown",
            reasoning="Explicit frozen annotation binding is missing",
            evidence_quality="unknown",
        )
    path = config.get("output_path", "plan.target_object").split(".")
    if path[0] not in {"scene", "plan", "action"}:
        return EvaluatorResult(
            verdict="unknown",
            reasoning="Annotation grading requires a candidate scene, plan or action output path",
            evidence_quality="unknown",
        )
    value = context.pipeline
    for part in path:
        if not isinstance(value, dict) or part not in value:
            return EvaluatorResult(
                verdict="unknown",
                reasoning="Configured structured candidate output is missing",
                evidence_quality="unknown",
            )
        value = value[part]
    matched = canonical_json(value) == canonical_json(context.annotations[key])
    refs = [f"review:{identity}" for identity in context.annotation_review_ids]
    return EvaluatorResult(
        verdict="pass" if matched else "fail",
        reasoning="Structured candidate output compared with the explicitly bound frozen SME annotation",
        evidence_quality="estimated",
        evidence_refs=refs,
        details={
            "annotation_review_ids": context.annotation_review_ids,
            "output_path": config.get("output_path", "plan.target_object"),
        },
    )
