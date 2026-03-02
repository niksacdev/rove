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


class TestParallelPipeline:
    """Test parallel pipeline mode — VLA execution first, then evaluation."""

    @pytest.mark.asyncio
    async def test_parallel_mode_stage_order(self):
        """In parallel mode, act and perceive run concurrently; verify is always last."""
        pipeline = EvaluationPipeline(
            perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            plan_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            act_adapter=MockVLAAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
            sim=MockSimAdapter(),
            pipeline_mode="parallel",
            verify_mode="precompute",
        )
        completed = []
        async for result in pipeline.run_trial("pick red bracket", "fake_img"):
            if result.status in (StageStatus.COMPLETED, StageStatus.ERROR):
                completed.append(result)

        stage_names = [s.stage for s in completed]
        # Act and perceive run concurrently — order depends on timing
        assert set(stage_names) == {"act", "perceive", "plan", "verify"}
        # Plan must come after perceive (dependency), verify must be last
        assert stage_names.index("plan") > stage_names.index("perceive")
        assert stage_names[-1] == "verify"
        assert all(s.status == StageStatus.COMPLETED for s in completed)

    @pytest.mark.asyncio
    async def test_parallel_mode_phase_labels(self):
        """Parallel mode stages carry execution/evaluation phase labels."""
        pipeline = EvaluationPipeline(
            perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            plan_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            act_adapter=MockVLAAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
            sim=MockSimAdapter(),
            pipeline_mode="parallel",
            verify_mode="precompute",
        )
        completed = []
        async for result in pipeline.run_trial("pick red bracket", "fake_img"):
            if result.status in (StageStatus.COMPLETED, StageStatus.ERROR):
                completed.append(result)

        phases = {s.stage: s.phase for s in completed}
        assert phases["act"] == "execution"
        assert phases["perceive"] == "evaluation"
        assert phases["plan"] == "evaluation"
        assert phases["verify"] == "evaluation"

    @pytest.mark.asyncio
    async def test_parallel_mode_act_latency_excludes_dynamics(self):
        """In parallel mode, act latency should only be VLA time, not dynamics."""
        pipeline = EvaluationPipeline(
            perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            plan_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            act_adapter=MockVLAAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
            sim=MockSimAdapter(),
            pipeline_mode="parallel",
            verify_mode="precompute",
            compute_dynamics=True,
        )
        completed = []
        async for result in pipeline.run_trial("pick red bracket", "fake_img"):
            if result.status in (StageStatus.COMPLETED, StageStatus.ERROR):
                completed.append(result)

        act_result = next(s for s in completed if s.stage == "act")
        # Act output should NOT contain dynamics_analysis in parallel mode
        assert "dynamics_analysis" not in (act_result.output or {})

    @pytest.mark.asyncio
    async def test_parallel_mode_with_dynamics(self):
        """Parallel mode with compute_dynamics yields a separate dynamics stage."""
        pipeline = EvaluationPipeline(
            perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            plan_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            act_adapter=MockVLAAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
            sim=MockSimAdapter(),
            pipeline_mode="parallel",
            verify_mode="precompute",
            compute_dynamics=True,
        )
        completed = []
        async for result in pipeline.run_trial("pick red bracket", "fake_img"):
            if result.status in (StageStatus.COMPLETED, StageStatus.ERROR):
                completed.append(result)

        stage_names = [s.stage for s in completed]
        assert "dynamics" in stage_names
        dynamics_result = next(s for s in completed if s.stage == "dynamics")
        assert dynamics_result.phase == "evaluation"
        # Dynamics should complete (with skipped reason since no URDF in test)
        assert dynamics_result.status == StageStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_sequential_mode_no_phase_labels(self):
        """Sequential mode stages have empty phase labels."""
        pipeline = EvaluationPipeline(
            perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            plan_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            act_adapter=MockVLAAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
            sim=MockSimAdapter(),
            pipeline_mode="sequential",
            verify_mode="precompute",
        )
        completed = []
        async for result in pipeline.run_trial("pick red bracket", "fake_img"):
            if result.status in (StageStatus.COMPLETED, StageStatus.ERROR):
                completed.append(result)

        # All sequential stages should have empty phase
        for s in completed:
            assert s.phase == ""

    @pytest.mark.asyncio
    async def test_parallel_continues_after_perceive_error(self):
        """In parallel mode, evaluation continues to verify even if perceive fails."""

        class FailingVLM(MockVLMAdapter):
            async def analyze_scene(self, *args, **kwargs):
                raise RuntimeError("perceive exploded")

        pipeline = EvaluationPipeline(
            perceive_adapter=FailingVLM(config={"mock_latency_ms": [1, 2]}),
            act_adapter=MockVLAAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
            sim=MockSimAdapter(),
            pipeline_mode="parallel",
            verify_mode="precompute",
        )
        completed = []
        async for result in pipeline.run_trial("pick red bracket", "fake_img"):
            if result.status in (StageStatus.COMPLETED, StageStatus.ERROR):
                completed.append(result)

        stages = {s.stage: s.status for s in completed}
        assert stages["act"] == StageStatus.COMPLETED
        assert stages["perceive"] == StageStatus.ERROR
        # Verify should still run despite perceive failure
        assert stages["verify"] == StageStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_parallel_no_perceive_no_plan(self):
        """Parallel mode with only act + verify (no perceive/plan adapters)."""
        pipeline = EvaluationPipeline(
            act_adapter=MockVLAAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
            sim=MockSimAdapter(),
            pipeline_mode="parallel",
            verify_mode="precompute",
        )
        completed = []
        async for result in pipeline.run_trial("pick red bracket", "fake_img"):
            if result.status in (StageStatus.COMPLETED, StageStatus.ERROR):
                completed.append(result)

        stage_names = [s.stage for s in completed]
        assert stage_names == ["act", "verify"]


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
        assert result.action_plausibility.bounds_check == 0.0
        assert result.action_plausibility.plan_alignment <= 0.3

    def test_self_collision_overrides_bounds_check(self):
        v = self._make_verification()
        dyn = {"joint_limits_ok": True, "self_collision": True}
        result = EvaluationPipeline._apply_dynamics_sanity_checks(v, dyn)
        assert result.action_plausibility.bounds_check == 0.0

    def test_high_displacement_overrides_smoothness(self):
        v = self._make_verification()
        dyn = {"joint_limits_ok": True, "total_displacement_m": 28.0}
        result = EvaluationPipeline._apply_dynamics_sanity_checks(v, dyn)
        assert result.action_plausibility.smoothness == 0.0
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
        assert result.action_plausibility.bounds_check == 1.0
        assert result.action_plausibility.smoothness == 1.0
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
        assert result.action_plausibility.bounds_check == 0.0
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
        assert result.action_plausibility.bounds_check == 0.0

    def test_workspace_reachability_capped_by_failures(self):
        v = self._make_verification()
        dyn = {"joint_limits_ok": False, "self_collision": True, "total_displacement_m": 28.0}
        result = EvaluationPipeline._apply_dynamics_sanity_checks(v, dyn)
        # 3 failures → 1.0 - 3*0.25 = 0.25
        assert result.action_plausibility.workspace_reachability <= 0.25

    def test_safety_assessment_computed_from_signals(self):
        v = self._make_verification()
        dyn = {
            "joint_limits_ok": True,
            "torque_feasible": False,
            "self_collision": True,
            "near_singularity": True,
            "gravity_feasible": False,
        }
        result = EvaluationPipeline._apply_dynamics_sanity_checks(v, dyn)
        # 4 safety penalties → 1.0 - 4*0.3 = 0.0 (clamped)
        assert result.action_plausibility.safety_assessment == 0.0

    def test_safety_assessment_partial_penalties(self):
        v = self._make_verification()
        dyn = {"joint_limits_ok": True, "torque_feasible": False, "near_singularity": True}
        result = EvaluationPipeline._apply_dynamics_sanity_checks(v, dyn)
        # 2 safety penalties → 1.0 - 2*0.3 = 0.4
        assert result.action_plausibility.safety_assessment == pytest.approx(0.4, abs=0.01)

    def test_dynamics_consistency_decreases_with_overrides(self):
        v = self._make_verification()
        dyn = {"joint_limits_ok": False, "torque_feasible": False, "near_singularity": True}
        result = EvaluationPipeline._apply_dynamics_sanity_checks(v, dyn)
        assert result.action_plausibility.dynamics_consistency < 1.0

    def test_task_completion_plausibility_not_overridden(self):
        """task_completion_plausibility should NOT be modified by dynamics checks."""
        v = self._make_verification()
        v.action_plausibility.task_completion_plausibility = 0.7
        dyn = {"joint_limits_ok": False, "torque_feasible": False}
        result = EvaluationPipeline._apply_dynamics_sanity_checks(v, dyn)
        assert result.action_plausibility.task_completion_plausibility == 0.7

    def test_clean_dynamics_leaves_extended_fields_unchanged(self):
        """No dynamics failures should not reduce extended fields."""
        v = self._make_verification()
        dyn = {"joint_limits_ok": True, "self_collision": False, "total_displacement_m": 0.1}
        result = EvaluationPipeline._apply_dynamics_sanity_checks(v, dyn)
        assert result.action_plausibility.workspace_reachability == 1.0
        assert result.action_plausibility.safety_assessment == 1.0
        assert result.action_plausibility.dynamics_consistency == 1.0


class TestVerifyLoop:
    """Test agentic verify loop with mock adapters."""

    @pytest.mark.asyncio
    async def test_verify_loop_requests_tools(self):
        """In agent_loop mode, mock adapter returns function_calls on turn 0,
        tools execute, final verdict on turn 1."""
        pipeline = EvaluationPipeline(
            perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            act_adapter=MockVLAAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
            sim=MockSimAdapter(),
            pipeline_mode="parallel",
            verify_mode="agent_loop",
        )
        all_results = []
        async for result in pipeline.run_trial("pick red bracket", "fake_img"):
            all_results.append(result)

        # Should have act + verify stages (perceive is a verify sub-step, not a separate stage)
        completed = [r for r in all_results if r.status == StageStatus.COMPLETED]
        completed_stages = [r.stage for r in completed]
        assert "act" in completed_stages
        assert "verify" in completed_stages
        # perceive should NOT appear as a separate completed stage
        assert "perceive" not in completed_stages
        # plan should NOT appear as a separate completed stage
        assert "plan" not in completed_stages

        # Verify output should have verify_turns > 1 (tool call + final)
        verify_result = next(r for r in completed if r.stage == "verify")
        assert verify_result.output is not None
        assert verify_result.output.get("verify_turns", 1) >= 1

        # Should have sub-step RUNNING events for verify
        verify_running = [
            r
            for r in all_results
            if r.stage == "verify" and r.status == StageStatus.RUNNING and r.output
        ]
        substep_events = [r for r in verify_running if r.output.get("substep")]
        assert len(substep_events) >= 1  # at least a turn or tool sub-step

    @pytest.mark.asyncio
    async def test_verify_loop_max_turns(self):
        """Verify loop respects max turn limit."""
        pipeline = EvaluationPipeline(
            perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            act_adapter=MockVLAAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
            sim=MockSimAdapter(),
            pipeline_mode="parallel",
            verify_mode="agent_loop",
        )
        completed = []
        async for result in pipeline.run_trial("pick red bracket", "fake_img"):
            if result.status == StageStatus.COMPLETED:
                completed.append(result)

        verify_result = next(r for r in completed if r.stage == "verify")
        # verify_turns should be <= 4 (max_turns default)
        assert verify_result.output.get("verify_turns", 1) <= 4

    @pytest.mark.asyncio
    async def test_verify_loop_no_tools_single_call(self):
        """When no tools available, verify loop does a single call."""
        pipeline = EvaluationPipeline(
            # No perceive adapter, no dynamics → no tools available
            act_adapter=MockVLAAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
            sim=MockSimAdapter(),
            pipeline_mode="parallel",
            verify_mode="agent_loop",
        )
        completed = []
        async for result in pipeline.run_trial("pick red bracket", "fake_img"):
            if result.status == StageStatus.COMPLETED:
                completed.append(result)

        verify_result = next(r for r in completed if r.stage == "verify")
        assert verify_result.output is not None
        # With no tools, should be 1 turn
        assert verify_result.output.get("verify_turns", 1) == 1

    @pytest.mark.asyncio
    async def test_precompute_mode_unchanged(self):
        """Precompute mode still runs perceive/plan/dynamics as separate stages."""
        pipeline = EvaluationPipeline(
            perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            plan_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            act_adapter=MockVLAAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
            sim=MockSimAdapter(),
            pipeline_mode="parallel",
            verify_mode="precompute",
        )
        completed = []
        async for result in pipeline.run_trial("pick red bracket", "fake_img"):
            if result.status in (StageStatus.COMPLETED, StageStatus.ERROR):
                completed.append(result)

        stage_names = [s.stage for s in completed]
        # Precompute should have separate perceive and plan stages
        assert "perceive" in stage_names
        assert "plan" in stage_names
        assert "act" in stage_names
        assert "verify" in stage_names

    @pytest.mark.asyncio
    async def test_verify_loop_resolution_path(self):
        """Verify loop should produce resolution_path in output."""
        pipeline = EvaluationPipeline(
            perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            act_adapter=MockVLAAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
            sim=MockSimAdapter(),
            pipeline_mode="parallel",
            verify_mode="agent_loop",
        )
        verify_output = None
        async for result in pipeline.run_trial("pick red bracket", "fake_img"):
            if result.status == StageStatus.COMPLETED and result.stage == "verify":
                verify_output = result.output

        assert verify_output is not None
        # Mock adapter should return resolution_path
        assert "resolution_path" in verify_output

    @pytest.mark.asyncio
    async def test_auto_mode_resolves_to_precompute_for_sequential(self):
        """Auto verify_mode should resolve to precompute for sequential pipelines."""
        pipeline = EvaluationPipeline(
            perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            plan_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
            pipeline_mode="sequential",
            verify_mode="auto",
        )
        assert pipeline._resolve_verify_mode() == "precompute"

    @pytest.mark.asyncio
    async def test_auto_mode_resolves_to_agent_loop_for_parallel(self):
        """Auto verify_mode should resolve to agent_loop for parallel with act adapter."""
        pipeline = EvaluationPipeline(
            perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            act_adapter=MockVLAAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
            pipeline_mode="parallel",
            verify_mode="auto",
        )
        assert pipeline._resolve_verify_mode() == "agent_loop"
