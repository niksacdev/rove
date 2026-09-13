"""Iteration history follows recorded lineage and preserves assessment boundaries."""

import copy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rove.api.trajectory import create_trajectory_router
from rove.benchmarks.baselines import BaselineInput, BaselineStore
from rove.benchmarks.models import CampaignSpec
from rove.benchmarks.runner import prepare
from rove.benchmarks.store import CampaignStore
from rove.benchmarks.trajectory import campaign_timeline
from rove.models.config import RoveConfig
from rove.trials.snapshots import content_hash, sanitize


@pytest.fixture
def h(tmp_path, monkeypatch):
    monkeypatch.setattr("rove.benchmarks.runner.runtime_fingerprint", lambda: {"test": "fixed"})
    config = RoveConfig.model_validate(
        {
            "endpoints": {
                "mock": {
                    "type": "vlm",
                    "adapter": "mock_vlm",
                    "capabilities": ["task_planning", "verification"],
                },
            },
            "strategies": {"base": {"plan": "mock", "verify": "mock"}},
        }
    )
    spec = CampaignSpec(
        name="Same name",
        suite_version="fixed",
        revision="commit-a",
        strategies=["base"],
        tasks=[{"id": "one", "task": "Pick the block", "image_asset": "a" * 64}],
        seeds=[0, 1],
        ks=[1, 2],
    )
    payload = prepare(spec, config)
    store = CampaignStore(tmp_path)

    def save(value, outcomes=("pass", "unknown")):
        identity = store.create(value)
        for task in value["spec"]["tasks"]:
            for seed in value["spec"]["seeds"]:
                store.save_trial(
                    identity,
                    {
                        "task_id": task["id"],
                        "strategy_id": "base",
                        "seed": seed,
                        "outcome": outcomes[seed],
                        "execution": "completed",
                        "latency_ms": 10 + seed,
                    },
                )
        store.status(identity, "completed")
        return identity

    first = save(payload)
    return tmp_path, store, payload, first, save


def child_payload(h):
    _, store, payload, first, _ = h
    child = copy.deepcopy(payload)
    child["spec"]["revision"] = "commit-b"
    child["source"] = {
        "type": "campaign",
        "id": first,
        "snapshot_sha256": content_hash(sanitize(store.get(first))),
        "historical_trials_counted": False,
    }
    return child


@pytest.mark.parametrize("change", ["cases", "success_metrics", "strategies"])
def test_timeline_classifies_changed_conditions_and_never_pools_unrelated_names(h, change):
    root, _, payload, first, save = h
    child = child_payload(h)
    if change == "cases":
        child["spec"]["tasks"][0]["task"] = "Pick another object"
    elif change == "success_metrics":
        child["contract_id"] = "changed"
        child["contract"] = {"name": "New rubric", "criteria": [], "metrics": []}
    else:
        child["config"]["strategies"]["base"]["plan"] = None
    second = save(child)
    unrelated = save(payload)
    data = campaign_timeline(root, second)
    assert {node["id"] for node in data["nodes"]} == {first, second}
    assert unrelated not in str(data)
    edge = data["edges"][0]
    assert change in edge["changes"]
    if change != "strategies":
        assert edge["comparable"] is False
        assert edge["comparisons"][0]["paired_counts"] is None
    assert data["nodes"][0]["outcomes"][0]["unknown"] == 1
    assert data["nodes"][0]["outcomes"][0]["latency_p95_ms"] == 11


def test_baseline_edge_uses_frozen_assessments_while_node_uses_current_assessments(h, monkeypatch):
    root, _store, _, first, save = h
    saved = BaselineStore(root).save(
        BaselineInput(
            name="Reference", campaign_id=first, strategy_id="base", operation_id="reference"
        )
    )
    child = child_payload(h)
    child["baseline"] = {
        "campaign_id": first,
        "strategy_id": "base",
        "revision_id": saved["revision_id"],
    }
    second = save(child, outcomes=("pass", "pass"))
    from rove.benchmarks import workflow

    original = workflow.assessed_trials

    def updated(root, campaign, trials):
        rows, assessments = original(root, campaign, trials)
        if campaign["id"] == first:
            rows = [{**row, "outcome": "pass"} for row in rows]
        return rows, assessments

    monkeypatch.setattr(workflow, "assessed_trials", updated)
    data = campaign_timeline(root, second)
    first_node = next(node for node in data["nodes"] if node["id"] == first)
    assert first_node["outcomes"][0]["passed"] == 2
    edge = data["edges"][0]
    assert edge["baseline_revision_id"] == saved["revision_id"]
    assert edge["comparisons"][0]["baseline_outcomes"][0]["unknown"] == 1
    assert edge["comparisons"][0]["paired_counts"]["unknown"] == 1
    assert edge["comparisons"][0]["paired_coverage"] == 0.5


def test_invalid_source_does_not_join_history_and_api_is_read_only(h):
    root, store, _, first, save = h
    child = child_payload(h)
    child["source"]["snapshot_sha256"] = "0" * 64
    second = save(child)
    before = store.list()
    app = FastAPI()
    app.include_router(create_trajectory_router(root))
    response = TestClient(app).get(f"/api/campaigns/{second}/timeline")
    assert response.status_code == 200
    data = response.json()
    assert len(data["nodes"]) == 1
    assert data["nodes"][0]["warnings"]
    assert data["edges"] == []
    assert first not in str(data)
    assert store.list() == before
    assert TestClient(app).get("/api/campaigns/missing/timeline").status_code == 404


@pytest.mark.parametrize(
    ("field", "category"),
    [
        ("dataset_revision_id", "cases"),
        ("contract_id", "success_metrics"),
        ("metric_scope", "success_metrics"),
        ("local_evaluators", "success_metrics"),
        ("suite_version", "cases"),
    ],
)
def test_identity_changes_never_look_like_same_configuration(h, field, category):
    root, _, _, _, save = h
    child = child_payload(h)
    if field == "suite_version":
        child["spec"][field] = "changed"
    else:
        child[field] = {"version": "new"} if field == "local_evaluators" else "changed"
    data = campaign_timeline(root, save(child))
    assert category in data["edges"][0]["changes"]


def test_strategy_identity_includes_resolved_model_settings_and_mock_scope(h):
    root, _, _, first, save = h
    child = child_payload(h)
    child["config"]["endpoints"]["mock"]["config"] = {"model": "new-revision"}
    second = save(child)
    data = campaign_timeline(root, second)
    nodes = {node["id"]: node for node in data["nodes"]}
    assert (
        nodes[first]["strategy_versions"][0]["fingerprint"]
        != nodes[second]["strategy_versions"][0]["fingerprint"]
    )
    assert nodes[first]["evidence_kind"] == "synthetic_or_mixed"
    assert data["as_of"] and nodes[first]["assessment_scope"] == "current_assessments"
