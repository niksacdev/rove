"""Read-only durable trial history and bounded private evidence retrieval."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from rove.trials.store import TrialStore


def create_trial_router(store: Callable[[], TrialStore]) -> APIRouter:
    router = APIRouter()

    def with_assessments(db: TrialStore, rows: list[dict]) -> list[dict]:
        from rove.benchmarks.store import CampaignStore
        from rove.benchmarks.workflow import assessed_trials

        campaigns = CampaignStore(db.root)
        projections = {}
        for row in rows:
            identity = row.get("campaign_id")
            if not identity or identity in projections:
                continue
            try:
                campaign = campaigns.get(identity)
            except KeyError:
                continue
            if not campaign.get("contract"):
                continue
            projected, metadata = assessed_trials(db.root, campaign, campaigns.trials(identity))
            projections[identity] = ({r.get("trial_id"): r for r in projected}, metadata)
        result = []
        for row in rows:
            if projection := projections.get(row.get("campaign_id")):
                outcomes, metadata = projection
                assessed = outcomes.get(row["id"], {})
                row = {
                    **row,
                    "assessment": {
                        **metadata,
                        "outcome": assessed.get("outcome", "unknown"),
                        "review_ids": assessed.get("assessment_ids", []),
                    },
                }
            result.append(row)
        return result

    def get(trial_id: str) -> dict:
        try:
            db = store()
            return with_assessments(db, [db.get(trial_id)])[0]
        except (KeyError, ValueError) as error:
            raise HTTPException(404, "Trial not found") from error

    @router.get("/api/trials")
    async def history(
        limit: int = Query(25, ge=1, le=100),
        offset: int = Query(0, ge=0),
        source: Literal["quick", "campaign", "legacy"] | None = None,
        case_revision_id: str | None = Query(None, max_length=128),
    ):
        db = store()
        return {
            "trials": with_assessments(
                db,
                db.list(
                    limit=limit, offset=offset, source=source, case_revision_id=case_revision_id
                ),
            ),
            "total": db.count(source=source, case_revision_id=case_revision_id),
            "limit": limit,
            "offset": offset,
        }

    @router.get("/api/trials/{trial_id}")
    async def detail(trial_id: str):
        return get(trial_id)

    @router.get("/api/trials/{trial_id}/events")
    async def events(
        trial_id: str, limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)
    ):
        trial = get(trial_id)
        return {
            "events": store().events(trial_id, limit=limit, offset=offset),
            "total": trial["event_count"],
            "limit": limit,
            "offset": offset,
        }

    @router.get("/api/trial-assets/{digest}")
    async def asset(digest: str):
        try:
            path = store().asset_path(digest)
            if not path.is_file():
                raise KeyError(digest)
        except (KeyError, ValueError, FileNotFoundError) as error:
            raise HTTPException(404, "Evidence asset not found") from error
        with path.open("rb") as stream:
            header = stream.read(12)
        media_type = (
            "image/png"
            if header.startswith(b"\x89PNG\r\n\x1a\n")
            else "image/jpeg"
            if header.startswith(b"\xff\xd8\xff")
            else "application/octet-stream"
        )
        # Sniff safe image signatures; never render HTML/SVG in the application origin.
        return FileResponse(
            path,
            media_type=media_type,
            filename=digest if media_type == "application/octet-stream" else None,
            headers={"X-Content-Type-Options": "nosniff"},
        )

    return router
