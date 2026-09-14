"""Non-robotics executions must use the real lifecycle, history and evidence stores."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path

import pytest

from rove.benchmarks.baselines import BaselineInput, BaselineStore
from rove.benchmarks.comparison import compare_campaigns
from rove.benchmarks.models import CampaignSpec
from rove.benchmarks.report import saved_report, to_csv, to_html
from rove.benchmarks.runner import run_campaign
from rove.benchmarks.store import CampaignStore
from rove.evaluation.executors import prepare_evaluation
from rove.evaluation.models import EvaluationCase, EvaluationConfig
from rove.evaluation.registry import resolve
from rove.evaluation.trials import run_evaluation_trials
from rove.trials.store import TrialStore
from rove.trials.traces import trial_trace


@pytest.fixture
def evaluation():
    spec = CampaignSpec(
        name="Text comparison",
        suite_version="text-v1",
        revision="1",
        execution="text-example",
        grading="executor_assessment",
        strategies=["raw", "normalized"],
        seeds=[1, 2],
        ks=[1, 2],
        timeout_s=10,
        tasks=[
            {
                "id": "spaces",
                "task": "Normalize input",
                "inputs": {"text": " HELLO "},
                "reference": {"expected": "hello"},
            },
            {
                "id": "plain",
                "task": "Normalize input",
                "inputs": {"text": "hello"},
                "reference": {"expected": "hello"},
            },
            {"id": "ungraded", "task": "Unknown reference", "inputs": {"text": "hello"}},
        ],
    )
    config = EvaluationConfig(
        strategies={"raw": {"mode": "identity"}, "normalized": {"mode": "strip_lower"}}
    )
    return spec, config


@pytest.mark.asyncio
async def test_shared_campaign_records_scores_evidence_and_resume(tmp_path, evaluation):
    spec, config = evaluation
    store = CampaignStore(tmp_path)
    identity = store.create(prepare_evaluation(spec, config))
    await run_campaign(store, identity)
    assert store.get(identity)["status"] == "completed"
    assert len(store.trials(identity)) == 12
    report = saved_report(tmp_path, identity)
    counts = {
        (t["task_id"], t["strategy_id"]): (t["passed"], t["failed"], t["unknown"])
        for t in report["summary"]["tasks"]
    }
    assert counts["spaces", "raw"] == (0, 2, 0)
    assert counts["spaces", "normalized"] == (2, 0, 0)
    assert counts["ungraded", "normalized"] == (0, 0, 2)
    assert report["summary"]["robotics"] == []
    assert report["verification"]["measurement_records"]
    assert report["assessments"]["scope"] == "executor_assessment"
    for task in report["campaign"]["spec"]["tasks"]:
        assert "reference" not in task and "inputs" not in task
    assert "spaces" in to_csv(report)
    html = to_html(report)
    assert "Text comparison" in html
    assert "your robotics agent pipeline" not in html
    trials = TrialStore(tmp_path)
    before = list(trials.for_campaign(identity))
    for trial in before:
        assert "image_asset" not in trial["task"]
        assert "reference" not in trial["task"]
        assert "stages" not in trial["result"]["result"]
        assert {e["id"] for e in trials.evidence(trial["id"])} == {
            "candidate.output",
            "case.reference",
            "assessment.output",
        }
        assert len(trials.events(trial["id"])) == 4
        assert trial_trace(trials, trial["id"])["lanes"]
    await run_campaign(store, identity)
    assert list(trials.for_campaign(identity)) == before


@pytest.mark.asyncio
async def test_standalone_uses_history_without_creating_campaign(tmp_path, evaluation):
    spec, config = evaluation
    result = await run_evaluation_trials(
        root=tmp_path,
        case=spec.tasks[0],
        config=config,
        strategy_ids=spec.strategies,
        execution=spec.execution,
    )
    assert [r["outcome"] for r in result["results"]] == ["fail", "pass"]
    assert CampaignStore(tmp_path).list() == []
    for identity in result["trial_ids"].values():
        row = TrialStore(tmp_path).get(identity)
        assert row["source"] == "quick" and row["campaign_id"] is None
        assert row["status"] == "completed"


@pytest.mark.asyncio
async def test_comparison_and_baseline_keep_grading_and_inputs_fixed(tmp_path, evaluation):
    spec, config = evaluation
    store = CampaignStore(tmp_path)
    identity = store.create(prepare_evaluation(spec, config))
    await run_campaign(store, identity)
    campaign, trials = store.get(identity), store.trials(identity)
    compared = compare_campaigns(campaign, trials, campaign, trials, "raw", "normalized")
    assert compared["comparable"], compared["reasons"]
    assert compared["paired_counts"]["fail_to_pass"] == 2
    baseline = BaselineStore(tmp_path).save(
        BaselineInput(
            name="Original", campaign_id=identity, strategy_id="raw", operation_id="capture"
        )
    )
    assert baseline["summary"]["planned_trials"] == 6
    assert baseline["trial_outcomes"][0]["result"]["assessment"]
    for key, changed in (
        ("grading", {"threshold": 0.9}),
        ("environment", {"dependency_lock": "changed"}),
    ):
        other = copy.deepcopy(campaign)
        other["config"][key] = changed
        assert not compare_campaigns(campaign, trials, other, trials, "raw", "normalized")[
            "comparable"
        ]
    other = copy.deepcopy(campaign)
    other["spec"]["tasks"][0]["reference"] = {"expected": "changed"}
    assert not compare_campaigns(campaign, trials, other, trials, "raw", "normalized")["comparable"]


def test_inputs_are_bounded_and_execution_is_not_a_python_path(evaluation):
    spec, _ = evaluation
    case = EvaluationCase(
        id="nested",
        task="Classify",
        inputs={"messages": [{"role": "user", "text": "hello"}]},
        reference={"private_answer": "yes"},
    )
    assert "private_answer" not in json.dumps(case.candidate().model_dump())
    with pytest.raises(ValueError):
        EvaluationCase(id="large", task="Classify", inputs={"text": "x" * 1_000_001})
    with pytest.raises(ValueError):
        EvaluationCase(id="nan", task="Classify", inputs={"value": float("nan")})
    for name in ("os:system", "uninstalled-executor", "../plugin"):
        with pytest.raises(ValueError):
            resolve(name)
    old = spec.model_dump()
    old["execution"] = "robotics"
    with pytest.raises(ValueError):
        CampaignSpec.model_validate(old)


@pytest.fixture
def installed_executor(tmp_path, monkeypatch):
    """Install test metadata on PYTHONPATH, exercising the real worker resolver."""
    directory = tmp_path / "plugins"
    directory.mkdir()
    module = directory / "evaluation_test_plugin.py"
    module.write_text("""import asyncio
from rove.evaluation.results import EvaluatorResult
class Adapter:
    revision = "test-v1"
    async def predict(self, case, strategy, seed):
        assert "reference" not in case.model_dump()
        if strategy.get("slow"):
            await asyncio.sleep(30)
        return {"value": case.inputs["nested"]["value"], "success": True}
    async def grade(self, case, output, reference, grading):
        assert reference["hidden"] == "only grader"
        output["value"] = "grader mutation"
        if grading.get("constructed"):
            return EvaluatorResult.model_construct(verdict="pass", reasoning="No evidence", evidence_quality="observed", evidence_refs=[])
        return EvaluatorResult(verdict="fail", reasoning="Candidate success field is not a grade.",
            evidence_quality="observed", evidence_refs=["invented" if grading.get("bad") else "candidate.output"])
""")
    module.write_text(
        module.read_text()
        + "\nclass SlowConstructor(Adapter):\n    def __init__(self):\n        import time\n        time.sleep(30)\n"
    )
    metadata = directory / "rove_test_executor-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: rove-test-executor\nVersion: 1.0\n")
    (metadata / "entry_points.txt").write_text(
        "[rove.evaluations]\ntest-executor = evaluation_test_plugin:Adapter\nslow-constructor = evaluation_test_plugin:SlowConstructor\n"
    )
    monkeypatch.syspath_prepend(str(directory))
    monkeypatch.delitem(sys.modules, "evaluation_test_plugin", raising=False)
    monkeypatch.setenv("PYTHONPATH", f"{directory}:{Path('src').resolve()}")
    return module


def custom_spec():
    return CampaignSpec(
        name="Custom",
        suite_version="nested",
        revision="1",
        execution="test-executor",
        grading="executor_assessment",
        strategies=["candidate"],
        seeds=[1],
        timeout_s=1,
        tasks=[
            {
                "id": "nested",
                "task": "Assess structured input",
                "inputs": {"nested": {"value": 3}},
                "reference": {"hidden": "only grader"},
            }
        ],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "grading,expected",
    [({}, "fail"), ({"bad": True}, "unknown"), ({"constructed": True}, "unknown")],
)
async def test_installed_executor_cannot_self_grade_or_invent_evidence(
    tmp_path, installed_executor, grading, expected
):
    store = CampaignStore(tmp_path / "store")
    config = EvaluationConfig(strategies={"candidate": {}}, grading=grading)
    identity = store.create(prepare_evaluation(custom_spec(), config))
    await run_campaign(store, identity)
    row = store.trials(identity)[0]
    assert row["outcome"] == expected
    assert row["execution"] == ("completed" if expected == "fail" else "error")
    if expected == "fail":
        assert row["result"]["output"]["value"] == 3
        journal = TrialStore(store.root)
        ref = next(e for e in journal.evidence(row["trial_id"]) if e["id"] == "candidate.output")
        assert (
            json.loads(journal.asset_path(ref["asset_sha256"]).read_text())
            == row["result"]["output"]
        )
    if expected == "unknown":
        assert TrialStore(store.root).evidence(row["trial_id"]), (
            "Candidate evidence survives grader failure"
        )


@pytest.mark.asyncio
async def test_executor_source_drift_rejected_before_attempt(tmp_path, installed_executor):
    store = CampaignStore(tmp_path / "store")
    identity = store.create(
        prepare_evaluation(custom_spec(), EvaluationConfig(strategies={"candidate": {}}))
    )
    installed_executor.write_text(installed_executor.read_text() + "\n# changed grader\n")
    with pytest.raises(ValueError, match="executor changed"):
        await run_campaign(store, identity)
    assert store.trials(identity) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_worker_timeout_and_cancellation_are_durable(tmp_path, installed_executor, cancel):
    store = CampaignStore(tmp_path / "store")
    identity = store.create(
        prepare_evaluation(
            custom_spec(), EvaluationConfig(strategies={"candidate": {"slow": True}})
        )
    )
    running = asyncio.create_task(run_campaign(store, identity))
    if cancel:
        for _ in range(200):
            if store.trials(identity):
                break
            await asyncio.sleep(0.01)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    else:
        await running
    trial = store.trials(identity)[0]
    assert trial["outcome"] == "unknown"
    assert trial["execution"] == ("cancelled" if cancel else "timeout")
    assert TrialStore(store.root).get(trial["trial_id"])["status"] == trial["execution"]


@pytest.mark.asyncio
async def test_constructor_runs_inside_worker_deadline(tmp_path, installed_executor):
    spec = custom_spec().model_copy(update={"execution": "slow-constructor"})
    store = CampaignStore(tmp_path / "store")
    identity = store.create(
        prepare_evaluation(spec, EvaluationConfig(strategies={"candidate": {}}))
    )
    await run_campaign(store, identity)
    assert store.trials(identity)[0]["execution"] == "timeout"


def test_secret_shaped_inputs_keep_distinct_private_content_identities(tmp_path, evaluation):
    from rove.evaluation.executors import StructuredExecutor
    from rove.trials.snapshots import snapshot

    spec, config = evaluation
    campaign = prepare_evaluation(spec, config)
    store = TrialStore(tmp_path)
    cases = [
        EvaluationCase(id="input", task="Use token", inputs={"token": value})
        for value in ("red", "blue")
    ]
    tasks = [StructuredExecutor().prepare_trial(campaign, case, "raw", store)[0] for case in cases]
    assert snapshot(tasks[0], {}, {})["sha256"] != snapshot(tasks[1], {}, {})["sha256"]
    for case, task in zip(cases, tasks, strict=True):
        assert (
            json.loads(store.asset_path(task["input_asset"]["sha256"]).read_text())
            == case.candidate().model_dump()
        )
        assert snapshot(task, {}, {})["task"]["inputs"]["token"] == "[REDACTED]"


def test_generic_timeline_classifies_grader_environment_and_case_changes(evaluation):
    from rove.benchmarks.trajectory import _changes

    spec, config = evaluation
    campaign = prepare_evaluation(spec, config)
    for field, category in (("grading", "success_metrics"), ("environment", "environment")):
        changed = copy.deepcopy(campaign)
        changed["config"][field] = {"token": "different"}
        assert category in _changes(campaign, changed)
    changed = copy.deepcopy(campaign)
    changed["spec"]["tasks"][0]["inputs"]["token"] = "different"
    assert "cases" in _changes(campaign, changed)


@pytest.mark.parametrize("component", ["strategy", "grading", "environment"])
def test_redacted_configuration_keeps_distinct_trial_identity(tmp_path, evaluation, component):
    from rove.evaluation.executors import StructuredExecutor
    from rove.trials.snapshots import snapshot

    spec, config = evaluation
    campaign = prepare_evaluation(spec, config)
    store = TrialStore(tmp_path)
    identities = []
    for value in ("red", "blue"):
        changed = copy.deepcopy(campaign)
        target = (
            changed["config"]["strategies"]["raw"]
            if component == "strategy"
            else changed["config"][component]
        )
        target["token"] = value
        task, frozen, _ = StructuredExecutor().prepare_trial(changed, spec.tasks[0], "raw", store)
        captured = snapshot(task, {"id": "raw"}, frozen)
        redacted = (
            captured["config"]["strategies"]["raw"]
            if component == "strategy"
            else captured["config"][component]
        )
        assert redacted["token"] == "[REDACTED]"
        identities.append(captured["sha256"])
    assert identities[0] != identities[1]
