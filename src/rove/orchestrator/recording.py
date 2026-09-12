"""Per-attempt durable event context, independent of any agent runtime."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

event_sink: ContextVar[Callable[[dict], None] | None] = ContextVar("rove_event_sink", default=None)


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


@contextmanager
def trial_span(trial_id: str | None):
    tracer = _tracer()
    if tracer is None or trial_id is None:
        yield
        return
    with tracer.start_as_current_span("rove.trial", attributes={"rove.trial.id": trial_id}) as span:
        context = span.get_span_context()
        identity = {"trace_id": f"{context.trace_id:032x}", "span_id": f"{context.span_id:016x}"}
        record_event({"event_type": "trial.span.started", **identity})
        try:
            yield
        finally:
            record_event({"event_type": "trial.span.ended", **identity})
