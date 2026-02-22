"""Mock agent adapter for testing multi-stage agent pipelines."""

from __future__ import annotations

import asyncio
import random


class MockAgentAdapter:
    """Mock implementation of AgentAdapter — returns stage-appropriate dicts."""

    def __init__(self, model_id: str = "mock-agent", config: dict | None = None):
        self.model_id = model_id
        cfg = config or {}
        self.display_name = cfg.get("display_name", "Mock Agent")
        self._latency_range = cfg.get("mock_latency_ms", [300, 800])

    async def _simulate_latency(self) -> None:
        lo, hi = self._latency_range
        await asyncio.sleep(random.uniform(lo, hi) / 1000.0)

    async def run_stage(
        self,
        stage: str,
        image_base64: str,
        task: str,
        context: dict | None = None,
    ) -> dict:
        await self._simulate_latency()
        handler = {
            "perceive": self._mock_perceive,
            "plan": self._mock_plan,
            "act": self._mock_act,
            "verify": self._mock_verify,
        }.get(stage)
        if handler is None:
            raise ValueError(f"Unknown stage: {stage}")
        return handler(task, context)

    def _mock_perceive(self, task: str, context: dict | None) -> dict:
        return {
            "objects": [
                {"name": "target object", "bbox": [120, 80, 60, 40], "confidence": 0.92},
                {"name": "destination", "bbox": [400, 200, 100, 80], "confidence": 0.95},
            ],
            "spatial_relations": ["target object is to the left of destination"],
            "task_relevant": ["target object", "destination"],
            "raw_response": '{"mock_agent": true}',
        }

    def _mock_plan(self, task: str, context: dict | None) -> dict:
        return {
            "strategy": "Approach target and execute task via tool calls",
            "reasoning": (
                "The agent will use available tools to perceive the workspace, "
                "plan a manipulation sequence, and execute it step by step."
            ),
            "steps": [
                "Locate target object using vision",
                "Plan approach trajectory",
                "Execute manipulation via move_to tool",
                "Verify task completion",
            ],
            "target_object": "target object",
            "confidence": 0.85,
            "raw_response": '{"mock_agent": true}',
        }

    def _mock_act(self, task: str, context: dict | None) -> dict:
        return {
            "action_type": "tool_calls",
            "tool_calls": [
                {"tool": "move_to", "args": {"x": 0.3, "y": 0.1, "z": 0.2}},
                {"tool": "grasp", "args": {"force": 0.5}},
                {"tool": "move_to", "args": {"x": 0.6, "y": 0.3, "z": 0.2}},
                {"tool": "release", "args": {}},
            ],
            "num_steps": 4,
            "confidence": 0.82,
            "raw_response": '{"mock_agent": true}',
        }

    def _mock_verify(self, task: str, context: dict | None) -> dict:
        success = random.random() < 0.85
        return {
            "success": success,
            "confidence": random.uniform(0.7, 0.95) if success else random.uniform(0.3, 0.6),
            "reasoning": (
                "Task completed successfully — target object moved to destination."
                if success
                else "Task may not have completed — target object position unclear."
            ),
            "raw_response": '{"mock_agent": true}',
        }

    async def health_check(self) -> bool:
        return True
