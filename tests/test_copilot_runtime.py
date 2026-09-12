"""Offline contracts: these fixtures do not establish provider/model quality."""

import asyncio
import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from rove.runtime.copilot import (
    CopilotRuntime,
    CopilotRuntimeConfig,
    RuntimeRecordingError,
    RuntimeTool,
    _Recorder,
)


def event(kind, data=None, **kwargs):
    return {"id": str(uuid4()), "type": kind, "data": data or {}, **kwargs}


class Session:
    def __init__(self, events=(), *, wait=False, output='{"objects": []}', fail=None):
        self.events, self.wait, self.output, self.fail = events, wait, output, fail
        self.calls = []
        self.config = {}

    async def send_and_wait(self, prompt, **kwargs):
        self.calls.append(("send", json.loads(prompt), kwargs))
        for item in self.events:
            self.config["on_event"](item)
        if self.fail:
            raise self.fail
        if self.wait:
            await asyncio.Event().wait()
        return event("assistant.message", {"content": self.output})

    async def abort(self):
        self.calls.append(("abort",))

    async def disconnect(self):
        self.calls.append(("disconnect",))


class Client:
    def __init__(self, session=None, *, start_error=None, create_error=None):
        self.session = session or Session()
        self.start_error, self.create_error = start_error, create_error
        self.calls = []

    async def start(self):
        self.calls.append("start")
        if self.start_error:
            raise self.start_error

    async def create_session(self, **config):
        if self.create_error:
            raise self.create_error
        self.session.config = config
        return self.session

    async def stop(self):
        self.calls.append("stop")


def runtime(client=None, sink=None, **kwargs):
    client = client or Client()
    options = []

    def factory(**settings):
        options.append(settings)
        return client

    config = CopilotRuntimeConfig("synthetic", {"base_url": "http://127.0.0.1"}, **kwargs)
    return CopilotRuntime(config, client_factory=factory, event_sink=sink), client, options


async def test_fresh_sessions_workspace_roles_and_no_ambient_credentials(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "private-auth")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "private-provider")
    host, client, options = runtime()
    first = await host.run_stage("perceive", "", "Pick red box", {"role": "grader"})
    second = await host.run_stage("perceive", "", "Pick red box")
    assert first["_runtime"]["session_id"] != second["_runtime"]["session_id"]
    assert first["_runtime"]["role"] == "candidate"
    assert options[0]["working_directory"] != options[1]["working_directory"]
    assert not Path(options[0]["working_directory"]).exists()
    assert "GITHUB_TOKEN" not in options[0]["env"]
    assert "AZURE_OPENAI_API_KEY" not in options[0]["env"]
    assert options[0]["mode"] == "empty"
    assert not options[0]["use_logged_in_user"]
    assert client.session.config["available_tools"] == []
    assert client.session.config["enable_config_discovery"] is False


async def test_live_usage_duplicate_preservation_and_parent_event_not_span():
    usage = event(
        "assistant.usage",
        {"input_tokens": 0, "duration": timedelta(milliseconds=150), "cost": 0.5},
        ephemeral=True,
        parentId="preceding-event",
    )
    seen = []
    host, _, _ = runtime(Client(Session([usage, usage])), seen.append)
    result = await host.run_stage("plan", "", "Pick red box")
    assert len(seen) == 1
    assert seen[0]["previous_event_id"] == "preceding-event"
    assert "span_id" not in seen[0]
    assert result["_runtime"]["usage"] == [
        {"input_tokens": 0, "duration": {"value": 0.15, "unit": "s"}, "cost": 0.5}
    ]
    assert "output_tokens" not in result["_runtime"]["usage"][0]


async def test_missing_usage_is_unknown_and_output_is_not_a_grade():
    host, _, _ = runtime()
    result = await host.run_stage("perceive", "", "Pick red box")
    assert result["_runtime"]["usage"] is None
    assert "success" not in result


async def test_conflicting_replay_fails_even_if_different_content_is_redacted():
    first = event("assistant.message", {"content": "first"})
    second = {**first, "data": {"content": "changed"}}
    seen = []
    host, _, _ = runtime(Client(Session([first, second])), seen.append)
    with pytest.raises(RuntimeRecordingError):
        await host.run_stage("plan", "", "task")
    assert len(seen) == 1


async def test_content_redaction_and_explicit_capture_still_redacts_credentials():
    item = event(
        "tool.execution_complete",
        {
            "result": {"text": "customer-image"},
            "api_key": "private",  # pragma: allowlist secret - synthetic fixture
        },
    )
    for capture in (True, False):
        seen = []
        host, _, _ = runtime(Client(Session([item])), seen.append, capture_content=capture)
        await host.run_stage("plan", "", "Pick red box")
        assert seen[0]["data"]["api_key"] == "[redacted]"
        assert (seen[0]["data"]["result"] == "[redacted]") is (not capture)


async def test_recorder_failure_aborts_immediately_and_cleans_up():
    def broken_sink(_):
        raise OSError("disk full")

    host, client, _ = runtime(Client(Session([event("assistant.usage")], wait=True)), broken_sink)
    with pytest.raises(RuntimeRecordingError, match="incomplete"):
        await asyncio.wait_for(host.run_stage("plan", "", "task"), 1)
    assert [call[0] for call in client.session.calls][-2:] == ["abort", "disconnect"]
    assert client.calls[-1] == "stop"


@pytest.mark.parametrize("kind", ["timeout", "cancel", "invalid", "provider_error"])
async def test_terminal_failure_does_not_leak_runtime(kind):
    session = Session(
        wait=kind in {"timeout", "cancel"},
        output="not JSON" if kind == "invalid" else "{}",
        fail=RuntimeError("provider failure") if kind == "provider_error" else None,
    )
    host, client, _ = runtime(Client(session), timeout_seconds=0.02 if kind == "timeout" else 5)
    pending = asyncio.create_task(host.run_stage("plan", "", "task"))
    if kind == "cancel":
        await asyncio.sleep(0.01)
        pending.cancel()
    expected = {
        "timeout": TimeoutError,
        "cancel": asyncio.CancelledError,
        "invalid": ValueError,
        "provider_error": RuntimeError,
    }[kind]
    with pytest.raises(expected):
        await pending
    assert [call[0] for call in session.calls][-2:] == ["abort", "disconnect"]
    assert client.calls[-1] == "stop"


@pytest.mark.parametrize("failure", ["start", "create"])
async def test_partial_startup_is_closed(failure):
    client = Client(**{f"{failure}_error": RuntimeError("startup")})
    host, _, _ = runtime(client)
    with pytest.raises(RuntimeError, match="startup"):
        await host.run_stage("plan", "", "task")
    assert client.calls[-1] == "stop"


async def test_images_are_passed_as_blobs_and_not_in_prompt():
    encoded = base64.b64encode(b"\xff\xd8\xffsynthetic-jpeg").decode()
    host, client, _ = runtime()
    await host.run_stage("perceive", encoded, "Find object")
    _, prompt, options = client.session.calls[0]
    assert encoded not in json.dumps(prompt)
    assert options["attachments"] == [
        {"type": "blob", "data": encoded, "mimeType": "image/jpeg", "displayName": "case-image"}
    ]


async def test_tool_role_separation_and_only_host_tools_exposed():
    tool = RuntimeTool(
        "observe", "Read synthetic observation", {"type": "object"}, lambda _: {"x": 1}
    )
    config = CopilotRuntimeConfig("test", {"base_url": "http://127.0.0.1"}, role="grader")
    with pytest.raises(ValueError, match="not permitted"):
        CopilotRuntime(config, tools=(tool,))
    host, client, _ = runtime()
    host.tools = (tool,)
    await host.run_stage("plan", "", "task")
    assert client.session.config["available_tools"] == ["observe"]
    result = await client.session.config["tools"][0]["handler"](
        SimpleNamespace(arguments={}, tool_call_id="call-1")
    )
    assert result == {"x": 1}


def test_event_and_size_limits_fail_explicitly():
    config = CopilotRuntimeConfig("test", {"base_url": "http://127.0.0.1"}, max_events=1)
    recorder = _Recorder(config, None, "session", "plan")
    recorder.emit(event("session.idle"))
    recorder.emit(event("session.idle"))
    with pytest.raises(RuntimeRecordingError):
        recorder.check()
    config = CopilotRuntimeConfig("test", {"base_url": "http://127.0.0.1"}, max_event_bytes=256)
    recorder = _Recorder(config, None, "session", "plan")
    recorder.emit(event("unknown", {"untrusted_payload": "x" * 500}))
    with pytest.raises(RuntimeRecordingError):
        recorder.check()


def test_typed_sdk_event_compatibility_and_explicit_trace_fields():
    class Kind(Enum):
        USAGE = "assistant.usage"

    @dataclass
    class Data:
        model: str
        input_tokens: int | None = None

    @dataclass
    class Event:
        id: object
        type: Kind
        timestamp: datetime
        data: Data

    seen = []
    config = CopilotRuntimeConfig("test", {"base_url": "http://127.0.0.1"})
    recorder = _Recorder(config, seen.append, "session", "plan")
    recorder.emit(Event(uuid4(), Kind.USAGE, datetime.now(UTC), Data("test")))
    recorder.emit(event("rove.tool.started", trace_id="a" * 32, span_id="b" * 16))
    recorder.check()
    assert seen[0]["data"] == {"model": "test"}
    assert seen[1]["trace_id"] == "a" * 32


async def test_tool_callback_records_actual_propagated_context(monkeypatch):
    monkeypatch.setattr(
        "rove.runtime.copilot.trace_identity", lambda: {"trace_id": "a" * 32, "span_id": "b" * 16}
    )
    seen = []
    host, client, _ = runtime(sink=seen.append)
    host.tools = (RuntimeTool("observe", "Read", {}, lambda _: {}),)
    await host.run_stage("plan", "", "task")
    await client.session.config["tools"][0]["handler"](
        SimpleNamespace(arguments={}, tool_call_id="c1")
    )
    assert [e["event_type"] for e in seen] == ["rove.tool.started", "rove.tool.completed"]
    assert all(e["trace_id"] == "a" * 32 for e in seen)


async def test_local_recording_does_not_require_external_collector():
    seen = []
    host, _, options = runtime(
        Client(Session([event("session.idle")])),
        seen.append,
        telemetry={"otlp_endpoint": "http://127.0.0.1:1", "capture_content": True},
    )
    await host.run_stage("plan", "", "task")
    assert len(seen) == 1
    assert options[0]["telemetry"]["capture_content"] is False


async def test_cleanup_failure_is_visible_and_other_cleanup_still_runs():
    seen = []
    host, client, _ = runtime(sink=seen.append)

    async def failed_abort():
        raise OSError("abort failure")

    client.session.abort = failed_abort
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await host.run_stage("plan", "", "task")
    assert client.session.calls[-1] == ("disconnect",)
    assert client.calls[-1] == "stop"
    assert seen[-1]["event_type"] == "rove.runtime.cleanup_failed"


async def test_async_sink_is_rejected_instead_of_silently_dropping_coroutine():
    async def unsupported(_):
        pass

    host, _, _ = runtime(Client(Session([event("session.idle")])), unsupported)
    with pytest.raises(RuntimeRecordingError):
        await host.run_stage("plan", "", "task")


def test_unknown_event_content_is_redacted_and_source_name_preserved():
    config = CopilotRuntimeConfig("test", {"base_url": "http://127.0.0.1"})
    seen = []
    recorder = _Recorder(config, seen.append, "session", "plan")
    recorder.emit(
        event("unknown", {"new_provider_text": "customer secret"}, raw_type="new.message")
    )
    recorder.emit(event("unknown", "customer secret"))
    recorder.check()
    assert "customer secret" not in json.dumps(seen)
    assert seen[0]["event_type"] == "new.message"


async def test_tool_failure_has_trace_identity_and_error_type_without_message(monkeypatch):
    monkeypatch.setattr(
        "rove.runtime.copilot.trace_identity", lambda: {"trace_id": "a" * 32, "span_id": "b" * 16}
    )

    def fail(_):
        raise ValueError("customer secret")

    seen = []
    host, client, _ = runtime(sink=seen.append)
    host.tools = (RuntimeTool("observe", "Read", {}, fail),)
    await host.run_stage("plan", "", "task")
    with pytest.raises(ValueError):
        await client.session.config["tools"][0]["handler"](
            SimpleNamespace(arguments={}, tool_call_id="c1")
        )
    assert seen[-1]["event_type"] == "rove.tool.failed"
    assert seen[-1]["trace_id"] == "a" * 32
    assert seen[-1]["data"]["error_type"] == "ValueError"
    assert "customer secret" not in json.dumps(seen)


def test_real_opentelemetry_context_is_recorded_when_installed():
    module = pytest.importorskip("opentelemetry.sdk.trace")
    from rove.runtime.copilot import trace_identity

    provider = module.TracerProvider()
    with provider.get_tracer("rove.test").start_as_current_span("trial") as span:
        assert trace_identity() == {
            "trace_id": f"{span.get_span_context().trace_id:032x}",
            "span_id": f"{span.get_span_context().span_id:016x}",
        }
    assert trace_identity() == {}
    provider.shutdown()
