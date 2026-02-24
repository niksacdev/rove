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
            obj_names = [o.get("name", "?") for o in objects]
            relations = scene.get("spatial_relations", [])
            relevant = scene.get("task_relevant", [])
            stage_lines.append("--- PERCEIVE stage output ---")
            stage_lines.append(f"Detected objects: {', '.join(obj_names)}")
            stage_lines.append(f"Spatial relations: {'; '.join(relations)}")
            stage_lines.append(f"Task-relevant objects: {', '.join(relevant)}")
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
            stage_lines.append(
                "Assess: Is the plan feasible and appropriate for the task given the scene?"
            )
            stage_lines.append("")

        if "act" in completed_stages:
            action = context.get("action", {})
            stage_lines.append("--- ACT stage output ---")
            stage_lines.append(f"Action type: {action.get('action_type', 'N/A')}")
            stage_lines.append(f"Num steps: {action.get('num_steps', 'N/A')}")
            stage_lines.append(f"Confidence: {action.get('confidence', 'N/A')}")

            # Include trajectory vectors for plausibility analysis
            actions = action.get("actions", [])
            if actions and isinstance(actions[0], list):
                dof_labels = ["dx", "dy", "dz", "rx", "ry", "rz", "grip"]
                num_dof = len(actions[0])
                header = ", ".join(
                    dof_labels[i] if i < len(dof_labels) else f"d{i}" for i in range(num_dof)
                )
                stage_lines.append(f"Trajectory ({num_dof}-DOF: {header}):")
                for i, step in enumerate(actions):
                    vals = ", ".join(f"{v:+.4f}" for v in step)
                    stage_lines.append(f"  step {i + 1}: [{vals}]")
                if action.get("actions_truncated"):
                    stage_lines.append(f"  ... (truncated, {action['num_steps']} total)")

                stage_lines.append("")
                stage_lines.append(
                    "Assess the trajectory plausibility:\n"
                    "- Are magnitudes physically reasonable for end-effector deltas"
                    " (typically ±0.05m per step)?\n"
                    "- Is the trajectory direction consistent with the plan"
                    " (e.g., 'move left' → negative dx)?\n"
                    "- Does the gripper open/close at appropriate steps"
                    " (grip ≈ 1.0 = open, ≈ 0.0 = closed)?\n"
                    "- Is the number of steps reasonable for the task complexity?"
                )
            else:
                stage_lines.append(
                    "Assess: Were the predicted actions reasonable for executing the plan?"
                )
            stage_lines.append("")

        if not completed_stages:
            stage_lines.append("No pipeline stages completed before verification.")
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
