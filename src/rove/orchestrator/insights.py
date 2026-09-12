"""Run insights — computed analysis of pipeline health and model comparison."""

from __future__ import annotations


def _dynamics_all_ok(dyn_data: dict) -> bool | None:
    """Unknown or estimated checks cannot count as passes or failures."""
    from rove.orchestrator.failure_attribution import _get_evidence_value

    expected = {
        "joint_limits_ok": True,
        "self_collision": False,
        "torque_feasible": True,
        "near_singularity": False,
    }
    values = {key: _get_evidence_value(dyn_data, key) for key in expected}
    if any(value is not None and value != expected[key] for key, value in values.items()):
        return False
    if any(value is None for value in values.values()):
        return None
    return True


# Human-readable labels for failure categories
FAILURE_LABELS = {
    "perceive_error": "Perception Error",
    "perceive_miss": "Perception Miss",
    "plan_error": "Planning Error",
    "plan_low_confidence": "Low Plan Confidence",
    "act_error": "Action Error",
    "dynamics_error": "Dynamics Error",
    "action_dynamics_violation": "Dynamics Violation",
    "action_torque_violation": "Torque Limit Exceeded",
    "action_singularity": "Near Singularity",
    "action_infeasible": "Infeasible Action",
    "action_unsafe": "Unsafe Action",
    "action_out_of_workspace": "Out of Workspace",
    "verify_error": "Verification Error",
    "unknown": "Unresolved evidence",
    "verification_mismatch": "Verification Mismatch",
}


def compute_run_insights(results: list[dict]) -> dict:
    """Compute insights from pipeline results. Pure function, no I/O.

    Args:
        results: List of strategy result dicts from RunManager, each containing
            strategy_id, success, total_latency_ms, failure_stage, failure_category,
            stages, models, display_name.

    Returns:
        Dict with failure_breakdown, stage_health, model_comparison,
        degradation_signals, top_finding, reasoning_trail.
    """
    if not results:
        return {
            "failure_breakdown": {},
            "stage_health": {},
            "model_comparison": [],
            "degradation_signals": [],
            "top_finding": "No results to analyze.",
            "reasoning_trail": {},
            "confidence_scores": [],
        }

    n = len(results)

    # 1. Failure breakdown
    failure_breakdown: dict[str, int] = {}
    for r in results:
        cat = r.get("failure_category")
        if cat:
            failure_breakdown[cat] = failure_breakdown.get(cat, 0) + 1

    # 2. Stage health (0-1 score per stage)
    stage_health = _compute_stage_health(results)

    # 3. Model comparison
    model_comparison = _compute_model_comparison(results)

    # 4. Degradation signals
    degradation_signals = _compute_degradation_signals(results, stage_health, n)

    # 5. Top finding
    top_finding = _compute_top_finding(results, failure_breakdown, stage_health, n)

    # 6. Reasoning trail (with full stage outputs for rich rendering)
    reasoning_trail = _compute_reasoning_trail(results)

    # 7. Per-strategy confidence scores (from verify output)
    confidence_scores = _compute_confidence_scores(results)

    return {
        "failure_breakdown": failure_breakdown,
        "stage_health": stage_health,
        "model_comparison": model_comparison,
        "degradation_signals": degradation_signals,
        "top_finding": top_finding,
        "reasoning_trail": reasoning_trail,
        "confidence_scores": confidence_scores,
    }


def _compute_stage_health(results: list[dict]) -> dict[str, float]:
    """Per-stage health score (0-1)."""
    stage_data: dict[str, list[float]] = {
        "perceive": [],
        "plan": [],
        "act": [],
        "dynamics": [],
        "verify": [],
    }

    for r in results:
        stages = r.get("stages", [])
        stage_map = {s["stage"]: s for s in stages}

        # Perceive: 1.0 if task_relevant non-empty, 0.0 if empty/missing
        p = stage_map.get("perceive")
        if p and p.get("output"):
            task_relevant = p["output"].get("task_relevant", [])
            stage_data["perceive"].append(1.0 if task_relevant else 0.0)
        elif p and p.get("status") == "error":
            stage_data["perceive"].append(0.0)

        # Plan: confidence value (0-1)
        pl = stage_map.get("plan")
        if pl and pl.get("output"):
            conf = pl["output"].get("confidence", 0.5)
            stage_data["plan"].append(min(1.0, max(0.0, conf)))
        elif pl and pl.get("status") == "error":
            stage_data["plan"].append(0.0)

        # Act: 1.0 if completed, 0.0 if error
        a = stage_map.get("act")
        if a and a.get("output"):
            # Check dynamics from act output (sequential mode) or dynamics stage (parallel)
            dyn_data = a["output"].get("dynamics_analysis")
            d_stage = stage_map.get("dynamics")
            if d_stage and d_stage.get("output") and not d_stage["output"].get("skipped"):
                dyn_data = d_stage["output"]
            if dyn_data:
                ok = _dynamics_all_ok(dyn_data)
                if ok is not None:
                    stage_data["act"].append(1.0 if ok else 0.0)
            else:
                stage_data["act"].append(1.0)
        elif a and a.get("status") == "error":
            stage_data["act"].append(0.0)

        # Dynamics: 1.0 if no violations, 0.0 if violations or error
        d = stage_map.get("dynamics")
        if d and d.get("output"):
            if d["output"].get("skipped"):
                pass  # Don't score skipped dynamics
            else:
                ok = _dynamics_all_ok(d["output"])
                if ok is not None:
                    stage_data["dynamics"].append(1.0 if ok else 0.0)
        elif d and d.get("status") == "error":
            stage_data["dynamics"].append(0.0)

        # Verify: 1.0 if success, 0.0 if not
        v = stage_map.get("verify")
        if v and v.get("output"):
            if v["output"].get("verdict_valid", True):
                stage_data["verify"].append(1.0 if v["output"].get("success") else 0.0)
        elif v and v.get("status") == "error":
            stage_data["verify"].append(0.0)

    health: dict[str, float] = {}
    for stage, scores in stage_data.items():
        if scores:
            health[stage] = round(sum(scores) / len(scores), 2)
    return health


def _compute_model_comparison(results: list[dict]) -> list[dict]:
    """Group strategies by model IDs, compute per-model avg confidence and avg latency."""
    # Group by each model that appeared in any stage
    model_stats: dict[str, dict] = {}  # model_id -> {confidences, total, latencies}

    for r in results:
        models = r.get("models", {})
        confidence = r.get("confidence", 0.0)
        has_model_confidence = r.get("verdict_valid") is not False and not any(
            (s.get("output") or {}).get("evaluator_result")
            for s in r.get("stages", [])
            if s.get("stage") == "verify"
        )
        latency = r.get("total_latency_ms", 0)

        for stage, model_id in models.items():
            if model_id not in model_stats:
                model_stats[model_id] = {
                    "confidences": [],
                    "total": 0,
                    "latencies": [],
                    "stages": set(),
                }
            model_stats[model_id]["total"] += 1
            model_stats[model_id]["latencies"].append(latency)
            model_stats[model_id]["stages"].add(stage)
            if has_model_confidence:
                model_stats[model_id]["confidences"].append(confidence)

    comparison = []
    for model_id, stats in sorted(model_stats.items()):
        avg_conf = (
            round(sum(stats["confidences"]) / len(stats["confidences"]), 2)
            if stats["confidences"]
            else None
        )
        comparison.append(
            {
                "model_id": model_id,
                "stages": sorted(stats["stages"]),
                "avg_confidence": avg_conf,
                "avg_latency_ms": round(sum(stats["latencies"]) / len(stats["latencies"]), 1)
                if stats["latencies"]
                else 0,
                "n": stats["total"],
            }
        )

    return comparison


def _compute_degradation_signals(
    results: list[dict], stage_health: dict[str, float], n: int
) -> list[str]:
    """Generate warning strings about pipeline degradation."""
    signals = []

    # Stage failure counts
    stage_failures: dict[str, int] = {}
    for r in results:
        fs = r.get("failure_stage")
        if fs:
            stage_failures[fs] = stage_failures.get(fs, 0) + 1

    for stage, count in stage_failures.items():
        if count >= 2:
            signals.append(f"{stage} stage failed in {count}/{n} strategies")

    # Low health warnings
    for stage, score in stage_health.items():
        if score < 0.5:
            signals.append(f"{stage} health score is {score:.0%} (below 50%)")

    # Low plan confidence across strategies
    low_conf_count = 0
    for r in results:
        stages = r.get("stages", [])
        plan_stage = next((s for s in stages if s["stage"] == "plan"), None)
        if (
            plan_stage
            and plan_stage.get("output")
            and plan_stage["output"].get("confidence", 1.0) < 0.5
        ):
            low_conf_count += 1
    if low_conf_count >= 2:
        signals.append(f"Plan confidence below 0.5 in {low_conf_count}/{n} strategies")

    return signals


def _compute_top_finding(
    results: list[dict],
    failure_breakdown: dict[str, int],
    stage_health: dict[str, float],
    n: int,
) -> str:
    """Single-sentence summary of the most important insight."""
    successes = sum(1 for r in results if r.get("success") and r.get("verdict_valid") is not False)

    unknown = sum(r.get("verdict_valid") is False for r in results)
    if unknown:
        return f"{successes}/{n} strategies passed; {unknown} have unresolved evidence."

    if successes == n:
        avg_lat = sum(r.get("total_latency_ms", 0) for r in results) / n
        return f"All {n} strategies passed (avg latency {avg_lat:.0f}ms)."

    if successes == 0:
        # Find most common failure
        if failure_breakdown:
            worst = max(failure_breakdown, key=failure_breakdown.get)  # type: ignore[arg-type]
            label = FAILURE_LABELS.get(worst, worst)
            return f"All {n} strategies failed. Most common: {label} ({failure_breakdown[worst]}x)."
        return f"All {n} strategies failed."

    # Partial success — find the weakest stage
    if stage_health:
        weakest = min(stage_health, key=stage_health.get)  # type: ignore[arg-type]
        score = stage_health[weakest]
        return f"{successes}/{n} strategies passed. Weakest stage: {weakest} ({score:.0%} health)."

    return f"{successes}/{n} strategies passed."


def _compute_reasoning_trail(results: list[dict]) -> dict[str, dict]:
    """Extract per-strategy stage outputs for rich rendering in insights card.

    Includes full stage output dicts so the frontend can render them
    using the same rich formatters as the per-strategy execution view.
    """
    trail: dict[str, dict] = {}

    for r in results:
        sid = r.get("strategy_id", "")
        stages = r.get("stages", [])
        stage_map = {s["stage"]: s for s in stages}
        entry: dict = {}

        # Include full stage outputs for rich frontend rendering
        stage_outputs: dict[str, dict] = {}
        for stage_name in ("perceive", "plan", "act", "dynamics", "verify"):
            stage = stage_map.get(stage_name)
            if stage and stage.get("output"):
                stage_outputs[stage_name] = stage["output"]
        if stage_outputs:
            entry["stage_outputs"] = stage_outputs

        # Keep extracted reasoning strings for backward compat / text search
        p = stage_map.get("perceive")
        if p and p.get("output"):
            raw = p["output"].get("raw_response", "")
            if raw:
                entry["perceive_reasoning"] = raw

        pl = stage_map.get("plan")
        if pl and pl.get("output"):
            reasoning = pl["output"].get("reasoning", "")
            if reasoning:
                entry["plan_reasoning"] = reasoning

        v = stage_map.get("verify")
        if v and v.get("output"):
            vr = v["output"].get("reasoning", "")
            if vr:
                entry["verify_reasoning"] = vr
            checks = v["output"].get("stage_checks", [])
            if checks:
                entry["stage_checks"] = checks

        if entry:
            trail[sid] = entry

    return trail


def _compute_confidence_scores(results: list[dict]) -> list[dict]:
    """Per-strategy confidence scores from verify output."""
    scores = []
    for r in results:
        sid = r.get("strategy_id", "")
        display_name = r.get("display_name", sid)
        if r.get("verdict_valid") is False or any(
            (s.get("output") or {}).get("evaluator_result")
            for s in r.get("stages", [])
            if s.get("stage") == "verify"
        ):
            continue
        confidence = r.get("confidence", 0.0)
        success = r.get("success", False)
        scores.append(
            {
                "strategy_id": sid,
                "display_name": display_name,
                "confidence": confidence,
                "success": success,
            }
        )
    return scores
