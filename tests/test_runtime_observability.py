"""Real local transport and explicit clocks; no hosted model/monitoring claims."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from pydantic import ValidationError

from rove.models.config import EndpointConfig, ObservabilityConfig, effective_runtime_contract
from rove.orchestrator import recording
from rove.runtime.copilot import (
    CLI_VERSION,
    CopilotRuntime,
    CopilotRuntimeConfig,
    RuntimeRecordingError,
    _Recorder,
)
from rove.runtime.telemetry import BoundedOTLPExporter, native_span
from rove.trials.store import TrialStore


def test_runtime_contract_attributes_claims_and_rejects_unsupported_memory():
    direct = EndpointConfig(type="agent", adapter="mock")
    assert effective_runtime_contract(direct) == {
        "memory": "unknown",
        "reset": "unknown",
        "telemetry_coverage": "stage_only",
        "attribution": "unspecified",
    }
    declared = EndpointConfig(
        type="agent",
        adapter="mock",
        runtime_contract={
            "memory": "fresh_per_trial",
            "reset": "customer_managed",
        },
    )
    assert effective_runtime_contract(declared)["attribution"] == "customer_declared"
    hosted = EndpointConfig(type="agent", adapter="copilot_agent")
    assert effective_runtime_contract(hosted)["memory"] == "fresh_per_stage"
    with pytest.raises(ValidationError, match="fresh-stage"):
        EndpointConfig(
            type="agent",
            adapter="copilot_agent",
            runtime_contract={
                "memory": "fresh_per_trial",
            },
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.com",
        "http://localhost:4318",
        "http://127.0.0.1:abc",
        "http://user:secret@127.0.0.1",  # pragma: allowlist secret — deliberately rejected URL
        "http://127.0.0.1?token=secret",
        "file:///tmp/export",
    ],
)
def test_local_monitoring_has_no_ambient_network_or_credential_destinations(endpoint):
    with pytest.raises(ValidationError):
        ObservabilityConfig(enabled=True, endpoint=endpoint)


def test_source_clock_does_not_substitute_receipt_or_event_parent_for_span_parent():
    events = []
    recorder = _Recorder(
        CopilotRuntimeConfig("test", {"base_url": "http://127.0.0.1"}),
        events.append,
        "session-a",
        "plan",
    )
    recorder.emit(
        {
            "id": "one",
            "type": "assistant.usage",
            "parentId": "event-before",
            "timestamp": "2020-01-01T01:00:00+00:00",
            "data": {"input_tokens": 2},
        }
    )
    recorder.emit({"id": "two", "type": "assistant.usage", "data": {}})
    assert events[0]["source_timestamp"] == "2020-01-01T01:00:00+00:00"
    assert events[0]["received_at"] != events[0]["source_timestamp"]
    assert events[0]["source_clock_id"] == "copilot:session-a"
    assert events[0]["clock_alignment"] == "unverified"
    assert events[0]["previous_event_id"] == "event-before"
    assert "parent_span_id" not in events[0]
    assert events[1]["source_timestamp"] is None and events[1]["time_unit"] is None


def _native_record():
    return {
        "type": "span",
        "traceId": "a" * 32,
        "spanId": "b" * 16,
        "parentSpanId": "c" * 16,
        "name": "external_tool observe_case",
        "kind": 0,
        "startTime": [1720000000, 123456789],
        "endTime": [1720000000, 123456999],
        "attributes": {
            "github.copilot.external_tool.name": "observe_case",
            "gen_ai.input.messages": "private prompt",
        },
        "status": {"code": 2, "message": "private provider response"},
        "resource": {"attributes": {"service.name": "github-copilot", "host.name": "private"}},
    }


def test_native_spans_preserve_exact_source_time_and_redact_unreviewed_content():
    event, payload = native_span(_native_record())
    assert event["start_time_unix_nano"] == "1720000000123456789"
    assert event["end_time_unix_nano"] == "1720000000123456999"
    assert event["parent_span_id"] == "c" * 16
    assert event["time_unit"] == "ns" and event["clock_alignment"] == "unverified"
    assert "private" not in json.dumps([event, payload])
    assert event["data"]["status"] == {"code": 2}


def test_native_duplicate_is_idempotent_but_conflicting_source_fails(tmp_path):
    config = CopilotRuntimeConfig("test", {"base_url": "http://127.0.0.1"})
    events = []
    recorder = _Recorder(config, events.append, "session-a", "plan")
    host = CopilotRuntime(config)
    path = tmp_path / "trace.jsonl"
    source = _native_record()
    path.write_text(json.dumps(source) + "\n" + json.dumps(source) + "\n")
    assert host._capture_native(path, recorder) == 1
    assert host._capture_native(path, recorder) == 0
    source["endTime"][1] += 1
    path.write_text(json.dumps(source) + "\n")
    with pytest.raises(RuntimeRecordingError, match="Conflicting native"):
        host._capture_native(path, recorder)


def test_stage_cancellation_is_attributed_without_leaking_exception_message(monkeypatch):
    events = []
    monkeypatch.setattr(recording, "_tracer", lambda: None)
    token = recording.event_sink.set(events.append)
    try:
        with (
            pytest.raises(asyncio.CancelledError),
            recording.stage_span("verify", "grader"),
            recording.stage_span("verify", "grader"),
        ):
            raise asyncio.CancelledError("private task contents")
    finally:
        recording.event_sink.reset(token)
    assert [e["event_type"] for e in events] == [
        "rove.stage.started",
        "rove.stage.cancelled",
        "rove.stage.ended",
    ]
    assert all(
        e["role"] == "grader" and e["source_clock_id"] == recording.HOST_CLOCK_ID for e in events
    )
    assert "private" not in json.dumps(events)


@contextmanager
def collector(mode="ok"):
    requests = []
    received, release = threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            received.set()
            if mode == "blackhole":
                release.wait(3)
                return
            self.send_response(200 if mode == "ok" else 503)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests, received
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(1)
        assert not thread.is_alive()


def test_blackhole_and_queue_overflow_cannot_block_shutdown_or_hide_gaps():
    with collector("blackhole") as (endpoint, requests, received):
        exporter = BoundedOTLPExporter(
            ObservabilityConfig(
                enabled=True,
                endpoint=endpoint,
                queue_capacity=1,
                request_timeout_s=0.2,
                shutdown_timeout_s=0.02,
            )
        )
        assert exporter.submit({"resourceSpans": []})
        assert received.wait(1)
        assert exporter.submit({"resourceSpans": []})
        assert not exporter.submit({"resourceSpans": []})
        started = time.monotonic()
        exporter.shutdown()
        assert time.monotonic() - started < 0.15
        assert exporter.status()["dropped"] == 2
        assert not exporter.submit({"resourceSpans": []})
        exporter._thread.join(1)
        assert not exporter.status()["worker_alive"]
        assert exporter.status()["failed"] == 1
        assert len(requests) == 1, "No monitoring retry is allowed"


def _cli_path():
    copilot = pytest.importorskip("copilot")
    from copilot._cli_download import get_cached_cli_path

    candidates = [
        os.environ.get("COPILOT_CLI_PATH"),
        shutil.which("copilot"),
        get_cached_cli_path(CLI_VERSION),
    ]
    candidates.extend(
        str(path) for path in (Path(copilot.__file__).parent / "bin").glob("copilot*")
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    pytest.skip("Pinned local CLI is unavailable; no runtime download is permitted")


@pytest.mark.parametrize("mode", ["ok", "blackhole", "unavailable"])
async def test_real_sdk_trial_stage_native_tool_ancestry_survives_collector_outage(
    tmp_path, monkeypatch, request, mode
):
    """Actual SDK/CLI and loopback HTTP; all model responses supplied in-process."""
    sdk = pytest.importorskip("opentelemetry.sdk.trace")
    from opentelemetry import trace

    cli = _cli_path()
    provider = sdk.TracerProvider(shutdown_on_exit=False)
    request.addfinalizer(provider.shutdown)
    monkeypatch.setattr(trace, "set_tracer_provider", lambda supplied: supplied.shutdown())
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)
    path = Path(__file__).resolve().parents[1] / "scripts/probe_copilot.py"
    spec = importlib.util.spec_from_file_location("rove_ancestry_probe", path)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    store = TrialStore(tmp_path / "journal")
    trial_id = store.begin(
        source="quick",
        task={"instruction": "synthetic red cube"},
        strategy={"id": "controlled-sdk"},
        config={"synthetic": True},
    )
    observed = []

    with collector(mode) as (endpoint, requests, received):
        profile = ObservabilityConfig(
            enabled=True, endpoint=endpoint, request_timeout_s=0.2, shutdown_timeout_s=0.1
        )

        class DurableRuntime(CopilotRuntime):
            def __init__(self, *args, **kwargs):
                sink = kwargs["event_sink"]

                def persist(event):
                    store.append_event(trial_id, event)
                    sink(event)

                kwargs["event_sink"] = persist
                super().__init__(*args, **kwargs)

            async def run_stage(self, *args, **kwargs):
                token = recording.event_sink.set(lambda e: store.append_event(trial_id, e))
                try:
                    with (
                        recording.trial_span(trial_id, profile),
                        recording.stage_span("plan", "controlled-sdk"),
                    ):
                        result = await super().run_stage(*args, **kwargs)
                        observed.append(result["_runtime"])
                        return result
                finally:
                    recording.event_sink.reset(token)

        monkeypatch.setattr(probe, "CopilotRuntime", DurableRuntime)
        result = await asyncio.wait_for(probe.probe(cli), 30)
        store.finish(
            trial_id, status="completed", result={"outcome": "pass", "quality": "synthetic"}
        )
        assert result["passed"] and result["usage_events"] == 2
        assert received.is_set()
        assert observed[0]["native_capture"] == "captured"
        assert observed[0]["native_span_count"] > 0
        assert all(observed[0][key] >= 0 for key in ("startup_ms", "inference_ms", "cleanup_ms"))

    reopened = TrialStore(tmp_path / "journal")
    events = reopened.events(trial_id)
    assert reopened.get(trial_id)["result"]["outcome"] == "pass"
    root = next(e for e in events if e["event_type"] == "trial.span.started")
    stage = next(e for e in events if e["event_type"] == "rove.stage.started")
    tool = next(e for e in events if e["event_type"] == "rove.tool.observe_case.started")
    native = [e for e in events if e["event_type"] == "native.span"]
    assert stage["parent_span_id"] == root["span_id"]
    assert root["trace_id"] == stage["trace_id"] == tool["trace_id"]
    spans = {e["span_id"]: e for e in [*native, root, stage, tool]}
    cursor, visited = tool["span_id"], set()
    while cursor != root["span_id"]:
        assert cursor not in visited
        visited.add(cursor)
        cursor = spans[cursor]["parent_span_id"]
    assert stage["span_id"] in visited and any(e["span_id"] in visited for e in native)
    assert all(e["source_clock_id"].startswith("copilot:") for e in native)
    assert all(e["clock_alignment"] == "unverified" for e in native)
    assert len([e for e in events if e["event_type"] == "assistant.usage"]) == 2
    status = next(e for e in events if e["event_type"] == "telemetry.export.status")["data"]
    if mode == "ok":
        delivered = [
            span
            for payload in requests
            for resource in payload["resourceSpans"]
            for scope in resource["scopeSpans"]
            for span in scope["spans"]
        ]
        assert {root["span_id"], stage["span_id"], tool["span_id"]} <= {
            s["spanId"] for s in delivered
        }
        assert any(
            s["spanId"] == tool["parent_span_id"]
            for s in delivered
            if s["name"] == "execute_tool observe_case"
        )
        # SDK 1.0.13 restores execute_tool as the Python callback's parent;
        # its external_tool span is a sibling, not a fabricated intermediate.
        assert any(
            s.get("parentSpanId") == tool["parent_span_id"]
            for s in delivered
            if s["name"] == "external_tool observe_case"
        )
        assert status["failed"] == status["dropped"] == status["pending"] == 0
    else:
        assert status["failed"] + status["dropped"] + status["in_flight"] > 0
    assert "private prompt" not in json.dumps(requests)
