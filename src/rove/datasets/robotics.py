"""Versioned episode evidence and a deliberately small deterministic test world."""

import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class EpisodeRecording(EvidenceModel):
    schema_version: Literal[1] = 1
    origin: Literal["recorded", "synthetic_rollout"]
    quality: Literal["observed", "synthetic"]
    case_id: str = Field(min_length=1, max_length=128)
    reset_id: str = Field(min_length=1, max_length=128)
    producing_system: str = Field(min_length=1, max_length=200)
    source_clock_id: str = Field(min_length=1, max_length=128)
    frame: str = Field(min_length=1, max_length=128)
    units: dict[str, str]
    task_completed: bool | None = Field(default=None, strict=True)
    duration_seconds: float | None = Field(default=None, ge=0)
    constraint_violations: int | None = Field(default=None, ge=0, strict=True)
    completion_time_s: float | None = Field(default=None, ge=0)
    intervention_count: int | None = Field(default=None, ge=0, strict=True)
    recovery_opportunities: int | None = Field(default=None, ge=0, strict=True)
    recoveries: int | None = Field(default=None, ge=0, strict=True)
    records: list[dict] = Field(default_factory=list, max_length=10000)
    evidence_refs: list[dict] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def consistency(self):
        if self.units.get("time") != "s" or self.units.get("position") != "m":
            raise ValueError("Episode units must explicitly use time=s and position=m")
        if self.origin == "synthetic_rollout" and self.quality != "synthetic":
            raise ValueError("Synthetic rollouts cannot claim observed quality")
        if self.completion_time_s is not None and self.task_completed is not True:
            raise ValueError("Completion time requires a completed episode")
        if (
            self.completion_time_s is not None
            and self.duration_seconds is not None
            and self.completion_time_s > self.duration_seconds
        ):
            raise ValueError("Completion time cannot exceed the recorded episode duration")
        if (self.recoveries is None) != (self.recovery_opportunities is None):
            raise ValueError("Recovery counts require an explicit opportunity denominator")
        if self.recoveries is not None and self.recoveries > self.recovery_opportunities:
            raise ValueError("Recoveries cannot exceed opportunities")
        json.dumps(self.records, allow_nan=False)
        previous = -1.0
        for row in self.records:
            timestamp = row.get("timestamp_s")
            if (
                type(timestamp) not in (int, float)
                or not math.isfinite(timestamp)
                or timestamp < 0
                or timestamp < previous
            ):
                raise ValueError("Episode samples need ordered nonnegative timestamp_s")
            if self.duration_seconds is not None and timestamp > self.duration_seconds:
                raise ValueError("Sample exceeds declared episode duration")
            previous = timestamp
        if not self.records and not self.evidence_refs:
            raise ValueError("Episode measurements require sample records or managed evidence")
        return self


class SyntheticEnvironment(EvidenceModel):
    schema_version: Literal[1] = 1
    frame: Literal["world_1d"] = "world_1d"
    position_unit: Literal["m"] = "m"
    action_unit: Literal["m"] = "m"
    time_unit: Literal["s"] = "s"
    initial_position_m: float = 0.0
    target_position_m: float = 1.0
    tolerance_m: float = Field(default=0.05, gt=0)
    max_delta_m: float = Field(default=0.5, gt=0)
    step_duration_s: float = Field(default=0.1, gt=0, le=60)
    max_steps: int = Field(default=100, ge=1, le=10000)
    forbidden_interval_m: tuple[float, float] | None = None
    disturbance_step: int | None = Field(default=None, ge=1, le=10000)
    disturbance_delta_m: float = 0.0

    @model_validator(mode="after")
    def feasible(self):
        if abs(self.target_position_m - self.initial_position_m) <= self.tolerance_m:
            raise ValueError("Synthetic task must require an action to reach its target")
        if (
            self.forbidden_interval_m
            and self.forbidden_interval_m[0] > self.forbidden_interval_m[1]
        ):
            raise ValueError("Forbidden interval bounds must be ordered")
        if self.disturbance_step and self.disturbance_step > self.max_steps:
            raise ValueError("Disturbance must fall within the episode horizon")
        if self.disturbance_step is not None and self.disturbance_delta_m == 0:
            raise ValueError("Recovery opportunities require a nonzero disturbance")
        return self
