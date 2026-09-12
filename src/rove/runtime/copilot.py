"""Copilot-backed stage execution with explicit scope and observable lifecycle.

The SDK runs the agent loop. This module never grades an outcome or controls a
robot; a completed tool or SDK turn is not evidence of physical task completion.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import hashlib
import inspect
import json
import math
import os
import shutil
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

SDK_VERSION = "1.0.13"
CLI_VERSION = "1.0.81-9"
Role = Literal["candidate", "grader", "assistant"]
EventSink = Callable[[dict[str, Any]], None]


class RuntimeRecordingError(RuntimeError):
    """Required runtime evidence could not be recorded completely."""


@dataclass(frozen=True)
class RuntimeTool:
    """A host-owned tool explicitly assigned to one or more actor roles.

    Handlers receive an argument dictionary and must validate domain inputs before
    performing an operation. A role declaration is configuration, never model input.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any] = field(repr=False, compare=False)
    roles: frozenset[str] = frozenset({"candidate"})


@dataclass(frozen=True)
class CopilotRuntimeConfig:
    model: str
    provider: dict[str, Any] = field(repr=False)
    role: Role = "candidate"
    timeout_seconds: float = 60.0
    cleanup_timeout_seconds: float = 5.0
    cli_path: str = "copilot"
    expected_cli_version: str = CLI_VERSION
    capture_content: bool = False
    max_events: int = 10_000
    max_event_bytes: int = 256_000
    system_message: str = "Return a JSON object describing the requested robotics pipeline stage."
    telemetry: dict[str, Any] | None = None
    native_traces: bool = True

    def __post_init__(self) -> None:
        if self.role not in {"candidate", "grader", "assistant"}:
            raise ValueError("Invalid runtime actor role")
        if not self.model or not self.provider.get("base_url"):
            raise ValueError("An explicit model and provider base_url are required")
        for value in (self.timeout_seconds, self.cleanup_timeout_seconds):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Runtime deadlines must be positive finite seconds")
        if self.max_events < 1 or self.max_event_bytes < 256:
            raise ValueError("Runtime evidence limits are too small")
        if type(self.native_traces) is not bool:
            raise ValueError("native_traces must be a boolean")


def _plain(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: _plain(getattr(value, f.name))
            for f in dataclasses.fields(value)
            if not f.name.startswith("_") and getattr(value, f.name) is not None
        }
    if isinstance(value, Mapping):
        return {
            str(k): _plain(v)
            for k, v in value.items()
            if v is not None and not str(k).startswith("_")
        }
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | UUID):
        return str(value)
    if isinstance(value, timedelta):
        return {"value": value.total_seconds(), "unit": "s"}
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"Unsupported runtime event value: {type(value).__name__}")


_CONTENT_KEYS = frozenset(
    {
        "content",
        "message",
        "reason",
        "reasoning",
        "arguments",
        "result",
        "error",
        "prompt",
        "text",
        "attachments",
        "data",
        "output",
        "input",
        "summary",
        "detailed_result",
        "detailedResult",
        "partial_arguments",
        "partialArguments",
    }
)
_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "token",
        "access_token",
        "github_token",
        "bearer_token",
        "headers",
        "secret",
        "password",
    }
)


_METADATA_KEYS = frozenset(
    {
        "model",
        "model_id",
        "tool_name",
        "toolName",
        "tool_call_id",
        "toolCallId",
        "api_call_id",
        "apiCallId",
        "provider_call_id",
        "service_request_id",
        "agent_id",
        "parent_agent_id",
        "parent_tool_call_id",
        "call_id",
        "interaction_id",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "accepted_prediction_tokens",
        "rejected_prediction_tokens",
        "duration",
        "time_to_first_token",
        "output_ttft",
        "inter_token_latency",
        "value",
        "unit",
        "cost",
        "is_byok",
        "is_auto",
        "finish_reason",
        "transport",
        "success",
        "status",
        "reasoning_effort",
        "type",
        "kind",
        "error_type",
    }
)


def _redact(value: Any, *, capture_content: bool) -> Any:
    if isinstance(value, dict):
        return {
            k: "[redacted]"
            if k.lower() in _SECRET_KEYS
            or (not capture_content and (k in _CONTENT_KEYS or k not in _METADATA_KEYS))
            else _redact(v, capture_content=capture_content)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v, capture_content=capture_content) for v in value]
    return value


def trace_identity() -> dict[str, str]:
    """Read the actual current OpenTelemetry span, when instrumentation exists."""
    try:
        from opentelemetry import trace
    except ImportError:
        return {}
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return {}
    return {"trace_id": f"{context.trace_id:032x}", "span_id": f"{context.span_id:016x}"}


class _Recorder:
    def __init__(
        self, config: CopilotRuntimeConfig, sink: EventSink | None, session_id: str, stage: str
    ):
        self.config, self.sink, self.session_id, self.stage = config, sink, session_id, stage
        self.seen: dict[str, str] = {}
        self.failure: Exception | None = None
        self.failed = asyncio.Event()
        self.count = 0
        self.usage: list[dict[str, Any]] = []

    def emit(self, event: Any) -> None:
        # SDK callbacks swallow exceptions. Retain the failure and signal the
        # supervising coroutine instead of silently reporting a complete trace.
        if self.failure:
            return
        try:
            raw = _plain(event)
            source_id = str(raw.get("id") or uuid4())
            fingerprint = hashlib.sha256(
                json.dumps(raw, sort_keys=True, allow_nan=False).encode()
            ).hexdigest()
            if source_id in self.seen:
                if self.seen[source_id] != fingerprint:
                    raise RuntimeRecordingError(
                        "Conflicting runtime events share one source identity"
                    )
                return
            if self.count >= self.config.max_events:
                raise RuntimeRecordingError("Runtime event limit exceeded; evidence is incomplete")
            kind = str(raw.get("raw_type") or raw.get("type", "unknown"))
            payload = raw.get("data", {})
            data = (
                _redact(payload, capture_content=self.config.capture_content)
                if isinstance(payload, dict) or self.config.capture_content
                else "[redacted]"
            )
            record = {
                "source": "copilot",
                "source_event_id": source_id,
                "previous_event_id": raw.get("parent_id", raw.get("parentId")),
                "session_id": self.session_id,
                "role": self.config.role,
                "stage": self.stage,
                "event_type": kind,
                "timestamp": raw.get("timestamp"),
                "source_clock_id": raw.get("source_clock_id", "copilot:" + self.session_id),
                "source_timestamp": raw.get("source_timestamp", raw.get("timestamp")),
                "time_unit": raw.get("time_unit", "iso8601" if raw.get("timestamp") else None),
                "clock_alignment": raw.get("clock_alignment", "unverified"),
                "received_at": datetime.now(UTC).isoformat(),
                "agent_id": raw.get("agent_id", raw.get("agentId")),
                "ephemeral": raw.get("ephemeral"),
                "data": data,
            }
            if kind.startswith("rove."):
                from rove.orchestrator.recording import source_clock

                record.update(source_clock())
            # Parent event IDs express sequence, never span parentage. Only use
            # span IDs explicitly supplied by a producer (our tool callbacks).
            for key in ("trace_id", "span_id", "parent_span_id"):
                if raw.get(key):
                    record[key] = raw[key]
            if len(json.dumps(record, allow_nan=False).encode()) > self.config.max_event_bytes:
                raise RuntimeRecordingError(
                    "Runtime event exceeds storage limit; evidence is incomplete"
                )
            if self.sink:
                pending = self.sink(record)
                if inspect.isawaitable(pending):
                    if inspect.iscoroutine(pending):
                        pending.close()
                    raise RuntimeRecordingError("Runtime event sink must record synchronously")
            self.seen[source_id] = fingerprint
            self.count += 1
            if kind == "assistant.usage":
                self.usage.append(data)
        except Exception as exc:
            self.failure = exc
            self.failed.set()

    def check(self) -> None:
        if self.failure:
            raise RuntimeRecordingError(
                "Runtime telemetry recording failed; evidence is incomplete"
            ) from self.failure


class CopilotRuntime:
    """An AgentAdapter-compatible host with fresh state per stage invocation.

    Provider credentials are passed explicitly by the application, never discovered
    from a user's existing Copilot sessions. The transport factory is injectable for
    offline lifecycle tests; production imports the pinned SDK lazily.
    """

    def __init__(
        self,
        config: CopilotRuntimeConfig,
        *,
        tools: tuple[RuntimeTool, ...] = (),
        event_sink: EventSink | None = None,
        client_factory: Callable[..., Any] | None = None,
    ):
        self.config = config
        self.tools = tools
        self.event_sink = event_sink
        self._factory = client_factory
        self.model_id = config.model
        self.display_name = f"Copilot: {config.model}"
        if len({tool.name for tool in tools}) != len(tools):
            raise ValueError("Runtime tool names must be unique")
        if any(config.role not in tool.roles for tool in tools):
            raise ValueError("A tool is not permitted for this runtime role")

    async def _client(self, workspace: str) -> Any:
        options: dict[str, Any] = {
            "working_directory": workspace,
            "base_directory": str(Path(workspace) / "copilot-state"),
            "use_logged_in_user": False,
            "mode": "empty",
            "enable_remote_sessions": False,
            "env": {
                k: v
                for k, v in os.environ.items()
                if k
                in {
                    "PATH",
                    "SYSTEMROOT",
                    "WINDIR",
                    "LANG",
                    "LC_ALL",
                    "TMPDIR",
                    "TEMP",
                    "TMP",
                }
            },
        }
        if self.config.telemetry is not None:
            options["telemetry"] = {
                **self.config.telemetry,
                "capture_content": self.config.capture_content,
            }
        elif self.config.native_traces:
            options["telemetry"] = {
                "exporter_type": "file",
                "file_path": str(Path(workspace) / "runtime-traces.jsonl"),
                "capture_content": False,
            }
        if self._factory:
            return self._factory(**options)
        try:
            installed = version("github-copilot-sdk")
        except PackageNotFoundError as exc:
            raise RuntimeError(
                "Install ROVE's copilot extra to enable the Copilot runtime"
            ) from exc
        if installed != SDK_VERSION:
            raise RuntimeError(f"Copilot SDK {SDK_VERSION} is required; installed {installed}")
        from copilot import CopilotClient, RuntimeConnection

        cli = shutil.which(self.config.cli_path)
        if cli is None:
            raise RuntimeError("Configured Copilot CLI executable was not found")
        process = await asyncio.create_subprocess_exec(
            cli,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
            env=options["env"],
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), 10)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        if process.returncode or f" {self.config.expected_cli_version}." not in stdout.decode():
            raise RuntimeError(
                f"Copilot CLI {self.config.expected_cli_version} is required by this runtime profile"
            )
        options["connection"] = RuntimeConnection.for_stdio(
            path=cli,
            args=[
                "--no-auto-update",
                "--no-remote",
                "--no-remote-export",
                "--disable-builtin-mcps",
                "--no-custom-instructions",
            ],
        )
        return CopilotClient(**options)

    async def health_check(self) -> bool:
        try:
            return (
                version("github-copilot-sdk") == SDK_VERSION
                and shutil.which(self.config.cli_path) is not None
            )
        except PackageNotFoundError:
            return False

    def _sdk_tools(self, recorder: _Recorder) -> list[Any]:
        from rove.orchestrator.recording import capture_recording_context, event_sink

        recording_context = capture_recording_context()
        durable_sink = recording_context[event_sink]
        if durable_sink is not None:

            def guarded_sink(event):
                try:
                    pending = durable_sink(event)
                    if inspect.isawaitable(pending):
                        if inspect.iscoroutine(pending):
                            pending.close()
                        raise RuntimeRecordingError("Runtime event sink must record synchronously")
                except Exception as error:
                    # SDK tool dispatch may convert callback errors into model
                    # observations. Wake supervision so journal loss cannot be
                    # mistaken for a recoverable tool failure and successful run.
                    recorder.failure = error
                    recorder.failed.set()
                    raise RuntimeRecordingError("Tool span recording failed") from error

            recording_context[event_sink] = guarded_sink
        prepared = []
        for definition in self.tools:

            async def invoke(call: Any, tool: RuntimeTool = definition) -> Any:
                from rove.orchestrator.recording import tool_span, use_recording_context

                call_id = getattr(call, "tool_call_id", None)
                with (
                    use_recording_context(recording_context),
                    tool_span(tool.name, self.config.role, recorder.stage, call_id),
                ):
                    return await invoke_scoped(call, tool)

            async def invoke_scoped(call: Any, tool: RuntimeTool) -> Any:
                args = call.arguments if hasattr(call, "arguments") else call.get("arguments")
                if not isinstance(args, dict):
                    raise ValueError("ROVE tool arguments must be an object")
                call_id = getattr(call, "tool_call_id", None)
                try:
                    from opentelemetry import trace

                    parent = getattr(trace.get_current_span(), "parent", None)
                except ImportError:
                    parent = None
                parent_identity = {"parent_span_id": f"{parent.span_id:016x}"} if parent else {}
                recorder.emit(
                    {
                        "id": str(uuid4()),
                        "type": "rove.tool.started",
                        **trace_identity(),
                        **parent_identity,
                        "data": {"tool_name": tool.name, "tool_call_id": call_id},
                    }
                )
                recorder.check()
                try:
                    result = tool.handler(args)
                    if inspect.isawaitable(result):
                        result = await result
                except (Exception, asyncio.CancelledError) as exc:
                    recorder.emit(
                        {
                            "id": str(uuid4()),
                            "type": "rove.tool.failed",
                            **trace_identity(),
                            **parent_identity,
                            "data": {
                                "tool_name": tool.name,
                                "tool_call_id": call_id,
                                "error_type": type(exc).__name__,
                            },
                        }
                    )
                    raise
                recorder.emit(
                    {
                        "id": str(uuid4()),
                        "type": "rove.tool.completed",
                        **trace_identity(),
                        **parent_identity,
                        "data": {"tool_name": tool.name, "tool_call_id": call_id},
                    }
                )
                recorder.check()
                return result

            values = {
                "name": definition.name,
                "description": definition.description,
                "parameters": definition.parameters,
                "handler": invoke,
                "skip_permission": True,
            }
            if self._factory:
                prepared.append(values)
            else:
                from copilot import Tool

                prepared.append(Tool(**values))
        return prepared

    def _capture_native(self, path: Path, recorder: _Recorder) -> int:
        from rove.orchestrator.recording import current_exporter
        from rove.runtime.telemetry import native_span

        if not path.exists():
            return 0
        count = 0
        with path.open("rb") as stream:
            for _ in range(self.config.max_events + 1):
                line = stream.readline(self.config.max_event_bytes + 1)
                if not line:
                    break
                if len(line) > self.config.max_event_bytes:
                    raise RuntimeRecordingError("Native trace record exceeds capture limit")
                raw = json.loads(line)
                if raw.get("type") != "span":
                    continue
                event, payload = native_span(raw)
                event.update(
                    session_id=recorder.session_id,
                    stage=recorder.stage,
                    role=self.config.role,
                    source_clock_id="copilot:" + recorder.session_id,
                    received_at=datetime.now(UTC).isoformat(),
                )
                # Native span IDs have a distinct namespace from SDK event IDs.
                source_id = event["source_event_id"]
                fingerprint = hashlib.sha256(
                    json.dumps(raw, sort_keys=True, allow_nan=False).encode()
                ).hexdigest()
                if source_id in recorder.seen:
                    if recorder.seen[source_id] != fingerprint:
                        raise RuntimeRecordingError("Conflicting native span identity")
                    continue
                if recorder.count >= self.config.max_events:
                    raise RuntimeRecordingError("Native trace capture limit exceeded")
                if recorder.sink:
                    pending = recorder.sink(event)
                    if inspect.isawaitable(pending):
                        if inspect.iscoroutine(pending):
                            pending.close()
                        raise RuntimeRecordingError("Runtime event sink must record synchronously")
                recorder.seen[source_id] = fingerprint
                recorder.count += 1
                count += 1
                if exporter := current_exporter():
                    exporter.submit(payload)
            else:
                raise RuntimeRecordingError("Native trace file exceeds event limit")
        return count

    async def run_stage(
        self, stage: str, image_base64: str, task: str, context: dict | None = None
    ) -> dict:
        session_id = str(uuid4())
        recorder = _Recorder(self.config, self.event_sink, session_id, stage)
        started = time.monotonic()
        # Inputs cannot override role, tools, provider, memory, deadlines or prompt.
        prompt = json.dumps(
            {"stage": stage, "task": task, "context": context or {}}, allow_nan=False
        )
        attachments = []
        if image_base64:
            content = image_base64
            mime = "image/png"
            if content.startswith("data:"):
                header, content = content.split(",", 1)
                mime = header[5:].split(";", 1)[0]
            raw = base64.b64decode(content, validate=True)
            if len(raw) > 20_000_000:
                raise ValueError("Runtime image exceeds 20 MB")
            if raw.startswith(b"\xff\xd8\xff"):
                mime = "image/jpeg"
            if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                raise ValueError("Unsupported runtime image type")
            attachments.append(
                {"type": "blob", "data": content, "mimeType": mime, "displayName": "case-image"}
            )
        client = session = None
        failure_wait = operation = None
        cleanup_errors: list[str] = []
        result: dict | None = None
        ready_at = finished_at = None
        native_count = 0
        with tempfile.TemporaryDirectory(prefix="rove-agent-") as workspace:
            try:
                async with asyncio.timeout(self.config.timeout_seconds):
                    client = await self._client(workspace)
                    await client.start()
                    session = await client.create_session(
                        model=self.config.model,
                        provider=dict(self.config.provider),
                        session_id=session_id,
                        available_tools=[tool.name for tool in self.tools],
                        tools=self._sdk_tools(recorder),
                        on_event=recorder.emit,
                        streaming=True,
                        system_message={"mode": "replace", "content": self.config.system_message},
                        skip_custom_instructions=True,
                        enable_config_discovery=False,
                        enable_file_hooks=False,
                        enable_host_git_operations=False,
                        enable_skills=False,
                        enable_session_store=False,
                    )
                    recorder.check()
                    ready_at = time.monotonic()
                    operation = asyncio.create_task(
                        session.send_and_wait(
                            prompt,
                            attachments=attachments,
                            timeout=self.config.timeout_seconds,
                        )
                    )
                    failure_wait = asyncio.create_task(recorder.failed.wait())
                    await asyncio.wait(
                        {operation, failure_wait}, return_when=asyncio.FIRST_COMPLETED
                    )
                    recorder.check()
                    response = await operation
                    raw_response = _plain(response) if response is not None else {}
                    output = raw_response.get("data", {}).get("content")
                    if not isinstance(output, str):
                        raise ValueError("Copilot stage returned no structured output")
                    if len(output.encode()) > 1_000_000:
                        raise ValueError("Copilot stage output exceeds 1 MB")
                    result = json.loads(output)
                    if not isinstance(result, dict):
                        raise ValueError("Copilot stage output must be a JSON object")
                    finished_at = time.monotonic()
            finally:
                for pending in (operation, failure_wait):
                    if pending is not None and not pending.done():
                        pending.cancel()
                await asyncio.gather(
                    *(p for p in (operation, failure_wait) if p), return_exceptions=True
                )
                # Always abort before disconnect: send_and_wait timeout alone
                # does not stop model/tool work. This does not stop hardware.
                for owner, method in (
                    (session, "abort"),
                    (session, "disconnect"),
                    (client, "stop"),
                ):
                    if owner is not None:
                        try:
                            await asyncio.wait_for(
                                getattr(owner, method)(), self.config.cleanup_timeout_seconds
                            )
                        except Exception as exc:
                            cleanup_errors.append(type(exc).__name__)
                            recorder.emit(
                                {
                                    "id": str(uuid4()),
                                    "type": "rove.runtime.cleanup_failed",
                                    "data": {"error_type": type(exc).__name__, "kind": method},
                                }
                            )
                            if method == "stop" and hasattr(client, "force_stop"):
                                await asyncio.wait_for(
                                    client.force_stop(), self.config.cleanup_timeout_seconds
                                )
                if self.config.native_traces:
                    native_count = self._capture_native(
                        Path(workspace) / "runtime-traces.jsonl", recorder
                    )
        recorder.check()
        if cleanup_errors:
            raise RuntimeError("Copilot runtime cleanup failed: " + ", ".join(cleanup_errors))
        if result is None:
            raise RuntimeError("Copilot stage terminated without an output")
        result["_runtime"] = {
            "provider": "copilot",
            "sdk_version": SDK_VERSION,
            "cli_version": self.config.expected_cli_version,
            "model": self.config.model,
            "role": self.config.role,
            "session_id": session_id,
            "memory": "fresh_per_stage",
            "reset": "fresh_process_session",
            "event_count": recorder.count,
            "usage": recorder.usage or None,
            "elapsed_ms": (time.monotonic() - started) * 1000,
            "telemetry_coverage": "sdk_events_and_native_spans" if native_count else "sdk_events",
            "native_span_count": native_count,
            "native_capture": "captured"
            if native_count
            else "not_observed"
            if self.config.native_traces
            else "disabled",
            "startup_ms": (ready_at - started) * 1000 if ready_at else None,
            "inference_ms": (finished_at - ready_at) * 1000 if finished_at and ready_at else None,
            "cleanup_ms": (time.monotonic() - finished_at) * 1000 if finished_at else None,
            "content_captured": self.config.capture_content,
        }
        return result
