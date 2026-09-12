"""Typed settings and evidence for configured verification stages."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class Measurement(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(min_length=1)
    value: float | None = None
    unit: str = Field(min_length=1)
    quality: Literal["observed", "estimated", "synthetic", "unknown"]
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unknown_has_no_value(self):
        if self.quality == "unknown" and self.value is not None:
            raise ValueError("Unknown measurements cannot contain a value")
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


class EvaluatorResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["pass", "fail", "unknown"]
    reasoning: str
    evidence_quality: Literal["observed", "estimated", "synthetic", "unknown"]
    measurements: list[Measurement] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_evidence(self):
        if self.verdict != "unknown" and (
            self.evidence_quality == "unknown" or not self.evidence_refs
        ):
            raise ValueError("A verdict requires evidence references and a declared quality")
        names = [m.name for m in self.measurements]
        if len(names) != len(set(names)):
            raise ValueError("Measurement names must be unique within an evaluator result")
        return self


class CheckResult(BaseModel):
    endpoint: str
    role: Literal["diagnostic", "constraint"]
    required: bool
    execution: Literal["completed", "error", "timeout", "not_requested"]
    evaluator_version: str = ""
    result: EvaluatorResult
