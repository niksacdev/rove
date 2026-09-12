"""Managed evidence identities and explicit, bounded recording selections."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    unit: Literal["byte", "sample", "frame", "second"]
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Evidence selections are nonempty half-open ranges")
        if self.unit != "second" and (not self.start.is_integer() or not self.end.is_integer()):
            raise ValueError("Byte, frame and sample offsets must be integers")
        return self


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    asset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: Literal["image", "video", "trajectory", "state", "tool_output", "other"] = "other"
    selector: Selection | None = None
    clock_id: str | None = Field(default=None, min_length=1, max_length=160)
    frame: str | None = Field(default=None, min_length=1, max_length=160)
    units: dict[str, str] = Field(default_factory=dict, max_length=50)


def verified_asset(store, digest: str) -> tuple[dict, bytes]:
    metadata = store.asset_metadata(digest)
    from rove.trials.store import MAX_ASSET_BYTES

    with store.asset_path(digest).open("rb") as stream:
        data = stream.read(MAX_ASSET_BYTES + 1)
    if len(data) != metadata["size_bytes"] or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Evidence asset integrity check failed")
    return metadata, data


def validate_reference(store, value: dict) -> dict:
    ref = EvidenceReference.model_validate(value).model_dump(mode="json", exclude_none=True)
    metadata, data = verified_asset(store, ref["asset_sha256"])
    selector = ref.get("selector")
    if selector:
        unit, end = selector["unit"], selector["end"]
        if unit == "byte":
            if end > len(data):
                raise ValueError("Evidence byte selection exceeds asset size")
        else:
            if metadata["media_type"] != "application/json":
                raise ValueError("Frame/sample/time selectors require an indexed JSON recording")
            document = json.loads(data)
            records = document.get("records") if isinstance(document, dict) else None
            if not isinstance(records, list):
                raise ValueError("Recording has no indexed records")
            if unit in {"sample", "frame"} and end > len(records):
                raise ValueError("Evidence selection exceeds recording length")
            if unit == "second":
                duration = document.get("duration_seconds")
                if (
                    not ref.get("clock_id")
                    or not isinstance(duration, int | float)
                    or isinstance(duration, bool)
                    or not math.isfinite(duration)
                    or end > duration
                ):
                    raise ValueError("Time selection needs a clock and declared recording duration")
                if document.get("source_clock_id") != ref["clock_id"]:
                    raise ValueError("Time selection clock does not match its recording")
                timestamps = [
                    r.get("timestamp_s") if isinstance(r, dict) else None for r in records
                ]
                if any(
                    not isinstance(t, int | float)
                    or isinstance(t, bool)
                    or not math.isfinite(t)
                    or t < 0
                    or t > duration
                    for t in timestamps
                ):
                    raise ValueError("Time selection requires bounded timestamps for every record")
                if timestamps != sorted(timestamps):
                    raise ValueError("Recording timestamps must be nondecreasing")
    return ref


def inspect_reference(store, value: dict) -> dict:
    """Missing/tampered bytes stay visible without preventing history inspection."""
    try:
        ref = validate_reference(store, value)
        metadata = store.asset_metadata(ref["asset_sha256"])
        return {**ref, "availability": "available", "asset": metadata}
    except (ValueError, KeyError, OSError) as error:
        return {**value, "availability": "unavailable", "reason": str(error)}
