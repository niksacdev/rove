"""Domain-neutral inputs. Reference answers are never part of candidate input."""

from __future__ import annotations

import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")]


class BoundedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @model_validator(mode="after")
    def bounded(self):
        if len(json.dumps(self.model_dump(mode="json"), allow_nan=False).encode()) > 1_000_000:
            raise ValueError(
                "Evaluation documents must fit in 1 MB; use asset references for media"
            )
        return self


class CandidateInput(BoundedModel):
    id: Identifier
    task: str = Field(min_length=1, max_length=10000)
    inputs: dict[str, JsonValue]


class EvaluationCase(CandidateInput):
    reference: dict[str, JsonValue] = Field(default_factory=dict)
    case_revision_id: str | None = Field(default=None, min_length=1, max_length=128)

    def candidate(self) -> CandidateInput:
        return CandidateInput(id=self.id, task=self.task, inputs=self.inputs)


class EvaluationConfig(BoundedModel):
    strategies: dict[Identifier, dict[str, JsonValue]] = Field(min_length=1, max_length=20)
    grading: dict[str, JsonValue] = Field(default_factory=dict)
    environment: dict[str, JsonValue] = Field(default_factory=dict)
