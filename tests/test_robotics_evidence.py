"""Causal synthetic execution, explicit label provenance and honest metric denominators."""

import copy
import io
from contextlib import nullcontext as _does_not_raise

import pytest
from PIL import Image

from rove.adapters.synthetic_actions import SyntheticActionAdapter
from rove.adapters.synthetic_sim import SyntheticSimAdapter
from rove.benchmarks.metrics import summarize
from rove.benchmarks.workflow import LaunchRequest, prepare_launch
from rove.datasets.models import SuccessContract
from rove.datasets.robotics import EpisodeRecording
from rove.datasets.service import DatasetService
from rove.evaluators.robotics import annotation_match, episode_outcome
from rove.models import ExampleData, TaskPlan
from rove.models.config import RoveConfig
from rove.models.verification import EvaluatorContext
from rove.orchestrator.pipeline import EvaluationPipeline
from rove.orchestrator.recording import event_sink


class EpisodeVerifier:
    model_id = "judge"
    display_name = "Episode judge"
    version = "test-episode-v1"

    async def evaluate(self, context):
        return episode_outcome(context, {})


def config():
    return RoveConfig.model_validate(
        {
            "endpoints": {
                "actions": {
                    "type": "vla",
                    "adapter": "synthetic_actions",
                    "capabilities": ["action_execution"],
                },
                "sim": {"type": "sim", "adapter": "synthetic_sim"},
                "judge": {
                    "type": "verifier",
                    "adapter": "local_verifier",
                    "capabilities": ["verification"],
                    "config": {
                        "entrypoint": "rove.evaluators.robotics:episode_outcome",
                        "revision": "v1",
                    },
                },
            },
            "strategies": {"base": {"act": "actions", "sim": "sim", "verify": "judge"}},
        }
    )


def make_case(service, **overrides):
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
    image = service.trials.save_asset(buffer.getvalue(), "image/png")
    return service.import_case(
        {
            "name": "Reach",
            "task": "Move the point to 1 metre",
            "conditions": {"synthetic_environment": {}},
            **overrides,
        },
        image["sha256"],
    )


def contract_payload(**overrides):
    return {
        "name": "Synthetic reaching",
        "scope": "episode_outcome",
        "evidence_mode": "synthetic_rollout",
        "criteria": [
            {
                "id": "completion",
                "description": "Reach the target without a constraint violation",
                "assessment": "configured_verifier",
                "endpoint": "judge",
            }
        ],
        "metrics": [
            "task_success",
            "episode_completion_time",
            "autonomous_completion",
            "recovery",
            "constraint_outcomes",
        ],
        **overrides,
    }


async def execute(actions, *, environment=None, mode="sequential", sink=None):
    pipeline = EvaluationPipeline(
        act_adapter=SyntheticActionAdapter(config={"actions": actions}),
        verify_adapter=EpisodeVerifier(),
        sim=SyntheticSimAdapter(),
        pipeline_mode=mode,
    )
    token = event_sink.set(sink)
    try:
        return [
            row.model_dump(mode="json")
            async for row in pipeline.run_trial(
                "Reach",
                "",
                example=ExampleData(
                    extras={
                        "synthetic_environment": environment or {},
                        "case_revision_id": "case-1",
                    }
                ),
            )
        ]
    finally:
        event_sink.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sequential", "parallel"])
async def test_candidate_actions_cause_success_or_failure_in_both_pipeline_modes(mode):
    successful = await execute([[0.5], [0.5]], mode=mode)
    failed = await execute([[-0.5], [-0.5]], mode=mode)
    assert successful[-1]["output"]["success"] is True
    assert failed[-1]["output"]["success"] is False
    evidence = successful[-1]["output"]["evaluator_result"]
    assert evidence["evidence_quality"] == "synthetic"
    assert evidence["details"]["episode_origin"] == "synthetic_rollout"
    assert evidence["details"]["case_id"] == "case-1"


@pytest.mark.asyncio
async def test_rollout_events_archive_executed_actions_and_clock_not_predicted_outcome(tmp_path):
    service = DatasetService(tmp_path)
    identity = service.trials.begin(source="quick", task={}, strategy={}, config={})
    await execute([[0.5], [0.5]], sink=lambda e: service.trials.append_event(identity, e))
    event = next(
        e for e in service.trials.events(identity) if e.get("event_type") == "episode.recorded"
    )
    assert "evidence_payload" not in event
    ref = event["evidence_refs"][0]
    assert ref["id"] == "episode"
    import json

    recorded = json.loads(service.trials.asset_path(ref["asset_sha256"]).read_text())
    assert [r.get("action_delta_m") for r in recorded["records"][1:]] == [0.5, 0.5]
    assert recorded["duration_seconds"] == 0.2


@pytest.mark.asyncio
@pytest.mark.parametrize("actions", [[], [[2.0]], [[0.5, 0.5]], [[float("nan")]]])
async def test_missing_or_invalid_actions_cannot_become_synthetic_success(actions):
    with pytest.raises(ValueError) if not actions else _does_not_raise():
        stages = await execute(actions)
        assert not any((s.get("output") or {}).get("success") for s in stages)


@pytest.mark.asyncio
async def test_disturbance_recovery_and_constraints_are_action_dependent():
    recovered = await execute(
        [[0.5], [0.5], [0.5]], environment={"disturbance_step": 1, "disturbance_delta_m": -0.5}
    )
    measures = {
        m["name"]: m["value"] for m in recovered[-1]["output"]["evaluator_result"]["measurements"]
    }
    assert measures["recovery_opportunities"] == measures["recoveries"] == 1
    blocked = await execute([[0.5], [0.5]], environment={"forbidden_interval_m": [0.25, 0.3]})
    assert blocked[-1]["output"]["success"] is False
    assert (
        next(
            m["value"]
            for m in blocked[-1]["output"]["evaluator_result"]["measurements"]
            if m["name"] == "constraint_violations"
        )
        == 1
    )


@pytest.mark.asyncio
async def test_failed_reset_aborts_before_candidate_execution():
    class BrokenSim(SyntheticSimAdapter):
        async def reset(self, task):
            raise ValueError("Reset unavailable")

    candidate = SyntheticActionAdapter()
    pipeline = EvaluationPipeline(
        act_adapter=candidate, sim=BrokenSim(), verify_adapter=EpisodeVerifier()
    )
    stages = [s async for s in pipeline.run_trial("Reach", "")]
    assert len(stages) == 1 and stages[0].status == "error"
    assert "reset" in stages[0].error.lower()


def recording(**changes):
    return {
        "schema_version": 1,
        "origin": "recorded",
        "quality": "observed",
        "case_id": "recorded-case",
        "reset_id": "reset-1",
        "producing_system": "customer-robot-v1",
        "source_clock_id": "robot-monotonic",
        "frame": "world",
        "units": {"time": "s", "position": "m"},
        "task_completed": True,
        "constraint_violations": 0,
        "records": [{"timestamp_s": 0, "position_m": 1}],
        **changes,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"units": {"time": "ms", "position": "m"}},
        {"frame": ""},
        {"source_clock_id": ""},
        {"records": []},
        {"records": [{"timestamp_s": 2}, {"timestamp_s": 1}]},
        {"records": [{"timestamp_s": float("nan")}]},
        {"recoveries": 1},
        {"recoveries": 2, "recovery_opportunities": 1},
        {"task_completed": False, "completion_time_s": 3},
        {"origin": "synthetic_rollout", "quality": "observed"},
    ],
)
def test_episode_schema_rejects_invalid_units_frames_clocks_and_denominators(change):
    with pytest.raises(ValueError):
        EpisodeRecording.model_validate(recording(**change))


def test_missing_recorded_assets_rejected_at_case_intake(tmp_path):
    service = DatasetService(tmp_path)
    with pytest.raises((KeyError, ValueError)):
        make_case(
            service,
            recorded_evidence=recording(
                records=[],
                evidence_refs=[{"id": "episode", "asset_sha256": "a" * 64, "kind": "trajectory"}],
            ),
        )


def test_synthetic_launch_requires_real_sim_act_pair_and_explicit_case_environment(tmp_path):
    service = DatasetService(tmp_path)
    case = make_case(service)
    contract = service.create_contract(contract_payload())
    request = LaunchRequest(
        case_revision_ids=[case["id"]], contract_id=contract["id"], strategies=["base"]
    )
    _, preview = prepare_launch(tmp_path, request, config())
    assert preview["ready"]
    bad = config()
    bad.strategies["base"].sim = None
    _, preview = prepare_launch(tmp_path, request, bad)
    assert not preview["ready"]
    assert any("synthetic_sim" in issue for issue in preview["blockers"])


def frozen_annotations(service, case, contract, value="red block"):
    review = service.create_review(
        {
            "target_type": "case_annotation",
            "case_revision_id": case["id"],
            "contract_id": contract["id"],
            "reviewer": "SME",
            "status": "final",
            "decision": "accepted",
            "annotations": {"target": value},
            "rationale": "Inspected image",
        }
    )
    payload = {
        "name": "Labels v1",
        "contract_id": contract["id"],
        "members": [{"case_revision_id": case["id"], "review_ids": [review["id"]]}],
    }
    dataset = service.freeze(
        payload, expected_preview_hash=service.preview_freeze(payload)["preview_hash"]
    )
    return dataset, review


def test_explicit_frozen_binding_preserves_review_identity_and_never_enters_candidate_context(
    tmp_path,
):
    service = DatasetService(tmp_path)
    case = make_case(service)
    contract = service.create_contract(
        contract_payload(
            scope="plan_quality",
            evidence_mode="candidate_output",
            metrics=["task_success"],
            annotation_bindings=[
                {"endpoint": "judge", "annotation_key": "target", "target_key": "expected"}
            ],
        )
    )
    dataset, review = frozen_annotations(service, case, contract)
    request = LaunchRequest(
        dataset_revision_id=dataset["id"], contract_id=contract["id"], strategies=["base"]
    )
    payload, preview = prepare_launch(tmp_path, request, config())
    assert preview["ready"]
    extras = payload["spec"]["tasks"][0]["example"]["extras"]
    context = EvaluationPipeline(verify_adapter=EpisodeVerifier())._prepare_context(
        "Task", "", ExampleData(extras=extras)
    )
    context.plan = TaskPlan(target_object="red block")
    assert "grader_context" not in context.to_dict()
    assert "red block" not in str(context.task_metadata)
    pipeline = EvaluationPipeline(verify_adapter=EpisodeVerifier())
    assert not pipeline._evaluator_context(context, "other").annotations
    grading = pipeline._evaluator_context(context, "judge")
    assert grading.annotation_review_ids == [review["id"]]
    assert annotation_match(grading, {}).verdict == "pass"
    grading.pipeline["plan"]["target_object"] = "wrong"
    assert annotation_match(grading, {}).verdict == "fail"
    assert payload["annotation_review_ids"] == [review["id"]]


def test_annotation_requires_frozen_labels_not_output_rating_or_case_reference_data(tmp_path):
    service = DatasetService(tmp_path)
    case = make_case(service, reference_data={"target": "secret"})
    contract = service.create_contract(
        contract_payload(
            annotation_bindings=[
                {"endpoint": "judge", "annotation_key": "target", "target_key": "expected"}
            ]
        )
    )
    with pytest.raises(ValueError, match="frozen dataset"):
        prepare_launch(
            tmp_path,
            LaunchRequest(
                case_revision_ids=[case["id"]], contract_id=contract["id"], strategies=["base"]
            ),
            config(),
        )
    empty = {
        "members": [{"case_revision_id": case["id"], "disposition": "included", "review_ids": []}],
        "contract_id": contract["id"],
    }
    with pytest.raises(ValueError, match="Missing"):
        service.resolve_annotations(empty, case["id"], contract["annotation_bindings"])


def test_annotation_grader_cannot_grade_private_labels_or_coerce_booleans_to_numbers():
    context = EvaluatorContext(
        task="test",
        pipeline={"ground_truth": {"label": True}, "plan": {"target_object": True}},
        annotations={"expected": 1},
        annotation_review_ids=["frozen-review"],
    )
    assert annotation_match(context, {"output_path": "ground_truth.label"}).verdict == "unknown"
    assert annotation_match(context, {}).verdict == "fail"


@pytest.mark.asyncio
async def test_metrics_targets_retain_unknown_planned_attempts_and_zero_recovery_denominator():
    stages = await execute([[0.5], [0.5]])
    campaign = {
        "spec": {
            "tasks": [{"id": "case-1"}],
            "strategies": ["base"],
            "seeds": [1, 2],
            "ks": [1, 2],
        },
        "contract": contract_payload(
            campaign_targets=[
                {
                    "metric": "autonomous_completion",
                    "operator": "gte",
                    "threshold": 0.9,
                    "unit": "fraction",
                }
            ]
        ),
    }
    row = {
        "task_id": "case-1",
        "strategy_id": "base",
        "seed": 1,
        "outcome": "pass",
        "execution": "completed",
        "latency_ms": 1,
        "result": {"stages": stages},
    }
    partial = summarize(campaign, [row])
    assert partial["campaign_targets"][0]["status"] == "unknown"
    assert partial["robotics"][0]["metrics"]["autonomous_completion"]["unknown_trials"] == 1
    complete = summarize(campaign, [row, {**copy.deepcopy(row), "seed": 2}])
    assert complete["campaign_targets"][0]["status"] == "met"
    metrics = complete["robotics"][0]["metrics"]
    assert metrics["autonomous_completion"]["value"] == 1
    assert metrics["episode_completion_time"]["value"] == 0.2
    assert metrics["recovery"]["value"] is None
    assert metrics["recovery"]["denominator"] == 0
    assert metrics["episode_completion_time"]["quality"] == "synthetic"


@pytest.mark.parametrize(
    "target",
    [
        {"metric": "task_success", "operator": "gte", "threshold": 95, "unit": "fraction"},
        {"metric": "episode_completion_time", "operator": "lte", "threshold": 3, "unit": "ms"},
        {"metric": "recovery", "operator": "gte", "threshold": float("nan"), "unit": "fraction"},
    ],
)
def test_campaign_targets_reject_unit_and_range_errors(target):
    with pytest.raises(ValueError):
        SuccessContract.model_validate(contract_payload(campaign_targets=[target]))


@pytest.mark.asyncio
async def test_real_campaign_workers_preserve_synthetic_causality_and_archived_evidence(tmp_path):
    from rove.benchmarks.runner import run_campaign
    from rove.benchmarks.store import CampaignStore
    from rove.benchmarks.workflow import assessed_trials, create_campaign

    service = DatasetService(tmp_path)
    case = make_case(service)
    contract = service.create_contract(
        contract_payload(
            campaign_targets=[
                {"metric": "task_success", "operator": "gte", "threshold": 1, "unit": "fraction"}
            ]
        )
    )
    cfg = config()
    cfg.endpoints["wrong-actions"] = cfg.endpoints["actions"].model_copy(
        update={"config": {"actions": [[-0.5], [-0.5]]}}
    )
    cfg.strategies["wrong"] = cfg.strategies["base"].model_copy(update={"act": "wrong-actions"})
    campaign_id, count = create_campaign(
        tmp_path,
        LaunchRequest(
            case_revision_ids=[case["id"]],
            contract_id=contract["id"],
            strategies=["base", "wrong"],
            seeds=[1, 2],
            ks=[1, 2],
            operation_id="causal-world",
        ),
        cfg,
    )
    store = CampaignStore(tmp_path)
    await run_campaign(store, campaign_id)
    campaign = store.get(campaign_id)
    rows, _ = assessed_trials(tmp_path, campaign, store.trials(campaign_id))
    assert count == len(rows) == 4
    assert {r["outcome"] for r in rows if r["strategy_id"] == "base"} == {"pass"}
    assert {r["outcome"] for r in rows if r["strategy_id"] == "wrong"} == {"fail"}
    report = summarize(campaign, rows)
    assert {t["strategy_id"]: t["status"] for t in report["campaign_targets"]} == {
        "base": "met",
        "wrong": "not_met",
    }
    for row in rows:
        events = service.trials.events(row["trial_id"])
        archived = next(e for e in events if e.get("event_type") == "episode.recorded")
        assert archived["evidence_refs"][0]["asset_sha256"]
    assert (
        len(
            {
                next(
                    s
                    for s in r["result"]["stages"]
                    if s["stage"] == "verify" and s["status"] == "completed"
                )["output"]["evaluator_result"]["details"]["reset_id"]
                for r in rows
            }
        )
        == 4
    )


@pytest.mark.asyncio
async def test_frozen_annotations_reach_real_local_grader_without_becoming_new_trials(tmp_path):
    from rove.benchmarks.runner import run_campaign
    from rove.benchmarks.store import CampaignStore
    from rove.benchmarks.workflow import assessed_trials, create_campaign

    service = DatasetService(tmp_path)
    case = make_case(service)
    contract = service.create_contract(
        contract_payload(
            scope="plan_quality",
            evidence_mode="candidate_output",
            metrics=["task_success"],
            annotation_bindings=[
                {"endpoint": "judge", "annotation_key": "target", "target_key": "expected"}
            ],
        )
    )
    dataset, review = frozen_annotations(service, case, contract, value=1)
    cfg = config()
    cfg.strategies["base"].sim = None
    cfg.endpoints["judge"].config = {
        "entrypoint": "rove.evaluators.robotics:annotation_match",
        "revision": "label-v1",
        "output_path": "action.declared_action_dim",
    }
    identity, count = create_campaign(
        tmp_path,
        LaunchRequest(
            dataset_revision_id=dataset["id"],
            contract_id=contract["id"],
            strategies=["base"],
            seeds=[1],
            ks=[1],
            operation_id="label-run",
        ),
        cfg,
    )
    store = CampaignStore(tmp_path)
    await run_campaign(store, identity)
    rows, metadata = assessed_trials(tmp_path, store.get(identity), store.trials(identity))
    assert count == len(rows) == service.trials.count() == 1
    assert rows[0]["outcome"] == "pass"
    assert rows[0]["annotation_review_ids"] == [review["id"]]
    assert metadata["review_ids"] == [review["id"]]
    verification = next(
        s
        for s in rows[0]["result"]["stages"]
        if s["stage"] == "verify" and s["status"] == "completed"
    )
    assert verification["output"]["evaluator_result"]["evidence_refs"] == [f"review:{review['id']}"]


def test_completion_time_cannot_exceed_recorded_duration():
    with pytest.raises(ValueError, match="exceed"):
        EpisodeRecording.model_validate(recording(completion_time_s=3, duration_seconds=2))


@pytest.mark.asyncio
async def test_reset_clocks_are_distinct_even_when_relative_samples_match():
    sim = SyntheticSimAdapter()
    await sim.reset("Reach")
    await sim.step([0.5])
    first = sim.episode_evidence()
    await sim.reset("Reach")
    await sim.step([0.5])
    second = sim.episode_evidence()
    assert first["records"] == second["records"]
    assert first["source_clock_id"] != second["source_clock_id"]
