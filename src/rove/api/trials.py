"""Read-only durable trial history and bounded private evidence retrieval."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from rove.trials.reading import with_assessments
from rove.trials.store import TrialStore


def create_trial_router(store: Callable[[], TrialStore]) -> APIRouter:
    router = APIRouter()

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

    @router.get("/api/exchange")
    async def export_exchange():
        import asyncio
        import tempfile
        import threading
        from pathlib import Path

        from starlette.background import BackgroundTask

        from rove.trials.exchange import export_bundle

        directory = tempfile.TemporaryDirectory(prefix="rove-exchange-")
        path = Path(directory.name) / "rove-exchange.zip"
        ownership_lock = threading.Lock()
        ownership = {"finished": False, "abandoned": False, "cleaned": False}

        def release_if_abandoned(**changes):
            # Cancelling to_thread does not stop its executor thread. Keep the
            # directory alive in that thread and hand cleanup off exactly once,
            # regardless of which side observes completion/cancellation first.
            with ownership_lock:
                ownership.update(changes)
                cleanup = (
                    ownership["finished"] and ownership["abandoned"] and not ownership["cleaned"]
                )
                if cleanup:
                    ownership["cleaned"] = True
            if cleanup:
                directory.cleanup()

        def build_export(root):
            try:
                return export_bundle(root, path)
            finally:
                release_if_abandoned(finished=True)

        try:
            await asyncio.to_thread(build_export, store().root)
        except asyncio.CancelledError:
            release_if_abandoned(abandoned=True)
            raise
        except (ValueError, OSError) as error:
            directory.cleanup()
            raise HTTPException(409, str(error)) from error
        except Exception:
            directory.cleanup()
            raise
        return FileResponse(
            path,
            media_type="application/zip",
            filename="rove-exchange.zip",
            background=BackgroundTask(directory.cleanup),
        )

    @router.post("/api/evidence-assets", status_code=201)
    async def upload_evidence(request: Request):
        from rove.trials.store import MAX_ASSET_BYTES

        media = request.headers.get("content-type", "application/octet-stream").split(";")[0]
        if media not in {
            "application/json",
            "application/octet-stream",
            "video/mp4",
            "image/png",
            "image/jpeg",
        }:
            raise HTTPException(415, "Unsupported evidence media type")
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_ASSET_BYTES:
                raise HTTPException(
                    413, "Evidence upload exceeds 64 MiB; upload bounded recording segments"
                )
        if not data:
            raise HTTPException(422, "Evidence asset is empty")
        return store().save_asset(bytes(data), media)

    @router.get("/api/trials/{trial_id}/trace")
    async def trace(trial_id: str):
        from rove.trials.traces import trial_trace

        get(trial_id)
        return trial_trace(store(), trial_id)

    @router.get("/api/trials/{trial_id}/evidence/{reference_id}")
    async def evidence_range(trial_id: str, reference_id: str):
        import json

        from rove.trials.evidence import verified_asset

        get(trial_id)
        db = store()
        ref = next((r for r in db.evidence(trial_id) if r["id"] == reference_id), None)
        if ref is None:
            raise HTTPException(404, "Evidence reference not found")
        if ref["availability"] != "available":
            raise HTTPException(409, "Recorded evidence is unavailable or changed")
        metadata, data = verified_asset(db, ref["asset_sha256"])
        selector = ref.get("selector")
        if selector and selector["unit"] != "byte":
            document = json.loads(data)
            records = document["records"]
            if selector["unit"] == "second":
                records = [
                    r
                    for r in records
                    if isinstance(r, dict)
                    and isinstance(r.get("timestamp_s"), float | int)
                    and selector["start"] <= r["timestamp_s"] < selector["end"]
                ]
            else:
                records = records[int(selector["start"]) : int(selector["end"])]
            return {"reference": ref, "records": records}
        if selector:
            data = data[int(selector["start"]) : int(selector["end"])]
        if metadata["media_type"] == "application/json" and not selector:
            try:
                return {"reference": ref, "content": json.loads(data)}
            except (ValueError, UnicodeDecodeError):
                pass
        return Response(
            data,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": 'attachment; filename="evidence.bin"',
                "X-Content-Type-Options": "nosniff",
            },
        )

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
