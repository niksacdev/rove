"""Bounded product contracts shared by the API, UI and local dataset service."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class CaseInput(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    task: str = Field(min_length=1, max_length=10000)
    candidate_context: dict = Field(default_factory=dict)
    conditions: dict = Field(default_factory=dict)
    recorded_evidence: dict = Field(default_factory=dict)


class Criterion(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    description: str = Field(min_length=1, max_length=2000)
    assessment: Literal["human_review", "configured_verifier"] = "human_review"
    required: bool = True
    endpoint: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def binding(self):
        if self.assessment == "configured_verifier" and not self.endpoint:
            raise ValueError("Automated criteria require a configured verifier endpoint")
        if self.assessment == "human_review" and self.endpoint is not None:
            raise ValueError("Human criteria cannot claim an automated endpoint")
        return self


class SuccessContract(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    scope: Literal["scene_understanding", "plan_quality", "episode_outcome"]
    evidence_mode: Literal["candidate_output", "recorded_episode", "synthetic_rollout"]
    criteria: list[Criterion] = Field(min_length=1, max_length=30)
    metrics: list[
        Literal[
            "task_success",
            "pass_at_k",
            "pass_pow_k",
            "pipeline_latency",
            "constraint_outcomes",
            "episode_completion_time",
            "autonomous_completion",
            "recovery",
        ]
    ] = Field(
        default=["task_success", "pass_at_k", "pass_pow_k", "pipeline_latency"], max_length=20
    )

    @model_validator(mode="after")
    def unique(self):
        if not any(criterion.required for criterion in self.criteria):
            raise ValueError("A success contract needs at least one required acceptance criterion")
        if len({criterion.id for criterion in self.criteria}) != len(self.criteria):
            raise ValueError("Criterion IDs must be unique")
        if len(set(self.metrics)) != len(self.metrics):
            raise ValueError("Metrics must be unique")
        return self


class ReviewInput(StrictModel):
    target_type: Literal["case_validity", "case_annotation", "trial_output"]
    case_revision_id: str = Field(min_length=1, max_length=128)
    trial_id: str | None = Field(default=None, min_length=1, max_length=128)
    contract_id: str = Field(min_length=1, max_length=128)
    reviewer: str = Field(min_length=1, max_length=160)
    status: Literal["draft", "final"] = "draft"
    decision: Literal["accepted", "rejected", "unknown"] = "unknown"
    criteria: dict[str, Literal["accepted", "rejected", "unknown"]] = Field(default_factory=dict)
    annotations: dict = Field(default_factory=dict)
    rationale: str = Field(default="", max_length=10000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    supersedes_id: str | None = None

    @model_validator(mode="after")
    def target(self):
        if (self.target_type == "trial_output") != (self.trial_id is not None):
            raise ValueError("Only trial output reviews require a trial ID")
        if self.target_type != "case_annotation" and self.annotations:
            raise ValueError("Reusable annotations require a case_annotation review")
        if self.status == "final" and not self.rationale.strip():
            raise ValueError("Final reviews require a rationale")
        if (
            self.target_type == "case_annotation"
            and self.decision == "accepted"
            and not self.annotations
        ):
            raise ValueError("Accepted annotations must contain explicitly supplied labels")
        if any(len(ref) > 512 for ref in self.evidence_refs):
            raise ValueError("Evidence references are too long")
        return self


class DatasetMember(StrictModel):
    case_revision_id: str = Field(min_length=1, max_length=128)
    disposition: Literal["included", "excluded"] = "included"
    review_ids: list[str] = Field(default_factory=list, max_length=100)
    reason: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def exclusion(self):
        if self.disposition == "excluded" and not self.reason.strip():
            raise ValueError("Excluded cases require a reason")
        if len(self.review_ids) != len(set(self.review_ids)):
            raise ValueError("A review may be selected only once per member")
        return self


class FreezeInput(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    parent_id: str | None = None
    contract_id: str = Field(min_length=1, max_length=128)
    members: list[DatasetMember] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique(self):
        ids = [member.case_revision_id for member in self.members]
        if len(ids) != len(set(ids)):
            raise ValueError("A case revision may appear only once")
        if not any(member.disposition == "included" for member in self.members):
            raise ValueError("A dataset requires at least one included case")
        return self
