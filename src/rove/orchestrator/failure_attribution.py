"""Failure attribution — derive root cause stage and category from pipeline results."""

from __future__ import annotations


def _get_evidence_value(dyn: dict, field: str) -> bool | None:
    """Extract a boolean evidence value from the new evidence structure.

    Supports both new evidence-structured format and legacy flat format.
    Returns None if field not found.
    """
    # New evidence structure
    evidence = dyn.get("evidence", [])
    for item in evidence:
        if item.get("field") == field and item.get("confidence") == "hard":
            return item.get("value")

    # Legacy flat format fallback (for backward compat)
    if field in dyn:
        return dyn[field]

    # Check summary (new format stores booleans there too)
    summary = dyn.get("summary", {})
    if field in summary:
        return summary[field]

    return None


def attribute_failure(
    stages: list[dict],
    success: bool,
) -> tuple[str | None, str | None]:
    """Determine which stage caused a pipeline failure and why.

    Returns (failure_stage, failure_category). Both are None if the pipeline succeeded.

    Attribution rules (first match wins):
    1. Stage with status "error" → {stage}_error
    2. Perceive produced no task_relevant objects → perceive_miss
    3. Plan confidence < 0.4 → plan_low_confidence
    4. Dynamics violation (joint limits or self-collision) from hard evidence → action_dynamics_violation
    4b. Torque limit exceeded from hard evidence → action_torque_violation
    4c. Near singularity at grasp config from hard evidence → action_singularity
    5. Action bounds_check failed → action_infeasible
    6. Verify success=False but all stages passed → verification_mismatch
    """
    if success:
        return None, None

    stage_map = {s["stage"]: s for s in stages}

    # Rule 1: explicit stage error
    for s in stages:
        if s.get("status") == "error":
            return s["stage"], f"{s['stage']}_error"

    # Rule 2: perceive miss — no task-relevant objects identified
    perceive = stage_map.get("perceive")
    if perceive and perceive.get("output"):
        task_relevant = perceive["output"].get("task_relevant", [])
        if not task_relevant:
            return "perceive", "perceive_miss"

    # Rule 3: plan low confidence
    plan = stage_map.get("plan")
    if plan and plan.get("output"):
        confidence = plan["output"].get("confidence", 1.0)
        if confidence < 0.4:
            return "plan", "plan_low_confidence"

    # Rule 4: dynamics violation — check both dynamics stage and act output
    dynamics = stage_map.get("dynamics")
    act = stage_map.get("act")
    dyn = None
    dyn_stage_name = "act"
    if dynamics and dynamics.get("output"):
        dyn = dynamics["output"]
        dyn_stage_name = "dynamics"
    elif act and act.get("output"):
        dyn = act["output"].get("dynamics_analysis")
    if dyn:
        # Only attribute failures from hard evidence
        jl_ok = _get_evidence_value(dyn, "joint_limits_ok")
        self_col = _get_evidence_value(dyn, "self_collision")
        if (jl_ok is not None and not jl_ok) or (self_col is not None and self_col):
            return dyn_stage_name, "action_dynamics_violation"

        # Rule 4b: torque limit exceeded
        torque_ok = _get_evidence_value(dyn, "torque_feasible")
        if torque_ok is not None and not torque_ok:
            return dyn_stage_name, "action_torque_violation"

        # Rule 4c: near singularity
        near_sing = _get_evidence_value(dyn, "near_singularity")
        if near_sing is not None and near_sing:
            return dyn_stage_name, "action_singularity"

    # Rule 5: action plausibility bounds check failed
    verify = stage_map.get("verify")
    if verify and verify.get("output"):
        plaus = verify["output"].get("action_plausibility")
        if plaus:
            bc = plaus.get("bounds_check")
            if bc is not None and not bc:
                return "act", "action_infeasible"
            # Rule 5b: safety assessment too low
            sa = plaus.get("safety_assessment")
            if sa is not None and sa < 0.3:
                return "act", "action_unsafe"
            # Rule 5c: workspace reachability too low
            wr = plaus.get("workspace_reachability")
            if wr is not None and wr < 0.3:
                return "act", "action_out_of_workspace"

    # Rule 6: verification mismatch (verify failed but no earlier stage caused it)
    if verify and verify.get("output") and not verify["output"].get("success", True):
        return "verify", "verification_mismatch"

    # Fallback: unknown failure
    return None, None


def get_failure_metadata(stages: list[dict]) -> dict:
    """Extract additional failure metadata from pipeline results.

    Includes resolution_path and verify_turns from the verify stage
    when available.
    """
    metadata: dict = {}
    for s in stages:
        if s.get("stage") == "verify" and s.get("output"):
            output = s["output"]
            resolution_path = output.get("resolution_path", "")
            if resolution_path:
                metadata["resolution_path"] = resolution_path
            verify_turns = output.get("verify_turns", 1)
            if verify_turns > 1:
                metadata["verify_turns"] = verify_turns
            contradictions = output.get("contradictions", [])
            if contradictions:
                metadata["contradictions"] = contradictions
    return metadata
