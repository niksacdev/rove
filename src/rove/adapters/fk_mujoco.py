"""Forward kinematics computation using MuJoCo.

Converts VLA action arrays (joint-space deltas) into Cartesian end-effector
positions so the verify stage can reason about spatial trajectories.

Requires: pip install rove-eval[kinematics]
"""

from __future__ import annotations

import logging
import tempfile
import xml.etree.ElementTree as ET  # nosec B405 — URDF is local trusted file
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


def _prepare_urdf_for_fk(urdf_path: Path) -> str:
    """Prepare a URDF for kinematics-only loading in MuJoCo.

    1. Strips <visual> and <collision> elements (avoids mesh file errors).
    2. Adds dummy <inertial> to links that lack them (MuJoCo requires mass > 0
       for all moving bodies).

    Returns path to the cleaned temp file.
    """
    tree = ET.parse(urdf_path)  # nosec B314 — URDF is a local trusted file
    root = tree.getroot()

    for link in root.iter("link"):
        # Strip visual and collision (we don't need meshes for FK)
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

    with tempfile.NamedTemporaryFile(delete=False, suffix=".urdf", prefix="rove_fk_") as tmp:
        tree.write(tmp, xml_declaration=True)
    return tmp.name


def compute_fk(
    urdf_path: str,
    actions: list[list[float]],
    initial_qpos: list[float] | None = None,
) -> dict:
    """Compute FK analysis for a VLA trajectory.

    Args:
        urdf_path: Path to URDF/MJCF file describing the robot.
        actions: List of action arrays (joint-space deltas, typically 7-DOF).
        initial_qpos: Optional initial joint positions. If None, uses model defaults.

    Returns:
        Dict with endpoint trajectory, joint limit checks, collision info,
        smoothness score, and trajectory statistics.
    """
    try:
        import mujoco
    except ImportError as err:
        raise ImportError(
            "MuJoCo is required for FK computation. Install with: pip install rove-eval[kinematics]"
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
        # URDF likely references mesh files MuJoCo can't find — strip them
        # and add dummy inertials since FK only needs the kinematic chain
        try:
            stripped = _prepare_urdf_for_fk(urdf)
            load_path = stripped
            model = mujoco.MjModel.from_xml_path(load_path)
            logger.info("Loaded URDF with meshes stripped for FK-only computation")
        except Exception as e2:
            raise ValueError(
                f"Failed to load URDF/MJCF '{urdf.name}': {e2}. "
                "Ensure the file is a valid URDF, MJCF, or MuJoCo XML."
            ) from e2

    data = mujoco.MjData(model)

    # Determine DOF counts
    n_act = model.nu  # number of actuators
    n_qpos = model.nq  # number of generalized coordinates
    # For FK we apply deltas to qpos directly; use nq if no actuators defined
    n_apply_max = n_qpos if n_act == 0 else n_act

    # Set initial joint positions
    if initial_qpos is not None:
        qpos = np.array(initial_qpos[: min(len(initial_qpos), n_qpos)], dtype=np.float64)
        data.qpos[: len(qpos)] = qpos
    mujoco.mj_forward(model, data)

    # Track end-effector via the last body in the kinematic chain
    ee_body_id = model.nbody - 1
    endpoint_trajectory: list[list[float]] = []
    velocities: list[float] = []
    joint_limits_ok = True
    self_collision = False

    # Record initial position
    ee_pos = data.xpos[ee_body_id].copy()
    endpoint_trajectory.append(ee_pos.tolist())

    prev_qpos = data.qpos[:n_apply_max].copy()

    for step_actions in actions:
        action_arr = np.array(step_actions, dtype=np.float64)

        # Apply deltas to joint positions (skip gripper DOF if action has extra)
        n_apply = min(len(action_arr), n_apply_max)
        data.qpos[:n_apply] += action_arr[:n_apply]

        # Handle gripper actuator if action has extra DOF and actuators exist
        if len(action_arr) > n_apply_max and n_act > 0:
            data.ctrl[-1] = action_arr[-1]

        mujoco.mj_forward(model, data)

        # Record endpoint position
        ee_pos = data.xpos[ee_body_id].copy()
        endpoint_trajectory.append(ee_pos.tolist())

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

        # Collision check (only if geometry is loaded)
        if data.ncon > 0:
            for c in range(data.ncon):
                body1 = model.geom_bodyid[data.contact[c].geom1]
                body2 = model.geom_bodyid[data.contact[c].geom2]
                # Self-collision: both geoms belong to the robot (not world body 0)
                if body1 > 0 and body2 > 0:
                    self_collision = True

    # Compute trajectory statistics
    traj_arr = np.array(endpoint_trajectory)
    displacements = np.linalg.norm(np.diff(traj_arr, axis=0), axis=1)
    total_displacement = float(np.sum(displacements))

    # Smoothness: ratio of direct distance to path length (1.0 = perfectly straight)
    direct_distance = float(np.linalg.norm(traj_arr[-1] - traj_arr[0]))
    smoothness_score = direct_distance / total_displacement if total_displacement > 1e-6 else 1.0
    smoothness_score = min(smoothness_score, 1.0)

    max_velocity = float(max(velocities)) if velocities else 0.0

    return {
        "endpoint_trajectory": endpoint_trajectory,
        "final_endpoint": endpoint_trajectory[-1] if endpoint_trajectory else [0, 0, 0],
        "joint_limits_ok": joint_limits_ok,
        "self_collision": self_collision,
        "total_displacement_m": round(total_displacement, 4),
        "max_velocity_rad_s": round(max_velocity, 4),
        "smoothness_score": round(smoothness_score, 4),
        "steps_analyzed": len(actions),
    }
