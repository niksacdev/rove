"""Evaluation pipeline — deterministic perceive → plan → act → verify."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from rove.adapters.protocols import (
    AgentAdapter,
    PolicyAdapter,
    SimAdapter,
    StageAdapter,
)
from rove.adapters.registry import AdapterRegistry
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

# Map stage names to their Pydantic model classes for AgentAdapter dict→model conversion
_STAGE_MODELS: dict[str, type] = {
    "perceive": SceneAnalysis,
    "plan": TaskPlan,
    "act": ActionPrediction,
    "verify": VerificationResult,
}


class EvaluationPipeline:
    """Runs the 4-stage pipeline: perceive → plan → act → verify.

    Each stage has its own adapter — they can be the same model or different models.
    Supports VLM, VLA (Policy), and Agent adapters.
    """

    def __init__(
        self,
        perceive_adapter: StageAdapter | None = None,
        plan_adapter: StageAdapter | None = None,
        act_adapter: PolicyAdapter | AgentAdapter | None = None,
        verify_adapter: StageAdapter | None = None,
        sim: SimAdapter | None = None,
        registry: AdapterRegistry | None = None,
    ):
        self.perceive_adapter = perceive_adapter
        self.plan_adapter = plan_adapter
        self.act_adapter = act_adapter
        self.verify_adapter = verify_adapter
        self.sim = sim
        self._registry = registry

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
            # Serialize Pydantic objects in context for agent adapters
            agent_context = (
                {k: v.model_dump() if hasattr(v, "model_dump") else v for k, v in context.items()}
                if context
                else None
            )
            raw = await adapter.run_stage(stage, image_base64, task, agent_context)
            model_cls = _STAGE_MODELS.get(stage)
            if model_cls is None:
                raise ValueError(f"Unknown stage: {stage}")
            return model_cls.model_validate(raw)

        # Type-specific calls for VLM/Policy adapters
        if stage == "perceive":
            return await adapter.analyze_scene(image_base64, task)
        elif stage == "plan":
            scene = context.get("scene")
            return await adapter.plan_task(image_base64, task, scene)
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

    async def run_trial(
        self,
        task: str,
        image_base64: str,
        eval_id: str | None = None,
    ) -> AsyncGenerator[PipelineStageResult, None]:
        """Run a trial, yielding stage results as they complete.

        Stages with None adapters are skipped. verify is always required.
        """
        eval_id = eval_id or str(uuid.uuid4())
        ctx = PipelineContext(task=task, image_base64=image_base64)
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
                )
                ctx.plan = plan
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
                    "sim_done": last_obs.done if last_obs else False,
                    "sim_success": last_obs.success if last_obs else False,
                }
                if action_pred.action_type == "trajectory":
                    act_output["actions_executed"] = len(action_pred.actions)
                elif action_pred.action_type == "tool_calls":
                    act_output["tool_calls"] = action_pred.tool_calls

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
        verify_model = self._get_model_id(self.verify_adapter)
        yield PipelineStageResult(stage="verify", status=StageStatus.RUNNING, model_id=verify_model)
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
            latency = (time.monotonic() - t0) * 1000
            yield PipelineStageResult(
                stage="verify",
                status=StageStatus.COMPLETED,
                latency_ms=round(latency, 1),
                output=verification.model_dump(),
                model_id=verify_model,
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
            )
