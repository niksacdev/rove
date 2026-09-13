"""Append-only strategy definitions; YAML and shared endpoint definitions stay intact."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from rove.trials.snapshots import canonical_json, content_hash, sanitize
from rove.trials.store import now


class RevisionConflict(ValueError):
    """The source or preview changed before an explicit save."""


class RevisionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parent_id: str = Field(min_length=1, max_length=160)
    expected_parent_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    definition: dict


class RevisionSave(RevisionDraft):
    expected_preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    operation_id: str = Field(min_length=1, max_length=128)


def catalog_path(config_path: Path) -> Path:
    # Filename partitioning keeps sibling configurations separate and relocation portable.
    partition = content_hash(config_path.name)[:16]
    return config_path.parent / ".rove" / f"strategy-revisions-{partition}.sqlite3"


def records(config_path: Path) -> list[dict]:
    path = catalog_path(config_path)
    if not path.exists():
        return []
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)) as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_revisions'"
        ).fetchone():
            return []
        return [
            json.loads(row[0])
            for row in db.execute(
                "SELECT payload FROM strategy_revisions ORDER BY created_at,strategy_id"
            )
        ]


def stages(endpoint) -> list[str]:
    capabilities = {
        "perceive": "scene_analysis",
        "plan": "task_planning",
        "act": "action_execution",
        "verify": "verification",
    }
    result = []
    for stage, capability in capabilities.items():
        eligible = (
            endpoint.type == "agent"
            or (stage == "act" and endpoint.type == "vla")
            or (stage != "act" and endpoint.type in {"vlm", "llm"})
            or (
                stage == "verify"
                and endpoint.type == "verifier"
                and endpoint.adapter != "forward_kinematics"
            )
        )
        if eligible and (
            capability in endpoint.capabilities or (stage == "act" and endpoint.type == "vla")
        ):
            result.append(stage)
    if endpoint.type == "sim":
        result.append("sim")
    return result


def validate_definition(definition: dict, config):
    from rove.models.config import RoveConfig, StrategyConfig

    if set(definition) - StrategyConfig.model_fields.keys():
        raise ValueError("Only existing strategy configuration fields can be changed")
    if len(canonical_json(definition).encode()) > 65536:
        raise ValueError("Strategy definition exceeds 64 KiB")
    strategy = StrategyConfig.model_validate(definition)
    RoveConfig.model_validate(
        {**config.model_dump(), "strategies": {"candidate": strategy.model_dump()}}
    )
    for stage in ("perceive", "plan", "act", "verify"):
        option = strategy.stage_options(stage)
        if option and stage not in stages(config.endpoints[option.endpoint]):
            raise ValueError(f"Endpoint {option.endpoint} cannot serve the {stage} stage")
    if strategy.sim and "sim" not in stages(config.endpoints[strategy.sim]):
        raise ValueError("Simulation requires an existing simulation endpoint")
    return strategy


def fingerprint(strategy, config) -> str:
    return content_hash(
        sanitize(
            {
                "definition": strategy.model_dump(mode="json"),
                "endpoints": {
                    key: config.endpoints[key].model_dump(mode="json")
                    for key in sorted(strategy.endpoint_refs())
                },
                "defaults": config.defaults.model_dump(mode="json"),
            }
        )
    )


def parent(config, identity: str) -> dict:
    if identity not in config.strategies:
        raise KeyError(identity)
    strategy = config.strategies[identity]
    return {
        "strategy_id": identity,
        "fingerprint": fingerprint(strategy, config),
        "definition": strategy.model_dump(mode="json"),
        "endpoints": [
            {
                "id": key,
                "display_name": value.display_name or key,
                "type": value.type,
                "adapter": value.adapter,
                "capabilities": value.capabilities,
                "stages": stages(value),
            }
            for key, value in config.endpoints.items()
        ],
    }


def differences(before, after, prefix=""):
    if isinstance(before, dict) and isinstance(after, dict):
        return [
            item
            for key in sorted(before.keys() | after.keys())
            for item in differences(
                before.get(key), after.get(key), f"{prefix}.{key}" if prefix else key
            )
        ]
    return [] if before == after else [{"path": prefix, "before": before, "after": after}]


def preview(config, draft: RevisionDraft) -> dict:
    source = parent(config, draft.parent_id)
    if source["fingerprint"] != draft.expected_parent_fingerprint:
        raise RevisionConflict(
            "Parent strategy or its endpoint configuration changed; reload it before previewing"
        )
    definition = validate_definition(draft.definition, config).model_dump(mode="json")
    delta = differences(source["definition"], definition)
    if not delta:
        raise ValueError("Change at least one strategy field before creating a revision")
    strategy = validate_definition(definition, config)
    payload = {
        "parent_id": draft.parent_id,
        "parent_fingerprint": source["fingerprint"],
        "definition": definition,
        "endpoint_fingerprints": {
            key: content_hash(sanitize(config.endpoints[key].model_dump(mode="json")))
            for key in sorted(strategy.endpoint_refs())
        },
        "runtime_fingerprint": fingerprint(strategy, config),
    }
    digest = content_hash(payload)
    identity = "revision_" + digest[:24]
    if identity in config.strategies:
        raise RevisionConflict(
            "This strategy revision already exists; select the existing revision"
        )
    return {**payload, "strategy_id": identity, "preview_hash": digest, "differences": delta}


def overlay(config, config_path: Path):
    from rove.models.config import RoveConfig

    strategies = dict(config.strategies)
    for record in records(config_path):
        identity = record["strategy_id"]
        if identity in strategies:
            raise RevisionConflict(f"Strategy revision ID conflicts with configuration: {identity}")
        try:
            restored = validate_definition(record["definition"], config)
            if (
                record.get("kind") == "historical_snapshot"
                and fingerprint(restored, config) != record["runtime_fingerprint"]
            ):
                continue
            strategies[identity] = restored
        except ValueError:
            # Keep the source configuration usable when a referenced endpoint is removed.
            continue
    return RoveConfig.model_validate({**config.model_dump(), "strategies": strategies})


def list_revisions(config_path: Path, config) -> list[dict]:
    result = []
    for record in records(config_path):
        try:
            strategy = validate_definition(record["definition"], config)
            current = fingerprint(strategy, config)
            if (
                record.get("kind") == "historical_snapshot"
                and current != record["runtime_fingerprint"]
            ):
                raise ValueError(
                    "Historical endpoint/default configuration changed; source revision is unavailable"
                )
            result.append(
                {
                    **record,
                    "available": True,
                    "configuration_changed": current != record["runtime_fingerprint"],
                    "issues": [],
                }
            )
        except ValueError as error:
            result.append(
                {
                    **record,
                    "available": False,
                    "configuration_changed": True,
                    "issues": [str(error)],
                }
            )
    return result


def save(config_path: Path, request: RevisionSave) -> dict:
    from rove.models.config import load_config, reset_config_cache

    request_hash = content_hash(request.model_dump(mode="json"))
    path = catalog_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=10)) as db, db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS strategy_revisions (strategy_id TEXT PRIMARY KEY,operation_id TEXT NOT NULL UNIQUE,request_hash TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL)"
        )
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute(
            "SELECT request_hash,payload FROM strategy_revisions WHERE operation_id=?",
            (request.operation_id,),
        ).fetchone()
        if previous:
            if previous[0] != request_hash:
                raise RevisionConflict(
                    "Operation ID already belongs to a different strategy change"
                )
            return json.loads(previous[1])
        reset_config_cache()
        config = load_config(config_path)
        planned = preview(config, request)
        if planned["preview_hash"] != request.expected_preview_hash:
            raise RevisionConflict("Candidate configuration changed since preview; preview again")
        result = {**planned, "created_at": now()}
        try:
            db.execute(
                "INSERT INTO strategy_revisions VALUES (?,?,?,?,?)",
                (
                    result["strategy_id"],
                    request.operation_id,
                    request_hash,
                    canonical_json(result),
                    result["created_at"],
                ),
            )
        except sqlite3.IntegrityError as error:
            raise RevisionConflict("This immutable strategy revision already exists") from error
    reset_config_cache()
    load_config(config_path)
    return result


def restore_snapshot(config_path: Path, *, source: dict, definition: dict, frozen: dict) -> dict:
    """Save a selectable historical definition only against unchanged runtime inputs.

    This explicit user action restores configuration, not an execution. Endpoint
    credentials remain in the live configuration and are excluded from identity.
    """
    from rove.models.config import load_config, reset_config_cache

    config = load_config(config_path)
    strategy = validate_definition(definition, config)
    historical = {
        "definition": strategy.model_dump(mode="json"),
        "endpoints": {
            key: frozen.get("endpoints", {}).get(key) for key in strategy.endpoint_refs()
        },
        "defaults": frozen.get("defaults"),
    }
    expected = content_hash(sanitize(historical))
    current = fingerprint(strategy, config)
    if expected != current:
        raise RevisionConflict(
            "Historical endpoint or default configuration changed; restore those components or choose an explicit new strategy"
        )
    identity = "snapshot_" + expected[:24]
    result = {
        "strategy_id": identity,
        "parent_id": source["id"],
        "parent_fingerprint": expected,
        "definition": strategy.model_dump(mode="json"),
        "endpoint_fingerprints": {
            key: content_hash(sanitize(frozen["endpoints"][key]))
            for key in strategy.endpoint_refs()
        },
        "runtime_fingerprint": expected,
        "preview_hash": expected,
        "differences": [],
        "kind": "historical_snapshot",
        "source": source,
    }
    path = catalog_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=10)) as db, db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS strategy_revisions (strategy_id TEXT PRIMARY KEY,operation_id TEXT NOT NULL UNIQUE,request_hash TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL)"
        )
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute(
            "SELECT payload FROM strategy_revisions WHERE strategy_id=?", (identity,)
        ).fetchone()
        if previous:
            saved = json.loads(previous[0])
            if (
                saved["runtime_fingerprint"] != expected
                or saved["definition"] != result["definition"]
            ):
                raise RevisionConflict(
                    "Historical strategy identifier conflicts with an existing revision"
                )
            return saved
        if identity in config.strategies:
            raise RevisionConflict("Historical strategy identifier conflicts with configuration")
        result["created_at"] = now()
        db.execute(
            "INSERT INTO strategy_revisions VALUES (?,?,?,?,?)",
            (
                identity,
                "restore-" + expected,
                expected,
                canonical_json(result),
                result["created_at"],
            ),
        )
    reset_config_cache()
    load_config(config_path)
    return result
