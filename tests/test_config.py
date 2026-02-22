"""Tests for rove.config — YAML loading and strategy resolution."""

from __future__ import annotations

from rove.config import find_model_config, get_strategies, load_config


class TestLoadConfig:
    def test_loads_yaml(self):
        config = load_config()
        assert "models" in config
        assert "strategies" in config

    def test_models_sections(self):
        config = load_config()
        models = config["models"]
        assert "vlm" in models
        assert "vla" in models
        assert "sim" in models

    def test_mock_vlm_exists(self):
        config = load_config()
        assert "mock-vlm" in config["models"]["vlm"]


class TestFindModelConfig:
    def test_find_vlm(self):
        model_type, cfg = find_model_config("mock-vlm")
        assert model_type == "vlm"
        assert cfg["adapter"] == "mock_vlm"

    def test_find_vla(self):
        model_type, cfg = find_model_config("mock-vla")
        assert model_type == "vla"

    def test_find_agent(self):
        model_type, cfg = find_model_config("mock-agent")
        assert model_type == "agent"

    def test_find_sim(self):
        model_type, cfg = find_model_config("mock-sim")
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
