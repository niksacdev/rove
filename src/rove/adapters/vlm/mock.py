"""Mock VLM adapter for testing without API calls."""

from __future__ import annotations

import asyncio
import random

from rove.models import SceneAnalysis, TaskPlan, VerificationResult


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
                {"name": "red bracket", "bbox": [120, 80, 60, 40], "confidence": 0.94},
                {"name": "blue bolt", "bbox": [250, 150, 30, 25], "confidence": 0.88},
                {"name": "bin A", "bbox": [400, 200, 100, 80], "confidence": 0.96},
                {"name": "gripper", "bbox": [300, 50, 50, 60], "confidence": 0.91},
            ],
            spatial_relations=[
                "red bracket is to the left of bin A",
                "blue bolt is between red bracket and bin A",
                "gripper is above the workspace",
            ],
            task_relevant=["red bracket", "bin A"],
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
        success = random.random() < self._quality

        # Use pipeline context for richer reasoning
        target = "the object"
        plan_strategy = ""
        if context:
            plan = context.get("plan", {})
            target = plan.get("target_object") or target
            plan_strategy = plan.get("strategy", "")

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
        )

    async def health_check(self) -> bool:
        return True
