"""Trusted host integrations own candidate execution and independent assessment."""

from typing import Protocol, runtime_checkable

from pydantic import JsonValue

from rove.evaluation.models import CandidateInput
from rove.evaluation.results import EvaluatorResult


@runtime_checkable
class EvaluationAdapter(Protocol):
    revision: str

    async def predict(self, case: CandidateInput, strategy: dict, seed: int) -> JsonValue: ...

    async def grade(
        self, case: CandidateInput, output: JsonValue, reference: dict, grading: dict
    ) -> EvaluatorResult: ...


class TrialExecutor(Protocol):
    def validate(self, campaign: dict) -> None: ...

    def prepare_trial(self, campaign, task, strategy, store) -> tuple[dict, dict, dict]: ...
