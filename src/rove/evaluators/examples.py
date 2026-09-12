"""Examples over supplied episode evidence, not live robot integrations."""

import math

from rove.models.verification import EvaluatorResult, Measurement


def _unknown(reason):
    return EvaluatorResult(verdict="unknown", reasoning=reason, evidence_quality="unknown")


def object_in_target(context, config):
    episode = context.episode
    quality = episode.get("quality")
    if quality not in {"observed", "synthetic"} or episode.get("observation_phase") != "final":
        return _unknown(
            "Supply a final object observation with explicit observed/synthetic quality"
        )
    position = episode.get("final_object_position_m")
    lower = config.get("target_min_m")
    upper = config.get("target_max_m")
    vectors = (position, lower, upper)
    if not all(
        isinstance(v, list)
        and len(v) == 3
        and all(type(x) in (int, float) and math.isfinite(x) for x in v)
        for v in vectors
    ):
        return _unknown("Final object position and three-dimensional target bounds are required")
    if any(low > high for low, high in zip(lower, upper, strict=True)):
        return _unknown("Invalid target bounds")
    inside = all(low <= x <= high for x, low, high in zip(position, lower, upper, strict=True))
    measurements = []
    duration = episode.get("completion_time_s")
    if type(duration) in (int, float) and math.isfinite(duration) and duration >= 0:
        measurements.append(
            Measurement(
                name="episode_completion_time",
                value=duration,
                unit="s",
                quality=quality,
                evidence_refs=["episode.completion_time_s"],
            )
        )
    return EvaluatorResult(
        verdict="pass" if inside else "fail",
        reasoning="Final object position is inside the target"
        if inside
        else "Final object position is outside the target",
        evidence_quality=quality,
        evidence_refs=["episode.final_object_position_m"],
        measurements=measurements,
    )


def force_limit(context, config):
    episode = context.episode
    force = episode.get("peak_contact_force_n")
    limit = config.get("max_force_n")
    quality = episode.get("quality")
    if quality not in {"observed", "synthetic"} or not all(
        type(v) in (float, int) and math.isfinite(v) and v >= 0 for v in (force, limit)
    ):
        return _unknown("Peak contact force, limit and evidence quality are required")
    return EvaluatorResult(
        verdict="pass" if force <= limit else "fail",
        reasoning="Supplied peak contact force compared with configured limit",
        evidence_quality=quality,
        evidence_refs=["episode.peak_contact_force_n"],
        measurements=[
            Measurement(
                name="peak_contact_force",
                value=force,
                unit="N",
                quality=quality,
                evidence_refs=["episode.peak_contact_force_n"],
            )
        ],
    )
