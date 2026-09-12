"""Customer-case provenance and SME curation must survive edits, crashes and disagreement."""

import concurrent.futures
import io
import json
import shutil
import sqlite3
import threading

import pytest
from PIL import Image

from rove.datasets.service import ConflictError, DatasetService
from rove.trials.store import TrialStore


@pytest.fixture
def service(tmp_path):
    return DatasetService(tmp_path)


@pytest.fixture
def image(service):
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
    return service.trials.save_asset(buffer.getvalue(), "image/png")


@pytest.fixture
def case(service, image):
    return service.import_case(
        {"name": "Red block", "task": "Place the red block in the bin"}, image["sha256"]
    )


@pytest.fixture
def contract(service):
    return service.create_contract(
        {
            "name": "Plan quality",
            "scope": "plan_quality",
            "evidence_mode": "candidate_output",
            "criteria": [{"id": "target", "description": "Correctly identifies the target"}],
        }
    )


def review_payload(case, contract, **overrides):
    return {
        "target_type": "case_validity",
        "case_revision_id": case["case_revision_id"],
        "contract_id": contract["id"],
        "reviewer": "Local SME",
        "decision": "accepted",
        "status": "final",
        "rationale": "The image and task identify the intended object",
        **overrides,
    }


def freeze_payload(case, contract, reviews=()):
    return {
        "name": "Customer dataset v1",
        "contract_id": contract["id"],
        "members": [
            {
                "case_revision_id": case["case_revision_id"],
                "review_ids": [r["id"] for r in reviews],
            },
        ],
    }


def approved_reviews(service, case, contract):
    validity = service.create_review(review_payload(case, contract))
    annotation = service.create_review(
        review_payload(
            case,
            contract,
            target_type="case_annotation",
            annotations={"target_object": "red block"},
        )
    )
    return validity, annotation


def completed_trial(service, case):
    identity = service.trials.begin(
        source="campaign", task={"case_revision_id": case["id"]}, strategy={"id": "a"}, config={}
    )
    service.trials.append_event(identity, {"event_type": "output"})
    service.trials.finish(identity, status="completed", result={"plan": "Pick the red block"})
    return identity


def test_case_versions_preserve_old_inputs_and_reuse_asset(service, case, image):
    revised = service.revise_case(
        case["case_id"],
        {"name": "Revision 2", "task": "Move the blue block"},
        expected_head_revision_id=case["id"],
    )
    assert revised["revision"] == 2
    assert revised["image_asset"] == image
    assert revised["sha256"] != case["sha256"]
    assert service.get_case_revision(case["id"])["task"] == case["task"]
    assert service.get_case(case["case_id"])["id"] == revised["id"]
    assert len(service.list_cases()) == 1
    with pytest.raises(ConflictError):
        service.revise_case(
            case["case_id"],
            {"name": "stale", "task": "stale"},
            expected_head_revision_id=case["id"],
        )


def test_competing_case_edits_cannot_lose_revision(service, case):
    def save(name):
        try:
            return service.revise_case(
                case["case_id"], {"name": name, "task": name}, expected_head_revision_id=case["id"]
            )
        except ConflictError:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["first edit", "second edit"]))
    assert sum(result is not None for result in results) == 1
    assert service.get_case(case["case_id"])["revision"] == 2


def test_image_integrity_decode_and_reference_restrictions(service, image):
    payload = {"name": "Case", "task": "Pick"}
    with pytest.raises(ValueError):
        service.import_case(payload, "../../etc/passwd")
    corrupt = service.trials.save_asset(b"\x89PNG\r\n\x1a\nnot actually a PNG", "image/png")
    with pytest.raises(ValueError, match="decoded"):
        service.import_case(payload, corrupt["sha256"])
    html = service.trials.save_asset(b"<script>alert(1)</script>", "image/png")
    with pytest.raises(ValueError, match="PNG or JPEG"):
        service.import_case(payload, html["sha256"])
    service.trials.asset_path(image["sha256"]).write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="integrity"):
        service.import_case(payload, image["sha256"])
    assert service.list_cases() == []


def test_private_evidence_stays_separate_from_candidate_inputs(service, image):
    imported = service.import_case(
        {
            "name": "Recorded episode",
            "task": "Place block",
            "candidate_context": {"constraints": ["Avoid blue block"]},
            "recorded_evidence": {
                "episode": {"outcome": "pass", "quality": "observed"},
                "producer": "old-system",
            },
        },
        image["sha256"],
    )
    assert "episode" not in imported["candidate_context"]
    assert imported["recorded_evidence"]["producer"] == "old-system"
    with pytest.raises(ValueError, match="Private"):
        service.import_case(
            {"name": "Leak", "task": "Pick", "candidate_context": {"ground_truth": "answer"}},
            image["sha256"],
        )


def test_contracts_are_immutable_content_identified_and_validate_binding(service, contract):
    payload = {
        key: value for key, value in contract.items() if key not in {"id", "sha256", "created_at"}
    }
    assert service.create_contract(payload)["id"] == contract["id"]
    changed = service.create_contract({**payload, "name": "New rubric"})
    assert changed["id"] != contract["id"]
    with pytest.raises(ValueError, match="endpoint"):
        service.create_contract(
            {
                **payload,
                "criteria": [{"id": "a", "description": "A", "assessment": "configured_verifier"}],
            }
        )
    with pytest.raises(ValueError, match="unique"):
        service.create_contract({**payload, "criteria": payload["criteria"] * 2})
    assert len(service.list_contracts()) == 2


def test_review_draft_survives_restart_and_rejects_stale_updates(service, case, contract):
    review = service.create_review(
        review_payload(case, contract, status="draft", decision="unknown", rationale="")
    )
    reopened = DatasetService(service.trials.root)
    saved = reopened.update_review(
        review["id"], {"rationale": "Image looks clear"}, expected_revision=1
    )
    assert saved["revision"] == 2
    with pytest.raises(ConflictError):
        service.update_review(review["id"], {"decision": "rejected"}, expected_revision=1)
    final = reopened.update_review(
        review["id"], {"status": "final", "decision": "accepted"}, expected_revision=2
    )
    assert final["revision"] == 3
    with pytest.raises(ConflictError):
        service.update_review(review["id"], {"decision": "rejected"}, expected_revision=3)
    assert service.get_review(review["id"])["decision"] == "accepted"


def test_review_attribution_and_target_cannot_change_mid_draft(service, case, contract):
    review = service.create_review(review_payload(case, contract, status="draft"))
    with pytest.raises(ValueError, match="attribution"):
        service.update_review(review["id"], {"reviewer": "Someone else"}, expected_revision=1)
    with pytest.raises(ValueError, match="rationale"):
        service.create_review(review_payload(case, contract, rationale=""))


def test_output_rating_binds_trial_case_rubric_and_exact_output(service, case, contract, image):
    trial = completed_trial(service, case)
    payload = review_payload(
        case, contract, target_type="trial_output", trial_id=trial, criteria={"target": "accepted"}
    )
    review = service.create_review(payload)
    assert review["output_sha256"]
    assert review["annotations"] == {}
    assert service.trials.count() == 1
    another = service.import_case({"name": "Other", "task": "Other task"}, image["sha256"])
    with pytest.raises(ValueError, match="does not belong"):
        service.create_review({**payload, "case_revision_id": another["id"]})
    with pytest.raises(ValueError, match=r"required.*criteria"):
        service.create_review({**payload, "criteria": {}})
    with pytest.raises(ValueError, match="annotations"):
        service.create_review({**payload, "annotations": {"answer": "baseline output"}})
    assert len(service.list_reviews(trial_id=trial)) == 1


def test_output_review_requires_finished_retained_output(service, case, contract):
    trial = service.trials.begin(
        source="quick", task={"case_revision_id": case["id"]}, strategy={}, config={}
    )
    payload = review_payload(
        case, contract, target_type="trial_output", trial_id=trial, decision="unknown"
    )
    with pytest.raises(ValueError, match="finished"):
        service.create_review(payload)
    service.trials.finish(trial, status="error")
    with pytest.raises(ValueError, match="retained output"):
        service.create_review(payload)


def test_review_evidence_refs_cannot_point_to_arbitrary_paths_or_other_trials(
    service, case, contract, image
):
    trial = completed_trial(service, case)
    event = service.trials.events(trial)[0]
    payload = review_payload(
        case, contract, target_type="trial_output", trial_id=trial, criteria={"target": "accepted"}
    )
    saved = service.create_review(
        {**payload, "evidence_refs": [f"asset:{image['sha256']}", f"event:{event['event_id']}"]}
    )
    assert len(saved["evidence_refs"]) == 2
    for reference in ("/etc/passwd", "https://remote/image", "trial:wrong", "event:missing"):
        with pytest.raises(ValueError, match="Evidence references"):
            service.create_review({**payload, "evidence_refs": [reference]})


def test_dataset_freezes_exact_membership_reviews_and_does_not_copy_output_labels(
    service, case, contract
):
    reviews = approved_reviews(service, case, contract)
    payload = freeze_payload(case, contract, reviews)
    preview = service.preview_freeze(payload)
    assert preview["readiness"] == "reviewed"
    frozen = service.freeze(payload, expected_preview_hash=preview["preview_hash"])
    before = json.dumps(frozen, sort_keys=True)
    service.revise_case(
        case["case_id"],
        {"name": "Changed", "task": "Changed task"},
        expected_head_revision_id=case["id"],
    )
    service.create_review(review_payload(case, contract, reviewer="Other SME", decision="rejected"))
    assert json.dumps(service.get_dataset(frozen["id"]), sort_keys=True) == before
    assert service.get_dataset(frozen["id"])["members"][0]["case_revision_id"] == case["id"]
    assert service.trials.count() == 0


def test_baseline_output_acceptance_does_not_make_dataset_fully_reviewed(service, case, contract):
    trial = completed_trial(service, case)
    output = service.create_review(
        review_payload(
            case,
            contract,
            target_type="trial_output",
            trial_id=trial,
            criteria={"target": "accepted"},
        )
    )
    frozen = service.freeze(freeze_payload(case, contract, [output]))
    assert frozen["readiness"] == "incomplete"
    assert frozen["coverage"]["reviewed"] == 0
    assert service.get_case_revision(case["id"])["candidate_context"] == {}


def test_new_disagreement_invalidates_preview_and_is_never_hidden_by_selection(
    service, case, contract
):
    reviews = approved_reviews(service, case, contract)
    payload = freeze_payload(case, contract, reviews)
    original = service.preview_freeze(payload)
    service.create_review(
        review_payload(case, contract, reviewer="Second SME", decision="rejected")
    )
    changed = service.preview_freeze(payload)
    assert changed["readiness"] == "incomplete"
    assert changed["preview_hash"] != original["preview_hash"]
    assert any(issue["code"] == "case_validity_disagreement" for issue in changed["issues"])
    with pytest.raises(ConflictError):
        service.freeze(payload, expected_preview_hash=original["preview_hash"])
    assert service.list_datasets() == []


def test_conflicting_accepted_annotations_count_as_disagreement(service, case, contract):
    reviews = approved_reviews(service, case, contract)
    service.create_review(
        review_payload(
            case,
            contract,
            reviewer="Second SME",
            target_type="case_annotation",
            annotations={"target_object": "blue block"},
        )
    )
    preview = service.preview_freeze(freeze_payload(case, contract, reviews))
    assert preview["readiness"] == "incomplete"
    assert any(issue["code"] == "case_annotation_disagreement" for issue in preview["issues"])


def test_final_review_correction_preserves_old_review_and_resolves_active_disagreement(
    service, case, contract
):
    reviews = approved_reviews(service, case, contract)
    rejected = service.create_review(
        review_payload(case, contract, reviewer="Second SME", decision="rejected")
    )
    correction = service.create_review(
        review_payload(case, contract, reviewer="Second SME", supersedes_id=rejected["id"])
    )
    assert service.get_review(rejected["id"])["decision"] == "rejected"
    preview = service.preview_freeze(freeze_payload(case, contract, [*reviews, correction]))
    assert preview["readiness"] == "reviewed"
    assert len(service.list_reviews(case_revision_id=case["id"])) == 4


def test_freeze_rejects_drafts_wrong_case_reviews_and_duplicate_case_versions(
    service, case, contract, image
):
    draft = service.create_review(review_payload(case, contract, status="draft"))
    with pytest.raises(ValueError, match="finalized"):
        service.freeze(freeze_payload(case, contract, [draft]))
    other = service.import_case({"name": "Other", "task": "Other task"}, image["sha256"])
    review = service.create_review(review_payload(other, contract))
    with pytest.raises(ValueError, match="does not match"):
        service.freeze(freeze_payload(case, contract, [review]))
    revised = service.revise_case(
        case["case_id"],
        {"name": "Changed", "task": "Changed"},
        expected_head_revision_id=case["id"],
    )
    payload = freeze_payload(case, contract)
    payload["members"].append({"case_revision_id": revised["id"]})
    with pytest.raises(ValueError, match="multiple revisions"):
        service.freeze(payload)
    assert service.list_datasets() == []


def test_exclusions_require_reason_and_preserve_failed_valid_cases(service, case, contract, image):
    other = service.import_case({"name": "Blurred", "task": "Unknown"}, image["sha256"])
    payload = freeze_payload(case, contract, approved_reviews(service, case, contract))
    payload["members"].append({"case_revision_id": other["id"], "disposition": "excluded"})
    with pytest.raises(ValueError, match="reason"):
        service.freeze(payload)
    payload["members"][-1]["reason"] = "Input not assessable"
    result = service.freeze(payload)
    assert result["coverage"]["excluded"] == 1
    assert result["readiness"] == "reviewed"
    assert len(result["members"]) == 2


def test_freeze_failure_rolls_back_all_rows(service, case, contract):
    with service.trials.connect() as db:
        db.execute("""CREATE TRIGGER reject_members BEFORE INSERT ON dataset_members
            BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        service.freeze(freeze_payload(case, contract))
    assert service.list_datasets() == []


def test_versioned_dataset_parent_and_members_survive_restart(service, case, contract):
    parent = service.freeze(freeze_payload(case, contract))
    child = service.freeze(
        {**freeze_payload(case, contract), "parent_id": parent["id"], "name": "v2"}
    )
    reopened = DatasetService(service.trials.root)
    assert reopened.get_dataset(child["id"])["parent_id"] == parent["id"]
    assert len(reopened.list_datasets()) == 2
    with pytest.raises(KeyError):
        reopened.freeze({**freeze_payload(case, contract), "parent_id": "missing"})


def _downgrade_empty_dataset_schema(root):
    with sqlite3.connect(root / "trials.sqlite3") as db:
        db.execute("DROP INDEX trial_case_revision")
        for table in (
            "quick_promotions",
            "dataset_member_reviews",
            "dataset_members",
            "dataset_revisions",
            "reviews",
            "case_revisions",
            "cases",
            "success_contracts",
        ):
            db.execute(f"DROP TABLE {table}")
        db.execute("PRAGMA user_version=1")


def test_v1_database_migrates_without_changing_existing_trial_evidence(tmp_path):
    store = TrialStore(tmp_path)
    trial = store.begin(source="quick", task={"task": "legacy"}, strategy={}, config={})
    store.append_event(trial, {"event_type": "stage"})
    store.finish(trial, status="completed", result={"outcome": "unknown"})
    original = store.get(trial)
    _downgrade_empty_dataset_schema(tmp_path)
    service = DatasetService(tmp_path)
    assert service.trials.get(trial) == original
    assert len(service.trials.events(trial)) == 1
    with service.trials.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


def test_failed_migration_rolls_back_schema_and_version(tmp_path, monkeypatch):
    TrialStore(tmp_path)
    _downgrade_empty_dataset_schema(tmp_path)

    def fail(db):
        db.execute("CREATE TABLE incomplete_migration (id TEXT)")
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr("rove.datasets.schema.migrate_v2", fail)
    with pytest.raises(RuntimeError, match="migration failure"):
        DatasetService(tmp_path)
    with sqlite3.connect(tmp_path / "trials.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert (
            db.execute("SELECT 1 FROM sqlite_master WHERE name='incomplete_migration'").fetchone()
            is None
        )


def test_document_bounds_and_unknown_values_do_not_create_rows(service, image, contract):
    with pytest.raises(ValueError, match="256 KiB"):
        service.import_case(
            {"name": "Too big", "task": "Task", "conditions": {"text": "x" * 300000}},
            image["sha256"],
        )
    with pytest.raises(ValueError):
        service.import_case(
            {"name": "Invalid", "task": "Task", "conditions": {"time": float("nan")}},
            image["sha256"],
        )
    case = service.import_case(
        {"name": "Missing", "task": "Task", "conditions": {"episode_time": None}}, image["sha256"]
    )
    assert case["conditions"]["episode_time"] is None
    with pytest.raises(ValueError):
        service.list_cases(limit=1001)


def test_case_trial_filter_preserves_pagination_and_source_scope(service, case):
    for source in ("quick", "campaign", "campaign"):
        service.trials.begin(
            source=source, task={"case_revision_id": case["id"]}, strategy={}, config={}
        )
    service.trials.begin(source="quick", task={}, strategy={}, config={})
    assert service.trials.count(case_revision_id=case["id"]) == 3
    assert service.trials.count(source="campaign", case_revision_id=case["id"]) == 2
    assert len(service.trials.list(case_revision_id=case["id"], limit=1, offset=1)) == 1
    assert len(service.trials.list(case_revision_id=case["id"], source="campaign")) == 2
    assert service.trials.list(case_revision_id="missing") == []


def test_missing_asset_keeps_history_visible_and_marks_case_unavailable(service, case):
    service.trials.asset_path(case["image_asset"]["sha256"]).unlink()
    listed = service.list_cases()[0]
    assert listed["id"] == case["id"]
    assert listed["readiness"]["can_run"] is False
    assert listed["readiness"]["issues"]


def test_competing_final_review_successors_conflict_without_overwriting(service, case, contract):
    original = service.create_review(review_payload(case, contract, decision="rejected"))
    successor = service.create_review(review_payload(case, contract, supersedes_id=original["id"]))
    with pytest.raises(ConflictError, match="successor"):
        service.create_review(
            review_payload(case, contract, supersedes_id=original["id"], decision="unknown")
        )
    assert service.get_review(original["id"])["decision"] == "rejected"
    assert service.get_review(successor["id"])["decision"] == "accepted"


def test_v1_backup_restores_assets_and_migrates_without_losing_history(tmp_path):
    original_root, restored_root = tmp_path / "original", tmp_path / "restored"
    original = TrialStore(original_root)
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
    asset = original.save_asset(buffer.getvalue(), "image/png")
    trial = original.begin(
        source="quick",
        task={"task": "Pick", "image_asset": asset},
        strategy={"id": "before-migration"},
        config={},
    )
    original.append_event(trial, {"event_type": "recorded-before-upgrade"})
    original.finish(trial, status="completed", result={"outcome": "unknown"})
    before, events = original.get(trial), original.events(trial)
    _downgrade_empty_dataset_schema(original_root)
    original.backup(restored_root / "trials.sqlite3")
    shutil.copytree(original_root / "assets", restored_root / "assets")
    restored = DatasetService(restored_root)
    assert restored.trials.get(trial) == before
    assert restored.trials.events(trial) == events
    case = restored.import_case({"name": "Restored input", "task": "Pick"}, asset["sha256"])
    assert restored.validate_case_image(case["id"]) == asset
    with restored.trials.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_review_finalization_serializes_after_atomic_freeze(service, case, contract, monkeypatch):
    payload = freeze_payload(case, contract, approved_reviews(service, case, contract))
    preview = service.preview_freeze(payload)
    freeze_holds_transaction = threading.Event()
    finish_freeze = threading.Event()
    reviewer_started = threading.Event()
    original_preview = service._preview

    def controlled_preview(db, selected):
        result = original_preview(db, selected)
        freeze_holds_transaction.set()
        assert finish_freeze.wait(timeout=5), "Test did not release the freeze transaction"
        return result

    monkeypatch.setattr(service, "_preview", controlled_preview)

    def add_disagreement():
        reviewer_started.set()
        return service.create_review(
            review_payload(case, contract, reviewer="Concurrent SME", decision="rejected")
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        freezing = pool.submit(
            service.freeze, payload, expected_preview_hash=preview["preview_hash"]
        )
        assert freeze_holds_transaction.wait(timeout=5)
        reviewing = pool.submit(add_disagreement)
        assert reviewer_started.wait(timeout=5)
        finish_freeze.set()
        frozen, review = freezing.result(timeout=5), reviewing.result(timeout=5)
    assert frozen["readiness"] == "reviewed"
    assert service.get_dataset(frozen["id"]) == frozen
    assert review["id"] not in frozen["members"][0]["review_ids"]
    monkeypatch.setattr(service, "_preview", original_preview)
    assert service.preview_freeze(payload)["readiness"] == "incomplete"
