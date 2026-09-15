"""Provider migration, credential boundaries and explicit sign-in lifecycle."""

import asyncio
import json
import sqlite3

import pytest

from rove.models.config import RoveConfig
from rove.runtime.assistant import (
    assistant_selection,
    assistant_settings,
    assistant_settings_view,
    normalize_selection,
    save_assistant_selection,
)
from rove.runtime.assistant_login import CopilotLogin


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("ROVE_ASSISTANT_ENDPOINT", raising=False)
    monkeypatch.setattr("rove.runtime.assistant.active_config_path", lambda: tmp_path / "rove.yaml")
    return tmp_path


def test_new_default_and_explicit_disabled_are_different_after_restart(workspace):
    assert assistant_selection(root=workspace)["selection_source"] == "default"
    default = assistant_settings(root=workspace)
    assert default.auth_mode == "github" and default.model == "" and default.provider == {}
    assert default.github_auth_directory.startswith(str(workspace))
    save_assistant_selection({"provider": "disabled"}, root=workspace)
    assert assistant_selection(root=workspace)["selection"] == {"provider": "disabled"}
    with pytest.raises(ValueError, match="disabled"):
        assistant_settings(root=workspace)


@pytest.mark.parametrize("old_endpoint", [None, "local"])
def test_legacy_rows_preserve_local_or_disabled_and_migrate(workspace, old_endpoint):
    config = RoveConfig.model_validate(
        {
            "endpoints": {
                "local": {
                    "type": "agent",
                    "adapter": "copilot_agent",
                    "config": {
                        "model": "local-model",
                        "provider": {"base_url": "http://127.0.0.1:1234/v1"},
                    },
                }
            }
        }
    )
    with sqlite3.connect(workspace / "assistant-settings.sqlite3") as db:
        db.execute(
            "CREATE TABLE assistant_settings (config_path TEXT PRIMARY KEY, endpoint_id TEXT)"
        )
        db.execute(
            "INSERT INTO assistant_settings VALUES (?, ?)",
            (str(workspace / "rove.yaml"), old_endpoint),
        )
    selected = assistant_selection(root=workspace)["selection"]
    assert selected["provider"] == ("endpoint" if old_endpoint else "disabled")
    if old_endpoint:
        assert assistant_settings(root=workspace, config_loader=lambda: config).auth_mode == "byok"
    save_assistant_selection({"provider": "copilot", "model": "selected-model"}, root=workspace)
    assert assistant_settings(root=workspace).model == "selected-model"
    with sqlite3.connect(workspace / "assistant-settings.sqlite3") as db:
        saved = json.loads(db.execute("SELECT settings_json FROM assistant_settings").fetchone()[0])
        assert saved == {"provider": "copilot", "model": "selected-model"}


@pytest.mark.parametrize(
    "url",
    [
        "http://resource.openai.azure.com",
        "https://resource.openai.azure.com.evil.test",
        "https://evil.test/openai/v1",
        "https://user:password@resource.openai.azure.com",  # pragma: allowlist secret
        "https://resource.openai.azure.com/?token=x",
        "https://resource.openai.azure.com/#fragment",
        "https://resource.services.ai.azure.com/api/projects/project",
        "https://resource.inference.ai.azure.com",
        "https://resource.openai.azure.com:1234",
        "https://openai.azure.com",
        "https://resource.openai.azure.com/openai/deployments/model",
    ],
)
def test_foundry_cannot_send_azure_identity_to_arbitrary_destinations(url):
    with pytest.raises(ValueError):
        normalize_selection({"provider": "foundry", "endpoint": url, "deployment": "model"})


def test_foundry_persists_deployment_and_url_without_credentials(workspace):
    save_assistant_selection(
        {
            "provider": "foundry",
            "endpoint": "https://factory.services.ai.azure.com",
            "deployment": "robot-planner",
        },
        root=workspace,
    )
    settings = assistant_settings(root=workspace)
    assert settings.auth_mode == "azure_cli" and settings.model == "robot-planner"
    assert settings.provider == {
        "type": "openai",
        "base_url": "https://factory.services.ai.azure.com/openai/v1/",
        "wire_api": "completions",
    }
    original = assistant_selection(root=workspace)["fingerprint"]
    save_assistant_selection(
        {
            "provider": "foundry",
            "endpoint": "https://factory.services.ai.azure.com",
            "deployment": "robot-planner-v2",
        },
        root=workspace,
    )
    assert assistant_selection(root=workspace)["fingerprint"] != original
    with pytest.raises(ValueError):
        save_assistant_selection(
            {
                "provider": "foundry",
                "endpoint": "https://factory.services.ai.azure.com",
                "deployment": "robot-planner",
                "api_key": "fixture",  # pragma: allowlist secret
            },
            root=workspace,
        )


def test_reuse_only_model_endpoints_not_foundry_agents(workspace):
    config = RoveConfig.model_validate(
        {
            "endpoints": {
                "model": {
                    "type": "vlm",
                    "adapter": "azure_openai",
                    "endpoint": "https://factory.openai.azure.com",
                    "config": {"deployment_name": "planner"},
                },
                "agent": {
                    "type": "agent",
                    "adapter": "azure_foundry_agent",
                    "config": {
                        "project_endpoint": "https://factory.services.ai.azure.com/api/projects/p",
                        "agent_name": "agent",
                    },
                },
                "placeholder": {
                    "type": "vlm",
                    "adapter": "azure_openai",
                    "endpoint": "https://your-resource.openai.azure.com",
                    "config": {"deployment_name": "model"},
                },
            }
        }
    )
    view = assistant_settings_view(root=workspace, config_loader=lambda: config)
    assert [item["id"] for item in view["foundry_endpoints"]] == ["model"]
    assert view["foundry_endpoints"][0]["deployment"] == "planner"


class Process:
    def __init__(self, output=b"", complete=False):
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(output)
        self.returncode = 0 if complete else None
        self.done = asyncio.Event()
        self.terminated = False
        if complete:
            self.stdout.feed_eof()
            self.done.set()

    async def wait(self):
        await self.done.wait()
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15
        self.stdout.feed_eof()
        self.done.set()

    def kill(self):
        self.terminate()


@pytest.mark.asyncio
async def test_login_exposes_only_device_code_and_reaps_cancelled_process(workspace, monkeypatch):
    monkeypatch.setattr("rove.runtime.assistant_login.shutil.which", lambda _: "/verified/copilot")
    process = Process(
        b"private provider diagnostics\nTo authenticate, visit https://github.com/login/device and enter code ABCD-1234.\nWaiting for authorization...\n"
    )
    calls = []

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return process

    manager = CopilotLogin(str(workspace / "auth"), spawn=spawn)
    first = await manager.start()
    await asyncio.sleep(0)
    pending = manager.get(first["operation_id"])
    assert pending["status"] == "pending" and pending["user_code"] == "ABCD-1234"
    assert "private" not in json.dumps(pending)
    assert (await manager.start())["operation_id"] == first["operation_id"]
    assert len(calls) == 1 and calls[0][0][-2:] == ("login", "--device-code")
    assert calls[0][1]["env"]["COPILOT_HOME"] == str(workspace / "auth")
    assert not set(calls[0][1]["env"]) & {"GH_TOKEN", "GITHUB_TOKEN", "COPILOT_GITHUB_TOKEN"}
    await manager.cancel(first["operation_id"])
    assert process.terminated and manager.get(first["operation_id"])["user_code"] is None


@pytest.mark.asyncio
async def test_login_timeout_reaps_process_and_removes_device_code(workspace, monkeypatch):
    monkeypatch.setattr("rove.runtime.assistant_login.shutil.which", lambda _: "/verified/copilot")
    process = Process()

    async def spawn(*args, **kwargs):
        return process

    manager = CopilotLogin(str(workspace / "auth"), spawn=spawn, timeout=0.01)
    operation = await manager.start()
    await manager.operation.task
    assert manager.get(operation["operation_id"])["status"] == "expired"
    assert process.terminated
    await manager.close()
