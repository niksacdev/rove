"""Tests for verify prompt building via PromptManager."""

from __future__ import annotations

from rove.utils.prompt_loader import get_prompt_manager


class TestRenderVerify:
    def test_perceive_only_prompt(self):
        pm = get_prompt_manager()
        ctx = {
            "task": "pick red bracket",
            "completed_stages": ["perceive"],
            "scene": {
                "objects": [{"name": "red bracket"}, {"name": "bin A"}],
                "spatial_relations": ["red bracket is left of bin A"],
                "task_relevant": ["red bracket", "bin A"],
            },
        }
        prompt = pm.render_verify("pick red bracket", ctx)
        assert "perceive" in prompt
        assert "red bracket" in prompt
        assert "bin A" in prompt
        assert "PLAN" not in prompt
        assert "ACT" not in prompt

    def test_full_pipeline_prompt(self):
        pm = get_prompt_manager()
        ctx = {
            "task": "pick bolt",
            "completed_stages": ["perceive", "plan", "act"],
            "scene": {
                "objects": [{"name": "bolt"}],
                "spatial_relations": [],
                "task_relevant": ["bolt"],
            },
            "plan": {
                "strategy": "top-down approach",
                "target_object": "bolt",
                "steps": ["locate", "grasp", "lift"],
                "confidence": 0.9,
            },
            "action": {
                "action_type": "trajectory",
                "num_steps": 3,
                "confidence": 0.85,
            },
        }
        prompt = pm.render_verify("pick bolt", ctx)
        assert "PERCEIVE" in prompt
        assert "PLAN" in prompt
        assert "ACT" in prompt
        assert "top-down approach" in prompt
        assert "trajectory" in prompt

    def test_act_trajectory_in_prompt(self):
        pm = get_prompt_manager()
        ctx = {
            "task": "pick bolt",
            "completed_stages": ["perceive", "plan", "act"],
            "scene": {
                "objects": [{"name": "bolt"}],
                "spatial_relations": [],
                "task_relevant": ["bolt"],
            },
            "plan": {
                "strategy": "top-down approach",
                "target_object": "bolt",
                "steps": ["locate", "grasp", "lift"],
                "confidence": 0.9,
            },
            "action": {
                "action_type": "trajectory",
                "num_steps": 2,
                "confidence": 0.85,
                "actions": [
                    [0.01, -0.02, -0.03, 0.0, 0.0, 0.0, 1.0],
                    [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                ],
            },
        }
        prompt = pm.render_verify("pick bolt", ctx)
        assert "7-DOF" in prompt
        assert "dx, dy, dz, rx, ry, rz, grip" in prompt
        assert "step 1:" in prompt
        assert "step 2:" in prompt
        assert "+0.0100" in prompt
        assert "gripper open/close" in prompt

    def test_ground_truth_in_prompt(self):
        pm = get_prompt_manager()
        ctx = {
            "completed_stages": ["perceive"],
            "scene": {"objects": [], "spatial_relations": [], "task_relevant": []},
            "ground_truth": {
                "question": "Is the bottle reachable?",
                "choices": ["Yes", "No", "Maybe"],
                "correct_answer_index": 0,
                "correct_answer_text": "Yes",
            },
        }
        prompt = pm.render_verify("test", ctx)
        assert "GROUND TRUTH" in prompt
        assert "Is the bottle reachable?" in prompt
        assert "Yes" in prompt
        assert "No" in prompt
        assert "Maybe" in prompt
        assert '"ground_truth"' in prompt

    def test_no_ground_truth(self):
        pm = get_prompt_manager()
        ctx = {
            "completed_stages": ["perceive"],
            "scene": {"objects": [], "spatial_relations": [], "task_relevant": []},
        }
        prompt = pm.render_verify("test", ctx)
        assert "GROUND TRUTH" not in prompt
        # Should not request ground_truth in output format
        assert '"ground_truth"' not in prompt

    def test_empty_context(self):
        pm = get_prompt_manager()
        prompt = pm.render_verify("test", {})
        assert "No pipeline stages completed" in prompt

    def test_system_message(self):
        pm = get_prompt_manager()
        msg = pm.render_verify_system()
        assert "LLM judge" in msg
        assert "JSON" in msg
