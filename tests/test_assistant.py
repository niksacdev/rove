"""An assistant proposal never grants the model permission to launch evaluations."""

import io
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from rove.api.assistant import create_assistant_router
from rove.benchmarks.store import CampaignStore
from rove.datasets.service import DatasetService
from rove.models.config import RoveConfig


class ControlledRuntime:
    def __init__(self):
        self.calls = []
        self.results = []
        self.output = {"answer": "Prepared a preview; confirm it to start."}
        self.error = None
        self.instances = []

    def __call__(self, config, tools=()):
        self.instances.append((config, tools))
        return self

    async def health_check(self):
        return True

    async def run_stage(self, stage, image_base64, task, context):
        self.input = (stage, image_base64, task, context)
        tools = {t.name: t for t in self.instances[-1][1]}
        for name, args in self.calls:
            self.results.append(tools[name].handler(args))
        if self.error:
            raise self.error
        return self.output


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.delenv("ROVE_ASSISTANT_ENDPOINT", raising=False)
    monkeypatch.setattr("rove.benchmarks.runner.runtime_fingerprint", lambda: {"version": "test"})
    config = RoveConfig.model_validate(
        {
            "endpoints": {
                "assistant": {
                    "type": "agent",
                    "adapter": "copilot_agent",
                    "config": {
                        "model": "assistant-model",
                        "provider": {
                            "base_url": "https://configured.invalid",
                            "api_key": "do-not-expose",  # pragma: allowlist secret — synthetic redaction fixture
                        },
                    },
                },
                "mock": {
                    "type": "vlm",
                    "adapter": "mock_vlm",
                    "capabilities": ["task_planning", "verification"],
                },
            },
            "strategies": {"baseline": {"plan": "mock", "verify": "mock"}},
        }
    )
    service = DatasetService(tmp_path)
    image = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(image, format="PNG")
    asset = service.trials.save_asset(image.getvalue(), "image/png")
    case = service.import_case({"name": "Pick", "task": "Pick red block"}, asset["sha256"])
    contract = service.create_contract(
        {
            "name": "Plan quality",
            "scope": "plan_quality",
            "evidence_mode": "candidate_output",
            "criteria": [{"id": "target", "description": "Correct target"}],
        }
    )
    runtime = ControlledRuntime()
    launched = []
    now = [100.0]
    app = FastAPI()
    app.include_router(
        create_assistant_router(
            tmp_path,
            launched.append,
            config_loader=lambda: config,
            runtime_factory=runtime,
            endpoint_id="assistant",
            clock=lambda: now[0],
        )
    )
    return {
        "client": TestClient(app),
        "runtime": runtime,
        "config": config,
        "root": tmp_path,
        "service": service,
        "launched": launched,
        "now": now,
        "draft": {
            "case_revision_ids": [case["id"]],
            "contract_id": contract["id"],
            "strategies": ["baseline"],
            "seeds": [1],
            "ks": [1],
        },
    }


def ask(h):
    return h["client"].post("/api/assistant/ask", json={"message": "Prepare my evaluation"})


def propose(h):
    h["runtime"].calls = [("preview_campaign", h["draft"])]
    response = ask(h)
    assert response.status_code == 200, response.text
    return response.json()["proposals"][0]


def confirmation(proposal):
    return {
        "operation_id": proposal["operation_id"],
        "confirmation_token": proposal["confirmation_token"],
        "confirmed": True,
    }


def test_unconfigured_is_optional_and_never_starts_runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("ROVE_ASSISTANT_ENDPOINT", raising=False)
    app = FastAPI()
    app.include_router(create_assistant_router(tmp_path, lambda _: pytest.fail("launch")))
    client = TestClient(app)
    assert client.get("/api/assistant/status").json()["available"] is False
    assert client.post("/api/assistant/ask", json={"message": "hello"}).status_code == 503


def test_preview_is_read_only_and_host_confirmation_is_idempotent(harness):
    h = harness
    proposal = propose(h)
    assert h["launched"] == []
    assert CampaignStore(h["root"]).list() == []
    assert proposal["preview"]["planned_trials"] == 1
    assert proposal["confirmation_token"] not in json.dumps(h["runtime"].results)
    config, tools = h["runtime"].instances[-1]
    assert config.role == "assistant" and config.capture_content is False
    assert all(t.roles == frozenset({"assistant"}) for t in tools)
    assert {t.name for t in tools} == {
        "list_cases",
        "list_datasets",
        "list_contracts",
        "list_strategies",
        "inspect_case",
        "inspect_trial",
        "inspect_campaign",
        "preview_campaign",
    }
    response = h["client"].post("/api/assistant/confirm", json=confirmation(proposal))
    assert response.status_code == 200, response.text
    repeat = h["client"].post("/api/assistant/confirm", json=confirmation(proposal))
    assert repeat.json() == response.json()
    assert h["launched"] == [response.json()["id"]]
    assert len(CampaignStore(h["root"]).list()) == 1


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"confirmed": False}, 422),
        ({"confirmed": "true"}, 422),
        ({"confirmation_token": "wrong" * 8}, 403),
        ({"operation_id": "invented"}, 409),
        ({"request": {"strategies": ["other"]}}, 422),
    ],
)
def test_confirmation_cannot_be_forged_or_overridden(harness, changes, status):
    proposal = propose(harness)
    response = harness["client"].post(
        "/api/assistant/confirm", json={**confirmation(proposal), **changes}
    )
    assert response.status_code == status
    assert harness["launched"] == []
    assert CampaignStore(harness["root"]).list() == []


def test_config_change_requires_another_preview(harness):
    proposal = propose(harness)
    harness["config"].endpoints["mock"].config["model"] = "different-model"
    response = harness["client"].post("/api/assistant/confirm", json=confirmation(proposal))
    assert response.status_code == 409
    assert "changed" in response.text
    assert not harness["launched"]


def test_expired_proposal_does_not_launch(harness):
    proposal = propose(harness)
    harness["now"][0] += 601
    assert (
        harness["client"].post("/api/assistant/confirm", json=confirmation(proposal)).status_code
        == 409
    )
    assert not harness["launched"]


@pytest.mark.parametrize(
    "name,args",
    [
        ("list_cases", {"limit": 51}),
        ("list_cases", {"offset": -1}),
        ("list_cases", {"limit": "1"}),
        ("list_cases", {"path": "/etc/passwd"}),
        ("inspect_case", {"id": "../../outside"}),
        ("inspect_trial", {"id": "https://example.com"}),
        ("list_strategies", {"provider": {"base_url": "https://other.invalid"}}),
    ],
)
def test_tool_argument_bounds_reject_ambient_access(harness, name, args):
    harness["runtime"].calls = [(name, args)]
    assert ask(harness).status_code == 200
    assert "error" in harness["runtime"].results[0]
    assert not harness["launched"]


def test_model_cannot_supply_operation_identity(harness):
    harness["runtime"].calls = [
        ("preview_campaign", {**harness["draft"], "operation_id": "chosen"})
    ]
    response = ask(harness)
    assert response.json()["proposals"] == []
    assert "error" in harness["runtime"].results[0]


def test_tool_budget_errors_and_redaction(harness):
    h = harness
    h["runtime"].calls = [("list_strategies", {})] * 17
    assert ask(h).status_code == 200
    assert h["runtime"].results[-1] == {"error": "Tool call limit reached"}
    assert "do-not-expose" not in json.dumps(h["runtime"].results)
    h["runtime"].calls = [("inspect_case", {"id": "missing"})]
    assert ask(h).status_code == 200
    assert h["runtime"].results[-1] == {"error": "Referenced record is unavailable"}


@pytest.mark.parametrize(
    "output",
    [
        {"answer": "Fine", "proposals": [{"operation_id": "fake"}]},
        {"answer": "Fine", "evidence": [{"url": "javascript:alert(1)"}]},
        {"answer": "x" * 16001},
    ],
)
def test_untrusted_output_cannot_create_links_or_authorizations(harness, output):
    harness["runtime"].output = output
    harness["runtime"].calls = [("preview_campaign", harness["draft"])]
    response = ask(harness)
    assert response.status_code == 502
    assert not harness["launched"]


def test_provider_error_is_safe_and_never_launches(harness):
    harness["runtime"].error = RuntimeError("secret connection token=private-key")
    harness["runtime"].calls = [("preview_campaign", harness["draft"])]
    response = ask(harness)
    assert response.status_code == 502
    assert "private-key" not in response.text
    assert not harness["launched"]
    operation_id = harness["runtime"].results[0]["operation_id"]
    assert (
        harness["client"]
        .post(
            "/api/assistant/confirm",
            json={
                "operation_id": operation_id,
                "confirmation_token": "unknown" * 8,
                "confirmed": True,
            },
        )
        .status_code
        == 409
    )


def test_evidence_links_are_host_attested_and_context_is_typed(harness):
    h = harness
    h["runtime"].calls = [("inspect_case", {"id": h["draft"]["case_revision_ids"][0]})]
    response = ask(h)
    assert response.json()["evidence"] == [
        {"label": "Case revision", "url": "/static/datasets.html"}
    ]
    assert (
        h["client"].post("/api/assistant/ask", json={"message": "hi", "role": "grader"}).status_code
        == 422
    )
    assert (
        h["client"]
        .post("/api/assistant/ask", json={"message": "hi", "context": {"path": "/tmp"}})
        .status_code
        == 422
    )


def test_failed_launch_retries_same_durable_campaign(harness):
    h = harness
    app = FastAPI()
    attempts = []

    def launch(identity):
        attempts.append(identity)
        if len(attempts) == 1:
            raise RuntimeError("queue temporarily unavailable")

    app.include_router(
        create_assistant_router(
            h["root"],
            launch,
            config_loader=lambda: h["config"],
            runtime_factory=h["runtime"],
            endpoint_id="assistant",
        )
    )
    h["client"] = TestClient(app)
    proposal = propose(h)
    assert (
        h["client"].post("/api/assistant/confirm", json=confirmation(proposal)).status_code == 503
    )
    response = h["client"].post("/api/assistant/confirm", json=confirmation(proposal))
    assert response.status_code == 200
    assert attempts == [response.json()["id"]] * 2
    assert len(CampaignStore(h["root"]).list()) == 1


def test_unavailable_runtime_degrades_without_model_call(harness):
    async def unavailable():
        return False

    harness["runtime"].health_check = unavailable
    assert harness["client"].get("/api/assistant/status").json()["available"] is False
    assert ask(harness).status_code == 503
    assert not hasattr(harness["runtime"], "input")


def test_blocked_preview_offers_no_confirmation(harness):
    harness["config"].endpoints["mock"].capabilities = []
    harness["runtime"].calls = [("preview_campaign", harness["draft"])]
    response = ask(harness)
    assert response.json()["proposals"] == []
    assert harness["runtime"].results[0]["preview"]["ready"] is False
    assert not harness["launched"]


def test_tool_record_redaction_and_result_size_bound(harness, monkeypatch):
    monkeypatch.setattr(
        DatasetService,
        "list_cases",
        lambda *a: [
            {
                "task": "robotics",
                "api_key": "private-key",  # pragma: allowlist secret — synthetic redaction fixture
                "image_base64": "aGVsbG8=",
            }
        ],
    )
    harness["runtime"].calls = [("list_cases", {})]
    assert ask(harness).status_code == 200
    result = harness["runtime"].results[-1]
    assert result[0]["api_key"] == "[REDACTED]"
    assert "private-key" not in json.dumps(result)
    assert result[0]["image_base64"]["availability"] == "reference_only"
    monkeypatch.setattr(DatasetService, "list_cases", lambda *a: [{"task": "x" * 64_001}])
    assert ask(harness).status_code == 200
    assert "too large" in harness["runtime"].results[-1]["error"]


def test_trial_and_campaign_reads_use_observed_not_invented_outcomes(harness):
    service = harness["service"]
    identity = service.trials.begin(source="quick", task={}, strategy={}, config={})
    service.trials.finish(identity, status="completed", result={"plan": {"goal": "pick"}})
    harness["runtime"].calls = [("inspect_trial", {"id": identity})]
    assert ask(harness).status_code == 200
    result = harness["runtime"].results[-1]
    assert result["result"] == {"plan": {"goal": "pick"}}
    assert "outcome" not in result
    proposal = propose(harness)
    confirmed = harness["client"].post("/api/assistant/confirm", json=confirmation(proposal)).json()
    harness["runtime"].calls = [("inspect_campaign", {"id": confirmed["id"]})]
    assert ask(harness).status_code == 200
    result = harness["runtime"].results[-1]
    assert result["status"] == "pending"
    assert "summary" in result and "assessments" in result
    assert "api_key" not in json.dumps(result)
