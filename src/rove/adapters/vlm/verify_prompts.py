"""LLM-as-judge prompt builder for the verify stage.

Loads prompt templates from ``src/rove/prompts/`` via :class:`PromptLoader`.
The stage-specific sections and ground-truth QA are assembled dynamically
and injected into the template placeholders.
"""

from __future__ import annotations

from rove.prompts import get_loader

_GT_OUTPUT_SPEC = """\
- "ground_truth": object with:
    - "question": string (the question)
    - "choices": list of strings
    - "correct_answer": string (the correct answer)
    - "pipeline_answer": string (your answer based on pipeline outputs)
    - "correct": boolean (whether pipeline_answer matches correct_answer)
    - "confidence": float 0-1"""


def build_verify_system_message() -> str:
    """System prompt for the verify stage VLM judge (loaded from file)."""
    return get_loader().load("verify_system")


def build_verify_prompt(task: str, context: dict) -> str:
    """Build a context-aware verify prompt that evaluates each completed stage.

    Reads ``completed_stages``, per-stage outputs, and optional ``ground_truth``
    from the pipeline context dict.  Renders the ``verify_user`` template.
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
            "Assess: Did the perceive stage correctly identify the relevant objects and spatial relationships for this task?"
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
        stage_lines.append("Assess: Were the predicted actions reasonable for executing the plan?")
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
            "Based on the pipeline outputs above, answer this question by selecting the correct choice."
        )

    loader = get_loader()
    return loader.render(
        "verify_user",
        task=task,
        completed_stages=", ".join(completed_stages) if completed_stages else "none",
        stage_sections="\n".join(stage_lines),
        ground_truth_section="\n".join(gt_lines),
        ground_truth_output_spec=_GT_OUTPUT_SPEC if ground_truth else "",
    )
