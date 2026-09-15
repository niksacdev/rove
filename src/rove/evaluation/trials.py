"""Standalone structured trials share the campaign worker and ordinary trial history."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from rove.benchmarks.models import CampaignSpec
from rove.benchmarks.runner import run_attempt, trial_status
from rove.evaluation.executors import executor_for, prepare_evaluation
from rove.evaluation.models import EvaluationCase, EvaluationConfig
from rove.trials.store import TrialStore


async def run_evaluation_trials(
    *,
    root: Path,
    case: EvaluationCase,
    config: EvaluationConfig,
    strategy_ids: list[str],
    execution: str,
    seed: int = 0,
    timeout_s: float = 120,
    on_trial_created=None,
) -> dict:
    spec = CampaignSpec(
        name="Standalone evaluation",
        suite_version="standalone",
        revision="1",
        tasks=[case],
        strategies=strategy_ids,
        execution=execution,
        grading="executor_assessment",
        seeds=[seed],
        ks=[1],
        timeout_s=timeout_s,
    )
    prepared = prepare_evaluation(spec, config)
    executor, store = executor_for(prepared), TrialStore(root)
    identities, results = {}, []
    for sid in strategy_ids:
        executor.validate(prepared)
        task_snapshot, frozen, worker_task = executor.prepare_trial(prepared, case, sid, store)
        trial_id = store.begin(
            source="quick",
            task=task_snapshot,
            strategy={"id": sid, **config.strategies[sid]},
            config={**frozen, "runtime": prepared["runtime"], "timeout_s": timeout_s},
            seed=seed,
        )
        identities[sid] = trial_id
        if on_trial_created is not None:
            on_trial_created(sid, trial_id)
        started = time.monotonic()
        try:
            result = await run_attempt(
                {
                    "config": prepared["config"],
                    "task": worker_task,
                    "strategy_id": sid,
                    "seed": seed,
                    "trial_id": trial_id,
                    "trial_root": str(root.resolve()),
                    "execution": execution,
                    "executor_identity": prepared["executor_identity"],
                },
                timeout_s,
            )
            executor.validate(prepared)
        except asyncio.CancelledError:
            store.finish(trial_id, status="cancelled")
            raise
        except Exception:
            result = {
                "outcome": "unknown",
                "execution": "error",
                "reason": "Evaluation could not complete",
            }
        except BaseException:
            store.finish(trial_id, status="interrupted")
            raise
        result.update(
            strategy_id=sid, trial_id=trial_id, attempt_wall_ms=(time.monotonic() - started) * 1000
        )
        store.finish(trial_id, status=trial_status(result["execution"]), result=result)
        results.append(result)
    return {"trial_ids": identities, "results": results}
