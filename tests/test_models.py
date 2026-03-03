"""Tests for rove.models — data model serialization and construction."""

from __future__ import annotations

from rove.models import (
    ActionPlausibility,
    ActionPrediction,
    ActionSpace,
    PipelineContext,
    PipelineStage,
    PipelineStageResult,
    RobotEmbodiment,
    SceneAnalysis,
    StageStatus,
    Strategy,
    TaskPlan,
    VerificationResult,
    VLACapabilities,
)
from rove.models.config import DefaultsConfig, StrategyConfig


class TestPipelineContext:
    def test_empty_to_dict(self):
        ctx = PipelineContext(task="pick object")
        d = ctx.to_dict()
        assert d["task"] == "pick object"
        assert "scene" not in d
        assert "plan" not in d

    def test_with_scene(self):
        ctx = PipelineContext(task="test")
        ctx.scene = SceneAnalysis(
            objects=[{"name": "bolt"}],
            spatial_relations=["bolt is on table"],
            task_relevant=["bolt"],
        )
        d = ctx.to_dict()
        assert d["scene"]["task_relevant"] == ["bolt"]
        assert d["scene"]["objects"] == [{"name": "bolt"}]

    def test_with_scene_position(self):
        ctx = PipelineContext(task="test")
        ctx.scene = SceneAnalysis(
            objects=[
                {
                    "name": "bolt",
                    "bbox": [10, 20, 30, 40],
                    "position": [0.25, -0.10, 0.32],
                    "confidence": 0.9,
                }
            ],
            spatial_relations=["bolt is on table"],
            task_relevant=["bolt"],
        )
        d = ctx.to_dict()
        obj = d["scene"]["objects"][0]
        assert obj["position"] == [0.25, -0.10, 0.32]
        assert obj["name"] == "bolt"

    def test_with_plan(self):
        ctx = PipelineContext(task="test")
        ctx.plan = TaskPlan(
            strategy="top-down",
            reasoning="clear path",
            steps=["approach", "grasp"],
            target_object="bolt",
            confidence=0.9,
        )
        d = ctx.to_dict()
        assert d["plan"]["target_object"] == "bolt"
        assert d["plan"]["steps"] == ["approach", "grasp"]

    def test_with_action(self):
        ctx = PipelineContext(task="test")
        ctx.action = ActionPrediction(action_type="trajectory", num_steps=3, confidence=0.8)
        d = ctx.to_dict()
        assert d["action"]["action_type"] == "trajectory"

    def test_proprioception_included(self):
        ctx = PipelineContext(task="test", proprioception=[0.1, 0.2, 0.3])
        d = ctx.to_dict()
        assert d["proprioception"] == [0.1, 0.2, 0.3]

    def test_proprioception_excluded_when_empty(self):
        ctx = PipelineContext(task="test")
        d = ctx.to_dict()
        assert "proprioception" not in d


class TestStrategy:
    def test_construction(self):
        s = Strategy(
            id="test",
            display_name="Test",
            description="desc",
            perceive="vlm-1",
            plan="vlm-1",
            act="vla-1",
            verify="vlm-1",
            sim="sim-1",
        )
        assert s.id == "test"
        assert s.tags == []
        assert s.pipeline_mode == "sequential"

    def test_with_tags(self):
        s = Strategy(
            id="x",
            display_name="X",
            description="",
            perceive="a",
            plan="b",
            act="c",
            verify="d",
            sim="e",
            tags=["fast", "cloud"],
        )
        assert s.tags == ["fast", "cloud"]

    def test_pipeline_mode_parallel(self):
        s = Strategy(
            id="vla",
            display_name="VLA",
            description="",
            act="vla-1",
            verify="vlm-1",
            pipeline_mode="parallel",
        )
        assert s.pipeline_mode == "parallel"

    def test_verify_mode_default(self):
        s = Strategy(id="x", display_name="X", description="", verify="vlm-1")
        assert s.verify_mode == "auto"

    def test_verify_mode_agent_loop(self):
        s = Strategy(
            id="x",
            display_name="X",
            description="",
            act="vla-1",
            verify="vlm-1",
            pipeline_mode="parallel",
            verify_mode="agent_loop",
        )
        assert s.verify_mode == "agent_loop"


class TestPipelineStage:
    def test_dynamics_enum(self):
        assert PipelineStage.DYNAMICS == "dynamics"
        assert "dynamics" in [s.value for s in PipelineStage]

    def test_all_stages(self):
        stages = [s.value for s in PipelineStage]
        assert stages == ["perceive", "plan", "act", "dynamics", "verify"]


class TestPipelineStageResult:
    def test_phase_default_empty(self):
        r = PipelineStageResult(stage="perceive", status=StageStatus.RUNNING)
        assert r.phase == ""

    def test_phase_execution(self):
        r = PipelineStageResult(stage="act", status=StageStatus.RUNNING, phase="execution")
        assert r.phase == "execution"

    def test_phase_evaluation(self):
        r = PipelineStageResult(stage="dynamics", status=StageStatus.COMPLETED, phase="evaluation")
        assert r.phase == "evaluation"

    def test_phase_in_model_dump(self):
        r = PipelineStageResult(stage="act", status=StageStatus.COMPLETED, phase="execution")
        d = r.model_dump()
        assert d["phase"] == "execution"


class TestDefaultsConfig:
    def test_latency_budget_default(self):
        d = DefaultsConfig()
        assert d.latency_budget_ms == 10000

    def test_latency_budget_custom(self):
        d = DefaultsConfig(latency_budget_ms=5000)
        assert d.latency_budget_ms == 5000

    def test_latency_budget_per_stage_defaults(self):
        d = DefaultsConfig()
        assert d.latency_budget == {
            "perceive": 3000,
            "plan": 3000,
            "act": 3000,
            "verify": 1000,
        }

    def test_latency_budget_per_stage_custom(self):
        d = DefaultsConfig(
            latency_budget={"perceive": 1000, "plan": 1000, "act": 5000, "verify": 500}
        )
        assert d.latency_budget["act"] == 5000


class TestStrategyConfig:
    def test_latency_budget_override_empty_by_default(self):
        s = StrategyConfig(verify="mock-vlm", sim="mock-sim", perceive="mock-vlm")
        assert s.latency_budget == {}

    def test_latency_budget_partial_override(self):
        s = StrategyConfig(
            verify="mock-vlm",
            sim="mock-sim",
            perceive="mock-vlm",
            latency_budget={"act": 8000},
        )
        assert s.latency_budget == {"act": 8000}
        assert "perceive" not in s.latency_budget


class TestActionPlausibility:
    def test_defaults(self):
        ap = ActionPlausibility()
        # All score fields default to None (not assessed)
        assert ap.bounds_check is None
        assert ap.smoothness is None
        assert ap.gripper_consistency is None
        assert ap.plan_alignment is None
        assert ap.reasoning == ""
        assert ap.workspace_reachability is None
        assert ap.task_completion_plausibility is None
        assert ap.dynamics_consistency is None
        assert ap.safety_assessment is None
        assert ap.evidence_quality == ""

    def test_custom_values(self):
        ap = ActionPlausibility(
            bounds_check=0.0,
            smoothness=1.0,
            gripper_consistency=0.0,
            plan_alignment=0.75,
            reasoning="Bounds exceeded at step 3",
        )
        assert ap.bounds_check == 0.0
        assert ap.plan_alignment == 0.75
        assert "step 3" in ap.reasoning

    def test_accepts_float_scores(self):
        """LLMs return float scores (0-1) instead of booleans."""
        ap = ActionPlausibility(
            bounds_check=0.5,
            smoothness=0.2,
            gripper_consistency=0.3,
        )
        assert ap.bounds_check == 0.5
        assert ap.smoothness == 0.2
        assert ap.gripper_consistency == 0.3

    def test_verification_result_with_plausibility(self):
        ap = ActionPlausibility(plan_alignment=0.8, reasoning="Good alignment")
        vr = VerificationResult(
            success=True,
            confidence=0.9,
            reasoning="Plausible",
            action_plausibility=ap,
        )
        assert vr.action_plausibility is not None
        assert vr.action_plausibility.plan_alignment == 0.8

    def test_verification_result_without_plausibility(self):
        vr = VerificationResult(success=True, confidence=0.9, reasoning="OK")
        assert vr.action_plausibility is None

    def test_verification_result_resolution_path(self):
        vr = VerificationResult(
            success=False,
            confidence=0.4,
            reasoning="Issues found",
            resolution_path="Retrain VLA with tighter joint limits",
        )
        assert vr.resolution_path == "Retrain VLA with tighter joint limits"

    def test_verification_result_verify_turns(self):
        vr = VerificationResult(
            success=True,
            confidence=0.9,
            reasoning="OK",
            verify_turns=2,
        )
        assert vr.verify_turns == 2

    def test_verification_result_defaults(self):
        vr = VerificationResult(success=True, confidence=0.9, reasoning="OK")
        assert vr.resolution_path == ""
        assert vr.verify_turns == 1

    def test_extended_fields(self):
        ap = ActionPlausibility(
            workspace_reachability=0.75,
            task_completion_plausibility=0.6,
            dynamics_consistency=0.5,
            safety_assessment=0.4,
        )
        assert ap.workspace_reachability == 0.75
        assert ap.task_completion_plausibility == 0.6
        assert ap.dynamics_consistency == 0.5
        assert ap.safety_assessment == 0.4


class TestActionSpace:
    def test_enum_values(self):
        assert ActionSpace.JOINT_POSITION == "joint_position"
        assert ActionSpace.JOINT_DELTA == "joint_delta"
        assert ActionSpace.EEF_DELTA == "eef_delta"
        assert ActionSpace.EEF_ABSOLUTE == "eef_absolute"

    def test_from_string(self):
        assert ActionSpace("eef_delta") == ActionSpace.EEF_DELTA
        assert ActionSpace("joint_position") == ActionSpace.JOINT_POSITION


class TestRobotEmbodiment:
    def test_construction(self):
        emb = RobotEmbodiment(robot_type="panda", arm_dof=6)
        assert emb.robot_type == "panda"
        assert emb.arm_dof == 6
        assert emb.gripper_dof == 1
        assert emb.action_space == ActionSpace.EEF_DELTA

    def test_action_dim_property(self):
        emb = RobotEmbodiment(robot_type="panda", arm_dof=6, gripper_dof=1)
        assert emb.action_dim == 7

    def test_action_dim_no_gripper(self):
        emb = RobotEmbodiment(robot_type="suction_bot", arm_dof=6, gripper_dof=0)
        assert emb.action_dim == 6

    def test_state_dim_default(self):
        emb = RobotEmbodiment(robot_type="panda", arm_dof=6)
        assert emb.state_dim == 7  # same as action_dim

    def test_state_dim_override(self):
        emb = RobotEmbodiment(robot_type="panda", arm_dof=6, state_dim_override=8)
        assert emb.state_dim == 8

    def test_all_fields(self):
        emb = RobotEmbodiment(
            robot_type="panda",
            arm_dof=6,
            gripper_dof=1,
            action_space=ActionSpace.EEF_DELTA,
            proprioception_space=ActionSpace.JOINT_POSITION,
            gripper_index=6,
            state_dim_override=8,
            urdf_path="/path/to/panda.urdf",
            embodiment_tag="franka_panda",
        )
        assert emb.gripper_index == 6
        assert emb.urdf_path == "/path/to/panda.urdf"
        assert emb.embodiment_tag == "franka_panda"


class TestVLACapabilities:
    def test_construction(self):
        caps = VLACapabilities(native_action_dim=7)
        assert caps.native_action_dim == 7
        assert caps.native_action_space == ActionSpace.EEF_DELTA
        assert caps.supported_robot_types is None
        assert caps.max_action_dim is None
        assert caps.requires_embodiment_tag is False

    def test_cross_embodiment(self):
        caps = VLACapabilities(
            native_action_dim=32,
            max_action_dim=32,
            supported_robot_types=None,
        )
        assert caps.supported_robot_types is None

    def test_checkpoint_bound(self):
        caps = VLACapabilities(
            native_action_dim=6,
            supported_robot_types=["so100"],
        )
        assert caps.supported_robot_types == ["so100"]

    def test_groot_style(self):
        caps = VLACapabilities(
            native_action_dim=7,
            requires_embodiment_tag=True,
        )
        assert caps.requires_embodiment_tag is True


class TestActionPredictionProvenance:
    def test_provenance_defaults_none(self):
        ap = ActionPrediction()
        assert ap.declared_action_dim is None
        assert ap.action_space is None
        assert ap.gripper_index is None
        assert ap.raw_action_dim is None

    def test_provenance_set(self):
        ap = ActionPrediction(
            actions=[[0.1] * 7],
            declared_action_dim=7,
            action_space=ActionSpace.EEF_DELTA,
            gripper_index=6,
            raw_action_dim=32,
        )
        assert ap.declared_action_dim == 7
        assert ap.action_space == ActionSpace.EEF_DELTA
        assert ap.gripper_index == 6
        assert ap.raw_action_dim == 32

    def test_provenance_in_model_dump(self):
        ap = ActionPrediction(
            declared_action_dim=7,
            action_space=ActionSpace.EEF_DELTA,
        )
        d = ap.model_dump()
        assert d["declared_action_dim"] == 7
        assert d["action_space"] == "eef_delta"


class TestPipelineContextRobotEmbodiment:
    def test_default_none(self):
        ctx = PipelineContext(task="test")
        assert ctx.robot_embodiment is None

    def test_with_embodiment(self):
        emb = RobotEmbodiment(robot_type="panda", arm_dof=6)
        ctx = PipelineContext(task="test", robot_embodiment=emb)
        assert ctx.robot_embodiment is not None
        assert ctx.robot_embodiment.robot_type == "panda"
