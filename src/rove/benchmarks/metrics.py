"""Task-macro pass@k/pass^k estimates; unknown outcomes remain visible as bounds."""

from __future__ import annotations

import math
from collections import Counter


def estimate(n: int, c: int, k: int, metric: str) -> float | None:
    if not 0 <= c <= n or k < 1:
        raise ValueError("Require 0 <= successes <= trials and k >= 1")
    if k > n:
        return None
    if metric == "pass_at_k":
        return 1 - math.comb(n - c, k) / math.comb(n, k)
    if metric == "pass_pow_k":
        return math.comb(c, k) / math.comb(n, k)
    raise ValueError("Unknown metric")


def wilson(c: int, n: int) -> tuple[float, float]:
    """95% Wilson interval under independent Bernoulli trials (not judge confidence)."""
    if n == 0:
        return 0.0, 1.0
    z = 1.959963984540054
    p = c / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, center - radius), min(1.0, center + radius)


def summarize(campaign: dict, trials: list[dict]) -> dict:
    spec = campaign["spec"]
    n = len(spec["seeds"])
    by_key = {(r["task_id"], r["strategy_id"], r["seed"]): r for r in trials}
    rows = []
    for strategy in spec["strategies"]:
        for task in spec["tasks"]:
            observed = [by_key.get((task["id"], strategy, seed)) for seed in spec["seeds"]]
            passed = sum(r is not None and r["outcome"] == "pass" for r in observed)
            failed = sum(r is not None and r["outcome"] == "fail" for r in observed)
            unknown = n - passed - failed
            counts = Counter(r["execution"] if r else "pending" for r in observed)
            curves = {}
            for metric in ("pass_at_k", "pass_pow_k"):
                curves[metric] = [
                    {
                        "k": k,
                        "value": estimate(n, passed, k, metric) if not unknown else None,
                        "lower": estimate(n, passed, k, metric),
                        "upper": estimate(n, passed + unknown, k, metric),
                    }
                    for k in spec["ks"]
                ]
            # Bounds on unresolved outcomes are NOT confidence intervals.
            ci = wilson(passed, n) if unknown == 0 else None
            rows.append(
                {
                    "task_id": task["id"],
                    "strategy_id": strategy,
                    "n": n,
                    "passed": passed,
                    "failed": failed,
                    "unknown": unknown,
                    "execution_counts": dict(counts),
                    "coverage": (passed + failed) / n,
                    "pass_at_1_wilson_95": ci,
                    **curves,
                }
            )
    aggregates = []
    for strategy in spec["strategies"]:
        tasks = [r for r in rows if r["strategy_id"] == strategy]
        agg = {
            "strategy_id": strategy,
            "tasks": len(tasks),
            "trials_per_task": n,
            "coverage": sum(t["coverage"] for t in tasks) / len(tasks),
        }
        for metric in ("pass_at_k", "pass_pow_k"):
            curve = []
            for index, k in enumerate(spec["ks"]):
                points = [t[metric][index] for t in tasks]
                point = {"k": k}
                for field in ("value", "lower", "upper"):
                    vals = [p[field] for p in points]
                    point[field] = None if any(v is None for v in vals) else sum(vals) / len(vals)
                curve.append(point)
            agg[metric] = curve
        latencies = sorted(
            r["latency_ms"]
            for r in trials
            if r["strategy_id"] == strategy and r.get("latency_ms") is not None
        )
        agg["latency_p95_ms"] = (
            latencies[math.ceil(0.95 * len(latencies)) - 1] if latencies else None
        )
        aggregates.append(agg)
    from rove.benchmarks.robotics import summarize_robotics

    robotics, targets = summarize_robotics(campaign, trials)
    solved = sum(any(r["passed"] for r in rows if r["task_id"] == t["id"]) for t in spec["tasks"])
    return {
        "strategies": aggregates,
        "robotics": robotics,
        "campaign_targets": targets,
        "tasks": rows,
        "completed_trials": len(trials),
        "planned_trials": len(rows) * n,
        "portfolio": {
            "tasks_solved": solved,
            "tasks_total": len(spec["tasks"]),
            "coverage": solved / len(spec["tasks"]),
            "attempt_budget_per_task": len(spec["strategies"]) * n,
            "meaning": "Observed any-success coverage across the selected configurations; hindsight, not a deployed selector.",
        },
    }
