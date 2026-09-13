"""Saved results seed new plans, preserving inputs without adding historical attempts."""

import copy
import io

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from rove.api.datasets import create_dataset_router
from rove.benchmarks.runner import run_campaign
from rove.benchmarks.seeds import TrialSeedRequest, campaign_seed, trial_seed
from rove.benchmarks.store import CampaignStore
from rove.benchmarks.workflow import LaunchRequest, prepare_launch
from rove.datasets.service import DatasetService
from rove.models.config import load_config, reset_config_cache
from rove.strategies.revisions import list_revisions
from rove.trials.snapshots import content_hash, sanitize


@pytest.fixture
def h(tmp_path, monkeypatch):
    endpoint = {
        "type": "vlm",
        "adapter": "mock_vlm",
        "capabilities": ["scene_analysis", "task_planning", "verification"],
        "config": {"mock_latency_ms": [0, 0]},
    }
    path = tmp_path / "rove.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "endpoints": {"mock": endpoint},
                "strategies": {"base": {"perceive": "mock", "plan": "mock", "verify": "mock"}},
            }
        )
    )
    config = load_config(path)
    monkeypatch.setattr(
        "rove.benchmarks.runner.runtime_fingerprint", lambda: {"version": "seed-test"}
    )
    service = DatasetService(tmp_path / "history")
    image = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(image, format="PNG")
    asset = service.trials.save_asset(image.getvalue(), "image/png")
    robot = service.trials.save_asset(
        b'<robot name="saved"><link name="base"/></robot>', "application/xml"
    )
    definition = {"perceive": "mock", "plan": "mock", "verify": "mock"}
    frozen = {
        "defaults": config.defaults.model_dump(mode="json"),
        "endpoints": {"mock": config.endpoints["mock"].model_dump(mode="json")},
        "strategies": {"single": definition},
        "robot_asset": robot,
    }
    trial = service.trials.begin(
        source="quick",
        task={
            "task": "Pick red block",
            "image_asset": asset,
            "example": {
                "ground_truth": {"answer": "private"},
                "extras": {"constraints": ["Keep clear of edge"], "expected_subtasks": ["private"]},
            },
        },
        strategy={"id": "single", **definition},
        config=frozen,
    )
    service.trials.finish(trial, status="completed", result={"stages": [], "failure_stage": "plan"})
    app = FastAPI()
    launched = []
    app.include_router(create_dataset_router(service.trials.root, launched.append))
    return {
        "root": service.trials.root,
        "service": service,
        "trial": trial,
        "robot": robot,
        "asset": asset,
        "path": path,
        "client": TestClient(app),
        "launched": launched,
    }


def seed(h, operation="seed-once"):
    return trial_seed(
        h["root"], h["trial"], TrialSeedRequest(operation_id=operation), config_path=h["path"]
    )


def contract(h):
    return h["service"].create_contract(
        {
            "name": "Outputs",
            "scope": "plan_quality",
            "evidence_mode": "candidate_output",
            "criteria": [{"id": "accept", "description": "Check target"}],
        }
    )


def request_from(h, draft):
    return LaunchRequest(
        name="New campaign",
        case_revision_ids=draft["case_revision_ids"],
        strategies=draft["strategies"],
        contract_id=contract(h)["id"],
        seeds=[0, 1],
        ks=[1, 2],
        source=draft["source"],
    )


def test_trial_seed_preserves_managed_inputs_restores_single_and_never_executes(h):
    before = h["service"].trials.get(h["trial"])
    response = h["client"].post(
        f"/api/trials/{h['trial']}/campaign-draft", json={"operation_id": "button"}
    )
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["strategies"][0].startswith("snapshot_") and not draft["blockers"]
    assert draft["strategies"][0] in load_config(h["path"]).strategies
    case = h["service"].get_case_revision(draft["case_revision_ids"][0])
    assert case["image_asset"] == h["asset"]
    assert case["candidate_context"]["robot_asset"] == h["robot"]
    assert case["candidate_context"]["constraints"] == ["Keep clear of edge"]
    assert "private" not in str(case["candidate_context"])
    assert h["service"].trials.get(h["trial"]) == before
    assert h["service"].trials.count() == 1 and h["launched"] == []
    assert CampaignStore(h["root"]).list() == []
    repeated = (
        h["client"]
        .post(f"/api/trials/{h['trial']}/campaign-draft", json={"operation_id": "button"})
        .json()
    )
    assert repeated["case_revision_ids"] == draft["case_revision_ids"]
    assert repeated["strategies"] == draft["strategies"]


def test_launch_persists_validated_source_and_source_is_not_in_denominator(h):
    draft = seed(h)
    payload, preview = prepare_launch(h["root"], request_from(h, draft), load_config(h["path"]))
    assert payload["source"] == {
        **draft["source"],
        "role": "exploratory_reference",
        "historical_trials_counted": False,
    }
    assert preview["planned_trials"] == 2
    assert payload["spec"]["tasks"][0]["example"]["extras"]["robot_asset"] == h["robot"]
    request = request_from(h, draft)
    request.source.snapshot_sha256 = "0" * 64
    with pytest.raises(ValueError, match="snapshot changed"):
        prepare_launch(h["root"], request, load_config(h["path"]))


@pytest.mark.parametrize("status", ["pending", "running"])
def test_improvement_rejects_unfinished_source_before_restoring_strategies(h, monkeypatch, status):
    draft = seed(h)
    request = request_from(h, draft)
    config = load_config(h["path"])
    payload, _ = prepare_launch(h["root"], request, config)
    store = CampaignStore(h["root"])
    campaign_id = store.create(payload)
    store.status(campaign_id, status)
    before = list_revisions(h["path"], config)

    def unexpected_restore(*args, **kwargs):
        pytest.fail("Unfinished improvement source must not restore strategy revisions")

    monkeypatch.setattr("rove.benchmarks.seeds._strategy_seed", unexpected_restore)
    with pytest.raises(ValueError, match="completed campaign"):
        campaign_seed(h["root"], campaign_id, config_path=h["path"])
    # A caller cannot bypass the draft guard with a valid hash of a running record.
    request = LaunchRequest.model_validate(
        {
            **request.model_dump(),
            "source": {
                "type": "campaign",
                "id": campaign_id,
                "snapshot_sha256": content_hash(sanitize(store.get(campaign_id))),
            },
        }
    )
    with pytest.raises(ValueError, match="completed campaign"):
        prepare_launch(h["root"], request, config)
    assert list_revisions(h["path"], config) == before


def test_endpoint_drift_blocks_restore_and_existing_snapshot_becomes_unavailable(h):
    draft = seed(h)
    contents = yaml.safe_load(h["path"].read_text())
    contents["endpoints"]["mock"]["config"]["mock_quality"] = 0.25
    h["path"].write_text(yaml.safe_dump(contents))
    reset_config_cache()
    current = load_config(h["path"])
    assert draft["strategies"][0] not in current.strategies
    assert not list_revisions(h["path"], current)[0]["available"]
    blocked = seed(h)
    assert blocked["strategies"] == [] and blocked["blockers"]
    assert "changed" in blocked["blockers"][0]
    assert h["service"].trials.count() == 1


def test_unchanged_existing_strategy_is_selected_without_duplicate_catalog(h):
    config = load_config(h["path"])
    definition = config.strategies["base"].model_dump(mode="json")
    original = h["service"].trials.get(h["trial"])
    frozen = {**original["snapshot"]["config"], "strategies": {"base": definition}}
    trial = h["service"].trials.begin(
        source="quick", task=original["task"], strategy={"id": "base", **definition}, config=frozen
    )
    h["service"].trials.finish(trial, status="completed", result={})
    draft = trial_seed(
        h["root"], trial, TrialSeedRequest(operation_id="existing"), config_path=h["path"]
    )
    assert draft["strategies"] == ["base"]
    assert list_revisions(h["path"], load_config(h["path"])) == []


@pytest.mark.asyncio
async def test_new_campaign_runs_fresh_mock_trials_preserving_source_and_robot(h):
    draft = seed(h)
    request = request_from(h, draft)
    request.strategies.append("base")
    payload, preview = prepare_launch(h["root"], request, load_config(h["path"]))
    store = CampaignStore(h["root"])
    cid = store.create(payload)
    await run_campaign(store, cid)
    assert store.get(cid)["status"] == "completed"
    assert preview["planned_trials"] == len(store.trials(cid)) == 4
    assert h["service"].trials.count() == 5  # one source plus four newly executed trials
    assert all(t["trial_id"] != h["trial"] for t in store.trials(cid))
    for row in store.trials(cid):
        task = h["service"].trials.get(row["trial_id"])["task"]
        assert task["example"]["extras"]["robot_asset"] == h["robot"]
    improve = campaign_seed(h["root"], cid, config_path=h["path"])
    assert improve["defaults"]["seeds"] == [0, 1] and improve["defaults"]["ks"] == [1, 2]
    assert improve["contract_id"] == request.contract_id
    assert improve["contract"]["id"] == request.contract_id
    assert improve["source"]["type"] == "campaign"
    assert improve["case_revision_ids"] == draft["case_revision_ids"]
    assert len(store.trials(cid)) == 4


@pytest.mark.asyncio
async def test_worker_passes_verified_robot_asset_to_real_run_manager(h, monkeypatch, tmp_path):
    from rove.benchmarks import worker
    from rove.orchestrator.run_manager import RunManager

    draft = seed(h)
    payload, _ = prepare_launch(h["root"], request_from(h, draft), load_config(h["path"]))
    task = copy.deepcopy(payload["spec"]["tasks"][0])
    import base64

    task["image_base64"] = base64.b64encode(
        h["service"].trials.asset_path(h["asset"]["sha256"]).read_bytes()
    ).decode()
    captured = []

    def factory(*args, **kwargs):
        captured.append(kwargs["urdf_path"])
        return RunManager(*args, **kwargs)

    monkeypatch.setattr(worker, "RunManager", factory)
    result = await worker.execute(
        {
            "config": copy.deepcopy(payload["config"]),
            "strategy_id": draft["strategies"][0],
            "seed": 1,
            "task": task,
            "trial_root": str(h["root"]),
        },
        tmp_path / "worker.yaml",
    )
    assert captured == [str(h["service"].trials.asset_path(h["robot"]["sha256"]))]
    assert result["execution"] == "completed"
    h["service"].trials.asset_path(h["robot"]["sha256"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        prepare_launch(h["root"], request_from(h, draft), load_config(h["path"]))


@pytest.mark.parametrize("difference", ["robot_absent", "constraints"])
def test_bound_case_reuse_checks_all_actual_public_inputs_and_absent_robot(h, difference):
    original = h["service"].trials.get(h["trial"])
    case = h["service"].import_case(
        {
            "name": "Existing case",
            "task": "Pick red block",
            "candidate_context": {"constraints": ["Old constraint"], "robot_asset": h["robot"]},
        },
        h["asset"]["sha256"],
    )
    frozen = copy.deepcopy(original["snapshot"]["config"])
    public = copy.deepcopy(case["candidate_context"])
    if difference == "robot_absent":
        frozen.pop("robot_asset")
    else:
        public["constraints"] = ["Actual trial constraint"]
    trial = h["service"].trials.begin(
        source="quick",
        task={
            "task": case["task"],
            "image_asset": h["asset"],
            "case_revision_id": case["id"],
            "example": {"extras": public},
        },
        strategy=original["strategy"],
        config=frozen,
    )
    h["service"].trials.finish(trial, status="completed", result={})
    draft = trial_seed(
        h["root"], trial, TrialSeedRequest(operation_id="different"), config_path=h["path"]
    )
    assert draft["case_revision_ids"] != [case["id"]]
    actual = h["service"].get_case_revision(draft["case_revision_ids"][0])["candidate_context"]
    assert actual.get("robot_asset") == frozen.get("robot_asset")
    assert actual["constraints"] == public["constraints"]
    request = request_from(h, draft)
    request.case_revision_ids = [case["id"]]
    with pytest.raises(ValueError, match="exact observation, public inputs"):
        prepare_launch(h["root"], request, load_config(h["path"]))


def test_recommendations_separate_missing_assessments_failures_and_all_pass(h):
    from rove.benchmarks.seeds import _recommendations

    def recommend(outcome, execution="completed", stage=None):
        return _recommendations(
            [
                {
                    "strategy_id": "base",
                    "trial_id": "trial",
                    "outcome": outcome,
                    "execution": execution,
                    "result": {"failure_stage": stage},
                }
            ],
            ["base"],
        )[0]

    unknown = recommend("unknown")
    assert unknown["source"] == "missing_assessment" and "reviews" in unknown["text"]
    passing = recommend("pass")
    assert passing["stage"] is None and "harder held-out" in passing["text"]
    failed = recommend("fail", stage="act")
    assert failed["stage"] == "act" and failed["source"] == "recorded_failure"
    assert failed["trial_ids"] == ["trial"] and "hypothesis" in failed["text"]


@pytest.mark.asyncio
async def test_improvement_keeps_exact_frozen_baseline_and_compares_each_candidate(h):
    from rove.benchmarks.baselines import BaselineInput, BaselineStore

    original = seed(h)
    first_request = request_from(h, original)
    first_request.strategies.append("base")
    payload, _ = prepare_launch(h["root"], first_request, load_config(h["path"]))
    store = CampaignStore(h["root"])
    first_id = store.create(payload)
    await run_campaign(store, first_id)
    named = BaselineStore(h["root"]).save(
        BaselineInput(
            name="Chosen reference",
            campaign_id=first_id,
            strategy_id=original["strategies"][0],
            operation_id="baseline",
        )
    )
    assert all(row["outcome"] == "unknown" for row in named["trial_outcomes"])
    # A later reviewer may resolve the source campaign, but cannot rewrite the reference.
    original_trial = store.trials(first_id)[0]
    h["service"].create_review(
        {
            "target_type": "trial_output",
            "trial_id": original_trial["trial_id"],
            "case_revision_id": original["case_revision_ids"][0],
            "contract_id": first_request.contract_id,
            "reviewer": "Expert",
            "status": "final",
            "decision": "accepted",
            "criteria": {"accept": "accepted"},
            "rationale": "Checked output",
        }
    )
    draft_response = h["client"].post(f"/api/campaigns/{first_id}/improvement-draft", json={})
    assert draft_response.status_code == 200, draft_response.text
    improved = draft_response.json()
    assert improved["baselines"][0]["revision_id"] == named["revision_id"]
    request = LaunchRequest(
        name="Candidate",
        case_revision_ids=improved["case_revision_ids"],
        strategies=improved["strategies"],
        contract_id=first_request.contract_id,
        seeds=improved["defaults"]["seeds"],
        ks=improved["defaults"]["ks"],
        source=improved["source"],
        baseline_revision_id=named["revision_id"],
    )
    candidate, preview = prepare_launch(h["root"], request, load_config(h["path"]))
    assert candidate["baseline"] == {
        "campaign_id": first_id,
        "strategy_id": named["strategy_id"],
        "revision_id": named["revision_id"],
    }
    assert {row["candidate_strategy_id"] for row in preview["comparisons"]} == set(
        request.strategies
    )
    assert all(row["comparable"] for row in preview["comparisons"])
    candidate_id = store.create(candidate)
    await run_campaign(store, candidate_id)
    for sid in request.strategies:
        response = h["client"].get(
            f"/api/campaigns/{candidate_id}/comparison", params={"candidate_strategy_id": sid}
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["candidate_strategy_id"] == sid
        assert data["baseline_reference"]["revision_id"] == named["revision_id"]
        assert all(row["outcome"] == "unknown" for row in data["baseline_trials"])
        assert data["baseline_assessments"] == named["assessments"]
    assert (
        h["client"]
        .get(
            f"/api/campaigns/{candidate_id}/comparison", params={"candidate_strategy_id": "missing"}
        )
        .status_code
        == 422
    )
    extra = h["service"].import_case(
        {"name": "More coverage", "task": "Check blue object"}, h["asset"]["sha256"]
    )
    request.case_revision_ids.append(extra["id"])
    _, larger = prepare_launch(h["root"], request, load_config(h["path"]))
    assert larger["ready"] and not any(row["comparable"] for row in larger["comparisons"])
    assert all(row["reasons"] for row in larger["comparisons"])
    request.baseline_revision_id = named["id"]
    with pytest.raises(ValueError, match="exact source campaign"):
        prepare_launch(h["root"], request, load_config(h["path"]))
