"""Read-only durable trial history and bounded private evidence retrieval."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from rove.trials.store import TrialStore


def create_trial_router(store: Callable[[], TrialStore]) -> APIRouter:
    router = APIRouter()

    def get(trial_id: str) -> dict:
        try:
            return store().get(trial_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(404, "Trial not found") from error

    @router.get("/api/trials")
    async def history(
        limit: int = Query(25, ge=1, le=100),
        offset: int = Query(0, ge=0),
        source: Literal["quick", "campaign", "legacy"] | None = None,
    ):
        db = store()
        return {
            "trials": db.list(limit=limit, offset=offset, source=source),
            "total": db.count(source=source),
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
