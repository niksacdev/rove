"""Evaluation pipeline — two modes: sequential and parallel (VLA).

Sequential: perceive → plan → act → verify (linear, no phase labels)
Parallel:   VLA execution first, then perceive/plan/dynamics/verify as evaluation
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from rove.adapters.protocols import (
    AgentAdapter,
    SimAdapter,
    StageAdapter,
    VLAAdapter,
)
from rove.adapters.registry import AdapterRegistry
from rove.models import (
    ActionPrediction,
    ExampleData,
    PipelineContext,
    PipelineStageResult,
    SceneAnalysis,
    StageStatus,
    TaskPlan,
    VerificationResult,
)

logger = logging.getLogger(__name__)

# Map stage names to their Pydantic model classes for AgentAdapter dict→model conversion
_STAGE_MODELS: dict[str, type] = {
    "perceive": SceneAnalysis,
    "plan": TaskPlan,
    "act": ActionPrediction,
    "verify": VerificationResult,
}


class EvaluationPipeline:
    """Runs the pipeline in sequential or parallel mode.

    Sequential (no VLA): perceive → plan → act → verify
    Parallel (VLA present): act first (execution), then perceive/plan/dynamics/verify (evaluation)

    Each stage has its own adapter — they can be the same model or different models.
    Supports VLM, VLA, and Agent adapters.
    """

    def __init__(
        self,
        perceive_adapter: StageAdapter | None = None,
        plan_adapter: StageAdapter | None = None,
        act_adapter: VLAAdapter | AgentAdapter | None = None,
        verify_adapter: StageAdapter | None = None,
        sim: SimAdapter | None = None,
        registry: AdapterRegistry | None = None,
        compute_dynamics: bool = False,
        urdf_path: str | None = None,
        pipeline_mode: str = "sequential",
        verify_mode: str = "auto",
    ):
        self.perceive_adapter = perceive_adapter
        self.plan_adapter = plan_adapter
        self.act_adapter = act_adapter
        self.verify_adapter = verify_adapter
        self.sim = sim
        self._registry = registry
        self._compute_dynamics = compute_dynamics
        self._urdf_path = urdf_path
        self._pipeline_mode = pipeline_mode
        self._verify_mode = verify_mode
        self._urdf_robot_info = self._extract_urdf_robot_info()

    def _extract_urdf_robot_info(self) -> dict:
        """Extract robot DOF and joint info from URDF at init time.

        Parses the URDF XML to count movable joints (revolute, prismatic,
        continuous) without requiring MuJoCo. Returns empty dict if no URDF.
        """
        if not self._urdf_path:
            return {}
        try:
            import xml.etree.ElementTree as ET  # nosec B405
            from pathlib import Path

            tree = ET.parse(self._urdf_path)  # nosec B314
            root = tree.getroot()

            robot_name = root.get("name", Path(self._urdf_path).stem)
            movable_types = {"revolute", "prismatic", "continuous"}
            movable_joints = []
            mimic_joints = []

            for joint in root.iter("joint"):
                jtype = joint.get("type", "fixed")
                jname = joint.get("name", "")
                if jtype in movable_types:
                    if joint.find("mimic") is not None:
                        mimic_joints.append(jname)
                    else:
                        movable_joints.append(jname)

            info: dict = {
                "robot_name": robot_name,
                "dof": len(movable_joints),
                "joint_names": movable_joints,
            }
            if mimic_joints:
                info["mimic_joints"] = mimic_joints
            return info
        except Exception:
            logger.warning("Failed to parse URDF for robot info", exc_info=True)
            return {}

    async def _call_adapter(
        self,
        stage: str,
        adapter: StageAdapter,
        image_base64: str,
        task: str,
        **context: Any,
    ) -> Any:
        """Dispatch to the appropriate adapter method based on adapter type and stage."""
        sem = self._registry.get_semaphore(adapter.model_id) if self._registry else None
        if sem is not None:
            async with sem:
                return await self._call_adapter_inner(stage, adapter, image_base64, task, **context)
        return await self._call_adapter_inner(stage, adapter, image_base64, task, **context)

    async def _call_adapter_inner(
        self,
        stage: str,
        adapter: StageAdapter,
        image_base64: str,
        task: str,
        **context: Any,
    ) -> Any:
        """Inner dispatch — actual adapter call without semaphore."""
        if isinstance(adapter, AgentAdapter):
            # For verify, pass pipeline_context directly (not wrapped in kwargs)
            if stage == "verify" and "pipeline_context" in context:
                agent_context = context["pipeline_context"]
                if hasattr(agent_context, "model_dump"):
                    agent_context = agent_context.model_dump()
            else:
                # Serialize Pydantic objects in context for agent adapters
                agent_context = (
                    {
                        k: v.model_dump() if hasattr(v, "model_dump") else v
                        for k, v in context.items()
                    }
                    if context
                    else None
                )
            raw = await adapter.run_stage(stage, image_base64, task, agent_context)
            model_cls = _STAGE_MODELS.get(stage)
            if model_cls is None:
                raise ValueError(f"Unknown stage: {stage}")
            return model_cls.model_validate(raw)

        # Type-specific calls for VLM/VLA adapters
        if stage == "perceive":
            return await adapter.analyze_scene(image_base64, task)
        elif stage == "plan":
            scene = context.get("scene")
            task_metadata = context.get("task_metadata")
            return await adapter.plan_task(image_base64, task, scene, task_metadata=task_metadata)
        elif stage == "act":
            return await adapter.predict_action(
                image_base64,
                task,
                proprioception=context.get("proprioception"),
                plan=context.get("plan"),
            )
        elif stage == "verify":
            after_image = context.get("after_image", image_base64)
            return await adapter.verify_success(
                image_base64,
                after_image,
                task,
                context=context.get("pipeline_context"),
            )
        else:
            raise ValueError(f"Unknown stage: {stage}")

    def _get_model_id(self, adapter: StageAdapter) -> str:
        return adapter.model_id

    @staticmethod
    def _apply_dynamics_sanity_checks(
        verification: VerificationResult, dynamics_analysis: dict
    ) -> VerificationResult:
        """Override VLM plausibility scores when dynamics data objectively contradicts them.

        MuJoCo dynamics provides ground-truth physics checks (joint limits,
        collisions, displacement, torques, singularity) that should override
        subjective VLM scores.
        """
        if not verification.action_plausibility:
            return verification

        plaus = verification.action_plausibility.model_copy()
        overrides: list[str] = []
        dyn_failures = 0

        # Joint limits violation
        if not dynamics_analysis.get("joint_limits_ok", True):
            if plaus.bounds_check:
                plaus.bounds_check = 0.0
                overrides.append("bounds_check=False: joint limits exceeded")
            dyn_failures += 1

        # Self-collision
        if dynamics_analysis.get("self_collision", False):
            if plaus.bounds_check:
                plaus.bounds_check = 0.0
                overrides.append("bounds_check=False: self-collision detected")
            dyn_failures += 1

        # Excessive displacement
        total_disp = dynamics_analysis.get("total_displacement_m", 0.0)
        if total_disp > 2.0:
            if plaus.smoothness:
                plaus.smoothness = 0.0
                overrides.append(
                    f"smoothness=False: displacement {total_disp:.1f}m exceeds 2.0m limit"
                )
            dyn_failures += 1

        # Very low smoothness score
        smoothness_score = dynamics_analysis.get("smoothness_score", 1.0)
        if smoothness_score < 0.1:
            dyn_failures += 1

        # Torque feasibility violation
        if not dynamics_analysis.get("torque_feasible", True):
            if plaus.bounds_check:
                plaus.bounds_check = 0.0
                overrides.append("bounds_check=False: torque limits exceeded")
            dyn_failures += 1

        # Near singularity — cap plan_alignment
        if dynamics_analysis.get("near_singularity", False):
            if plaus.plan_alignment > 0.3:
                overrides.append("plan_alignment capped 0.3: near singularity at grasp config")
                plaus.plan_alignment = 0.3
            dyn_failures += 1

        # Gravity compensation infeasible
        if not dynamics_analysis.get("gravity_feasible", True):
            if plaus.bounds_check:
                plaus.bounds_check = 0.0
                overrides.append("bounds_check=False: gravity compensation infeasible")
            dyn_failures += 1

        # Workspace reachability — cap based on dynamics failure count
        reachability = max(0.0, 1.0 - dyn_failures * 0.25)
        if reachability < plaus.workspace_reachability:
            overrides.append(
                f"workspace_reachability={reachability:.2f}: {dyn_failures} dynamics failure(s)"
            )
            plaus.workspace_reachability = reachability

        # Safety assessment — compute from 4 binary signals
        safety_penalties = 0
        if not dynamics_analysis.get("torque_feasible", True):
            safety_penalties += 1
        if dynamics_analysis.get("self_collision", False):
            safety_penalties += 1
        if dynamics_analysis.get("near_singularity", False):
            safety_penalties += 1
        if not dynamics_analysis.get("gravity_feasible", True):
            safety_penalties += 1
        safety_score = max(0.0, 1.0 - safety_penalties * 0.3)
        if safety_score < plaus.safety_assessment:
            overrides.append(
                f"safety_assessment={safety_score:.2f}: {safety_penalties} safety issue(s)"
            )
            plaus.safety_assessment = safety_score

        # Dynamics consistency — each override applied = disagreement between LLM and physics
        # Count how many overrides we've accumulated so far (before this point)
        n_overrides = len(overrides)
        consistency = max(0.0, 1.0 - n_overrides * 0.15)
        if consistency < plaus.dynamics_consistency:
            overrides.append(
                f"dynamics_consistency={consistency:.2f}: {n_overrides} LLM-physics disagreement(s)"
            )
            plaus.dynamics_consistency = consistency

        # NOTE: task_completion_plausibility is NOT overridden — it's a semantic
        # assessment that only the LLM can make (requires understanding task intent)

        # Cap plan_alignment if any dynamics failure
        if dyn_failures >= 1 and plaus.plan_alignment > 0.3:
            overrides.append(f"plan_alignment capped 0.3: {dyn_failures} dynamics failure(s)")
            plaus.plan_alignment = 0.3

        # Cap overall confidence if 2+ dynamics failures
        new_confidence = verification.confidence
        if dyn_failures >= 2 and verification.confidence > 0.4:
            overrides.append(f"confidence capped 0.4: {dyn_failures} dynamics failures")
            new_confidence = 0.4

        if overrides:
            override_text = " | ".join(f"[Dynamics override: {o}]" for o in overrides)
            plaus.reasoning = (
                f"{plaus.reasoning} {override_text}" if plaus.reasoning else override_text
            )

        return verification.model_copy(
            update={
                "action_plausibility": plaus,
                "confidence": new_confidence,
            }
        )

    def _resolve_verify_mode(self) -> str:
        """Resolve 'auto' verify_mode to a concrete mode.

        'auto' → 'agent_loop' when pipeline_mode is parallel and act adapter present,
        otherwise 'precompute'.
        """
        if self._verify_mode != "auto":
            return self._verify_mode
        if self._pipeline_mode == "parallel" and self.act_adapter is not None:
            return "agent_loop"
        return "precompute"

    def _build_verify_tools(
        self,
        image_base64: str,
        task: str,
        ctx: PipelineContext,
    ) -> tuple[list[dict], dict[str, Any]]:
        """Build OpenAI function calling tools from available pipeline adapters.

        Returns (tool_schemas, executors) where tool_schemas is a list of
        OpenAI function calling dicts and executors maps tool name → async callable.
        """
        tools: list[dict] = []
        executors: dict[str, Any] = {}

        if self.perceive_adapter is not None:
            tools.append(
                {
                    "type": "function",
                    "name": "perceive",
                    "description": (
                        "Analyze the scene image to detect objects, spatial relations, "
                        "and task-relevant items. Returns structured scene analysis."
                    ),
                    "parameters": {"type": "object", "properties": {}},
                }
            )

            async def _exec_perceive() -> dict:
                result = await self._call_adapter(
                    "perceive", self.perceive_adapter, image_base64, task
                )
                ctx.scene = result
                ctx.completed_stages.append("perceive")
                return result.model_dump() if hasattr(result, "model_dump") else result

            executors["perceive"] = _exec_perceive

        if self._compute_dynamics and self._urdf_path and ctx.action:
            tools.append(
                {
                    "type": "function",
                    "name": "compute_dynamics",
                    "description": (
                        "Run MuJoCo dynamics analysis on the VLA action trajectory. "
                        "Returns end-effector positions, joint limit violations, "
                        "collisions, torque feasibility, smoothness. Physics ground truth."
                    ),
                    "parameters": {"type": "object", "properties": {}},
                }
            )

            async def _exec_dynamics() -> dict:
                self._compute_dynamics_analysis(ctx, ctx.action)
                return ctx.dynamics_analysis or {"skipped": "computation failed"}

            executors["compute_dynamics"] = _exec_dynamics

        return tools, executors

    def _build_verify_input(
        self,
        task: str,
        image_base64: str,
        ctx: PipelineContext,
    ) -> list[dict]:
        """Build initial input_items for the verify loop (system + user prompt)."""
        from rove.utils.prompt_loader import get_prompt_manager

        pm = get_prompt_manager()
        action_summary = ""
        if ctx.action:
            action_dict = ctx.to_dict().get("action", {})
            action_summary = pm.format_action_summary(action_dict)

        # Build robot spec from available metadata
        robot_spec = self._build_robot_spec(ctx)

        user_text = pm.render_verify_loop_user(task, action_summary, robot_spec)

        user_parts: list[dict] = [{"type": "input_text", "text": user_text}]
        if image_base64:
            user_parts.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{image_base64}",
                }
            )

        return [{"role": "user", "content": user_parts}]

    def _build_robot_spec(self, ctx: PipelineContext) -> str:
        """Build robot embodiment spec from manifest metadata and/or URDF.

        Provides the verifier with ground truth about the target robot so it can
        detect embodiment mismatches in VLA output (wrong DOF, action space,
        control mode, etc.). All data comes from the dataset manifest or URDF —
        no hardcoded robot knowledge.
        """
        lines: list[str] = []
        meta = ctx.task_metadata or {}
        urdf_info = self._urdf_robot_info

        robot_name = meta.get("robot", "") or urdf_info.get("robot_name", "")
        action_dim = meta.get("action_dim")
        state_dim = meta.get("state_dim")
        dof = urdf_info.get("dof")

        if not robot_name and not action_dim and not dof:
            return ""

        if robot_name:
            lines.append(f"Robot: {robot_name}")
        if dof is not None:
            joint_names = urdf_info.get("joint_names", [])
            mimic_joints = urdf_info.get("mimic_joints", [])
            lines.append(f"DOF (from URDF): {dof} independent joints")
            if joint_names:
                lines.append(f"Joint names: {', '.join(joint_names)}")
            if mimic_joints:
                lines.append(f"Mimic joints (not independent): {', '.join(mimic_joints)}")
        if action_dim is not None:
            lines.append(f"Expected action dimensions: {action_dim}")
        if state_dim is not None:
            lines.append(f"State dimensions: {state_dim}")
        if ctx.proprioception:
            lines.append(f"Proprioception dimensions: {len(ctx.proprioception)}")

        return "\n".join(lines)

    async def _verify_call(
        self,
        input_items: list,
        tool_schemas: list[dict],
        verify_model: str,
    ) -> Any:
        """Dispatch a verify call with tools based on adapter type.

        Returns a response object with .output list containing either
        function_call items or message items with text.
        """
        from rove.utils.prompt_loader import get_prompt_manager

        pm = get_prompt_manager()
        has_dynamics = any(t.get("name") == "compute_dynamics" for t in tool_schemas)
        instructions = pm.render_verify_loop_system(has_dynamics=has_dynamics)

        adapter = self.verify_adapter

        # MockVLMAdapter — has verify_with_tools method
        if hasattr(adapter, "verify_with_tools"):
            return await adapter.verify_with_tools(input_items, tool_schemas, instructions)

        # MockAgentAdapter — has verify_with_tools method
        if isinstance(adapter, AgentAdapter) and hasattr(adapter, "verify_with_tools"):
            return await adapter.verify_with_tools(input_items, tool_schemas, instructions)

        # GenericVLMAdapter — provider may support tool calling
        if hasattr(adapter, "_provider"):
            from rove.providers import ToolCallingProvider

            provider = adapter._provider
            if isinstance(provider, ToolCallingProvider):
                return await provider.get_response_with_tools(
                    input_items, tool_schemas, instructions=instructions
                )

        # AzureFoundryAgentAdapter — has _ensure_agent and uses OpenAI Responses API
        if hasattr(adapter, "_ensure_agent") and hasattr(adapter, "_openai_client"):
            await adapter._ensure_agent()
            agent_instructions = adapter._agent_instructions or ""
            if agent_instructions:
                agent_instructions += "\n\n"
            agent_instructions += instructions

            kwargs: dict = {
                "model": adapter._agent_model,
                "instructions": agent_instructions,
                "input": input_items,
                "max_output_tokens": 4096,
            }
            if tool_schemas:
                kwargs["tools"] = tool_schemas
            if adapter._agent_temperature is not None:
                kwargs["temperature"] = adapter._agent_temperature

            return await adapter._openai_client.responses.create(**kwargs)

        # Fallback: single-call verify (no tool support)
        raise NotImplementedError(f"Adapter {type(adapter).__name__} does not support tool calling")

    def _parse_verify_response(self, response: Any, turn: int = 1) -> VerificationResult:
        """Parse a verify response into VerificationResult.

        Handles both mock response objects and real OpenAI API responses.
        """
        # Mock response with .data attribute
        if hasattr(response, "data") and isinstance(response.data, dict):
            data = response.data
            data["verify_turns"] = turn
            return VerificationResult.model_validate(data)

        # Real OpenAI response — extract text from output items
        response_text = ""
        if hasattr(response, "output"):
            for item in response.output:
                if hasattr(item, "content"):
                    for part in item.content:
                        if hasattr(part, "text"):
                            response_text += part.text
                elif hasattr(item, "text"):
                    response_text += item.text

        if hasattr(response, "output_text") and not response_text:
            response_text = response.output_text or ""

        if not response_text:
            return VerificationResult(
                success=False,
                confidence=0.0,
                reasoning="Empty response from verify model",
                verify_turns=turn,
            )

        # Parse JSON from response text
        import re

        cleaned = response_text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
        cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # Attempt brace-matching extraction for truncated responses
            data = self._extract_json_object(cleaned)

        if data is not None:
            data["raw_response"] = response_text
            data["verify_turns"] = turn
            return VerificationResult.model_validate(data)

        return VerificationResult(
            success=False,
            confidence=0.0,
            reasoning=f"Could not parse verify response: {response_text[:500]}",
            raw_response=response_text,
            verify_turns=turn,
        )

    @staticmethod
    def _extract_json_object(text: str) -> dict | None:
        """Extract a JSON object via brace matching, repairing truncated tails."""
        start = text.find("{")
        if start < 0:
            return None
        depth = 0
        in_str = False
        escape = False
        for i, ch in enumerate(text[start:], start):
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        return None
        # Truncated — close open braces and try to parse
        if depth > 0:
            # Trim trailing incomplete value (after last comma or colon)
            candidate = text[start:]
            # Remove trailing partial string/value after last complete key-value
            candidate = re.sub(r',\s*"[^"]*"?\s*:?\s*[^,}\]]*$', "", candidate)
            candidate += "}" * depth
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                return None
        return None

    async def _run_verify_loop(
        self,
        task: str,
        image_base64: str,
        ctx: PipelineContext,
        phase: str = "",
        max_turns: int = 4,
    ) -> AsyncGenerator[PipelineStageResult, None]:
        """Agentic verify loop using native OpenAI function calling.

        The loop:
        1. Calls LLM with tool definitions
        2. If response contains function_call items → execute tools, append results, re-call
        3. If response contains text only → parse as VerificationResult, done
        """
        verify_model = self._get_model_id(self.verify_adapter)
        tool_schemas, executors = self._build_verify_tools(image_base64, task, ctx)

        yield PipelineStageResult(
            stage="verify", status=StageStatus.RUNNING, model_id=verify_model, phase=phase
        )

        input_items = self._build_verify_input(task, image_base64, ctx)
        executed_tools: dict[str, Any] = {}

        t0 = time.monotonic()
        try:
            for turn in range(max_turns):
                response = await self._verify_call(input_items, tool_schemas, verify_model)

                # Extract function calls from response
                fn_calls = []
                if hasattr(response, "output"):
                    for item in response.output:
                        item_type = getattr(item, "type", None)
                        if item_type == "function_call":
                            fn_calls.append(item)

                if not fn_calls:
                    # Final verdict — no more tool calls
                    verification = self._parse_verify_response(response, turn=turn + 1)

                    # Apply dynamics sanity checks if we have dynamics data
                    if ctx.dynamics_analysis:
                        verification = self._apply_dynamics_sanity_checks(
                            verification, ctx.dynamics_analysis
                        )

                    latency = (time.monotonic() - t0) * 1000
                    yield PipelineStageResult(
                        stage="verify",
                        status=StageStatus.COMPLETED,
                        latency_ms=round(latency, 1),
                        output=verification.model_dump(),
                        model_id=verify_model,
                        phase=phase,
                    )
                    return

                # Yield turn sub-step for dashboard
                tool_names = [getattr(fc, "name", "?") for fc in fn_calls]
                yield PipelineStageResult(
                    stage="verify",
                    status=StageStatus.RUNNING,
                    model_id=verify_model,
                    phase=phase,
                    output={
                        "substep": "turn",
                        "turn": turn + 1,
                        "tools_requested": tool_names,
                    },
                )

                # Execute each requested tool
                for fc in fn_calls:
                    fc_name = getattr(fc, "name", None)
                    fc_call_id = getattr(fc, "call_id", None)
                    if not fc_name or fc_name in executed_tools:
                        # Already executed or unknown — send empty result
                        if fc_call_id:
                            input_items.append(fc)
                            input_items.append(
                                {
                                    "type": "function_call_output",
                                    "call_id": fc_call_id,
                                    "output": json.dumps(executed_tools.get(fc_name, {})),
                                }
                            )
                        continue

                    executor = executors.get(fc_name)
                    if not executor:
                        if fc_call_id:
                            input_items.append(fc)
                            input_items.append(
                                {
                                    "type": "function_call_output",
                                    "call_id": fc_call_id,
                                    "output": json.dumps({"error": f"Unknown tool: {fc_name}"}),
                                }
                            )
                        continue

                    tool_t0 = time.monotonic()
                    try:
                        result = await executor()
                    except Exception as e:
                        logger.warning("Verify tool '%s' failed: %s", fc_name, e)
                        result = {"error": str(e)}
                    tool_latency = (time.monotonic() - tool_t0) * 1000

                    executed_tools[fc_name] = result

                    # Append function_call + function_call_output to conversation
                    input_items.append(fc)
                    input_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": fc_call_id,
                            "output": json.dumps(result, default=str),
                        }
                    )

                    # Yield tool sub-step for dashboard
                    yield PipelineStageResult(
                        stage="verify",
                        status=StageStatus.RUNNING,
                        model_id=verify_model,
                        phase=phase,
                        output={
                            "substep": "tool",
                            "tool": fc_name,
                            "latency_ms": round(tool_latency, 1),
                        },
                    )

            # Max turns exhausted — parse whatever we have
            verification = self._parse_verify_response(response, turn=max_turns)
            if ctx.dynamics_analysis:
                verification = self._apply_dynamics_sanity_checks(
                    verification, ctx.dynamics_analysis
                )
            latency = (time.monotonic() - t0) * 1000
            yield PipelineStageResult(
                stage="verify",
                status=StageStatus.COMPLETED,
                latency_ms=round(latency, 1),
                output=verification.model_dump(),
                model_id=verify_model,
                phase=phase,
            )
        except Exception as e:
            latency = (time.monotonic() - t0) * 1000
            logger.exception("verify loop failed")
            yield PipelineStageResult(
                stage="verify",
                status=StageStatus.ERROR,
                latency_ms=round(latency, 1),
                error=str(e),
                model_id=verify_model,
                phase=phase,
            )

    def _prepare_context(
        self,
        task: str,
        image_base64: str,
        example: ExampleData | None,
    ) -> PipelineContext:
        """Build initial PipelineContext from inputs and example data."""
        ground_truth = example.ground_truth if example else None
        ctx = PipelineContext(task=task, image_base64=image_base64, ground_truth=ground_truth)

        # Seed proprioception from example data (dataset mode, no sim)
        if example and example.extras.get("proprioception"):
            ctx.proprioception = example.extras["proprioception"]

        # Carry task metadata (constraints, eval_category, correction, etc.)
        if example:
            meta: dict = {}
            for key in (
                "eval_category",
                "constraints",
                "correction",
                "turns",
                "expected_subtasks",
                "acceptable_interpretations",
                "difficulty",
                "robot",
                "action_dim",
                "state_dim",
            ):
                val = example.extras.get(key)
                if val is not None:
                    meta[key] = val
            if meta:
                ctx.task_metadata = meta

        # Auto-detect URDF from example robot type if dynamics enabled but no URDF provided
        if self._compute_dynamics and not self._urdf_path and example:
            robot = example.extras.get("robot")
            if robot:
                from pathlib import Path

                data_dir = Path(__file__).parent.parent.parent.parent / "data"
                candidate = data_dir / "urdf" / robot / f"{robot}.urdf"
                if candidate.exists():
                    self._urdf_path = str(candidate)
                    logger.info("Auto-detected URDF for robot '%s': %s", robot, candidate)

        return ctx

    async def run_trial(
        self,
        task: str,
        image_base64: str,
        eval_id: str | None = None,
        example: ExampleData | None = None,
    ) -> AsyncGenerator[PipelineStageResult, None]:
        """Run a trial, yielding stage results as they complete.

        Dispatches to sequential or parallel path based on pipeline_mode.
        """
        eval_id = eval_id or str(uuid.uuid4())
        ctx = self._prepare_context(task, image_base64, example)

        if self._pipeline_mode == "parallel" and self.act_adapter is not None:
            async for result in self._run_parallel(task, image_base64, ctx):
                yield result
        else:
            async for result in self._run_sequential(task, image_base64, ctx):
                yield result

    async def _run_sequential(
        self,
        task: str,
        image_base64: str,
        ctx: PipelineContext,
    ) -> AsyncGenerator[PipelineStageResult, None]:
        """Sequential pipeline: perceive → plan → act → verify. No phase labels."""
        scene = None
        plan = None
        reset_obs = None

        # --- PERCEIVE (optional) ---
        if self.perceive_adapter is not None:
            perceive_model = self._get_model_id(self.perceive_adapter)
            yield PipelineStageResult(
                stage="perceive", status=StageStatus.RUNNING, model_id=perceive_model
            )
            t0 = time.monotonic()
            try:
                scene = await self._call_adapter(
                    "perceive", self.perceive_adapter, image_base64, task
                )
                ctx.scene = scene
                ctx.completed_stages.append("perceive")
                latency = (time.monotonic() - t0) * 1000
                yield PipelineStageResult(
                    stage="perceive",
                    status=StageStatus.COMPLETED,
                    latency_ms=round(latency, 1),
                    output=scene.model_dump(),
                    model_id=perceive_model,
                )
            except Exception as e:
                latency = (time.monotonic() - t0) * 1000
                logger.exception("perceive failed")
                yield PipelineStageResult(
                    stage="perceive",
                    status=StageStatus.ERROR,
                    latency_ms=round(latency, 1),
                    error=str(e),
                    model_id=perceive_model,
                )
                return

        # --- SIM RESET + PROPRIOCEPTION (only if act stage is present) ---
        if self.act_adapter is not None and self.sim is not None:
            try:
                reset_obs = await self.sim.reset(task)
                ctx.proprioception = reset_obs.proprioception
            except Exception as e:
                logger.warning(f"sim.reset failed: {e}")

        # --- PLAN (optional) ---
        if self.plan_adapter is not None:
            plan_model = self._get_model_id(self.plan_adapter)
            yield PipelineStageResult(stage="plan", status=StageStatus.RUNNING, model_id=plan_model)
            t0 = time.monotonic()
            try:
                plan = await self._call_adapter(
                    "plan",
                    self.plan_adapter,
                    image_base64,
                    task,
                    scene=scene,
                    task_metadata=ctx.task_metadata,
                )
                ctx.plan = plan
                ctx.completed_stages.append("plan")
                latency = (time.monotonic() - t0) * 1000
                yield PipelineStageResult(
                    stage="plan",
                    status=StageStatus.COMPLETED,
                    latency_ms=round(latency, 1),
                    output=plan.model_dump(),
                    model_id=plan_model,
                )
            except Exception as e:
                latency = (time.monotonic() - t0) * 1000
                logger.exception("plan failed")
                yield PipelineStageResult(
                    stage="plan",
                    status=StageStatus.ERROR,
                    latency_ms=round(latency, 1),
                    error=str(e),
                    model_id=plan_model,
                )
                return

        # --- ACT (optional) ---
        if self.act_adapter is not None:
            act_model = self._get_model_id(self.act_adapter)
            yield PipelineStageResult(stage="act", status=StageStatus.RUNNING, model_id=act_model)
            t0 = time.monotonic()
            try:
                action_pred = await self._call_adapter(
                    "act",
                    self.act_adapter,
                    image_base64,
                    task,
                    scene=scene,
                    plan=plan,
                    proprioception=ctx.proprioception,
                )
                ctx.action = action_pred
                ctx.completed_stages.append("act")

                last_obs = reset_obs
                if self.sim is not None:
                    if action_pred.action_type == "trajectory" and action_pred.actions:
                        for action in action_pred.actions:
                            last_obs = await self.sim.step(action)
                            if last_obs.done:
                                break
                    elif action_pred.action_type == "tool_calls":
                        last_obs = await self.sim.get_observation()

                # Compute dynamics inline for sequential mode (bundled with act)
                dynamics_skipped_reason: str | None = None
                if self._compute_dynamics:
                    dynamics_skipped_reason = self._compute_dynamics_analysis(ctx, action_pred)

                latency = (time.monotonic() - t0) * 1000

                act_output = self._build_act_output(
                    action_pred, last_obs, ctx, dynamics_skipped_reason
                )

                yield PipelineStageResult(
                    stage="act",
                    status=StageStatus.COMPLETED,
                    latency_ms=round(latency, 1),
                    output=act_output,
                    model_id=act_model,
                )
            except Exception as e:
                latency = (time.monotonic() - t0) * 1000
                logger.exception("act failed")
                yield PipelineStageResult(
                    stage="act",
                    status=StageStatus.ERROR,
                    latency_ms=round(latency, 1),
                    error=str(e),
                    model_id=act_model,
                )
                return

        # --- VERIFY (always runs) ---
        async for result in self._run_verify(task, image_base64, ctx):
            yield result

    async def _run_parallel(
        self,
        task: str,
        image_base64: str,
        ctx: PipelineContext,
    ) -> AsyncGenerator[PipelineStageResult, None]:
        """Parallel pipeline for VLA strategies.

        Act (execution) runs concurrently with perceive→plan (evaluation context).
        Results are streamed as each branch produces them (not batched).
        Then dynamics runs (needs act output), then verify (needs everything).

        Dependency graph:
            act ──────────────────────────────┐
            perceive ──> plan ──┐             │
                                ├──> dynamics ──> verify
        """
        reset_obs = None

        # --- SIM RESET + PROPRIOCEPTION ---
        if self.sim is not None:
            try:
                reset_obs = await self.sim.reset(task)
                ctx.proprioception = reset_obs.proprioception
            except Exception as e:
                logger.warning(f"sim.reset failed: {e}")

        resolved_mode = self._resolve_verify_mode()

        # Yield RUNNING events for all concurrent stages upfront
        act_model = self._get_model_id(self.act_adapter)
        yield PipelineStageResult(
            stage="act", status=StageStatus.RUNNING, model_id=act_model, phase="execution"
        )
        # In agent_loop mode, perceive/plan become verify sub-steps — don't show them here
        if resolved_mode != "agent_loop" and self.perceive_adapter is not None:
            perceive_model = self._get_model_id(self.perceive_adapter)
            yield PipelineStageResult(
                stage="perceive",
                status=StageStatus.RUNNING,
                model_id=perceive_model,
                phase="evaluation",
            )

        # Use a queue to stream results from both branches as they complete
        _SENTINEL = object()
        queue: asyncio.Queue[PipelineStageResult | object] = asyncio.Queue()
        branches_done = 0

        async def _run_branch(coro):
            """Run a branch coroutine, pushing each result to the shared queue."""
            results = await coro
            for r in results:
                await queue.put(r)
            await queue.put(_SENTINEL)

        act_task = asyncio.create_task(
            _run_branch(self._exec_act(task, image_base64, ctx, reset_obs))
        )

        # In agent_loop mode, skip eval_context — perceive/plan are verify sub-steps
        eval_task = None
        if resolved_mode != "agent_loop":
            eval_task = asyncio.create_task(
                _run_branch(self._exec_eval_context(task, image_base64, ctx))
            )

        # Stream results as they arrive from branches
        total_branches = 2 if eval_task is not None else 1
        act_failed = False
        while branches_done < total_branches:
            item = await queue.get()
            if item is _SENTINEL:
                branches_done += 1
                continue
            result = item  # type: PipelineStageResult
            yield result
            if result.stage == "act" and result.status == StageStatus.ERROR:
                act_failed = True

        # Ensure tasks are complete (should already be)
        await act_task
        if eval_task is not None:
            await eval_task

        if act_failed:
            return

        if resolved_mode == "agent_loop":
            # Agent loop mode: perceive/plan/dynamics become verify sub-steps
            # Only run dynamics eagerly if compute_dynamics is set (tool needs URDF)
            async for result in self._run_verify_loop(task, image_base64, ctx, phase="evaluation"):
                yield result
        else:
            # Precompute mode: run dynamics as separate stage, then single-call verify
            if self._compute_dynamics and ctx.action:
                async for result in self._run_dynamics(ctx, ctx.action):
                    yield result

            async for result in self._run_verify(task, image_base64, ctx, phase="evaluation"):
                yield result

    async def _exec_act(
        self,
        task: str,
        image_base64: str,
        ctx: PipelineContext,
        reset_obs: Any,
    ) -> list[PipelineStageResult]:
        """Execute VLA act stage. Returns list of completed/error results."""
        act_model = self._get_model_id(self.act_adapter)
        t0 = time.monotonic()
        try:
            action_pred = await self._call_adapter(
                "act",
                self.act_adapter,
                image_base64,
                task,
                proprioception=ctx.proprioception,
            )
            ctx.action = action_pred
            ctx.completed_stages.append("act")

            last_obs = reset_obs
            if self.sim is not None:
                if action_pred.action_type == "trajectory" and action_pred.actions:
                    for action in action_pred.actions:
                        last_obs = await self.sim.step(action)
                        if last_obs.done:
                            break
                elif action_pred.action_type == "tool_calls":
                    last_obs = await self.sim.get_observation()

            latency = (time.monotonic() - t0) * 1000

            act_output: dict[str, Any] = action_pred.model_dump()
            act_output["sim_done"] = last_obs.done if last_obs else False
            act_output["sim_success"] = last_obs.success if last_obs else False
            act_output["sim_is_mock"] = self.sim is not None and getattr(
                self.sim, "model_id", ""
            ).startswith("mock")
            if action_pred.action_type == "trajectory":
                act_output["actions_executed"] = len(action_pred.actions)

            return [
                PipelineStageResult(
                    stage="act",
                    status=StageStatus.COMPLETED,
                    latency_ms=round(latency, 1),
                    output=act_output,
                    model_id=act_model,
                    phase="execution",
                )
            ]
        except Exception as e:
            latency = (time.monotonic() - t0) * 1000
            logger.exception("act failed")
            return [
                PipelineStageResult(
                    stage="act",
                    status=StageStatus.ERROR,
                    latency_ms=round(latency, 1),
                    error=str(e),
                    model_id=act_model,
                    phase="execution",
                )
            ]

    async def _exec_eval_context(
        self,
        task: str,
        image_base64: str,
        ctx: PipelineContext,
    ) -> list[PipelineStageResult]:
        """Run perceive→plan as evaluation context. Returns list of results.

        These run concurrently with act but sequentially with each other
        (plan depends on perceive's scene output).
        """
        results: list[PipelineStageResult] = []
        scene = None

        # --- PERCEIVE ---
        if self.perceive_adapter is not None:
            perceive_model = self._get_model_id(self.perceive_adapter)
            t0 = time.monotonic()
            try:
                scene = await self._call_adapter(
                    "perceive", self.perceive_adapter, image_base64, task
                )
                ctx.scene = scene
                ctx.completed_stages.append("perceive")
                latency = (time.monotonic() - t0) * 1000
                results.append(
                    PipelineStageResult(
                        stage="perceive",
                        status=StageStatus.COMPLETED,
                        latency_ms=round(latency, 1),
                        output=scene.model_dump(),
                        model_id=perceive_model,
                        phase="evaluation",
                    )
                )
            except Exception as e:
                latency = (time.monotonic() - t0) * 1000
                logger.exception("perceive failed")
                results.append(
                    PipelineStageResult(
                        stage="perceive",
                        status=StageStatus.ERROR,
                        latency_ms=round(latency, 1),
                        error=str(e),
                        model_id=perceive_model,
                        phase="evaluation",
                    )
                )

        # --- PLAN (depends on perceive scene) ---
        if self.plan_adapter is not None:
            plan_model = self._get_model_id(self.plan_adapter)
            # Yield RUNNING for plan (appended to results so caller yields it)
            results.append(
                PipelineStageResult(
                    stage="plan",
                    status=StageStatus.RUNNING,
                    model_id=plan_model,
                    phase="evaluation",
                )
            )
            t0 = time.monotonic()
            try:
                plan = await self._call_adapter(
                    "plan",
                    self.plan_adapter,
                    image_base64,
                    task,
                    scene=scene,
                    task_metadata=ctx.task_metadata,
                )
                ctx.plan = plan
                ctx.completed_stages.append("plan")
                latency = (time.monotonic() - t0) * 1000
                results.append(
                    PipelineStageResult(
                        stage="plan",
                        status=StageStatus.COMPLETED,
                        latency_ms=round(latency, 1),
                        output=plan.model_dump(),
                        model_id=plan_model,
                        phase="evaluation",
                    )
                )
            except Exception as e:
                latency = (time.monotonic() - t0) * 1000
                logger.exception("plan failed")
                results.append(
                    PipelineStageResult(
                        stage="plan",
                        status=StageStatus.ERROR,
                        latency_ms=round(latency, 1),
                        error=str(e),
                        model_id=plan_model,
                        phase="evaluation",
                    )
                )

        return results

    def _compute_dynamics_analysis(
        self, ctx: PipelineContext, action_pred: ActionPrediction
    ) -> str | None:
        """Compute dynamics analysis, returning skip reason if skipped."""
        if not self._urdf_path:
            logger.warning("Dynamics enabled but no URDF path — skipping")
            return "No URDF provided — upload a URDF or use a dataset with robot metadata"
        if not action_pred.actions:
            logger.warning("Dynamics enabled but no actions — skipping")
            return "No trajectory actions to analyze"
        try:
            from rove.adapters.dynamics_mujoco import compute_dynamics

            dyn = compute_dynamics(
                self._urdf_path,
                action_pred.actions,
                initial_qpos=ctx.proprioception or None,
            )
            ctx.dynamics_analysis = dyn
            logger.info("Dynamics analysis: %d steps", dyn.get("steps_analyzed", 0))
            return None
        except ImportError:
            logger.warning("MuJoCo not installed — skipping dynamics")
            return "MuJoCo not installed"
        except Exception as dyn_err:
            logger.warning("Dynamics computation failed: %s", dyn_err)
            return f"Dynamics computation failed: {dyn_err}"

    def _build_act_output(
        self,
        action_pred: ActionPrediction,
        last_obs: Any,
        ctx: PipelineContext,
        dynamics_skipped_reason: str | None,
    ) -> dict[str, Any]:
        """Build the act stage output dict."""
        act_output: dict[str, Any] = action_pred.model_dump()
        act_output["sim_done"] = last_obs.done if last_obs else False
        act_output["sim_success"] = last_obs.success if last_obs else False
        act_output["sim_is_mock"] = self.sim is not None and getattr(
            self.sim, "model_id", ""
        ).startswith("mock")
        if action_pred.action_type == "trajectory":
            act_output["actions_executed"] = len(action_pred.actions)
        if ctx.dynamics_analysis:
            act_output["dynamics_analysis"] = ctx.dynamics_analysis
        if dynamics_skipped_reason:
            act_output["dynamics_skipped"] = dynamics_skipped_reason
        return act_output

    async def _run_dynamics(
        self,
        ctx: PipelineContext,
        action_pred: ActionPrediction,
    ) -> AsyncGenerator[PipelineStageResult, None]:
        """Run dynamics analysis as a separate evaluation stage."""
        yield PipelineStageResult(stage="dynamics", status=StageStatus.RUNNING, phase="evaluation")
        t0 = time.monotonic()

        skip_reason = self._compute_dynamics_analysis(ctx, action_pred)
        latency = (time.monotonic() - t0) * 1000

        if skip_reason:
            yield PipelineStageResult(
                stage="dynamics",
                status=StageStatus.COMPLETED,
                latency_ms=round(latency, 1),
                output={"skipped": skip_reason},
                phase="evaluation",
            )
        else:
            yield PipelineStageResult(
                stage="dynamics",
                status=StageStatus.COMPLETED,
                latency_ms=round(latency, 1),
                output=ctx.dynamics_analysis,
                phase="evaluation",
            )

    async def _run_verify(
        self,
        task: str,
        image_base64: str,
        ctx: PipelineContext,
        phase: str = "",
    ) -> AsyncGenerator[PipelineStageResult, None]:
        """Run the verify stage."""
        verify_model = self._get_model_id(self.verify_adapter)
        yield PipelineStageResult(
            stage="verify", status=StageStatus.RUNNING, model_id=verify_model, phase=phase
        )
        t0 = time.monotonic()
        try:
            # Get post-action observation if sim was used, otherwise compare same image
            if self.sim is not None and self.act_adapter is not None:
                after_obs = await self.sim.get_observation()
                after_image = after_obs.image_base64 or image_base64
            else:
                after_image = image_base64
            ctx.after_image_base64 = after_image

            verification = await self._call_adapter(
                "verify",
                self.verify_adapter,
                image_base64,
                task,
                after_image=after_image,
                pipeline_context=ctx.to_dict(),
            )
            if ctx.dynamics_analysis:
                verification = self._apply_dynamics_sanity_checks(
                    verification, ctx.dynamics_analysis
                )
            latency = (time.monotonic() - t0) * 1000
            yield PipelineStageResult(
                stage="verify",
                status=StageStatus.COMPLETED,
                latency_ms=round(latency, 1),
                output=verification.model_dump(),
                model_id=verify_model,
                phase=phase,
            )
        except Exception as e:
            latency = (time.monotonic() - t0) * 1000
            logger.exception("verify failed")
            yield PipelineStageResult(
                stage="verify",
                status=StageStatus.ERROR,
                latency_ms=round(latency, 1),
                error=str(e),
                model_id=verify_model,
                phase=phase,
            )
