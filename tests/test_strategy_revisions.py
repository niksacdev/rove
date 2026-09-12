"""Strategy variants remain immutable and share the real campaign execution path."""

from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rove.api.strategy_revisions import create_strategy_revision_router
from rove.benchmarks.models import CampaignSpec
from rove.benchmarks.report import report_data
from rove.benchmarks.runner import prepare, run_campaign
from rove.benchmarks.store import CampaignStore
from rove.models.config import get_strategies, load_config, reset_config_cache
from rove.strategies.revisions import (
    RevisionConflict,
    RevisionDraft,
    RevisionSave,
    catalog_path,
    list_revisions,
    parent,
    preview,
    records,
    save,
)


@pytest.fixture
def config_file(tmp_path):
    endpoint = {
        "type": "vlm",
        "adapter": "mock_vlm",
        "capabilities": ["scene_analysis", "task_planning", "verification"],
        "config": {"mock_latency_ms": [0, 0]},
    }
    raw = {
        "endpoints": {
            "vision-a": endpoint,
            "vision-b": {**endpoint, "config": {"mock_quality": 0.8, "mock_latency_ms": [0, 0]}},
            "sim": {"type": "sim", "adapter": "mock_sim", "capabilities": []},
        },
        "strategies": {
            "base": {
                "display_name": "Original pipeline",
                "perceive": "vision-a",
                "plan": "vision-a",
                "verify": "vision-a",
            }
        },
    }
    path = tmp_path / "rove.yaml"
    path.write_text("# Preserve my config comments\n" + yaml.safe_dump(raw))
    load_config(path)
    return path


def draft_for(path, identity="base"):
    config = load_config(path)
    source = parent(config, identity)
    definition = {
        **source["definition"],
        "display_name": "Different planner",
        "plan": {"endpoint": "vision-b", "timeout_ms": 2000},
    }
    return RevisionDraft(
        parent_id=identity, expected_parent_fingerprint=source["fingerprint"], definition=definition
    )


def save_request(path, operation="create-variant"):
    draft = draft_for(path)
    planned = preview(load_config(path), draft)
    return RevisionSave(
        **draft.model_dump(), expected_preview_hash=planned["preview_hash"], operation_id=operation
    )


def test_preview_and_save_api_preserve_source_and_expose_shared_strategy(config_file):
    original = config_file.read_bytes()
    app = FastAPI()
    app.include_router(create_strategy_revision_router())
    client = TestClient(app)
    source = client.get("/api/strategy-revisions/parents/base").json()
    assert source["definition"]["plan"] == "vision-a"
    assert "plan" in next(e for e in source["endpoints"] if e["id"] == "vision-b")["stages"]
    assert client.get("/api/strategy-revisions").json() == {"revisions": []}
    draft = draft_for(config_file).model_dump()
    planned = client.post("/api/strategy-revisions/preview", json=draft)
    assert planned.status_code == 200
    planned = planned.json()
    assert any(row["path"] == "plan" for row in planned["differences"])
    assert not catalog_path(config_file).exists()
    payload = {
        **draft,
        "expected_preview_hash": planned["preview_hash"],
        "operation_id": "first-save",
    }
    saved = client.post("/api/strategy-revisions", json=payload)
    assert saved.status_code == 201, saved.text
    result = saved.json()
    identity = result["strategy_id"]
    assert client.post("/api/strategy-revisions", json=payload).json() == result
    assert get_strategies()[identity].plan == "vision-b"
    assert load_config().strategies["base"].plan == "vision-a"
    assert config_file.read_bytes() == original
    assert not (config_file.parent / ".rove" / "benchmarks").exists()
    rows = client.get("/api/strategy-revisions").json()["revisions"]
    assert len(rows) == 1 and rows[0]["available"] and not rows[0]["configuration_changed"]
    assert str(config_file.parent) not in json.dumps(rows)
    duplicate = {**payload, "operation_id": "second-save"}
    assert client.post("/api/strategy-revisions", json=duplicate).status_code == 409
    assert (
        client.post(
            "/api/strategy-revisions", json={**payload, "config_path": "/other.yaml"}
        ).status_code
        == 422
    )
    assert client.get("/api/strategy-revisions/parents/missing").status_code == 404


def test_parent_and_candidate_endpoint_drift_require_new_preview(config_file):
    request = save_request(config_file)
    raw = yaml.safe_load(config_file.read_text())
    raw["endpoints"]["vision-b"]["config"]["mock_quality"] = 0.2
    config_file.write_text(yaml.safe_dump(raw))
    with pytest.raises(RevisionConflict, match="since preview"):
        save(config_file, request)
    request = save_request(config_file)
    raw["strategies"]["base"]["description"] = "Updated source"
    config_file.write_text(yaml.safe_dump(raw))
    with pytest.raises(RevisionConflict, match="Parent strategy"):
        save(config_file, request)
    assert records(config_file) == []


@pytest.mark.parametrize(
    "change",
    [
        {"plan": "missing"},
        {"plan": "sim"},
        {"sim": "vision-a"},
        {"unexpected_endpoint_definition": {}},
        {"verify": {"endpoint": "vision-a", "unknown": True}},
    ],
)
def test_unknown_fields_and_incompatible_endpoint_bindings_are_rejected(config_file, change):
    draft = draft_for(config_file)
    draft.definition.update(change)
    with pytest.raises(ValueError):
        preview(load_config(), draft)
    assert not catalog_path(config_file).exists()


def test_catalog_partitioning_relocation_and_endpoint_availability(config_file, tmp_path):
    result = save(config_file, save_request(config_file))
    other = tmp_path / "other.yaml"
    other.write_bytes(config_file.read_bytes())
    assert result["strategy_id"] not in load_config(other).strategies
    relocated = tmp_path / "moved" / "rove.yaml"
    relocated.parent.mkdir()
    relocated.write_bytes(config_file.read_bytes())
    destination = catalog_path(relocated)
    destination.parent.mkdir()
    destination.write_bytes(catalog_path(config_file).read_bytes())
    assert result["strategy_id"] in load_config(relocated).strategies
    raw = yaml.safe_load(config_file.read_text())
    del raw["endpoints"]["vision-b"]
    config_file.write_text(yaml.safe_dump(raw))
    config = load_config(config_file)
    assert "base" in config.strategies and result["strategy_id"] not in config.strategies
    assert list_revisions(config_file, config)[0]["available"] is False
    assert len(records(config_file)) == 1


def test_concurrent_idempotent_saves_and_operation_identity(config_file):
    request = save_request(config_file)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: save(config_file, request), range(2)))
    assert outcomes[0] == outcomes[1]
    assert len(records(config_file)) == 1
    changed = request.model_copy(deep=True)
    changed.definition["description"] = "Different request"
    with pytest.raises(RevisionConflict, match="Operation ID"):
        save(config_file, changed)


@pytest.mark.parametrize("pipeline_mode", ["sequential", "parallel"])
async def test_multistrategy_campaign_uses_frozen_revision_after_catalog_deleted(
    config_file, tmp_path, mock_image_base64, pipeline_mode
):
    request = save_request(config_file)
    request.definition["pipeline_mode"] = pipeline_mode
    request.expected_preview_hash = preview(load_config(), request)["preview_hash"]
    record = save(config_file, request)
    identity = record["strategy_id"]
    spec = CampaignSpec(
        name="Compare planner revisions",
        suite_version="v1",
        revision="test",
        strategies=["base", identity],
        tasks=[{"id": "pick", "task": "Pick the red block", "image_base64": mock_image_base64}],
        seeds=[1, 2],
        ks=[1, 2],
        timeout_s=10,
    )
    store = CampaignStore(tmp_path / "campaigns")
    campaign_id = store.create(prepare(spec, load_config()))
    original = copy.deepcopy(store.get(campaign_id)["config"])
    catalog_path(config_file).unlink()
    reset_config_cache()
    assert identity not in load_config(config_file).strategies
    await run_campaign(store, campaign_id)
    campaign = store.get(campaign_id)
    trials = store.trials(campaign_id)
    assert campaign["status"] == "completed"
    assert len(trials) == 4 and {row["strategy_id"] for row in trials} == {"base", identity}
    assert all(row["execution"] == "completed" for row in trials), trials
    assert campaign["config"] == original
    assert campaign["config"]["strategies"][identity]["plan"]["endpoint"] == "vision-b"
    assert campaign["config"]["strategies"]["base"]["plan"] == "vision-a"
    assert campaign["config"]["strategies"][identity]["pipeline_mode"] == pipeline_mode
    report = report_data(campaign, trials)
    assert {row["strategy_id"] for row in report["summary"]["strategies"]} == {"base", identity}
    await run_campaign(store, campaign_id)
    assert len(store.trials(campaign_id)) == 4
