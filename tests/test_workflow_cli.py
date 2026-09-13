"""CLI and dashboard services agree on durable trials, assessments and CI outcomes."""

import json

import pytest
import yaml
from PIL import Image

from rove.benchmarks import baseline_cli, workflow_cli
from rove.benchmarks import cli as legacy_cli
from rove.benchmarks.report import saved_report
from rove.benchmarks.store import CampaignStore
from rove.datasets.service import DatasetService
from rove.trials import cli as trial_cli
from rove.trials.store import TrialStore


@pytest.fixture
def h(tmp_path, monkeypatch):
    monkeypatch.delenv("ROVE_ASSISTANT_ENDPOINT", raising=False)
    monkeypatch.setattr(
        "rove.benchmarks.runner.runtime_fingerprint", lambda: {"version": "cli-test"}
    )
    monkeypatch.setattr(
        "rove.trials.execution.runtime_fingerprint", lambda: {"version": "cli-test"}
    )
    image = tmp_path / "observation.png"
    Image.new("RGB", (8, 8), "red").save(image)
    robot = tmp_path / "robot.urdf"
    robot.write_text('<robot name="fixture"><link name="base"/></robot>')
    config = tmp_path / "rove.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "endpoints": {
                    "m": {
                        "type": "vlm",
                        "adapter": "mock_vlm",
                        "capabilities": ["scene_analysis", "task_planning", "verification"],
                        "config": {"mock_latency_ms": [0, 0], "mock_quality": 1},
                    }
                },
                "strategies": {
                    "a": {"perceive": "m", "plan": "m", "verify": "m"},
                    "b": {"perceive": "m", "plan": "m", "verify": "m", "pipeline_mode": "parallel"},
                },
            }
        )
    )
    root = tmp_path / "journal"
    return {
        "root": root,
        "config": config,
        "image": image,
        "robot": robot,
        "temp": tmp_path,
        "common": ["--store", str(root), "--config", str(config)],
    }


def invoke(module, args, h, capsys, *, exit_code=None):
    if exit_code is None:
        module.main([*args, *h["common"]])
    else:
        with pytest.raises(SystemExit) as caught:
            module.main([*args, *h["common"]])
        assert caught.value.code == exit_code
    output = capsys.readouterr()
    return json.loads(output.out) if output.out else None, output.err


def document(h, name, value):
    path = h["temp"] / name
    path.write_text(json.dumps(value))
    return str(path)


def first_trial(h, capsys):
    return invoke(
        trial_cli,
        [
            "run",
            "--task",
            "Pick red block",
            "--image",
            str(h["image"]),
            "--urdf",
            str(h["robot"]),
            "--strategy",
            "a",
            "--strategy",
            "b",
        ],
        h,
        capsys,
    )[0]


def seed_and_request(h, capsys):
    result = first_trial(h, capsys)
    source = next(iter(result["trial_ids"].values()))
    draft = invoke(trial_cli, ["seed", source, "--operation-id", "seed"], h, capsys)[0]
    contract = DatasetService(h["root"]).create_contract(
        {
            "name": "Human output review",
            "scope": "plan_quality",
            "evidence_mode": "candidate_output",
            "criteria": [{"id": "target", "description": "Addresses the task"}],
        }
    )
    request = {
        "name": "CLI campaign",
        "case_revision_ids": draft["case_revision_ids"],
        "strategies": ["a", "b"],
        "contract_id": contract["id"],
        "seeds": [0],
        "ks": [1],
        "source": draft["source"],
        "operation_id": "run-one",
    }
    return draft, request


def review_all(h, cid):
    campaign = CampaignStore(h["root"]).get(cid)
    service = DatasetService(h["root"])
    for trial in CampaignStore(h["root"]).trials(cid):
        service.create_review(
            {
                "target_type": "trial_output",
                "trial_id": trial["trial_id"],
                "case_revision_id": campaign["case_revision_ids"][0],
                "contract_id": campaign["contract_id"],
                "reviewer": "Fixture reviewer",
                "status": "final",
                "decision": "accepted",
                "criteria": {"target": "accepted"},
                "rationale": "Reviewed mock output against fixture criterion",
            }
        )


def test_complete_offline_cli_sequence_preserves_inputs_gates_and_baseline(h, capsys):
    _draft, request = seed_and_request(h, capsys)
    source_rows = TrialStore(h["root"]).list()
    assert len(source_rows) == 2 and {row["strategy"]["id"] for row in source_rows} == {"a", "b"}
    assert len({row["task"]["image_asset"]["sha256"] for row in source_rows}) == 1
    assert len({row["snapshot"]["config"]["robot_asset"]["sha256"] for row in source_rows}) == 1
    for row in source_rows:
        assert row["status"] == "completed" and TrialStore(h["root"]).events(row["id"])
    bundle = h["temp"] / "artifacts"
    run, error = invoke(
        workflow_cli,
        [
            "run",
            document(h, "launch.json", request),
            "--revision",
            "commit-abc",
            "--output",
            str(bundle),
            "--require-success-rate",
            "0",
        ],
        h,
        capsys,
        exit_code=2,
    )
    assert "unresolved" in error
    assert run["report"]["campaign"]["spec"]["revision"] == "commit-abc"
    assert (
        run["report"]["summary"]["planned_trials"] == 2
    )  # earlier exploratory trials are excluded
    assert not run["quality_gate"]["passed"]
    assert {p.suffix for p in bundle.iterdir()} == {".html", ".json", ".csv"}
    assert TrialStore(h["root"]).count() == 4
    review_all(h, run["id"])
    report = invoke(workflow_cli, ["report", run["id"], "--require-success-rate", "1"], h, capsys)[
        0
    ]
    assert report["quality_gate"]["passed"]
    assert report["report"]["summary"] == json.loads(
        json.dumps(saved_report(h["root"], run["id"])["summary"])
    )
    row = CampaignStore(h["root"]).trials(run["id"])[0]
    shown = invoke(trial_cli, ["show", row["trial_id"]], h, capsys)[0]
    assert shown["assessment"]["outcome"] == "pass"
    listed = invoke(trial_cli, ["list", "--source", "campaign"], h, capsys)[0]
    assert all(item["assessment"]["outcome"] == "pass" for item in listed["trials"])
    baseline = invoke(
        baseline_cli,
        [
            "save",
            document(
                h,
                "baseline.json",
                {
                    "name": "Reference",
                    "campaign_id": run["id"],
                    "strategy_id": "a",
                    "operation_id": "baseline-one",
                },
            ),
        ],
        h,
        capsys,
    )[0]
    improved = invoke(workflow_cli, ["improve", run["id"]], h, capsys)[0]
    assert improved["baselines"][0]["revision_id"] == baseline["revision_id"]
    assert improved["defaults"]["seeds"] == [0]
    revised = {
        **request,
        "operation_id": "run-two",
        "source": improved["source"],
        "baseline_revision_id": baseline["revision_id"],
    }
    candidate = invoke(workflow_cli, ["run", document(h, "candidate.json", revised)], h, capsys)[0]
    compared = invoke(workflow_cli, ["compare", candidate["id"], "--strategy", "b"], h, capsys)[0]
    assert compared["candidate_strategy_id"] == "b"
    assert compared["baseline_reference"]["revision_id"] == baseline["revision_id"]
    assert all(item["outcome"] == "pass" for item in compared["baseline_trials"])
    assert compared["comparable"]
    # Idempotent CI retry with the same operation does not launch duplicate attempts.
    invoke(workflow_cli, ["run", document(h, "candidate.json", revised)], h, capsys)
    assert TrialStore(h["root"]).count() == 6


def test_saved_case_cli_uses_its_robot_without_separate_urdf_and_rejects_changed_input(h, capsys):
    draft, _ = seed_and_request(h, capsys)
    result = invoke(
        trial_cli, ["run", "--case", draft["case_revision_ids"][0], "--strategy", "a"], h, capsys
    )[0]
    trial = TrialStore(h["root"]).get(result["trial_ids"]["a"])
    assert trial["task"]["case_revision_id"] == draft["case_revision_ids"][0]
    assert trial["snapshot"]["config"]["robot_asset"]
    before = TrialStore(h["root"]).count()
    _, error = invoke(
        trial_cli,
        [
            "run",
            "--case",
            draft["case_revision_ids"][0],
            "--task",
            "Different task",
            "--strategy",
            "a",
        ],
        h,
        capsys,
        exit_code=2,
    )
    assert "differs" in error and TrialStore(h["root"]).count() == before


def test_cli_metrics_draft_is_honest_when_unconfigured(h, capsys):
    draft, _ = seed_and_request(h, capsys)
    output = invoke(
        workflow_cli,
        [
            "metrics-draft",
            document(
                h,
                "metrics.json",
                {"case_revision_ids": draft["case_revision_ids"], "strategies": ["a"]},
            ),
        ],
        h,
        capsys,
    )[0]
    assert output["source"] == "template" and not output["assistant_configured"]
    assert 3 <= len(output["contract"]["criteria"]) <= 5
    assert output["requires_confirmation"]


def test_campaign_iteration_can_replace_case_coverage_but_is_not_comparable(h, capsys):
    draft, request = seed_and_request(h, capsys)
    source = invoke(workflow_cli, ["run", document(h, "source.json", request)], h, capsys)[0]
    baseline = invoke(
        baseline_cli,
        [
            "save",
            document(
                h,
                "base.json",
                {
                    "name": "Baseline",
                    "campaign_id": source["id"],
                    "strategy_id": "a",
                    "operation_id": "save-base",
                },
            ),
        ],
        h,
        capsys,
    )[0]
    improvement = invoke(workflow_cli, ["improve", source["id"]], h, capsys)[0]
    service = DatasetService(h["root"])
    original = service.get_case_revision(draft["case_revision_ids"][0])
    replacement = service.import_case(
        {"name": "New coverage", "task": "Inspect scene"}, original["image_asset"]["sha256"]
    )
    changed = {
        **request,
        "source": improvement["source"],
        "case_revision_ids": [replacement["id"]],
        "baseline_revision_id": baseline["revision_id"],
    }
    preview = invoke(workflow_cli, ["preview", document(h, "replace.json", changed)], h, capsys)[0]
    assert preview["ready"] and not any(item["comparable"] for item in preview["comparisons"])


def test_cli_bounded_binary_input_does_not_create_trials(h, capsys):
    too_large = h["temp"] / "huge.urdf"
    with too_large.open("wb") as stream:
        stream.truncate(4 * 1024 * 1024 + 1)
    _, error = invoke(
        trial_cli,
        [
            "run",
            "--task",
            "Pick",
            "--image",
            str(h["image"]),
            "--urdf",
            str(too_large),
            "--strategy",
            "a",
        ],
        h,
        capsys,
        exit_code=2,
    )
    assert "byte limit" in error and TrialStore(h["root"]).count() == 0


def test_legacy_report_uses_current_assessments(h, capsys):
    _, request = seed_and_request(h, capsys)
    run = invoke(workflow_cli, ["run", document(h, "run.json", request)], h, capsys)[0]
    review_all(h, run["id"])
    out = h["temp"] / "legacy-report"
    legacy_cli.main(["report", run["id"], *h["common"], "--output", str(out)])
    capsys.readouterr()
    report = json.loads((out / (run["id"] + ".json")).read_text())
    assert all(row["outcome"] == "pass" for row in report["trials"])
    assert report["assessments"]["review_ids"]


def test_quality_gate_requires_declared_resolved_targets(h, capsys):
    _, request = seed_and_request(h, capsys)
    run = invoke(workflow_cli, ["run", document(h, "run.json", request)], h, capsys)[0]
    review_all(h, run["id"])
    data, error = invoke(
        workflow_cli, ["report", run["id"], "--require-targets"], h, capsys, exit_code=2
    )
    assert not data["quality_gate"]["passed"] and "targets are missing" in error


def test_ci_automatic_mock_campaign_meets_declared_targets_without_human_fabrication(h, capsys):
    draft, request = seed_and_request(h, capsys)
    service = DatasetService(h["root"])
    automatic = service.create_contract(
        {
            "name": "Configured mock output verification",
            "scope": "plan_quality",
            "evidence_mode": "candidate_output",
            "criteria": [
                {
                    "id": "verifier",
                    "description": "Configured verifier accepts output",
                    "assessment": "configured_verifier",
                    "endpoint": "m",
                }
            ],
            "campaign_targets": [
                {"metric": "task_success", "operator": "gte", "threshold": 1, "unit": "fraction"}
            ],
        }
    )
    request.update(contract_id=automatic["id"], source=draft["source"])
    result, stderr = invoke(
        workflow_cli,
        [
            "run",
            document(h, "automatic.json", request),
            "--require-success-rate",
            "1",
            "--require-targets",
            "--output",
            str(h["temp"] / "ci-artifacts"),
        ],
        h,
        capsys,
    )
    assert result["quality_gate"]["passed"]
    assert all(
        target["status"] == "met" for target in result["report"]["summary"]["campaign_targets"]
    )
    assert service.list_reviews() == []
    assert "execution completed" in stderr and "(pass)" not in stderr


def test_cancel_requires_owning_loopback_server_and_never_changes_local_status(
    h, capsys, monkeypatch
):
    import httpx

    _, request = seed_and_request(h, capsys)
    completed = invoke(workflow_cli, ["run", document(h, "completed.json", request)], h, capsys)[0]
    before = CampaignStore(h["root"]).get(completed["id"])["status"]
    _, error = invoke(
        workflow_cli,
        ["cancel", completed["id"], "--url", "https://example.com"],
        h,
        capsys,
        exit_code=2,
    )
    assert "loopback" in error

    class Unavailable:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def post(self, url):
            raise httpx.ConnectError("fixture local server unavailable")

    monkeypatch.setattr(workflow_cli.httpx, "Client", Unavailable)
    _, error = invoke(workflow_cli, ["cancel", completed["id"]], h, capsys, exit_code=2)
    assert "no local status was changed" in error
    assert CampaignStore(h["root"]).get(completed["id"])["status"] == before


def test_case_cli_rejects_valid_replacement_image_under_original_digest(h, capsys):
    import io

    service = DatasetService(h["root"])
    original = h["image"].read_bytes()
    asset = service.trials.save_asset(original, "image/png")
    case = service.import_case({"name": "Saved", "task": "Pick red block"}, asset["sha256"])
    replacement = None
    for red in range(254, 0, -1):
        stream = io.BytesIO()
        Image.new("RGB", (8, 8), (red, 0, 0)).save(stream, format="PNG")
        if len(stream.getvalue()) == len(original):
            replacement = stream.getvalue()
            break
    assert replacement is not None and replacement != original
    service.trials.asset_path(asset["sha256"]).write_bytes(replacement)
    # Metadata/size still match, but the immutable identity must reject altered bytes.
    assert service.get_case_revision(case["id"])["id"] == case["id"]
    _, error = invoke(
        trial_cli, ["run", "--case", case["id"], "--strategy", "a"], h, capsys, exit_code=2
    )
    assert "integrity" in error
    assert service.trials.count() == 0


@pytest.mark.parametrize("execution", ["error", "timeout", "interrupted", "completed"])
def test_execution_commands_fail_after_writing_reports_but_judgment_failure_is_valid(
    h, capsys, monkeypatch, execution
):
    _, request = seed_and_request(h, capsys)

    async def attempt(*_):
        return {
            "execution": execution,
            "outcome": "fail" if execution == "completed" else "unknown",
        }

    monkeypatch.setattr("rove.benchmarks.runner.run_attempt", attempt)
    exit_code = None if execution == "completed" else 2
    output = h["temp"] / "execution-artifacts"
    result, error = invoke(
        workflow_cli,
        ["run", document(h, "execution.json", request), "--output", str(output)],
        h,
        capsys,
        exit_code=exit_code,
    )
    assert not result["quality_gate"]["enabled"]
    assert result["quality_gate"]["passed"] is None
    assert all(
        (output / f"{result['id']}.{suffix}").is_file() for suffix in ("html", "json", "csv")
    )
    if exit_code:
        assert "Campaign execution failed" in error
    invoke(
        workflow_cli,
        ["resume", result["id"], "--output", str(output)],
        h,
        capsys,
        exit_code=exit_code,
    )
    # Exporting already saved failures succeeds unless an explicit quality gate rejects them.
    invoke(workflow_cli, ["report", result["id"]], h, capsys)
