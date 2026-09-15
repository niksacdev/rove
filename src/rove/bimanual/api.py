"""Bounded browser controls for host-configured ABC evaluations.

The browser selects registered strategies and episode budgets. Interpreter paths,
checkpoints and dataset provenance stay in the operator's local profile.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from rove.benchmarks.store import CampaignStore
from rove.bimanual.config import build_spec, read_profile, validate_profile
from rove.evaluation.executors import prepare_evaluation
from rove.evaluation.trials import run_evaluation_trials

Seed = Annotated[StrictInt, Field(ge=0, le=2**31 - 1)]


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    strategy_ids: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(
        min_length=1, max_length=20
    )
    reset_seeds: list[Seed] = Field(min_length=1, max_length=100)
    policy_seeds: list[Seed] = Field(min_length=1, max_length=100)
    max_steps: Annotated[StrictInt, Field(ge=1, le=3540)] = 3540

    @model_validator(mode="after")
    def bounded_plan(self):
        if not self.name.strip():
            raise ValueError("Give this comparison a name")
        for values in (self.strategy_ids, self.reset_seeds, self.policy_seeds):
            if len(values) != len(set(values)):
                raise ValueError("Strategies and seeds must not contain duplicates")
        if len(self.strategy_ids) * len(self.reset_seeds) * len(self.policy_seeds) > 2000:
            raise ValueError("Use at most 2,000 trials per comparison")
        return self


class LaunchRequest(ComparisonRequest):
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def create_bimanual_router(root: Path, launch_campaign: Callable[[str], None]) -> APIRouter:
    jobs: dict[str, dict] = {}
    running: dict[str, asyncio.Task] = {}

    @asynccontextmanager
    async def lifespan(_app):
        yield
        tasks = list(running.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    router = APIRouter(prefix="/api/bimanual", lifespan=lifespan)

    def profile():
        try:
            return read_profile(root)
        except (ValueError, OSError) as error:
            raise HTTPException(
                409, "Configure a local ABC profile with the ROVE CLI first"
            ) from error

    async def prepare_request(request: ComparisonRequest):
        config = profile()
        try:
            spec, frozen = build_spec(
                config,
                name=request.name.strip(),
                strategy_ids=request.strategy_ids,
                reset_seeds=request.reset_seeds,
                policy_seeds=request.policy_seeds,
                max_steps=request.max_steps,
            )
            readiness = await asyncio.to_thread(validate_profile, frozen, verify_assets=True)
        except (ValueError, OSError) as error:
            raise HTTPException(422, str(error)) from error
        selected = {row["id"]: row for row in readiness.get("strategies", [])}
        issues = list(readiness.get("issues", []))
        for sid in request.strategy_ids:
            row = selected.get(sid)
            if not row or not row.get("ready"):
                issues.extend(
                    (row.get("issues") if row else None) or [f"Strategy {sid} is unavailable"]
                )
        token = hashlib.sha256(
            json.dumps(
                {"spec": spec.model_dump(mode="json"), "config": frozen.model_dump(mode="json")},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        preview = {
            "preview_hash": token,
            "name": spec.name,
            "planned_trials": spec.planned_trials,
            "cases": len(spec.tasks),
            "strategies": request.strategy_ids,
            "attempts_per_case": len(spec.seeds),
            "max_steps": request.max_steps,
            "ready": not issues,
            "issues": list(dict.fromkeys(issues)),
            "success_criterion": frozen.grading,
        }
        return spec, frozen, preview

    async def reviewed(request: LaunchRequest):
        spec, frozen, preview = await prepare_request(request)
        if not secrets.compare_digest(request.preview_hash, preview["preview_hash"]):
            raise HTTPException(
                409, "Configuration changed. Review the comparison again before running"
            )
        if not preview["ready"]:
            raise HTTPException(
                409, {"message": "Complete setup before running", "issues": preview["issues"]}
            )
        return spec, frozen

    @router.get("/config")
    async def configuration():
        try:
            config = read_profile(root)
            readiness = await asyncio.to_thread(validate_profile, config, verify_assets=True)
        except (ValueError, OSError):
            return {"configured": False, "ready": False, "strategies": [], "issues": []}
        return {
            "configured": True,
            "ready": readiness.get("ready", False),
            "strategies": [
                {key: row[key] for key in ("id", "label", "kind", "ready", "issues") if key in row}
                for row in readiness.get("strategies", [])
            ],
            "issues": readiness.get("issues", []),
            "task": "Put plastic bottles in the bin",
            "defaults": {"reset_seeds": [0, 1, 2], "policy_seeds": [0, 1, 2], "max_steps": 3540},
        }

    @router.post("/preview")
    async def preview(request: ComparisonRequest):
        return (await prepare_request(request))[2]

    @router.post("/campaigns", status_code=202)
    async def campaign(request: LaunchRequest):
        spec, config = await reviewed(request)
        if running:
            raise HTTPException(409, "Another standalone ABC comparison is running")
        try:
            payload = await asyncio.to_thread(prepare_evaluation, spec, config)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        campaign_id = CampaignStore(root).create(payload)
        launch_campaign(campaign_id)
        return {"id": campaign_id, "planned_trials": spec.planned_trials}

    async def execute(job_id: str, spec, config):
        job = jobs[job_id]

        def on_created(strategy_id: str, trial_id: str):
            job["trial_ids"][strategy_id] = trial_id

        try:
            result = await run_evaluation_trials(
                root=root,
                case=spec.tasks[0],
                config=config,
                strategy_ids=spec.strategies,
                execution=spec.execution,
                seed=spec.seeds[0],
                timeout_s=spec.timeout_s,
                on_trial_created=on_created,
            )
            job["trial_ids"].update(result["trial_ids"])
            job["status"] = "completed"
        except asyncio.CancelledError:
            job["status"] = "cancelled"
            raise
        except Exception:
            job["status"] = "error"
            job["message"] = "Comparison interrupted. Inspect any recorded trials for details."
        finally:
            running.pop(job_id, None)

    @router.post("/trials", status_code=202)
    async def trials(request: LaunchRequest):
        if len(request.reset_seeds) != 1 or len(request.policy_seeds) != 1:
            raise HTTPException(
                422, "A standalone comparison uses one scene seed and one model seed"
            )
        if running:
            raise HTTPException(409, "Another standalone ABC comparison is running")
        spec, config = await reviewed(request)
        if running:
            raise HTTPException(409, "Another standalone ABC comparison is running")
        if len(jobs) >= 100:
            jobs.pop(next(key for key in jobs if key not in running))
        job_id = secrets.token_hex(16)
        jobs[job_id] = {
            "id": job_id,
            "status": "running",
            "trial_ids": {},
            "strategy_ids": spec.strategies,
            "tasks": [{"id": task.id, "task": task.task} for task in spec.tasks],
            "seeds": spec.seeds,
        }
        running[job_id] = asyncio.create_task(execute(job_id, spec, config))
        return {**jobs[job_id], "planned_trials": spec.planned_trials}

    def job(job_id: str):
        if job_id not in jobs:
            raise HTTPException(
                404, "Comparison session not found. Recorded trials remain in Trials."
            )
        return jobs[job_id]

    @router.get("/trials/{job_id}")
    async def trial_job(job_id: str):
        return job(job_id)

    @router.post("/trials/{job_id}/cancel")
    async def cancel(job_id: str):
        value = job(job_id)
        if task := running.get(job_id):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            running.pop(job_id, None)
            if value["status"] == "running":
                value["status"] = "cancelled"
        return value

    return router
