"""Per-attempt durable event context, independent of any agent runtime."""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import lru_cache
from uuid import uuid4

event_sink: ContextVar[Callable[[dict], None] | None] = ContextVar("rove_event_sink", default=None)
_active_tracer: ContextVar[object | None] = ContextVar("rove_active_tracer", default=None)
_active_exporter: ContextVar[object | None] = ContextVar("rove_active_exporter", default=None)
_active_stage: ContextVar[tuple[str, str] | None] = ContextVar("rove_active_stage", default=None)
HOST_CLOCK_ID = "rove-process:" + str(uuid4())


def source_clock() -> dict:
    return {
        "source": "rove",
        "source_clock_id": HOST_CLOCK_ID,
        "source_timestamp": datetime.now(UTC).isoformat(),
        "time_unit": "iso8601",
        "source_monotonic_ns": str(time.monotonic_ns()),
        "clock_alignment": "same_process",
    }


def current_exporter():
    return _active_exporter.get()


def capture_recording_context():
    """SDK callback tasks restore OTel parentage, but not application ContextVars."""
    return {
        variable: variable.get()
        for variable in (
            event_sink,
            _active_tracer,
            _active_exporter,
            _active_stage,
        )
    }


@contextmanager
def use_recording_context(values):
    # Copy only ROVE's recording scope. Replacing the entire Python context here
    # would erase the native parent span restored by the SDK tool dispatcher.
    tokens = [(variable, variable.set(value)) for variable, value in values.items()]
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


def record_event(event: dict) -> None:
    """Recorder errors are execution errors; never silently lose required evidence."""
    sink = event_sink.get()
    if sink is not None:
        sink(event)


@lru_cache(maxsize=1)
def _tracer():
    try:
        from opentelemetry.sdk.trace import TracerProvider
    except ImportError:
        return None
    # A private provider avoids changing instrumentation configured by an embedder.
    return TracerProvider().get_tracer("rove.trials")


def _identity(span):
    if span is None:
        return {}
    context = span.get_span_context()
    if not context.is_valid:
        return {}
    value = {"trace_id": f"{context.trace_id:032x}", "span_id": f"{context.span_id:016x}"}
    parent = getattr(span, "parent", None)
    if parent:
        value["parent_span_id"] = f"{parent.span_id:016x}"
    return value


@contextmanager
def _span(name, fields):
    from contextlib import nullcontext

    tracer = _active_tracer.get() or _tracer()
    context = (
        tracer.start_as_current_span(
            name,
            attributes={
                f"rove.{key}": value
                for key, value in fields.items()
                if isinstance(value, str | bool | int | float)
            },
            record_exception=False,
            set_status_on_exception=False,
        )
        if tracer
        else nullcontext(None)
    )
    with context as span:
        identity = _identity(span)
        record_event({**source_clock(), **identity, **fields, "event_type": name + ".started"})
        try:
            yield span
        except BaseException as error:
            import asyncio

            if span is not None:
                from opentelemetry.trace import Status, StatusCode

                span.set_status(Status(StatusCode.ERROR, type(error).__name__))
            record_event(
                {
                    **source_clock(),
                    **identity,
                    **fields,
                    "event_type": name
                    + (".cancelled" if isinstance(error, asyncio.CancelledError) else ".failed"),
                    "error_type": type(error).__name__,
                }
            )
            raise
        finally:
            record_event({**source_clock(), **identity, **fields, "event_type": name + ".ended"})


@contextmanager
def stage_span(stage: str, endpoint_id: str, runtime_contract: dict | None = None):
    """Wrap actual adapter work; do not hold a span across async-generator yields."""
    if _active_stage.get() == (stage, endpoint_id):
        yield
        return
    token = _active_stage.set((stage, endpoint_id))
    try:
        with _span(
            "rove.stage",
            {
                "stage": stage,
                "endpoint_id": endpoint_id,
                "role": "grader" if stage == "verify" else "candidate",
                "runtime_contract": runtime_contract
                or {
                    "memory": "unknown",
                    "reset": "unknown",
                    "telemetry_coverage": "stage_only",
                    "attribution": "unspecified",
                },
            },
        ):
            yield
    finally:
        _active_stage.reset(token)


@contextmanager
def tool_span(name: str, role: str, stage: str, call_id: str | None = None):
    with _span(
        "rove.tool." + name,
        {"tool_name": name, "role": role, "stage": stage, "tool_call_id": call_id},
    ) as span:
        yield _identity(span)


@contextmanager
def trial_span(trial_id: str | None, profile=None):
    if trial_id is None:
        yield
        return
    exporter = provider = None
    if profile is None:
        try:
            from rove.models.config import load_config

            profile = load_config().defaults.observability
        except (ValueError, FileNotFoundError):
            profile = None
    if profile is not None and profile.enabled:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        from rove.runtime.telemetry import BoundedOTLPExporter

        exporter = BoundedOTLPExporter(profile)
        provider = TracerProvider(shutdown_on_exit=False)
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("rove.trials")
    else:
        tracer = _tracer()
    if tracer is None:
        yield
        return
    tracer_token = _active_tracer.set(tracer)
    exporter_token = _active_exporter.set(exporter)
    try:
        with tracer.start_as_current_span(
            "rove.trial",
            attributes={"rove.trial.id": trial_id},
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            identity = _identity(span)
            record_event({"event_type": "trial.span.started", **source_clock(), **identity})
            try:
                yield
            except BaseException as error:
                from opentelemetry.trace import Status, StatusCode

                span.set_status(Status(StatusCode.ERROR, type(error).__name__))
                raise
            finally:
                record_event({"event_type": "trial.span.ended", **source_clock(), **identity})
    finally:
        try:
            if provider:
                provider.shutdown()
            if exporter:
                record_event(
                    {
                        **source_clock(),
                        "event_type": "telemetry.export.status",
                        "data": exporter.status(),
                        "monitoring_only": True,
                    }
                )
        finally:
            _active_exporter.reset(exporter_token)
            _active_tracer.reset(tracer_token)
