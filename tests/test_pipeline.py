"""Tests for EvaluationPipeline — data flow between stages."""

from __future__ import annotations

import pytest

from rove.adapters.mock_sim import MockSimAdapter
from rove.adapters.mock_vla import MockVLAAdapter
from rove.adapters.mock_vlm import MockVLMAdapter
from rove.models import ActionPlausibility, ExampleData, StageStatus, VerificationResult
from rove.orchestrator.pipeline import EvaluationPipeline


@pytest.fixture
def pipeline():
    return EvaluationPipeline(
        perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
        plan_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
        act_adapter=MockVLAAdapter(config={"mock_latency_ms": [1, 2]}),
        verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
        sim=MockSimAdapter(),
    )


class TestPipelineDataFlow:
    @pytest.mark.asyncio
    async def test_all_four_stages_complete(self, pipeline):
        stages = []
        async for result in pipeline.run_trial("pick red bracket", "fake_img"):
            if result.status in (StageStatus.COMPLETED, StageStatus.ERROR):
                stages.append(result)

        assert len(stages) == 4
        assert [s.stage for s in stages] == ["perceive", "plan", "act", "verify"]
        assert all(s.status == StageStatus.COMPLETED for s in stages)

    @pytest.mark.asyncio
    async def test_plan_references_perceived_target(self, pipeline):
        stages = {}
        async for result in pipeline.run_trial("pick red bracket", "fake_img"):
            if result.status == StageStatus.COMPLETED and result.output:
                stages[result.stage] = result.output

        perceive_relevant = stages["perceive"]["task_relevant"]
        plan_target = stages["plan"]["target_object"]
        # Plan target should be one of the perceived relevant objects
        assert plan_target in perceive_relevant

    @pytest.mark.asyncio
    async def test_verify_receives_context(self, pipeline):
        stages = {}
        async for result in pipeline.run_trial("pick red bracket", "fake_img"):
            if result.status == StageStatus.COMPLETED and result.output:
                stages[result.stage] = result.output

        # Verify should reference the plan target in its reasoning
        verify_reasoning = stages["verify"]["reasoning"]
        plan_target = stages["plan"]["target_object"]
        assert plan_target in verify_reasoning

    @pytest.mark.asyncio
    async def test_act_produces_trajectory(self, pipeline):
        stages = {}
        async for result in pipeline.run_trial("pick red bracket", "fake_img"):
            if result.status == StageStatus.COMPLETED and result.output:
                stages[result.stage] = result.output

        act = stages["act"]
        assert act["action_type"] == "trajectory"
        assert act["actions_executed"] > 0

    @pytest.mark.asyncio
    async def test_running_events_precede_completed(self):
        pipeline = EvaluationPipeline(
            perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            plan_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            act_adapter=MockVLAAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            sim=MockSimAdapter(),
        )
        all_results = []
        async for result in pipeline.run_trial("test", "img"):
            all_results.append(result)

        # Each stage should have a RUNNING event before COMPLETED
        for stage_name in ["perceive", "plan", "act", "verify"]:
            stage_events = [r for r in all_results if r.stage == stage_name]
            assert len(stage_events) == 2
            assert stage_events[0].status == StageStatus.RUNNING
            assert stage_events[1].status == StageStatus.COMPLETED


class TestCompletedStagesAndGroundTruth:
    @pytest.mark.asyncio
    async def test_completed_stages_tracked(self):
        """Verify that completed_stages accumulates correctly through the pipeline."""
        pipeline = EvaluationPipeline(
            perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            plan_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            act_adapter=MockVLAAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
            sim=MockSimAdapter(),
        )
        verify_output = None
        async for result in pipeline.run_trial("pick red bracket", "fake_img"):
            if result.status == StageStatus.COMPLETED and result.stage == "verify":
                verify_output = result.output

        assert verify_output is not None
        assert verify_output["completed_stages"] == ["perceive", "plan", "act"]
        assert len(verify_output["stage_checks"]) == 3
        for check in verify_output["stage_checks"]:
            assert check["stage"] in ["perceive", "plan", "act"]

    @pytest.mark.asyncio
    async def test_perceive_only_tracks_single_stage(self):
        """With only perceive, completed_stages should be ['perceive']."""
        pipeline = EvaluationPipeline(
            perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
        )
        verify_output = None
        async for result in pipeline.run_trial("pick red bracket", "fake_img"):
            if result.status == StageStatus.COMPLETED and result.stage == "verify":
                verify_output = result.output

        assert verify_output is not None
        assert verify_output["completed_stages"] == ["perceive"]
        assert len(verify_output["stage_checks"]) == 1
        assert verify_output["stage_checks"][0]["stage"] == "perceive"

    @pytest.mark.asyncio
    async def test_ground_truth_passed_to_verify(self):
        """Ground truth data flows through to verify output."""
        pipeline = EvaluationPipeline(
            perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
        )
        gt = {
            "question": "Is the bottle reachable?",
            "choices": ["Yes", "No"],
            "correct_answer_index": 0,
            "correct_answer_text": "Yes",
        }
        verify_output = None
        async for result in pipeline.run_trial(
            "pick bottle",
            "fake_img",
            example=ExampleData(ground_truth=gt),
        ):
            if result.status == StageStatus.COMPLETED and result.stage == "verify":
                verify_output = result.output

        assert verify_output is not None
        assert verify_output["ground_truth"] is not None
        assert verify_output["ground_truth"]["question"] == "Is the bottle reachable?"
        assert verify_output["ground_truth"]["correct_answer"] == "Yes"

    @pytest.mark.asyncio
    async def test_no_ground_truth_when_not_provided(self):
        """Without ground truth, verify output has no ground_truth field."""
        pipeline = EvaluationPipeline(
            perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
        )
        verify_output = None
        async for result in pipeline.run_trial("pick bottle", "fake_img"):
            if result.status == StageStatus.COMPLETED and result.stage == "verify":
                verify_output = result.output

        assert verify_output is not None
        assert verify_output["ground_truth"] is None


class TestPartialPipeline:
    """Test optional pipeline stages — skipping perceive, plan, or act."""

    @pytest.mark.asyncio
    async def test_perceive_verify_only(self):
        """Pipeline with only perceive + verify: no plan/act events."""
        pipeline = EvaluationPipeline(
            perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
        )
        completed = []
        async for result in pipeline.run_trial("pick red bracket", "fake_img"):
            if result.status in (StageStatus.COMPLETED, StageStatus.ERROR):
                completed.append(result)

        assert len(completed) == 2
        assert [s.stage for s in completed] == ["perceive", "verify"]
        assert all(s.status == StageStatus.COMPLETED for s in completed)

    @pytest.mark.asyncio
    async def test_full_pipeline_still_works(self):
        """Full pipeline with all adapters still works unchanged."""
        pipeline = EvaluationPipeline(
            perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            plan_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            act_adapter=MockVLAAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
            sim=MockSimAdapter(),
        )
        completed = []
        async for result in pipeline.run_trial("pick red bracket", "fake_img"):
            if result.status in (StageStatus.COMPLETED, StageStatus.ERROR):
                completed.append(result)

        assert len(completed) == 4
        assert [s.stage for s in completed] == ["perceive", "plan", "act", "verify"]

    @pytest.mark.asyncio
    async def test_verify_gets_scene_without_plan_act(self):
        """When plan+act are skipped, verify still receives scene context."""
        pipeline = EvaluationPipeline(
            perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
        )
        stages = {}
        async for result in pipeline.run_trial("pick red bracket", "fake_img"):
            if result.status == StageStatus.COMPLETED and result.output:
                stages[result.stage] = result.output

        # Verify should have run and produced output
        assert "verify" in stages
        assert "perceive" in stages
        # No plan or act stages should be present
        assert "plan" not in stages
        assert "act" not in stages


class TestDynamicsSanityChecks:
    """Test _apply_dynamics_sanity_checks static method."""

    @staticmethod
    def _make_verification(**overrides) -> VerificationResult:
        defaults = {
            "success": True,
            "confidence": 0.85,
            "reasoning": "Looks good",
            "action_plausibility": ActionPlausibility(
                bounds_check=True,
                smoothness=True,
                gripper_consistency=True,
                plan_alignment=0.8,
                reasoning="VLM says plausible",
            ),
        }
        defaults.update(overrides)
        return VerificationResult(**defaults)

    def test_joint_limits_override_bounds_check(self):
        v = self._make_verification()
        dyn = {"joint_limits_ok": False, "self_collision": False}
        result = EvaluationPipeline._apply_dynamics_sanity_checks(v, dyn)
        assert result.action_plausibility.bounds_check is False
        assert result.action_plausibility.plan_alignment <= 0.3

    def test_self_collision_overrides_bounds_check(self):
        v = self._make_verification()
        dyn = {"joint_limits_ok": True, "self_collision": True}
        result = EvaluationPipeline._apply_dynamics_sanity_checks(v, dyn)
        assert result.action_plausibility.bounds_check is False

    def test_high_displacement_overrides_smoothness(self):
        v = self._make_verification()
        dyn = {"joint_limits_ok": True, "total_displacement_m": 28.0}
        result = EvaluationPipeline._apply_dynamics_sanity_checks(v, dyn)
        assert result.action_plausibility.smoothness is False
        assert result.action_plausibility.plan_alignment <= 0.3

    def test_multiple_failures_cap_confidence(self):
        v = self._make_verification(confidence=0.85)
        dyn = {"joint_limits_ok": False, "total_displacement_m": 28.0}
        result = EvaluationPipeline._apply_dynamics_sanity_checks(v, dyn)
        assert result.confidence <= 0.4
        assert result.action_plausibility.plan_alignment <= 0.3

    def test_no_dynamics_leaves_verification_unchanged(self):
        """When no dynamics analysis, the method should not be called — but if it is
        with empty data, nothing should change."""
        v = self._make_verification()
        dyn = {"joint_limits_ok": True, "self_collision": False, "total_displacement_m": 0.1}
        result = EvaluationPipeline._apply_dynamics_sanity_checks(v, dyn)
        assert result.action_plausibility.bounds_check is True
        assert result.action_plausibility.smoothness is True
        assert result.action_plausibility.plan_alignment == 0.8
        assert result.confidence == 0.85

    def test_dynamics_override_appends_reasoning(self):
        v = self._make_verification()
        dyn = {"joint_limits_ok": False}
        result = EvaluationPipeline._apply_dynamics_sanity_checks(v, dyn)
        assert "[Dynamics override:" in result.action_plausibility.reasoning
        assert "VLM says plausible" in result.action_plausibility.reasoning

    def test_no_plausibility_returns_unchanged(self):
        """If there's no action_plausibility, verification passes through."""
        v = self._make_verification(action_plausibility=None)
        dyn = {"joint_limits_ok": False}
        result = EvaluationPipeline._apply_dynamics_sanity_checks(v, dyn)
        assert result.action_plausibility is None
        assert result.confidence == 0.85

    def test_torque_violation_overrides_bounds_check(self):
        v = self._make_verification()
        dyn = {"joint_limits_ok": True, "torque_feasible": False}
        result = EvaluationPipeline._apply_dynamics_sanity_checks(v, dyn)
        assert result.action_plausibility.bounds_check is False
        assert result.action_plausibility.plan_alignment <= 0.3

    def test_near_singularity_caps_plan_alignment(self):
        v = self._make_verification()
        dyn = {"joint_limits_ok": True, "near_singularity": True}
        result = EvaluationPipeline._apply_dynamics_sanity_checks(v, dyn)
        assert result.action_plausibility.plan_alignment <= 0.3

    def test_gravity_infeasible_overrides_bounds_check(self):
        v = self._make_verification()
        dyn = {"joint_limits_ok": True, "gravity_feasible": False}
        result = EvaluationPipeline._apply_dynamics_sanity_checks(v, dyn)
        assert result.action_plausibility.bounds_check is False
