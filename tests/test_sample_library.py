"""Existing robotics samples become stable cases without changing their evidence meaning."""

import concurrent.futures
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from rove.benchmarks.workflow import LaunchRequest, prepare_launch
from rove.datasets.library import sync_library
from rove.datasets.service import DatasetService
from rove.models.config import load_config


@pytest.fixture
def source(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir()
    output = io.BytesIO()
    Image.new("RGB", (3, 3), "red").save(output, format="PNG")
    (directory / "scene.png").write_bytes(output.getvalue())
    row = {
        "filename": "scene.png",
        "task": "Pick red block",
        "eval_category": "atomic",
        "source": {"dataset": "Synthetic fixture", "license": "MIT", "id": "scene1"},
        "proprioception": [0, 1],
        "constraints": ["Avoid blue block"],
        "eval_qa": {"correct_answer_text": "red"},
        "expected_subtasks": ["grasp"],
        "ground_truth_action": [0.1, 0.2],
        "robot_descriptor": {"name": "test"},
    }
    (directory / "manifest.json").write_text(json.dumps([row]))
    return directory


def test_real_library_all_49_preserve_sources_and_private_references(tmp_path):
    directory = Path(__file__).resolve().parents[1] / "data"
    original = (directory / "manifest.json").read_bytes()
    first = sync_library(tmp_path, directory)
    second = sync_library(tmp_path, directory)
    assert first["summary"] == {"total": 49, "imported": 49, "unavailable": 0}
    assert first == second
    assert original == (directory / "manifest.json").read_bytes()
    service = DatasetService(tmp_path)
    assert len(service.list_cases(limit=100)) == 49
    assert service.trials.count() == 0
    assert service.list_reviews() == []
    for row in first["examples"]:
        case = service.get_case_revision(row["case_revision_id"])
        assert case["conditions"]["source"] == row["source"]
        assert case["recorded_evidence"] == {}
        assert case["readiness"]["assessment"] == "unreviewed"
        for key in (
            "eval_qa",
            "expected_subtasks",
            "acceptable_interpretations",
            "ground_truth_action",
        ):
            assert key not in case["candidate_context"]
            if key in row:
                assert case["reference_data"][key] == row[key]
        for key in ("robot", "proprioception", "constraints", "correction", "turns"):
            if key in row:
                assert case["candidate_context"][key] == row[key]


def test_concurrent_imports_do_not_create_duplicate_cases(tmp_path, source):
    root = tmp_path / "journal"
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        outputs = list(pool.map(lambda _: sync_library(root, source), range(3)))
    assert outputs[0] == outputs[1] == outputs[2]
    with DatasetService(root).trials.connect() as db:
        assert db.execute("SELECT count(*) FROM cases").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM case_revisions").fetchone()[0] == 1


def test_source_changes_version_inputs_without_overwriting_user_edits(tmp_path, source):
    root = tmp_path / "journal"
    first = sync_library(root, source)["examples"][0]
    service = DatasetService(root)
    old = service.get_case_revision(first["case_revision_id"])
    manifest = source / "manifest.json"
    row = json.loads(manifest.read_text())[0]
    row["task"] = "Pick blue block"
    manifest.write_text(json.dumps([row]))
    second = sync_library(root, source)["examples"][0]
    assert second["case_id"] == first["case_id"]
    assert second["case_revision_id"] != first["case_revision_id"]
    assert service.get_case(first["case_id"])["revision"] == 2
    assert service.get_case_revision(old["id"])["task"] == "Pick red block"
    edited = service.revise_case(
        first["case_id"],
        {"name": "My custom task", "task": "My task"},
        expected_head_revision_id=second["case_revision_id"],
    )
    row["task"] = "Latest source instruction"
    manifest.write_text(json.dumps([row]))
    third = sync_library(root, source)["examples"][0]
    assert service.get_case(first["case_id"])["id"] == edited["id"]
    assert service.get_case_revision(third["case_revision_id"])["task"] == row["task"]
    assert sync_library(root, source)["examples"][0] == third
    assert (
        service.get_case_revision(third["case_revision_id"])["parent_id"]
        == second["case_revision_id"]
    )
    assert edited["reference_data"] == old["reference_data"]
    later = service.revise_case(
        first["case_id"],
        {"name": "Later", "task": "My next task"},
        expected_head_revision_id=edited["id"],
    )
    assert later["revision"] == 5 and later["parent_id"] == edited["id"]


@pytest.mark.parametrize("filename", ["../outside.png", "/etc/passwd", "missing.png", "escape.png"])
def test_missing_or_unsafe_media_is_reported_without_losing_originals(tmp_path, source, filename):
    (source / "escape.png").symlink_to(source.parent / "outside.png")
    manifest = source / "manifest.json"
    rows = json.loads(manifest.read_text())
    rows.append({**rows[0], "filename": filename})
    manifest.write_text(json.dumps(rows))
    result = sync_library(tmp_path / "journal", source)
    assert result["summary"] == {"total": 2, "imported": 1, "unavailable": 1}
    assert result["examples"][1]["import_status"] == "unavailable"
    assert "case_revision_id" not in result["examples"][1]


def test_new_case_campaign_keeps_reference_answers_out_of_candidate_and_episode(tmp_path, source):
    root = tmp_path / "journal"
    row = sync_library(root, source)["examples"][0]
    service = DatasetService(root)
    contract = service.create_contract(
        {
            "name": "SME rubric",
            "scope": "plan_quality",
            "evidence_mode": "candidate_output",
            "criteria": [{"id": "plan", "description": "Correct plan"}],
        }
    )
    payload, preview = prepare_launch(
        root,
        LaunchRequest(
            case_revision_ids=[row["case_revision_id"]],
            contract_id=contract["id"],
            strategies=["mock"],
            seeds=[1],
            ks=[1],
        ),
        load_config(),
    )
    assert preview["ready"]
    example = payload["spec"]["tasks"][0]["example"]
    serialized = json.dumps(example)
    for key in ("eval_qa", "expected_subtasks", "ground_truth_action", "episode"):
        assert key not in serialized
    assert example["extras"]["proprioception"] == [0, 1]


@pytest.mark.asyncio
async def test_examples_api_enriches_legacy_response_without_changing_category_filter(
    tmp_path, source, monkeypatch
):
    from httpx import ASGITransport, AsyncClient

    from rove.api.app import app

    monkeypatch.setattr("rove.api.app._data_dir", source)
    monkeypatch.setattr("rove.api.app._trial_root", tmp_path / "journal")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.get("/api/examples?eval_category=atomic")
        assert response.status_code == 200
        row = response.json()["examples"][0]
        assert row["filename"] == "scene.png" and row["case_revision_id"]
        assert response.json()["categories"] == [{"name": "atomic", "count": 1}]
        assert (await client.get("/api/examples?eval_category=negative")).json()["examples"] == []
