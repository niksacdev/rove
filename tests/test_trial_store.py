"""Durability, isolation and evidence-integrity tests for the shared trial journal."""

import base64
import concurrent.futures
import hashlib
import json
import sqlite3

import pytest

from rove.trials.snapshots import canonical_json, sanitize, snapshot
from rove.trials.store import MAX_EVENT_BYTES, TrialStore


def begin(store, **kwargs):
    return store.begin(
        source="quick",
        task={"instruction": "Pick cup"},
        strategy={"id": "vision"},
        config={"model": "mock"},
        **kwargs,
    )


def test_started_trial_survives_reopen_and_recovery_is_explicit(tmp_path):
    store = TrialStore(tmp_path)
    trial = begin(store)
    reopened = TrialStore(tmp_path)
    assert reopened.get(trial)["status"] == "running"
    assert reopened.recover_interrupted() == 1
    record = reopened.get(trial)
    assert record["status"] == "interrupted"
    assert record["result"] is None
    assert record["finished_at"]
    assert reopened.recover_interrupted() == 0


def test_filtered_recovery_does_not_interrupt_campaign_worker(tmp_path):
    store = TrialStore(tmp_path)
    quick = begin(store)
    campaign = store.begin(source="campaign", task={}, strategy={}, config={})
    assert store.recover_interrupted(source="quick") == 1
    assert store.get(quick)["status"] == "interrupted"
    assert store.get(campaign)["status"] == "running"


def test_terminal_records_cannot_be_overwritten(tmp_path):
    store = TrialStore(tmp_path)
    trial = begin(store)
    result = {"outcome": "unknown", "usage": None}
    store.finish(trial, status="completed", result=result)
    first = store.get(trial)
    store.finish(trial, status="completed", result=result)
    assert store.get(trial) == first
    for status, changed in [("failed", result), ("completed", {"outcome": "pass"})]:
        with pytest.raises(ValueError, match="immutable"):
            store.finish(trial, status=status, result=changed)
    with pytest.raises(ValueError, match="immutable"):
        store.append_event(trial, {"type": "late_event"})
    with pytest.raises(ValueError, match="terminal"):
        store.finish(trial, status="running")


def test_concurrent_terminal_transition_has_one_winner(tmp_path):
    store = TrialStore(tmp_path)
    trial = begin(store)

    def finish(status):
        try:
            store.finish(trial, status=status)
            return True
        except ValueError:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(finish, ["completed", "cancelled"]))
    assert outcomes.count(True) == 1
    assert store.get(trial)["status"] in {"completed", "cancelled"}


def test_event_deduplication_is_atomic_and_producer_scoped(tmp_path):
    store = TrialStore(tmp_path)
    trial = begin(store)
    event = {"source": "sdk", "source_event_id": "usage1", "tokens": None}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        inserted = list(pool.map(lambda _: store.append_event(trial, event), range(20)))
    assert sum(inserted) == 1
    assert store.append_event(trial, {**event, "source": "customer"})
    with pytest.raises(ValueError, match="different content"):
        store.append_event(trial, {**event, "tokens": 42})
    assert store.get(trial)["event_count"] == 2
    assert store.events(trial)[0]["tokens"] is None
    store.finish(trial, status="completed")
    assert not store.append_event(trial, event)


def test_events_without_source_identity_are_not_guessed_duplicates(tmp_path):
    store = TrialStore(tmp_path)
    trial = begin(store)
    for _ in range(3):
        assert store.append_event(trial, {"type": "observation"})
    assert len(store.events(trial)) == 3
    assert len({event["event_id"] for event in store.events(trial)}) == 3


def test_events_survive_recovery_and_respect_pagination(tmp_path):
    store = TrialStore(tmp_path)
    trial = begin(store)
    for index in range(5):
        store.append_event(trial, {"source_event_id": str(index), "step": index})
    reopened = TrialStore(tmp_path)
    reopened.recover_interrupted()
    assert [e["step"] for e in reopened.events(trial, limit=2, offset=2)] == [2, 3]
    assert reopened.get(trial)["event_count"] == 5


def test_event_bounds_fail_before_changing_journal(tmp_path):
    store = TrialStore(tmp_path)
    trial = begin(store)
    with pytest.raises(ValueError, match="exceeds"):
        store.append_event(trial, {"text": "x" * MAX_EVENT_BYTES})
    with pytest.raises(ValueError):
        store.append_event(trial, {"measurement": float("nan")})
    assert store.get(trial)["event_count"] == 0
    with pytest.raises(KeyError):
        store.append_event("missing", {})


def test_snapshot_is_canonical_detached_and_relevant_changes_change_hash():
    config = {
        "model": "v1",
        "temperature": 0.2,
        "api_key": "very-sensitive",  # pragma: allowlist secret - synthetic fixture
    }
    original = snapshot({"instruction": "pick"}, {"id": "a"}, config)
    reordered = snapshot({"instruction": "pick"}, {"id": "a"}, dict(reversed(config.items())))
    assert original == reordered
    config["model"] = "v2"
    assert original["config"]["model"] == "v1"
    assert snapshot({"instruction": "pick"}, {"id": "a"}, config)["sha256"] != original["sha256"]
    config["model"] = "v1"
    config["api_key"] = "different-secret"  # pragma: allowlist secret - synthetic redaction fixture
    assert snapshot({"instruction": "pick"}, {"id": "a"}, config)["sha256"] == original["sha256"]
    assert "very-sensitive" not in canonical_json(original)


def test_secrets_inline_images_and_private_paths_are_not_stored(tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-secret-with-enough-length")
    monkeypatch.setenv("GH_TOKEN", "github-secret-test")
    image = base64.b64encode(b"pixel bytes").decode()
    store = TrialStore(tmp_path)
    trial = store.begin(
        source="quick",
        task={"image_base64": image, "path": "/Users/private/image.jpg"},
        strategy={},
        config={
            "password": "hunter-test",  # pragma: allowlist secret - synthetic redaction fixture
            "url": "https://user:pass@host/x?sig=hidden",  # pragma: allowlist secret - synthetic redaction fixture
            "message": "test-secret-with-enough-length github-secret-test",
        },
    )
    store.append_event(
        trial, {"authorization": "Bearer private-value", "message": "github-secret-test"}
    )
    store.finish(trial, status="error", error="Failed: test-secret-with-enough-length")
    saved = json.dumps(store.get(trial)) + json.dumps(store.events(trial))
    for secret in (
        image,
        "/Users/private",
        "hunter-test",
        "user:pass",
        "hidden",
        "test-secret-with-enough-length",
        "github-secret-test",
        "Bearer private-value",
    ):
        assert secret not in saved
    assert (
        store.get(trial)["task"]["image_base64"]["sha256"]
        == hashlib.sha256(b"pixel bytes").hexdigest()
    )
    assert store.get(trial)["snapshot_id"]


def test_snapshot_does_not_zero_missing_measurements_or_usage():
    assert sanitize({"tokens": None, "input_tokens": 5, "measurement": None}) == {
        "tokens": None,
        "input_tokens": 5,
        "measurement": None,
    }


def test_redaction_covers_camel_case_and_signed_cloud_urls():
    result = sanitize(
        {
            "clientSecret": "private-secret",  # pragma: allowlist secret - synthetic redaction fixture
            "endpoint": "https://host/image?X-Amz-Signature=private-signature&api-version=2026-01",
            "image": "data:image/png;base64,cGl4ZWxz",
        }
    )
    assert result["clientSecret"] == "[REDACTED]"
    assert "private-signature" not in result["endpoint"]
    assert "api-version=2026-01" in result["endpoint"]
    assert result["image"]["sha256"] == hashlib.sha256(b"pixels").hexdigest()


def test_listing_is_bounded_filtered_and_does_not_include_event_bodies(tmp_path):
    store = TrialStore(tmp_path)
    for _ in range(4):
        store.append_event(begin(store), {"detail": "large event body"})
    store.begin(source="campaign", task={}, strategy={}, config={}, campaign_id="campaign1", seed=4)
    page = store.list(limit=2, offset=1, source="quick")
    assert len(page) == 2
    assert all(row["source"] == "quick" and "events" not in row for row in page)
    assert store.count() == 5
    assert store.count("quick") == 4
    with pytest.raises(ValueError):
        store.list(limit=1001)
    with pytest.raises(ValueError):
        store.list(offset=-1)


def test_foreign_keys_and_schema_version_are_enforced(tmp_path):
    store = TrialStore(tmp_path)
    with store.connect() as db:
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        db.execute("PRAGMA user_version=900")
    with pytest.raises(ValueError, match="Unsupported"):
        TrialStore(tmp_path)


def test_concurrent_creation_retains_all_unique_trials(tmp_path):
    store = TrialStore(tmp_path)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: begin(store), range(30)))
    assert len(set(ids)) == store.count() == 30
    with pytest.raises(sqlite3.IntegrityError):
        begin(store, trial_id=ids[0])


def test_database_backup_is_consistent_and_reopenable(tmp_path):
    store = TrialStore(tmp_path / "source")
    trial = begin(store)
    store.append_event(trial, {"type": "stage_start"})
    store.finish(trial, status="completed", result={"outcome": "fail"})
    destination = tmp_path / "restored" / "trials.sqlite3"
    store.backup(destination)
    restored = TrialStore(destination.parent)
    assert restored.get(trial) == store.get(trial)
    assert restored.events(trial) == store.events(trial)
    with pytest.raises(ValueError):
        store.backup(store.path)


def test_legacy_import_is_idempotent_splits_strategies_and_keeps_unknowns(tmp_path):
    store = TrialStore(tmp_path / "records")
    path = tmp_path / "history.jsonl"
    rows = [
        {
            "eval_id": "old1",
            "task": "pick",
            "results": [
                {"strategy_id": "a", "outcome": "fail"},
                {"strategy_id": "b", "outcome": "pass"},
            ],
        },
        {"eval_id": "old2", "provenance": {"seed": 1}},
    ]
    original = "\n".join(json.dumps(row) for row in rows)
    path.write_text(original)
    assert store.import_jsonl(path) == 3
    assert store.import_jsonl(path) == 0
    assert path.read_text() == original
    assert store.count("legacy") == 3
    assert sum(row["snapshot"] is None for row in store.list()) == 2
    partial = next(row for row in store.list() if row["snapshot"])
    assert partial["snapshot"]["legacy_provenance"] == {"seed": 1}
    assert partial["result"] is None
    assert partial["snapshot_id"] is None


def test_malformed_legacy_import_rolls_back_every_line(tmp_path):
    store = TrialStore(tmp_path / "records")
    path = tmp_path / "history.jsonl"
    path.write_text('{"eval_id":"valid"}\n{"eval_id":')
    with pytest.raises(json.JSONDecodeError):
        store.import_jsonl(path)
    assert store.count() == 0


def test_legacy_projection_skips_existing_trials_but_preserves_missing_references(tmp_path):
    store = TrialStore(tmp_path / "records")
    existing = begin(store)
    path = tmp_path / "history.jsonl"
    rows = [
        {
            "eval_id": "projected",
            "trial_ids": {"a": existing},
            "results": [{"strategy_id": "a", "outcome": "pass"}],
        },
        {
            "eval_id": "missing",
            "trial_ids": {"b": "unavailable"},
            "results": [{"strategy_id": "b", "outcome": "fail"}],
        },
        {
            "eval_id": "partially-saved",
            "trial_ids": {"a": existing, "c": "missing"},
            "results": [{"strategy_id": "a"}, {"strategy_id": "c"}],
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows))
    assert store.import_jsonl(path) == 2
    assert store.count() == 3
    assert {row["strategy"]["id"] for row in store.list(source="legacy")} == {"b", "c"}
    assert store.import_jsonl(path) == 0


def test_campaign_iteration_visits_all_pages_and_only_selected_campaign(tmp_path):
    store = TrialStore(tmp_path)
    for _ in range(205):
        store.begin(source="campaign", campaign_id="selected", task={}, strategy={}, config={})
    store.begin(source="campaign", campaign_id="other", task={}, strategy={}, config={})
    begin(store)
    ids = []
    for trial in store.for_campaign("selected"):
        ids.append(trial["id"])
        store.finish(trial["id"], status="interrupted")
    assert len(ids) == len(set(ids)) == 205
    assert len(list(store.for_campaign("other"))) == 1
    assert list(store.for_campaign("missing")) == []


def test_asset_deduplication_and_private_reference_resolution(tmp_path):
    store = TrialStore(tmp_path)
    data = b"\x89PNG\r\n\x1a\nexample"
    reference = store.save_asset(data, "image/png")
    assert reference == store.save_asset(data, "image/png")
    assert reference["size_bytes"] == len(data)
    assert reference["media_type"] == "image/png"
    assert store.asset_path(reference["sha256"]).read_bytes() == data
    assert store.asset_metadata(reference["sha256"]) == reference
    assert len(list((tmp_path / "assets").iterdir())) == 1
    assert store.asset_path(reference["sha256"]).stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        store.asset_path("../../etc/passwd")
    with pytest.raises(KeyError):
        store.asset_path("a" * 64)


def test_asset_rejects_symlink_escape_and_partial_files(tmp_path):
    store = TrialStore(tmp_path / "records")
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.root / "assets").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        store.save_asset(b"test", "image/png")
    assert not list(outside.iterdir())
