"""Run manager — execute multiple strategies concurrently with bounded parallelism."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from rove.adapters.registry import AdapterRegistry
from rove.models import ExampleData, StageStatus, Strategy
from rove.orchestrator.failure_attribution import attribute_failure
from rove.orchestrator.pipeline import EvaluationPipeline
from rove.orchestrator.recording import event_sink, trial_span
from rove.trials.store import TrialStore

logger = logging.getLogger(__name__)


class RunManager:
    """Execute multiple strategies concurrently with bounded parallelism."""

    def __init__(
        self,
        registry: AdapterRegistry,
        max_concurrent: int = 5,
        urdf_path: str | None = None,
    ):
        self.registry = registry
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._urdf_path = urdf_path

    async def run_strategies(
        self,
        strategies: list[Strategy],
        task: str,
        image_base64: str,
        on_event: Callable[[str, str, dict], Awaitable[None]],
        example: ExampleData | None = None,
        trial_contexts: dict[str, tuple[TrialStore, str]] | None = None,
    ) -> list[dict]:
        """Run all strategies concurrently. on_event(strategy_id, event_type, data)."""
        results: list[dict] = []

        async with asyncio.TaskGroup() as tg:
            for strategy in strategies:
                tg.create_task(
                    self._recorded_strategy(
                        strategy,
                        task,
                        image_base64,
                        results,
                        on_event,
                        example,
                        (trial_contexts or {}).get(strategy.id),
                    )
                )

        return results

    async def _recorded_strategy(self, strategy, task, image, results, on_event, example, trial):
        token = None
        if trial is not None:
            store, trial_id = trial

            def sink(event):
                store.append_event(trial_id, event)

            token = event_sink.set(sink)

        async def deliver(strategy_id, event_type, data):
            if trial is not None:
                sink({"event_type": event_type, "stage": data.get("stage"), "data": data})
            await on_event(strategy_id, event_type, data)

        try:
            with trial_span(trial[1] if trial else None):
                await self._run_strategy(strategy, task, image, results, deliver, example)
            if trial is not None and store.get(trial_id)["source"] == "quick":
                result = next(item for item in results if item["strategy_id"] == strategy.id)
                failed = result.get("error") or any(
                    s.get("status") == "error" for s in result.get("stages", [])
                )
                store.finish(trial_id, status="error" if failed else "completed", result=result)
        finally:
            if token is not None:
                event_sink.reset(token)

    async def _run_strategy(
        self,
        strategy: Strategy,
        task: str,
        image_base64: str,
        results: list[dict],
        on_event: Callable[[str, str, dict], Awaitable[None]],
        example: ExampleData | None = None,
    ) -> None:
        await on_event(
            strategy.id,
            "strategy_queued",
            {
                "strategy_id": strategy.id,
                "display_name": strategy.display_name,
            },
        )
        async with self._semaphore:
            await on_event(
                strategy.id,
                "strategy_started",
                {
                    "strategy_id": strategy.id,
                    "display_name": strategy.display_name,
                    "pipeline_mode": strategy.pipeline_mode,
                },
            )
            try:
                pipeline = self._build_pipeline(strategy)
                stages: list[dict] = []

                async for stage_result in pipeline.run_trial(
                    task,
                    image_base64,
                    example=example,
                ):
                    stage_dict = stage_result.model_dump()
                    stage_dict["strategy_id"] = strategy.id
                    await on_event(strategy.id, "stage", stage_dict)
                    if stage_result.status in (StageStatus.COMPLETED, StageStatus.ERROR):
                        stages.append(stage_dict)

                total_latency = sum(s.get("latency_ms", 0) for s in stages)
                verify_stage = next((s for s in stages if s["stage"] == "verify"), None)
                success = (
                    verify_stage["output"].get("success", False)
                    if verify_stage and verify_stage.get("output")
                    else False
                )
                confidence = (
                    verify_stage["output"].get("confidence", 0.0)
                    if verify_stage and verify_stage.get("output")
                    else 0.0
                )

                verdict_valid = bool(
                    verify_stage and (verify_stage.get("output") or {}).get("verdict_valid", False)
                ) and not any(s["status"] == "error" for s in stages)
                failure_stage, failure_category = attribute_failure(stages, success)
                if not verdict_valid:
                    failure_category = "unknown"

                result = {
                    "strategy_id": strategy.id,
                    "display_name": strategy.display_name,
                    "success": success,
                    "verdict_valid": verdict_valid,
                    "outcome": ("pass" if success else "fail") if verdict_valid else "unknown",
                    "confidence": confidence,
                    "total_latency_ms": round(total_latency, 1),
                    "failure_stage": failure_stage,
                    "failure_category": failure_category,
                    "stages": stages,
                    "models": {
                        k: v
                        for k, v in {
                            "perceive": strategy.perceive,
                            "plan": strategy.plan,
                            "act": strategy.act,
                            "verify": strategy.verify,
                            "sim": strategy.sim,
                        }.items()
                        if v is not None
                    },
                }
                await on_event(strategy.id, "strategy_complete", result)
                results.append(result)

            except Exception as e:
                logger.exception(f"Strategy {strategy.id} failed")
                error_result = {
                    "strategy_id": strategy.id,
                    "display_name": strategy.display_name,
                    "success": False,
                    "error": str(e),
                    "verdict_valid": False,
                    "outcome": "unknown",
                    "stages": [],
                }
                await on_event(strategy.id, "strategy_error", error_result)
                results.append(error_result)

    def _build_pipeline(self, strategy: Strategy) -> EvaluationPipeline:
        """Build pipeline for a strategy. Creates fresh sim instances for isolation."""
        from rove.models import PipelineStage

        perceive = (
            self.registry.get_adapter_for_stage(PipelineStage.PERCEIVE, strategy.perceive)
            if strategy.perceive
            else None
        )
        plan = (
            self.registry.get_adapter_for_stage(PipelineStage.PLAN, strategy.plan)
            if strategy.plan
            else None
        )
        act = (
            self.registry.get_adapter_for_stage(PipelineStage.ACT, strategy.act)
            if strategy.act
            else None
        )
        verify = self.registry.get_adapter_for_stage(PipelineStage.VERIFY, strategy.verify)
        sim = self.registry.create_sim(strategy.sim) if strategy.act and strategy.sim else None

        return EvaluationPipeline(
            perceive_adapter=perceive,
            plan_adapter=plan,
            act_adapter=act,
            verify_adapter=verify,
            sim=sim,
            registry=self.registry,
            compute_dynamics=strategy.compute_dynamics,
            urdf_path=self._urdf_path,
            pipeline_mode=strategy.pipeline_mode,
            verify_mode=strategy.verify_mode,
            verification_checks=strategy.verification_checks,
            stage_timeouts=strategy.stage_timeouts,
        )
