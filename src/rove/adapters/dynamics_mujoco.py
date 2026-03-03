"""MuJoCo dynamics engine — FK, inverse dynamics, gravity compensation, manipulability.

Converts VLA action arrays into Cartesian end-effector positions and computes
torque feasibility, gravity compensation, and manipulability analysis for the
verify stage.  Supports both joint-space and end-effector delta control spaces.

Requires: pip install rove-eval[kinematics]
"""

from __future__ import annotations

import logging
import tempfile
import xml.etree.ElementTree as ET  # nosec B405 — URDF is local trusted file
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


def _prepare_urdf_for_dynamics(urdf_path: Path) -> str:
    """Prepare a URDF for dynamics loading in MuJoCo.

    1. Strips <visual> and <collision> elements (avoids mesh file errors).
    2. Adds dummy <inertial> to links that lack them (MuJoCo requires mass > 0
       for all moving bodies).

    Returns path to the cleaned temp file.
    """
    tree = ET.parse(urdf_path)  # nosec B314 — URDF is a local trusted file
    root = tree.getroot()

    for link in root.iter("link"):
        # Strip visual and collision (we don't need meshes for dynamics)
        for tag in ("visual", "collision"):
            for elem in link.findall(tag):
                link.remove(elem)

        # Add dummy inertial if missing (MuJoCo needs mass > mjMINVAL)
        if link.find("inertial") is None:
            inertial = ET.SubElement(link, "inertial")
            ET.SubElement(inertial, "mass", value="0.1")
            ET.SubElement(inertial, "origin", xyz="0 0 0", rpy="0 0 0")
            ET.SubElement(
                inertial,
                "inertia",
                ixx="0.001",
                ixy="0",
                ixz="0",
                iyy="0.001",
                iyz="0",
                izz="0.001",
            )

    with tempfile.NamedTemporaryFile(delete=False, suffix=".urdf", prefix="rove_dynamics_") as tmp:
        tree.write(tmp, xml_declaration=True)
    return tmp.name


def _find_grasp_step(actions: list[list[float]]) -> int:
    """Find the first gripper-close event in the trajectory.

    Scans the last DOF for the first negative-going transition (gripper close).
    Falls back to midpoint if no transition found.
    """
    prev_open = True
    for i, a in enumerate(actions):
        grip = a[-1]
        is_open = grip > 0.0
        if not is_open and prev_open:
            return i
        prev_open = is_open
    return len(actions) // 2


def _extract_gripper_events(actions: list[list[float]]) -> list[dict]:
    """Extract gripper open/close transitions from an action trajectory."""
    events: list[dict] = []
    prev_open = True
    for i, a in enumerate(actions):
        grip = a[-1]
        is_open = grip > 0.0
        if is_open != prev_open:
            events.append({"step": i, "type": "open" if is_open else "close"})
            prev_open = is_open
    return events


def compute_dynamics(
    urdf_path: str,
    actions: list[list[float]],
    initial_qpos: list[float] | None = None,
    payload_kg: float = 0.5,
    control_space: str = "joint_space",
) -> dict:
    """Compute full dynamics analysis for a VLA trajectory.

    Args:
        urdf_path: Path to URDF/MJCF file describing the robot.
        actions: List of action arrays (joint-space deltas or EE deltas).
        initial_qpos: Optional initial joint positions. If None, uses model defaults.
        payload_kg: Assumed payload mass for gravity compensation check.
        control_space: "joint_space" or "end_effector_delta". Controls how
            actions are interpreted and applied.

    Returns:
        Evidence-structured dict with analysis_mode, robot info, evidence list,
        not_computed list, gripper_events, and summary.
    """
    try:
        import mujoco
    except ImportError as err:
        raise ImportError(
            "MuJoCo is required for dynamics computation. "
            "Install with: pip install rove-eval[kinematics]"
        ) from err

    urdf = Path(urdf_path)
    if not urdf.exists():
        raise FileNotFoundError(f"URDF file not found: {urdf_path}")
    if not urdf.is_file():
        raise ValueError(f"URDF path is not a file: {urdf_path}")

    # Try loading directly first; if mesh errors occur, strip meshes and retry
    load_path = str(urdf)
    try:
        model = mujoco.MjModel.from_xml_path(load_path)
    except Exception:
        try:
            stripped = _prepare_urdf_for_dynamics(urdf)
            load_path = stripped
            model = mujoco.MjModel.from_xml_path(load_path)
            logger.info("Loaded URDF with meshes stripped for dynamics computation")
        except Exception as e2:
            raise ValueError(
                f"Failed to load URDF/MJCF '{urdf.name}': {e2}. "
                "Ensure the file is a valid URDF, MJCF, or MuJoCo XML."
            ) from e2

    data = mujoco.MjData(model)

    # Determine DOF counts
    n_act = model.nu  # number of actuators
    n_qpos = model.nq  # number of generalized coordinates
    n_dof = model.nv  # number of degrees of freedom (velocity space)
    n_apply_max = n_qpos if n_act == 0 else n_act

    # Set initial joint positions
    if initial_qpos is not None:
        qpos = np.array(initial_qpos[: min(len(initial_qpos), n_qpos)], dtype=np.float64)
        data.qpos[: len(qpos)] = qpos
    mujoco.mj_forward(model, data)

    # Track end-effector via the last body in the kinematic chain
    ee_body_id = model.nbody - 1

    # Determine analysis mode
    is_ee_delta = control_space == "end_effector_delta"
    analysis_mode = "ee_delta_jacobian_ik" if is_ee_delta else "joint_space"
    evidence_source = "jacobian_ik" if is_ee_delta else "joint_space"

    # Run trajectory integration
    endpoint_trajectory: list[list[float]] = []
    velocities: list[float] = []
    joint_limits_ok = True
    joint_limit_details: list[str] = []
    self_collision = False

    # Record initial position
    ee_pos = data.xpos[ee_body_id].copy()
    endpoint_trajectory.append(ee_pos.tolist())

    prev_qpos = data.qpos[:n_apply_max].copy()

    # Record qpos at each step for inverse dynamics
    qpos_trajectory: list[np.ndarray] = [data.qpos[:n_dof].copy()]

    for step_idx, step_actions in enumerate(actions):
        action_arr = np.array(step_actions, dtype=np.float64)

        if is_ee_delta:
            # EE-delta mode: use Jacobian pseudoinverse to convert EE deltas → joint deltas
            jacp = np.zeros((3, n_dof))
            jacr = np.zeros((3, n_dof))
            mujoco.mj_jac(model, data, jacp, jacr, data.xpos[ee_body_id], ee_body_id)
            J = np.vstack([jacp, jacr])  # 6 x N
            J_pinv = np.linalg.pinv(J)  # N x 6

            # Extract EE delta (first 6 values: 3 pos + 3 rot)
            n_ee = min(len(action_arr), 6)
            ee_delta = np.zeros(6)
            ee_delta[:n_ee] = action_arr[:n_ee]

            # Convert EE delta → joint delta
            joint_delta = J_pinv @ ee_delta
            data.qpos[:n_dof] += joint_delta

            # Handle gripper separately (last DOF in action)
            if len(action_arr) > 6 and n_act > 0:
                data.ctrl[-1] = action_arr[-1]
        else:
            # Joint-space mode: apply deltas directly to joint positions
            n_apply = min(len(action_arr), n_apply_max)
            data.qpos[:n_apply] += action_arr[:n_apply]

            # Handle gripper actuator if action has extra DOF
            if len(action_arr) > n_apply_max and n_act > 0:
                data.ctrl[-1] = action_arr[-1]

        mujoco.mj_forward(model, data)

        # Record endpoint position
        ee_pos = data.xpos[ee_body_id].copy()
        endpoint_trajectory.append(ee_pos.tolist())

        # Record qpos for inverse dynamics
        qpos_trajectory.append(data.qpos[:n_dof].copy())

        # Compute angular velocity (max joint delta per step)
        current_qpos = data.qpos[:n_apply_max].copy()
        delta = current_qpos - prev_qpos
        vel = float(np.max(np.abs(delta)))
        velocities.append(vel)
        prev_qpos = current_qpos.copy()

        # Joint limit check
        for i in range(min(n_apply_max, model.njnt)):
            if model.jnt_limited[i]:
                lo = model.jnt_range[i, 0]
                hi = model.jnt_range[i, 1]
                if data.qpos[i] < lo - 0.01 or data.qpos[i] > hi + 0.01:
                    joint_limits_ok = False
                    exceeded = data.qpos[i] - hi if data.qpos[i] > hi else lo - data.qpos[i]
                    joint_limit_details.append(
                        f"Joint {i} exceeded by {exceeded:.3f} rad at step {step_idx}"
                    )

        # Collision check
        if data.ncon > 0:
            for c in range(data.ncon):
                body1 = model.geom_bodyid[data.contact[c].geom1]
                body2 = model.geom_bodyid[data.contact[c].geom2]
                if body1 > 0 and body2 > 0:
                    self_collision = True

    # Compute trajectory statistics
    traj_arr = np.array(endpoint_trajectory)
    displacements = np.linalg.norm(np.diff(traj_arr, axis=0), axis=1)
    total_displacement = float(np.sum(displacements))

    direct_distance = float(np.linalg.norm(traj_arr[-1] - traj_arr[0]))
    smoothness_score = direct_distance / total_displacement if total_displacement > 1e-6 else 1.0
    smoothness_score = min(smoothness_score, 1.0)

    max_velocity = float(max(velocities)) if velocities else 0.0

    # --- Inverse Dynamics (torque feasibility) ---
    torque_feasible = True
    torque_detail = "feasible"
    peak_torques: list[float] = [0.0] * n_dof

    if len(qpos_trajectory) >= 3:
        dt = model.opt.timestep if model.opt.timestep > 0 else 0.002
        inv_data = mujoco.MjData(model)

        for step_idx in range(1, len(qpos_trajectory) - 1):
            q_prev = qpos_trajectory[step_idx - 1]
            q_curr = qpos_trajectory[step_idx]
            q_next = qpos_trajectory[step_idx + 1]

            qvel = (q_curr - q_prev) / dt
            qacc = (q_next - 2 * q_curr + q_prev) / (dt * dt)

            inv_data.qpos[:n_dof] = q_curr
            inv_data.qvel[:n_dof] = qvel
            inv_data.qacc[:n_dof] = qacc

            mujoco.mj_inverse(model, inv_data)

            torques = inv_data.qfrc_inverse[:n_dof].copy()

            for j in range(n_dof):
                abs_torque = abs(float(torques[j]))
                if abs_torque > peak_torques[j]:
                    peak_torques[j] = abs_torque

            if n_act > 0 and hasattr(model, "actuator_forcerange"):
                n_check = min(n_dof, n_act)
                for j in range(n_check):
                    limit_lo = model.actuator_forcerange[j, 0]
                    limit_hi = model.actuator_forcerange[j, 1]
                    if (limit_lo != 0.0 or limit_hi != 0.0) and (
                        float(torques[j]) < limit_lo or float(torques[j]) > limit_hi
                    ):
                        torque_feasible = False
                        torque_detail = (
                            f"exceeded at step {step_idx} joint {j} "
                            f"(torque {float(torques[j]):.2f}, limit {float(limit_hi):.2f})"
                        )

    peak_torques = [round(t, 4) for t in peak_torques]
    peak_torque_max = max(peak_torques) if peak_torques else 0.0

    # --- Gravity Compensation (payload hold feasibility) ---
    grasp_step = _find_grasp_step(actions) if actions else 0
    gravity_feasible = True

    if actions and grasp_step < len(qpos_trajectory):
        grasp_data = mujoco.MjData(model)
        grasp_data.qpos[:n_dof] = qpos_trajectory[min(grasp_step, len(qpos_trajectory) - 1)]
        mujoco.mj_forward(model, grasp_data)

        jacp = np.zeros((3, n_dof))
        jacr = np.zeros((3, n_dof))
        mujoco.mj_jac(model, grasp_data, jacp, jacr, data.xpos[ee_body_id], ee_body_id)

        gravity_force = np.array([0.0, 0.0, -9.81 * payload_kg])
        holding_torques = jacp.T @ gravity_force

        if n_act > 0 and hasattr(model, "actuator_forcerange"):
            n_check = min(n_dof, n_act)
            for j in range(n_check):
                limit_hi = model.actuator_forcerange[j, 1]
                if limit_hi != 0.0:
                    combined = abs(holding_torques[j]) + peak_torques[j]
                    if combined > abs(limit_hi):
                        gravity_feasible = False

    # --- Manipulability (singularity detection) ---
    manipulability = 0.0
    min_singular_value = 0.0
    near_singularity = False

    if actions and grasp_step < len(qpos_trajectory):
        manip_data = mujoco.MjData(model)
        manip_data.qpos[:n_dof] = qpos_trajectory[min(grasp_step, len(qpos_trajectory) - 1)]
        mujoco.mj_forward(model, manip_data)

        jacp = np.zeros((3, n_dof))
        jacr = np.zeros((3, n_dof))
        mujoco.mj_jac(model, manip_data, jacp, jacr, manip_data.xpos[ee_body_id], ee_body_id)

        full_jac = np.vstack([jacp, jacr])  # 6 x N
        singular_values = np.linalg.svd(full_jac, compute_uv=False)
        min_singular_value = float(np.min(singular_values)) if len(singular_values) > 0 else 0.0
        manipulability = float(np.prod(singular_values)) if len(singular_values) > 0 else 0.0
        near_singularity = min_singular_value < 0.01

    # Extract robot name from URDF filename
    robot_name = urdf.stem

    # --- Build evidence-structured output ---
    evidence: list[dict] = [
        {
            "field": "joint_limits_ok",
            "value": joint_limits_ok,
            "confidence": "hard",
            "source": evidence_source,
            "detail": "All joints within limits"
            if joint_limits_ok
            else "; ".join(joint_limit_details[:5]),
        },
        {
            "field": "self_collision",
            "value": self_collision,
            "confidence": "hard",
            "source": evidence_source,
            "detail": "Self-collision detected" if self_collision else "No self-collision detected",
        },
        {
            "field": "torque_feasible",
            "value": torque_feasible,
            "confidence": "hard",
            "source": "mj_inverse",
            "detail": torque_detail,
        },
        {
            "field": "endpoint_trajectory",
            "value": endpoint_trajectory,
            "confidence": "hard",
            "source": "mj_forward",
        },
        {
            "field": "manipulability",
            "value": round(manipulability, 6),
            "confidence": "hard",
            "source": "jacobian_svd",
            "detail": f"min singular value {min_singular_value:.4f}"
            + (" — near singularity" if near_singularity else ""),
        },
        {
            "field": "gravity_feasible",
            "value": gravity_feasible,
            "confidence": "hard",
            "source": "mj_inverse",
            "detail": f"feasible for {payload_kg}kg payload"
            if gravity_feasible
            else f"infeasible for {payload_kg}kg payload",
        },
        {
            "field": "near_singularity",
            "value": near_singularity,
            "confidence": "hard",
            "source": "jacobian_svd",
        },
    ]

    # Not-computed fields
    not_computed: list[dict] = []
    if is_ee_delta:
        not_computed.append(
            {
                "field": "exact_controller_trajectory",
                "reason": (
                    "Jacobian IK is a first-order approximation. "
                    "The robot's actual low-level controller may resolve EE deltas differently."
                ),
                "would_need": "Simulation with the robot's actual controller",
            }
        )

    gripper_events = _extract_gripper_events(actions)

    return {
        "analysis_mode": analysis_mode,
        "robot": {"name": robot_name, "dof": n_dof, "actuators": n_act},
        "evidence": evidence,
        "not_computed": not_computed,
        "gripper_events": gripper_events,
        "summary": {
            "total_displacement_m": round(total_displacement, 4),
            "smoothness_score": round(smoothness_score, 4),
            "max_velocity_rad_s": round(max_velocity, 4),
            "steps_analyzed": len(actions),
            "joint_limits_ok": joint_limits_ok,
            "torque_feasible": torque_feasible,
            "gravity_feasible": gravity_feasible,
            "self_collision": self_collision,
            "near_singularity": near_singularity,
            "peak_torque_nm": round(peak_torque_max, 4),
            "manipulability": round(manipulability, 6),
            "final_endpoint": endpoint_trajectory[-1] if endpoint_trajectory else [0, 0, 0],
        },
    }
