"""Probe outputs remain inspectable but cannot enter simulation or dynamics."""

from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from rove.adapters.forward_kinematics import forward_kinematics
from rove.adapters.mock_sim import MockSimAdapter
from rove.adapters.mock_vla import MockVLAAdapter
from rove.adapters.mock_vlm import MockVLMAdapter
from rove.models import ActionPrediction, ActionSpace, PipelineContext
from rove.models.verification import EvaluatorContext
from rove.orchestrator.pipeline import EvaluationPipeline


class Probe(MockVLAAdapter):
    async def predict_action(self, *args, **kwargs):
        return ActionPrediction(
            actions=[[0.0] * 7],
            num_steps=1,
            action_space=ActionSpace.EEF_DELTA,
            execution_eligible=False,
        )


@pytest.mark.parametrize("mode", ["sequential", "parallel"])
async def test_probe_cannot_execute_on_any_simulator_even_with_copied_action_space(mode):
    sim = MockSimAdapter()
    sim.step = AsyncMock(side_effect=AssertionError("Probe reached simulator"))
    pipeline = EvaluationPipeline(
        act_adapter=Probe(),
        sim=sim,
        verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [0, 0]}),
        pipeline_mode=mode,
        verify_mode="precompute",
        compute_dynamics=True,
    )
    pipeline._compute_dynamics_analysis = Mock(side_effect=AssertionError("Probe reached dynamics"))
    results = [row async for row in pipeline.run_trial("Inspect image only", "image")]
    failed = [row for row in results if row.stage == "act" and row.status == "error"]
    assert len(failed) == 1
    assert "ineligible for simulator execution" in failed[0].error
    sim.step.assert_not_called()
    pipeline._compute_dynamics_analysis.assert_not_called()


def test_probe_dynamics_and_configured_fk_are_unavailable_before_physics_call(monkeypatch):
    import sys
    from types import SimpleNamespace

    compute = Mock(side_effect=AssertionError("Probe reached physics engine"))
    monkeypatch.setitem(
        sys.modules, "rove.adapters.dynamics_mujoco", SimpleNamespace(compute_dynamics=compute)
    )
    action = ActionPrediction(
        actions=[[0.0] * 7],
        num_steps=1,
        action_space=ActionSpace.EEF_DELTA,
        execution_eligible=False,
    )
    pipeline = EvaluationPipeline(urdf_path="unused.xml")
    ctx = PipelineContext(task="Inspect", image_base64="image")
    assert "ineligible" in pipeline._compute_dynamics_analysis(ctx, action)
    assert ctx.dynamics_analysis is None
    result = forward_kinematics(
        EvaluatorContext(
            task="Inspect",
            urdf_path="unused.xml",
            pipeline={"action": action.model_dump(), "task_metadata": {}},
        ),
        {},
    )
    assert result.verdict == "unknown" and result.evidence_quality == "unknown"
    assert "ineligible" in result.reasoning
    assert result.evidence_refs == []
    compute.assert_not_called()


def test_legacy_prediction_preserves_existing_simulator_behavior():
    action = ActionPrediction(actions=[[0.0] * 7])
    assert action.execution_eligible is None
    EvaluationPipeline(sim=MockSimAdapter())._validate_sim_actions(action)
    with pytest.raises(ValidationError):
        ActionPrediction(execution_eligible="false")


def test_probe_context_preserves_assumptions_without_inferred_gripper_events():
    action = ActionPrediction(
        actions=[[0.0] * 7] * 12,
        num_steps=12,
        execution_eligible=False,
        input_assumptions=["Zero state is not measured robot state"],
    )
    context = PipelineContext(task="Inspect", action=action).to_dict()["action"]
    assert context["action_space"] is None and context["gripper_index"] is None
    assert context["execution_eligible"] is False
    assert context["input_assumptions"] == action.input_assumptions
    assert "gripper_events" not in context
    assert "gripper_events" not in context["actions_note"]
    assert len(context["actions"]) == 10 and context["actions_truncated"]
    legacy = PipelineContext(task="Inspect", action=ActionPrediction(actions=[[0.0] * 7]))
    assert legacy.to_dict()["action"]["gripper_events"]


def test_campaign_stage_evidence_carries_probe_restrictions_without_raw_actions():
    from rove.benchmarks.insights import trial_evidence

    evidence = trial_evidence(
        {
            "result": {
                "stages": [
                    {
                        "stage": "act",
                        "output": {
                            "execution_eligible": False,
                            "input_assumptions": ["Zero state is not measured"],
                            "action_space": None,
                            "gripper_index": None,
                            "actions": [[0.0] * 7],
                        },
                    }
                ]
            }
        }
    )
    stage = evidence["stages"][0]
    assert stage["execution_eligible"] is False
    assert stage["input_assumptions"] == ["Zero state is not measured"]
    assert "actions" not in stage


@pytest.mark.parametrize("assumptions", [["x" * 2001], ["x"] * 31])
def test_input_assumptions_are_bounded_before_entering_evidence(assumptions):
    with pytest.raises(ValidationError):
        ActionPrediction(input_assumptions=assumptions)


@pytest.mark.parametrize("mode", ["sequential", "parallel"])
async def test_probe_output_stays_inspectable_without_simulation_or_dynamics(mode):
    pipeline = EvaluationPipeline(
        act_adapter=Probe(),
        verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [0, 0]}),
        pipeline_mode=mode,
        verify_mode="precompute",
        compute_dynamics=True,
    )
    results = [row async for row in pipeline.run_trial("Inspect image only", "image")]
    action = next(row.output for row in results if row.stage == "act" and row.status == "completed")
    assert action["execution_eligible"] is False
    assert action["actions"] == [[0.0] * 7]
    assert action["sim_success"] is None
    assert "actions_executed" not in action
    assert "dynamics_analysis" not in action
    if mode == "sequential":
        assert "ineligible" in action["dynamics_skipped"]
    else:
        dynamics = next(
            row.output for row in results if row.stage == "dynamics" and row.status == "completed"
        )
        assert "ineligible" in dynamics["skipped"]
