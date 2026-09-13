"""Read-only connected campaign iteration timeline."""

from fastapi import APIRouter, HTTPException

from rove.benchmarks.trajectory import campaign_timeline


def create_trajectory_router(root):
    router = APIRouter(prefix="/api/campaigns")

    @router.get("/{campaign_id}/timeline")
    async def timeline(campaign_id: str):
        try:
            return campaign_timeline(root, campaign_id)
        except KeyError as error:
            raise HTTPException(404, "Campaign not found") from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    return router
