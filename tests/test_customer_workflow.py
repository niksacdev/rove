"""Exercise the customer journey across HTTP, durable inputs, execution and assessment."""

import asyncio
import concurrent.futures
import io
import json

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from rove.api.datasets import create_dataset_router
from rove.api.trials import create_trial_router as trial_router
from rove.benchmarks.api import create_router
from rove.benchmarks.runner import run_campaign
from rove.benchmarks.store import CampaignStore
from rove.benchmarks.workflow import LaunchRequest, assessed_trials, create_campaign, prepare_launch
from rove.datasets.service import DatasetService
from rove.models.config import RoveConfig


@pytest.fixture
def config(monkeypatch):
    config = RoveConfig.model_validate(
        {
            "endpoints": {
                "judge": {
                    "type": "vlm",
                    "adapter": "mock_vlm",
                    "capabilities": ["scene_analysis", "verification"],
                    "config": {"mock_latency_ms": [0, 0]},
                },
                "candidate": {
                    "type": "vlm",
                    "adapter": "mock_vlm",
                    "capabilities": ["scene_analysis", "verification"],
                    "config": {"mock_quality": 0.9, "mock_latency_ms": [0, 0]},
                },
            },
            "strategies": {
                "base": {"perceive": "judge", "verify": "judge"},
                "candidate": {"perceive": "candidate", "verify": "judge"},
            },
        }
    )
    monkeypatch.setattr("rove.api.datasets.load_config", lambda: config)
    monkeypatch.setattr("rove.benchmarks.api.load_config", lambda: config)
    return config


@pytest.fixture
def service(tmp_path):
    return DatasetService(tmp_path)


@pytest.fixture
def case(service):
    stream = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(stream, format="PNG")
    asset = service.trials.save_asset(stream.getvalue(), "image/png")
    return service.import_case(
        {
            "name": "Red block",
            "task": "Plan picking the red block",
            "candidate_context": {"constraints": ["Avoid the blue block"]},
        },
        asset["sha256"],
    )


@pytest.fixture
def contract(service):
    return service.create_contract(
        {
            "name": "Useful plan",
            "scope": "plan_quality",
            "evidence_mode": "candidate_output",
            "criteria": [
                {"id": "correct", "description": "Selects the correct object and a safe path"}
            ],
        }
    )


def request(case, contract, **kwargs):
    return LaunchRequest(
        case_revision_ids=[case["id"]],
        contract_id=contract["id"],
        strategies=["base"],
        seeds=[1, 2],
        ks=[1, 2, 3],
        operation_id="baseline",
        **kwargs,
    )


def rating(case, contract, trial_id, **kwargs):
    return {
        "target_type": "trial_output",
        "case_revision_id": case["id"],
        "contract_id": contract["id"],
        "trial_id": trial_id,
        "reviewer": "SME",
        "status": "final",
        "decision": "accepted",
        "criteria": {"correct": "accepted"},
        "rationale": "Checked the exact plan against the image",
        **kwargs,
    }


async def client_for(root, launch=lambda _identity: None):
    app = FastAPI()
    app.include_router(create_dataset_router(root, launch))
    app.include_router(trial_router(lambda: DatasetService(root).trials))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_customer_baseline_review_freeze_ablation_journey(
    tmp_path, config, case, contract, service
):
    async with await client_for(tmp_path) as client:
        preview = await client.post(
            "/api/campaigns/preview", json=request(case, contract).model_dump()
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["ready"]
        assert preview.json()["metrics"][0]["status"] == "needs_review"
        created = await client.post(
            "/api/campaigns/from-cases", json=request(case, contract).model_dump()
        )
        assert created.status_code == 202, created.text
        baseline_id = created.json()["id"]
        store = CampaignStore(tmp_path)
        await run_campaign(store, baseline_id)
        raw = store.trials(baseline_id)
        assert len(raw) == 2
        assert all(t["execution"] == "completed" for t in raw), raw
        frozen_task = store.get(baseline_id)["spec"]["tasks"][0]
        assert frozen_task["image_base64"] is None
        assert frozen_task["image_asset"] == case["image_asset"]["sha256"]
        pending = (await client.get(f"/api/campaigns/{baseline_id}/assessments")).json()
        assert pending["summary"]["tasks"][0]["unknown"] == 2
        pending_history = (await client.get("/api/trials")).json()
        assert all(t["assessment"]["outcome"] == "unknown" for t in pending_history["trials"])
        pending_detail = (await client.get(f"/api/trials/{raw[0]['trial_id']}")).json()
        assert pending_detail["assessment"]["outcome"] == "unknown"
        for trial in raw:
            review = await client.post(
                "/api/reviews", json=rating(case, contract, trial["trial_id"])
            )
            assert review.status_code == 201, review.text
        assessed = (await client.get(f"/api/campaigns/{baseline_id}/assessments")).json()
        assert assessed["summary"]["tasks"][0]["passed"] == 2
        assert len(assessed["assessments"]["review_ids"]) == 2
        assert store.trials(baseline_id) == raw  # Immutable execution evidence.
        assert len(list(service.trials.for_campaign(baseline_id))) == 2
        history = (await client.get("/api/trials", params={"case_revision_id": case["id"]})).json()
        assert history["total"] == 2
        reusable = []
        for target in ("case_validity", "case_annotation"):
            payload = {
                "target_type": target,
                "case_revision_id": case["id"],
                "contract_id": contract["id"],
                "reviewer": "SME",
                "status": "final",
                "decision": "accepted",
                "rationale": "Reusable input reviewed",
            }
            if target == "case_annotation":
                payload["annotations"] = {"target": "red block"}
            reusable.append((await client.post("/api/reviews", json=payload)).json()["id"])
        freeze = {
            "name": "Customer data v1",
            "contract_id": contract["id"],
            "members": [{"case_revision_id": case["id"], "review_ids": reusable}],
        }
        reviewed = (await client.post("/api/datasets/preview", json=freeze)).json()
        dataset = await client.post(
            "/api/datasets", json={**freeze, "expected_preview_hash": reviewed["preview_hash"]}
        )
        assert dataset.status_code == 201, dataset.text
        frozen_request = request(case, contract).model_dump()
        frozen_request.update(
            case_revision_ids=[],
            dataset_revision_id=dataset.json()["id"],
            operation_id="frozen-baseline",
        )
        frozen_base = (await client.post("/api/campaigns/from-cases", json=frozen_request)).json()[
            "id"
        ]
        ablation = {
            "baseline_strategy_id": "base",
            "candidate_strategy_id": "candidate",
            "intended_change": "Change perception model only",
            "operation_id": "ablation",
        }
        ablation_preview = await client.post(
            f"/api/campaigns/{frozen_base}/ablation-preview", json=ablation
        )
        assert ablation_preview.status_code == 200, ablation_preview.text
        assert ablation_preview.json()["comparison"]["comparable"], ablation_preview.text
        candidate = await client.post(f"/api/campaigns/{frozen_base}/ablation", json=ablation)
        assert candidate.status_code == 202, candidate.text
        candidate_id = candidate.json()["id"]
        assert store.get(candidate_id)["dataset_revision_id"] == dataset.json()["id"]
        compared = await client.get(f"/api/campaigns/{candidate_id}/comparison")
        assert compared.json()["comparable"]
        assert compared.json()["candidate"]["tasks"][0]["unknown"] == 2
        assert "candidate_trials" in compared.json()


@pytest.mark.asyncio
async def test_revision_deeplink_upload_and_request_errors(
    tmp_path, config, case, contract, service
):
    async with await client_for(tmp_path) as client:
        revision = service.revise_case(
            case["case_id"],
            {"name": "New", "task": "New task"},
            expected_head_revision_id=case["id"],
        )
        old = await client.get(f"/api/cases/{case['id']}")
        assert old.json()["task"] == case["task"]
        head = await client.get(f"/api/cases/{case['case_id']}")
        assert head.json()["id"] == revision["id"]
        bad = await client.post(
            "/api/cases", data={"metadata": "{"}, files={"image": ("a.png", b"bad", "image/png")}
        )
        assert bad.status_code == 422
        no_version = await client.post(
            f"/api/cases/{case['case_id']}/revisions",
            data={"metadata": json.dumps({"name": "Oops", "task": "Oops"})},
        )
        assert no_version.status_code == 422
        examples = await client.post("/api/cases/examples")
        assert examples.status_code == 201, examples.text
        assert len(examples.json()["cases"]) == 3
        assert all(
            c["conditions"]["evidence_quality"] == "synthetic" for c in examples.json()["cases"]
        )


def test_operation_id_is_atomic_and_rejects_changed_configuration(tmp_path, config, case, contract):
    req = request(case, contract)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: create_campaign(tmp_path, req, config)[0], range(3)))
    assert len(set(results)) == 1
    changed = req.model_copy(update={"name": "Changed after submission"})
    with pytest.raises(ValueError, match="Operation ID"):
        create_campaign(tmp_path, changed, config)
    assert len(CampaignStore(tmp_path).list()) == 1


@pytest.mark.asyncio
async def test_duplicate_http_launch_does_not_start_two_workers(
    tmp_path, config, case, contract, monkeypatch
):
    started = []
    gate = asyncio.Event()

    async def worker(_store, identity):
        started.append(identity)
        await gate.wait()

    monkeypatch.setattr("rove.benchmarks.api.run_campaign", worker)
    app = FastAPI()
    app.include_router(create_router(tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        responses = await asyncio.gather(
            *(
                client.post("/api/campaigns/from-cases", json=request(case, contract).model_dump())
                for _ in range(2)
            )
        )
        assert all(r.status_code == 202 for r in responses), [r.text for r in responses]
        await asyncio.sleep(0)
        assert len(started) == 1
        gate.set()


def test_launch_validates_asset_hash_and_physical_evidence_scope(
    tmp_path, config, case, contract, service
):
    path = service.trials.asset_path(case["image_asset"]["sha256"])
    original = path.read_bytes()
    path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    with pytest.raises(ValueError, match="integrity"):
        prepare_launch(tmp_path, request(case, contract), config)
    path.write_bytes(original)
    physical = service.create_contract(
        {
            "name": "Physical completion",
            "scope": "episode_outcome",
            "evidence_mode": "candidate_output",
            "criteria": [{"id": "done", "description": "Placed object"}],
            "metrics": ["episode_completion_time", "autonomous_completion", "recovery"],
        }
    )
    _, preview = prepare_launch(tmp_path, request(case, physical), config)
    assert not preview["ready"]
    assert all(m["status"] == "unavailable" for m in preview["metrics"])


def test_reviews_never_overwrite_execution_and_disagreement_is_unknown(
    tmp_path, config, case, contract, service
):
    payload, _ = prepare_launch(tmp_path, request(case, contract), config)
    trial_id = service.trials.begin(
        source="campaign", task={"case_revision_id": case["id"]}, strategy={"id": "base"}, config={}
    )
    service.trials.finish(trial_id, status="error", result={"partial": "candidate text"})
    service.create_review(rating(case, contract, trial_id))
    raw = {
        "trial_id": trial_id,
        "task_id": case["id"],
        "strategy_id": "base",
        "seed": 1,
        "execution": "error",
        "outcome": "unknown",
    }
    projected, _ = assessed_trials(tmp_path, payload, [raw])
    assert projected[0]["outcome"] == "unknown"
    raw["execution"] = "completed"
    assert assessed_trials(tmp_path, payload, [raw])[0][0]["outcome"] == "pass"
    dissent = service.create_review(
        rating(
            case,
            contract,
            trial_id,
            reviewer="Second SME",
            decision="rejected",
            criteria={"correct": "rejected"},
        )
    )
    assert assessed_trials(tmp_path, payload, [raw])[0][0]["outcome"] == "unknown"
    service.create_review(
        rating(case, contract, trial_id, reviewer="Second SME", supersedes_id=dissent["id"])
    )
    assert assessed_trials(tmp_path, payload, [raw])[0][0]["outcome"] == "pass"
    assert raw["outcome"] == "unknown"
