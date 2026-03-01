"""Tests for Azure AI Foundry Agent adapter — registry, import, config, protocol."""

from __future__ import annotations

import pytest


class TestFoundryAgentRegistry:
    def test_registry_entry_exists(self):
        from rove.adapters.registry import _AGENT_ADAPTERS

        assert "azure_foundry_agent" in _AGENT_ADAPTERS
        assert "AzureFoundryAgentAdapter" in _AGENT_ADAPTERS["azure_foundry_agent"]

    def test_module_importable(self):
        from rove.adapters.azure_foundry_agent import AzureFoundryAgentAdapter

        assert AzureFoundryAgentAdapter is not None


class TestFoundryAgentConfig:
    def test_config_fields(self):
        from rove.adapters.azure_foundry_agent import AzureFoundryAgentAdapter

        adapter = AzureFoundryAgentAdapter(
            model_id="test-agent",
            config={
                "display_name": "Test Agent",
                "project_endpoint": "https://example.services.ai.azure.com/api/projects/test",
                "agent_name": "my-agent",
            },
        )
        assert adapter.model_id == "test-agent"
        assert adapter.display_name == "Test Agent"
        assert (
            adapter._project_endpoint == "https://example.services.ai.azure.com/api/projects/test"
        )
        assert adapter._agent_name == "my-agent"

    def test_default_config(self):
        from rove.adapters.azure_foundry_agent import AzureFoundryAgentAdapter

        adapter = AzureFoundryAgentAdapter()
        assert adapter.model_id == "foundry-agent"
        assert adapter.display_name == "Foundry Agent"
        assert adapter._agent_name == ""

    def test_env_fallback(self, monkeypatch):
        from rove.adapters.azure_foundry_agent import AzureFoundryAgentAdapter

        monkeypatch.setenv("AZURE_AI_FOUNDRY_ENDPOINT", "https://env-endpoint.azure.com")
        adapter = AzureFoundryAgentAdapter(config={"agent_name": "test"})
        assert adapter._project_endpoint == "https://env-endpoint.azure.com"


class TestFoundryAgentProtocol:
    def test_protocol_compliance(self):
        """AzureFoundryAgentAdapter should satisfy AgentAdapter protocol."""
        try:
            from rove.adapters.azure_foundry_agent import AzureFoundryAgentAdapter
            from rove.adapters.protocols import AgentAdapter

            adapter = AzureFoundryAgentAdapter(model_id="test", config={"display_name": "Test"})
            assert isinstance(adapter, AgentAdapter)
        except ImportError:
            pytest.skip("SDK not installed")


class TestFoundryAgentPrompts:
    def test_stage_prompts_defined(self):
        from rove.adapters.azure_foundry_agent import _STAGE_PROMPTS

        assert "perceive" in _STAGE_PROMPTS
        assert "plan" in _STAGE_PROMPTS
        assert "act" in _STAGE_PROMPTS
        assert "verify" in _STAGE_PROMPTS

    def test_stage_prompts_request_json(self):
        from rove.adapters.azure_foundry_agent import _STAGE_PROMPTS

        for stage, prompt in _STAGE_PROMPTS.items():
            assert "JSON" in prompt, f"Stage '{stage}' prompt should mention JSON"

    def test_stage_fallbacks_defined(self):
        from rove.adapters.azure_foundry_agent import _STAGE_FALLBACKS

        assert set(_STAGE_FALLBACKS.keys()) == {"perceive", "plan", "act", "verify"}

    def test_strip_markdown_fences(self):
        from rove.adapters.azure_foundry_agent import _strip_markdown_fences

        assert _strip_markdown_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
        assert _strip_markdown_fences('```\n{"a": 1}\n```') == '{"a": 1}'
        assert _strip_markdown_fences('{"a": 1}') == '{"a": 1}'


class TestFoundryAgentHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_fails_without_sdk(self):
        """Health check should return False when SDK is not installed or endpoint is missing."""
        from rove.adapters.azure_foundry_agent import AzureFoundryAgentAdapter

        adapter = AzureFoundryAgentAdapter(config={"project_endpoint": ""})
        result = await adapter.health_check()
        assert result is False


class TestFoundryAgentYAMLConfig:
    def test_bing_grounding_agent_in_yaml(self):
        from rove.models.config import get_endpoint_config

        ep = get_endpoint_config("bing-grounding-agent")
        assert ep.type == "agent"
        assert ep.adapter == "azure_foundry_agent"
        assert "task_planning" in ep.capabilities
        assert ep.config["agent_name"] == "binggroundingagent"

    def test_hybrid_foundry_strategy(self):
        from rove.models.config import get_strategies

        strategies = get_strategies()
        assert "hybrid_foundry" in strategies
        s = strategies["hybrid_foundry"]
        assert s.plan == "bing-grounding-agent"
        assert s.perceive == "qwen3-vl-8b"
        assert s.act == "pi05-libero"
        assert s.verify == "bing-grounding-agent"
