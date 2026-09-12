"""Independent statistical checks and campaign lifecycle regression tests."""

from __future__ import annotations

import asyncio
import itertools
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from rove.benchmarks.api import create_router
from rove.benchmarks.metrics import estimate, summarize, wilson
from rove.benchmarks.models import CampaignSpec
from rove.benchmarks.report import public_campaign, report_data, to_csv, to_html
from rove.benchmarks.runner import campaign_lock, prepare, run_attempt, run_campaign
from rove.benchmarks.store import CampaignStore
from rove.benchmarks.worker import classify
from rove.models.config import RoveConfig
from rove.orchestrator.pipeline import EvaluationPipeline


@pytest.fixture
def spec(mock_image_base64):
    return CampaignSpec(
        name="Test",
        suite_version="v1",
        revision="base",
        strategies=["a", "b"],
        tasks=[
            {"id": "pick", "task": "Pick up the bracket", "image_base64": mock_image_base64},
            {"id": "place", "task": "Place the bracket", "image_base64": mock_image_base64},
        ],
        seeds=[1, 2, 3],
        ks=[1, 2, 4],
        timeout_s=10,
    )


@pytest.fixture
def config():
    return RoveConfig.model_validate(
        {
            "endpoints": {
                "mock": {
                    "type": "vlm",
                    "adapter": "mock_vlm",
                    "capabilities": ["scene_analysis", "verification"],
                    "config": {"mock_quality": 0.5, "mock_latency_ms": [0, 0]},
                }
            },
            "strategies": {sid: {"perceive": "mock", "verify": "mock"} for sid in ("a", "b")},
        }
    )


@pytest.mark.parametrize("n", range(1, 9))
def test_estimates_match_exhaustive_subsets(n):
    for c in range(n + 1):
        population = [True] * c + [False] * (n - c)
        for k in range(1, n + 1):
            subsets = list(itertools.combinations(population, k))
            assert estimate(n, c, k, "pass_at_k") == pytest.approx(
                sum(any(s) for s in subsets) / len(subsets)
            )
            assert estimate(n, c, k, "pass_pow_k") == pytest.approx(
                sum(all(s) for s in subsets) / len(subsets)
            )
    assert estimate(n, n, n + 1, "pass_at_k") is None


def test_unknowns_bound_scores_without_dropping_attempts(spec, config):
    campaign = prepare(spec, config)
    trials = [
        {
            "task_id": "pick",
            "strategy_id": "a",
            "seed": 1,
            "outcome": "pass",
            "execution": "completed",
        },
        {
            "task_id": "pick",
            "strategy_id": "a",
            "seed": 2,
            "outcome": "fail",
            "execution": "completed",
        },
        {
            "task_id": "pick",
            "strategy_id": "a",
            "seed": 3,
            "outcome": "unknown",
            "execution": "timeout",
        },
    ]
    summary = summarize(campaign, trials)
    row = summary["tasks"][0]
    assert row["coverage"] == 2 / 3
    assert row["pass_at_k"][0] == {
        "k": 1,
        "value": None,
        "lower": pytest.approx(1 / 3),
        "upper": pytest.approx(2 / 3),
    }
    assert row["pass_at_k"][2]["lower"] is None
    assert row["pass_at_1_wilson_95"] is None
    assert summary["strategies"][0]["pass_at_k"][0]["value"] is None
    assert summary["portfolio"]["coverage"] == 0.5
    assert summary["portfolio"]["attempt_budget_per_task"] == 6


def test_wilson_uncertainty_at_extremes():
    assert wilson(3, 3)[0] < 0.5
    assert wilson(0, 3)[1] > 0.5
    assert wilson(80, 100) == pytest.approx((0.71117083, 0.86663307))


def test_config_validation(spec):
    for update in (
        {"seeds": [1, 1]},
        {"strategies": ["a", "a"]},
        {"ks": [0]},
        {"timeout_s": float("nan")},
        {"tasks": []},
    ):
        with pytest.raises(ValueError):
            CampaignSpec.model_validate({**spec.model_dump(), **update})


async def test_resume_does_not_replay_attempts_and_freezes_config(
    tmp_path, spec, config, monkeypatch
):
    store = CampaignStore(tmp_path)
    campaign_id = store.create(prepare(spec, config))
    calls = []

    async def attempt(request, timeout):
        calls.append(request)
        return {"outcome": "pass", "execution": "completed", "latency_ms": 1}

    monkeypatch.setattr("rove.benchmarks.runner.run_attempt", attempt)
    store.save_trial(
        campaign_id,
        {
            "task_id": "pick",
            "strategy_id": "a",
            "seed": 1,
            "outcome": "unknown",
            "execution": "running",
        },
    )
    config.endpoints["mock"].config["mock_quality"] = 0
    await run_campaign(store, campaign_id)
    assert len(calls) == 11
    assert calls[0]["config"]["endpoints"]["mock"]["config"]["mock_quality"] == 0.5
    assert store.trials(campaign_id)[0]["execution"] == "interrupted"
    await run_campaign(store, campaign_id)
    assert len(calls) == 11
    assert len(store.trials(campaign_id)) == 12


async def test_cancel_retains_unknown_and_resume_skips_cancelled_attempt(
    tmp_path, spec, config, monkeypatch
):
    store = CampaignStore(tmp_path)
    campaign_id = store.create(prepare(spec, config))
    entered = asyncio.Event()

    async def attempt(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("rove.benchmarks.runner.run_attempt", attempt)
    task = asyncio.create_task(run_campaign(store, campaign_id))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.get(campaign_id)["status"] == "cancelled"
    assert store.trials(campaign_id)[0]["execution"] == "cancelled"
    assert store.trials(campaign_id)[0]["outcome"] == "unknown"


def test_campaign_lock_excludes_second_runner(tmp_path, spec, config):
    store = CampaignStore(tmp_path)
    campaign_id = store.create(prepare(spec, config))
    with (
        campaign_lock(store, campaign_id),
        pytest.raises(ValueError, match="already running"),
        campaign_lock(store, campaign_id),
    ):
        pass


async def test_runtime_drift_blocks_resume(tmp_path, spec, config, monkeypatch):
    store = CampaignStore(tmp_path)
    campaign_id = store.create(prepare(spec, config))
    monkeypatch.setattr("rove.benchmarks.runner.runtime_fingerprint", lambda: {})
    with pytest.raises(ValueError, match="Runtime changed"):
        await run_campaign(store, campaign_id)


async def test_real_worker_seed_reproducibility_and_timeout(spec, config):
    payload = prepare(spec, config)
    request = {
        "config": payload["config"],
        "task": spec.tasks[0].model_dump(),
        "seed": 25,
        "strategy_id": "a",
    }
    first = await run_attempt(request, 10)
    second = await run_attempt(request, 10)
    assert first["execution"] == second["execution"] == "completed"
    assert first["outcome"] == second["outcome"]
    assert first["result"]["stages"][-1]["output"] == second["result"]["stages"][-1]["output"]
    assert first["seed_support"] == {"mock": "applied"}
    timed_out = await run_attempt(request, 0.001)
    assert timed_out == {"execution": "timeout", "outcome": "unknown"}


@pytest.mark.parametrize(
    "response",
    [SimpleNamespace(output_text=""), SimpleNamespace(output_text="this is not a verdict")],
)
def test_parse_failure_is_unknown_not_task_failure(response):
    verdict = EvaluationPipeline()._parse_verify_response(response)
    assert not verdict.verdict_valid
    result = classify(
        {"stages": [{"stage": "verify", "status": "completed", "output": verdict.model_dump()}]}
    )
    assert result == {"outcome": "unknown", "execution": "invalid_verdict"}


def test_reports_escape_evidence_and_exclude_secrets_and_changed_evaluators(tmp_path, spec, config):
    spec.name = '</script><script>alert("unsafe")</script>'
    config.endpoints["mock"].config["api_key"] = (
        "private-value-for-test"  # pragma: allowlist secret
    )
    store = CampaignStore(tmp_path)
    first_id = store.create(prepare(spec, config))
    campaign = store.get(first_id)
    config.endpoints["mock"].config["mock_quality"] = 0.9
    second_id = store.create(prepare(spec, config))
    data = report_data(campaign, [], [(campaign, []), (store.get(second_id), [])])
    assert data["excluded_history_series"] == 2
    assert len(data["trends"]) == 2
    assert "private-value-for-test" not in json.dumps(data)
    assert "image_base64" not in json.dumps(public_campaign(campaign))
    rendered = to_html(data)
    assert spec.name not in rendered
    assert "plotly" in rendered.lower()
    assert "cdn.plot.ly" not in rendered.split("</head>")[1]
    assert "Task outcomes" in rendered
    assert "Unresolved" not in to_csv(data)


async def test_api_validation_export_and_missing_campaign(tmp_path, spec, config, monkeypatch):
    app = FastAPI()
    app.include_router(create_router(tmp_path))
    monkeypatch.setattr("rove.benchmarks.api.load_config", lambda: config)

    async def attempt(*args):
        return {"outcome": "pass", "execution": "completed"}

    monkeypatch.setattr("rove.benchmarks.runner.run_attempt", attempt)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        assert (await client.get("/api/campaigns/missing")).status_code == 404
        invalid = spec.model_dump()
        invalid["strategies"] = ["missing"]
        assert (await client.post("/api/campaigns", json=invalid)).status_code == 422
        response = await client.post("/api/campaigns", json=spec.model_dump())
        assert response.status_code == 202
        campaign_id = response.json()["id"]
        await asyncio.sleep(0.05)
        detail = (await client.get(f"/api/campaigns/{campaign_id}")).json()
        assert detail["campaign"]["status"] == "completed"
        assert detail["summary"]["planned_trials"] == 12
        assert "config" not in detail["campaign"]
        report = await client.get(f"/api/campaigns/{campaign_id}/report?format=csv")
        assert report.status_code == 200
        assert len(report.text.splitlines()) == 13
        assert (await client.post(f"/api/campaigns/{campaign_id}/cancel")).status_code == 409
