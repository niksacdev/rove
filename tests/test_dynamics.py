"""Tests for MuJoCo dynamics computation and integration."""

from __future__ import annotations

import pytest

from rove.models.config import StrategyConfig, get_strategies
from rove.models.core import PipelineContext, Strategy, VerificationResult
from rove.utils.prompt_loader import get_prompt_manager


class TestStrategyDynamicsConfig:
    """Test compute_dynamics field on strategy config."""

    def test_strategy_config_default_false(self):
        sc = StrategyConfig(verify="mock-vlm", sim="mock-sim", perceive="mock-vlm")
        assert sc.compute_dynamics is False

    def test_strategy_config_explicit_true(self):
        sc = StrategyConfig(
            verify="mock-vlm", sim="mock-sim", perceive="mock-vlm", compute_dynamics=True
        )
        assert sc.compute_dynamics is True

    def test_strategies_from_yaml_have_dynamics(self):
        strategies = get_strategies()
        # mock strategy has compute_dynamics: true in rove.yaml
        assert strategies["mock"].compute_dynamics is True
        # scene_detect has no act stage and no dynamics field
        assert strategies["scene_detect"].compute_dynamics is False

    def test_strategy_model_has_dynamics_field(self):
        s = Strategy(
            id="test",
            display_name="Test",
            description="",
            perceive="mock-vlm",
            act="mock-vla",
            verify="mock-vlm",
            sim="mock-sim",
            compute_dynamics=True,
        )
        assert s.compute_dynamics is True
        d = s.model_dump()
        assert d["compute_dynamics"] is True


class TestPipelineContextDynamics:
    """Test dynamics_analysis on PipelineContext."""

    def test_dynamics_analysis_default_none(self):
        ctx = PipelineContext(task="test")
        assert ctx.dynamics_analysis is None

    def test_dynamics_analysis_in_to_dict(self):
        ctx = PipelineContext(task="test")
        ctx.dynamics_analysis = {"final_endpoint": [0.3, 0.2, 0.4], "steps_analyzed": 5}
        d = ctx.to_dict()
        assert "dynamics_analysis" in d
        assert d["dynamics_analysis"]["steps_analyzed"] == 5

    def test_dynamics_analysis_excluded_when_none(self):
        ctx = PipelineContext(task="test")
        d = ctx.to_dict()
        assert "dynamics_analysis" not in d


class TestVerificationResultDynamics:
    """Test dynamics_analysis on VerificationResult."""

    def test_dynamics_analysis_field(self):
        vr = VerificationResult(
            success=True,
            confidence=0.9,
            reasoning="good",
            dynamics_analysis={"joint_limits_ok": True},
        )
        assert vr.dynamics_analysis["joint_limits_ok"] is True


class TestVerifyPromptDynamics:
    """Test dynamics injection in verify prompts."""

    def test_dynamics_in_verify_prompt(self):
        pm = get_prompt_manager()
        ctx = {
            "task": "pick bolt",
            "completed_stages": ["perceive", "act"],
            "scene": {
                "objects": [{"name": "bolt"}],
                "spatial_relations": [],
                "task_relevant": ["bolt"],
            },
            "action": {
                "action_type": "trajectory",
                "num_steps": 10,
                "confidence": 0.85,
                "actions": [
                    [0.02, -0.01, 0.05, 0.0, 0.1, -0.02, 0.8],
                    [0.01, -0.02, 0.03, 0.0, 0.0, -0.01, 0.8],
                ],
                "gripper_events": [{"step": 5, "action": "close", "grip_value": 0.0}],
            },
            "dynamics_analysis": {
                "final_endpoint": [0.340, 0.210, 0.460],
                "total_displacement_m": 0.34,
                "joint_limits_ok": True,
                "self_collision": False,
                "smoothness_score": 0.95,
                "max_velocity_rad_s": 1.2,
                "steps_analyzed": 10,
                "endpoint_trajectory": [
                    [0.3, 0.2, 0.4],
                    [0.32, 0.21, 0.44],
                    [0.34, 0.21, 0.46],
                ],
                "torque_feasible": True,
                "torque_violations": [],
                "peak_torques": [1.2, 0.8, 0.5, 0.3, 0.2, 0.1, 0.05],
                "gravity_torques": [0.5, 0.3, 0.2, 0.1, 0.05, 0.02, 0.01],
                "gravity_feasible": True,
                "payload_kg": 0.5,
                "manipulability": 0.0142,
                "min_singular_value": 0.05,
                "near_singularity": False,
            },
        }
        prompt = pm.render_verify("pick bolt", ctx)
        assert "MUJOCO DYNAMICS ANALYSIS" in prompt
        assert "0.340" in prompt
        assert "within bounds" in prompt
        assert "SPATIAL CONTEXT" in prompt
        assert "Torque feasibility" in prompt
        assert "Gravity compensation" in prompt
        assert "Manipulability" in prompt
        # Should NOT contain the "CANNOT determine" disclaimer
        assert "CANNOT determine" not in prompt

    def test_no_dynamics_shows_disclaimer(self):
        pm = get_prompt_manager()
        ctx = {
            "task": "pick bolt",
            "completed_stages": ["act"],
            "action": {
                "action_type": "trajectory",
                "num_steps": 5,
                "confidence": 0.85,
                "actions": [
                    [0.02, -0.01, 0.05, 0.0, 0.1, -0.02, 0.8],
                ],
                "gripper_events": [{"step": 3, "action": "close", "grip_value": 0.0}],
            },
        }
        prompt = pm.render_verify("pick bolt", ctx)
        assert "CANNOT determine" in prompt
        assert "MUJOCO DYNAMICS" not in prompt


class TestComputeDynamics:
    """Test the compute_dynamics function (requires mujoco)."""

    @pytest.fixture
    def panda_urdf(self):
        from pathlib import Path

        urdf = Path(__file__).parent.parent / "data" / "urdf" / "panda" / "panda.urdf"
        if not urdf.exists():
            pytest.skip("Panda URDF not available at data/urdf/panda/panda.urdf")
        return str(urdf)

    def test_compute_dynamics_basic(self, panda_urdf):
        mujoco = pytest.importorskip("mujoco")  # noqa: F841
        from rove.adapters.dynamics_mujoco import compute_dynamics

        actions = [
            [0.01, -0.01, 0.02, 0.0, 0.01, -0.01, 0.0],
            [0.02, -0.02, 0.01, 0.0, 0.02, -0.02, 0.0],
            [0.01, 0.0, 0.01, 0.0, 0.01, 0.0, 0.0],
        ]
        result = compute_dynamics(panda_urdf, actions)

        # Existing FK fields
        assert "endpoint_trajectory" in result
        assert "final_endpoint" in result
        assert "joint_limits_ok" in result
        assert "self_collision" in result
        assert "total_displacement_m" in result
        assert "smoothness_score" in result
        assert "steps_analyzed" in result
        assert result["steps_analyzed"] == 3
        assert len(result["endpoint_trajectory"]) == 4  # initial + 3 steps
        assert len(result["final_endpoint"]) == 3
        assert isinstance(result["smoothness_score"], float)
        assert 0.0 <= result["smoothness_score"] <= 1.0

        # New dynamics fields
        assert "torque_feasible" in result
        assert isinstance(result["torque_feasible"], bool)
        assert "torque_violations" in result
        assert isinstance(result["torque_violations"], list)
        assert "peak_torques" in result
        assert isinstance(result["peak_torques"], list)
        assert "gravity_torques" in result
        assert "gravity_feasible" in result
        assert isinstance(result["gravity_feasible"], bool)
        assert "payload_kg" in result
        assert result["payload_kg"] == 0.5
        assert "manipulability" in result
        assert isinstance(result["manipulability"], float)
        assert "min_singular_value" in result
        assert "near_singularity" in result
        assert isinstance(result["near_singularity"], bool)

    def test_compute_dynamics_empty_actions(self, panda_urdf):
        mujoco = pytest.importorskip("mujoco")  # noqa: F841
        from rove.adapters.dynamics_mujoco import compute_dynamics

        result = compute_dynamics(panda_urdf, [])
        assert result["steps_analyzed"] == 0
        assert len(result["endpoint_trajectory"]) == 1  # just initial position
        assert result["torque_feasible"] is True
        assert result["gravity_feasible"] is True
        assert result["near_singularity"] is False

    def test_compute_dynamics_file_not_found(self):
        """Test graceful error for missing URDF file."""
        pytest.importorskip("mujoco")
        from rove.adapters.dynamics_mujoco import compute_dynamics

        with pytest.raises(FileNotFoundError, match="not found"):
            compute_dynamics("/nonexistent/path/robot.urdf", [[0.1] * 7])

    def test_compute_dynamics_invalid_file(self, tmp_path):
        """Test graceful error for invalid URDF content."""
        pytest.importorskip("mujoco")
        from rove.adapters.dynamics_mujoco import compute_dynamics

        bad_urdf = tmp_path / "bad.urdf"
        bad_urdf.write_text("this is not valid urdf content")
        with pytest.raises(ValueError, match="Failed to load"):
            compute_dynamics(str(bad_urdf), [[0.1] * 7])

    def test_compute_dynamics_import_error(self):
        """Test that missing mujoco raises ImportError with helpful message."""
        import sys

        # Only test if mujoco is NOT installed
        if "mujoco" in sys.modules:
            pytest.skip("mujoco is installed")
        try:
            from rove.adapters.dynamics_mujoco import compute_dynamics

            compute_dynamics("nonexistent.urdf", [[0.1] * 7])
        except ImportError as e:
            assert "kinematics" in str(e)
