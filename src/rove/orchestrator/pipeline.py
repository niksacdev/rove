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
    ActionSpace,
    ExampleData,
    PipelineContext,
    PipelineStageResult,
    RobotEmbodiment,
    SceneAnalysis,
    StageStatus,
    TaskPlan,
    VerificationResult,
    VLACapabilities,
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

    def _build_robot_embodiment(self, ctx: PipelineContext) -> RobotEmbodiment | None:
        """Build typed robot descriptor from URDF + manifest metadata.

        Uses the same data that _build_robot_spec() currently formats as text.
        Returns None if no robot metadata available (e.g., perceive-only tasks).
        """
        meta = ctx.task_metadata or {}
        urdf_info = self._urdf_robot_info

        # Try nested robot block first (new format), then flat fields (legacy)
        robot_block = meta.get("robot_descriptor", {})
        if robot_block and isinstance(robot_block, dict):
            # New nested format — map directly to RobotEmbodiment
            return RobotEmbodiment(
                robot_type=robot_block.get("robot_type", ""),
                arm_dof=robot_block.get("arm_dof", 6),
                gripper_dof=robot_block.get("gripper_dof", 1),
                action_space=ActionSpace(robot_block["action_space"])
                if robot_block.get("action_space")
                else ActionSpace.EEF_DELTA,
                proprioception_space=ActionSpace(robot_block["proprioception_space"])
                if robot_block.get("proprioception_space")
                else ActionSpace.JOINT_POSITION,
                gripper_index=robot_block.get("gripper_index"),
                state_dim_override=robot_block.get("state_dim_override"),
                urdf_path=self._urdf_path,
                embodiment_tag=robot_block.get("embodiment_tag"),
            )

        # Legacy flat format
        robot_type = meta.get("robot", "") or urdf_info.get("robot_name", "")
        if not robot_type:
            return None

        action_dim = meta.get("action_dim")
        arm_dof = urdf_info.get("dof") or (action_dim - 1 if action_dim else 6)
        state_dim = meta.get("state_dim")
        control_space = meta.get("control_space", "")

        # Map legacy control_space strings to ActionSpace enum
        space_map = {
            "end_effector_delta": ActionSpace.EEF_DELTA,
            "eef_delta": ActionSpace.EEF_DELTA,
            "joint_position": ActionSpace.JOINT_POSITION,
            "joint_delta": ActionSpace.JOINT_DELTA,
            "eef_absolute": ActionSpace.EEF_ABSOLUTE,
        }
        action_space = space_map.get(control_space, ActionSpace.EEF_DELTA)

        return RobotEmbodiment(
            robot_type=robot_type,
            arm_dof=arm_dof,
            gripper_dof=1,
            action_space=action_space,
            gripper_index=(action_dim - 1) if action_dim else None,
            state_dim_override=state_dim
            if state_dim and action_dim and state_dim != action_dim
            else None,
            urdf_path=self._urdf_path,
        )

    def _validate_embodiment_compat(
        self,
        embodiment: RobotEmbodiment,
        capabilities: VLACapabilities,
        model_id: str,
    ) -> list[str]:
        """Validate robot embodiment is compatible with VLA capabilities.

        Returns a list of warnings. Raises ValueError for hard incompatibilities.
        """
        warnings: list[str] = []

        # Checkpoint-bound model on wrong robot
        if (
            capabilities.supported_robot_types is not None
            and embodiment.robot_type not in capabilities.supported_robot_types
        ):
            raise ValueError(
                f"VLA '{model_id}' supports robots {capabilities.supported_robot_types} "
                f"but task requires '{embodiment.robot_type}'"
            )

        # Target action_dim exceeds model's max
        if (
            capabilities.max_action_dim is not None
            and embodiment.action_dim > capabilities.max_action_dim
        ):
            raise ValueError(
                f"VLA '{model_id}' max action dim is {capabilities.max_action_dim} "
                f"but robot requires {embodiment.action_dim}"
            )

        # Embodiment tag required but missing
        if capabilities.requires_embodiment_tag and not embodiment.embodiment_tag:
            raise ValueError(
                f"VLA '{model_id}' requires an embodiment_tag but none provided in robot metadata"
            )

        # Action space mismatch — warning, not error (valid research configuration)
        if capabilities.native_action_space != embodiment.action_space:
            warnings.append(
                f"Action space mismatch: VLA outputs {capabilities.native_action_space.value} "
                f"but robot expects {embodiment.action_space.value}"
            )

        return warnings

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
                embodiment=context.get("embodiment"),
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
    @staticmethod
    def _validate_evidence_consistency(
        verification: VerificationResult, dynamics: dict | None
    ) -> VerificationResult:
        """Lightweight consistency check. Flags contradictions, nullifies
        hallucinated fields, but does NOT override LLM scores.

        Three rules:
        1. If no dynamics was provided, null out fields that require physics data.
        2. If dynamics was provided, flag contradictions between LLM scores and
           hard evidence (without changing the scores).
        3. Log if confidence seems miscalibrated vs evidence quality.
        """
        ap = verification.action_plausibility
        if ap is None:
            return verification

        contradictions: list[str] = []

        if dynamics is None:
            # No dynamics — null out fields that require physics data
            changed = False
            for field in (
                "bounds_check",
                "dynamics_consistency",
                "workspace_reachability",
                "safety_assessment",
            ):
                if getattr(ap, field, None) is not None:
                    setattr(ap, field, None)
                    changed = True
            if changed and not ap.evidence_quality:
                ap.evidence_quality = "perception_only"
        else:
            # Dynamics was provided — flag contradictions without overriding
            evidence_map = {e["field"]: e for e in dynamics.get("evidence", [])}

            # Check joint limits contradiction
            jl = evidence_map.get("joint_limits_ok")
            if (
                jl
                and jl["confidence"] == "hard"
                and not jl["value"]
                and ap.bounds_check is not None
                and ap.bounds_check > 0.5
            ):
                contradictions.append(
                    f"bounds_check={ap.bounds_check} but hard evidence shows joint limit violations"
                )

            # Check self-collision contradiction
            sc = evidence_map.get("self_collision")
            if (
                sc
                and sc["confidence"] == "hard"
                and sc["value"]
                and ap.safety_assessment is not None
                and ap.safety_assessment > 0.7
            ):
                contradictions.append(
                    f"safety_assessment={ap.safety_assessment} but hard evidence "
                    f"shows self-collision"
                )

            # Check torque contradiction
            tf = evidence_map.get("torque_feasible")
            if (
                tf
                and tf["confidence"] == "hard"
                and not tf["value"]
                and ap.safety_assessment is not None
                and ap.safety_assessment > 0.7
            ):
                contradictions.append(
                    f"safety_assessment={ap.safety_assessment} but hard evidence "
                    f"shows torque infeasibility"
                )

            # Set evidence_quality if not already set
            if not ap.evidence_quality:
                ap.evidence_quality = "hard"

            # Log miscalibration warning (don't override)
            summary = dynamics.get("summary", {})
            has_violations = (
                not summary.get("joint_limits_ok", True)
                or summary.get("self_collision", False)
                or not summary.get("torque_feasible", True)
            )
            if has_violations and verification.confidence > 0.8:
                logger.warning(
                    "Confidence %.2f seems high given physics violations — "
                    "prompt calibration may need tuning",
                    verification.confidence,
                )

        if contradictions:
            verification = verification.model_copy(update={"contradictions": contradictions})

        return verification

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
                        "Run MuJoCo analysis on the VLA action trajectory. "
                        "Returns structured evidence categorized as 'hard' (physics ground truth) "
                        "or 'estimated' (approximate). Also reports what CANNOT be computed "
                        "and what additional tooling would help."
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
        """Build robot embodiment spec for the verifier.

        Uses the typed RobotEmbodiment if available, falls back to raw metadata
        for backward compatibility. Provides the verifier with ground truth about
        the target robot so it can detect embodiment mismatches in VLA output.
        """
        emb = ctx.robot_embodiment
        if emb is not None:
            lines = [
                f"Robot: {emb.robot_type}",
                f"Arm DOF: {emb.arm_dof} (+ {emb.gripper_dof} gripper)",
                f"Action dim: {emb.action_dim}",
                f"Action space: {emb.action_space.value}",
                f"State space: {emb.proprioception_space.value}",
            ]
            if emb.state_dim_override:
                lines.append(f"State dimensions: {emb.state_dim}")
            # Add URDF joint info if available
            urdf_info = self._urdf_robot_info
            if urdf_info.get("joint_names"):
                lines.append(f"Joint names: {', '.join(urdf_info['joint_names'])}")
            if urdf_info.get("mimic_joints"):
                lines.append(
                    f"Mimic joints (not independent): {', '.join(urdf_info['mimic_joints'])}"
                )
            # Note EE-delta vs joint DOF mismatch
            dof = urdf_info.get("dof")
            if (
                dof is not None
                and dof != emb.action_dim
                and emb.action_space == ActionSpace.EEF_DELTA
            ):
                lines.append(
                    f"NOTE: Action dim ({emb.action_dim}) != joint DOF ({dof}) — "
                    f"EE-delta control space. A low-level controller maps EE deltas to joints."
                )
            # Action space description from metadata (human-readable)
            action_space_desc = (ctx.task_metadata or {}).get("action_space_desc", "")
            if action_space_desc:
                lines.append(f"Action space description: {action_space_desc}")
            if ctx.proprioception:
                lines.append(f"Proprioception dimensions: {len(ctx.proprioception)}")
            return "\n".join(lines)

        # Fallback: raw metadata (no RobotEmbodiment available)
        lines: list[str] = []
        meta = ctx.task_metadata or {}
        urdf_info = self._urdf_robot_info

        robot_name = meta.get("robot", "") or urdf_info.get("robot_name", "")
        action_dim = meta.get("action_dim")
        state_dim = meta.get("state_dim")
        dof = urdf_info.get("dof")

        if not robot_name and not action_dim and not dof:
            return ""

        control_space = meta.get("control_space", "")
        action_space_desc = meta.get("action_space_desc", "")

        if robot_name:
            lines.append(f"Robot: {robot_name}")
        if dof is not None:
            joint_names = urdf_info.get("joint_names", [])
            mimic_joints = urdf_info.get("mimic_joints", [])
            lines.append(f"Joint DOF (from URDF): {dof} independent joints")
            if joint_names:
                lines.append(f"Joint names: {', '.join(joint_names)}")
            if mimic_joints:
                lines.append(f"Mimic joints (not independent): {', '.join(mimic_joints)}")
        if action_dim is not None:
            lines.append(f"Action dimensions: {action_dim}")
        if control_space:
            lines.append(f"Control space: {control_space}")
        if action_space_desc:
            lines.append(f"Action space: {action_space_desc}")
        if (
            dof is not None
            and action_dim is not None
            and dof != action_dim
            and control_space == "end_effector_delta"
        ):
            lines.append(
                f"NOTE: Action dim ({action_dim}) != joint DOF ({dof}) — "
                f"EE-delta control space. A low-level controller maps EE deltas to joints."
            )
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
        instructions = pm.render_verify_loop_system()

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

    def _compile_physics_evidence(self, dynamics_analysis: dict | None) -> str:
        """Format dynamics analysis into evidence text for the verifier.

        No score modification. The LLM reasons about this evidence.
        """
        if not dynamics_analysis:
            return ""

        lines = ["## Physics Evidence (MuJoCo Dynamics Analysis)", ""]
        mode = dynamics_analysis.get("analysis_mode", "unknown")
        mode_desc = {
            "joint_space": "joint-space (exact)",
            "ee_delta_jacobian_ik": "end-effector delta (resolved via Jacobian pseudoinverse IK)",
        }.get(mode, mode)
        lines.append(f"Analysis mode: {mode_desc}")
        lines.append("")

        # Evidence items
        evidence = dynamics_analysis.get("evidence", [])
        summary = dynamics_analysis.get("summary", {})

        lines.append("### Trajectory Analysis")
        for item in evidence:
            field = item.get("field", "")
            value = item.get("value")
            detail = item.get("detail", "")
            source = item.get("source", "")

            if field == "endpoint_trajectory":
                traj = value or []
                if traj:
                    lines.append(
                        f"- EE trajectory: {traj[0]} -> {traj[-1]} ({len(traj)} points, source: {source})"
                    )
                continue
            if field in (
                "joint_limits_ok",
                "self_collision",
                "torque_feasible",
                "gravity_feasible",
                "near_singularity",
            ):
                label = field.replace("_", " ").title()
                status = (
                    "OK"
                    if (value and field != "self_collision")
                    or (not value and field == "self_collision")
                    else "VIOLATED"
                    if field != "self_collision"
                    else "DETECTED"
                )
                detail_str = f" ({detail})" if detail else ""
                lines.append(f"- {label}: {status}{detail_str} [source: {source}]")
            elif field == "manipulability":
                lines.append(
                    f"- Manipulability: {value}{' (' + detail + ')' if detail else ''} [source: {source}]"
                )

        # Summary stats
        if summary:
            lines.append("")
            lines.append("### Summary Statistics")
            lines.append(f"- Total displacement: {summary.get('total_displacement_m', 0):.3f}m")
            lines.append(f"- Smoothness score: {summary.get('smoothness_score', 0):.2f}")
            lines.append(f"- Steps analyzed: {summary.get('steps_analyzed', 0)}")
            ep = summary.get("final_endpoint", [0, 0, 0])
            lines.append(f"- Final endpoint: [{ep[0]:.3f}, {ep[1]:.3f}, {ep[2]:.3f}]m")
            if summary.get("peak_torque_nm", 0) > 0:
                lines.append(f"- Peak torque: {summary['peak_torque_nm']:.2f} Nm")

        # Gripper events
        gripper_events = dynamics_analysis.get("gripper_events", [])
        if gripper_events:
            lines.append("")
            lines.append("### Gripper Events")
            for evt in gripper_events:
                lines.append(f"- {evt['type'].title()} at step {evt['step']}")

        # Not computed
        not_computed = dynamics_analysis.get("not_computed", [])
        if not_computed:
            lines.append("")
            lines.append("### Not Computed")
            for nc in not_computed:
                lines.append(f"- {nc.get('field', '?')}: {nc.get('reason', '')}")
                if nc.get("would_need"):
                    lines.append(f"  Would need: {nc['would_need']}")

        return "\n".join(lines)

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

                    # Validate evidence consistency (flags contradictions, nullifies hallucinations)
                    has_dynamics = "compute_dynamics" in executed_tools and ctx.dynamics_analysis
                    verification = self._validate_evidence_consistency(
                        verification, ctx.dynamics_analysis if has_dynamics else None
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
            has_dynamics = "compute_dynamics" in executed_tools and ctx.dynamics_analysis
            verification = self._validate_evidence_consistency(
                verification, ctx.dynamics_analysis if has_dynamics else None
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
                "control_space",
                "action_space_desc",
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

        # Build typed robot embodiment from URDF + manifest metadata
        ctx.robot_embodiment = self._build_robot_embodiment(ctx)

        # Validate VLA compatibility at config time (fail fast)
        if ctx.robot_embodiment and self.act_adapter is not None:
            caps = getattr(self.act_adapter, "capabilities", None)
            if caps is not None:
                warnings = self._validate_embodiment_compat(
                    ctx.robot_embodiment, caps, self.act_adapter.model_id
                )
                for w in warnings:
                    logger.warning("Embodiment compat: %s", w)

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
                    embodiment=ctx.robot_embodiment,
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
                embodiment=ctx.robot_embodiment,
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

        meta = ctx.task_metadata or {}
        control_space = meta.get("control_space", "joint_space")

        try:
            from rove.adapters.dynamics_mujoco import compute_dynamics

            dyn = compute_dynamics(
                self._urdf_path,
                action_pred.actions,
                initial_qpos=ctx.proprioception or None,
                control_space=control_space,
            )
            ctx.dynamics_analysis = dyn
            logger.info(
                "Dynamics analysis: %d steps, mode=%s",
                dyn.get("summary", {}).get("steps_analyzed", 0),
                dyn.get("analysis_mode", "unknown"),
            )
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
            verification = self._validate_evidence_consistency(verification, ctx.dynamics_analysis)
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
