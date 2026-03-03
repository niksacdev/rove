"""Tests for mock adapters — VLM, VLA, Agent, Sim behavior."""

from __future__ import annotations

import pytest

from rove.adapters.mock_agent import MockAgentAdapter
from rove.adapters.mock_sim import MockSimAdapter
from rove.adapters.mock_vla import MockVLAAdapter
from rove.adapters.mock_vlm import MockVLMAdapter
from rove.models import ActionSpace, RobotEmbodiment, SceneAnalysis, TaskPlan, VLACapabilities


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
    async def test_analyze_scene_has_positions(self):
        vlm = MockVLMAdapter(config={"mock_latency_ms": [1, 2]})
        scene = await vlm.analyze_scene("img", "pick red bracket")
        for obj in scene.objects:
            assert "position" in obj, f"Missing position for {obj['name']}"
            assert isinstance(obj["position"], list)
            assert len(obj["position"]) == 3

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

    @pytest.mark.asyncio
    async def test_verify_plausibility_with_act(self):
        vlm = MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0})
        ctx = {
            "completed_stages": ["perceive", "plan", "act"],
            "plan": {"target_object": "bolt", "strategy": "top-down"},
        }
        result = await vlm.verify_success("before", "after", "test", context=ctx)
        assert result.action_plausibility is not None
        ap = result.action_plausibility
        # Physics-dependent fields are None without dynamics evidence
        assert ap.bounds_check is None
        assert ap.workspace_reachability is None
        assert ap.dynamics_consistency is None
        assert ap.safety_assessment is None
        # Perception-based fields are still floats
        assert isinstance(ap.smoothness, float)
        assert isinstance(ap.gripper_consistency, float)
        assert 0.0 <= ap.plan_alignment <= 1.0
        assert ap.reasoning != ""
        assert 0.0 <= ap.task_completion_plausibility <= 1.0
        assert ap.evidence_quality == "perception_only"

    @pytest.mark.asyncio
    async def test_verify_plausibility_without_act(self):
        vlm = MockVLMAdapter(config={"mock_latency_ms": [1, 2], "mock_quality": 1.0})
        ctx = {
            "completed_stages": ["perceive", "plan"],
            "plan": {"target_object": "bolt"},
        }
        result = await vlm.verify_success("before", "after", "test", context=ctx)
        assert result.action_plausibility is None


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

    def test_capabilities_from_config(self):
        """Capabilities come from vla_capabilities in config dict."""
        vla = MockVLAAdapter(
            config={
                "mock_latency_ms": [1, 2],
                "vla_capabilities": {
                    "native_action_dim": 6,
                    "native_action_space": "eef_delta",
                    "supported_robot_types": ["so100"],
                },
            }
        )
        caps = vla.capabilities
        assert isinstance(caps, VLACapabilities)
        assert caps.native_action_dim == 6
        assert caps.native_action_space == ActionSpace.EEF_DELTA
        assert caps.supported_robot_types == ["so100"]

    def test_capabilities_fallback_without_config(self):
        """Without vla_capabilities block, falls back to defaults."""
        vla = MockVLAAdapter(config={"mock_latency_ms": [1, 2]})
        caps = vla.capabilities
        assert isinstance(caps, VLACapabilities)
        assert caps.native_action_dim == 7
        assert caps.native_action_space == ActionSpace.EEF_DELTA
        assert caps.supported_robot_types is None

    def test_capabilities_fallback_no_config(self):
        """Without any config, falls back to defaults."""
        vla = MockVLAAdapter()
        caps = vla.capabilities
        assert isinstance(caps, VLACapabilities)
        assert caps.native_action_dim == 7

    @pytest.mark.asyncio
    async def test_embodiment_shapes_output(self):
        vla = MockVLAAdapter(config={"mock_latency_ms": [1, 2]})
        emb = RobotEmbodiment(robot_type="panda", arm_dof=6, gripper_dof=1, gripper_index=6)
        pred = await vla.predict_action("img", "task", embodiment=emb)
        assert pred.declared_action_dim == 7
        assert pred.action_space == ActionSpace.EEF_DELTA
        assert pred.gripper_index == 6
        assert pred.raw_action_dim == 7
        for action in pred.actions:
            assert len(action) == 7

    @pytest.mark.asyncio
    async def test_embodiment_different_dof(self):
        vla = MockVLAAdapter(config={"mock_latency_ms": [1, 2]})
        emb = RobotEmbodiment(robot_type="so100", arm_dof=5, gripper_dof=1, gripper_index=5)
        pred = await vla.predict_action("img", "task", embodiment=emb)
        assert pred.declared_action_dim == 6
        for action in pred.actions:
            assert len(action) == 6

    @pytest.mark.asyncio
    async def test_no_embodiment_defaults_to_7dof(self):
        vla = MockVLAAdapter(config={"mock_latency_ms": [1, 2]})
        pred = await vla.predict_action("img", "task")
        assert pred.declared_action_dim == 7
        for action in pred.actions:
            assert len(action) == 7


class TestMockAgentAdapter:
    @pytest.mark.asyncio
    async def test_perceive_has_positions(self):
        agent = MockAgentAdapter(config={"mock_latency_ms": [1, 2]})
        result = await agent.run_stage("perceive", "img", "pick object")
        for obj in result["objects"]:
            assert "position" in obj, f"Missing position for {obj['name']}"
            assert isinstance(obj["position"], list)
            assert len(obj["position"]) == 3

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

    def test_capabilities_from_config(self):
        agent = MockAgentAdapter(
            config={
                "mock_latency_ms": [1, 2],
                "vla_capabilities": {
                    "native_action_dim": 6,
                    "supported_robot_types": ["so100"],
                },
            }
        )
        caps = agent.capabilities
        assert isinstance(caps, VLACapabilities)
        assert caps.native_action_dim == 6
        assert caps.supported_robot_types == ["so100"]

    def test_capabilities_fallback(self):
        agent = MockAgentAdapter(config={"mock_latency_ms": [1, 2]})
        caps = agent.capabilities
        assert isinstance(caps, VLACapabilities)
        assert caps.native_action_dim == 7
        assert caps.supported_robot_types is None

    @pytest.mark.asyncio
    async def test_act_includes_action_space(self):
        agent = MockAgentAdapter(config={"mock_latency_ms": [1, 2]})
        result = await agent.run_stage("act", "img", "grab bolt")
        assert result["action_space"] == "eef_delta"


class TestActionNormalizer:
    def test_slice_to_target_dim(self):
        from rove.adapters.normalizer import slice_to_target_dim

        raw = [[0.1] * 32, [0.2] * 32]
        sliced = slice_to_target_dim(raw, 7)
        assert len(sliced) == 2
        assert len(sliced[0]) == 7
        assert len(sliced[1]) == 7

    def test_slice_already_correct_size(self):
        from rove.adapters.normalizer import slice_to_target_dim

        raw = [[0.1] * 7, [0.2] * 7]
        sliced = slice_to_target_dim(raw, 7)
        assert sliced == raw

    def test_normalize_proprioception(self):
        from rove.adapters.normalizer import normalize_proprioception

        state = [1.0, 2.0, 3.0]
        mean = [0.0, 1.0, 2.0]
        std = [1.0, 1.0, 1.0]
        result = normalize_proprioception(state, mean, std)
        assert result == [1.0, 1.0, 1.0]

    def test_denormalize_actions(self):
        from rove.adapters.normalizer import denormalize_actions

        actions = [[1.0, 1.0], [2.0, 2.0]]
        mean = [0.0, 1.0]
        std = [1.0, 2.0]
        result = denormalize_actions(actions, mean, std)
        assert result[0] == [1.0, 3.0]
        assert result[1] == [2.0, 5.0]
