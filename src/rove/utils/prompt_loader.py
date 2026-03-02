"""Prompt management for ROVE pipeline stages."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

_GT_OUTPUT_SPEC = """\
- "ground_truth": object with:
    - "question": string (the question)
    - "choices": list of strings
    - "correct_answer": string (the correct answer)
    - "pipeline_answer": string (your answer based on pipeline outputs)
    - "correct": boolean (whether pipeline_answer matches correct_answer)
    - "confidence": float 0-1"""


class PromptManager:
    """Load, render, and assemble prompt templates for all pipeline stages.

    Templates use Python str.format() placeholders (e.g. ``{task}``).
    Loaded templates are cached for the lifetime of the process.
    """

    def __init__(self, prompts_dir: Path | None = None):
        self._dir = prompts_dir or _PROMPTS_DIR
        self._cache: dict[str, str] = {}

    def load(self, name: str) -> str:
        """Load a prompt template by name (without extension).

        Looks for ``<name>.txt`` in the prompts directory.
        """
        if name in self._cache:
            return self._cache[name]
        path = self._dir / f"{name}.txt"
        if not path.exists():
            raise FileNotFoundError(f"Prompt template not found: {path}")
        text = path.read_text().strip()
        self._cache[name] = text
        return text

    def render(self, name: str, **kwargs: str) -> str:
        """Load and render a prompt template with the given variables.

        Missing placeholders are left as-is (uses format_map with a
        defaultdict-like fallback).
        """
        template = self.load(name)
        return template.format_map(_SafeDict(kwargs))

    def render_verify_system(self) -> str:
        """System prompt for the verify stage VLM judge."""
        return self.load("verify_system")

    def render_verify(self, task: str, context: dict) -> str:
        """Build a context-aware verify prompt that evaluates each completed stage.

        Reads ``completed_stages``, per-stage outputs, and optional ``ground_truth``
        from the pipeline context dict. Renders the ``verify_user`` template.
        """
        completed_stages: list[str] = context.get("completed_stages", [])

        # Build per-stage sections
        stage_lines: list[str] = []

        if "perceive" in completed_stages:
            scene = context.get("scene", {})
            objects = scene.get("objects", [])
            obj_summaries = []
            for o in objects:
                name = o.get("name", "?")
                pos = o.get("position")
                if pos:
                    obj_summaries.append(f"{name} at [{pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f}]m")
                else:
                    obj_summaries.append(name)
            relations = scene.get("spatial_relations", [])
            relevant = scene.get("task_relevant", [])
            stage_lines.append("--- PERCEIVE stage output ---")
            stage_lines.append(f"Detected objects: {', '.join(obj_summaries)}")
            stage_lines.append(f"Spatial relations: {'; '.join(relations)}")
            stage_lines.append(f"Task-relevant objects: {', '.join(relevant)}")
            env_dist = scene.get("environment_distribution", "")
            if env_dist:
                stage_lines.append(f"Environment: {env_dist}")
            stage_lines.append(
                "Assess: Did the perceive stage correctly identify the relevant objects"
                " and spatial relationships for this task?"
            )
            stage_lines.append("")

        if "plan" in completed_stages:
            plan = context.get("plan", {})
            stage_lines.append("--- PLAN stage output ---")
            stage_lines.append(f"Strategy: {plan.get('strategy', 'N/A')}")
            stage_lines.append(f"Target object: {plan.get('target_object', 'N/A')}")
            steps = plan.get("steps", [])
            stage_lines.append(f"Steps: {'; '.join(steps) if steps else 'N/A'}")
            stage_lines.append(f"Confidence: {plan.get('confidence', 'N/A')}")
            repertoire = plan.get("task_repertoire", [])
            if repertoire:
                stage_lines.append(f"Task repertoire: {'; '.join(repertoire)}")
            artifacts = plan.get("artifacts", [])
            if artifacts:
                stage_lines.append(f"Success artifacts: {'; '.join(artifacts)}")
            degradation = plan.get("degradation_profile", [])
            if degradation:
                stage_lines.append(f"Degradation profile: {'; '.join(degradation)}")
            stage_lines.append(
                "Assess: Is the plan feasible and appropriate for the task given the scene?"
            )
            stage_lines.append(
                "If environment_distribution, task_repertoire, artifacts, or degradation_profile "
                "are missing or shallow, note this as a weakness — these fields help the robot "
                "anticipate domain-specific challenges and define concrete success criteria."
            )
            stage_lines.append("")

        if "act" in completed_stages:
            action = context.get("action", {})
            stage_lines.append("--- ACT stage output ---")
            stage_lines.append(f"Action type: {action.get('action_type', 'N/A')}")
            stage_lines.append(f"Num steps: {action.get('num_steps', 'N/A')}")
            stage_lines.append(f"Confidence: {action.get('confidence', 'N/A')}")

            # Include trajectory summary for plausibility analysis
            actions = action.get("actions", [])
            if actions and isinstance(actions[0], list):
                dof_labels = ["dx", "dy", "dz", "rx", "ry", "rz", "grip"]
                num_dof = len(actions[0])
                header = ", ".join(
                    dof_labels[i] if i < len(dof_labels) else f"d{i}" for i in range(num_dof)
                )
                total_steps = action.get("num_steps", len(actions))

                # Gripper events are the primary evidence for manipulation tasks
                gripper_events = action.get("gripper_events", [])
                if gripper_events:
                    stage_lines.append(f"Trajectory: {total_steps} steps, {num_dof}-DOF ({header})")
                    stage_lines.append("")
                    stage_lines.append("Gripper events in trajectory:")
                    for evt in gripper_events:
                        stage_lines.append(
                            f"  Step {evt['step']}/{total_steps}: gripper {evt['action']}"
                            f" (value={evt['grip_value']:.3f})"
                        )
                    n_close = sum(1 for e in gripper_events if e["action"] == "close")
                    n_open = sum(1 for e in gripper_events if e["action"] == "open")
                    stage_lines.append(f"  Summary: {n_close} grasp(es), {n_open} release(s)")
                else:
                    stage_lines.append(
                        f"Trajectory: {total_steps} steps, {num_dof}-DOF"
                        f" ({header}). No gripper events detected."
                    )

                # Show sample steps for reference
                stage_lines.append("")
                stage_lines.append("Sample trajectory steps:")
                for i, step in enumerate(actions):
                    if action.get("actions_truncated") and i >= 5:
                        step_num = total_steps - (len(actions) - 1 - i)
                    else:
                        step_num = i + 1
                    vals = ", ".join(f"{v:+.4f}" for v in step)
                    stage_lines.append(f"  step {step_num}: [{vals}]")
                if action.get("actions_truncated"):
                    stage_lines.append(f"  (first 5 + last 5 of {total_steps})")

                # Dynamics analysis provides spatial context when available
                dyn = context.get("dynamics_analysis")
                if dyn:
                    stage_lines.append("")
                    stage_lines.append("--- MUJOCO DYNAMICS ANALYSIS ---")
                    ep = dyn.get("final_endpoint", [0, 0, 0])
                    stage_lines.append(
                        f"Final end-effector position: [{ep[0]:.3f}, {ep[1]:.3f}, {ep[2]:.3f}] meters"
                    )
                    stage_lines.append(
                        f"Total displacement: {dyn.get('total_displacement_m', 0):.3f}m"
                    )
                    stage_lines.append(
                        "Joint limits: "
                        + ("all within bounds" if dyn.get("joint_limits_ok") else "EXCEEDED")
                    )
                    stage_lines.append(
                        "Self-collision: "
                        + ("DETECTED" if dyn.get("self_collision") else "none detected")
                    )
                    stage_lines.append(f"Smoothness score: {dyn.get('smoothness_score', 0):.2f}")
                    stage_lines.append(
                        f"Max angular velocity: {dyn.get('max_velocity_rad_s', 0):.2f} rad/s"
                    )
                    stage_lines.append(f"Steps analyzed: {dyn.get('steps_analyzed', 0)}")

                    # Inverse dynamics
                    stage_lines.append("")
                    torque_ok = dyn.get("torque_feasible", True)
                    stage_lines.append(
                        "Torque feasibility: " + ("feasible" if torque_ok else "VIOLATED")
                    )
                    peak = dyn.get("peak_torques", [])
                    if peak:
                        stage_lines.append(
                            f"  Peak torques: [{', '.join(f'{t:.2f}' for t in peak)}]"
                        )
                    violations = dyn.get("torque_violations", [])
                    for v in violations[:5]:
                        stage_lines.append(
                            f"  Violation: step {v['step']} joint {v['joint']}"
                            f" (torque {v['torque']:.2f} > limit {v['limit']:.2f})"
                        )

                    # Gravity compensation
                    payload = dyn.get("payload_kg", 0.5)
                    grav_ok = dyn.get("gravity_feasible", True)
                    stage_lines.append(
                        f"Gravity compensation at grasp ({payload}kg payload): "
                        + ("feasible" if grav_ok else "INFEASIBLE")
                    )
                    grav_torques = dyn.get("gravity_torques", [])
                    if grav_torques:
                        stage_lines.append(
                            f"  Holding torques: [{', '.join(f'{t:.2f}' for t in grav_torques)}]"
                        )

                    # Manipulability
                    manip = dyn.get("manipulability", 0.0)
                    min_sv = dyn.get("min_singular_value", 0.0)
                    sing = dyn.get("near_singularity", False)
                    stage_lines.append(f"Manipulability at grasp: {manip:.4f}")
                    stage_lines.append(f"  Min singular value: {min_sv:.4f}")
                    stage_lines.append(
                        "  Near singularity: " + ("YES — reduced dexterity" if sing else "no")
                    )

                    traj = dyn.get("endpoint_trajectory", [])
                    if traj:
                        stage_lines.append("")
                        stage_lines.append("End-effector trajectory (sample):")
                        for i, pt in enumerate(traj[:5]):
                            stage_lines.append(
                                f"  step {i + 1}: [{pt[0]:.3f}, {pt[1]:.3f}, {pt[2]:.3f}]m"
                            )
                        if len(traj) > 5:
                            stage_lines.append(f"  ... ({len(traj)} total steps)")

                    stage_lines.append("")
                    stage_lines.append(
                        "IMPORTANT: Dynamics analysis provides SPATIAL CONTEXT for the VLA trajectory."
                        " You can now reason about WHERE the robot arm moves, not just the raw deltas."
                        " Use the end-effector positions to assess:\n"
                        "- Does the trajectory reach the target object's perceived position?\n"
                        "- Does the lift height clear obstacles?\n"
                        "- Is the placement position near the goal?\n"
                        "- Do joint limit violations, self-collisions, or torque violations"
                        " indicate an unsafe plan?\n"
                        "- Does near-singularity suggest the grasp pose is poorly configured?\n"
                        "Cross-reference dynamics positions with the PERCEIVE stage's object positions."
                    )
                else:
                    stage_lines.append("")
                    stage_lines.append(
                        "IMPORTANT: Without before/after images from a simulator,"
                        " you CANNOT determine whether the trajectory reached the"
                        " correct object or destination. Assess action PLAUSIBILITY,"
                        " not action SUCCESS. Evaluate what IS observable:\n"
                        "- Does the gripper open/close pattern match the task"
                        " (e.g., pick-and-place needs close->open)?\n"
                        "- Is the number of steps reasonable?\n"
                        "- Are the delta magnitudes physically plausible?\n"
                        "- Does the trajectory show distinct phases (approach,"
                        " grasp, transport, place)?\n"
                        "- How faithfully does the VLA trajectory follow the planned steps?\n"
                        "Do NOT fail the act stage solely because you cannot verify"
                        " the exact target position from delta values alone."
                    )

                stage_lines.append("")
                stage_lines.append(
                    "Return an 'action_plausibility' object with: bounds_check (bool),"
                    " smoothness (bool), gripper_consistency (bool),"
                    " plan_alignment (float 0-1), reasoning (string),"
                    " workspace_reachability (float 0-1),"
                    " task_completion_plausibility (float 0-1),"
                    " dynamics_consistency (float 0-1),"
                    " safety_assessment (float 0-1)."
                )
            else:
                stage_lines.append(
                    "Assess: Were the predicted actions reasonable for executing the plan?"
                )
            stage_lines.append("")

        if not completed_stages:
            stage_lines.append("No pipeline stages completed before verification.")
            stage_lines.append("")

        # Scope instruction: tell verifier what stages were/weren't in the pipeline
        if completed_stages and "act" not in completed_stages:
            stage_lines.append("--- EVALUATION SCOPE ---")
            stage_lines.append(
                "This pipeline did NOT include an act (execution) stage."
                " The robot was not expected to physically perform the task."
                " Evaluate ONLY the stages listed above (perceive and/or plan)."
                " Do NOT penalize the pipeline for missing execution — that was"
                " not part of this evaluation configuration."
            )
            stage_lines.append("")

        # Ground truth QA section
        gt_lines: list[str] = []
        ground_truth = context.get("ground_truth")
        if ground_truth:
            gt_lines.append("--- GROUND TRUTH QA ---")
            gt_lines.append(f"Question: {ground_truth.get('question', '')}")
            choices = ground_truth.get("choices", [])
            for i, choice in enumerate(choices):
                gt_lines.append(f"  {i}: {choice}")
            gt_lines.append(
                "Based on the pipeline outputs above, answer this question"
                " by selecting the correct choice."
            )

        return self.render(
            "verify_user",
            task=task,
            completed_stages=", ".join(completed_stages) if completed_stages else "none",
            stage_sections="\n".join(stage_lines),
            ground_truth_section="\n".join(gt_lines),
            ground_truth_output_spec=_GT_OUTPUT_SPEC if ground_truth else "",
        )

    def render_verify_loop_system(self, has_dynamics: bool = True) -> str:
        """System prompt for the agentic verify loop.

        Selects the appropriate prompt based on whether dynamics tools are available.
        The two prompts are fundamentally different — not patched versions of each other.
        """
        if has_dynamics:
            return self.load("verify_agent_loop_system_dynamics")
        return self.load("verify_agent_loop_system_no_dynamics")

    def render_verify_loop_user(self, task: str, action_summary: str, robot_spec: str = "") -> str:
        """User prompt for the agentic verify loop with action data and robot spec."""
        return self.render(
            "verify_agent_loop_user",
            task=task,
            action_summary=action_summary,
            robot_spec=robot_spec,
        )

    @staticmethod
    def format_action_summary(action: dict) -> str:
        """Format a compact VLA output description for the verify loop user prompt."""
        action_type = action.get("action_type", "unknown")
        num_steps = action.get("num_steps", 0)
        confidence = action.get("confidence", 0.0)

        lines = [
            f"Action type: {action_type}",
            f"Steps: {num_steps}",
            f"VLA confidence: {confidence:.2f}",
        ]

        actions = action.get("actions", [])
        if actions and isinstance(actions[0], list):
            dof = len(actions[0])
            lines.append(f"DOF: {dof}")

            # Gripper events
            gripper_events = action.get("gripper_events", [])
            if gripper_events:
                n_close = sum(1 for e in gripper_events if e.get("action") == "close")
                n_open = sum(1 for e in gripper_events if e.get("action") == "open")
                lines.append(f"Gripper events: {n_close} grasp(es), {n_open} release(s)")
                for evt in gripper_events[:5]:
                    lines.append(
                        f"  Step {evt['step']}/{num_steps}: "
                        f"gripper {evt['action']} (value={evt.get('grip_value', 0):.3f})"
                    )

            # Sample first/last step
            if len(actions) >= 2:
                first = ", ".join(f"{v:+.4f}" for v in actions[0])
                last = ", ".join(f"{v:+.4f}" for v in actions[-1])
                lines.append(f"First step: [{first}]")
                lines.append(f"Last step:  [{last}]")

        elif action_type == "tool_calls":
            tool_calls = action.get("tool_calls", [])
            lines.append(f"Tool calls: {len(tool_calls)}")
            for tc in tool_calls[:5]:
                lines.append(f"  - {tc.get('tool', '?')}({tc.get('args', {})})")

        return "\n".join(lines)


class _SafeDict(dict):
    """Dict subclass that returns the key wrapped in braces for missing keys."""

    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


# Module-level singleton for convenience
_default_manager: PromptManager | None = None


def get_prompt_manager(prompts_dir: Path | None = None) -> PromptManager:
    """Get the default PromptManager (creates on first call)."""
    global _default_manager
    if _default_manager is None or prompts_dir is not None:
        _default_manager = PromptManager(prompts_dir)
    return _default_manager
