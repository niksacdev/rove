"""Optional read-only AI drafts and campaign interpretations, never execution controls."""

from pathlib import Path

from fastapi import APIRouter, HTTPException

from rove.benchmarks.insights import (
    CampaignInsights,
    DraftResponse,
    MetricsDraftRequest,
    SummaryResponse,
)


def create_insights_router(root: Path, **dependencies):
    router = APIRouter(prefix="/api/campaigns")
    insights = CampaignInsights(root, **dependencies)

    @router.post("/metrics-draft", response_model=DraftResponse)
    async def metrics_draft(request: MetricsDraftRequest):
        try:
            return await insights.draft(request)
        except KeyError as error:
            raise HTTPException(404, "Selected case or dataset revision not found") from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.post("/{campaign_id}/outcome-summary", response_model=SummaryResponse)
    async def outcome_summary(campaign_id: str):
        try:
            return await insights.summary(campaign_id)
        except KeyError as error:
            raise HTTPException(404, "Campaign not found") from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    return router
