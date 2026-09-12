"""Local monitoring failures must not become missing evaluation evidence.

The CLI integration uses a real loopback HTTP endpoint and the installed pinned
SDK/runtime, with synthetic model transport. It does not call a model provider or
validate a hosted telemetry product.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from rove.orchestrator import recording
from rove.runtime.copilot import CLI_VERSION, CopilotRuntime
from rove.trials.store import TrialStore


def _begin(store):
    return store.begin(
        source="quick",
        task={"instruction": "Plan a synthetic red cube placement"},
        strategy={"id": "telemetry-fixture"},
        config={"evidence_quality": "synthetic"},
    )


@pytest.mark.parametrize("raises", [False, True])
def test_failed_span_export_preserves_local_events_and_terminal_result(
    tmp_path, monkeypatch, raises
):
    trace = pytest.importorskip("opentelemetry.sdk.trace")
    export = pytest.importorskip("opentelemetry.sdk.trace.export")

    class FailingExporter(export.SpanExporter):
        def __init__(self):
            self.attempts = 0
            self.closed = False

        def export(self, spans):
            self.attempts += len(spans)
            if raises:
                raise ConnectionError("Controlled collector failure")
            return export.SpanExportResult.FAILURE

        def shutdown(self):
            self.closed = True

    exporter = FailingExporter()
    provider = trace.TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(export.SimpleSpanProcessor(exporter))
    monkeypatch.setattr(recording, "_tracer", lambda: provider.get_tracer("rove.test"))
    store = TrialStore(tmp_path)
    trial_id = _begin(store)
    token = recording.event_sink.set(lambda event: store.append_event(trial_id, event))
    try:
        with recording.trial_span(trial_id):
            recording.record_event(
                {
                    "event_type": "assistant.usage",
                    "source_event_id": "usage-1",
                    "data": {"input_tokens": 12, "output_tokens": 8},
                }
            )
        store.finish(
            trial_id,
            status="completed",
            result={"outcome": "pass", "evidence_quality": "synthetic"},
        )
    finally:
        recording.event_sink.reset(token)
        started = time.monotonic()
        provider.shutdown()
        shutdown_elapsed = time.monotonic() - started

    assert exporter.attempts == 1
    assert exporter.closed
    assert shutdown_elapsed < 1
    reopened = TrialStore(tmp_path)
    assert reopened.get(trial_id)["result"]["outcome"] == "pass"
    events = reopened.events(trial_id)
    assert [event["event_type"] for event in events] == [
        "trial.span.started",
        "assistant.usage",
        "trial.span.ended",
    ]
    assert events[1]["data"]["input_tokens"] == 12
    assert events[0]["trace_id"] == events[-1]["trace_id"]


@contextmanager
def _failed_collector():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(size)
            requests.append(
                {
                    "path": self.path,
                    "content_type": self.headers.get("Content-Type"),
                    "body": payload,
                }
            )
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
        assert not thread.is_alive(), "Controlled collector did not shut down"


async def test_real_sdk_cli_failed_otlp_collector_keeps_trial_and_usage(
    tmp_path, monkeypatch, request
):
    """The exact installed CLI exports to a real failing local OTLP HTTP server."""
    copilot = pytest.importorskip("copilot")
    sdk_trace = pytest.importorskip("opentelemetry.sdk.trace")
    from copilot._cli_download import get_cached_cli_path
    from opentelemetry import trace

    # The SDK's default resolver can download a runtime. Only inspect its cache,
    # explicit installations and wheel assets here: tests must stay offline.
    candidates = [os.environ.get("COPILOT_CLI_PATH"), shutil.which("copilot")]
    candidates.append(get_cached_cli_path(CLI_VERSION))
    candidates.extend(
        str(path) for path in (Path(copilot.__file__).parent / "bin").glob("copilot*")
    )
    cli = None
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file() or not os.access(candidate, os.X_OK):
            continue
        checked = subprocess.run(
            [candidate, "--version"], capture_output=True, text=True, timeout=10
        )
        if not checked.returncode and f" {CLI_VERSION}." in checked.stdout:
            cli = candidate
            break
    if cli is None:
        pytest.skip(f"Real collector integration requires Copilot CLI {CLI_VERSION}")

    # The standalone probe installs a provider globally. Keep that operation
    # private to this test so subsequent tests retain their original provider.
    provider = sdk_trace.TracerProvider(shutdown_on_exit=False)
    request.addfinalizer(provider.shutdown)
    monkeypatch.setattr(trace, "set_tracer_provider", lambda supplied: supplied.shutdown())
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)

    # Reuse the versioned real-runtime image/tool fixture. Its request handler
    # supplies model responses in process and refuses unrecognized network calls.
    path = Path(__file__).resolve().parents[1] / "scripts" / "probe_copilot.py"
    spec = importlib.util.spec_from_file_location("rove_local_collector_probe", path)
    assert spec is not None and spec.loader is not None
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    original_client = copilot.CopilotClient
    store = TrialStore(tmp_path / "journal")
    trial_id = _begin(store)
    timings = []

    class DurableRuntime(CopilotRuntime):
        def __init__(self, *args, **kwargs):
            original_sink = kwargs["event_sink"]

            def persist(event):
                store.append_event(trial_id, event)
                original_sink(event)

            kwargs["event_sink"] = persist
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(probe, "CopilotRuntime", DurableRuntime)
    with _failed_collector() as (endpoint, requests):

        def client(**kwargs):
            kwargs["telemetry"] = {
                "exporter_type": "otlp-http",
                "otlp_endpoint": endpoint,
                "otlp_protocol": "http/json",
                "capture_content": False,
            }
            instance = original_client(**kwargs)
            stop = instance.stop

            async def timed_stop():
                started = time.monotonic()
                try:
                    return await stop()
                finally:
                    timings.append(time.monotonic() - started)

            instance.stop = timed_stop
            return instance

        monkeypatch.setattr(copilot, "CopilotClient", client)
        result = await asyncio.wait_for(probe.probe(cli), timeout=55)
        assert result["passed"], result
        assert result["usage_events"] == 2
        assert result["trace_correlated"]
        assert requests, "A configured endpoint alone is not evidence that export was attempted"
        assert any(item["path"].endswith("/v1/traces") for item in requests)
        exported = [
            json.loads(item["body"]) for item in requests if item["path"].endswith("/v1/traces")
        ]
        assert any(item.get("resourceSpans") for item in exported)
        # The actual runtime's default cleanup limit is five seconds. Allow
        # scheduler overhead, but do not wait on an unbounded monitoring flush.
        assert timings and max(timings) < 6
        store.finish(
            trial_id,
            status="completed",
            result={"outcome": "pass", "evidence_quality": "synthetic", "probe": result},
        )

    reopened = TrialStore(tmp_path / "journal")
    assert reopened.get(trial_id)["status"] == "completed"
    assert reopened.get(trial_id)["result"]["outcome"] == "pass"
    usage = [
        event for event in reopened.events(trial_id) if event["event_type"] == "assistant.usage"
    ]
    assert len(usage) == 2
    assert all(event["data"]["input_tokens"] == 12 for event in usage)
    # Content capture is off; only synthetic fixture telemetry is used here.
    trace_payload = b"".join(item["body"] for item in requests)
    assert b"Place cube in tray" not in trace_payload
