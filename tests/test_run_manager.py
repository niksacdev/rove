"""Tests for RunManager — concurrent multi-strategy execution."""

from __future__ import annotations

import pytest

from rove.adapters.registry import AdapterRegistry
from rove.orchestrator.run_manager import RunManager


class TestRunManager:
    @pytest.mark.asyncio
    async def test_two_strategies_complete(self, mock_strategy, agent_strategy):
        registry = AdapterRegistry()
        rm = RunManager(registry, max_concurrent=2)
        events = []

        async def on_event(sid, event_type, data):
            events.append((sid, event_type))

        results = await rm.run_strategies(
            [mock_strategy, agent_strategy],
            "pick red bracket",
            "fake_img",
            on_event,
        )

        assert len(results) == 2
        strategy_ids = {r["strategy_id"] for r in results}
        assert strategy_ids == {"mock", "agent-pipeline"}

    @pytest.mark.asyncio
    async def test_each_strategy_has_four_stages(self, mock_strategy, agent_strategy):
        registry = AdapterRegistry()
        rm = RunManager(registry, max_concurrent=2)

        async def on_event(sid, event_type, data):
            pass

        results = await rm.run_strategies(
            [mock_strategy, agent_strategy],
            "pick red bracket",
            "fake_img",
            on_event,
        )

        for r in results:
            assert len(r["stages"]) == 4, f"{r['strategy_id']} has {len(r['stages'])} stages"

    @pytest.mark.asyncio
    async def test_strategy_complete_events_fire(self, mock_strategy, agent_strategy):
        registry = AdapterRegistry()
        rm = RunManager(registry, max_concurrent=2)
        complete_events = []

        async def on_event(sid, event_type, data):
            if event_type == "strategy_complete":
                complete_events.append(sid)

        await rm.run_strategies(
            [mock_strategy, agent_strategy],
            "test",
            "img",
            on_event,
        )

        assert set(complete_events) == {"mock", "agent-pipeline"}

    @pytest.mark.asyncio
    async def test_sim_instances_are_isolated(self, registry):
        """Sim instances should not be shared between strategies."""
        sim1 = registry.create_sim("mock-sim")
        sim2 = registry.create_sim("mock-sim")
        assert sim1 is not sim2

        # Stepping one shouldn't affect the other
        await sim1.reset("test")
        await sim2.reset("test")
        await sim1.step([0.1] * 7)

        obs1 = await sim1.get_observation()
        obs2 = await sim2.get_observation()
        assert obs1.image_base64 != obs2.image_base64  # sim1 stepped, sim2 didn't

    @pytest.mark.asyncio
    async def test_semaphore_bounds_concurrency(self, mock_strategy):
        """With semaphore=1, strategies run sequentially (smoke test)."""
        registry = AdapterRegistry()
        rm = RunManager(registry, max_concurrent=1)
        events = []

        async def on_event(sid, event_type, data):
            events.append((sid, event_type))

        # Duplicate the same strategy to get 2 runs
        from rove.models import Strategy
        strat2 = Strategy(
            id="mock-2", display_name="Mock 2", description="",
            perceive="mock-vlm", plan="mock-vlm", act="mock-vla",
            verify="mock-vlm", sim="mock-sim",
        )

        results = await rm.run_strategies(
            [mock_strategy, strat2], "test", "img", on_event,
        )
        assert len(results) == 2
