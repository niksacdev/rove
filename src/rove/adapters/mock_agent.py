"""Mock agent adapter for testing multi-stage agent pipelines."""

from __future__ import annotations

import asyncio
import random
from types import SimpleNamespace


class MockAgentAdapter:
    """Mock implementation of AgentAdapter — returns stage-appropriate dicts."""

    def __init__(self, model_id: str = "mock-agent", config: dict | None = None, **_kwargs):
        self.model_id = model_id
        cfg = config or {}
        self.display_name = cfg.get("display_name", "Mock Agent")
        self._latency_range = cfg.get("mock_latency_ms", [300, 800])
        seed = cfg.get("seed")
        self._rng = random.Random(seed) if seed is not None else random.Random()

    async def _simulate_latency(self) -> None:
        lo, hi = self._latency_range
        await asyncio.sleep(self._rng.uniform(lo, hi) / 1000.0)

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
                {
                    "name": "target object",
                    "bbox": [120, 80, 60, 40],
                    "position": [0.25, -0.10, 0.32],
                    "confidence": 0.92,
                },
                {
                    "name": "destination",
                    "bbox": [400, 200, 100, 80],
                    "position": [0.50, 0.15, 0.30],
                    "confidence": 0.95,
                },
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
            "subtask_reasoning": (
                f"First subtask is to locate {target} because we need visual confirmation "
                "before planning approach. Approach must precede manipulation."
            ),
            "action_reasoning": (
                f"The {target} is on the workspace surface. Agent will use move_to tool "
                "to position end-effector above the target before grasping."
            ),
            "constraints_acknowledged": [],
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
            passed = self._rng.random() < quality
            if not passed:
                all_passed = False
            stage_checks.append(
                {
                    "stage": stage,
                    "passed": passed,
                    "confidence": self._rng.uniform(0.7, 0.95)
                    if passed
                    else self._rng.uniform(0.3, 0.6),
                    "reasoning": f"{stage} stage output is {'consistent with' if passed else 'inconsistent with'} task requirements.",
                }
            )

        # Ground truth QA check
        gt_check: dict | None = None
        if ground_truth_data:
            correct_answer = ground_truth_data.get("correct_answer_text", "")
            choices = ground_truth_data.get("choices", [])
            is_correct = self._rng.random() < quality
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
                "confidence": self._rng.uniform(0.7, 0.95)
                if is_correct
                else self._rng.uniform(0.3, 0.6),
            }

        success = all_passed if (stage_checks or gt_check) else self._rng.random() < quality

        result: dict = {
            "success": success,
            "confidence": self._rng.uniform(0.7, 0.95) if success else self._rng.uniform(0.3, 0.6),
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

    async def verify_with_tools(
        self,
        input_items: list,
        tools: list[dict],
        instructions: str | None = None,
    ) -> object:
        """Multi-turn mock verify for agent_loop mode."""
        await self._simulate_latency()

        has_tool_results = any(
            (isinstance(item, dict) and item.get("type") == "function_call_output")
            for item in input_items
        )

        if not has_tool_results and tools:
            output_items = []
            for tool in tools:
                tool_name = tool.get("name", "unknown")
                output_items.append(
                    SimpleNamespace(
                        type="function_call",
                        name=tool_name,
                        call_id=f"mock_call_{tool_name}",
                        arguments="{}",
                    )
                )
            return SimpleNamespace(output=output_items)

        has_dynamics = any(t.get("name") == "compute_dynamics" for t in tools) if tools else False

        quality = 0.85
        success = self._rng.random() < quality
        if has_dynamics:
            confidence = self._rng.uniform(0.7, 0.95) if success else self._rng.uniform(0.3, 0.6)
        else:
            confidence = self._rng.uniform(0.35, 0.55) if success else self._rng.uniform(0.2, 0.4)

        # Build reasoning — fundamentally different with and without dynamics
        if has_dynamics:
            if success:
                reasoning = (
                    f"Verdict: The VLA action trajectory is plausible and well-aligned with the task. "
                    f"Confidence: {round(confidence, 2)}.\n\n"
                    "VLA Output Analysis: The VLA produced 4 tool calls (move_to, grasp, move_to, release) "
                    "with consistent trajectory and appropriate gripper timing.\n\n"
                    "Evidence — Scene Analysis: Target object and destination found in expected positions.\n\n"
                    "Evidence — Planned Approach: Approach target, grasp, transport to destination, release.\n\n"
                    "Evidence — Dynamics Verification: All joints within limits, no collisions, smooth motion."
                )
            else:
                reasoning = (
                    f"Verdict: The VLA trajectory has issues — dynamics flagged violations. "
                    f"Confidence: {round(confidence, 2)}.\n\n"
                    "VLA Output Analysis: Trajectory shows potential misalignment at transport phase.\n\n"
                    "Evidence — Scene Analysis: Target object and destination found.\n\n"
                    "Evidence — Planned Approach: Approach, grasp, transport, release.\n\n"
                    "Evidence — Dynamics Verification: Joint limit warnings at step 3, smoothness below threshold."
                )
        else:
            if success:
                reasoning = (
                    f"Verdict: Based on limited evidence (no dynamics data), the VLA trajectory "
                    f"appears task-aligned. Physical plausibility cannot be verified. "
                    f"Confidence: {round(confidence, 2)}.\n\n"
                    "VLA Output Analysis: The VLA produced 4 tool calls (move_to, grasp, move_to, release). "
                    "The pattern appears consistent with pick-and-place.\n\n"
                    "Evidence — Scene Analysis: Target object and destination found in expected positions.\n\n"
                    "Evidence — Planned Approach: Approach target, grasp, transport to destination, release. "
                    "Without dynamics data, trajectory alignment cannot be verified."
                )
            else:
                reasoning = (
                    f"Verdict: Based on limited evidence (no dynamics data), the VLA trajectory "
                    f"shows task alignment concerns. Physical plausibility cannot be verified. "
                    f"Confidence: {round(confidence, 2)}.\n\n"
                    "VLA Output Analysis: Trajectory shows potential issues at transport phase.\n\n"
                    "Evidence — Scene Analysis: Target object and destination found.\n\n"
                    "Evidence — Planned Approach: Approach, grasp, transport, release. "
                    "Without dynamics data, alignment cannot be verified."
                )

        result_data = {
            "success": success,
            "confidence": round(confidence, 2),
            "reasoning": reasoning,
            "resolution_path": (
                (
                    "No critical issues found — action trajectory is well-aligned with task requirements."
                    if success
                    else "Consider adjusting VLA training data distribution. "
                    "Increase action chunk size for transport phase."
                )
                if has_dynamics
                else "Enable MuJoCo dynamics for a reliable assessment. "
                "Without physics data, this evaluation cannot verify trajectory feasibility."
            ),
            "completed_stages": ["perceive", "act"],
            "stage_checks": [
                {
                    "stage": "perceive",
                    "passed": True,
                    "confidence": 0.9,
                    "reasoning": "Scene analysis consistent with task.",
                },
                {
                    "stage": "act",
                    "passed": success,
                    "confidence": round(confidence, 2),
                    "reasoning": (
                        "Action plausibility from dynamics."
                        if has_dynamics
                        else "Action plausibility from scene and task alignment."
                    ),
                },
            ],
        }

        return SimpleNamespace(
            output=[SimpleNamespace(type="message", text="")],
            data=result_data,
        )

    async def health_check(self) -> bool:
        return True
