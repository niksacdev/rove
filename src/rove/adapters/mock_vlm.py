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

    async def _simulate_latency(self) -> None:
        lo, hi = self._latency_range
        await asyncio.sleep(random.uniform(lo, hi) / 1000.0)

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

    async def plan_task(self, image_base64: str, task: str, scene: SceneAnalysis) -> TaskPlan:
        await self._simulate_latency()
        # Use scene data to inform plan
        target = scene.task_relevant[0] if scene.task_relevant else "target object"
        return TaskPlan(
            strategy=f"Approach {target} from above",
            reasoning=(
                f"The {target} is accessible from above with no obstructions. "
                "A direct top-down approach minimizes collision risk and allows "
                f"precise alignment with the {target}."
            ),
            steps=[
                f"Identify {target} in scene",
                f"Move gripper above {target}",
                f"Lower and grasp {target}",
                "Lift to destination",
            ],
            target_object=target,
            confidence=0.88,
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
            passed = random.random() < self._quality
            if not passed:
                all_passed = False
            stage_checks.append(
                StageCheck(
                    stage=stage,
                    passed=passed,
                    confidence=random.uniform(0.7, 0.95) if passed else random.uniform(0.3, 0.6),
                    reasoning=f"{stage} stage output is {'consistent with' if passed else 'inconsistent with'} task requirements.",
                )
            )

        # Ground truth QA check
        gt_check: GroundTruthCheck | None = None
        if ground_truth_data:
            correct_answer = ground_truth_data.get("correct_answer_text", "")
            choices = ground_truth_data.get("choices", [])
            is_correct = random.random() < self._quality
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
                confidence=random.uniform(0.7, 0.95) if is_correct else random.uniform(0.3, 0.6),
            )

        # Generate action plausibility if act stage was completed
        plausibility = None
        if "act" in completed_stages:
            plausibility = ActionPlausibility(
                bounds_check=random.random() < self._quality,
                smoothness=random.random() < self._quality,
                gripper_consistency=random.random() < self._quality,
                plan_alignment=round(random.uniform(0.5, 0.95), 2),
                reasoning=(
                    "Action deltas are within expected ranges. "
                    "Gripper pattern is consistent with pick-and-place. "
                    "Note: true success cannot be assessed without a simulator "
                    "or post-execution image."
                ),
            )

        success = all_passed if (stage_checks or gt_check) else random.random() < self._quality

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
            confidence=random.uniform(0.7, 0.95) if success else random.uniform(0.3, 0.6),
            reasoning=reasoning,
            raw_response='{"mock": true}',
            completed_stages=completed_stages,
            stage_checks=stage_checks,
            ground_truth=gt_check,
            action_plausibility=plausibility,
        )

    async def health_check(self) -> bool:
        return True
