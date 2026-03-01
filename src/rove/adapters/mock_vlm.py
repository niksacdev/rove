"""Mock VLM adapter for testing without API calls."""

from __future__ import annotations

import asyncio
import random

from rove.models import (
    ActionPlausibility,
    GroundTruthCheck,
    SceneAnalysis,
    StageCheck,
    TaskPlan,
    VerificationResult,
)


class MockVLMAdapter:
    def __init__(self, model_id: str = "mock-vlm", config: dict | None = None):
        self.model_id = model_id
        self.display_name = "Mock VLM"
        cfg = config or {}
        self._quality = cfg.get("mock_quality", 0.85)
        self._latency_range = cfg.get("mock_latency_ms", [200, 500])
        seed = cfg.get("seed")
        self._rng = random.Random(seed) if seed is not None else random.Random()

    async def _simulate_latency(self) -> None:
        lo, hi = self._latency_range
        await asyncio.sleep(self._rng.uniform(lo, hi) / 1000.0)

    async def analyze_scene(self, image_base64: str, task: str) -> SceneAnalysis:
        await self._simulate_latency()
        return SceneAnalysis(
            objects=[
                {
                    "name": "red bracket",
                    "bbox": [120, 80, 60, 40],
                    "position": [0.25, -0.10, 0.32],
                    "confidence": 0.94,
                },
                {
                    "name": "blue bolt",
                    "bbox": [250, 150, 30, 25],
                    "position": [0.35, 0.05, 0.31],
                    "confidence": 0.88,
                },
                {
                    "name": "bin A",
                    "bbox": [400, 200, 100, 80],
                    "position": [0.50, 0.15, 0.30],
                    "confidence": 0.96,
                },
                {
                    "name": "gripper",
                    "bbox": [300, 50, 50, 60],
                    "position": [0.30, 0.00, 0.45],
                    "confidence": 0.91,
                },
            ],
            spatial_relations=[
                "red bracket is to the left of bin A",
                "blue bolt is between red bracket and bin A",
                "gripper is above the workspace",
            ],
            task_relevant=["red bracket", "bin A"],
            environment_distribution="Indoor tabletop workspace with scattered industrial parts, overhead lighting, light clutter",
            raw_response='{"mock": true, "quality": ' + str(self._quality) + "}",
        )

    def _detect_task_category(self, task: str) -> str:
        """Infer eval_category from task text for category-aware mock outputs."""
        task_lower = task.lower()
        # Negative: "except", "don't", "not the", "only the"
        if any(
            kw in task_lower
            for kw in ("except", "don't", "not the", "but don't", "only the", "leave the")
        ):
            return "negative"
        # Constrained: "but", "keep it", "allergic", "upright", "careful"
        if any(
            kw in task_lower for kw in ("but ", "allergic", "upright", "careful", "don't reach")
        ):
            return "constrained"
        # Open-ended: questions, vague verbs
        if any(kw in task_lower for kw in ("can we", "what can", "tidier", "ready?", "something ")):
            return "open_ended"
        # Multi-stage: "and", "then", "both"
        if any(kw in task_lower for kw in (" and ", " then ", "both ")):
            return "multi_stage"
        return "atomic"

    async def plan_task(
        self, image_base64: str, task: str, scene: SceneAnalysis, **kwargs
    ) -> TaskPlan:
        await self._simulate_latency()
        # Use scene data to inform plan
        target = scene.task_relevant[0] if scene.task_relevant else "target object"
        category = self._detect_task_category(task)

        # Structured reasoning fields — vary by category
        subtask_reasoning = ""
        action_reasoning = ""
        constraints_acknowledged: list[str] = []

        if category == "multi_stage":
            subtask_reasoning = (
                f"This is a multi-step task. The first subtask is to interact with {target} "
                "because it is the nearest relevant object and must be handled before subsequent steps."
            )
            action_reasoning = (
                f"The {target} is on the workspace surface. The robot needs to approach from above "
                "to grasp it before proceeding to the next stage of the task."
            )
            steps = [
                f"Locate {target} in scene",
                f"Grasp {target}",
                f"Move {target} to intermediate position",
                "Proceed to next subtask",
                "Complete final placement",
            ]
        elif category == "constrained":
            # Extract constraints from task text
            constraints_acknowledged = [
                c
                for c in task.split(",")
                if any(kw in c.lower() for kw in ("but", "don't", "keep", "allergic", "upright"))
            ]
            subtask_reasoning = (
                f"The task has explicit constraints that must be satisfied. "
                f"Planning {target} manipulation with constraint-aware path."
            )
            action_reasoning = (
                f"The {target} must be approached via a safe route that respects "
                "the user's stated constraints. Adjusting trajectory accordingly."
            )
            steps = [
                f"Identify {target} and constraint zones",
                "Plan constraint-respecting path",
                f"Grasp {target} via safe route",
                "Deliver to destination while maintaining constraints",
            ]
        elif category == "open_ended":
            subtask_reasoning = (
                "This is an open-ended request requiring semantic interpretation. "
                f"Identifying relevant items in the scene to satisfy the intent: '{task}'."
            )
            action_reasoning = (
                "Multiple valid interpretations exist. Selecting the most likely intent "
                "based on visible objects and common-sense reasoning about the context."
            )
            steps = [
                "Interpret user intent from context",
                "Identify relevant items in scene",
                f"Select {target} as best match",
                "Execute retrieval/arrangement",
            ]
        elif category == "negative":
            # Extract exclusion targets
            constraints_acknowledged = [f"Exclude: items matching negation in '{task}'"]
            subtask_reasoning = (
                "This task requires filtering — some items must be excluded from the action set. "
                "Identifying which items to act on and which to leave untouched."
            )
            action_reasoning = (
                f"The {target} is in the inclusion set. Other items matching the exclusion "
                "criteria will be left in place."
            )
            steps = [
                "Identify all candidate items",
                "Filter out excluded items",
                f"Act on {target} (included)",
                "Verify excluded items untouched",
            ]
        else:
            # atomic — original behavior
            subtask_reasoning = (
                f"Single-step task: the {target} needs to be grasped and moved. "
                "No decomposition needed."
            )
            action_reasoning = (
                f"The {target} is accessible from above with no obstructions. "
                "Moving gripper directly above for top-down approach."
            )
            steps = [
                f"Identify {target} in scene",
                f"Move gripper above {target}",
                f"Lower and grasp {target}",
                "Lift to destination",
            ]

        return TaskPlan(
            strategy=f"Approach {target} from above",
            reasoning=(
                f"The {target} is accessible from above with no obstructions. "
                "A direct top-down approach minimizes collision risk and allows "
                f"precise alignment with the {target}."
            ),
            steps=steps,
            target_object=target,
            confidence=0.88,
            subtask_reasoning=subtask_reasoning,
            action_reasoning=action_reasoning,
            constraints_acknowledged=constraints_acknowledged,
            task_repertoire=["top-down grasping", "precise placement", "obstacle-aware transport"],
            artifacts=[
                f"{target} placed in destination bin",
                f"{target} no longer at original position",
            ],
            degradation_profile=[
                "small part size requires precise gripper alignment",
                "nearby objects risk collision during transport",
            ],
            raw_response='{"mock": true}',
        )

    async def verify_success(
        self,
        before_image_base64: str,
        after_image_base64: str,
        task: str,
        context: dict | None = None,
    ) -> VerificationResult:
        await self._simulate_latency()

        # Read completed stages and ground truth from context
        completed_stages: list[str] = []
        ground_truth_data: dict | None = None
        target = "the object"
        plan_strategy = ""
        if context:
            completed_stages = context.get("completed_stages", [])
            ground_truth_data = context.get("ground_truth")
            plan = context.get("plan", {})
            target = plan.get("target_object") or target
            plan_strategy = plan.get("strategy", "")

        # Generate per-stage checks
        stage_checks: list[StageCheck] = []
        all_passed = True
        for stage in completed_stages:
            passed = self._rng.random() < self._quality
            if not passed:
                all_passed = False
            stage_checks.append(
                StageCheck(
                    stage=stage,
                    passed=passed,
                    confidence=self._rng.uniform(0.7, 0.95)
                    if passed
                    else self._rng.uniform(0.3, 0.6),
                    reasoning=f"{stage} stage output is {'consistent with' if passed else 'inconsistent with'} task requirements.",
                )
            )

        # Ground truth QA check
        gt_check: GroundTruthCheck | None = None
        if ground_truth_data:
            correct_answer = ground_truth_data.get("correct_answer_text", "")
            choices = ground_truth_data.get("choices", [])
            is_correct = self._rng.random() < self._quality
            pipeline_answer = (
                correct_answer
                if is_correct
                else (
                    choices[0]
                    if choices and choices[0] != correct_answer
                    else choices[-1]
                    if choices
                    else "Unknown"
                )
            )
            if not is_correct:
                all_passed = False
            gt_check = GroundTruthCheck(
                question=ground_truth_data.get("question", ""),
                choices=choices,
                correct_answer=correct_answer,
                pipeline_answer=pipeline_answer,
                correct=is_correct,
                confidence=self._rng.uniform(0.7, 0.95)
                if is_correct
                else self._rng.uniform(0.3, 0.6),
            )

        # Generate action plausibility if act stage was completed
        plausibility = None
        dyn_data = context.get("dynamics_analysis") if context else None
        if "act" in completed_stages:
            if dyn_data:
                # Derive scores from dynamics analysis instead of random
                dyn_bounds = dyn_data.get("joint_limits_ok", True) and not dyn_data.get(
                    "self_collision", False
                )
                dyn_disp = dyn_data.get("total_displacement_m", 0.0)
                dyn_smooth = dyn_data.get("smoothness_score", 1.0)
                dyn_smoothness = dyn_disp < 2.0 and dyn_smooth > 0.1
                dyn_torque_ok = dyn_data.get("torque_feasible", True)
                dyn_singularity = dyn_data.get("near_singularity", False)
                dyn_plan_align = round(
                    min(dyn_smooth, 0.3 if (not dyn_bounds or dyn_singularity) else 1.0), 2
                )

                reasons = []
                if not dyn_bounds:
                    reasons.append("Dynamics: joint limits exceeded or self-collision detected")
                    all_passed = False
                if not dyn_smoothness:
                    reasons.append(
                        f"Dynamics: displacement {dyn_disp:.1f}m or smoothness "
                        f"{dyn_smooth:.2f} out of range"
                    )
                    all_passed = False
                if not dyn_torque_ok:
                    reasons.append("Dynamics: torque limits exceeded")
                    all_passed = False
                if dyn_singularity:
                    reasons.append("Dynamics: near singularity at grasp configuration")
                if not reasons:
                    reasons.append(
                        "Dynamics: all checks passed, trajectory is physically plausible"
                    )

                plausibility = ActionPlausibility(
                    bounds_check=dyn_bounds and dyn_torque_ok,
                    smoothness=dyn_smoothness,
                    gripper_consistency=self._rng.random() < self._quality,
                    plan_alignment=dyn_plan_align,
                    reasoning=" ".join(reasons),
                )
            else:
                plausibility = ActionPlausibility(
                    bounds_check=self._rng.random() < self._quality,
                    smoothness=self._rng.random() < self._quality,
                    gripper_consistency=self._rng.random() < self._quality,
                    plan_alignment=round(self._rng.uniform(0.5, 0.95), 2),
                    reasoning=(
                        "Action deltas are within expected ranges. "
                        "Gripper pattern is consistent with pick-and-place. "
                        "Note: true success cannot be assessed without a simulator "
                        "or post-execution image."
                    ),
                )

        success = all_passed if (stage_checks or gt_check) else self._rng.random() < self._quality

        if success:
            reasoning = (
                f"The {target} has been successfully moved to its destination. "
                f"The {target} is no longer at its original position."
            )
            if plan_strategy:
                reasoning += f" Strategy '{plan_strategy}' executed correctly."
        else:
            reasoning = (
                f"The {target} appears to have been dropped during transfer. "
                "It is not visible at the destination."
            )

        return VerificationResult(
            success=success,
            confidence=self._rng.uniform(0.7, 0.95) if success else self._rng.uniform(0.3, 0.6),
            reasoning=reasoning,
            raw_response='{"mock": true}',
            completed_stages=completed_stages,
            stage_checks=stage_checks,
            ground_truth=gt_check,
            action_plausibility=plausibility,
        )

    async def health_check(self) -> bool:
        return True
