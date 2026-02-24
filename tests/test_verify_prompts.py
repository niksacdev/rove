"""Tests for verify prompt builder."""

from __future__ import annotations

from rove.adapters.vlm.verify_prompts import build_verify_prompt, build_verify_system_message


class TestBuildVerifyPrompt:
    def test_perceive_only_prompt(self):
        ctx = {
            "task": "pick red bracket",
            "completed_stages": ["perceive"],
            "scene": {
                "objects": [{"name": "red bracket"}, {"name": "bin A"}],
                "spatial_relations": ["red bracket is left of bin A"],
                "task_relevant": ["red bracket", "bin A"],
            },
        }
        prompt = build_verify_prompt("pick red bracket", ctx)
        assert "perceive" in prompt
        assert "red bracket" in prompt
        assert "bin A" in prompt
        assert "PLAN" not in prompt
        assert "ACT" not in prompt

    def test_full_pipeline_prompt(self):
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
        prompt = build_verify_prompt("pick bolt", ctx)
        assert "PERCEIVE" in prompt
        assert "PLAN" in prompt
        assert "ACT" in prompt
        assert "top-down approach" in prompt
        assert "trajectory" in prompt

    def test_ground_truth_in_prompt(self):
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
        prompt = build_verify_prompt("test", ctx)
        assert "GROUND TRUTH" in prompt
        assert "Is the bottle reachable?" in prompt
        assert "Yes" in prompt
        assert "No" in prompt
        assert "Maybe" in prompt
        assert '"ground_truth"' in prompt

    def test_no_ground_truth(self):
        ctx = {
            "completed_stages": ["perceive"],
            "scene": {"objects": [], "spatial_relations": [], "task_relevant": []},
        }
        prompt = build_verify_prompt("test", ctx)
        assert "GROUND TRUTH" not in prompt
        # Should not request ground_truth in output format
        assert '"ground_truth"' not in prompt

    def test_empty_context(self):
        prompt = build_verify_prompt("test", {})
        assert "No pipeline stages completed" in prompt

    def test_system_message(self):
        msg = build_verify_system_message()
        assert "LLM judge" in msg
        assert "JSON" in msg
