"""Mock VLM adapter for testing without API calls."""

from __future__ import annotations

import asyncio
import random
from types import SimpleNamespace

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
                # Derive scores from dynamics evidence
                summary = dyn_data.get("summary", {})
                dyn_bounds = summary.get("joint_limits_ok", True) and not summary.get(
                    "self_collision", False
                )
                dyn_smooth = summary.get("smoothness_score", 1.0)
                dyn_torque_ok = summary.get("torque_feasible", True)
                dyn_singularity = summary.get("near_singularity", False)

                reasons = []
                if not dyn_bounds:
                    reasons.append("Hard evidence: joint limits exceeded or self-collision")
                    all_passed = False
                if not dyn_torque_ok:
                    reasons.append("Hard evidence: torque limits exceeded")
                    all_passed = False
                if dyn_singularity:
                    reasons.append("Hard evidence: near singularity at grasp config")
                if not reasons:
                    reasons.append("All physics checks passed — trajectory is plausible")

                plausibility = ActionPlausibility(
                    bounds_check=round(1.0 if dyn_bounds and dyn_torque_ok else 0.2, 2),
                    smoothness=round(min(dyn_smooth, 1.0), 2),
                    gripper_consistency=round(self._rng.uniform(0.6, 0.95), 2),
                    plan_alignment=round(self._rng.uniform(0.5, 0.9), 2),
                    reasoning=" ".join(reasons),
                    workspace_reachability=round(
                        1.0 if dyn_bounds else self._rng.uniform(0.3, 0.6), 2
                    ),
                    task_completion_plausibility=round(self._rng.uniform(0.3, 0.8), 2),
                    dynamics_consistency=round(self._rng.uniform(0.7, 1.0), 2),
                    safety_assessment=round(
                        1.0 if dyn_torque_ok and not dyn_singularity else 0.3, 2
                    ),
                    evidence_quality="hard",
                )
            else:
                # No dynamics — set physics-dependent fields to None
                plausibility = ActionPlausibility(
                    smoothness=round(self._rng.uniform(0.3, 0.5), 2),
                    gripper_consistency=round(self._rng.uniform(0.3, 0.5), 2),
                    plan_alignment=round(self._rng.uniform(0.3, 0.5), 2),
                    task_completion_plausibility=round(self._rng.uniform(0.2, 0.4), 2),
                    reasoning=(
                        "Without dynamics data, plausibility scores reflect high "
                        "uncertainty. Physics-dependent fields set to null."
                    ),
                    evidence_quality="perception_only",
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

    async def verify_with_tools(
        self,
        input_items: list,
        tools: list[dict],
        instructions: str | None = None,
    ) -> object:
        """Multi-turn mock verify for agent_loop mode.

        Turn 0 (no function_call_output in input): return function_call items
        for all available tools (parallel request).
        Turn 1+ (has function_call_output): return final verdict as text.
        """
        await self._simulate_latency()

        # Check if this is a follow-up turn (has function_call_output items)
        has_tool_results = any(
            (isinstance(item, dict) and item.get("type") == "function_call_output")
            for item in input_items
        )

        if not has_tool_results and tools:
            # Turn 0: request all available tools
            output_items = []
            for i, tool in enumerate(tools):
                tool_name = tool.get("name", f"tool_{i}")
                output_items.append(
                    SimpleNamespace(
                        type="function_call",
                        name=tool_name,
                        call_id=f"mock_call_{tool_name}",
                        arguments="{}",
                    )
                )
            return SimpleNamespace(output=output_items)

        # Turn 1+: generate final verdict with resolution_path
        has_dynamics = any(t.get("name") == "compute_dynamics" for t in tools) if tools else False
        success = self._rng.random() < self._quality
        if has_dynamics:
            confidence = self._rng.uniform(0.7, 0.95) if success else self._rng.uniform(0.3, 0.6)
        else:
            # Without dynamics, confidence should be low — we lack physics evidence
            confidence = self._rng.uniform(0.35, 0.55) if success else self._rng.uniform(0.2, 0.4)

        # Build reasoning based on available tools
        if has_dynamics:
            if success:
                reasoning = (
                    f"Verdict: Based on our evaluation, the VLA action trajectory is plausible for this task. "
                    f"The trajectory aligns well with the expected manipulation sequence. Confidence: {round(confidence, 2)}.\n\n"
                    "VLA Output Analysis: The VLA produced 4 action steps with move_to and grasp/release tool calls. "
                    "The trajectory moves from the target position to the destination with a gripper "
                    "close-open pattern consistent with pick-and-place.\n\n"
                    "Evidence — Scene Analysis: Our analysis of the scene found 2 task-relevant objects: "
                    "target object at [0.25, -0.10, 0.32]m and destination at [0.50, 0.15, 0.30]m. "
                    "The target is to the left of the destination on the workspace surface.\n\n"
                    "Evidence — Planned Approach: For this task, the expected manipulation sequence is: "
                    "(1) approach target object from above, (2) grasp with appropriate force, "
                    "(3) transport to destination position, (4) release. "
                    "The robot should move ~0.30m laterally to complete the transfer.\n\n"
                    "Evidence — Dynamics Verification: MuJoCo dynamics analysis shows: all joint limits satisfied, "
                    "workspace reachability 0.92, no collisions detected, smoothness score 0.87. "
                    "No physics violations found."
                )
            else:
                reasoning = (
                    f"Verdict: The VLA action trajectory shows significant issues for this task. "
                    f"Dynamics violations and endpoint error reduce confidence. Confidence: {round(confidence, 2)}.\n\n"
                    "VLA Output Analysis: The VLA produced 4 action steps. The trajectory endpoint is 0.15m "
                    "from the destination position, suggesting the transport phase was incomplete.\n\n"
                    "Evidence — Scene Analysis: Our analysis of the scene found 2 task-relevant objects: "
                    "target object at [0.25, -0.10, 0.32]m and destination at [0.50, 0.15, 0.30]m.\n\n"
                    "Evidence — Planned Approach: The robot should approach the target, grasp it, "
                    "transport to destination, and release.\n\n"
                    "Evidence — Dynamics Verification: MuJoCo dynamics analysis shows joint limit violations "
                    "near step 3. Smoothness score 0.45 indicates jerky motion."
                )
        else:
            if success:
                reasoning = (
                    f"Verdict: Based on limited evidence (no dynamics data), the VLA action trajectory "
                    f"appears task-aligned. However, physical plausibility cannot be verified without "
                    f"dynamics analysis. Confidence: {round(confidence, 2)}.\n\n"
                    "VLA Output Analysis: The VLA produced 4 action steps with move_to and grasp/release tool calls. "
                    "The gripper close-open pattern is consistent with pick-and-place. "
                    "Step count appears reasonable for the task.\n\n"
                    "Evidence — Scene Analysis: Our analysis of the scene found 2 task-relevant objects: "
                    "target object at [0.25, -0.10, 0.32]m and destination at [0.50, 0.15, 0.30]m. "
                    "The target is to the left of the destination on the workspace surface.\n\n"
                    "Evidence — Planned Approach: For this task, the expected manipulation sequence is: "
                    "(1) approach target, (2) grasp, (3) transport to destination, (4) release. "
                    "Without dynamics data, we cannot verify whether the trajectory actually follows this plan."
                )
            else:
                reasoning = (
                    f"Verdict: Based on limited evidence (no dynamics data), the VLA action trajectory "
                    f"shows concerns with task alignment. Physical plausibility cannot be verified. "
                    f"Confidence: {round(confidence, 2)}.\n\n"
                    "VLA Output Analysis: The VLA produced 4 action steps. The gripper pattern "
                    "is unclear and step count may be insufficient for the task complexity.\n\n"
                    "Evidence — Scene Analysis: Our analysis of the scene found 2 task-relevant objects: "
                    "target object at [0.25, -0.10, 0.32]m and destination at [0.50, 0.15, 0.30]m.\n\n"
                    "Evidence — Planned Approach: The robot should approach the target, grasp it, "
                    "transport to destination, and release. Without dynamics data, alignment cannot be verified."
                )

        result_data = {
            "success": success,
            "confidence": round(confidence, 2),
            "reasoning": reasoning,
            "resolution_path": (
                (
                    "No critical issues found — action trajectory is well-aligned with task requirements. "
                    "Minor refinement: consider increasing action resolution for smoother endpoint approach."
                    if success
                    else "Consider retraining VLA with tighter joint limit constraints. "
                    "Increase action chunk size for smoother trajectories. "
                    "Endpoint accuracy may improve with fine-tuning on similar displacement distances."
                )
                if has_dynamics
                else (
                    "Run simulation or enable MuJoCo dynamics and ensure the VLA produces "
                    "actions matching the target robot's full joint specification (including "
                    "gripper). Only with dynamics can physical plausibility and task "
                    "completion be reliably assessed."
                )
            ),
            "completed_stages": ["perceive", "act"],
            "stage_checks": [
                {
                    "stage": "perceive",
                    "passed": True,
                    "confidence": round(self._rng.uniform(0.8, 0.95), 2),
                    "reasoning": "Scene analysis correctly identified task-relevant objects.",
                },
                {
                    "stage": "act",
                    "passed": success,
                    "confidence": round(confidence, 2),
                    "reasoning": (
                        "Action plausibility assessment based on dynamics analysis."
                        if has_dynamics
                        else "Action plausibility assessment based on scene and task alignment."
                    ),
                },
            ],
        }

        # Action plausibility — scores reflect available evidence
        if has_dynamics:
            ap: dict = {
                "bounds_check": 1.0 if success else 0.2,
                "smoothness": round(self._rng.uniform(0.6, 0.95), 2),
                "gripper_consistency": round(self._rng.uniform(0.5, 0.95), 2),
                "plan_alignment": round(self._rng.uniform(0.5, 0.9), 2),
                "workspace_reachability": round(self._rng.uniform(0.7, 1.0), 2),
                "dynamics_consistency": round(self._rng.uniform(0.7, 1.0), 2),
                "task_completion_plausibility": round(self._rng.uniform(0.4, 0.8), 2),
                "safety_assessment": round(self._rng.uniform(0.7, 1.0), 2),
                "evidence_quality": "hard",
                "reasoning": "Plausibility assessment grounded in MuJoCo dynamics evidence.",
            }
        else:
            # Without dynamics — physics-dependent fields are null
            ap = {
                "bounds_check": None,
                "smoothness": round(self._rng.uniform(0.3, 0.5), 2),
                "gripper_consistency": round(self._rng.uniform(0.3, 0.5), 2),
                "plan_alignment": round(self._rng.uniform(0.3, 0.5), 2),
                "task_completion_plausibility": round(self._rng.uniform(0.2, 0.4), 2),
                "dynamics_consistency": None,
                "workspace_reachability": None,
                "safety_assessment": None,
                "evidence_quality": "perception_only",
                "reasoning": (
                    "Without dynamics data, physics-dependent fields are null. "
                    "Remaining scores reflect perception-only assessment."
                ),
            }
        result_data["action_plausibility"] = ap

        return SimpleNamespace(
            output=[SimpleNamespace(type="message", text="")],
            data=result_data,
        )

    async def health_check(self) -> bool:
        return True
