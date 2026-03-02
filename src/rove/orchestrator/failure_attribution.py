"""Failure attribution — derive root cause stage and category from pipeline results."""

from __future__ import annotations


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
    4. Dynamics violation (joint limits or self-collision) → action_dynamics_violation
    4b. Torque limit exceeded → action_torque_violation
    4c. Near singularity at grasp config → action_singularity
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
        if not dyn.get("joint_limits_ok", True) or dyn.get("self_collision", False):
            return dyn_stage_name, "action_dynamics_violation"
        # Rule 4b: torque limit exceeded
        if not dyn.get("torque_feasible", True):
            return dyn_stage_name, "action_torque_violation"
        # Rule 4c: near singularity
        if dyn.get("near_singularity", False):
            return dyn_stage_name, "action_singularity"

    # Rule 5: action plausibility bounds check failed
    verify = stage_map.get("verify")
    if verify and verify.get("output"):
        plaus = verify["output"].get("action_plausibility")
        if plaus:
            if not plaus.get("bounds_check", True):
                return "act", "action_infeasible"
            # Rule 5b: safety assessment too low
            if plaus.get("safety_assessment", 1.0) < 0.3:
                return "act", "action_unsafe"
            # Rule 5c: workspace reachability too low
            if plaus.get("workspace_reachability", 1.0) < 0.3:
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
    return metadata
