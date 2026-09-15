"""Controlled authentication contracts; no live sign-in or paid inference."""

import asyncio
import json
import os
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from rove.runtime import copilot
from rove.runtime.copilot import CopilotRuntime, CopilotRuntimeConfig, github_login_environment


class Client:
    def __init__(self, auth_type="user", authenticated=True):
        self.auth_type = auth_type
        self.authenticated = authenticated
        self.calls = []
        self.sessions = []

    async def start(self):
        self.calls.append("start")

    async def get_auth_status(self):
        self.calls.append("auth")
        return SimpleNamespace(
            isAuthenticated=self.authenticated, authType=self.auth_type, login="test-user"
        )

    async def list_models(self):
        self.calls.append("models")
        return [SimpleNamespace(id="model-a", name="Model A")]

    async def create_session(self, **kwargs):
        self.sessions.append(kwargs)
        return Session(self, kwargs)

    async def stop(self):
        self.calls.append("stop")


class Session:
    def __init__(self, client, config):
        self.client = client
        self.config = config
        self.rpc = SimpleNamespace(options=SimpleNamespace(update=self.update))

    async def update(self, params):
        assert params.installed_plugins == []
        self.client.calls.append("isolate")

    async def send_and_wait(self, *args, **kwargs):
        self.client.calls.append("send")
        self.config["on_event"](
            {
                "id": "usage",
                "type": "assistant.usage",
                "data": {"model": "resolved-model", "input_tokens": 1},
            }
        )
        return {"data": {"content": '{"connection":"ok"}'}}

    async def abort(self):
        self.client.calls.append("abort")

    async def disconnect(self):
        self.client.calls.append("disconnect")


def github(tmp_path, client=None, model=""):
    client = client or Client()
    options = []

    def factory(**kwargs):
        options.append(kwargs)
        return client

    config = CopilotRuntimeConfig(
        model=model,
        auth_mode="github",
        github_auth_directory=str(tmp_path / "auth"),
        native_traces=False,
    )
    return CopilotRuntime(config, client_factory=factory), client, options


async def test_github_default_has_no_byok_route_and_keeps_session_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "private-ambient-value")
    monkeypatch.setenv("COPILOT_PROVIDER_API_KEY", "private-provider-value")
    monkeypatch.setenv("GH_CONFIG_DIR", "/private/ambient/config")
    runtime, client, options = github(tmp_path)
    output = await runtime.run_stage("assist", "", "Synthetic test")
    config = client.sessions[0]
    assert "model" not in config and "provider" not in config
    assert config["available_tools"] == [] and config["memory"] == {"enabled": False}
    assert config["enable_session_store"] is False and config["enable_config_discovery"] is False
    assert config["enable_skills"] is False and config["enable_host_git_operations"] is False
    assert config["enable_on_demand_instruction_discovery"] is False
    assert config["plugin_directories"] == [] and config["included_builtin_skills"] == []
    assert client.calls == ["start", "auth", "isolate", "send", "abort", "disconnect", "stop"]
    assert options[0]["use_logged_in_user"] and options[0]["mode"] == "copilot-cli"
    env = options[0]["env"]
    assert "GH_TOKEN" not in env and "COPILOT_PROVIDER_API_KEY" not in env
    assert env["GH_CONFIG_DIR"] == str(tmp_path / "auth" / "isolated-gh")
    assert options[0]["base_directory"] == str(tmp_path / "auth")
    assert not Path(options[0]["working_directory"]).exists()
    assert output["_runtime"]["requested_model"] == "copilot-default"
    assert output["_runtime"]["observed_models"] == ["resolved-model"]
    assert output["_runtime"]["model"] == "resolved-model"
    assert "private-ambient-value" not in json.dumps(output)


@pytest.mark.parametrize("auth_type", ["gh-cli", "env", "token", None])
async def test_github_rejects_ambient_auth_sources_before_inference(tmp_path, auth_type):
    runtime, client, _ = github(tmp_path, Client(auth_type=auth_type))
    with pytest.raises(RuntimeError, match="dedicated ROVE"):
        await runtime.run_stage("assist", "", "Synthetic")
    assert not client.sessions and client.calls[-1] == "stop"
    status = await runtime.github_identity(include_models=True)
    assert not status["authenticated"] and status["models"] == []
    assert "models" not in client.calls


async def test_github_identity_discovers_models_without_inference(tmp_path):
    runtime, client, _ = github(tmp_path)
    assert await runtime.github_identity(include_models=True) == {
        "authenticated": True,
        "login": "test-user",
        "models": [{"id": "model-a", "name": "Model A"}],
        "reason": None,
    }
    assert not client.sessions and client.calls[-1] == "stop"


async def test_github_explicit_model_preserves_requested_identity(tmp_path):
    runtime, client, _ = github(tmp_path, model="selected-model")
    output = await runtime.run_stage("assist", "", "Synthetic")
    assert client.sessions[0]["model"] == "selected-model"
    assert output["_runtime"]["requested_model"] == "selected-model"


@pytest.mark.parametrize(
    "url",
    [
        "http://resource.openai.azure.com/openai/v1",
        "https://resource.openai.azure.com.evil.invalid/openai/v1",
        "https://resource.openai.azure.com/inference",
        "https://resource.services.ai.azure.com/api/projects/project/openai/v1",
        "https://user@resource.openai.azure.com/openai/v1",
        "https://resource.openai.azure.com/openai/v1?token=x",
    ],
)
def test_azure_cli_bearer_requires_explicit_supported_foundry_endpoint(url):
    with pytest.raises(ValueError, match="Foundry"):
        CopilotRuntimeConfig("deployment", {"base_url": url}, auth_mode="azure_cli")


@pytest.mark.parametrize(
    "auth_field", ["api_key", "api_key_env", "bearer_token", "bearer_token_provider", "headers"]
)
def test_azure_cli_never_uses_alternative_credentials(auth_field):
    with pytest.raises(ValueError, match="alternative"):
        CopilotRuntimeConfig(
            "deployment",
            {"base_url": "https://resource.openai.azure.com/openai/v1", auth_field: "unused"},
            auth_mode="azure_cli",
        )


async def test_azure_token_fresh_per_execution_not_serialized_or_fallback(monkeypatch):
    calls = []

    async def token():
        calls.append("acquire")
        return f"private-token-{len(calls)}"

    monkeypatch.setattr(copilot, "_azure_cli_token", token)
    config = CopilotRuntimeConfig(
        "deployment",
        {"type": "openai", "base_url": "https://resource.services.ai.azure.com/openai/v1"},
        auth_mode="azure_cli",
        native_traces=False,
    )
    client = Client()
    options = []

    def factory(**kwargs):
        options.append(kwargs)
        return client

    runtime = CopilotRuntime(config, client_factory=factory)
    first = await runtime.run_stage("assist", "", "Synthetic")
    second = await runtime.run_stage("assist", "", "Synthetic")
    assert len(calls) == 2
    assert (
        client.sessions[0]["provider"]["bearer_token"]
        != client.sessions[1]["provider"]["bearer_token"]
    )
    assert all(not option["use_logged_in_user"] and option["mode"] == "empty" for option in options)
    assert "auth" not in client.calls
    assert "private-token" not in json.dumps([asdict(config), first, second, options])


async def test_azure_credential_acquisition_is_offloop_bounded_and_drained_on_cancel(monkeypatch):
    started, release = threading.Event(), threading.Event()
    closed = []

    class Credential:
        def __init__(self, **kwargs):
            assert kwargs == {"process_timeout": 10}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append(True)

        def get_token(self, scope):
            assert scope == "https://ai.azure.com/.default"
            assert threading.current_thread() is not threading.main_thread()
            started.set()
            assert release.wait(2)
            return SimpleNamespace(token="private-token", expires_on=time.time() + 120)

    monkeypatch.setitem(
        sys.modules, "azure.identity", SimpleNamespace(AzureCliCredential=Credential)
    )
    task = asyncio.create_task(copilot._azure_cli_token())
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [True]


def test_github_requires_explicit_profile_and_cannot_mix_providers():
    with pytest.raises(ValueError, match="absolute ROVE profile"):
        CopilotRuntimeConfig("", auth_mode="github")
    with pytest.raises(ValueError, match="BYOK"):
        CopilotRuntimeConfig("", {"base_url": "http://127.0.0.1"}, auth_mode="github")
    with pytest.raises(ValueError, match="absolute profile"):
        github_login_environment("relative")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX controlled CLI executable")
async def test_installed_azure_identity_runs_controlled_cli_and_reaps_timeout(
    tmp_path, monkeypatch
):
    identity = pytest.importorskip("azure.identity")
    native_credential = identity.AzureCliCredential
    executable = tmp_path / "az"
    pid_file = tmp_path / "pid"
    executable.write_text(
        f"#!{sys.executable}\nimport os,time\n"
        f"open({str(pid_file)!r},'w').write(str(os.getpid()))\n"
        "time.sleep(30)\n"
    )
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))

    def bounded_native(**kwargs):
        assert kwargs == {"process_timeout": 10}
        # Keep the test fast while exercising the installed SDK's actual subprocess timeout.
        return native_credential(process_timeout=2)

    monkeypatch.setattr(identity, "AzureCliCredential", bounded_native)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="Azure CLI authentication failed"):
        await copilot._azure_cli_token()
    assert time.monotonic() - started < 6
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


async def test_azure_authentication_failure_stops_before_any_model_request(monkeypatch):
    async def fail():
        raise RuntimeError("Azure CLI authentication failed")

    monkeypatch.setattr(copilot, "_azure_cli_token", fail)
    config = CopilotRuntimeConfig(
        "deployment",
        {"base_url": "https://resource.openai.azure.com/openai/v1"},
        auth_mode="azure_cli",
    )
    client = Client()
    runtime = CopilotRuntime(config, client_factory=lambda **_: client)
    with pytest.raises(RuntimeError, match="authentication failed"):
        await runtime.run_stage("assist", "", "Synthetic")
    assert client.sessions == [] and client.calls == ["start", "stop"]
