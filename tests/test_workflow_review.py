"""Independent adversarial checks for contract credibility and report lineage."""

import copy
import csv
import io
import json
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi import FastAPI

from rove.api.trials import create_trial_router
from rove.benchmarks.api import create_router
from rove.benchmarks.report import report_data, to_csv
from rove.benchmarks.store import CampaignStore
from rove.benchmarks.workflow import assessed_trials, prepare_launch
from rove.datasets.service import DatasetService
from rove.models.config import RoveConfig
from tests import test_customer_workflow as journey

case, config, contract, service = journey.case, journey.config, journey.contract, journey.service
client_for, rating, request = journey.client_for, journey.rating, journey.request


def setup_trial(service, case, payload, *, checks=(), primary_success=True, model_id="judge"):
    result = {
        "stages": [
            {
                "stage": "verify",
                "status": "completed",
                "model_id": model_id,
                "output": {
                    "success": primary_success,
                    "verdict_valid": True,
                    "check_results": list(checks),
                },
            }
        ]
    }
    trial_id = service.trials.begin(
        source="campaign",
        campaign_id=payload.get("id"),
        task={"case_revision_id": case["id"]},
        strategy={"id": "base"},
        config={},
    )
    service.trials.finish(trial_id, status="completed", result=result)
    return {
        "trial_id": trial_id,
        "task_id": case["id"],
        "strategy_id": "base",
        "seed": 1,
        "execution": "completed",
        "outcome": "pass" if primary_success else "fail",
        "result": result,
    }


def with_check(config, *, role="constraint"):
    value = config.model_dump()
    value["endpoints"]["check"] = {
        "type": "verifier",
        "adapter": "local_verifier",
        "capabilities": ["diagnostic_check"],
        "config": {"callable": "rove.evaluators.pick_place:evaluate"},
    }
    # A fixture does not need to import/execute a custom evaluator to test frozen evidence.
    value["endpoints"]["check"]["adapter"] = (
        "forward_kinematics" if role == "diagnostic" else "mock_check"
    )
    value["strategies"]["base"]["verify"] = {
        "endpoint": "judge",
        "checks": [{"endpoint": "check", "role": role, "required": True}],
    }
    return RoveConfig.model_validate(value)


def evidence(verdict="pass", *, role="constraint", quality="observed", execution="completed"):
    return {
        "endpoint": "check",
        "role": role,
        "required": True,
        "execution": execution,
        "result": {
            "verdict": verdict,
            "reasoning": "Measured configured evidence",
            "evidence_quality": quality,
            "evidence_refs": ["episode:1"],
        },
    }


def mixed_contract(service, *, endpoint="judge"):
    return service.create_contract(
        {
            "name": "Mixed",
            "scope": "plan_quality",
            "evidence_mode": "candidate_output",
            "criteria": [
                {"id": "correct", "description": "SME checks task relevance"},
                {
                    "id": "automated",
                    "description": "Configured criterion",
                    "assessment": "configured_verifier",
                    "endpoint": endpoint,
                },
            ],
        }
    )


def test_mixed_review_does_not_require_human_to_claim_automated_result(
    tmp_path, config, case, service
):
    contract = mixed_contract(service)
    payload, _ = prepare_launch(tmp_path, request(case, contract), config)
    trial = setup_trial(service, case, payload)
    review = service.create_review(rating(case, contract, trial["trial_id"]))
    projected, metadata = assessed_trials(tmp_path, payload, [trial])
    assert projected[0]["outcome"] == "pass"
    assert metadata["scope"] == "success_contract"
    assert metadata["review_ids"] == [review["id"]]
    trial["result"]["stages"][0]["model_id"] = "different-judge"
    assert assessed_trials(tmp_path, payload, [trial])[0][0]["outcome"] == "unknown"


def test_criterion_reads_its_bound_constraint_not_unrelated_primary_judgment(
    tmp_path, config, case, service
):
    contract = mixed_contract(service, endpoint="check")
    payload, preview = prepare_launch(tmp_path, request(case, contract), with_check(config))
    assert preview["ready"]
    trial = setup_trial(service, case, payload, checks=[evidence()], primary_success=False)
    service.create_review(rating(case, contract, trial["trial_id"]))
    projected, _ = assessed_trials(tmp_path, payload, [trial])
    assert projected[0]["outcome"] == "pass"
    assert projected[0]["configured_outcome"] == "fail"


@pytest.mark.parametrize(
    "verdict,quality,execution,expected",
    [
        ("unknown", "estimated", "completed", "pass"),
        ("unknown", "unknown", "completed", "unknown"),
        ("unknown", "estimated", "timeout", "unknown"),
        ("fail", "estimated", "completed", "pass"),
    ],
)
def test_required_diagnostic_is_an_evidence_gate_not_task_verdict(
    tmp_path,
    config,
    case,
    contract,
    service,
    verdict,
    quality,
    execution,
    expected,
):
    payload, _ = prepare_launch(
        tmp_path, request(case, contract), with_check(config, role="diagnostic")
    )
    trial = setup_trial(
        service,
        case,
        payload,
        checks=[evidence(verdict, role="diagnostic", quality=quality, execution=execution)],
    )
    service.create_review(rating(case, contract, trial["trial_id"]))
    assert assessed_trials(tmp_path, payload, [trial])[0][0]["outcome"] == expected


def test_diagnostic_cannot_be_bound_as_automatic_success_criterion(tmp_path, config, case, service):
    contract = mixed_contract(service, endpoint="check")
    _, preview = prepare_launch(
        tmp_path, request(case, contract), with_check(config, role="diagnostic")
    )
    assert not preview["ready"]
    assert any("required constraint" in text for text in preview["blockers"])


def test_known_required_constraint_failure_overrides_missing_human_vote(
    tmp_path, config, case, contract, service
):
    payload, _ = prepare_launch(tmp_path, request(case, contract), with_check(config))
    trial = setup_trial(service, case, payload, checks=[evidence("fail")])
    assert assessed_trials(tmp_path, payload, [trial])[0][0]["outcome"] == "fail"
    trial["result"]["stages"][0]["output"]["check_results"] = []
    assert assessed_trials(tmp_path, payload, [trial])[0][0]["outcome"] == "unknown"


def test_automated_only_contract_requires_bound_endpoint_evidence(tmp_path, config, case, service):
    contract = service.create_contract(
        {
            "name": "Automatic",
            "scope": "plan_quality",
            "evidence_mode": "candidate_output",
            "criteria": [
                {
                    "id": "auto",
                    "description": "Task verifier",
                    "assessment": "configured_verifier",
                    "endpoint": "judge",
                }
            ],
        }
    )
    payload, _ = prepare_launch(tmp_path, request(case, contract), config)
    trial = setup_trial(service, case, payload, model_id="not-judge")
    projected, metadata = assessed_trials(tmp_path, payload, [trial])
    assert projected[0]["outcome"] == "unknown"
    assert metadata["scope"] == "success_contract"


@pytest.mark.parametrize(
    "context",
    [
        {"expected_subtasks": ["secret answer"]},
        {"constraints": [{"reference_output": "secret answer"}]},
        {"robot": {"acceptable_interpretations": ["secret answer"]}},
    ],
)
def test_private_labels_cannot_be_imported_as_candidate_observations(service, case, context):
    with pytest.raises(ValueError, match="Private labels"):
        service.import_case(
            {"name": "Leaky", "task": "Task", "candidate_context": context},
            case["image_asset"]["sha256"],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", ["[]", "null", '"text"', "123"])
async def test_nonobject_revision_metadata_is_validation_error(tmp_path, case, metadata):
    async with await client_for(tmp_path) as client:
        response = await client.post(
            f"/api/cases/{case['case_id']}/revisions", data={"metadata": metadata}
        )
    assert response.status_code == 422


def test_default_mock_vla_is_launchable_without_redundant_capability(tmp_path, case, contract):
    root = Path(__file__).resolve().parents[1]
    config = RoveConfig.model_validate(yaml.safe_load((root / "rove.yaml").read_text()))
    req = request(case, contract).model_copy(update={"strategies": ["mock"]})
    _, preview = prepare_launch(tmp_path, req, config)
    assert preview["ready"], preview["blockers"]


def test_trends_reject_different_contracts_and_csv_retains_assessment_lineage(
    tmp_path, config, case, contract, service
):
    payload, _ = prepare_launch(tmp_path, request(case, contract), config)
    campaign = {**payload, "id": "current", "created_at": "2026-09-12T00:00:00+00:00"}
    previous = copy.deepcopy(campaign)
    previous["id"] = "previous"
    previous["contract_id"] = "different"
    trial = setup_trial(service, case, payload)
    trial.update(configured_outcome="fail", assessment_ids=["review-123"])
    data = report_data(campaign, [trial], [(previous, [trial])])
    assert data["trends"] == []
    assert data["excluded_history_series"] == 1
    data["assessments"] = {"scope": "success_contract", "assessment_set_hash": "set-123"}
    row = next(csv.DictReader(io.StringIO(to_csv(data))))
    assert row["trial_id"] == trial["trial_id"]
    assert row["configured_outcome"] == "fail"
    assert json.loads(row["assessment_ids"]) == ["review-123"]
    assert row["contract_id"] == contract["id"]
    assert row["metric_scope"] == "success_contract"
    assert row["assessment_set_hash"] == "set-123"


def test_active_dissent_is_not_hidden_after_one_review_page(
    tmp_path, config, case, contract, service, monkeypatch
):
    payload, _ = prepare_launch(tmp_path, request(case, contract), config)
    trial = setup_trial(service, case, payload)
    accepted = {**rating(case, contract, trial["trial_id"]), "id": "accepted"}
    dissent = {
        **accepted,
        "id": "old-dissent",
        "decision": "rejected",
        "criteria": {"correct": "rejected"},
    }
    rows = [{**accepted, "id": f"accepted-{i}"} for i in range(1000)] + [dissent]
    monkeypatch.setattr(
        DatasetService,
        "list_reviews",
        lambda self, *, trial_id, limit, offset: rows[offset : offset + limit],
    )
    projected, metadata = assessed_trials(tmp_path, payload, [trial])
    assert projected[0]["outcome"] == "unknown"
    assert len(metadata["review_ids"]) == 1001


def test_contract_requires_a_required_acceptance_criterion(service):
    with pytest.raises(ValueError, match="at least one required"):
        service.create_contract(
            {
                "name": "No success condition",
                "scope": "plan_quality",
                "evidence_mode": "candidate_output",
                "criteria": [{"id": "optional", "description": "Optional note", "required": False}],
            }
        )


@pytest.mark.asyncio
async def test_report_history_and_assessment_endpoint_share_same_contract_outcome(
    tmp_path,
    config,
    case,
    service,
):
    contract = mixed_contract(service)
    payload, _ = prepare_launch(tmp_path, request(case, contract), config)
    store = CampaignStore(tmp_path)
    identity = store.create(payload)
    trial = setup_trial(service, case, store.get(identity), model_id="wrong-endpoint")
    service.create_review(rating(case, contract, trial["trial_id"]))
    store.save_trial(identity, trial)
    app = FastAPI()
    app.include_router(create_router(tmp_path))
    app.include_router(create_trial_router(lambda: service.trials))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assessment = (await client.get(f"/api/campaigns/{identity}/assessments")).json()
        report = (await client.get(f"/api/campaigns/{identity}/report?format=json")).json()
        history = (await client.get(f"/api/trials/{trial['trial_id']}")).json()
        csv_response = await client.get(f"/api/campaigns/{identity}/report?format=csv")
    assert assessment["trials"][0]["outcome"] == "unknown"
    assert report["trials"][0]["outcome"] == "unknown"
    assert history["assessment"]["outcome"] == "unknown"
    assert report["summary"] == assessment["summary"]
    row = next(csv.DictReader(io.StringIO(csv_response.text)))
    assert row["outcome"] == "unknown" and row["configured_outcome"] == "pass"
    assert json.loads(row["assessment_ids"]) == history["assessment"]["review_ids"]
