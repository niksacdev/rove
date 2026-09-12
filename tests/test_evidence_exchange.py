"""Evidence, trace, and exchange invariants across restart and malformed input."""

import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rove.api.trials import create_trial_router
from rove.trials.evidence import validate_reference
from rove.trials.exchange import export_bundle, import_bundle
from rove.trials.store import TrialStore
from rove.trials.traces import trace_lanes


def setup_store(tmp_path):
    store = TrialStore(tmp_path)
    trial = store.begin(
        source="quick", task={"instruction": "Reach target"}, strategy={"id": "agent"}, config={}
    )
    return store, trial


def test_recording_event_archives_bytes_and_exact_selection_survives_restart(tmp_path):
    store, trial = setup_store(tmp_path)
    payload = {
        "records": [{"timestamp_s": i, "position_m": i / 10} for i in range(4)],
        "source_clock_id": "sim",
        "duration_seconds": 4,
    }
    event = {
        "event_type": "rollout.finished",
        "source": "sim",
        "source_event_id": "rollout",
        "evidence_payload": {
            "id": "episode",
            "kind": "trajectory",
            "content": payload,
            "clock_id": "sim",
            "frame": "world",
            "units": {"position": "m"},
        },
    }
    assert store.append_event(trial, event)
    assert not store.append_event(trial, event)
    archived = store.events(trial)[0]
    assert "evidence_payload" not in archived
    ref = archived["evidence_refs"][0]
    store.attach_evidence(
        trial, {**ref, "id": "completion", "selector": {"unit": "sample", "start": 1, "end": 3}}
    )
    store.attach_evidence(
        trial, {**ref, "id": "time", "selector": {"unit": "second", "start": 1, "end": 3}}
    )
    store.finish(
        trial, status="completed", result={"outcome": "pass", "evidence_quality": "synthetic"}
    )
    reopened = TrialStore(tmp_path)
    assert len(reopened.get(trial)["evidence_refs"]) == 3
    app = FastAPI()
    app.include_router(create_trial_router(lambda: reopened))
    with TestClient(app) as client:
        for identity in ("completion", "time"):
            response = client.get(f"/api/trials/{trial}/evidence/{identity}")
            assert response.status_code == 200
            assert response.json()["records"] == payload["records"][1:3]
    assert json.loads(reopened.asset_path(ref["asset_sha256"]).read_bytes()) == payload
    with pytest.raises(ValueError, match="immutable"):
        reopened.attach_evidence(trial, {**ref, "id": "late"})


def test_invalid_range_and_tampered_asset_stay_unavailable(tmp_path):
    store, trial = setup_store(tmp_path)
    asset = store.save_asset(b'{"records":[1,2]}', "application/json")
    ref = {"id": "steps", "asset_sha256": asset["sha256"], "kind": "trajectory"}
    for selector in (
        {"unit": "sample", "start": 0, "end": 3},
        {"unit": "sample", "start": 0.5, "end": 1},
        {"unit": "second", "start": 0, "end": 1},
    ):
        with pytest.raises(ValueError):
            validate_reference(store, {**ref, "selector": selector})
    store.attach_evidence(trial, ref)
    store.asset_path(asset["sha256"]).write_bytes(b"tampered")
    assert store.get(trial)["evidence_refs"][0]["availability"] == "unavailable"


def test_conflicting_evidence_rolls_back_event(tmp_path):
    store, trial = setup_store(tmp_path)
    a = store.save_asset(b"a", "application/octet-stream")
    b = store.save_asset(b"b", "application/octet-stream")
    store.attach_evidence(trial, {"id": "same", "asset_sha256": a["sha256"]})
    with pytest.raises(ValueError, match="different content"):
        store.append_event(
            trial,
            {"event_type": "bad", "evidence_refs": [{"id": "same", "asset_sha256": b["sha256"]}]},
        )
    assert store.events(trial) == []


def test_traces_do_not_align_unrelated_clocks_or_invent_missing_duration():
    def event(clock, time, kind, span, **extra):
        return {
            "source_clock_id": clock,
            "source_timestamp": time,
            "time_unit": "s",
            "event_type": kind,
            "trace_id": "trace",
            "span_id": span,
            "stage": "act",
            **extra,
        }

    trace = trace_lanes(
        [
            event("a", 100, "stage.started", "one"),
            event("b", 5, "stage.started", "two"),
            event("a", 102, "stage.ended", "one"),
            {"event_type": "unknown"},
        ]
    )
    lanes = trace["lanes"]
    assert len(lanes) == 3
    assert lanes[0]["items"][0]["duration_seconds"] == 2
    assert lanes[1]["items"][0]["offset_seconds"] == 0
    assert lanes[1]["items"][0]["duration_seconds"] is None
    assert lanes[2]["items"][0]["timing"] == "unavailable"


def test_exchange_round_trip_retains_events_assets_and_trial_identity(tmp_path):
    root = tmp_path / "source"
    store, trial = setup_store(root)
    store.append_event(
        trial,
        {
            "event_type": "output",
            "evidence_payload": {"id": "output", "content": {"result": "uncertain"}},
        },
    )
    store.finish(trial, status="completed", result={"outcome": "unknown"})
    original = store.get(trial)
    path = tmp_path / "export.zip"
    manifest = export_bundle(root, path)
    assert manifest["version"] == 1
    destination = tmp_path / "restored"
    import_bundle(path, destination)
    restored = TrialStore(destination)
    assert restored.get(trial) == original
    assert restored.events(trial) == store.events(trial)
    with pytest.raises(ValueError, match="new destination"):
        import_bundle(path, destination)
    with pytest.raises(ValueError, match="already exists"):
        export_bundle(root, path)


def test_exchange_rejects_active_trials_and_corrupt_or_unexpected_entries(tmp_path):
    root = tmp_path / "source"
    store, trial = setup_store(root)
    path = tmp_path / "export.zip"
    with pytest.raises(ValueError, match="active trials"):
        export_bundle(root, path)
    store.finish(trial, status="completed")
    export_bundle(root, path)
    malicious = tmp_path / "bad.zip"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(malicious, "w") as out:
        for name in src.namelist():
            out.writestr(name, src.read(name))
        out.writestr("../escape", b"bad")
    destination = tmp_path / "restored"
    with pytest.raises(ValueError, match="Unexpected"):
        import_bundle(malicious, destination)
    assert not destination.exists()
    assert not (tmp_path / "escape").exists()
    corrupt = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(corrupt, "w") as out:
        for name in src.namelist():
            out.writestr(name, b"{}\n" if name == "tables/trials/trials.jsonl" else src.read(name))
    with pytest.raises(ValueError, match="hash mismatch"):
        import_bundle(corrupt, destination)
    assert not destination.exists()


def test_finished_stage_output_archived_without_rewriting_original_result(tmp_path):
    store, trial = setup_store(tmp_path)
    result = {"stages": [{"stage": "plan", "output": {"actions": ["move", "grasp"]}}]}
    store.finish(trial, status="completed", result=result)
    record = store.get(trial)
    assert record["result"] == result
    assert record["evidence_refs"][0]["id"] == "rove.stage.0"
    digest = record["evidence_refs"][0]["asset_sha256"]
    assert json.loads(store.asset_path(digest).read_bytes()) == result["stages"][0]["output"]


def test_exchange_preserves_frozen_private_reviews_and_case_revision_cycle(tmp_path):
    import io

    from PIL import Image

    from rove.datasets.service import DatasetService

    service = DatasetService(tmp_path / "source")
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
    image = service.trials.save_asset(buffer.getvalue(), "image/png")
    case = service.import_case({"name": "Object", "task": "Name the object"}, image["sha256"])
    contract = service.create_contract(
        {
            "name": "Scene",
            "scope": "scene_understanding",
            "evidence_mode": "candidate_output",
            "criteria": [{"id": "target", "description": "Correct target"}],
        }
    )
    review = service.create_review(
        {
            "target_type": "case_annotation",
            "case_revision_id": case["id"],
            "contract_id": contract["id"],
            "reviewer": "SME",
            "decision": "accepted",
            "status": "final",
            "rationale": "Reference reviewed",
            "annotations": {"target": "red block"},
        }
    )
    frozen = service.freeze(
        {
            "name": "Frozen",
            "contract_id": contract["id"],
            "members": [{"case_revision_id": case["id"], "review_ids": [review["id"]]}],
        }
    )
    revised = service.revise_case(
        case["case_id"],
        {"name": "Object revised", "task": "Describe the object"},
        expected_head_revision_id=case["id"],
    )
    archive = tmp_path / "exchange.zip"
    export_bundle(service.trials.root, archive)
    import_bundle(archive, tmp_path / "restored")
    restored = DatasetService(tmp_path / "restored")
    assert restored.get_dataset(frozen["id"]) == service.get_dataset(frozen["id"])
    assert restored.get_case(case["case_id"])["id"] == revised["id"]
    assert restored.get_case_revision(case["id"]) == service.get_case_revision(case["id"])
    assert restored.get_review(review["id"]) == service.get_review(review["id"])


def test_trace_aliases_coalesce_without_orphan_end_or_duplicate_duration():
    base = {
        "source_clock_id": "host",
        "time_unit": "s",
        "trace_id": "trace",
        "span_id": "tool",
        "stage": "plan",
    }
    events = [
        {**base, "event_type": kind, "source_timestamp": time}
        for kind, time in [
            ("rove.tool.observe.started", 10),
            ("rove.tool.started", 10.01),
            ("rove.tool.completed", 11.9),
            ("rove.tool.observe.ended", 12),
        ]
    ]
    items = trace_lanes(events)["lanes"][0]["items"]
    assert len(items) == 1
    assert items[0]["name"] == "rove.tool.observe.started"
    assert items[0]["duration_seconds"] == 2


def test_trace_explicit_end_ns_and_unknown_clock_are_not_misrepresented():
    result = trace_lanes(
        [
            {
                "event_type": "stage.started",
                "source_clock_id": "c",
                "span_id": "s",
                "start_time_unix_nano": "1000000000",
            },
            {
                "event_type": "stage.ended",
                "source_clock_id": "c",
                "span_id": "s",
                "end_time_unix_nano": "3000000000",
            },
            {"event_type": "a", "source_timestamp": 100, "time_unit": "s"},
            {"event_type": "b", "source_timestamp": 900, "time_unit": "s"},
        ]
    )
    assert result["lanes"][0]["items"][0]["duration_seconds"] == 2
    assert all(item["offset_seconds"] is None for item in result["lanes"][1]["items"])


def test_atomic_import_publication_never_overwrites_existing_empty_directory(tmp_path):
    from rove.trials.exchange import _publish_directory

    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "data").write_text("preserved")
    with pytest.raises(OSError):
        _publish_directory(source, destination)
    assert source.is_dir() and destination.is_dir()
    assert not (destination / "data").exists()


def test_exchange_rejects_cross_database_orphan_before_publication(tmp_path):
    store = TrialStore(tmp_path / "source")
    trial = store.begin(source="campaign", task={}, strategy={}, config={}, campaign_id="missing")
    store.finish(trial, status="completed")
    with pytest.raises(ValueError, match="missing campaign lineage"):
        export_bundle(store.root, tmp_path / "bad.zip")
    assert not (tmp_path / "bad.zip").exists()
