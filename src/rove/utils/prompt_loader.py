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

                # Dynamics analysis provides structured evidence when available
                dyn = context.get("dynamics_analysis")
                if dyn:
                    stage_lines.append("")
                    stage_lines.append("--- PHYSICS EVIDENCE (MuJoCo Dynamics) ---")
                    mode = dyn.get("analysis_mode", "unknown")
                    stage_lines.append(f"Analysis mode: {mode}")
                    stage_lines.append("")

                    # Evidence items
                    for item in dyn.get("evidence", []):
                        field = item.get("field", "")
                        value = item.get("value")
                        conf = item.get("confidence", "")
                        source = item.get("source", "")
                        detail = item.get("detail", "")

                        if field == "endpoint_trajectory":
                            traj = value or []
                            if traj:
                                stage_lines.append(
                                    f"EE trajectory: {traj[0]} → {traj[-1]} "
                                    f"({len(traj)} points) [confidence: {conf}, source: {source}]"
                                )
                                for i, pt in enumerate(traj[:5]):
                                    stage_lines.append(
                                        f"  step {i + 1}: [{pt[0]:.3f}, {pt[1]:.3f}, {pt[2]:.3f}]m"
                                    )
                                if len(traj) > 5:
                                    stage_lines.append(f"  ... ({len(traj)} total)")
                        else:
                            label = field.replace("_", " ").title()
                            detail_str = f" — {detail}" if detail else ""
                            stage_lines.append(
                                f"{label}: {'UNKNOWN / NOT COMPUTED' if value is None else value}{detail_str} "
                                f"[confidence: {conf}, source: {source}]"
                            )

                    # Summary stats
                    summary = dyn.get("summary", {})
                    if summary:
                        stage_lines.append("")
                        ep = summary.get("final_endpoint", [0, 0, 0])
                        stage_lines.append(
                            f"Final endpoint: [{ep[0]:.3f}, {ep[1]:.3f}, {ep[2]:.3f}]m"
                        )
                        stage_lines.append(
                            f"Displacement: {summary.get('total_displacement_m', 0):.3f}m, "
                            f"smoothness: {summary.get('smoothness_score', 0):.2f}, "
                            f"steps: {summary.get('steps_analyzed', 0)}"
                        )

                    # Gripper events
                    gevts = dyn.get("gripper_events", [])
                    if gevts:
                        stage_lines.append(
                            "Gripper: "
                            + ", ".join(f"{e['type']} at step {e['step']}" for e in gevts)
                        )

                    # Not computed
                    for nc in dyn.get("not_computed", []):
                        stage_lines.append(
                            f"NOT COMPUTED: {nc.get('field', '?')} — {nc.get('reason', '')}"
                        )

                    stage_lines.append("")
                    stage_lines.append(
                        "Use evidence confidence levels to calibrate your assessment. "
                        "'hard' means computed for the supplied model and state, not real-world ground truth. Unknown is neither pass nor fail. "
                        "Cross-reference dynamics positions with PERCEIVE object positions."
                    )
                else:
                    stage_lines.append("")
                    stage_lines.append(
                        "No dynamics data available. Assess action PLAUSIBILITY "
                        "from observable trajectory features only. "
                        "Set physics-dependent fields (bounds_check, dynamics_consistency, "
                        "workspace_reachability, safety_assessment) to null."
                    )

                stage_lines.append("")
                stage_lines.append(
                    "Return an 'action_plausibility' object with float 0-1 or null: "
                    "bounds_check, smoothness, gripper_consistency, plan_alignment, "
                    "workspace_reachability, task_completion_plausibility, "
                    "dynamics_consistency, safety_assessment, evidence_quality, reasoning. "
                    "Set fields to null when you lack evidence to assess them."
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

    def render_verify_loop_system(self, **_kwargs) -> str:
        """System prompt for the agentic verify loop.

        Single unified prompt handles all evidence levels (full dynamics,
        partial, none). The LLM reasons about evidence quality directly.
        """
        return self.load("verify_agent_loop_system")

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
