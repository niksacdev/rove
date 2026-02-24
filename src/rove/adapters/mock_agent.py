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
        # Use scene data from upstream if available
        target = "target object"
        if context:
            scene = context.get("scene", {})
            relevant = scene.get("task_relevant", [])
            if relevant:
                target = relevant[0]

        return {
            "strategy": f"Approach {target} and execute task via tool calls",
            "reasoning": (
                f"The agent identified {target} in the scene. "
                "It will use available tools to plan a manipulation sequence "
                "and execute it step by step."
            ),
            "steps": [
                f"Locate {target} using vision",
                "Plan approach trajectory",
                "Execute manipulation via move_to tool",
                "Verify task completion",
            ],
            "target_object": target,
            "confidence": 0.85,
            "raw_response": '{"mock_agent": true}',
        }

    def _mock_act(self, task: str, context: dict | None) -> dict:
        # Use plan data from upstream if available
        target = "target"
        if context:
            plan = context.get("plan", {})
            target = plan.get("target_object") or target

        return {
            "action_type": "tool_calls",
            "tool_calls": [
                {"tool": "move_to", "args": {"target": target, "x": 0.3, "y": 0.1, "z": 0.2}},
                {"tool": "grasp", "args": {"force": 0.5}},
                {"tool": "move_to", "args": {"x": 0.6, "y": 0.3, "z": 0.2}},
                {"tool": "release", "args": {}},
            ],
            "num_steps": 4,
            "confidence": 0.82,
            "raw_response": '{"mock_agent": true}',
        }

    def _mock_verify(self, task: str, context: dict | None) -> dict:
        quality = 0.85

        # Read completed stages and ground truth from context
        completed_stages: list[str] = []
        ground_truth_data: dict | None = None
        target = "target object"
        if context:
            completed_stages = context.get("completed_stages", [])
            ground_truth_data = context.get("ground_truth")
            plan = context.get("plan", {})
            target = plan.get("target_object") or target

        # Generate per-stage checks
        stage_checks: list[dict] = []
        all_passed = True
        for stage in completed_stages:
            passed = random.random() < quality
            if not passed:
                all_passed = False
            stage_checks.append(
                {
                    "stage": stage,
                    "passed": passed,
                    "confidence": random.uniform(0.7, 0.95) if passed else random.uniform(0.3, 0.6),
                    "reasoning": f"{stage} stage output is {'consistent with' if passed else 'inconsistent with'} task requirements.",
                }
            )

        # Ground truth QA check
        gt_check: dict | None = None
        if ground_truth_data:
            correct_answer = ground_truth_data.get("correct_answer_text", "")
            choices = ground_truth_data.get("choices", [])
            is_correct = random.random() < quality
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
            gt_check = {
                "question": ground_truth_data.get("question", ""),
                "choices": choices,
                "correct_answer": correct_answer,
                "pipeline_answer": pipeline_answer,
                "correct": is_correct,
                "confidence": random.uniform(0.7, 0.95) if is_correct else random.uniform(0.3, 0.6),
            }

        success = all_passed if (stage_checks or gt_check) else random.random() < quality

        result: dict = {
            "success": success,
            "confidence": random.uniform(0.7, 0.95) if success else random.uniform(0.3, 0.6),
            "reasoning": (
                f"Task completed successfully — {target} moved to destination."
                if success
                else f"Task may not have completed — {target} position unclear."
            ),
            "raw_response": '{"mock_agent": true}',
            "completed_stages": completed_stages,
            "stage_checks": stage_checks,
        }
        if gt_check is not None:
            result["ground_truth"] = gt_check
        return result

    async def health_check(self) -> bool:
        return True
