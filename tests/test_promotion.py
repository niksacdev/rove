"""Quick-history promotion links evidence without manufacturing new attempts."""

import concurrent.futures
import io
import sqlite3

import pytest
from PIL import Image

from rove.datasets.promotion import get_promotion, list_promotions, promote_quick_trial
from rove.datasets.service import ConflictError, DatasetService


@pytest.fixture
def source(tmp_path):
    service = DatasetService(tmp_path)
    image = io.BytesIO()
    Image.new("RGB", (2, 2)).save(image, format="PNG")
    asset = service.trials.save_asset(image.getvalue(), "image/png")
    task = {
        "task": "Pick the red block",
        "image_asset": asset,
        "example": {
            "ground_truth": {"target": "secret answer"},
            "extras": {
                "robot": "test robot",
                "proprioception": [0.1],
                "expected_subtasks": ["private step"],
                "episode": {"outcome": "pass"},
            },
        },
        "candidate_context": {"nested": {"reference_output": "private output", "public": True}},
    }
    trial = service.trials.begin(source="quick", task=task, strategy={"id": "original"}, config={})
    service.trials.append_event(trial, {"event_type": "stage"})
    service.trials.finish(
        trial, status="completed", result={"outcome": "pass", "answer": "baseline answer"}
    )
    return service, trial


def test_promotion_keeps_source_trial_immutable_and_excludes_private_material(source):
    service, trial = source
    before, events = service.trials.get(trial), service.trials.events(trial)
    promoted = promote_quick_trial(service.trials.root, trial, operation_id="operation1")
    assert promoted["trial_id"] == trial
    assert promoted["role"] == "exploratory_reference"
    assert promoted["eligible_for_reliability"] is False
    assert service.trials.count() == 1
    assert service.trials.get(trial) == before
    assert service.trials.events(trial) == events
    case = service.get_case_revision(promoted["case_revision_id"])
    assert case["candidate_context"] == {
        "robot": "test robot",
        "proprioception": [0.1],
        "nested": {"public": True},
    }
    assert case["recorded_evidence"] == {}
    assert "trial_id" not in case["candidate_context"]
    assert service.list_reviews() == []
    assert service.list_datasets() == []


def test_promotion_retry_after_restart_is_idempotent_and_rejects_operation_reuse(source):
    service, trial = source
    first = promote_quick_trial(service.trials.root, trial, operation_id="same", name="Named input")
    retried = promote_quick_trial(
        service.trials.root, trial, operation_id="same", name="Named input"
    )
    assert retried["reused"] is True
    assert first["case_revision_id"] == retried["case_revision_id"]
    assert len(service.list_cases()) == 1
    assert get_promotion(service.trials.root, "same")["trial_id"] == trial
    assert len(list_promotions(service.trials.root, trial_id=trial)) == 1
    with pytest.raises(ConflictError):
        promote_quick_trial(
            service.trials.root, trial, operation_id="same", name="Different request"
        )


def test_concurrent_duplicate_promotion_creates_one_case(source):
    service, trial = source
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(
                lambda _: promote_quick_trial(
                    service.trials.root, trial, operation_id="concurrent"
                ),
                range(12),
            )
        )
    assert len({result["case_revision_id"] for result in results}) == 1
    assert sum(not result["reused"] for result in results) == 1
    assert len(service.list_cases()) == 1
    assert service.trials.count() == 1


def test_failed_promotion_rolls_back_case_and_reference(source):
    service, trial = source
    with service.trials.connect() as db:
        db.execute("""CREATE TRIGGER reject_promotion BEFORE INSERT ON quick_promotions
            BEGIN SELECT RAISE(ABORT, 'injected promotion failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        promote_quick_trial(service.trials.root, trial, operation_id="failed")
    assert service.list_cases() == []
    assert list_promotions(service.trials.root) == []
    assert service.trials.count() == 1


def test_missing_or_corrupt_input_cannot_create_promotion(source):
    service, trial = source
    image = service.trials.get(trial)["task"]["image_asset"]["sha256"]
    service.trials.asset_path(image).write_bytes(b"corrupt image")
    with pytest.raises(ValueError, match="integrity"):
        promote_quick_trial(service.trials.root, trial, operation_id="corrupt")
    assert service.list_cases() == []
    absent = service.trials.begin(
        source="quick", task={"task": "Missing image"}, strategy={}, config={}
    )
    service.trials.finish(absent, status="error")
    with pytest.raises(ValueError, match="lacks"):
        promote_quick_trial(service.trials.root, absent, operation_id="absent")


def test_running_campaign_and_missing_trials_are_rejected(source):
    service, trial = source
    for origin, status in (("quick", "running"), ("campaign", "completed")):
        identity = service.trials.begin(
            source=origin, task=service.trials.get(trial)["task"], strategy={}, config={}
        )
        if status != "running":
            service.trials.finish(identity, status=status)
        with pytest.raises(ValueError, match="finished quick"):
            promote_quick_trial(service.trials.root, identity, operation_id=identity)
    with pytest.raises(KeyError):
        promote_quick_trial(service.trials.root, "missing", operation_id="missing")
    with pytest.raises(ValueError, match="operation_id"):
        promote_quick_trial(service.trials.root, trial, operation_id="")


def test_finished_error_can_supply_input_but_not_success_evidence(source):
    service, trial = source
    failed = service.trials.begin(
        source="quick", task=service.trials.get(trial)["task"], strategy={}, config={}
    )
    service.trials.finish(failed, status="error", error="Provider unavailable")
    promoted = promote_quick_trial(service.trials.root, failed, operation_id="retry-input")
    assert promoted["eligible_for_reliability"] is False
    assert service.trials.get(failed)["status"] == "error"
    assert service.trials.count() == 2


def test_existing_promotion_reference_survives_later_asset_loss(source):
    service, trial = source
    first = promote_quick_trial(service.trials.root, trial, operation_id="persisted")
    digest = service.trials.get(trial)["task"]["image_asset"]["sha256"]
    service.trials.asset_path(digest).unlink()
    repeated = promote_quick_trial(service.trials.root, trial, operation_id="persisted")
    assert repeated["case_revision_id"] == first["case_revision_id"]
    assert service.get_case_revision(first["case_revision_id"])["readiness"]["can_run"] is False
