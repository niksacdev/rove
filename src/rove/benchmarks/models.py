"""Saved benchmark inputs. Existing strategy and verify-stage contracts remain unchanged."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rove.models import ExampleData

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")]


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class BenchmarkTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Identifier
    task: str = Field(min_length=1, max_length=10000)
    image_base64: str = Field(min_length=1, max_length=4_000_000)
    example: ExampleData | None = None

    @field_validator("image_base64")
    @classmethod
    def valid_image(cls, value: str) -> str:
        raw = base64.b64decode(value, validate=True)
        if not raw.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")):
            raise ValueError("Use a base64 PNG or JPEG image")
        return value


class CampaignSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    suite_version: str = Field(min_length=1, max_length=160)
    revision: str = Field(min_length=1, max_length=160)
    strategies: list[Identifier] = Field(min_length=1, max_length=20)
    tasks: list[BenchmarkTask] = Field(min_length=1, max_length=1000)
    seeds: list[Annotated[int, Field(ge=0, le=2**32 - 1)]] = Field(min_length=1, max_length=1000)
    ks: list[Annotated[int, Field(ge=1, le=1000)]] = Field(
        default=[1, 3, 5], min_length=1, max_length=100
    )
    timeout_s: float = Field(default=120, ge=1, le=3600, allow_inf_nan=False)
    grading: Literal["verify_stage_judgment"] = "verify_stage_judgment"

    @model_validator(mode="after")
    def unique_inputs(self) -> CampaignSpec:
        for label, values in (
            ("strategies", self.strategies),
            ("task IDs", [t.id for t in self.tasks]),
            ("seeds", self.seeds),
            ("ks", self.ks),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"Duplicate {label} are not allowed")
        if self.planned_trials > 20000:
            raise ValueError("A campaign may contain at most 20,000 trials")
        return self

    @property
    def planned_trials(self) -> int:
        return len(self.tasks) * len(self.strategies) * len(self.seeds)
