"""Domain-neutral assessment and measurement contracts, also used by robotics."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
