"""Optional durable progress and evidence access for trusted evaluation adapters.

This is a host integration contract, not a sandbox. Candidate inputs and private
references remain separate; the context contains only declared environment data.
"""

from __future__ import annotations

import copy
import re
import time
from pathlib import Path

from rove.trials.snapshots import canonical_json
from rove.trials.store import MAX_ASSET_BYTES, TrialStore

_RESERVED = frozenset(
    {"candidate.output", "case.reference", "assessment.output", "evaluation.error"}
)


class EvaluationContext:
    def __init__(self, store: TrialStore, trial_id: str, environment: dict):
        self._store = store
        self.trial_id = trial_id
        self.clock_id = f"worker:{trial_id}"
        self._environment = copy.deepcopy(environment)

    @property
    def environment(self) -> dict:
        """A fresh copy prevents an adapter from changing the frozen declaration."""
        return copy.deepcopy(self._environment)

    @property
    def evidence_ids(self) -> frozenset[str]:
        """Only currently available, integrity-checked evidence may support grading."""
        return frozenset(
            ref["id"]
            for ref in self._store.evidence(self.trial_id)
            if ref["availability"] == "available"
        )

    def evidence_digest(self, reference_id: str) -> str:
        """Return the verified stored hash for comparing an assessment input to evidence."""
        for reference in self._store.evidence(self.trial_id):
            if reference["id"] == reference_id and reference["availability"] == "available":
                return reference["asset_sha256"]
        raise ValueError("Evidence is missing or changed")

    def emit(
        self,
        stage: str,
        phase: str,
        *,
        name: str | None = None,
        data: dict | None = None,
        span_id: str | None = None,
        evidence_refs: list[str] | None = None,
    ) -> None:
        """Append before continuing so an interrupted episode retains its progress."""
        for value in (stage, phase):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", value):
                raise ValueError("Progress stage and phase must be bounded identifiers")
        refs = list(evidence_refs or [])
        if refs and set(refs) - self.evidence_ids:
            raise ValueError("Progress refers to evidence that was not recorded")
        self._store.append_event(
            self.trial_id,
            {
                "event_type": f"evaluation.{phase}",
                "stage": stage,
                "name": name or f"{stage.title()} {phase}",
                "data": copy.deepcopy(data or {}),
                "source": "rove.evaluation",
                "source_clock_id": self.clock_id,
                "time_unit": "ns",
                "source_timestamp": time.monotonic_ns(),
                "span_id": span_id or f"{self.trial_id}:{stage}",
                "evidence_refs": refs,
            },
        )

    def record_bytes(
        self,
        reference_id: str,
        data: bytes,
        media_type: str,
        *,
        kind: str = "tool_output",
        units: dict[str, str] | None = None,
        clock_id: str | None = None,
        frame: str | None = None,
    ) -> dict:
        """Save at most 64 MiB using existing content-addressed managed assets."""
        if reference_id in _RESERVED:
            raise ValueError("Evidence ID is reserved for the evaluation lifecycle")
        return self._record(
            reference_id,
            data,
            media_type,
            kind=kind,
            units=units,
            clock_id=clock_id,
            frame=frame,
        )

    def record_json(self, reference_id: str, value, **metadata) -> dict:
        return self.record_bytes(
            reference_id, canonical_json(value).encode(), "application/json", **metadata
        )

    def record_file(self, reference_id: str, path: Path, media_type: str, **metadata) -> dict:
        """Read a bounded artifact produced by the trusted adapter, not a manifest URL."""
        with Path(path).open("rb") as stream:
            data = stream.read(MAX_ASSET_BYTES + 1)
        if len(data) > MAX_ASSET_BYTES:
            raise ValueError("Artifact exceeds 64 MiB; record bounded segments")
        return self.record_bytes(reference_id, data, media_type, **metadata)

    def _record(
        self,
        reference_id: str,
        data: bytes,
        media_type: str,
        *,
        kind: str = "tool_output",
        units: dict[str, str] | None = None,
        clock_id: str | None = None,
        frame: str | None = None,
    ) -> dict:
        asset = self._store.save_asset(data, media_type)
        reference = {
            "id": reference_id,
            "asset_sha256": asset["sha256"],
            "kind": kind,
            "units": units or {},
        }
        if clock_id is not None:
            reference["clock_id"] = clock_id
        if frame is not None:
            reference["frame"] = frame
        self._store.attach_evidence(self.trial_id, reference)
        return {**reference, "asset": asset}
