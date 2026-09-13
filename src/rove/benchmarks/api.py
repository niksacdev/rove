"""Local campaign API; shares the existing app's host/origin protections."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response

from rove.benchmarks.metrics import summarize
from rove.benchmarks.models import CampaignSpec
from rove.benchmarks.report import public_campaign, report_data, to_csv, to_html
from rove.benchmarks.runner import prepare, run_campaign
from rove.benchmarks.store import CampaignStore
from rove.models.config import load_config


def create_router(root: Path) -> APIRouter:
    running: dict[str, asyncio.Task] = {}

    @asynccontextmanager
    async def lifespan(_app):
        yield
        tasks = list(running.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    router = APIRouter(prefix="/api/campaigns")

    def store() -> CampaignStore:
        return CampaignStore(root)

    def get(campaign_id: str) -> dict:
        try:
            return store().get(campaign_id)
        except KeyError as error:
            raise HTTPException(404, "Campaign not found") from error

    async def execute(campaign_id: str):
        try:
            await run_campaign(store(), campaign_id)
        except ValueError:
            # Includes an incompatible runtime or another process owning the campaign.
            if store().get(campaign_id)["status"] == "pending":
                store().status(campaign_id, "interrupted")
        finally:
            running.pop(campaign_id, None)

    def launch(campaign_id: str):
        if campaign_id in running:
            raise HTTPException(409, "Campaign is already running")
        running[campaign_id] = asyncio.create_task(execute(campaign_id))

    @router.get("")
    async def list_campaigns():
        return [
            {
                "id": c["id"],
                "name": c["spec"]["name"],
                "revision": c["spec"]["revision"],
                "created_at": c["created_at"],
                "status": c["status"],
            }
            for c in store().list()
        ]

    @router.post("", status_code=202)
    async def create(spec: CampaignSpec):
        try:
            payload = prepare(spec, load_config())
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        campaign_id = store().create(payload)
        launch(campaign_id)
        return {"id": campaign_id, "planned_trials": spec.planned_trials}

    @router.get("/{campaign_id}")
    async def detail(campaign_id: str):
        from rove.benchmarks.workflow import assessed_trials

        campaign = get(campaign_id)
        trials, assessments = assessed_trials(root, campaign, store().trials(campaign_id))
        return {
            "campaign": public_campaign(campaign),
            "summary": summarize(campaign, trials),
            "assessments": assessments,
        }

    @router.post("/{campaign_id}/cancel")
    async def cancel(campaign_id: str):
        campaign = get(campaign_id)
        if campaign["status"] == "completed":
            raise HTTPException(409, "Campaign already completed")
        if task := running.get(campaign_id):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        else:
            store().status(campaign_id, "cancelling")
        return {"status": store().get(campaign_id)["status"]}

    @router.post("/{campaign_id}/resume", status_code=202)
    async def resume(campaign_id: str):
        from rove.benchmarks.runner import runtime_fingerprint

        campaign = get(campaign_id)
        if campaign["runtime"] != runtime_fingerprint():
            raise HTTPException(409, "Runtime changed; create a new campaign")
        launch(campaign_id)
        return {"id": campaign_id}

    @router.get("/{campaign_id}/report")
    async def report(campaign_id: str, format: str = "html"):
        from rove.benchmarks.workflow import assessed_trials

        campaign = get(campaign_id)
        db = store()
        history = [(c, assessed_trials(root, c, db.trials(c["id"]))[0]) for c in db.list()]
        trials, assessments = assessed_trials(root, campaign, db.trials(campaign_id))
        data = report_data(campaign, trials, history)
        data["assessments"] = assessments
        headers = {"Content-Disposition": f'attachment; filename="rove-{campaign_id}.{format}"'}
        if format == "json":
            return Response(
                json.dumps(data, allow_nan=False), media_type="application/json", headers=headers
            )
        if format == "csv":
            return Response(to_csv(data), media_type="text/csv", headers=headers)
        if format == "html":
            return HTMLResponse(await asyncio.to_thread(to_html, data))
        raise HTTPException(422, "Use html, json or csv")

    from rove.api.assistant import create_assistant_router
    from rove.api.datasets import create_dataset_router
    from rove.api.insights import create_insights_router
    from rove.api.trajectory import create_trajectory_router

    combined = APIRouter(lifespan=lifespan)

    def ensure_launched(campaign_id: str):
        if campaign_id not in running:
            launch(campaign_id)

    combined.include_router(create_dataset_router(root, ensure_launched))
    combined.include_router(create_assistant_router(root, ensure_launched))
    combined.include_router(create_insights_router(root))
    combined.include_router(create_trajectory_router(root))
    combined.include_router(router)
    return combined
