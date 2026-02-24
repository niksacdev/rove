"""Tests for mock adapters — VLM, VLA, Agent, Sim behavior."""

from __future__ import annotations

import pytest

from rove.adapters.mock_agent import MockAgentAdapter
from rove.adapters.mock_sim import MockSimAdapter
from rove.adapters.mock_vla import MockVLAAdapter
from rove.adapters.mock_vlm import MockVLMAdapter
from rove.models import SceneAnalysis, TaskPlan


class TestMockSimAdapter:
    @pytest.mark.asyncio
    async def test_reset_clears_state(self):
        sim = MockSimAdapter()
        await sim.step([0.0] * 7)  # advance state
        obs = await sim.reset("test")
        assert obs.proprioception == [0.0] * 7
        assert obs.image_base64 == ""
        assert obs.success is False

    @pytest.mark.asyncio
    async def test_step_updates_state(self):
        sim = MockSimAdapter()
        await sim.reset("test")
        obs = await sim.step([0.1] * 7)
        assert obs.image_base64 != ""
        assert obs.proprioception != [0.0] * 7

    @pytest.mark.asyncio
    async def test_get_observation_returns_last_state(self):
        sim = MockSimAdapter()
        await sim.reset("test")
        step_obs = await sim.step([0.1] * 7)
        current = await sim.get_observation()
        assert current.image_base64 == step_obs.image_base64
        assert current.proprioception == step_obs.proprioception
        assert current.success == step_obs.success

    @pytest.mark.asyncio
    async def test_get_observation_after_reset(self):
        sim = MockSimAdapter()
        await sim.reset("test")
        obs = await sim.get_observation()
        assert obs.image_base64 == ""
        assert obs.success is False


class TestMockVLMAdapter:
    @pytest.mark.asyncio
    async def test_plan_uses_scene_target(self):
        vlm = MockVLMAdapter(config={"mock_latency_ms": [1, 2]})
        scene = SceneAnalysis(
            objects=[{"name": "blue bolt"}],
            spatial_relations=[],
            task_relevant=["blue bolt", "bin B"],
        )
        plan = await vlm.plan_task("img", "pick blue bolt", scene)
        assert plan.target_object == "blue bolt"
        assert "blue bolt" in plan.strategy

    @pytest.mark.asyncio
    async def test_verify_uses_context(self):
        vlm = MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0})
        ctx = {"plan": {"target_object": "red bracket", "strategy": "top-down"}}
        result = await vlm.verify_success("before", "after", "test", context=ctx)
        assert "red bracket" in result.reasoning

    @pytest.mark.asyncio
    async def test_verify_without_context(self):
        vlm = MockVLMAdapter(config={"mock_latency_ms": [1, 2]})
        result = await vlm.verify_success("before", "after", "test")
        assert result.reasoning  # should still produce reasoning


class TestMockVLMVerifyStageAware:
    @pytest.mark.asyncio
    async def test_verify_stage_checks(self):
        vlm = MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0})
        ctx = {
            "completed_stages": ["perceive", "plan"],
            "plan": {"target_object": "bolt", "strategy": "top-down"},
        }
        result = await vlm.verify_success("before", "after", "test", context=ctx)
        assert result.completed_stages == ["perceive", "plan"]
        assert len(result.stage_checks) == 2
        assert result.stage_checks[0].stage == "perceive"
        assert result.stage_checks[1].stage == "plan"
        # With quality=1.0, all should pass
        assert all(sc.passed for sc in result.stage_checks)

    @pytest.mark.asyncio
    async def test_verify_ground_truth(self):
        vlm = MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0})
        ctx = {
            "completed_stages": ["perceive"],
            "ground_truth": {
                "question": "Is the bottle reachable?",
                "choices": ["Yes", "No"],
                "correct_answer_index": 0,
                "correct_answer_text": "Yes",
            },
        }
        result = await vlm.verify_success("before", "after", "test", context=ctx)
        assert result.ground_truth is not None
        assert result.ground_truth.question == "Is the bottle reachable?"
        assert result.ground_truth.correct_answer == "Yes"
        # With quality=1.0, should be correct
        assert result.ground_truth.correct is True
        assert result.ground_truth.pipeline_answer == "Yes"

    @pytest.mark.asyncio
    async def test_verify_no_ground_truth(self):
        vlm = MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0})
        ctx = {"completed_stages": ["perceive"]}
        result = await vlm.verify_success("before", "after", "test", context=ctx)
        assert result.ground_truth is None


class TestMockVLAAdapter:
    @pytest.mark.asyncio
    async def test_uses_plan_steps_for_trajectory_length(self):
        vla = MockVLAAdapter(config={"mock_latency_ms": [1, 2]})
        plan = TaskPlan(
            strategy="test",
            reasoning="test",
            steps=["a", "b", "c"],
            target_object="obj",
        )
        pred = await vla.predict_action("img", "task", plan=plan)
        assert pred.num_steps == 4  # len(steps) + 1

    @pytest.mark.asyncio
    async def test_default_steps_without_plan(self):
        vla = MockVLAAdapter(config={"mock_latency_ms": [1, 2]})
        pred = await vla.predict_action("img", "task")
        assert pred.num_steps == 5  # default


class TestMockAgentAdapter:
    @pytest.mark.asyncio
    async def test_plan_uses_scene_context(self):
        agent = MockAgentAdapter(config={"mock_latency_ms": [1, 2]})
        ctx = {"scene": {"task_relevant": ["wrench", "bin C"]}}
        result = await agent.run_stage("plan", "img", "grab wrench", ctx)
        assert result["target_object"] == "wrench"

    @pytest.mark.asyncio
    async def test_act_uses_plan_context(self):
        agent = MockAgentAdapter(config={"mock_latency_ms": [1, 2]})
        ctx = {"plan": {"target_object": "bolt"}}
        result = await agent.run_stage("act", "img", "grab bolt", ctx)
        assert any("bolt" in str(tc.get("args", {})) for tc in result["tool_calls"])

    @pytest.mark.asyncio
    async def test_verify_uses_plan_context(self):
        agent = MockAgentAdapter(config={"mock_latency_ms": [1, 2]})
        ctx = {"plan": {"target_object": "gear"}}
        result = await agent.run_stage("verify", "img", "test", ctx)
        assert "gear" in result["reasoning"]
