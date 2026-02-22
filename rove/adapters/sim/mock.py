"""Mock simulation adapter for testing without MuJoCo."""

from __future__ import annotations

import asyncio
import random

from rove.models import SimObservation


class MockSimAdapter:
    def __init__(self, model_id: str = "mock-sim", config: dict | None = None):
        self.model_id = model_id
        self.display_name = "Mock Sim"
        cfg = config or {}
        self._success_rate = cfg.get("mock_success_rate", 0.80)
        self._step_count = 0

    async def reset(self, task: str) -> SimObservation:
        self._step_count = 0
        return SimObservation(
            image_base64="",
            proprioception=[0.0] * 7,
            success=False,
            done=False,
        )

    async def step(self, action: list[float]) -> SimObservation:
        await asyncio.sleep(random.uniform(0.01, 0.03))
        self._step_count += 1
        done = self._step_count >= 5
        success = done and random.random() < self._success_rate
        return SimObservation(
            image_base64="",
            proprioception=[round(random.uniform(-0.5, 0.5), 3) for _ in range(7)],
            success=success,
            done=done,
        )

    async def get_observation(self) -> SimObservation:
        return SimObservation(
            image_base64="",
            proprioception=[round(random.uniform(-0.5, 0.5), 3) for _ in range(7)],
            success=False,
            done=False,
        )
