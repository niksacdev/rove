"""Bounded optional OTLP/HTTP JSON export, independent of durable trial evidence.

The worker never executes callbacks or waits on the evaluation event loop. It
uses one fixed socket deadline, no retries, no redirects and no ambient proxies.
Closing drops queued monitoring copies; it does not discard local journal data.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from rove.models.config import ObservabilityConfig


def _value(value):
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, list | tuple):
        return {"arrayValue": {"values": [_value(v) for v in value]}}
    return {"stringValue": str(value)}


def attributes(values):
    return [{"key": key, "value": _value(value)} for key, value in values.items()]


def otlp_payload(span: dict, *, resource: dict | None = None, scope="rove") -> dict:
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": attributes(resource or {"service.name": "rove"})},
                "scopeSpans": [{"scope": {"name": scope}, "spans": [span]}],
            }
        ]
    }


def readable_span(span) -> dict:
    context = span.get_span_context()
    result = {
        "traceId": f"{context.trace_id:032x}",
        "spanId": f"{context.span_id:016x}",
        "name": span.name,
        "kind": span.kind.value + 1,
        "startTimeUnixNano": str(span.start_time),
        "endTimeUnixNano": str(span.end_time),
        "attributes": attributes(dict(span.attributes or {})),
        "status": {"code": span.status.status_code.value},
    }
    if span.parent:
        result["parentSpanId"] = f"{span.parent.span_id:016x}"
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class BoundedOTLPExporter:
    """SpanExporter-compatible monitoring sink with explicit disposal bounds."""

    def __init__(self, config: ObservabilityConfig):
        if not config.enabled or not config.endpoint:
            raise ValueError("An enabled collector profile is required")
        self.config = config
        self.endpoint = config.endpoint.rstrip("/")
        if not self.endpoint.endswith("/v1/traces"):
            self.endpoint += "/v1/traces"
        self._queue: queue.Queue[bytes] = queue.Queue(config.queue_capacity)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._counts = dict(queued=0, sent=0, failed=0, dropped=0, in_flight=0)
        self._thread = threading.Thread(target=self._run, name="rove-monitor-export", daemon=True)
        self._thread.start()

    def submit(self, payload: dict) -> bool:
        try:
            encoded = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
            if len(encoded) > self.config.max_payload_bytes or self._stop.is_set():
                raise ValueError("Monitoring copy exceeds limits or exporter is closed")
            self._queue.put_nowait(encoded)
            with self._lock:
                self._counts["queued"] += 1
            return True
        except (ValueError, TypeError, queue.Full):
            with self._lock:
                self._counts["dropped"] += 1
            return False

    def export(self, spans):
        from opentelemetry.sdk.trace.export import SpanExportResult

        accepted = all([self.submit(otlp_payload(readable_span(span))) for span in spans])
        return SpanExportResult.SUCCESS if accepted else SpanExportResult.FAILURE

    def _run(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        while not self._stop.is_set():
            try:
                payload = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            with self._lock:
                self._counts["in_flight"] = 1
            try:
                request = urllib.request.Request(
                    self.endpoint, payload, {"Content-Type": "application/json"}, method="POST"
                )
                with opener.open(request, timeout=self.config.request_timeout_s) as response:
                    success = 200 <= response.status < 300
                with self._lock:
                    self._counts["sent" if success else "failed"] += 1
            except Exception:
                with self._lock:
                    self._counts["failed"] += 1
            finally:
                with self._lock:
                    self._counts["in_flight"] = 0
                self._queue.task_done()

    def status(self) -> dict:
        with self._lock:
            return {
                **self._counts,
                "pending": self._queue.qsize(),
                "worker_alive": self._thread.is_alive(),
            }

    def shutdown(self):
        deadline = time.monotonic() + self.config.shutdown_timeout_s
        while (
            self._queue.unfinished_tasks or self.status()["in_flight"]
        ) and time.monotonic() < deadline:
            self._stop.wait(min(0.005, max(0, deadline - time.monotonic())))
        self._stop.set()
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                with self._lock:
                    self._counts["dropped"] += 1
            except queue.Empty:
                break
        self._thread.join(timeout=max(0, deadline - time.monotonic()))

    def force_flush(self, timeout_millis=0):
        # A flush must never wait on a collector; use status for delivery evidence.
        return self._queue.empty() and self.status()["in_flight"] == 0


def native_span(record: dict[str, Any]) -> tuple[dict, dict]:
    """Convert the pinned CLI file record without inventing clocks or parentage."""
    import re

    trace_id, span_id = record.get("traceId", ""), record.get("spanId", "")
    if (
        not isinstance(trace_id, str)
        or not isinstance(span_id, str)
        or not re.fullmatch(r"[0-9a-f]{32}", trace_id)
        or not re.fullmatch(r"[0-9a-f]{16}", span_id)
        or int(trace_id, 16) == 0
        or int(span_id, 16) == 0
    ):
        raise ValueError("Native span lacks a valid source identity")

    def nanoseconds(value):
        if (
            not isinstance(value, list)
            or len(value) != 2
            or any(type(part) is not int for part in value)
            or value[0] < 0
            or not 0 <= value[1] < 1_000_000_000
        ):
            raise ValueError("Native span timestamp is not the pinned SDK clock format")
        return str(value[0] * 1_000_000_000 + value[1])

    start, end = nanoseconds(record.get("startTime")), nanoseconds(record.get("endTime"))
    if int(end) < int(start):
        raise ValueError("Native span ends before it starts")
    parent = record.get("parentSpanId")
    if parent is not None and (
        not isinstance(parent, str) or not re.fullmatch(r"[0-9a-f]{16}", parent)
    ):
        raise ValueError("Native parent span identity is invalid")
    # Only capture structural metadata; source logs may contain unknown attributes
    # even when SDK content capture is disabled. Model and tool payloads stay local.
    raw_attributes = record.get("attributes", {})
    if not isinstance(raw_attributes, dict):
        raise ValueError("Native span attributes must be an object")
    metadata = {
        key: value
        for key, value in raw_attributes.items()
        if key
        in {
            "gen_ai.conversation.id",
            "github.copilot.external_tool.name",
            "github.copilot.external_tool.call_id",
            "gen_ai.operation.name",
            "gen_ai.request.model",
            "gen_ai.response.model",
        }
        and isinstance(value, str | bool | int | float)
    }
    status_code = record.get("status", {}).get("code", 0)
    if type(status_code) is not int or status_code not in {0, 1, 2}:
        raise ValueError("Native span status code is invalid")
    # Error descriptions can include customer prompts or credentials. Only the
    # status code belongs in structural monitoring exports.
    status = {"code": status_code}
    event = {
        "source": "copilot_otel",
        "source_event_id": "native-span:" + span_id,
        "event_type": "native.span",
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent,
        "start_time_unix_nano": start,
        "end_time_unix_nano": end,
        "source_timestamp": start,
        "time_unit": "ns",
        "clock_alignment": "unverified",
        "data": {
            "name": record.get("name", "native.span"),
            "attributes": metadata,
            "status": status,
        },
    }
    span = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": event["data"]["name"],
        "kind": record.get("kind", 0) + 1,
        "startTimeUnixNano": start,
        "endTimeUnixNano": end,
        "attributes": attributes(metadata),
        "status": status,
    }
    if parent:
        span["parentSpanId"] = parent
    resource = record.get("resource", {}).get("attributes", {})
    return event, otlp_payload(
        span,
        resource={
            key: value
            for key, value in resource.items()
            if key in {"service.name", "service.version"}
        },
        scope="github.copilot",
    )
