"""Mock VLA adapter for testing without real VLA models."""

from __future__ import annotations

import asyncio
import random

from rove.models import ActionPrediction, TaskPlan


class MockVLAAdapter:
    def __init__(self, model_id: str = "mock-vla", config: dict | None = None):
        self.model_id = model_id
        self.display_name = "Mock VLA"
        cfg = config or {}
        self._latency_range = cfg.get("mock_latency_ms", [50, 150])
        seed = cfg.get("seed")
        self._rng = random.Random(seed) if seed is not None else random.Random()

    async def predict_action(
        self,
        image_base64: str,
        task: str,
        proprioception: list[float] | None = None,
        plan: TaskPlan | None = None,
    ) -> ActionPrediction:
        lo, hi = self._latency_range
        await asyncio.sleep(self._rng.uniform(lo, hi) / 1000.0)

        # Use plan steps to determine trajectory length
        num_steps = len(plan.steps) + 1 if plan and plan.steps else 5

        # Bias first action using proprioception if available
        start_bias = proprioception[0] if proprioception else 0.0

        actions = []
        for i in range(num_steps):
            t = i / num_steps
            dx = round(self._rng.uniform(-0.02, 0.02) + (start_bias * 0.01 if i == 0 else 0), 4)
            actions.append(
                [
                    dx,  # dx
                    round(self._rng.uniform(-0.02, 0.02), 4),  # dy
                    round(-0.01 * (1 - t), 4),  # dz (moving down)
                    round(self._rng.uniform(-0.05, 0.05), 4),  # rx
                    round(self._rng.uniform(-0.05, 0.05), 4),  # ry
                    round(self._rng.uniform(-0.05, 0.05), 4),  # rz
                    round(1.0 if i < num_steps - 1 else 0.0, 1),  # gripper (close at end)
                ]
            )

        return ActionPrediction(
            actions=actions,
            num_steps=num_steps,
            confidence=self._rng.uniform(0.7, 0.95),
        )

    async def health_check(self) -> bool:
        return True
