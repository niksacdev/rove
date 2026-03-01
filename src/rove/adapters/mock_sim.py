"""Mock simulation adapter for testing without MuJoCo."""

from __future__ import annotations

import asyncio
import random

from rove.models import SimObservation

# Minimal 1x1 white PNG (67 bytes) — base64 encoded
_MOCK_IMAGE_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
    "nGP4z8BQDwAEgAF/pooBPQAAAABJRU5ErkJggg=="
)


class MockSimAdapter:
    def __init__(self, model_id: str = "mock-sim", config: dict | None = None):
        self.model_id = model_id
        self.display_name = "Mock Sim"
        cfg = config or {}
        self._success_rate = cfg.get("mock_success_rate", 0.80)
        seed = cfg.get("seed")
        self._rng = random.Random(seed) if seed is not None else random.Random()
        self._step_count = 0
        self._last_success = False
        self._last_proprioception: list[float] = [0.0] * 7
        self._last_image_base64 = ""
        self._done = False

    async def reset(self, task: str) -> SimObservation:
        self._step_count = 0
        self._last_success = False
        self._last_proprioception = [0.0] * 7
        self._last_image_base64 = ""
        self._done = False
        return SimObservation(
            image_base64="",
            proprioception=list(self._last_proprioception),
            success=False,
            done=False,
        )

    async def step(self, action: list[float]) -> SimObservation:
        await asyncio.sleep(self._rng.uniform(0.01, 0.03))
        self._step_count += 1
        self._done = self._step_count >= 5
        self._last_success = self._done and self._rng.random() < self._success_rate
        self._last_proprioception = [round(self._rng.uniform(-0.5, 0.5), 3) for _ in range(7)]
        self._last_image_base64 = _MOCK_IMAGE_B64
        return SimObservation(
            image_base64=self._last_image_base64,
            proprioception=list(self._last_proprioception),
            success=self._last_success,
            done=self._done,
        )

    async def get_observation(self) -> SimObservation:
        return SimObservation(
            image_base64=self._last_image_base64,
            proprioception=list(self._last_proprioception),
            success=self._last_success,
            done=self._done,
        )
