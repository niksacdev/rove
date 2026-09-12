"""Existing FK/dynamics calculations exposed as supporting diagnostic evidence."""

from rove.adapters.local_verifier import LocalVerifierAdapter
from rove.models import ActionSpace
from rove.models.verification import EvaluatorContext, EvaluatorResult


def analyze_trajectory(urdf_path, action, metadata):
    if not urdf_path:
        return None, "No URDF provided — upload a URDF or use a dataset with robot metadata"
    if not action or not action.get("actions"):
        return None, "No trajectory actions to analyze"
    space = action.get("action_space")
    if space == ActionSpace.EEF_DELTA:
        control_space = "end_effector_delta"
    elif space == ActionSpace.JOINT_DELTA:
        control_space = "joint_space"
    else:
        return None, f"Dynamics does not support {space} trajectories yet"
    try:
        from rove.adapters.dynamics_mujoco import compute_dynamics

        return compute_dynamics(
            urdf_path,
            action["actions"],
            initial_qpos=metadata.get("initial_joint_positions"),
            control_space=control_space,
        ), None
    except ImportError:
        return None, "MuJoCo not installed"
    except Exception as error:
        return None, f"Dynamics computation failed: {error}"


def forward_kinematics(context: EvaluatorContext, config: dict) -> EvaluatorResult:
    dynamics, reason = analyze_trajectory(
        context.urdf_path, context.pipeline.get("action"), context.pipeline.get("task_metadata", {})
    )
    return EvaluatorResult(
        verdict="unknown",  # FK does not establish task completion.
        reasoning=reason
        or "Computed trajectory diagnostics; task completion is not established by FK.",
        evidence_quality="estimated" if dynamics else "unknown",
        evidence_refs=["pipeline.action", "robot_model"] if dynamics else [],
        details={"dynamics_analysis": dynamics} if dynamics else {},
    )


class ForwardKinematicsAdapter(LocalVerifierAdapter):
    def __init__(self, model_id: str, config: dict):
        super().__init__(
            model_id,
            {
                **config,
                "entrypoint": "rove.adapters.forward_kinematics:forward_kinematics",
                "revision": "rove-forward-kinematics-v1",
            },
        )
