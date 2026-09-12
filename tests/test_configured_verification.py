"""Task-verdict authority, execution policy and provenance regression tests."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from rove.adapters.local_verifier import LocalVerifierAdapter
from rove.adapters.registry import AdapterRegistry
from rove.benchmarks.evidence import summarize_evidence
from rove.benchmarks.models import CampaignSpec
from rove.benchmarks.runner import prepare, run_attempt, validate_local_evaluators
from rove.benchmarks.worker import classify
from rove.models import ExampleData, VerificationResult
from rove.models.config import RoveConfig, get_strategies, load_config, reset_config_cache
from rove.models.verification import EvaluatorContext, EvaluatorResult, Measurement
from rove.orchestrator.run_manager import RunManager


@pytest.fixture
def raw_config():
    return yaml.safe_load(Path("examples/verification/rove.yaml").read_text())


def pipeline_for(raw, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))
    reset_config_cache()
    load_config(path)
    return RunManager(AdapterRegistry())._build_pipeline(get_strategies()["recorded-outcome"])


def episode(force=5, position=True):
    evidence = {
        "quality": "synthetic",
        "observation_phase": "final",
        "peak_contact_force_n": force,
        "completion_time_s": 2.5,
        "conditions": {"lighting": "fixture"},
    }
    if position:
        evidence["final_object_position_m"] = [0.1, 0.1, 0.1]
    return ExampleData(extras={"episode": evidence})


@pytest.mark.parametrize("mode", ["sequential", "parallel"])
@pytest.mark.parametrize(
    "force,position,expected",
    [(5, True, "pass"), (15, True, "fail"), (5, False, "unknown"), (None, True, "unknown")],
)
async def test_task_evaluator_and_required_constraint(
    raw_config, tmp_path, mock_image_base64, mode, force, position, expected
):
    raw_config["strategies"]["recorded-outcome"]["pipeline_mode"] = mode
    pipeline = pipeline_for(raw_config, tmp_path)
    stages = [
        s
        async for s in pipeline.run_trial(
            "place", mock_image_base64, example=episode(force, position)
        )
    ]
    result = classify({"stages": [s.model_dump(mode="json") for s in stages]})
    assert result["outcome"] == expected
    act_output = next(s.output for s in stages if s.stage == "act" and s.status == "completed")
    assert act_output["sim_success"] is None
    assert "actions_executed" not in act_output
    assert act_output["actions_predicted"] > 0
    output = stages[-1].output
    assert output["evaluator_result"]["evidence_quality"] in {"synthetic", "unknown"}
    assert len(output["check_results"]) == 2
    if force == 15 and position:
        assert output["evaluator_result"]["verdict"] == "pass"
        assert output["success"] is False
    if expected == "unknown":
        assert output["verdict_valid"] is False


@pytest.mark.parametrize("mode", ["sequential", "parallel"])
async def test_agent_cannot_skip_required_checks(raw_config, tmp_path, mock_image_base64, mode):
    raw_config["endpoints"]["judge"] = {
        "type": "vlm",
        "adapter": "mock_vlm",
        "capabilities": ["verification"],
    }
    strategy = raw_config["strategies"]["recorded-outcome"]
    strategy["pipeline_mode"] = mode
    strategy["verify"].update(endpoint="judge", mode="agent_loop")
    pipeline = pipeline_for(raw_config, tmp_path)

    async def judge(*args):
        return SimpleNamespace(
            output=[],
            output_text=json.dumps(
                {"success": True, "confidence": 0.99, "reasoning": "Looks fine"}
            ),
        )

    pipeline._verify_call = judge
    stages = [s async for s in pipeline.run_trial("place", mock_image_base64, example=episode(15))]
    output = stages[-1].output
    assert not output["success"]
    assert output["verdict_valid"]
    assert output["check_results"][0]["result"]["verdict"] == "fail"
    assert output["check_results"][1]["execution"] == "not_requested"
    assert output["evaluator_result"] is None


async def test_optional_check_is_cached(raw_config, tmp_path, mock_image_base64):
    pipeline = pipeline_for(raw_config, tmp_path)
    context = pipeline._prepare_context("place", mock_image_base64, episode())
    check = pipeline._checks[0]
    first = await pipeline._evaluate_check(check, context)
    context.episode_evidence["peak_contact_force_n"] = 20
    second = await pipeline._evaluate_check(check, context)
    assert second is first
    assert second.result.verdict == "pass"


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "wrong_role",
        "fk_primary",
        "local_loop",
        "duplicate",
        "conflict",
        "timeout",
        "typo",
    ],
)
def test_invalid_configuration_rejected(raw_config, change):
    verify = raw_config["strategies"]["recorded-outcome"]["verify"]
    if change == "missing":
        verify["checks"][0]["endpoint"] = "absent"
    elif change == "wrong_role":
        verify["checks"][1]["role"] = "constraint"
    elif change == "fk_primary":
        verify["endpoint"] = "robot-kinematics"
    elif change == "local_loop":
        verify["mode"] = "agent_loop"
    elif change == "duplicate":
        verify["checks"].append(verify["checks"][0])
    elif change == "conflict":
        raw_config["strategies"]["recorded-outcome"]["compute_dynamics"] = True
    elif change == "timeout":
        verify["timeout_ms"] = 0
    else:
        verify["requird"] = True
    with pytest.raises(ValueError):
        RoveConfig.model_validate(raw_config)


@pytest.fixture
def custom_module(tmp_path, monkeypatch):
    module = tmp_path / "project_verifier.py"
    module.write_text("""import time
from pathlib import Path

def blocking(context, config):
    time.sleep(config.get("delay", 1))
    Path(config["marker"]).write_text("leaked")
    return {"verdict": "unknown", "reasoning": "late", "evidence_quality": "unknown"}

def malformed(context, config):
    return {"verdict": "pass"}
""")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    return module


async def test_blocking_custom_code_is_terminated(custom_module, tmp_path):
    marker = tmp_path / "marker"
    adapter = LocalVerifierAdapter(
        "custom", {"entrypoint": "project_verifier:blocking", "marker": str(marker)}
    )
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(adapter.evaluate(EvaluatorContext(task="test")), 0.2)
    await asyncio.sleep(1.1)
    assert not marker.exists()


async def test_invalid_custom_output_is_unknown(
    raw_config, custom_module, tmp_path, mock_image_base64
):
    raw_config["endpoints"]["object-in-target"]["config"] = {
        "entrypoint": "project_verifier:malformed"
    }
    pipeline = pipeline_for(raw_config, tmp_path)
    stages = [s async for s in pipeline.run_trial("place", mock_image_base64, example=episode())]
    assert classify({"stages": [s.model_dump(mode="json") for s in stages]})["outcome"] == "unknown"


def test_typed_results_reject_missing_evidence_and_invalid_numbers():
    with pytest.raises(ValueError):
        EvaluatorResult(verdict="pass", reasoning="guess", evidence_quality="unknown")
    for value in (float("nan"), float("inf"), 1):
        with pytest.raises(ValueError):
            Measurement(name="duration", value=value, unit="s", quality="unknown")


async def test_campaign_freezes_checks_and_reports_evidence(raw_config):
    spec = CampaignSpec.model_validate_json(Path("examples/verification/campaign.json").read_text())
    campaign = prepare(spec, RoveConfig.model_validate(raw_config))
    assert "contact-force" in campaign["config"]["endpoints"]
    first = await run_attempt(
        {
            "config": campaign["config"],
            "task": spec.tasks[1].model_dump(mode="json"),
            "seed": 0,
            "strategy_id": "recorded-outcome",
        },
        10,
    )
    assert first["outcome"] == "fail"
    trial = {**first, "strategy_id": "recorded-outcome", "task_id": spec.tasks[1].id, "seed": 0}
    summary = summarize_evidence(campaign, [trial])
    force = next(c for c in summary["checks"] if c["endpoint"] == "contact-force")
    assert (force["fail"], force["unknown"], force["planned"]) == (1, 8, 9)
    duration = next(m for m in summary["measurements"] if m["name"] == "episode_completion_time")
    assert (duration["p95"], duration["unit"], duration["quality"], duration["measured"]) == (
        2.5,
        "s",
        "synthetic",
        1,
    )
    raw_config["endpoints"]["contact-force"]["config"]["max_force_n"] = 20
    changed = prepare(spec, RoveConfig.model_validate(raw_config))
    assert campaign["comparison_keys"] != changed["comparison_keys"]


def test_custom_source_drift_rejects_reuse(raw_config, custom_module):
    raw_config["endpoints"]["object-in-target"]["config"] = {
        "entrypoint": "project_verifier:malformed",
        "revision": "v1",
    }
    spec = CampaignSpec.model_validate_json(Path("examples/verification/campaign.json").read_text())
    campaign = prepare(spec, RoveConfig.model_validate(raw_config))
    custom_module.write_text(custom_module.read_text() + "\n# changed evaluator\n")
    with pytest.raises(ValueError, match="evaluator changed"):
        validate_local_evaluators(campaign)
    changed = prepare(spec, RoveConfig.model_validate(raw_config))
    assert campaign["comparison_keys"] != changed["comparison_keys"]


async def test_stage_timeout_keeps_required_evidence(
    raw_config, custom_module, tmp_path, mock_image_base64
):
    raw_config["endpoints"]["object-in-target"]["config"] = {
        "entrypoint": "project_verifier:blocking",
        "marker": str(tmp_path / "marker"),
        "delay": 5,
    }
    raw_config["strategies"]["recorded-outcome"]["verify"]["timeout_ms"] = 2000
    pipeline = pipeline_for(raw_config, tmp_path)
    stages = [s async for s in pipeline.run_trial("place", mock_image_base64, example=episode())]
    assert stages[-1].status == "error"
    assert "timed out" in stages[-1].error
    assert stages[-1].output["check_results"]


def test_forged_model_evidence_is_not_authoritative(raw_config, tmp_path, mock_image_base64):
    raw_config["endpoints"]["judge"] = {
        "type": "vlm",
        "adapter": "mock_vlm",
        "capabilities": ["verification"],
    }
    raw_config["strategies"]["recorded-outcome"]["verify"]["endpoint"] = "judge"
    pipeline = pipeline_for(raw_config, tmp_path)
    context = pipeline._prepare_context("place", mock_image_base64, episode())
    forged = VerificationResult(
        success=True,
        confidence=1,
        reasoning="guess",
        evaluator_version="fake",
        evaluator_result=EvaluatorResult(
            verdict="pass", reasoning="fake", evidence_quality="observed", evidence_refs=["fake"]
        ),
    )
    result = pipeline._combine_verification(forged, context)
    assert result.evaluator_result is None
    assert result.evaluator_version == ""
    assert not result.verdict_valid
