"""Evaluation pipeline — deterministic perceive → plan → act → verify."""

from __future__ import annotations

import logging
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
    """Runs the 4-stage pipeline: perceive → plan → act → verify.

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
        forward_kinematics: bool = False,
        urdf_path: str | None = None,
    ):
        self.perceive_adapter = perceive_adapter
        self.plan_adapter = plan_adapter
        self.act_adapter = act_adapter
        self.verify_adapter = verify_adapter
        self.sim = sim
        self._registry = registry
        self._forward_kinematics = forward_kinematics
        self._urdf_path = urdf_path

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

        # Type-specific calls for VLM/VLA adapters
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

    @staticmethod
    def _apply_fk_sanity_checks(
        verification: VerificationResult, fk_analysis: dict
    ) -> VerificationResult:
        """Override VLM plausibility scores when FK data objectively contradicts them.

        FK provides ground-truth physics checks (joint limits, collisions,
        displacement) that should override subjective VLM scores.
        """
        if not verification.action_plausibility:
            return verification

        plaus = verification.action_plausibility.model_copy()
        overrides: list[str] = []
        fk_failures = 0

        # Joint limits violation
        if not fk_analysis.get("joint_limits_ok", True):
            if plaus.bounds_check:
                plaus.bounds_check = False
                overrides.append("bounds_check=False: joint limits exceeded")
            fk_failures += 1

        # Self-collision
        if fk_analysis.get("self_collision", False):
            if plaus.bounds_check:
                plaus.bounds_check = False
                overrides.append("bounds_check=False: self-collision detected")
            fk_failures += 1

        # Excessive displacement
        total_disp = fk_analysis.get("total_displacement_m", 0.0)
        if total_disp > 2.0:
            if plaus.smoothness:
                plaus.smoothness = False
                overrides.append(
                    f"smoothness=False: displacement {total_disp:.1f}m exceeds 2.0m limit"
                )
            fk_failures += 1

        # Very low smoothness score
        smoothness_score = fk_analysis.get("smoothness_score", 1.0)
        if smoothness_score < 0.1:
            fk_failures += 1

        # Cap plan_alignment if any FK failure
        if fk_failures >= 1 and plaus.plan_alignment > 0.3:
            overrides.append(f"plan_alignment capped 0.3: {fk_failures} FK failure(s)")
            plaus.plan_alignment = 0.3

        # Cap overall confidence if 2+ FK failures
        new_confidence = verification.confidence
        if fk_failures >= 2 and verification.confidence > 0.4:
            overrides.append(f"confidence capped 0.4: {fk_failures} FK failures")
            new_confidence = 0.4

        if overrides:
            override_text = " | ".join(f"[FK override: {o}]" for o in overrides)
            plaus.reasoning = (
                f"{plaus.reasoning} {override_text}" if plaus.reasoning else override_text
            )

        return verification.model_copy(
            update={
                "action_plausibility": plaus,
                "confidence": new_confidence,
            }
        )

    async def run_trial(
        self,
        task: str,
        image_base64: str,
        eval_id: str | None = None,
        example: ExampleData | None = None,
    ) -> AsyncGenerator[PipelineStageResult, None]:
        """Run a trial, yielding stage results as they complete.

        Stages with None adapters are skipped. verify is always required.
        """
        eval_id = eval_id or str(uuid.uuid4())
        ground_truth = example.ground_truth if example else None
        ctx = PipelineContext(task=task, image_base64=image_base64, ground_truth=ground_truth)

        # Seed proprioception from example data (dataset mode, no sim)
        if example and example.extras.get("proprioception"):
            ctx.proprioception = example.extras["proprioception"]

        # Auto-detect URDF from example robot type if FK enabled but no URDF provided
        if self._forward_kinematics and not self._urdf_path and example:
            robot = example.extras.get("robot")
            if robot:
                from pathlib import Path

                data_dir = Path(__file__).parent.parent.parent.parent / "data"
                candidate = data_dir / "urdf" / robot / f"{robot}.urdf"
                if candidate.exists():
                    self._urdf_path = str(candidate)
                    logger.info("Auto-detected URDF for robot '%s': %s", robot, candidate)

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
                if action_pred.action_type == "trajectory" and action_pred.actions:
                    for action in action_pred.actions:
                        last_obs = await self.sim.step(action)
                        if last_obs.done:
                            break
                elif action_pred.action_type == "tool_calls":
                    last_obs = await self.sim.get_observation()

                # Compute FK before yielding act result so it's included in output
                fk_skipped_reason: str | None = None
                if self._forward_kinematics:
                    if not self._urdf_path:
                        fk_skipped_reason = (
                            "No URDF provided — upload a URDF or use a dataset with robot metadata"
                        )
                        logger.warning("FK enabled but no URDF path — skipping FK")
                    elif not action_pred.actions:
                        fk_skipped_reason = "No trajectory actions to analyze"
                        logger.warning("FK enabled but no actions — skipping FK")
                    else:
                        try:
                            from rove.adapters.fk_mujoco import compute_fk

                            fk = compute_fk(
                                self._urdf_path,
                                action_pred.actions,
                                initial_qpos=ctx.proprioception or None,
                            )
                            ctx.fk_analysis = fk
                            logger.info("FK analysis: %d steps", fk.get("steps_analyzed", 0))
                        except ImportError:
                            fk_skipped_reason = "MuJoCo not installed"
                            logger.warning("MuJoCo not installed — skipping FK")
                        except Exception as fk_err:
                            fk_skipped_reason = f"FK computation failed: {fk_err}"
                            logger.warning("FK computation failed: %s", fk_err)

                latency = (time.monotonic() - t0) * 1000

                act_output: dict[str, Any] = action_pred.model_dump()
                act_output["sim_done"] = last_obs.done if last_obs else False
                act_output["sim_success"] = last_obs.success if last_obs else False
                act_output["sim_is_mock"] = self.sim is not None and getattr(
                    self.sim, "model_id", ""
                ).startswith("mock")
                if action_pred.action_type == "trajectory":
                    act_output["actions_executed"] = len(action_pred.actions)
                if ctx.fk_analysis:
                    act_output["fk_analysis"] = ctx.fk_analysis
                if fk_skipped_reason:
                    act_output["fk_skipped"] = fk_skipped_reason

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
            if ctx.fk_analysis:
                verification = self._apply_fk_sanity_checks(verification, ctx.fk_analysis)
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
