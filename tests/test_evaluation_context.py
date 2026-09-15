"""Long-running adapters retain bounded evidence through the existing lifecycle."""

from __future__ import annotations

import asyncio
import hashlib
import json
import runpy
from types import SimpleNamespace

import pytest

from rove.evaluation.context import EvaluationContext
from rove.evaluation.registry import resolve
from rove.evaluation.results import EvaluatorResult, Measurement
from rove.evaluation.worker import execute
from rove.trials.store import TrialStore
from rove.trials.traces import trial_trace


@pytest.fixture
def request_record(tmp_path):
    store = TrialStore(tmp_path)
    identity = store.begin(source="quick", task={}, strategy={}, config={})
    return store, {
        "trial_root": str(tmp_path),
        "trial_id": identity,
        "execution": "context-test",
        "executor_identity": {"name": "context-test"},
        "strategy_id": "policy",
        "seed": 7,
        "task": {
            "id": "case",
            "task": "Complete the task",
            "inputs": {"reset_seed": 3},
            "reference": {"private_goal": "held-out-reference"},
        },
        "config": {
            "strategies": {"policy": {}},
            "grading": {},
            "environment": {"scene": {"revision": "fixed"}},
        },
    }


class RecordingAdapter:
    revision = "context-test-v1"

    def bind_context(self, context):
        self.context = context

    async def predict(self, case, strategy, seed):
        assert "reference" not in case.model_dump()
        environment = self.context.environment
        environment["scene"]["revision"] = "attempted mutation"
        assert self.context.environment["scene"]["revision"] == "fixed"
        assert not hasattr(self.context, "reference")
        self.context.emit("episode", "started", data={"reset_seed": case.inputs["reset_seed"]})
        self.context.record_json(
            "episode.state", {"goal_at_end": True}, kind="state", units={"time": "second"}
        )
        self.context.emit("episode", "completed", evidence_refs=["episode.state"])
        return {"actions_executed": 15}

    async def grade(self, case, output, reference, grading):
        assert reference == {"private_goal": "held-out-reference"}
        return EvaluatorResult(
            verdict="pass",
            reasoning="The environment recorded the final goal state.",
            evidence_quality="observed",
            evidence_refs=["episode.state"],
            measurements=[
                Measurement(
                    name="actions_executed",
                    value=output["actions_executed"],
                    unit="actions",
                    quality="observed",
                    evidence_refs=["candidate.output"],
                )
            ],
        )


def use_adapter(monkeypatch, adapter):
    monkeypatch.setattr(
        "rove.evaluation.worker.resolve", lambda _: (adapter, {"name": "context-test"})
    )


@pytest.mark.asyncio
async def test_context_binds_environment_records_evidence_and_grades(request_record, monkeypatch):
    store, request = request_record
    use_adapter(monkeypatch, RecordingAdapter)
    result = await execute(request)
    assert result["outcome"] == "pass"
    assert result["metric_scope"] == "executor_assessment"
    assert request["config"]["environment"] == {"scene": {"revision": "fixed"}}
    assert {ref["id"] for ref in store.evidence(request["trial_id"])} == {
        "episode.state",
        "candidate.output",
        "case.reference",
        "assessment.output",
    }
    trace = trial_trace(store, request["trial_id"])
    episode = next(lane for lane in trace["lanes"] if lane["stage"] == "episode")
    assert episode["items"][0]["duration_seconds"] >= 0


@pytest.mark.asyncio
@pytest.mark.parametrize("measurement", [False, True])
async def test_grader_cannot_cite_an_unrecorded_artifact(request_record, monkeypatch, measurement):
    class InventedEvidence(RecordingAdapter):
        async def grade(self, *args):
            result = await super().grade(*args)
            if measurement:
                result.measurements[0].evidence_refs = ["not.recorded"]
            else:
                result.evidence_refs = ["not.recorded"]
            return result

    _, request = request_record
    use_adapter(monkeypatch, InventedEvidence)
    with pytest.raises(ValueError, match="not recorded"):
        await execute(request)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_failure_preserves_progress_and_records_no_exception_secrets(
    request_record, monkeypatch, cancelled
):
    class InterruptedAdapter(RecordingAdapter):
        async def predict(self, *args):
            await super().predict(*args)
            error = asyncio.CancelledError if cancelled else RuntimeError
            raise error("Credential value must never be persisted in error diagnostics")

    store, request = request_record
    use_adapter(monkeypatch, InterruptedAdapter)
    with pytest.raises(asyncio.CancelledError if cancelled else RuntimeError):
        await execute(request)
    refs = {ref["id"]: ref for ref in store.evidence(request["trial_id"])}
    assert "episode.state" in refs
    assert "candidate.output" not in refs
    error = json.loads(store.asset_path(refs["evaluation.error"]["asset_sha256"]).read_text())
    assert error["exception_type"] == ("CancelledError" if cancelled else "RuntimeError")
    assert "Credential" not in json.dumps(error)
    events = store.events(request["trial_id"])
    assert events[-1]["event_type"] == (
        "evaluation.cancelled" if cancelled else "evaluation.failed"
    )
    assert "Credential" not in json.dumps(events)


@pytest.mark.asyncio
async def test_changed_evidence_is_not_valid_for_grading(request_record, monkeypatch):
    store, request = request_record

    class ChangedEvidence(RecordingAdapter):
        async def grade(self, *args):
            ref = next(
                ref for ref in store.evidence(request["trial_id"]) if ref["id"] == "episode.state"
            )
            store.asset_path(ref["asset_sha256"]).write_text("changed")
            return await super().grade(*args)

    use_adapter(monkeypatch, ChangedEvidence)
    with pytest.raises(ValueError, match="not recorded"):
        await execute(request)


def test_context_reserves_core_ids_and_bounds_artifacts(request_record, tmp_path, monkeypatch):
    store, request = request_record
    context = EvaluationContext(store, request["trial_id"], {})
    with pytest.raises(ValueError, match="reserved"):
        context.record_json("case.reference", {"spoofed": True})
    with pytest.raises(ValueError, match="not recorded"):
        context.emit("episode", "progress", evidence_refs=["invented"])
    artifact = tmp_path / "video.mp4"
    artifact.write_bytes(b"bounded example")
    monkeypatch.setattr("rove.evaluation.context.MAX_ASSET_BYTES", 8)
    with pytest.raises(ValueError, match="bounded segments"):
        context.record_file("episode.video", artifact, "video/mp4", kind="video")
    artifact.write_bytes(b"video")
    recorded = context.record_file("episode.video", artifact, "video/mp4", kind="video")
    assert recorded["asset"]["size_bytes"] == 5
    assert context.evidence_ids == {"episode.video"}


@pytest.mark.asyncio
async def test_async_binder_is_supported(request_record, monkeypatch):
    class AsyncBindAdapter(RecordingAdapter):
        async def bind_context(self, context):
            super().bind_context(context)

    _, request = request_record
    use_adapter(monkeypatch, AsyncBindAdapter)
    assert (await execute(request))["outcome"] == "pass"


def test_binding_source_is_part_of_executor_identity(tmp_path, monkeypatch):
    binding_source = tmp_path / "context_mixin.py"
    binding_source.write_text(
        "class ContextMixin:\n    def bind_context(self, context):\n        self.context = context\n"
    )
    mixin = runpy.run_path(str(binding_source))["ContextMixin"]

    class Adapter(mixin, RecordingAdapter):
        pass

    monkeypatch.setattr(
        "rove.evaluation.registry.importlib.metadata.entry_points",
        lambda **kwargs: [SimpleNamespace(load=lambda: Adapter, dist=SimpleNamespace(name="test"))],
    )
    _, first = resolve("context-test")
    assert (
        first["sources"]["context_binding"]
        == hashlib.sha256(binding_source.read_bytes()).hexdigest()
    )
    binding_source.write_text(binding_source.read_text() + "\n# revised binding\n")
    _, second = resolve("context-test")
    assert first != second


def test_non_callable_context_binding_is_rejected(monkeypatch):
    class InvalidAdapter(RecordingAdapter):
        bind_context = "not callable"

    monkeypatch.setattr(
        "rove.evaluation.registry.importlib.metadata.entry_points",
        lambda **kwargs: [SimpleNamespace(load=lambda: InvalidAdapter, dist=None)],
    )
    with pytest.raises(ValueError, match="must be callable"):
        resolve("context-test")


def test_builtin_episode_executor_cannot_be_overridden(monkeypatch):
    monkeypatch.setattr(
        "rove.evaluation.registry.importlib.metadata.entry_points", lambda **kwargs: [object()]
    )
    with pytest.raises(ValueError, match="conflicts with the built-in abc-bimanual"):
        resolve("abc-bimanual")
