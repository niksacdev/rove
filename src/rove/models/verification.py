"""Typed settings and evidence for configured verification stages."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rove.evaluation.results import (  # noqa: F401 — public compatibility aliases
    EvaluatorResult,
    Measurement,
)

AGGREGATION_VERSION = "required-constraints-v1"


class StageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(min_length=1)
    timeout_ms: int | None = Field(default=None, gt=0, le=3_600_000)


class CheckConfig(StageConfig):
    role: Literal["diagnostic", "constraint"] = "diagnostic"
    required: bool = False


class VerifyStageConfig(StageConfig):
    mode: Literal["auto", "precompute", "agent_loop"] = "auto"
    checks: list[CheckConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_checks(self):
        ids = [c.endpoint for c in self.checks]
        if len(ids) != len(set(ids)):
            raise ValueError("Verification checks must have unique endpoints")
        return self


class EvaluatorContext(BaseModel):
    task: str
    pipeline: dict[str, Any] = Field(default_factory=dict)
    episode: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)
    annotation_review_ids: list[str] = Field(default_factory=list)
    before_image_base64: str = ""
    after_image_base64: str = ""
    urdf_path: str | None = None


class CheckResult(BaseModel):
    endpoint: str
    role: Literal["diagnostic", "constraint"]
    required: bool
    execution: Literal["completed", "error", "timeout", "not_requested"]
    evaluator_version: str = ""
    result: EvaluatorResult
