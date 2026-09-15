"""Saved assistant choice is shared, credential-free, and distinct from inference health."""

import json
import stat
from concurrent.futures import ThreadPoolExecutor
from typing import ClassVar

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rove.api.assistant import create_assistant_router
from rove.benchmarks.insights import CampaignInsights
from rove.models.config import RoveConfig
from rove.runtime.assistant import assistant_selection, save_assistant_selection


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("ROVE_ASSISTANT_ENDPOINT", raising=False)
    monkeypatch.setattr("rove.runtime.assistant.active_config_path", lambda: tmp_path / "rove.yaml")
    private = "private-test-provider-value"
    config = RoveConfig.model_validate(
        {
            "endpoints": {
                "first": {
                    "type": "agent",
                    "adapter": "copilot_agent",
                    "display_name": "First",
                    "config": {
                        "model": "first-model",
                        "provider": {
                            "base_url": f"https://user:{private}@provider.invalid/private-path?key={private}",
                            "api_key": private,
                        },
                    },
                },
                "second": {
                    "type": "agent",
                    "adapter": "copilot_agent",
                    "config": {
                        "model": "second-model",
                        "provider": {"base_url": "http://127.0.0.1:1234/v1"},
                    },
                },
                "mock": {"type": "vlm", "adapter": "mock_vlm"},
            }
        }
    )
    calls = []

    class Runtime:
        output: ClassVar = {"connection": "ok", "_runtime": {}}
        error = None
        healthy = True

        def __init__(self, settings, tools=()):
            self.settings = settings
            self.tools = tools

        async def health_check(self):
            return self.healthy

        async def run_stage(self, stage, image, task, context):
            calls.append((self.settings, self.tools, stage, image, task, context))
            if self.error:
                raise self.error
            return self.output

    def client(override=None):
        app = FastAPI()
        app.include_router(
            create_assistant_router(
                tmp_path,
                lambda _: None,
                config_loader=lambda: config,
                runtime_factory=Runtime,
                endpoint_id=override,
            )
        )
        return TestClient(app)

    return tmp_path, config, Runtime, calls, client, private


def test_saved_selection_survives_new_router_and_changes_insight_model(setup):
    root, config, _, calls, client, private = setup
    initial = client().get("/api/assistant/settings").json()
    assert initial["configured"] and initial["selection_source"] == "default"
    assert initial["selection"] == {"provider": "copilot", "model": ""}
    assert not (root / "assistant-settings.sqlite3").exists()
    assert {e["id"] for e in initial["endpoints"]} == {"first", "second"}
    assert initial["endpoints"][0]["provider_base_url"] == "https://provider.invalid"
    assert private not in json.dumps(initial) and "private-path" not in json.dumps(initial)
    saved = client().put("/api/assistant/settings", json={"endpoint_id": "first"})
    assert saved.status_code == 200 and saved.json()["configured"]
    assert client().get("/api/assistant/settings").json()["effective_endpoint_id"] == "first"
    assert stat.S_IMODE((root / "assistant-settings.sqlite3").stat().st_mode) == 0o600
    insights = CampaignInsights(root, config_loader=lambda: config)
    first, fingerprint, _ = insights.settings("Explain")
    assert first.model == "first-model"
    assert (
        client().put("/api/assistant/settings", json={"endpoint_id": "second"}).status_code == 200
    )
    second, changed, _ = insights.settings("Explain")
    assert second.model == "second-model" and changed != fingerprint
    assert not calls  # Saving and inspecting config never performs inference.
    assert (
        client().put("/api/assistant/settings", json={"endpoint_id": None}).json()["configured"]
        is False
    )
    assert insights.settings("Explain")[0] is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"endpoint_id": "missing"},
        {"endpoint_id": "mock"},
        {"endpoint_id": 123},
        {"endpoint_id": "first", "api_key": "unexpected"},
    ],
)
def test_invalid_selection_does_not_create_storage(setup, body):
    root, _, _, _, client, _ = setup
    assert client().put("/api/assistant/settings", json=body).status_code == 422
    assert not (root / "assistant-settings.sqlite3").exists()


def test_overrides_are_visible_read_only_and_take_precedence(setup, monkeypatch):
    _, _, _, _, client, _ = setup
    client().put("/api/assistant/settings", json={"endpoint_id": "first"})
    monkeypatch.setenv("ROVE_ASSISTANT_ENDPOINT", "second")
    state = client().get("/api/assistant/settings").json()
    assert (
        state["selected_endpoint_id"],
        state["effective_endpoint_id"],
        state["selection_source"],
    ) == ("first", "second", "environment")
    assert state["read_only"] and "ROVE_ASSISTANT_ENDPOINT" in state["override_reason"]
    assert client().put("/api/assistant/settings", json={"endpoint_id": None}).status_code == 409
    explicit = client("first").get("/api/assistant/settings").json()
    assert (
        explicit["selection_source"] == "explicit" and explicit["effective_endpoint_id"] == "first"
    )
    monkeypatch.delenv("ROVE_ASSISTANT_ENDPOINT")
    assert client().get("/api/assistant/settings").json()["effective_endpoint_id"] == "first"


def test_selections_are_scoped_by_active_config_path(setup, monkeypatch):
    root, _, _, _, client, _ = setup
    client().put("/api/assistant/settings", json={"endpoint_id": "first"})
    monkeypatch.setattr("rove.runtime.assistant.active_config_path", lambda: root / "other.yaml")
    assert client().get("/api/assistant/settings").json()["selection_source"] == "default"
    client().put("/api/assistant/settings", json={"endpoint_id": "second"})
    monkeypatch.setattr("rove.runtime.assistant.active_config_path", lambda: root / "rove.yaml")
    assert client().get("/api/assistant/settings").json()["effective_endpoint_id"] == "first"


def test_status_is_not_provider_proof_but_explicit_test_is(setup):
    _, _, _, calls, client, _ = setup
    api = client()
    api.put("/api/assistant/settings", json={"provider": "disabled"})
    assert not api.post("/api/assistant/test").json()["provider_tested"]
    api.put("/api/assistant/settings", json={"endpoint_id": "second"})
    status = api.get("/api/assistant/status").json()
    assert status["runtime_available"] and not status["provider_tested"] and not calls
    proof = api.post("/api/assistant/test").json()
    assert proof == {
        "available": True,
        "provider_tested": True,
        "endpoint_id": "second",
        "fingerprint": api.get("/api/assistant/settings").json()["fingerprint"],
        "reason": None,
    }
    settings, tools, stage, image, prompt, context = calls[0]
    assert settings.role == "assistant" and settings.timeout_seconds <= 30
    assert tools == () and image == "" and context == {} and stage == "assist"
    assert prompt == 'Return only {"connection":"ok"}.'


@pytest.mark.parametrize(
    "output", [{"connection": "wrong"}, {"connection": "ok", "extra": "x"}, None]
)
def test_test_requires_schema_valid_output(setup, output):
    _, _, runtime, _, client, _ = setup
    runtime.output = output
    api = client("first")
    result = api.post("/api/assistant/test").json()
    assert not result["available"] and not result["provider_tested"]


def test_provider_failure_and_missing_credentials_are_not_exposed(setup, monkeypatch):
    _, config, runtime, _, client, private = setup
    runtime.error = RuntimeError(private)
    response = client("first").post("/api/assistant/test")
    assert response.status_code == 200 and not response.json()["available"]
    assert private not in response.text
    config.endpoints["first"].config["provider"] = {
        "base_url": "https://provider.invalid",
        "api_key_env": "MISSING_TEST_SECRET",  # pragma: allowlist secret - environment variable name
    }
    monkeypatch.delenv("MISSING_TEST_SECRET", raising=False)
    view = client("first").get("/api/assistant/settings").json()
    assert not view["configured"] and "MISSING_TEST_SECRET" not in json.dumps(view)


def test_removed_saved_endpoint_remains_visible_as_unavailable(setup):
    _, config, _, _, client, _ = setup
    api = client()
    api.put("/api/assistant/settings", json={"endpoint_id": "first"})
    del config.endpoints["first"]
    view = client().get("/api/assistant/settings").json()
    assert view["effective_endpoint_id"] == "first" and view["selection_source"] == "saved"
    assert not view["configured"] and view["status_reason"]
    assert api.put("/api/assistant/settings", json={"endpoint_id": None}).status_code == 200


def test_missing_runtime_does_not_attempt_inference_and_timeout_is_safe(setup):
    _, _, runtime, calls, client, _ = setup
    runtime.healthy = False
    result = client("first").post("/api/assistant/test").json()
    assert not result["provider_tested"] and not calls
    runtime.healthy = True
    runtime.error = TimeoutError("private timeout detail")
    result = client("first").post("/api/assistant/test")
    assert not result.json()["provider_tested"] and "private timeout" not in result.text


def test_concurrent_first_saves_remain_valid(setup):
    root, config, _, _, _, _ = setup
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(
            executor.map(
                lambda i: save_assistant_selection(
                    "first" if i % 2 else "second", root=root, config_loader=lambda: config
                ),
                range(12),
            )
        )
    assert assistant_selection(root=root)["effective_endpoint_id"] in {"first", "second"}
