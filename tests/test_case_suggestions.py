"""Source-derived rubrics stay editable and cannot become outcome evidence."""

import io
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from rove.api.datasets import create_dataset_router
from rove.benchmarks.workflow import LaunchRequest, prepare_launch
from rove.datasets.library import sync_library
from rove.datasets.models import SuccessContract
from rove.datasets.service import DatasetService
from rove.datasets.suggestions import suggest_success
from rove.models.config import load_config


def test_bundled_rubrics_use_real_tasks_without_creating_reviews_or_changing_revisions(tmp_path):
    library = sync_library(tmp_path, Path(__file__).resolve().parents[1] / "data")
    service = DatasetService(tmp_path)
    assert library["summary"]["imported"] == 49
    for row in library["examples"]:
        case = service.get_case_revision(row["case_revision_id"])
        draft = row["suggested_success"]
        assert draft == case["suggested_success"]
        assert draft["requires_confirmation"] and draft["status"] == "draft"
        contract = SuccessContract.model_validate(draft["contract"])
        assert contract.evidence_mode == "candidate_output"
        assert contract.scope != "episode_outcome"
        assert all(c.assessment == "human_review" for c in contract.criteria)
        assert row["task"] in contract.criteria[0].description
        assert case["readiness"]["assessment"] == "unreviewed"
        assert "suggested_success" not in case["candidate_context"]
    assert service.list_contracts() == []
    assert service.list_reviews() == []
    assert service.trials.count() == 0
    with service.trials.connect() as db:
        rows = db.execute("SELECT payload FROM case_revisions").fetchall()
    assert all("suggested_success" not in json.loads(row[0]) for row in rows)

    bowl = next(r for r in library["examples"] if r["filename"].endswith("goal_bowl_on_plate.jpg"))
    rubric = bowl["suggested_success"]["contract"]
    assert "pick up the bowl; place on the plate" in rubric["criteria"][2]["description"]
    contract = service.create_contract(rubric)
    payload, _ = prepare_launch(
        tmp_path,
        LaunchRequest(
            case_revision_ids=[bowl["case_revision_id"]],
            contract_id=contract["id"],
            strategies=["mock"],
            seeds=[0],
            ks=[1],
        ),
        load_config(),
    )
    extras = payload["spec"]["tasks"][0]["example"]["extras"]
    assert "reference_data" not in extras
    assert "expected_subtasks" not in extras
    assert "suggested_success" not in extras
    assert "episode" not in extras

    private = "Reviewer-only expected placement: center of the plate"
    extended = service.create_contract(
        {**rubric, "case_expectations": {bowl["case_revision_id"]: private}}
    )
    assert extended["id"] != contract["id"]
    assert service.get_contract(extended["id"])["case_expectations"] == {
        bowl["case_revision_id"]: private
    }
    payload, _ = prepare_launch(
        tmp_path,
        LaunchRequest(
            case_revision_ids=[bowl["case_revision_id"]],
            contract_id=extended["id"],
            strategies=["mock"],
            seeds=[0],
            ks=[1],
        ),
        load_config(),
    )
    assert payload["contract"]["case_expectations"][bowl["case_revision_id"]] == private
    assert private not in json.dumps(payload["spec"]["tasks"])
    with pytest.raises(KeyError):
        service.create_contract({**rubric, "case_expectations": {"missing-revision": "Expected"}})
    with pytest.raises(ValueError):
        service.create_contract(
            {**rubric, "case_expectations": {bowl["case_revision_id"]: "x" * 8001}}
        )


def test_correction_constraints_and_source_qa_are_explicitly_reviewable_drafts():
    result = suggest_success(
        {
            "name": "Corrected instruction",
            "task": "Put mugs on plates",
            "candidate_context": {
                "constraints": ["exclude_object:yellow_mug"],
                "correction": {"feedback": "Only move the white mug"},
            },
            "reference_data": {
                "eval_qa": {
                    "question": "Is the yellow mug excluded?",
                    "correct_answer_text": "Yes",
                },
                "acceptable_interpretations": ["Move white mug to the empty plate"],
            },
        }
    )
    criteria = {r["id"]: r["description"] for r in result["contract"]["criteria"]}
    assert "Only move the white mug" in criteria["correction"]
    assert "exclude_object:yellow_mug" in criteria["constraints"]
    assert "not the only acceptable answers" in criteria["interpretation"]
    assert 'source annotation says "Yes"' in criteria["source_reference"]
    assert "criterion unknown" in criteria["source_reference"]
    assert "not evidence" in criteria["source_reference"]


def test_uploaded_long_or_unusual_metadata_does_not_break_case_reads():
    draft = suggest_success(
        {
            "name": "x" * 160,
            "task": "Move " + "x" * 9990,
            "candidate_context": {"correction": "not structured", "constraints": [{"a": 1}]},
            "reference_data": {"expected_subtasks": ["x" * 5000] * 40, "eval_qa": []},
        }
    )
    assert SuccessContract.model_validate(draft["contract"])
    assert "reference_data.eval_qa" not in draft["basis"]


def test_search_api_paginates_every_case_and_only_current_revisions(tmp_path):
    service = DatasetService(tmp_path)
    image = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(image, format="PNG")
    asset = service.trials.save_asset(image.getvalue(), "image/png")
    cases = []
    for i in range(27):
        cases.append(
            service.import_case(
                {
                    "name": f"Case {i:02}",
                    "task": f"Move item {i:02} onto plate",
                    "candidate_context": {"eval_category": "atomic" if i < 20 else "negative"},
                },
                asset["sha256"],
            )
        )
    app = FastAPI()
    app.include_router(create_dataset_router(tmp_path, lambda _: None))
    client = TestClient(app)
    first = client.get("/api/cases?limit=12&offset=0").json()
    second = client.get("/api/cases?limit=12&offset=12").json()
    third = client.get("/api/cases?limit=12&offset=24").json()
    assert first["total"] == second["total"] == third["total"] == 27
    assert len(first["cases"]) == len(second["cases"]) == 12
    assert len(third["cases"]) == 3
    assert len({r["id"] for p in (first, second, third) for r in p["cases"]}) == 27
    search = client.get("/api/cases?q=ITEM%2000&category=atomic").json()
    assert search["total"] == 1 and search["cases"][0]["id"] == cases[0]["id"]
    assert search["categories"] == [{"name": "atomic", "count": 1}]
    assert client.get("/api/cases?category=negative").json()["total"] == 7
    assert client.get("/api/cases?q=%25%27%20OR%201%3D1").json()["total"] == 0
    assert client.get("/api/cases?limit=101").status_code == 422
    assert client.get("/api/cases?offset=-1").status_code == 422
    assert client.get("/api/cases", params={"q": "x" * 201}).status_code == 422
    updated = service.revise_case(
        cases[0]["case_id"],
        {"name": "Replacement", "task": "Find uniquely revised target"},
        expected_head_revision_id=cases[0]["id"],
    )
    assert client.get("/api/cases?q=item%2000").json()["total"] == 0
    revised = client.get("/api/cases?q=uniquely&category=uncategorized").json()
    assert revised["cases"][0]["id"] == updated["id"]
    assert revised["total"] == 1
