"""Browser transport must preserve the CLI's frozen plans and durable execution."""

import asyncio
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rove.bimanual import api
from rove.evaluation.models import EvaluationConfig


@pytest.fixture
def transport(tmp_path, monkeypatch):
    config = EvaluationConfig(
        strategies={
            "abc-vla": {
                "kind": "abc_vla",
                "label": "ABC model",
                "checkpoint": "/private/model.pt",
                "python": "/private/python",
            },
            "pi05": {
                "kind": "pi05",
                "label": "Adapted Pi",
                "checkpoint": "/private/pi",
                "python": "/private/pi-python",
            },
        },
        environment={"source": "/private/abc", "python": "/private/python"},
    )
    calls = {"launches": [], "prepared": [], "created": [], "trials": [], "cancelled": False}
    monkeypatch.setattr(api, "read_profile", lambda _root: config)

    def validate(value, **_kwargs):
        return {
            "ready": True,
            "issues": [],
            "strategies": [
                {
                    "id": sid,
                    "kind": row["kind"],
                    "label": row["label"],
                    "ready": True,
                    "issues": [],
                    "checkpoint_revision": "a" * 64,
                }
                for sid, row in value.strategies.items()
            ],
        }

    monkeypatch.setattr(api, "validate_profile", validate)

    def prepare(spec, value):
        calls["prepared"].append((spec, value))
        return {"spec": spec.model_dump(), "config": value.model_dump()}

    monkeypatch.setattr(api, "prepare_evaluation", prepare)

    class Store:
        def __init__(self, root):
            assert root == tmp_path

        def create(self, payload):
            calls["created"].append(payload)
            return "campaign-id"

    monkeypatch.setattr(api, "CampaignStore", Store)

    async def run(**kwargs):
        calls["trials"].append(kwargs)
        kwargs["on_trial_created"]("abc-vla", "durable-trial")
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            calls["cancelled"] = True
            raise

    monkeypatch.setattr(api, "run_evaluation_trials", run)
    app = FastAPI()
    app.include_router(api.create_bimanual_router(tmp_path, calls["launches"].append))
    with TestClient(app) as client:
        yield client, config, calls


def request(**changes):
    return {
        "name": "Bottles comparison",
        "strategy_ids": ["abc-vla", "pi05"],
        "reset_seeds": [4, 9],
        "policy_seeds": [0, 1, 2],
        "max_steps": 300,
        **changes,
    }


def preview(client, body):
    response = client.post("/api/bimanual/preview", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_status_omits_operator_paths_and_preview_does_not_launch(transport):
    client, _, calls = transport
    status = client.get("/api/bimanual/config")
    assert status.status_code == 200
    assert "/private" not in status.text
    assert "checkpoint_revision" not in status.text
    plan = preview(client, request())
    assert plan["planned_trials"] == 12
    assert plan["cases"] == 2
    assert plan["attempts_per_case"] == 3
    assert plan["success_criterion"] == {"success_mode": "ever"}
    assert calls["launches"] == calls["created"] == []


@pytest.mark.parametrize(
    "patch",
    [
        {"python": "/bin/sh"},
        {"strategy_ids": ["missing"]},
        {"reset_seeds": [True]},
        {"policy_seeds": [1, 1]},
        {"max_steps": 0},
        {"max_steps": 3541},
        {"reset_seeds": [-1]},
        {"name": " "},
        {"strategy_ids": ["abc-vla", "abc-vla"]},
        {"reset_seeds": list(range(100)), "policy_seeds": list(range(100))},
    ],
)
def test_untrusted_commands_and_invalid_plans_cannot_reach_execution(transport, patch):
    client, _, calls = transport
    assert client.post("/api/bimanual/preview", json=request(**patch)).status_code == 422
    assert calls["launches"] == calls["created"] == calls["trials"] == []


def test_launch_requires_current_preview_and_uses_shared_campaign_runner(transport):
    client, config, calls = transport
    body = request()
    plan = preview(client, body)
    assert client.post("/api/bimanual/campaigns", json=body).status_code == 422
    config.strategies["pi05"]["checkpoint"] = "/private/new-checkpoint"
    stale = client.post(
        "/api/bimanual/campaigns", json={**body, "preview_hash": plan["preview_hash"]}
    )
    assert stale.status_code == 409
    assert calls["created"] == []
    plan = preview(client, body)
    response = client.post(
        "/api/bimanual/campaigns", json={**body, "preview_hash": plan["preview_hash"]}
    )
    assert response.status_code == 202, response.text
    assert calls["launches"] == ["campaign-id"]
    spec, frozen = calls["prepared"][0]
    assert spec.execution == "abc-bimanual"
    assert [case.inputs["reset_seed"] for case in spec.tasks] == [4, 9]
    assert spec.seeds == [0, 1, 2]
    assert frozen.environment["max_steps"] == 300


def test_unready_selected_strategy_cannot_launch_but_ready_subset_can(transport, monkeypatch):
    client, _, calls = transport

    def validate(config, **_):
        return {
            "ready": False,
            "issues": [],
            "strategies": [
                {
                    "id": sid,
                    "ready": sid == "abc-vla",
                    "issues": [] if sid == "abc-vla" else ["Adapted checkpoint missing"],
                }
                for sid in config.strategies
            ],
        }

    monkeypatch.setattr(api, "validate_profile", validate)
    body = request()
    plan = preview(client, body)
    assert not plan["ready"]
    assert (
        client.post(
            "/api/bimanual/campaigns", json={**body, "preview_hash": plan["preview_hash"]}
        ).status_code
        == 409
    )
    body = request(strategy_ids=["abc-vla"])
    plan = preview(client, body)
    assert plan["ready"]
    assert (
        client.post(
            "/api/bimanual/campaigns", json={**body, "preview_hash": plan["preview_hash"]}
        ).status_code
        == 202
    )
    assert len(calls["launches"]) == 1


def test_standalone_ids_are_visible_before_completion_and_cancel_is_awaited(transport):
    client, _, calls = transport
    body = request(strategy_ids=["abc-vla"], reset_seeds=[4], policy_seeds=[8])
    plan = preview(client, body)
    response = client.post(
        "/api/bimanual/trials", json={**body, "preview_hash": plan["preview_hash"]}
    )
    assert response.status_code == 202, response.text
    job = response.json()["id"]
    for _ in range(30):
        state = client.get(f"/api/bimanual/trials/{job}").json()
        if state["trial_ids"]:
            break
        time.sleep(0.01)
    assert state["trial_ids"] == {"abc-vla": "durable-trial"}
    assert state["status"] == "running"
    assert calls["created"] == []
    assert calls["trials"][0]["case"].inputs["reset_seed"] == 4
    assert calls["trials"][0]["seed"] == 8
    stopped = client.post(f"/api/bimanual/trials/{job}/cancel")
    assert stopped.json()["status"] == "cancelled"
    assert calls["cancelled"]
    assert client.get("/api/bimanual/trials/missing").status_code == 404


def test_missing_profile_is_actionable_and_repeated_trial_request_is_rejected(
    transport, monkeypatch
):
    client, _, _ = transport
    body = request()
    plan = preview(client, body)
    assert (
        client.post(
            "/api/bimanual/trials", json={**body, "preview_hash": plan["preview_hash"]}
        ).status_code
        == 422
    )

    def missing(_):
        raise FileNotFoundError()

    monkeypatch.setattr(api, "read_profile", missing)
    assert client.get("/api/bimanual/config").json()["configured"] is False
    response = client.post("/api/bimanual/preview", json=body)
    assert response.status_code == 409
    assert "CLI" in response.json()["detail"]
