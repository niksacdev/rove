"""Independent edge cases for imported branches and malformed source metadata."""

import concurrent.futures
import json

import pytest
from httpx import ASGITransport, AsyncClient

from rove.datasets.library import sync_library
from rove.datasets.service import DatasetService
from tests import test_sample_library as samples

source = samples.source


def change_source(source, **changes):
    path = source / "manifest.json"
    row = json.loads(path.read_text())[0]
    row.update(changes)
    path.write_text(json.dumps([row]))


def test_source_rollback_selects_existing_revision_without_rewriting_history(tmp_path, source):
    root = tmp_path / "journal"
    first = sync_library(root, source)["examples"][0]
    service = DatasetService(root)
    original = service.get_case_revision(first["case_revision_id"])
    change_source(source, task="Source B")
    second = sync_library(root, source)["examples"][0]
    change_source(source, task=first["task"])
    restored = sync_library(root, source)["examples"][0]
    assert restored["case_revision_id"] == first["case_revision_id"]
    assert service.get_case(first["case_id"])["id"] == first["case_revision_id"]
    assert service.get_case_revision(second["case_revision_id"])["task"] == "Source B"
    assert service.get_case_revision(second["case_revision_id"])["revision"] == 2
    edited = service.revise_case(
        first["case_id"],
        {"name": "Edited", "task": "User task"},
        expected_head_revision_id=restored["case_revision_id"],
    )
    assert edited["revision"] == 3 and edited["parent_id"] == original["id"]
    assert edited["reference_data"] == original["reference_data"]
    assert service.trials.count() == 0 and service.list_reviews() == []


def test_repeated_source_rollback_cannot_displace_even_identical_user_authored_revision(
    tmp_path, source
):
    root = tmp_path / "journal"
    first = sync_library(root, source)["examples"][0]
    service = DatasetService(root)
    original = service.get_case_revision(first["case_revision_id"])
    fields = (
        "name",
        "task",
        "conditions",
        "candidate_context",
        "reference_data",
        "recorded_evidence",
    )
    edited = service.revise_case(
        first["case_id"],
        {key: original[key] for key in fields},
        expected_head_revision_id=original["id"],
    )
    assert edited["sha256"] == original["sha256"] and edited["id"] != original["id"]
    change_source(source, task="Source B")
    second = sync_library(root, source)["examples"][0]
    change_source(source, task=first["task"])
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        restored = list(pool.map(lambda _: sync_library(root, source), range(3)))
    assert all(row["examples"][0]["case_revision_id"] == original["id"] for row in restored)
    assert service.get_case(first["case_id"])["id"] == edited["id"]
    assert service.get_case_revision(second["case_revision_id"])["parent_id"] == original["id"]
    with service.trials.connect() as db:
        assert db.execute("SELECT count(*) FROM case_revisions").fetchone()[0] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("category", [[], {}, None, 3, ""])
async def test_malformed_category_remains_visible_as_unavailable(
    tmp_path, source, category, monkeypatch
):
    from rove.api.app import app

    path = source / "manifest.json"
    good = json.loads(path.read_text())[0]
    bad = {**good, "filename": "bad.png", "eval_category": category}
    path.write_text(json.dumps([good, bad]))
    root = tmp_path / "journal"
    monkeypatch.setattr("rove.api.app._data_dir", source)
    monkeypatch.setattr("rove.api.app._trial_root", root)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.get("/api/examples")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == {"total": 2, "imported": 1, "unavailable": 1}
    assert payload["examples"][1]["import_status"] == "unavailable"
    assert "case_revision_id" not in payload["examples"][1]
    assert {row["name"] for row in payload["categories"]} == {"atomic", "uncategorized"}
    case = DatasetService(root).get_case_revision(payload["examples"][0]["case_revision_id"])
    assert case["conditions"]["eval_category"] == "atomic"
    assert case["recorded_evidence"] == {}
