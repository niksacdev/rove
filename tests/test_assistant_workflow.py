"""Confirmed local workflow journeys use real services and a fake assistant runtime."""

import copy
import json

import pytest

from rove.benchmarks.baselines import BaselineInput, BaselineStore
from rove.benchmarks.store import CampaignStore
from rove.datasets.service import ConflictError
from tests.test_assistant import confirmation
from tests.test_assistant import harness as harness_fixture

harness = harness_fixture


def prepare(h, name, args, context=None):
    h["runtime"].calls = [(name, args)]
    response = h["client"].post(
        "/api/assistant/ask", json={"message": "Prepare this change", "context": context or {}}
    )
    assert response.status_code == 200, response.text
    return response.json()


def confirm(h, proposal):
    result = h["client"].post("/api/assistant/confirm", json=confirmation(proposal))
    assert result.status_code == 200, result.text
    repeated = h["client"].post("/api/assistant/confirm", json=confirmation(proposal))
    assert repeated.json() == result.json()
    assert proposal["confirmation_token"] not in json.dumps(h["runtime"].results)
    return result.json()


def imported(h):
    case = h["service"].get_case_revision(h["draft"]["case_revision_ids"][0])
    digest = case["image_asset"]["sha256"]
    response = prepare(
        h,
        "preview_import_case",
        {
            "metadata": {"name": "Customer pick", "task": "Pick the red cube"},
            "image_sha256": digest,
        },
        {"asset_sha256s": [digest]},
    )
    assert len(h["service"].list_cases()) == 1
    result = confirm(h, response["proposals"][0])
    assert len(h["service"].list_cases()) == 2
    return result


def launch(h, case_id, contract_id):
    result = prepare(
        h,
        "preview_campaign",
        {**h["draft"], "case_revision_ids": [case_id], "contract_id": contract_id},
    )
    return confirm(h, result["proposals"][0])


def test_import_to_freeze_baseline_ablation_comparison_journey(harness):
    h = harness
    case = imported(h)
    contract_body = {
        "name": "Customer output",
        "scope": "plan_quality",
        "evidence_mode": "candidate_output",
        "criteria": [{"id": "clear", "description": "Clear safe plan"}],
    }
    response = prepare(
        h,
        "preview_contract",
        {"contract": contract_body, "parent_contract_id": h["draft"]["contract_id"]},
    )
    assert len(h["service"].list_contracts()) == 1
    contract = confirm(h, response["proposals"][0])
    assert contract["parent_contract_id"] == h["draft"]["contract_id"]
    # Only the actual human review service writes these judgments; the assistant has no such tool.
    review = h["service"].create_review(
        {
            "target_type": "case_validity",
            "case_revision_id": case["id"],
            "contract_id": contract["id"],
            "reviewer": "fixture SME",
            "status": "final",
            "decision": "accepted",
            "rationale": "Task and observation are clear",
        }
    )
    freeze = {
        "dataset": {
            "name": "Reviewed set",
            "contract_id": contract["id"],
            "members": [{"case_revision_id": case["id"], "review_ids": [review["id"]]}],
        }
    }
    proposal = prepare(h, "preview_freeze", freeze)["proposals"][0]
    assert h["service"].list_datasets() == []
    dataset = confirm(h, proposal)
    assert dataset["members"][0]["review_ids"] == [review["id"]]
    baseline_campaign = launch(h, case["id"], contract["id"])
    store = CampaignStore(h["root"])
    store.status(baseline_campaign["id"], "completed")
    proposal = prepare(
        h,
        "preview_baseline",
        {
            "name": "Release baseline",
            "campaign_id": baseline_campaign["id"],
            "strategy_id": "baseline",
        },
    )["proposals"][0]
    assert BaselineStore(h["root"]).list() == []
    baseline = confirm(h, proposal)
    assert baseline["summary"]["tasks"][0]["unknown"] == 1
    h["config"].strategies["candidate"] = copy.deepcopy(h["config"].strategies["baseline"])
    draft = {
        "baseline_campaign_id": baseline_campaign["id"],
        "baseline_strategy_id": "baseline",
        "baseline_revision_id": baseline["revision_id"],
        "candidate_strategy_id": "candidate",
        "name": "Candidate",
        "intended_change": "Same pipeline control",
    }
    proposal = prepare(h, "preview_ablation", draft)["proposals"][0]
    assert len(store.list()) == 1
    candidate = confirm(h, proposal)
    assert store.get(candidate["id"])["baseline"]["revision_id"] == baseline["revision_id"]
    comparison = prepare(h, "inspect_comparison", {"id": candidate["id"]})
    assert comparison["proposals"] == []
    assert h["runtime"].results[-1]["comparable"]
    assert len(h["launched"]) == 2
    assert len(h["service"].list_reviews(case_revision_id=case["id"])) == 1


def test_import_cannot_use_unattached_asset_or_invent_labels(harness):
    h = harness
    digest = h["service"].get_case_revision(h["draft"]["case_revision_ids"][0])["image_asset"][
        "sha256"
    ]
    args = {"metadata": {"name": "Pick", "task": "Pick"}, "image_sha256": digest}
    assert prepare(h, "preview_import_case", args)["proposals"] == []
    for private in ("reference_data", "recorded_evidence"):
        args["metadata"][private] = {"invented": True}
        assert (
            prepare(h, "preview_import_case", args, {"asset_sha256s": [digest]})["proposals"] == []
        )
        args["metadata"].pop(private)
    assert len(h["service"].list_cases()) == 1


def revision_proposal(h):
    original = h["service"].get_case_revision(h["draft"]["case_revision_ids"][0])
    args = {
        "case_id": original["case_id"],
        "expected_head_revision_id": original["id"],
        "image_sha256": original["image_asset"]["sha256"],
        "metadata": {"name": "Clearer case", "task": "Pick the red cube carefully"},
    }
    return original, args, prepare(h, "preview_revise_case", args)["proposals"][0]


def test_case_revision_confirmation_detects_stale_head(harness):
    original, _args, proposal = revision_proposal(harness)
    harness["service"].revise_case(
        original["case_id"],
        {"name": "Other edit", "task": "Different task"},
        expected_head_revision_id=original["id"],
    )
    response = harness["client"].post("/api/assistant/confirm", json=confirmation(proposal))
    assert response.status_code == 409
    assert harness["service"].get_case(original["case_id"])["task"] == "Different task"


def test_confirmation_retry_after_committed_write_returns_same_revision(harness, monkeypatch):
    import rove.api.assistant as api

    original, _, proposal = revision_proposal(harness)
    real = api.execute_operation
    calls = []

    def fail_after_commit(*args, **kwargs):
        result = real(*args, **kwargs)
        calls.append(result["id"])
        if len(calls) == 1:
            raise RuntimeError("Lost response after commit")
        return result

    monkeypatch.setattr(api, "execute_operation", fail_after_commit)
    response = harness["client"].post("/api/assistant/confirm", json=confirmation(proposal))
    assert response.status_code == 503
    result = confirm(harness, proposal)
    assert calls == [result["id"], result["id"]]
    assert harness["service"].get_case(original["case_id"])["revision"] == 2


def test_freeze_requires_existing_final_review_ids(harness):
    case = harness["draft"]["case_revision_ids"][0]
    body = {
        "dataset": {
            "name": "Not reviewed",
            "contract_id": harness["draft"]["contract_id"],
            "members": [{"case_revision_id": case}],
        }
    }
    assert prepare(harness, "preview_freeze", body)["proposals"] == []
    body["dataset"]["members"][0]["review_ids"] = ["fabricated"]
    assert prepare(harness, "preview_freeze", body)["proposals"] == []
    assert not harness["service"].list_datasets()


def test_mutation_operation_ids_reject_changed_requests(harness):
    h = harness
    asset = h["service"].get_case_revision(h["draft"]["case_revision_ids"][0])["image_asset"][
        "sha256"
    ]
    first = h["service"].import_case(
        {"name": "One", "task": "Pick"}, asset, operation_id="stable-import"
    )
    assert (
        h["service"].import_case(
            {"name": "One", "task": "Pick"}, asset, operation_id="stable-import"
        )["id"]
        == first["id"]
    )
    with pytest.raises(ConflictError):
        h["service"].import_case(
            {"name": "Changed", "task": "Pick"}, asset, operation_id="stable-import"
        )


def test_named_baseline_history_cas_name_and_operation_conflicts(harness):
    h = harness
    campaign = launch(h, h["draft"]["case_revision_ids"][0], h["draft"]["contract_id"])
    store = CampaignStore(h["root"])
    request = BaselineInput(
        name="Pinned",
        campaign_id=campaign["id"],
        strategy_id="baseline",
        operation_id="baseline-op",
    )
    baselines = BaselineStore(h["root"])
    with pytest.raises(ValueError, match="completed"):
        baselines.save(request)
    store.status(campaign["id"], "completed")
    first = baselines.save(request)
    assert baselines.save(request) == first
    second = baselines.save(
        request.model_copy(
            update={
                "name": "Renamed",
                "pinned": False,
                "operation_id": "rename",
                "expected_head_revision_id": first["revision_id"],
            }
        ),
        first["id"],
    )
    assert second["revision"] == 2 and not second["pinned"]
    assert baselines.get(first["revision_id"])["name"] == "Pinned"
    assert len(baselines.revisions(first["id"])) == 2
    with pytest.raises(ConflictError, match="changed"):
        baselines.save(
            request.model_copy(
                update={"operation_id": "stale", "expected_head_revision_id": first["revision_id"]}
            ),
            first["id"],
        )
    with pytest.raises(ConflictError, match="name"):
        baselines.save(request.model_copy(update={"name": "renamed", "operation_id": "collision"}))
    with pytest.raises(ConflictError, match="identity"):
        baselines.save(request.model_copy(update={"name": "Wrong"}))


def test_named_baseline_freezes_assessed_outcomes_despite_later_judgments(harness, monkeypatch):
    h = harness
    campaign = launch(h, h["draft"]["case_revision_ids"][0], h["draft"]["contract_id"])
    store = CampaignStore(h["root"])
    store.status(campaign["id"], "completed")
    task_id = store.get(campaign["id"])["spec"]["tasks"][0]["id"]
    projection = [
        {
            "task_id": task_id,
            "strategy_id": "baseline",
            "seed": 1,
            "execution": "completed",
            "outcome": "pass",
            "trial_id": "trial",
            "assessment_ids": ["review-before"],
            "result": {
                "stages": [
                    {
                        "stage": "verify",
                        "status": "completed",
                        "model_id": "mock",
                        "output": {"evaluator_result": {"measurement": "original"}},
                    }
                ]
            },
        }
    ]
    evidence = {
        "scope": "success_contract",
        "assessment_set_hash": "before",
        "review_ids": ["review-before"],
    }
    monkeypatch.setattr(
        "rove.benchmarks.workflow.assessed_trials", lambda *_: (projection, evidence)
    )
    request = BaselineInput(
        name="Stable", campaign_id=campaign["id"], strategy_id="baseline", operation_id="capture"
    )
    baselines = BaselineStore(h["root"])
    saved = baselines.save(request)
    projection[0]["outcome"] = "fail"
    evidence["assessment_set_hash"] = "after"
    restored = baselines.get(saved["revision_id"])
    assert restored["trial_outcomes"][0]["outcome"] == "pass"
    assert (
        restored["trial_outcomes"][0]["result"]["stages"][0]["output"]["evaluator_result"][
            "measurement"
        ]
        == "original"
    )
    assert restored["assessment_set_hash"] == "before"
    assert restored["summary"]["tasks"][0]["passed"] == 1
    assert baselines.capture(request)["assessment_set_hash"] == "after"


def test_named_baseline_api_preserves_versions(harness):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from rove.api.datasets import create_dataset_router

    h = harness
    campaign = launch(h, h["draft"]["case_revision_ids"][0], h["draft"]["contract_id"])
    CampaignStore(h["root"]).status(campaign["id"], "completed")
    app = FastAPI()
    app.include_router(create_dataset_router(h["root"], h["launched"].append))
    client = TestClient(app)
    body = {
        "name": "API baseline",
        "campaign_id": campaign["id"],
        "strategy_id": "baseline",
        "operation_id": "api-create",
    }
    first = client.post("/api/baselines", json=body)
    assert first.status_code == 201
    first = first.json()
    assert client.get("/api/baselines").json()["baselines"][0]["id"] == first["id"]
    update = {
        **body,
        "name": "Current",
        "operation_id": "api-update",
        "expected_head_revision_id": first["revision_id"],
    }
    second = client.patch(f"/api/baselines/{first['id']}", json=update)
    assert second.status_code == 200
    assert client.get(f"/api/baselines/{first['revision_id']}").json()["name"] == "API baseline"
    assert (
        client.patch(
            f"/api/baselines/{first['id']}", json={**update, "operation_id": "stale"}
        ).status_code
        == 409
    )
    assert len(client.get(f"/api/baselines/{first['id']}/revisions").json()["revisions"]) == 2
