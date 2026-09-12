"""Named baseline revisions preserve exact campaign and assessment identities."""

from __future__ import annotations

import json
import uuid

from pydantic import BaseModel, ConfigDict, Field

from rove.benchmarks.metrics import summarize
from rove.benchmarks.store import CampaignStore
from rove.datasets.service import ConflictError
from rove.trials.snapshots import canonical_json, content_hash, sanitize
from rove.trials.store import TrialStore, now


def migrate_local_workflow(db):
    """Called once by the shared trial schema migration, never by API handlers."""
    statements = (
        "CREATE TABLE named_baselines (id TEXT PRIMARY KEY, name_key TEXT NOT NULL UNIQUE, head_revision_id TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE TABLE baseline_revisions (id TEXT PRIMARY KEY, baseline_id TEXT NOT NULL REFERENCES named_baselines(id), revision INTEGER NOT NULL, parent_id TEXT, operation_id TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE TABLE assistant_operations (operation_id TEXT PRIMARY KEY, kind TEXT NOT NULL, request_hash TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE TABLE workflow_mutations (operation_id TEXT PRIMARY KEY, kind TEXT NOT NULL, request_hash TEXT NOT NULL, result_id TEXT NOT NULL, created_at TEXT NOT NULL)",
    )
    for statement in statements:
        db.execute(statement)


class BaselineInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=160)
    campaign_id: str = Field(min_length=1, max_length=128)
    strategy_id: str = Field(min_length=1, max_length=128)
    pinned: bool = True
    operation_id: str = Field(min_length=1, max_length=128)
    expected_head_revision_id: str | None = None


class BaselineStore:
    def __init__(self, root):
        self.root, self.trials = root, TrialStore(root)

    def capture(self, request: BaselineInput):
        from rove.benchmarks.workflow import assessed_trials

        store = CampaignStore(self.root)
        campaign = store.get(request.campaign_id)
        if campaign["status"] != "completed":
            raise ValueError("A named baseline requires a completed campaign")
        if request.strategy_id not in campaign["spec"]["strategies"]:
            raise ValueError("Baseline strategy must belong to the recorded campaign")
        assessed, assessments = assessed_trials(self.root, campaign, store.trials(campaign["id"]))
        # Persist the projection rather than copying raw observations and model outputs.
        outcomes = [
            {
                key: row[key]
                for key in (
                    "trial_id",
                    "task_id",
                    "strategy_id",
                    "seed",
                    "outcome",
                    "execution",
                    "latency_ms",
                    "assessment_ids",
                )
                if key in row
            }
            for row in assessed
            if row["strategy_id"] == request.strategy_id
        ]
        originals = {(row["task_id"], row["strategy_id"], row["seed"]): row for row in assessed}
        for outcome in outcomes:
            original = originals[outcome["task_id"], outcome["strategy_id"], outcome["seed"]]
            outcome["result"] = {
                "stages": [
                    stage
                    for stage in (original.get("result") or {}).get("stages", [])
                    if stage.get("stage") == "verify"
                ]
            }
        return {
            "name": request.name,
            "pinned": request.pinned,
            "campaign_id": request.campaign_id,
            "strategy_id": request.strategy_id,
            "campaign_hash": content_hash(campaign),
            "contract_id": campaign.get("contract_id"),
            "dataset_revision_id": campaign.get("dataset_revision_id"),
            "case_revision_ids": campaign.get("case_revision_ids", []),
            "assessment_set_hash": assessments.get("assessment_set_hash"),
            "assessments": assessments,
            "trial_outcomes": outcomes,
            "summary": summarize(
                {**campaign, "spec": {**campaign["spec"], "strategies": [request.strategy_id]}},
                outcomes,
            ),
            "note": "A baseline preserves this assessment projection. Later reviews do not rewrite it. Unknown outcomes remain unknown.",
        }

    def save(self, request: BaselineInput, baseline_id=None, *, expected_capture_hash=None):
        fingerprint = content_hash({"baseline_id": baseline_id, **request.model_dump()})
        with self.trials.connect() as db:
            previous = db.execute(
                "SELECT request_hash,payload FROM baseline_revisions WHERE operation_id=?",
                (request.operation_id,),
            ).fetchone()
        if previous:
            if previous["request_hash"] != fingerprint:
                raise ConflictError(
                    "Operation identity already belongs to a different baseline change"
                )
            return json.loads(previous["payload"])
        payload = self.capture(request)
        if expected_capture_hash and content_hash(payload) != expected_capture_hash:
            raise ConflictError("Baseline evidence changed; preview the current assessment again")
        identity = uuid.uuid4().hex
        created = now()
        with self.trials.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            repeated = db.execute(
                "SELECT request_hash,payload FROM baseline_revisions WHERE operation_id=?",
                (request.operation_id,),
            ).fetchone()
            if repeated:
                if repeated["request_hash"] != fingerprint:
                    raise ConflictError("Operation identity changed")
                return json.loads(repeated["payload"])
            if baseline_id:
                baseline = db.execute(
                    "SELECT * FROM named_baselines WHERE id=?", (baseline_id,)
                ).fetchone()
                if baseline is None:
                    raise KeyError(baseline_id)
                if baseline["head_revision_id"] != request.expected_head_revision_id:
                    raise ConflictError("Baseline changed; refresh before saving")
                parent = baseline["head_revision_id"]
                revision = (
                    db.execute(
                        "SELECT revision FROM baseline_revisions WHERE id=?", (parent,)
                    ).fetchone()[0]
                    + 1
                )
            else:
                if request.expected_head_revision_id:
                    raise ValueError("New baselines cannot have a previous head revision")
                baseline_id, parent, revision = uuid.uuid4().hex, None, 1
            collision = db.execute(
                "SELECT id FROM named_baselines WHERE name_key=? AND id<>?",
                (request.name.casefold(), baseline_id),
            ).fetchone()
            if collision:
                raise ConflictError("A baseline already uses this name")
            result = {
                **sanitize(payload),
                "id": baseline_id,
                "revision_id": identity,
                "revision": revision,
                "parent_id": parent,
                "created_at": created,
            }
            if parent:
                db.execute(
                    "UPDATE named_baselines SET name_key=?,head_revision_id=? WHERE id=?",
                    (request.name.casefold(), identity, baseline_id),
                )
            else:
                db.execute(
                    "INSERT INTO named_baselines VALUES (?,?,?,?)",
                    (baseline_id, request.name.casefold(), identity, created),
                )
            db.execute(
                "INSERT INTO baseline_revisions VALUES (?,?,?,?,?,?,?,?)",
                (
                    identity,
                    baseline_id,
                    revision,
                    parent,
                    request.operation_id,
                    fingerprint,
                    canonical_json(result),
                    created,
                ),
            )
        return result

    def get(self, identity):
        with self.trials.connect() as db:
            row = db.execute(
                "SELECT r.payload FROM baseline_revisions r LEFT JOIN named_baselines b ON b.head_revision_id=r.id WHERE b.id=? OR r.id=?",
                (identity, identity),
            ).fetchone()
        if row is None:
            raise KeyError(identity)
        return json.loads(row["payload"])

    def list(self):
        with self.trials.connect() as db:
            rows = db.execute(
                "SELECT r.payload FROM named_baselines b JOIN baseline_revisions r ON r.id=b.head_revision_id ORDER BY b.created_at DESC,b.id"
            ).fetchall()
        fields = {
            "id",
            "revision_id",
            "revision",
            "name",
            "pinned",
            "campaign_id",
            "strategy_id",
            "contract_id",
            "dataset_revision_id",
            "assessment_set_hash",
            "created_at",
        }
        summaries = [
            {key: value for key, value in json.loads(row["payload"]).items() if key in fields}
            for row in rows
        ]
        return sorted(summaries, key=lambda row: not row["pinned"])

    def revisions(self, identity):
        with self.trials.connect() as db:
            rows = db.execute(
                "SELECT payload FROM baseline_revisions WHERE baseline_id=? ORDER BY revision DESC",
                (identity,),
            ).fetchall()
        if not rows:
            raise KeyError(identity)
        return [json.loads(row["payload"]) for row in rows]
