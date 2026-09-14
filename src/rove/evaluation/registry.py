"""Only built-in or explicitly installed host entry points may execute Python."""

from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
import re
from pathlib import Path

from rove.evaluation.protocols import EvaluationAdapter


def resolve(name: str) -> tuple[type[EvaluationAdapter], dict]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", name) or name == "robotics":
        raise ValueError("Invalid evaluation executor name")
    entries = list(importlib.metadata.entry_points(group="rove.evaluations", name=name))
    if name == "text-example":
        from rove.evaluation.examples import TextEvaluation

        if entries:
            raise ValueError("Installed executor conflicts with the built-in text-example")
        factory, distribution = TextEvaluation, "rove-eval"
    else:
        if len(entries) != 1:
            raise ValueError(f"Install exactly one trusted rove.evaluations executor named {name}")
        factory = entries[0].load()
        distribution = entries[0].dist.name if entries[0].dist else "unknown"
    # The parent only inspects metadata. Model loading belongs inside the timed worker.
    if not inspect.isclass(factory) or not isinstance(factory, EvaluationAdapter):
        raise ValueError("Executor entry point must name an EvaluationAdapter class")
    revision = factory.revision
    if not isinstance(revision, str) or not revision.strip() or len(revision) > 160:
        raise ValueError("Executor revision must contain 1-160 characters")
    sources = {}
    for name_, value in (
        ("adapter", factory),
        ("candidate", factory.predict),
        ("grader", factory.grade),
    ):
        path = inspect.getsourcefile(value)
        if not path:
            raise ValueError("Executor requires inspectable Python source for provenance")
        sources[name_] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return factory, {
        "name": name,
        "revision": revision,
        "distribution": distribution,
        "sources": sources,
    }
