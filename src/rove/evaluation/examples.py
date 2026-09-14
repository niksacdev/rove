"""Small offline integration proving reuse, not a model benchmark."""

from rove.evaluation.models import CandidateInput
from rove.evaluation.results import EvaluatorResult, Measurement


class TextEvaluation:
    revision = "text-normalization-v1"

    async def predict(self, case: CandidateInput, strategy: dict, seed: int):
        mode = strategy.get("mode", "identity")
        if mode not in {"identity", "strip_lower"}:
            raise ValueError("Use identity or strip_lower in the example strategy")
        text = case.inputs.get("text")
        if not isinstance(text, str):
            raise ValueError("Example input requires a text string")
        return text.strip().lower() if mode == "strip_lower" else text

    async def grade(self, case, output, reference, grading):
        if grading:
            raise ValueError("The example grader has no configurable settings")
        if "expected" not in reference:
            return EvaluatorResult(
                verdict="unknown",
                reasoning="No expected answer supplied.",
                evidence_quality="unknown",
            )
        return EvaluatorResult(
            verdict="pass" if output == reference["expected"] else "fail",
            reasoning="Compared the candidate output with the private expected answer.",
            evidence_quality="observed",
            evidence_refs=["candidate.output", "case.reference"],
            measurements=[
                Measurement(
                    name="output_length",
                    value=len(output),
                    unit="characters",
                    quality="observed",
                    evidence_refs=["candidate.output"],
                )
            ],
        )
