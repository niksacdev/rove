"""Real subprocess/storage integration; the child is synthetic, never model evidence."""

import asyncio
import copy
import json
import sys
from types import SimpleNamespace

import pytest

from rove.bimanual import adapter as adapter_module
from rove.bimanual.adapter import ABCBimanualEvaluation
from rove.bimanual.config import Environment, Strategy
from rove.evaluation import worker
from rove.evaluation.context import EvaluationContext
from rove.evaluation.models import EvaluationCase
from rove.trials.store import TrialStore

# Execute a separate Python process through the production adapter's line protocol.
# This exercises its actual stdout parsing, file boundaries, teardown and journal.
FAKE_RUNTIME = r"""
import json
import sys
from pathlib import Path

request = json.loads(Path(sys.argv[1]).read_text())
directory = Path(sys.argv[1]).parent
mode = request['strategy']['label']
steps = request['environment']['max_steps']
scene_seed = request['case']['inputs']['reset_seed']

def emit(**event):
    print(json.dumps(event), flush=True)

def artifact(name, filename, media_type='application/json'):
    emit(type='artifact', id=name, file=filename, media_type=media_type)

emit(type='progress', stage='policy', phase='started',
     span_id='policy-call-1', data={'replan': 1})
(directory / 'initial.json').write_text(json.dumps({'reset_seed':scene_seed,
    'state': {'qpos': [0.0] * 14, 'qvel': [0.0] * 14, 'time':0.0}}))
if mode == 'traversal':
    artifact('episode.initial_state', '../outside.json')
    sys.exit(0)
if mode == 'namespace':
    artifact('candidate.output', 'initial.json')
    sys.exit(0)
if mode == 'symlink':
    (directory / 'linked.json').symlink_to(Path(sys.argv[0]).resolve())
    artifact('episode.initial_state', 'linked.json')
    sys.exit(0)
if mode != 'missing_initial':
    artifact('episode.initial_state', 'initial.json')

with (directory / 'trajectory.jsonl').open('w') as stream:
    for step in range(steps):
        stream.write(json.dumps({'step': step + 1, 'action': [0.0] * 14,
            'state_before': [0.0] * 14, 'time': (step + 1) * 0.034,
            'evaluation': {'task_success':step == 0}}) + '\n')
if mode == 'child_error':
    emit(type='error', error_type='RuntimeError', message='private provider message')
    sys.exit(1)
artifact('episode.trajectory', 'trajectory.jsonl', 'application/x-ndjson')
emit(type='progress', stage='policy', phase='completed',
     span_id='policy-call-1', data={'replan': 1, 'latency_s':0.01})
summary = {
    'scope': 'abc_simulation', 'episode_steps': steps,
    'simulated_time_s': steps * 0.034, 'replans':2,
    'gripper_out_of_range_steps':0,
    'inference_latency_mean_s':0.01, 'inference_latency_p95_s':0.01,
    'inference_wall_s':0.02, 'task_success':True, 'final_success':False,
    'reset_seed':scene_seed, 'policy_seed':request['seed'],
    'initial_state_digest':'a' * 64,
    'final_evaluation':{'task_success':False},
}
if mode == 'wrong_scene_seed':
    summary['reset_seed'] += 1
if mode == 'wrong_policy_seed':
    summary['policy_seed'] += 1
emit(type='result', summary=summary)
if mode == 'duplicate_result':
    emit(type='result', summary=summary)
"""


@pytest.fixture
def harness(tmp_path, monkeypatch):
    script = tmp_path / "synthetic_runtime.py"
    script.write_text(FAKE_RUNTIME)
    original_spawn = asyncio.create_subprocess_exec
    launches = []

    async def spawn(*args, **kwargs):
        launches.append(args)
        return await original_spawn(sys.executable, str(script), args[-1], **kwargs)

    monkeypatch.setattr(adapter_module.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(adapter_module, "validate_profile", lambda _: {"ready": True})

    def create(mode="valid", success_mode="ever"):
        environment = Environment(
            source=str(tmp_path),
            python=sys.executable,
            max_steps=30,
            success_mode=success_mode,
        )
        strategy = Strategy(
            kind="abc_vla", label=mode, checkpoint=str(tmp_path), python=sys.executable
        ).model_dump()
        case = EvaluationCase(
            id="bottles-seed-11",
            task=environment.prompt,
            inputs={"task": environment.task, "reset_seed": 11},
        )
        store = TrialStore(tmp_path / "trials")
        config = {
            "environment": environment.model_dump(),
            "strategies": {"abc": strategy},
            "grading": {"success_mode": success_mode},
        }
        trial_id = store.begin(
            source="campaign",
            task=case.model_dump(),
            strategy=strategy,
            config=config,
            seed=7,
        )
        context = EvaluationContext(store, trial_id, environment.model_dump())
        adapter = ABCBimanualEvaluation()
        adapter.bind_context(context)
        identity = {"name": "abc-bimanual", "revision": adapter.revision}
        monkeypatch.setattr(worker, "resolve", lambda _: (ABCBimanualEvaluation, identity))
        return SimpleNamespace(
            adapter=adapter,
            context=context,
            case=case,
            strategy=strategy,
            store=store,
            trial_id=trial_id,
            launches=launches,
            request={
                "execution": {},
                "executor_identity": identity,
                "task": case.model_dump(),
                "trial_root": str(tmp_path / "trials"),
                "trial_id": trial_id,
                "config": config,
                "strategy_id": "abc",
                "seed": 7,
            },
        )

    return create


@pytest.mark.parametrize("mode,verdict", [("ever", "pass"), ("final", "fail")])
def test_real_child_to_durable_measurements_and_trace_spans(harness, mode, verdict):
    run = harness(success_mode=mode)
    result = asyncio.run(worker.execute(run.request))
    run.store.finish(run.trial_id, status="completed", result=result)
    reopened = TrialStore(run.store.root)
    assert reopened.get(run.trial_id)["result"]["outcome"] == verdict
    assessment = result["result"]["assessment"]
    assert assessment["evidence_quality"] == "observed"
    assert assessment["details"]["physical_robot_success"] == "not_measured"
    measures = {item["name"]: item for item in assessment["measurements"]}
    assert measures["episode_success"]["value"] == 1
    assert measures["final_success"]["value"] == 0
    assert measures["simulated_time_s"]["value"] == pytest.approx(1.02)
    assert measures["inference_latency_mean_s"]["unit"] == "s"
    evidence = {item["id"]: item for item in reopened.evidence(run.trial_id)}
    assert set(evidence) >= {
        "episode.initial_state",
        "episode.trajectory",
        "episode.summary",
        "candidate.output",
        "assessment.output",
        "case.reference",
    }
    assert all(item["availability"] == "available" for item in evidence.values())
    trajectory = reopened.asset_path(evidence["episode.trajectory"]["asset_sha256"])
    assert len(trajectory.read_text().splitlines()) == 30
    summary = reopened.asset_path(evidence["episode.summary"]["asset_sha256"])
    assert json.loads(summary.read_text())["reset_seed"] == 11
    events = reopened.events(run.trial_id)
    policy_events = [event for event in events if event["stage"] == "policy"]
    assert [event["event_type"] for event in policy_events] == [
        "evaluation.started",
        "evaluation.completed",
    ]
    assert {event["span_id"] for event in policy_events} == {"policy-call-1"}
    assert all(event["source_clock_id"] and event["time_unit"] == "ns" for event in events)
    assert {event["stage"] for event in events} >= {"preflight", "candidate", "assessment"}
    assert len(run.launches) == 1


@pytest.mark.parametrize(
    "mode,match",
    [
        ("traversal", "within its episode directory"),
        ("symlink", "escaped its episode directory"),
        ("namespace", "episode namespace"),
        ("duplicate_result", "Duplicate episode result"),
        ("wrong_scene_seed", "different scene or policy seeds"),
        ("wrong_policy_seed", "different scene or policy seeds"),
        ("missing_initial", "evidence is missing"),
    ],
)
def test_invalid_child_protocol_never_produces_an_assessment(harness, mode, match):
    run = harness(mode)
    with pytest.raises(ValueError, match=match):
        asyncio.run(worker.execute(run.request))
    assert "assessment.output" not in run.context.evidence_ids
    assert "evaluation.error" in run.context.evidence_ids
    assert run.store.get(run.trial_id)["result"] is None


@pytest.mark.parametrize("seed", [True, -1, 2**32, "11", None])
def test_malformed_scene_seed_cannot_start_child(harness, seed):
    run = harness()
    run.case.inputs["reset_seed"] = seed
    with pytest.raises(ValueError, match="unsigned scene seed"):
        asyncio.run(run.adapter.predict(run.case.candidate(), run.strategy, 7))
    assert not run.launches


def test_grade_rejects_modified_output_and_corrupted_saved_evidence(harness):
    run = harness()
    output = asyncio.run(run.adapter.predict(run.case.candidate(), run.strategy, 7))
    tampered = copy.deepcopy(output)
    tampered["task_success"] = False
    with pytest.raises(ValueError, match="differs from the recorded"):
        asyncio.run(run.adapter.grade(run.case.candidate(), tampered, {}, {}))
    run.store.asset_path(run.context.evidence_digest("episode.trajectory")).write_bytes(b"changed")
    with pytest.raises(ValueError, match="evidence is missing"):
        asyncio.run(run.adapter.grade(run.case.candidate(), output, {}, {}))


def test_child_failure_preserves_partial_evidence_without_a_robot_verdict(harness):
    run = harness("child_error")
    with pytest.raises(ValueError, match="failed before producing outcome evidence"):
        asyncio.run(worker.execute(run.request))
    run.store.finish(run.trial_id, status="error", error="Evaluation stage did not complete")
    reopened = TrialStore(run.store.root)
    record = reopened.get(run.trial_id)
    assert record["status"] == "error"
    assert record["result"] is None  # Failure to execute is not a measured task failure.
    ids = {item["id"] for item in reopened.evidence(run.trial_id)}
    assert ids == {"episode.initial_state", "episode.partial_trajectory", "evaluation.error"}
    events = reopened.events(run.trial_id)
    assert any(
        event["stage"] == "episode" and event["event_type"] == "evaluation.failed"
        for event in events
    )
    assert "private provider message" not in json.dumps(events)


def test_runtime_mutation_during_execution_prevents_grading(harness, monkeypatch):
    run = harness()
    responses = iter([{"ready": True}, {"ready": False}])
    monkeypatch.setattr(adapter_module, "validate_profile", lambda _: next(responses))
    with pytest.raises(ValueError, match="changed during execution"):
        asyncio.run(worker.execute(run.request))
    assert "episode.trajectory" in run.context.evidence_ids
    assert "episode.summary" not in run.context.evidence_ids
    assert "assessment.output" not in run.context.evidence_ids
