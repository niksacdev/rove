"""Tests for EvaluationPipeline — data flow between stages."""

from __future__ import annotations

import pytest

from rove.adapters.policy.mock import MockPolicyAdapter
from rove.adapters.sim.mock import MockSimAdapter
from rove.adapters.vlm.mock import MockVLMAdapter
from rove.models import StageStatus
from rove.orchestrator.pipeline import EvaluationPipeline


@pytest.fixture
def pipeline():
    return EvaluationPipeline(
        perceive_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
        plan_adapter=MockVLMAdapter(config={"mock_latency_ms": [1, 2]}),
        act_adapter=MockPolicyAdapter(config={"mock_latency_ms": [1, 2]}),
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
            act_adapter=MockPolicyAdapter(config={"mock_latency_ms": [1, 2]}),
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
            act_adapter=MockPolicyAdapter(config={"mock_latency_ms": [1, 2]}),
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
