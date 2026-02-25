"""Tests for forward kinematics computation and integration."""

from __future__ import annotations

import pytest

from rove.models.config import StrategyConfig, get_strategies
from rove.models.core import PipelineContext, Strategy, VerificationResult
from rove.utils.prompt_loader import get_prompt_manager


class TestStrategyFKConfig:
    """Test forward_kinematics field on strategy config."""

    def test_strategy_config_default_false(self):
        sc = StrategyConfig(verify="mock-vlm", sim="mock-sim", perceive="mock-vlm")
        assert sc.forward_kinematics is False

    def test_strategy_config_explicit_true(self):
        sc = StrategyConfig(
            verify="mock-vlm", sim="mock-sim", perceive="mock-vlm", forward_kinematics=True
        )
        assert sc.forward_kinematics is True

    def test_strategies_from_yaml_have_fk(self):
        strategies = get_strategies()
        # mock strategy has forward_kinematics: true in rove.yaml
        assert strategies["mock"].forward_kinematics is True
        # scene_detect has no act stage and no FK field
        assert strategies["scene_detect"].forward_kinematics is False

    def test_strategy_model_has_fk_field(self):
        s = Strategy(
            id="test",
            display_name="Test",
            description="",
            perceive="mock-vlm",
            act="mock-vla",
            verify="mock-vlm",
            sim="mock-sim",
            forward_kinematics=True,
        )
        assert s.forward_kinematics is True
        d = s.model_dump()
        assert d["forward_kinematics"] is True


class TestPipelineContextFK:
    """Test fk_analysis on PipelineContext."""

    def test_fk_analysis_default_none(self):
        ctx = PipelineContext(task="test")
        assert ctx.fk_analysis is None

    def test_fk_analysis_in_to_dict(self):
        ctx = PipelineContext(task="test")
        ctx.fk_analysis = {"final_endpoint": [0.3, 0.2, 0.4], "steps_analyzed": 5}
        d = ctx.to_dict()
        assert "fk_analysis" in d
        assert d["fk_analysis"]["steps_analyzed"] == 5

    def test_fk_analysis_excluded_when_none(self):
        ctx = PipelineContext(task="test")
        d = ctx.to_dict()
        assert "fk_analysis" not in d


class TestVerificationResultFK:
    """Test fk_analysis on VerificationResult."""

    def test_fk_analysis_field(self):
        vr = VerificationResult(
            success=True,
            confidence=0.9,
            reasoning="good",
            fk_analysis={"joint_limits_ok": True},
        )
        assert vr.fk_analysis["joint_limits_ok"] is True


class TestVerifyPromptFK:
    """Test FK injection in verify prompts."""

    def test_fk_in_verify_prompt(self):
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
            "fk_analysis": {
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
            },
        }
        prompt = pm.render_verify("pick bolt", ctx)
        assert "FORWARD KINEMATICS" in prompt
        assert "0.340" in prompt
        assert "within bounds" in prompt
        assert "SPATIAL CONTEXT" in prompt
        # Should NOT contain the "CANNOT determine" disclaimer
        assert "CANNOT determine" not in prompt

    def test_no_fk_shows_disclaimer(self):
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
        assert "FORWARD KINEMATICS" not in prompt


class TestComputeFK:
    """Test the compute_fk function (requires mujoco)."""

    @pytest.fixture
    def panda_urdf(self):
        from pathlib import Path

        urdf = Path(__file__).parent.parent / "data" / "urdf" / "panda" / "panda.urdf"
        if not urdf.exists():
            pytest.skip("Panda URDF not available at data/urdf/panda/panda.urdf")
        return str(urdf)

    def test_compute_fk_basic(self, panda_urdf):
        mujoco = pytest.importorskip("mujoco")  # noqa: F841
        from rove.adapters.fk_mujoco import compute_fk

        actions = [
            [0.01, -0.01, 0.02, 0.0, 0.01, -0.01, 0.0],
            [0.02, -0.02, 0.01, 0.0, 0.02, -0.02, 0.0],
            [0.01, 0.0, 0.01, 0.0, 0.01, 0.0, 0.0],
        ]
        result = compute_fk(panda_urdf, actions)

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

    def test_compute_fk_empty_actions(self, panda_urdf):
        mujoco = pytest.importorskip("mujoco")  # noqa: F841
        from rove.adapters.fk_mujoco import compute_fk

        result = compute_fk(panda_urdf, [])
        assert result["steps_analyzed"] == 0
        assert len(result["endpoint_trajectory"]) == 1  # just initial position

    def test_compute_fk_file_not_found(self):
        """Test graceful error for missing URDF file."""
        pytest.importorskip("mujoco")
        from rove.adapters.fk_mujoco import compute_fk

        with pytest.raises(FileNotFoundError, match="not found"):
            compute_fk("/nonexistent/path/robot.urdf", [[0.1] * 7])

    def test_compute_fk_invalid_file(self, tmp_path):
        """Test graceful error for invalid URDF content."""
        pytest.importorskip("mujoco")
        from rove.adapters.fk_mujoco import compute_fk

        bad_urdf = tmp_path / "bad.urdf"
        bad_urdf.write_text("this is not valid urdf content")
        with pytest.raises(ValueError, match="Failed to load"):
            compute_fk(str(bad_urdf), [[0.1] * 7])

    def test_compute_fk_import_error(self):
        """Test that missing mujoco raises ImportError with helpful message."""
        import sys

        # Only test if mujoco is NOT installed
        if "mujoco" in sys.modules:
            pytest.skip("mujoco is installed")
        try:
            from rove.adapters.fk_mujoco import compute_fk

            compute_fk("nonexistent.urdf", [[0.1] * 7])
        except ImportError as e:
            assert "kinematics" in str(e)
