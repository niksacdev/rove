"""Aggregate only explicit episode measurements; missing evidence stays unknown."""

import math

from rove.models.verification import EvaluatorResult


def _episode(trial, campaign):
    endpoints = {
        c.get("endpoint")
        for c in (campaign.get("contract") or {}).get("criteria", [])
        if c.get("assessment") == "configured_verifier"
    }
    evidence = []
    for stage in (trial.get("result") or {}).get("stages", []):
        if stage.get("stage") != "verify" or stage.get("status") != "completed":
            continue
        output = stage.get("output") or {}
        if stage.get("model_id") in endpoints:
            evidence.append(output.get("evaluator_result"))
        for check in output.get("check_results", []):
            if check.get("endpoint") in endpoints and check.get("execution") == "completed":
                evidence.append(check.get("result"))
    valid = []
    for item in evidence:
        try:
            result = EvaluatorResult.model_validate(item)
        except ValueError:
            continue
        if result.details.get("episode_origin") not in {
            "recorded",
            "synthetic_rollout",
        } or result.evidence_quality not in {"observed", "synthetic"}:
            continue
        if any(
            not result.details.get(key)
            for key in ("case_id", "reset_id", "producing_system", "source_clock_id", "frame")
        ):
            continue
        if (
            result.details["episode_origin"] == "synthetic_rollout"
            and result.evidence_quality != "synthetic"
        ):
            continue
        if (campaign.get("contract") or {}).get("evidence_mode") == "synthetic_rollout" and (
            result.details["episode_origin"] != "synthetic_rollout"
            or result.details["case_id"] != trial["task_id"]
        ):
            continue
        values = {m.name: m for m in result.measurements}
        if any(
            m.quality != result.evidence_quality or not m.evidence_refs for m in values.values()
        ):
            continue
        valid.append((values, result.evidence_quality))
    # Several differently scoped episode evaluators would require an explicit metric binding.
    return valid[0] if len(valid) == 1 else ({}, None)


def summarize_robotics(campaign, trials):
    contract = campaign.get("contract") or {}
    requested = contract.get("metrics", [])
    planned = len(campaign["spec"]["tasks"]) * len(campaign["spec"]["seeds"])
    reports, targets = [], []
    for strategy in campaign["spec"]["strategies"]:
        by_slot = {(r["task_id"], r["seed"]): r for r in trials if r["strategy_id"] == strategy}
        rows = [
            by_slot.get((task["id"], seed))
            for task in campaign["spec"]["tasks"]
            for seed in campaign["spec"]["seeds"]
        ]
        metrics = {}
        for name in requested:
            if name in {"pass_at_k", "pass_pow_k"}:
                continue
            known, numerator, denominator, values, qualities = 0, 0, 0, [], set()
            unit = {"pipeline_latency": "ms", "episode_completion_time": "s"}.get(name, "fraction")
            for row in rows:
                if row is None:
                    continue
                outcome = row.get("outcome")
                if name == "task_success":
                    if outcome in {"pass", "fail"}:
                        known += 1
                        numerator += outcome == "pass"
                        denominator += 1
                        _, quality = _episode(row, campaign)
                        qualities.add(quality or "unknown")
                    continue
                if name == "pipeline_latency":
                    value = row.get("latency_ms")
                    if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                        known += 1
                        values.append(value)
                        qualities.add("observed")
                    continue
                if row.get("execution") not in {
                    "completed",
                    "invalid_verdict",
                    "unresolved_evidence",
                }:
                    continue
                measures, quality = _episode(row, campaign)
                if quality:
                    qualities.add(quality)

                def number(key, expected_unit, measures=measures):
                    item = measures.get(key)
                    return (
                        item.value
                        if item
                        and item.unit == expected_unit
                        and item.value is not None
                        and item.value >= 0
                        and (expected_unit != "count" or item.value.is_integer())
                        else None
                    )

                if name == "episode_completion_time":
                    value = number(name, "s")
                    if outcome == "pass" and value is not None:
                        known += 1
                        values.append(value)
                    elif outcome == "fail" and measures:
                        known += 1  # A failed episode is excluded from completion-time denominator.
                elif name == "autonomous_completion":
                    interventions = number("intervention_count", "count")
                    if outcome in {"pass", "fail"} and interventions is not None:
                        known += 1
                        denominator += 1
                        numerator += outcome == "pass" and interventions == 0
                elif name == "constraint_outcomes":
                    violations = number("constraint_violations", "count")
                    if violations is not None:
                        known += 1
                        denominator += 1
                        numerator += violations == 0
                elif name == "recovery":
                    opportunities, recovered = (
                        number("recovery_opportunities", "count"),
                        number("recoveries", "count"),
                    )
                    if (
                        opportunities is not None
                        and recovered is not None
                        and recovered <= opportunities
                    ):
                        known += 1
                        denominator += opportunities
                        numerator += recovered
            if values:
                denominator = len(values)
                numerator = sum(values)
                value = (
                    sorted(values)[math.ceil(0.95 * len(values)) - 1]
                    if name == "pipeline_latency"
                    else numerator / denominator
                )
            else:
                value = numerator / denominator if denominator else None
            if known < planned:
                value = None
            if name == "pipeline_latency":
                numerator = None  # A percentile is not the sum divided by the sample count.
            metrics[name] = {
                "definition_version": "robotics-metrics-v1",
                "value": value,
                "unit": unit,
                "numerator": numerator,
                "denominator": denominator,
                "planned_trials": planned,
                "known_trials": known,
                "unknown_trials": planned - known,
                "coverage": known / planned if planned else 0,
                "quality": next(iter(qualities))
                if len(qualities) == 1
                else "mixed"
                if qualities
                else "unknown",
                "aggregation": "p95"
                if name == "pipeline_latency"
                else "mean_successful_episodes"
                if name == "episode_completion_time"
                else "ratio",
                "status": "available" if value is not None else "unavailable",
                "reason": "Missing required evidence or no eligible denominator"
                if value is None
                else "Calculated from the declared evidence and denominator",
            }
        reports.append(
            {
                "strategy_id": strategy,
                "metrics": metrics,
                "evidence_mode": contract.get("evidence_mode"),
            }
        )
        for target in contract.get("campaign_targets", []):
            measured = metrics.get(target["metric"], {})
            value = measured.get("value")
            status = (
                "unknown"
                if value is None
                else "met"
                if (
                    value >= target["threshold"]
                    if target["operator"] == "gte"
                    else value <= target["threshold"]
                )
                else "not_met"
            )
            targets.append(
                {
                    "strategy_id": strategy,
                    **target,
                    "value": value,
                    "status": status,
                    "denominator": measured.get("denominator", 0),
                    "unknown_trials": measured.get("unknown_trials", planned),
                }
            )
    return reports, targets
