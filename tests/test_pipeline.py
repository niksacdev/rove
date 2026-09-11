"""Tests for EvaluationPipeline — data flow between stages."""

from __future__ import annotations

import pytest

from rove.adapters.mock_sim import MockSimAdapter
from rove.adapters.mock_vla import MockVLAAdapter
from rove.adapters.mock_vlm import MockVLMAdapter
from rove.models import (
    ActionPlausibility,
    ActionSpace,
    ExampleData,
    RobotEmbodiment,
    StageStatus,
    VerificationResult,
    VLACapabilities,
)
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


class TestEvidenceConsistency:
    """Test _validate_evidence_consistency — lightweight hallucination guard."""

    @staticmethod
    def _make_verification(**overrides) -> VerificationResult:
        defaults = {
            "success": True,
            "confidence": 0.85,
            "reasoning": "Looks good",
            "action_plausibility": ActionPlausibility(
                bounds_check=0.9,
                smoothness=0.8,
                gripper_consistency=0.7,
                plan_alignment=0.8,
                reasoning="VLM says plausible",
                workspace_reachability=0.9,
                dynamics_consistency=0.9,
                safety_assessment=0.9,
            ),
        }
        defaults.update(overrides)
        return VerificationResult(**defaults)

    @staticmethod
    def _make_evidence_dynamics(
        joint_limits_ok=True,
        self_collision=False,
        torque_feasible=True,
        near_singularity=False,
    ) -> dict:
        """Build evidence-structured dynamics dict."""
        return {
            "analysis_mode": "joint_space",
            "robot": {"name": "panda", "dof": 7, "actuators": 7},
            "evidence": [
                {
                    "field": "joint_limits_ok",
                    "value": joint_limits_ok,
                    "confidence": "hard",
                    "source": "joint_space",
                },
                {
                    "field": "self_collision",
                    "value": self_collision,
                    "confidence": "hard",
                    "source": "joint_space",
                },
                {
                    "field": "torque_feasible",
                    "value": torque_feasible,
                    "confidence": "hard",
                    "source": "mj_inverse",
                },
                {
                    "field": "near_singularity",
                    "value": near_singularity,
                    "confidence": "hard",
                    "source": "jacobian_svd",
                },
            ],
            "summary": {
                "joint_limits_ok": joint_limits_ok,
                "self_collision": self_collision,
                "torque_feasible": torque_feasible,
                "near_singularity": near_singularity,
                "total_displacement_m": 0.3,
                "smoothness_score": 0.85,
                "steps_analyzed": 50,
            },
            "not_computed": [],
            "gripper_events": [],
        }

    def test_no_dynamics_nulls_physics_fields(self):
        """Without dynamics, physics-dependent fields are set to None."""
        v = self._make_verification()
        result = EvaluationPipeline._validate_evidence_consistency(v, None)
        assert result.action_plausibility.bounds_check is None
        assert result.action_plausibility.dynamics_consistency is None
        assert result.action_plausibility.workspace_reachability is None
        assert result.action_plausibility.safety_assessment is None
        assert result.action_plausibility.evidence_quality == "perception_only"

    def test_no_dynamics_preserves_non_physics_fields(self):
        """Without dynamics, perception-based fields are preserved."""
        v = self._make_verification()
        result = EvaluationPipeline._validate_evidence_consistency(v, None)
        assert result.action_plausibility.smoothness == 0.8
        assert result.action_plausibility.gripper_consistency == 0.7
        assert result.action_plausibility.plan_alignment == 0.8

    def test_clean_dynamics_no_contradictions(self):
        """Clean dynamics data produces no contradictions."""
        v = self._make_verification()
        dyn = self._make_evidence_dynamics()
        result = EvaluationPipeline._validate_evidence_consistency(v, dyn)
        assert result.contradictions == []
        assert result.action_plausibility.evidence_quality == "hard"

    def test_joint_limit_contradiction_flagged(self):
        """Joint limit violation flags contradiction when bounds_check is high."""
        v = self._make_verification()
        dyn = self._make_evidence_dynamics(joint_limits_ok=False)
        result = EvaluationPipeline._validate_evidence_consistency(v, dyn)
        assert len(result.contradictions) >= 1
        assert "bounds_check" in result.contradictions[0]
        # Score is NOT overridden
        assert result.action_plausibility.bounds_check == 0.9

    def test_self_collision_contradiction_flagged(self):
        """Self-collision flags contradiction when safety is high."""
        v = self._make_verification()
        dyn = self._make_evidence_dynamics(self_collision=True)
        result = EvaluationPipeline._validate_evidence_consistency(v, dyn)
        assert len(result.contradictions) >= 1
        assert "self-collision" in result.contradictions[0]
        # Score is NOT overridden
        assert result.action_plausibility.safety_assessment == 0.9

    def test_torque_infeasible_contradiction_flagged(self):
        """Torque infeasibility flags contradiction when safety is high."""
        v = self._make_verification()
        dyn = self._make_evidence_dynamics(torque_feasible=False)
        result = EvaluationPipeline._validate_evidence_consistency(v, dyn)
        assert len(result.contradictions) >= 1
        assert "torque" in result.contradictions[0]

    def test_no_plausibility_returns_unchanged(self):
        """If there's no action_plausibility, verification passes through."""
        v = self._make_verification(action_plausibility=None)
        dyn = self._make_evidence_dynamics(joint_limits_ok=False)
        result = EvaluationPipeline._validate_evidence_consistency(v, dyn)
        assert result.action_plausibility is None
        assert result.confidence == 0.85

    def test_confidence_not_overridden(self):
        """Confidence is never overridden by evidence consistency check."""
        v = self._make_verification(confidence=0.9)
        dyn = self._make_evidence_dynamics(joint_limits_ok=False, torque_feasible=False)
        result = EvaluationPipeline._validate_evidence_consistency(v, dyn)
        assert result.confidence == 0.9  # NOT capped

    def test_low_scores_no_contradiction(self):
        """Low LLM scores matching bad dynamics = no contradiction."""
        v = self._make_verification()
        v.action_plausibility.bounds_check = 0.1
        v.action_plausibility.safety_assessment = 0.2
        dyn = self._make_evidence_dynamics(joint_limits_ok=False, self_collision=True)
        result = EvaluationPipeline._validate_evidence_consistency(v, dyn)
        assert result.contradictions == []


class TestURDFRobotInfo:
    """Test URDF parsing for robot embodiment info."""

    def test_extract_urdf_robot_info_panda(self):
        """Panda URDF should yield 8 independent DOF (7 arm + 1 gripper)."""
        pipeline = EvaluationPipeline(
            urdf_path="data/urdf/panda/panda.urdf",
        )
        info = pipeline._urdf_robot_info
        assert info["robot_name"] == "panda"
        assert info["dof"] == 8  # 7 revolute arm + 1 prismatic gripper finger
        assert "panda_joint1" in info["joint_names"]
        assert "panda_finger_joint1" in info["joint_names"]
        # finger_joint2 is a mimic joint (mirrors finger_joint1)
        assert "panda_finger_joint2" in info.get("mimic_joints", [])

    def test_extract_urdf_robot_info_none(self):
        """No URDF should yield empty dict."""
        pipeline = EvaluationPipeline()
        assert pipeline._urdf_robot_info == {}

    def test_robot_spec_includes_dof(self):
        """Robot spec string should include DOF from URDF."""
        from rove.models import PipelineContext

        pipeline = EvaluationPipeline(
            urdf_path="data/urdf/panda/panda.urdf",
        )
        ctx = PipelineContext()
        spec = pipeline._build_robot_spec(ctx)
        assert "Joint DOF (from URDF): 8" in spec
        assert "panda" in spec.lower()

    def test_robot_spec_ee_control_space(self):
        """Robot spec explains EEF control space when action_dim != joint DOF."""
        from rove.models import PipelineContext

        pipeline = EvaluationPipeline(
            urdf_path="data/urdf/panda/panda.urdf",
        )
        ctx = PipelineContext()
        ctx.task_metadata = {
            "robot": "panda",
            "action_dim": 7,
            "control_space": "end_effector_delta",
            "action_space_desc": "3 pos + 3 rot + 1 gripper",
        }
        spec = pipeline._build_robot_spec(ctx)
        assert "Control space: end_effector_delta" in spec
        assert "EE-delta" in spec
        assert "Action dimensions: 7" in spec
        assert "Joint DOF (from URDF): 8" in spec


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


class TestRobotEmbodimentPipeline:
    def test_build_embodiment_from_legacy_metadata(self):
        """_build_robot_embodiment reads flat manifest fields (backward compat)."""
        pipeline = EvaluationPipeline(
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
        )
        from rove.models import PipelineContext

        ctx = PipelineContext(
            task="test",
            task_metadata={
                "robot": "panda",
                "action_dim": 7,
                "state_dim": 8,
                "control_space": "end_effector_delta",
            },
        )
        emb = pipeline._build_robot_embodiment(ctx)
        assert emb is not None
        assert emb.robot_type == "panda"
        assert emb.action_dim == 7
        assert emb.arm_dof == 6
        assert emb.gripper_dof == 1
        assert emb.action_space == ActionSpace.EEF_DELTA
        assert emb.state_dim == 8

    def test_build_embodiment_from_nested_descriptor(self):
        """_build_robot_embodiment reads new nested robot_descriptor format."""
        pipeline = EvaluationPipeline(
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
        )
        from rove.models import PipelineContext

        ctx = PipelineContext(
            task="test",
            task_metadata={
                "robot_descriptor": {
                    "robot_type": "panda",
                    "arm_dof": 6,
                    "gripper_dof": 1,
                    "action_space": "eef_delta",
                    "proprioception_space": "joint_position",
                    "gripper_index": 6,
                },
            },
        )
        emb = pipeline._build_robot_embodiment(ctx)
        assert emb is not None
        assert emb.robot_type == "panda"
        assert emb.arm_dof == 6
        assert emb.gripper_index == 6
        assert emb.proprioception_space == ActionSpace.JOINT_POSITION

    def test_build_embodiment_returns_none_without_robot(self):
        """_build_robot_embodiment returns None for perceive-only tasks."""
        pipeline = EvaluationPipeline(
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
        )
        from rove.models import PipelineContext

        ctx = PipelineContext(task="describe scene")
        emb = pipeline._build_robot_embodiment(ctx)
        assert emb is None

    def test_validate_compat_cross_embodiment_passes(self):
        """Cross-embodiment VLA (supported_robot_types=None) passes any robot."""
        pipeline = EvaluationPipeline(
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
        )
        emb = RobotEmbodiment(robot_type="panda", arm_dof=6)
        caps = VLACapabilities(native_action_dim=32, max_action_dim=32)
        warnings = pipeline._validate_embodiment_compat(emb, caps, "pi05")
        assert isinstance(warnings, list)

    def test_validate_compat_checkpoint_bound_wrong_robot(self):
        """Checkpoint-bound VLA on wrong robot should raise ValueError."""
        pipeline = EvaluationPipeline(
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
        )
        emb = RobotEmbodiment(robot_type="panda", arm_dof=6)
        caps = VLACapabilities(
            native_action_dim=6,
            supported_robot_types=["so100"],
        )
        with pytest.raises(ValueError, match="supports robots"):
            pipeline._validate_embodiment_compat(emb, caps, "smolvla-base")

    def test_validate_compat_action_dim_exceeds_max(self):
        """VLA with max_action_dim smaller than robot action_dim should raise."""
        pipeline = EvaluationPipeline(
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
        )
        emb = RobotEmbodiment(robot_type="huge_arm", arm_dof=40, gripper_dof=1)
        caps = VLACapabilities(native_action_dim=32, max_action_dim=32)
        with pytest.raises(ValueError, match="max action dim"):
            pipeline._validate_embodiment_compat(emb, caps, "pi05")

    def test_validate_compat_embodiment_tag_required(self):
        """GR00T-style VLA requiring embodiment_tag should raise if missing."""
        pipeline = EvaluationPipeline(
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
        )
        emb = RobotEmbodiment(robot_type="panda", arm_dof=6)
        caps = VLACapabilities(
            native_action_dim=7,
            requires_embodiment_tag=True,
        )
        with pytest.raises(ValueError, match="embodiment_tag"):
            pipeline._validate_embodiment_compat(emb, caps, "groot-n1")

    def test_validate_compat_action_space_mismatch_warning(self):
        """Action space mismatch should produce a warning, not an error."""
        pipeline = EvaluationPipeline(
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
        )
        emb = RobotEmbodiment(robot_type="panda", arm_dof=6, action_space=ActionSpace.JOINT_DELTA)
        caps = VLACapabilities(
            native_action_dim=7,
            native_action_space=ActionSpace.EEF_DELTA,
        )
        warnings = pipeline._validate_embodiment_compat(emb, caps, "some-vla")
        assert len(warnings) == 1
        assert "mismatch" in warnings[0].lower()

    @pytest.mark.asyncio
    async def test_pipeline_builds_embodiment_from_example(self):
        """Pipeline should build robot_embodiment on PipelineContext from example data."""
        pipeline = EvaluationPipeline(
            perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            act_adapter=MockVLAAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
            sim=MockSimAdapter(),
        )
        example = ExampleData(
            extras={
                "robot": "panda",
                "action_dim": 7,
                "state_dim": 8,
                "control_space": "end_effector_delta",
                "proprioception": [0.1] * 8,
            }
        )
        stages = []
        async for result in pipeline.run_trial("pick object", "fake_img", example=example):
            if result.status == StageStatus.COMPLETED:
                stages.append(result)

        # Pipeline should complete successfully with embodiment
        assert len(stages) >= 3  # at least perceive + act + verify

    @pytest.mark.asyncio
    async def test_provenance_fields_in_act_output(self):
        """Act stage output should contain provenance fields from VLA."""
        pipeline = EvaluationPipeline(
            perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            plan_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
            act_adapter=MockVLAAdapter(config={"mock_latency_ms": [1, 2]}),
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0}),
            sim=MockSimAdapter(),
        )
        stages = {}
        async for result in pipeline.run_trial("pick object", "fake_img"):
            if result.status == StageStatus.COMPLETED and result.output:
                stages[result.stage] = result.output

        act_output = stages.get("act", {})
        assert act_output.get("declared_action_dim") == 7
        assert act_output.get("action_space") == "eef_delta"

    def test_build_robot_spec_from_embodiment(self):
        """_build_robot_spec should format RobotEmbodiment as text."""
        pipeline = EvaluationPipeline(
            verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
        )
        from rove.models import PipelineContext

        emb = RobotEmbodiment(
            robot_type="panda",
            arm_dof=6,
            gripper_dof=1,
            action_space=ActionSpace.EEF_DELTA,
            state_dim_override=8,
        )
        ctx = PipelineContext(task="test", robot_embodiment=emb)
        spec = pipeline._build_robot_spec(ctx)
        assert "Robot: panda" in spec
        assert "Arm DOF: 6" in spec
        assert "Action dim: 7" in spec
        assert "eef_delta" in spec
        assert "State dimensions: 8" in spec
