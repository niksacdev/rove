"""An interactive trial can claim a saved case only when its actual inputs match."""

import asyncio
import importlib
import io
import json

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from rove.datasets.service import DatasetService


@pytest.fixture
def linked_case(tmp_path, monkeypatch):
    app = importlib.import_module("rove.api.app")
    root = tmp_path / "case-runner"
    monkeypatch.setattr(app, "_trial_root", root)
    service = DatasetService(root)
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "blue").save(stream, format="PNG")
    data = stream.getvalue()
    image = service.trials.save_asset(data, "image/png")
    case = service.import_case(
        {
            "name": "Blue target",
            "task": "Place the blue block in the tray",
            "candidate_context": {
                "proprioception": [0, 1],
                "constraints": ["Keep clear of the cup"],
            },
            "reference_data": {
                "eval_qa": {"correct_answer_text": "private blue answer"},
                "expected_subtasks": ["private expected step"],
            },
            "recorded_evidence": {"earlier_outcome": "pass"},
        },
        image["sha256"],
    )
    calls = []

    async def execute(*args, **kwargs):
        calls.append((args, kwargs))
        for trial_id in kwargs["trial_ids"].values():
            service.trials.finish(trial_id, status="completed", result={"stages": []})

    monkeypatch.setattr(app, "_run_multi_strategy", execute)
    return app, service, case, data, calls


async def test_case_trial_records_exact_revision_and_public_context_before_execution(linked_case):
    app, service, case, data, calls = linked_case
    async with AsyncClient(
        transport=ASGITransport(app=app.app), base_url="http://localhost"
    ) as client:
        response = await client.post(
            "/api/evaluate",
            data={"task": case["task"], "strategy_ids": "mock", "case_revision_id": case["id"]},
            files={
                "image": ("observation.png", data, "image/png"),
                "urdf": ("robot.urdf", b'<robot name="test"/>', "application/xml"),
            },
        )
        assert response.status_code == 202, response.text
        identity = response.json()["trial_ids"]["mock"]
        trial = service.trials.get(identity)
        assert trial["task"]["case_revision_id"] == case["id"]
        assert trial["task"]["case_id"] == case["case_id"]
        assert trial["task"]["case_sha256"] == case["sha256"]
        assert trial["task"]["image_asset"]["sha256"] == case["image_asset"]["sha256"]
        assert trial["task"]["example"] == {
            "ground_truth": None,
            "extras": case["candidate_context"],
        }
        robot_asset = trial["snapshot"]["config"]["robot_asset"]
        assert (
            service.trials.asset_path(robot_asset["sha256"]).read_bytes() == b'<robot name="test"/>'
        )
        assert "private blue answer" not in json.dumps(trial)
        assert "private expected step" not in json.dumps(trial)
        assert "earlier_outcome" not in json.dumps(trial)
        await asyncio.gather(*list(app._background_tasks))
        assert len(calls) == 1
        assert calls[0][1]["example"].ground_truth is None
        assert calls[0][1]["example"].extras == case["candidate_context"]
        assert calls[0][1]["urdf_path"]
        page = await client.get("/api/trials", params={"case_revision_id": case["id"]})
        assert [row["id"] for row in page.json()["trials"]] == [identity]


@pytest.mark.parametrize("changed", ["task", "image", "legacy", "missing"])
async def test_mismatched_case_input_is_rejected_before_any_trial_or_execution(
    linked_case, changed
):
    app, service, case, data, calls = linked_case
    form = {"task": case["task"], "strategy_ids": "mock", "case_revision_id": case["id"]}
    if changed == "task":
        form["task"] = "A different task"
    if changed == "image":
        data = b"different observation"
    if changed == "legacy":
        form["example_filename"] = "libero/libero_000.jpg"
    if changed == "missing":
        form["case_revision_id"] = "missing"
    async with AsyncClient(
        transport=ASGITransport(app=app.app), base_url="http://localhost"
    ) as client:
        response = await client.post(
            "/api/evaluate", data=form, files={"image": ("observation.png", data, "image/png")}
        )
    assert response.status_code == (404 if changed == "missing" else 422)
    assert service.trials.count() == 0
    assert calls == []
