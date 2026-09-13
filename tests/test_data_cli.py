"""Data CLI parity without a web server: revisions and freeze guards remain authoritative."""

import io
import json

import pytest
import yaml
from PIL import Image

from rove.datasets.cli import main
from rove.datasets.service import DatasetService


@pytest.fixture
def cli(tmp_path, capsys):
    store = tmp_path / "store"
    counter = 0

    def invoke(*args, payload=None):
        nonlocal counter
        values = [str(arg) for arg in args]
        if payload is not None:
            counter += 1
            file = tmp_path / f"input-{counter}.yaml"
            file.write_text(yaml.safe_dump(payload))
            values += ["--file", str(file)]
        main([*values, "--store", str(store)])
        return json.loads(capsys.readouterr().out)

    image = tmp_path / "observation.png"
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
    image.write_bytes(buffer.getvalue())
    return invoke, store, image


def contract_payload():
    return {
        "name": "Safe placement",
        "scope": "plan_quality",
        "evidence_mode": "candidate_output",
        "criteria": [
            {
                "id": "clearance",
                "description": "Keep a clear approach",
                "assessment": "human_review",
            }
        ],
    }


def test_case_create_search_show_revision_cas_and_retry(cli, capsys):
    run, store, image = cli
    payload = {"name": "Red block", "task": "Put the block in the bin"}
    case = run("case", "create", "--image", image, "--operation-id", "first-case", payload=payload)
    retry = run("case", "create", "--image", image, "--operation-id", "first-case", payload=payload)
    assert retry["id"] == case["id"]
    assert run("case", "list", "--query", "block")["total"] == 1
    revision = run(
        "case",
        "revise",
        case["case_id"],
        "--expected-head",
        case["id"],
        payload={**payload, "task": "Put the block on the tray"},
    )
    assert revision["revision"] == 2
    assert run("case", "show", case["case_id"])["id"] == revision["id"]
    assert run("case", "show", case["id"])["task"] == payload["task"]
    with pytest.raises(SystemExit) as error:
        run("case", "revise", case["case_id"], "--expected-head", case["id"], payload=payload)
    assert error.value.code == 2
    assert "changed" in capsys.readouterr().err.lower()
    assert len(DatasetService(store).list_cases()) == 1


def test_case_input_privacy_and_bulk_manifest_validation(cli, tmp_path, capsys):
    run, store, image = cli
    with pytest.raises(SystemExit):
        run(
            "case",
            "create",
            "--image",
            image,
            payload={
                "name": "Leak",
                "task": "Pick",
                "candidate_context": {"ground_truth": "hidden"},
            },
        )
    assert "Private labels" in capsys.readouterr().err
    assert DatasetService(store).list_cases() == []
    records = [
        {"image": image.name, "name": "One", "task": "Pick"},
        {"image": image.name, "name": "Two", "task": "Place"},
    ]
    manifest = tmp_path / "cases.yaml"
    manifest.write_text(yaml.safe_dump(records))
    result = run("case", "import", "--file", manifest, "--operation-id", "batch-1")
    repeated = run("case", "import", "--file", manifest, "--operation-id", "batch-1")
    assert [case["id"] for case in result["cases"]] == [case["id"] for case in repeated["cases"]]
    assert result["count"] == 2
    records[1]["image"] = "../outside.png"
    manifest.write_text(yaml.safe_dump(records))
    with pytest.raises(SystemExit):
        run("case", "import", "--file", manifest)
    assert "inside the manifest directory" in capsys.readouterr().err
    assert len(DatasetService(store).list_cases()) == 2


def test_jsonl_case_import_validates_all_lines_before_inserting(cli, tmp_path, capsys):
    run, store, image = cli
    manifest = tmp_path / "cases.jsonl"
    rows = [
        {"image": image.name, "name": "One", "task": "Pick"},
        {"image": image.name, "name": "Two", "task": "Place"},
    ]
    manifest.write_text(json.dumps(rows[0]) + '\n{"private":"DO_NOT_ECHO"\n')
    with pytest.raises(SystemExit):
        run("case", "import", "--file", manifest)
    failure = capsys.readouterr().err
    assert "Invalid JSONL record on line 2" in failure
    assert "DO_NOT_ECHO" not in failure
    assert DatasetService(store).list_cases() == []
    manifest.write_text("\n".join(json.dumps(row) for row in rows) + "\n\n")
    result = run("case", "import", "--file", manifest, "--operation-id", "jsonl-batch")
    retry = run("case", "import", "--file", manifest, "--operation-id", "jsonl-batch")
    assert result["count"] == 2
    assert [row["id"] for row in result["cases"]] == [row["id"] for row in retry["cases"]]
    assert {row["task"] for row in DatasetService(store).list_cases()} == {"Pick", "Place"}


@pytest.mark.parametrize("oversized_bytes", [False, True])
def test_jsonl_import_limits_records_and_file_before_case_inserts(
    cli, tmp_path, capsys, oversized_bytes
):
    run, store, image = cli
    manifest = tmp_path / "large.jsonl"
    row = json.dumps({"image": image.name, "name": "One", "task": "Pick"})
    manifest.write_text(" " * (16 * 1024 * 1024 + 1) if oversized_bytes else (row + "\n") * 1001)
    with pytest.raises(SystemExit):
        run("case", "import", "--file", manifest)
    assert ("16777216 bytes" if oversized_bytes else "1000 records") in capsys.readouterr().err
    assert DatasetService(store).list_cases() == []


def test_contract_review_draft_revision_and_final_supersession(cli, capsys):
    run, _, image = cli
    case = run("case", "create", "--image", image, payload={"name": "Block", "task": "Place"})
    contract = run("contract", "create", payload=contract_payload())
    assert run("contract", "show", contract["id"])["criteria"][0]["id"] == "clearance"
    assert len(run("contract", "list")["contracts"]) == 1
    review = run(
        "review",
        "record",
        payload={
            "target_type": "case_validity",
            "case_revision_id": case["id"],
            "contract_id": contract["id"],
            "reviewer": "Local SME",
            "status": "draft",
        },
    )
    final = run(
        "review",
        "revise",
        review["id"],
        "--expected-revision",
        "1",
        payload={
            "status": "final",
            "decision": "accepted",
            "rationale": "Scene and task are usable",
        },
    )
    assert final["id"] == review["id"] and final["revision"] == 2
    correction = run(
        "review",
        "revise",
        final["id"],
        "--expected-revision",
        "2",
        payload={"decision": "rejected", "rationale": "Target was occluded"},
    )
    assert correction["id"] != final["id"] and correction["supersedes_id"] == final["id"]
    assert run("review", "show", final["id"])["decision"] == "accepted"
    assert len(run("review", "list", "--case", case["id"])["reviews"]) == 2
    with pytest.raises(SystemExit):
        run(
            "review",
            "revise",
            final["id"],
            "--expected-revision",
            "1",
            payload={"rationale": "Stale revision"},
        )
    assert "changed" in capsys.readouterr().err


def test_dataset_freeze_requires_current_preview_and_preserves_membership(cli, capsys, tmp_path):
    run, _, image = cli
    case = run("case", "create", "--image", image, payload={"name": "Block", "task": "Place"})
    contract = run("contract", "create", payload=contract_payload())
    review = run(
        "review",
        "record",
        payload={
            "target_type": "case_validity",
            "case_revision_id": case["id"],
            "contract_id": contract["id"],
            "reviewer": "Local SME",
            "status": "final",
            "decision": "accepted",
            "rationale": "Scene is usable",
        },
    )
    membership = {
        "name": "Placement set",
        "contract_id": contract["id"],
        "members": [{"case_revision_id": case["id"], "review_ids": [review["id"]]}],
    }
    preview = run("dataset", "preview", payload=membership)
    run(
        "review",
        "revise",
        review["id"],
        "--expected-revision",
        "1",
        payload={"status": "final", "decision": "rejected", "rationale": "Target was occluded"},
    )
    with pytest.raises(SystemExit):
        run("dataset", "freeze", "--preview-hash", preview["preview_hash"], payload=membership)
    assert "preview changed" in capsys.readouterr().err
    preview = run("dataset", "preview", payload=membership)
    frozen = run(
        "dataset",
        "freeze",
        "--preview-hash",
        preview["preview_hash"],
        "--operation-id",
        "freeze-1",
        payload=membership,
    )
    retry = run(
        "dataset",
        "freeze",
        "--preview-hash",
        preview["preview_hash"],
        "--operation-id",
        "freeze-1",
        payload=membership,
    )
    assert frozen["id"] == retry["id"]
    assert run("dataset", "show", frozen["id"])["members"][0]["case_revision_id"] == case["id"]
    assert len(run("dataset", "list")["datasets"]) == 1
    output = tmp_path / "dataset.json"
    main(["dataset", "export", frozen["id"], "--store", str(cli[1]), "--output", str(output)])
    assert json.loads(output.read_text())["id"] == frozen["id"]


def test_evidence_assets_are_verified_and_export_never_overwrites(cli, tmp_path, capsys):
    run, store, _ = cli
    recording = tmp_path / "trajectory.json"
    recording.write_text('{"position": [0, 1, 2]}')
    asset = run("asset", "create", "--file", recording, "--media-type", "application/json")
    assert run("asset", "show", asset["sha256"])["size_bytes"] == recording.stat().st_size
    shared_output = tmp_path / "conflicting-output.json"
    with pytest.raises(SystemExit):
        run("asset", "export", asset["sha256"], shared_output, "--output", shared_output)
    assert "different output paths" in capsys.readouterr().err
    assert not shared_output.exists()
    copy = tmp_path / "saved-recording.json"
    run("asset", "export", asset["sha256"], copy)
    assert copy.read_bytes() == recording.read_bytes()
    with pytest.raises(SystemExit):
        run("asset", "export", asset["sha256"], copy)
    capsys.readouterr()
    DatasetService(store).trials.asset_path(asset["sha256"]).write_bytes(b"corrupt")
    with pytest.raises(SystemExit):
        run("asset", "export", asset["sha256"], tmp_path / "corrupt-copy")
    assert "integrity" in capsys.readouterr().err.lower()
    assert not (tmp_path / "corrupt-copy").exists()


def test_sample_library_import_is_idempotent_and_keeps_labels_private(cli, tmp_path):
    run, store, image = cli
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            [
                {
                    "filename": image.name,
                    "task": "Place the red block",
                    "eval_category": "atomic",
                    "expected_subtasks": ["pick", "place"],
                }
            ]
        )
    )
    first = run("case", "import-samples", tmp_path)
    again = run("case", "import-samples", tmp_path)
    assert first["summary"]["imported"] == 1
    assert first["examples"][0]["case_revision_id"] == again["examples"][0]["case_revision_id"]
    case = run("case", "show", first["examples"][0]["case_revision_id"])
    assert "expected_subtasks" not in case["candidate_context"]
    assert case["reference_data"]["expected_subtasks"] == ["pick", "place"]
    assert DatasetService(store).list_reviews() == []


def test_contract_stdin_and_nonobject_input_errors_remain_machine_friendly(
    cli, monkeypatch, capsys
):
    run, _, _ = cli
    monkeypatch.setattr("sys.stdin", io.StringIO(yaml.safe_dump(contract_payload())))
    contract = run("contract", "create", "--file", "-")
    assert contract["name"] == "Safe placement"
    with pytest.raises(SystemExit) as error:
        run("contract", "create", payload=["not", "an", "object"])
    assert error.value.code == 2
    assert "JSON or YAML object" in capsys.readouterr().err
