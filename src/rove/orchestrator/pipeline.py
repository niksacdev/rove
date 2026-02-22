"""Evaluation pipeline — deterministic perceive → plan → act → verify."""

from __future__ import annotations

import dataclasses
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from rove.adapters.protocols import AgentAdapter, PolicyAdapter, SimAdapter, StageAdapter, VLMAdapter
from rove.models import (
    ActionPrediction,
    PipelineContext,
    PipelineStageResult,
    SceneAnalysis,
    StageStatus,
    TaskPlan,
    VerificationResult,
)

logger = logging.getLogger(__name__)


def _to_dict(obj: Any) -> dict | list | Any:
    """Convert dataclass to dict recursively."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_dict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_to_dict(v) for v in obj]
    return obj


def _dict_to_scene(d: dict) -> SceneAnalysis:
    return SceneAnalysis(
        objects=d.get("objects", []),
        spatial_relations=d.get("spatial_relations", []),
        task_relevant=d.get("task_relevant", []),
        raw_response=d.get("raw_response", ""),
    )


def _dict_to_plan(d: dict) -> TaskPlan:
    return TaskPlan(
        strategy=d.get("strategy", ""),
        reasoning=d.get("reasoning", ""),
        steps=d.get("steps", []),
        target_object=d.get("target_object", ""),
        confidence=float(d.get("confidence", 0.0)),
        raw_response=d.get("raw_response", ""),
    )


def _dict_to_action(d: dict) -> ActionPrediction:
    return ActionPrediction(
        actions=d.get("actions", []),
        tool_calls=d.get("tool_calls", []),
        action_type=d.get("action_type", "trajectory"),
        num_steps=d.get("num_steps", 0),
        confidence=float(d.get("confidence", 0.0)),
        raw_response=d.get("raw_response", ""),
    )


def _dict_to_verification(d: dict) -> VerificationResult:
    return VerificationResult(
        success=d.get("success", False),
        confidence=float(d.get("confidence", 0.5)),
        reasoning=d.get("reasoning", ""),
        raw_response=d.get("raw_response", ""),
    )


class EvaluationPipeline:
    """Runs the 4-stage pipeline: perceive → plan → act → verify.

    Each stage has its own adapter — they can be the same model or different models.
    Supports VLM, VLA (Policy), and Agent adapters.
    """

    def __init__(
        self,
        perceive_adapter: StageAdapter,
        plan_adapter: StageAdapter,
        act_adapter: PolicyAdapter | AgentAdapter,
        verify_adapter: StageAdapter,
        sim: SimAdapter,
    ):
        self.perceive_adapter = perceive_adapter
        self.plan_adapter = plan_adapter
        self.act_adapter = act_adapter
        self.verify_adapter = verify_adapter
        self.sim = sim

    async def _call_adapter(
        self,
        stage: str,
        adapter: StageAdapter,
        image_base64: str,
        task: str,
        **context: Any,
    ) -> Any:
        """Dispatch to the appropriate adapter method based on adapter type and stage."""
        if isinstance(adapter, AgentAdapter):
            # Serialize dataclass objects in context for agent adapters
            agent_context = {k: _to_dict(v) for k, v in context.items()} if context else None
            raw = await adapter.run_stage(stage, image_base64, task, agent_context)
            converters = {
                "perceive": _dict_to_scene,
                "plan": _dict_to_plan,
                "act": _dict_to_action,
                "verify": _dict_to_verification,
            }
            return converters[stage](raw)

        # Type-specific calls for VLM/Policy adapters
        if stage == "perceive":
            return await adapter.analyze_scene(image_base64, task)
        elif stage == "plan":
            scene = context.get("scene")
            return await adapter.plan_task(image_base64, task, scene)
        elif stage == "act":
            return await adapter.predict_action(
                image_base64, task,
                proprioception=context.get("proprioception"),
                plan=context.get("plan"),
            )
        elif stage == "verify":
            after_image = context.get("after_image", image_base64)
            return await adapter.verify_success(
                image_base64, after_image, task,
                context=context.get("pipeline_context"),
            )
        else:
            raise ValueError(f"Unknown stage: {stage}")

    def _get_model_id(self, adapter: StageAdapter) -> str:
        return adapter.model_id

    async def run_trial(
        self,
        task: str,
        image_base64: str,
        eval_id: str | None = None,
    ) -> AsyncGenerator[PipelineStageResult, None]:
        """Run a full trial, yielding stage results as they complete."""
        eval_id = eval_id or str(uuid.uuid4())
        ctx = PipelineContext(task=task, image_base64=image_base64)

        # --- PERCEIVE ---
        perceive_model = self._get_model_id(self.perceive_adapter)
        yield PipelineStageResult(stage="perceive", status=StageStatus.RUNNING, model_id=perceive_model)
        t0 = time.monotonic()
        try:
            scene = await self._call_adapter("perceive", self.perceive_adapter, image_base64, task)
            ctx.scene = scene
            latency = (time.monotonic() - t0) * 1000
            yield PipelineStageResult(
                stage="perceive", status=StageStatus.COMPLETED,
                latency_ms=round(latency, 1), output=_to_dict(scene), model_id=perceive_model,
            )
        except Exception as e:
            latency = (time.monotonic() - t0) * 1000
            logger.exception("perceive failed")
            yield PipelineStageResult(
                stage="perceive", status=StageStatus.ERROR,
                latency_ms=round(latency, 1), error=str(e), model_id=perceive_model,
            )
            return

        # --- SIM RESET + PROPRIOCEPTION ---
        try:
            reset_obs = await self.sim.reset(task)
            ctx.proprioception = reset_obs.proprioception
        except Exception as e:
            logger.warning(f"sim.reset failed: {e}")

        # --- PLAN ---
        plan = None
        plan_model = self._get_model_id(self.plan_adapter)
        yield PipelineStageResult(stage="plan", status=StageStatus.RUNNING, model_id=plan_model)
        t0 = time.monotonic()
        try:
            plan = await self._call_adapter(
                "plan", self.plan_adapter, image_base64, task,
                scene=scene,
            )
            ctx.plan = plan
            latency = (time.monotonic() - t0) * 1000
            yield PipelineStageResult(
                stage="plan", status=StageStatus.COMPLETED,
                latency_ms=round(latency, 1), output=_to_dict(plan), model_id=plan_model,
            )
        except Exception as e:
            latency = (time.monotonic() - t0) * 1000
            logger.exception("plan failed")
            yield PipelineStageResult(
                stage="plan", status=StageStatus.ERROR,
                latency_ms=round(latency, 1), error=str(e), model_id=plan_model,
            )
            return

        # --- ACT ---
        act_model = self._get_model_id(self.act_adapter)
        yield PipelineStageResult(stage="act", status=StageStatus.RUNNING, model_id=act_model)
        t0 = time.monotonic()
        try:
            action_pred = await self._call_adapter(
                "act", self.act_adapter, image_base64, task,
                scene=scene, plan=plan, proprioception=ctx.proprioception,
            )
            ctx.action = action_pred

            last_obs = reset_obs
            if action_pred.action_type == "trajectory" and action_pred.actions:
                for action in action_pred.actions:
                    last_obs = await self.sim.step(action)
                    if last_obs.done:
                        break
            elif action_pred.action_type == "tool_calls":
                last_obs = await self.sim.get_observation()

            latency = (time.monotonic() - t0) * 1000

            act_output: dict[str, Any] = {
                "action_type": action_pred.action_type,
                "num_steps": action_pred.num_steps,
                "confidence": action_pred.confidence,
                "sim_done": last_obs.done,
                "sim_success": last_obs.success,
            }
            if action_pred.action_type == "trajectory":
                act_output["actions_executed"] = len(action_pred.actions)
            elif action_pred.action_type == "tool_calls":
                act_output["tool_calls"] = action_pred.tool_calls

            yield PipelineStageResult(
                stage="act", status=StageStatus.COMPLETED,
                latency_ms=round(latency, 1), output=act_output, model_id=act_model,
            )
        except Exception as e:
            latency = (time.monotonic() - t0) * 1000
            logger.exception("act failed")
            yield PipelineStageResult(
                stage="act", status=StageStatus.ERROR,
                latency_ms=round(latency, 1), error=str(e), model_id=act_model,
            )
            return

        # --- VERIFY ---
        verify_model = self._get_model_id(self.verify_adapter)
        yield PipelineStageResult(stage="verify", status=StageStatus.RUNNING, model_id=verify_model)
        t0 = time.monotonic()
        try:
            after_obs = await self.sim.get_observation()
            after_image = after_obs.image_base64 or image_base64
            ctx.after_image_base64 = after_image

            verification = await self._call_adapter(
                "verify", self.verify_adapter, image_base64, task,
                after_image=after_image,
                pipeline_context=ctx.to_dict(),
            )
            latency = (time.monotonic() - t0) * 1000
            yield PipelineStageResult(
                stage="verify", status=StageStatus.COMPLETED,
                latency_ms=round(latency, 1), output=_to_dict(verification), model_id=verify_model,
            )
        except Exception as e:
            latency = (time.monotonic() - t0) * 1000
            logger.exception("verify failed")
            yield PipelineStageResult(
                stage="verify", status=StageStatus.ERROR,
                latency_ms=round(latency, 1), error=str(e), model_id=verify_model,
            )
