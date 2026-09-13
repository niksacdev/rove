"""Upload selected ABC exports through the same importer used by the CLI."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from rove.datasets.models import StrictModel
from rove.datasets.service import DatasetService
from rove.trials.store import MAX_ASSET_BYTES


class Options(StrictModel):
    episode_id: str = Field(min_length=1, max_length=160)
    source_revision: str = Field(min_length=1, max_length=160)
    split: Literal["train", "val", "unknown"] = "unknown"
    domain: Literal["real", "sim", "unknown"] = "unknown"
    frame_index: int = Field(default=0, ge=0, strict=True)
    camera: Literal["top", "left", "right", "combined"] = "top"


def create_abc_router(root: Path) -> APIRouter:
    router = APIRouter()

    async def process(metadata_file, states_file, video_file, options, preview_hash=None):
        from rove.api.datasets import errors
        from rove.datasets.abc import import_prepared, prepare_episode

        with errors():
            if len(options.encode()) > 4096:
                raise ValueError("ABC import options exceed 4 KiB")
            selected = Options.model_validate(json.loads(options)).model_dump()
            if preview_hash is not None and (
                len(preview_hash) != 64 or any(c not in "0123456789abcdef" for c in preview_hash)
            ):
                raise ValueError("Preview this episode before importing")
            # Client filenames never become paths. No archives, URLs or server paths are accepted.
            with TemporaryDirectory(prefix="rove-abc-upload-") as temporary:
                directory = Path(temporary)
                for upload, filename, limit in (
                    (metadata_file, "episode_metadata.json", 256 * 1024),
                    (states_file, "states_actions.bin", MAX_ASSET_BYTES),
                    (video_file, "combined_camera-images-rgb.mp4", MAX_ASSET_BYTES),
                ):
                    size = 0
                    with (directory / filename).open("wb") as target:
                        while chunk := await upload.read(1024 * 1024):
                            size += len(chunk)
                            if size > limit:
                                raise HTTPException(413, f"{filename} exceeds its upload limit")
                            target.write(chunk)
                prepared = await run_in_threadpool(prepare_episode, directory, **selected)
                if preview_hash is None:
                    return prepared.preview()
                # A source mismatch must not create any durable records.
                if prepared.fingerprint != preview_hash:
                    raise ValueError("The episode or selection changed; preview it again")
                return await run_in_threadpool(
                    import_prepared,
                    DatasetService(root),
                    prepared,
                    expected_preview_hash=preview_hash,
                )

    @router.post("/api/cases/import/abc/preview")
    async def preview(
        metadata_file: UploadFile = File(...),
        states_file: UploadFile = File(...),
        video_file: UploadFile = File(...),
        options: str = Form(...),
    ):
        return await process(metadata_file, states_file, video_file, options)

    @router.post("/api/cases/import/abc", status_code=201)
    async def import_episode(
        metadata_file: UploadFile = File(...),
        states_file: UploadFile = File(...),
        video_file: UploadFile = File(...),
        options: str = Form(...),
        preview_hash: str = Form(..., min_length=64, max_length=64),
    ):
        return await process(metadata_file, states_file, video_file, options, preview_hash)

    return router
