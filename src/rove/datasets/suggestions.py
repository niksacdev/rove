"""Editable review rubrics from case inputs, never inferred review decisions.

These deterministic suggestions use supplied tasks and reference annotations.
They do not invoke a vision model, execute a robot, or validate source labels.
"""

from __future__ import annotations


def _text(value, limit=1200):
    return value.strip()[:limit] if isinstance(value, str) else ""


def _items(value):
    return (
        [_text(item, 200) for item in value[:8] if _text(item, 200)]
        if isinstance(value, list)
        else []
    )


def suggest_success(case: dict) -> dict:
    """Return a bounded, directly editable SuccessContract plus its draft provenance."""
    public = case.get("candidate_context", {})
    references = case.get("reference_data", {})
    conditions = case.get("conditions", {})
    category = public.get("eval_category") or conditions.get("eval_category")
    task = _text(case.get("task"))
    scope = "scene_understanding" if category == "scene_analysis" else "plan_quality"
    criteria = []
    basis = ["task"]

    def add(identity, description):
        criteria.append(
            {
                "id": identity,
                "description": description[:2000],
                "assessment": "human_review",
                "required": True,
            }
        )

    add(
        "grounding",
        f'For the task "{task}", the output identifies the relevant objects and locations '
        "from the supplied image. It states uncertainty or asks for clarification when a target, "
        "destination, or required state cannot be established from that image.",
    )
    if scope == "plan_quality":
        add(
            "task_alignment",
            f'The proposed plan addresses "{task}" with a clear sequence and intended final '
            "arrangement. It does not claim that describing a plan or producing actions proves "
            "that the robot completed the task.",
        )
    subtasks = _items(references.get("expected_subtasks"))
    if subtasks:
        basis.append("reference_data.expected_subtasks")
        add(
            "reference_steps",
            "Where consistent with the image and instruction, the plan covers these source "
            "reference steps in the required order: " + "; ".join(subtasks) + ".",
        )
    constraints = _items(public.get("constraints"))
    if constraints:
        basis.append("candidate_context.constraints")
        add(
            "constraints",
            "The output explicitly respects the supplied constraints throughout its proposed "
            "steps: " + "; ".join(constraints) + ".",
        )
    correction = public.get("correction")
    feedback = _text(correction.get("feedback")) if isinstance(correction, dict) else ""
    if feedback:
        basis.append("candidate_context.correction")
        add(
            "correction",
            f'The proposed plan incorporates the supplied correction "{feedback}" and removes '
            "superseded targets or steps. This checks response to the supplied instruction; "
            "actual recovery during execution needs a recorded episode.",
        )
    interpretations = _items(references.get("acceptable_interpretations"))
    if interpretations:
        basis.append("reference_data.acceptable_interpretations")
        add(
            "interpretation",
            "The plan explains a reasonable interpretation grounded in the visible scene. "
            "Source examples (not the only acceptable answers): "
            + "; ".join(interpretations)
            + ". Ask for clarification if the intended outcome remains ambiguous.",
        )
    qa = references.get("eval_qa")
    if isinstance(qa, dict):
        question = _text(qa.get("question"), 900)
        answer = _text(qa.get("correct_answer_text"), 500)
        if question and answer:
            basis.append("reference_data.eval_qa")
            add(
                "source_reference",
                f'Check relevant scene or plan statements against the source question "{question}". '
                f'The source annotation says "{answer}". Confirm that annotation against the '
                "provided image before accepting it; missing views or overlays make this "
                "criterion unknown. This source label is not evidence of this trial's robot outcome.",
            )
    return {
        "status": "draft",
        "requires_confirmation": True,
        "basis": basis,
        "evidence_note": "Suggested from the task and available source annotations. Review the "
        "image and edit these expectations before using them. A single input image supports "
        "scene or plan review; it does not establish physical task completion. No expert "
        "rating or success decision has been recorded.",
        "contract": {
            "name": (_text(case.get("name"), 130) or task[:130]) + " — output review",
            "scope": scope,
            "evidence_mode": "candidate_output",
            "criteria": criteria,
            "metrics": ["task_success", "pass_at_k", "pass_pow_k", "pipeline_latency"],
        },
    }
