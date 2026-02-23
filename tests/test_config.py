"""Tests for rove.config — Pydantic-validated YAML loading and strategy resolution."""

from __future__ import annotations

import pytest

from rove.config import (
    EndpointConfig,
    RoveConfig,
    StrategyConfig,
    find_model_config,
    get_endpoint_config,
    get_strategies,
    load_config,
)


class TestLoadConfig:
    def test_loads_yaml(self):
        config = load_config()
        assert isinstance(config, RoveConfig)
        assert config.endpoints
        assert config.strategies

    def test_endpoint_types(self):
        config = load_config()
        types = {ep.type for ep in config.endpoints.values()}
        assert "vlm" in types
        assert "vla" in types
        assert "sim" in types

    def test_mock_vlm_exists(self):
        config = load_config()
        assert "mock-vlm" in config.endpoints
        assert config.endpoints["mock-vlm"].type == "vlm"

    def test_endpoint_is_pydantic(self):
        config = load_config()
        ep = config.endpoints["mock-vlm"]
        assert isinstance(ep, EndpointConfig)
        assert ep.adapter == "mock_vlm"


class TestGetEndpointConfig:
    def test_get_by_id(self):
        ep = get_endpoint_config("mock-vlm")
        assert ep.type == "vlm"
        assert ep.adapter == "mock_vlm"

    def test_missing_raises(self):
        import pytest

        with pytest.raises(KeyError):
            get_endpoint_config("nonexistent-model")


class TestFindModelConfig:
    def test_find_vlm(self):
        model_type, cfg = find_model_config("mock-vlm")
        assert model_type == "vlm"
        assert cfg["adapter"] == "mock_vlm"

    def test_find_vla(self):
        model_type, _cfg = find_model_config("mock-vla")
        assert model_type == "vla"

    def test_find_agent(self):
        model_type, _cfg = find_model_config("mock-agent")
        assert model_type == "agent"

    def test_find_sim(self):
        model_type, _cfg = find_model_config("mock-sim")
        assert model_type == "sim"


class TestGetStrategies:
    def test_loads_all_strategies(self):
        strategies = get_strategies()
        assert "mock" in strategies
        assert "cloud-fast" in strategies
        assert "edge-local" in strategies
        assert "agent-pipeline" in strategies

    def test_mock_strategy_models(self):
        strategies = get_strategies()
        mock = strategies["mock"]
        assert mock.perceive == "mock-vlm"
        assert mock.plan == "mock-vlm"
        assert mock.act == "mock-vla"
        assert mock.verify == "mock-vlm"
        assert mock.sim == "mock-sim"

    def test_strategy_tags(self):
        strategies = get_strategies()
        assert "test" in strategies["mock"].tags
        assert "agent" in strategies["agent-pipeline"].tags

    def test_agent_strategy_uses_agent_model(self):
        strategies = get_strategies()
        agent = strategies["agent-pipeline"]
        assert agent.plan == "mock-agent"
        assert agent.act == "mock-agent"

    def test_partial_strategy_loads(self):
        """Strategy with only perceive + verify (plan/act omitted) loads correctly."""
        strategies = get_strategies()
        vlm_compare = strategies["vlm-compare"]
        assert vlm_compare.perceive == "mock-vlm"
        assert vlm_compare.plan is None
        assert vlm_compare.act is None
        assert vlm_compare.verify == "mock-vlm"
        assert vlm_compare.sim == "mock-sim"


class TestOptionalStageValidation:
    def test_no_optional_stages_raises(self):
        """Strategy with only verify + sim (no perceive/plan/act) raises ValueError."""
        with pytest.raises(ValueError, match="must define at least one"):
            RoveConfig.model_validate({
                "endpoints": {
                    "mock-vlm": {"type": "vlm", "adapter": "mock_vlm"},
                    "mock-sim": {"type": "sim", "adapter": "mock_sim"},
                },
                "strategies": {
                    "bad": {
                        "verify": "mock-vlm",
                        "sim": "mock-sim",
                    }
                },
            })

    def test_invalid_ref_on_present_stage_raises(self):
        """Validation catches invalid endpoint refs on present (non-None) stages."""
        with pytest.raises(ValueError, match="not defined in endpoints"):
            RoveConfig.model_validate({
                "endpoints": {
                    "mock-vlm": {"type": "vlm", "adapter": "mock_vlm"},
                    "mock-sim": {"type": "sim", "adapter": "mock_sim"},
                },
                "strategies": {
                    "bad": {
                        "perceive": "nonexistent",
                        "verify": "mock-vlm",
                        "sim": "mock-sim",
                    }
                },
            })

    def test_none_stages_skip_ref_validation(self):
        """None stages are not validated against endpoints."""
        config = RoveConfig.model_validate({
            "endpoints": {
                "mock-vlm": {"type": "vlm", "adapter": "mock_vlm"},
                "mock-sim": {"type": "sim", "adapter": "mock_sim"},
            },
            "strategies": {
                "partial": {
                    "perceive": "mock-vlm",
                    "verify": "mock-vlm",
                    "sim": "mock-sim",
                }
            },
        })
        strat = config.strategies["partial"]
        assert strat.perceive == "mock-vlm"
        assert strat.plan is None
        assert strat.act is None
