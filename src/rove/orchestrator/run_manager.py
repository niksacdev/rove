"""Run manager — execute multiple strategies concurrently with bounded parallelism."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from rove.adapters.registry import AdapterRegistry
from rove.models import ExampleData, StageStatus, Strategy
from rove.orchestrator.pipeline import EvaluationPipeline

logger = logging.getLogger(__name__)


class RunManager:
    """Execute multiple strategies concurrently with bounded parallelism."""

    def __init__(self, registry: AdapterRegistry, max_concurrent: int = 5):
        self.registry = registry
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def run_strategies(
        self,
        strategies: list[Strategy],
        task: str,
        image_base64: str,
        on_event: Callable[[str, str, dict], Awaitable[None]],
        example: ExampleData | None = None,
    ) -> list[dict]:
        """Run all strategies concurrently. on_event(strategy_id, event_type, data)."""
        results: list[dict] = []

        async with asyncio.TaskGroup() as tg:
            for strategy in strategies:
                tg.create_task(
                    self._run_strategy(strategy, task, image_base64, results, on_event, example)
                )

        return results

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
                    verify_stage["output"]["success"]
                    if verify_stage and verify_stage.get("output")
                    else False
                )

                result = {
                    "strategy_id": strategy.id,
                    "display_name": strategy.display_name,
                    "success": success,
                    "total_latency_ms": round(total_latency, 1),
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
        sim = self.registry.create_sim(strategy.sim) if strategy.act else None

        return EvaluationPipeline(
            perceive_adapter=perceive,
            plan_adapter=plan,
            act_adapter=act,
            verify_adapter=verify,
            sim=sim,
            registry=self.registry,
        )
