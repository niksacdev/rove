"""Summarize measured evidence without treating missing data as zero."""

import math
from collections import defaultdict

from rove.models.config import StrategyConfig


def summarize_evidence(campaign, trials):
    planned = len(campaign["spec"]["tasks"]) * len(campaign["spec"]["seeds"])
    checks = {}
    for sid, definition in campaign["strategy_definitions"].items():
        for check in StrategyConfig.model_validate(definition).stage_options("verify").checks:
            checks[sid, check.endpoint] = {
                "strategy": sid,
                "endpoint": check.endpoint,
                "role": check.role,
                "required": check.required,
                "planned": planned,
                "pass": 0,
                "fail": 0,
                "unknown": planned,
            }
    measurements = defaultdict(list)
    records = []
    for trial in trials:
        stage = next(
            (s for s in trial.get("result", {}).get("stages", []) if s.get("stage") == "verify"), {}
        )
        output = stage.get("output") or {}
        entries = []
        if output.get("evaluator_result"):
            entries.append(("task_evaluator", output["evaluator_result"]))
        for check in output.get("check_results", []):
            key = trial["strategy_id"], check["endpoint"]
            result = check["result"]
            verdict = result["verdict"]
            if key in checks and verdict in {"pass", "fail"} and check["execution"] == "completed":
                checks[key][verdict] += 1
                checks[key]["unknown"] -= 1
            entries.append((check["endpoint"], result))
        for endpoint, result in entries:
            for measurement in result.get("measurements", []):
                records.append(
                    {
                        "strategy": trial["strategy_id"],
                        "task": trial["task_id"],
                        "seed": trial["seed"],
                        "endpoint": endpoint,
                        **measurement,
                    }
                )
                value = measurement.get("value")
                if value is not None and measurement["quality"] != "unknown":
                    key = (
                        trial["strategy_id"],
                        endpoint,
                        measurement["name"],
                        measurement["unit"],
                        measurement["quality"],
                    )
                    measurements[key].append(value)
    aggregates = []
    for (sid, endpoint, name, unit, quality), values in sorted(measurements.items()):
        values.sort()
        aggregates.append(
            {
                "strategy": sid,
                "endpoint": endpoint,
                "name": name,
                "unit": unit,
                "quality": quality,
                "measured": len(values),
                "planned": planned,
                "min": values[0],
                "max": values[-1],
                "p95": values[math.ceil(0.95 * len(values)) - 1],
            }
        )
    return {
        "checks": list(checks.values()),
        "measurements": aggregates,
        "measurement_records": records,
    }
