"""Mock policy adapter for testing without real VLA models."""

from __future__ import annotations

import asyncio
import random

from rove.models import ActionPrediction, TaskPlan


class MockPolicyAdapter:
    def __init__(self, model_id: str = "mock-vla", config: dict | None = None):
        self.model_id = model_id
        self.display_name = "Mock VLA"
        cfg = config or {}
        self._latency_range = cfg.get("mock_latency_ms", [50, 150])

    async def predict_action(
        self,
        image_base64: str,
        task: str,
        proprioception: list[float] | None = None,
        plan: TaskPlan | None = None,
    ) -> ActionPrediction:
        lo, hi = self._latency_range
        await asyncio.sleep(random.uniform(lo, hi) / 1000.0)

        # Generate 5 synthetic 7-DOF action steps
        num_steps = 5
        actions = []
        for i in range(num_steps):
            t = i / num_steps
            actions.append([
                round(random.uniform(-0.02, 0.02), 4),  # dx
                round(random.uniform(-0.02, 0.02), 4),  # dy
                round(-0.01 * (1 - t), 4),               # dz (moving down)
                round(random.uniform(-0.05, 0.05), 4),   # rx
                round(random.uniform(-0.05, 0.05), 4),   # ry
                round(random.uniform(-0.05, 0.05), 4),   # rz
                round(1.0 if i < num_steps - 1 else 0.0, 1),  # gripper (close at end)
            ])

        return ActionPrediction(
            actions=actions,
            num_steps=num_steps,
            confidence=random.uniform(0.7, 0.95),
        )

    async def health_check(self) -> bool:
        return True
