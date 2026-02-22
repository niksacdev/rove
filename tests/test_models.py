"""Tests for rove.models — data model serialization and construction."""

from __future__ import annotations

from rove.models import (
    ActionPrediction,
    PipelineContext,
    SceneAnalysis,
    Strategy,
    TaskPlan,
)


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
        ctx.action = ActionPrediction(
            action_type="trajectory", num_steps=3, confidence=0.8
        )
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

    def test_with_tags(self):
        s = Strategy(
            id="x", display_name="X", description="",
            perceive="a", plan="b", act="c", verify="d", sim="e",
            tags=["fast", "cloud"],
        )
        assert s.tags == ["fast", "cloud"]
