"""Exercise actual API, worker and recording failure boundaries."""

from __future__ import annotations

import asyncio
import importlib

import pytest
from httpx import ASGITransport, AsyncClient

from rove.benchmarks.models import CampaignSpec
from rove.benchmarks.runner import prepare, run_campaign
from rove.benchmarks.store import CampaignStore
from rove.models.config import RoveConfig
from rove.trials.store import TrialStore


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    module = importlib.import_module("rove.api.app")
    monkeypatch.setattr(module, "_trial_root", tmp_path / "journal")
    monkeypatch.setattr(module, "_history_path", tmp_path / "history.jsonl")
    return module


@pytest.mark.parametrize("strategies", ["mock", ""])
async def test_quick_trial_is_recorded_before_execution_and_reopens(app_module, strategies):
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://localhost"
    ) as client:
        response = await client.post(
            "/api/evaluate",
            data={"task": "pick bracket", "strategy_ids": strategies},
            files={"image": ("case.png", b"test-image", "image/png")},
        )
        assert response.status_code == 202
        trial_id = next(iter(response.json()["trial_ids"].values()))
        before = app_module.trial_store().get(trial_id)
        assert before["status"] == "running"
        assert before["snapshot"]["provenance_status"] == "captured_before_execution"
        await asyncio.gather(*list(app_module._background_tasks))
        reopened = TrialStore(app_module._trial_root)
        assert reopened.get(trial_id)["status"] == "completed"
        assert reopened.get(trial_id)["result"] is not None
        assert reopened.events(trial_id)
        app_module._evaluations.clear()
        detail = await client.get(f"/api/trials/{trial_id}")
        assert detail.status_code == 200
        assert detail.json()["snapshot"] == before["snapshot"]
        digest = detail.json()["task"]["image_asset"]["sha256"]
        asset = await client.get(f"/api/trial-assets/{digest}")
        assert asset.content == b"test-image"
        assert asset.headers["x-content-type-options"] == "nosniff"
        assert (await client.get("/api/trials?limit=0")).status_code == 422
        assert (await client.get("/api/trials/missing")).status_code == 404
        assert (await client.get(f"/data/{digest}")).status_code == 404


async def test_failed_recorder_does_not_run_models(app_module, monkeypatch):
    def broken(*_args, **_kwargs):
        raise OSError("recorder unavailable")

    monkeypatch.setattr(TrialStore, "begin", broken)
    called = False

    async def execute(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(app_module, "_run_multi_strategy", execute)
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app, raise_app_exceptions=False),
        base_url="http://localhost",
    ) as client:
        response = await client.post(
            "/api/evaluate",
            data={"task": "pick", "strategy_ids": "mock"},
            files={"image": ("case.png", b"image", "image/png")},
        )
    assert response.status_code == 500
    assert not called


async def test_cancelled_quick_run_is_retained(app_module):
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://localhost"
    ) as client:
        response = await client.post(
            "/api/evaluate",
            data={"task": "pick", "strategy_ids": "mock"},
            files={"image": ("case.png", b"image", "image/png")},
        )
        trial_id = next(iter(response.json()["trial_ids"].values()))
        await asyncio.sleep(0.01)
        tasks = list(app_module._background_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert app_module.trial_store().get(trial_id)["status"] == "cancelled"


async def test_real_campaign_worker_records_events_before_terminal(tmp_path, mock_image_base64):
    config = RoveConfig.model_validate(
        {
            "endpoints": {
                "m": {
                    "type": "vlm",
                    "adapter": "mock_vlm",
                    "capabilities": ["scene_analysis", "verification"],
                    "config": {"mock_latency_ms": [0, 0]},
                }
            },
            "strategies": {"s": {"perceive": "m", "verify": "m"}},
        }
    )
    spec = CampaignSpec(
        name="Recorded",
        suite_version="v1",
        revision="base",
        strategies=["s"],
        tasks=[{"id": "pick", "task": "pick", "image_base64": mock_image_base64}],
        seeds=[1],
        ks=[1],
        timeout_s=20,
    )
    campaigns = CampaignStore(tmp_path)
    campaign_id = campaigns.create(prepare(spec, config))
    await run_campaign(campaigns, campaign_id)
    record = campaigns.trials(campaign_id)[0]
    assert record["execution"] == "completed"
    journal = TrialStore(tmp_path)
    trial = journal.get(record["trial_id"])
    assert trial["campaign_id"] == campaign_id
    assert trial["status"] == "completed"
    assert any(event["event_type"] == "strategy_queued" for event in journal.events(trial["id"]))
    assert trial["result"]["outcome"] == record["outcome"]


async def test_candidate_context_excludes_grading_labels(monkeypatch):
    from rove.adapters.copilot_agent import CopilotAgentAdapter
    from rove.runtime.copilot import CopilotRuntime

    observed = []

    async def run(self, stage, image, task, context):
        observed.append((self.config.role, context))
        return {}

    monkeypatch.setattr(CopilotRuntime, "run_stage", run)
    adapter = CopilotAgentAdapter("test", {"provider": {"base_url": "http://localhost:9999"}})
    context = {
        "ground_truth": "private",
        "scene": {"objects": []},
        "task_metadata": {"expected_subtasks": ["private"], "constraints": ["avoid glass"]},
    }
    await adapter.run_stage("plan", "", "pick", context)
    await adapter.run_stage("verify", "", "pick", context)
    assert observed[0] == (
        "candidate",
        {"scene": {"objects": []}, "task_metadata": {"constraints": ["avoid glass"]}},
    )
    assert observed[1] == ("grader", context)
    assert context["ground_truth"] == "private"


async def test_campaign_crash_reconciliation_includes_completed_and_orphan(
    tmp_path, mock_image_base64
):
    config = RoveConfig.model_validate(
        {
            "endpoints": {
                "m": {
                    "type": "vlm",
                    "adapter": "mock_vlm",
                    "capabilities": ["scene_analysis", "verification"],
                }
            },
            "strategies": {"s": {"perceive": "m", "verify": "m"}},
        }
    )
    spec = CampaignSpec(
        name="Recovery",
        suite_version="v1",
        revision="base",
        strategies=["s"],
        tasks=[{"id": "pick", "task": "pick", "image_base64": mock_image_base64}],
        seeds=[1],
        ks=[1],
        timeout_s=20,
    )
    campaigns, journal = CampaignStore(tmp_path), TrialStore(tmp_path)
    campaign_id = campaigns.create(prepare(spec, config))
    first = journal.begin(
        source="campaign", campaign_id=campaign_id, task={}, strategy={}, config={}
    )
    orphan = journal.begin(
        source="campaign", campaign_id=campaign_id, task={}, strategy={}, config={}
    )
    campaigns.save_trial(
        campaign_id,
        {
            "task_id": "pick",
            "strategy_id": "s",
            "seed": 1,
            "trial_id": first,
            "execution": "completed",
            "outcome": "fail",
        },
    )
    campaigns.status(campaign_id, "completed")
    await run_campaign(campaigns, campaign_id)
    assert journal.get(first)["status"] == "completed"
    assert journal.get(first)["result"]["outcome"] == "fail"
    assert journal.get(orphan)["status"] == "interrupted"
    assert len(campaigns.trials(campaign_id)) == 1


async def test_finished_strategy_survives_sibling_cancellation(
    tmp_path, mock_strategy, monkeypatch
):
    from rove.adapters.registry import AdapterRegistry
    from rove.orchestrator.run_manager import RunManager

    journal = TrialStore(tmp_path)
    fast = mock_strategy.model_copy(update={"id": "fast"})
    slow = mock_strategy.model_copy(update={"id": "slow"})
    ids = {
        s.id: journal.begin(source="quick", task={}, strategy={"id": s.id}, config={})
        for s in (fast, slow)
    }
    manager = RunManager(AdapterRegistry())
    finished = asyncio.Event()

    async def execute(strategy, task, image, results, event, example):
        if strategy.id == "slow":
            await asyncio.Event().wait()
        results.append({"strategy_id": strategy.id, "outcome": "fail", "stages": []})
        finished.set()

    async def ignore(*_args):
        pass

    monkeypatch.setattr(manager, "_run_strategy", execute)
    operation = asyncio.create_task(
        manager.run_strategies(
            [fast, slow],
            "pick",
            "",
            ignore,
            trial_contexts={sid: (journal, tid) for sid, tid in ids.items()},
        )
    )
    await finished.wait()
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert journal.get(ids["fast"])["status"] == "completed"
    assert journal.get(ids["fast"])["result"]["outcome"] == "fail"


async def test_cancellation_before_task_starts_is_recorded(app_module):
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://localhost"
    ) as client:
        response = await client.post(
            "/api/evaluate",
            data={"task": "pick", "strategy_ids": "mock"},
            files={"image": ("case.png", b"image", "image/png")},
        )
        trial_id = next(iter(response.json()["trial_ids"].values()))
        tasks = list(app_module._background_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert app_module.trial_store().get(trial_id)["status"] == "cancelled"


async def test_external_verifier_source_changes_quick_snapshot(app_module, tmp_path, monkeypatch):
    module = tmp_path / "external_grader.py"
    module.write_text("def grade(context): return {}\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    config = RoveConfig.model_validate(
        {
            "endpoints": {
                "m": {"type": "vlm", "adapter": "mock_vlm", "capabilities": ["scene_analysis"]},
                "v": {
                    "type": "verifier",
                    "adapter": "local_verifier",
                    "capabilities": ["verification"],
                    "config": {"entrypoint": "external_grader:grade", "revision": "v1"},
                },
            },
            "strategies": {"s": {"perceive": "m", "verify": "v"}},
        }
    )
    monkeypatch.setattr(app_module, "load_config", lambda: config)
    snapshots = []
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://localhost"
    ) as client:
        for body in (
            "def grade(context): return {}\n",
            "def grade(context): return {'verdict':'fail'}\n",
        ):
            module.write_text(body)
            response = await client.post(
                "/api/evaluate",
                data={"task": "pick", "strategy_ids": "s"},
                files={"image": ("case.png", b"image", "image/png")},
            )
            trial_id = response.json()["trial_ids"]["s"]
            snapshots.append(app_module.trial_store().get(trial_id)["snapshot"])
            tasks = list(app_module._background_tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    assert snapshots[0]["strategy"] == snapshots[1]["strategy"]
    assert snapshots[0]["config"]["local_evaluators"] != snapshots[1]["config"]["local_evaluators"]
    assert snapshots[0]["sha256"] != snapshots[1]["sha256"]


def test_unresolved_assessment_preserves_completed_execution():
    from rove.benchmarks.runner import trial_status

    assert trial_status("unresolved_evidence") == "completed"
    assert trial_status("invalid_verdict") == "completed"
    assert trial_status("error") == "error"
    assert trial_status("timeout") == "timeout"
