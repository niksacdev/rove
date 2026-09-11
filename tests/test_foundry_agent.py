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
    def test_agent_specific_prompt_files_exist(self):
        """Agent-specific prompt files should exist (act + verify system only)."""
        from pathlib import Path

        prompts_dir = Path(__file__).parent.parent / "src" / "rove" / "prompts"
        for name in ("agent_act", "agent_verify_system"):
            path = prompts_dir / f"{name}.txt"
            assert path.exists(), f"Missing prompt file: {path}"

    def test_shared_prompt_files_exist(self):
        """Shared VLM/agent prompt files should exist."""
        from pathlib import Path

        prompts_dir = Path(__file__).parent.parent / "src" / "rove" / "prompts"
        for name in ("perceive_user", "plan_user", "verify_user", "system", "verify_system"):
            path = prompts_dir / f"{name}.txt"
            assert path.exists(), f"Missing prompt file: {path}"

    def test_no_redundant_agent_perceive_plan_prompts(self):
        """agent_perceive.txt and agent_plan.txt should NOT exist — use shared prompts."""
        from pathlib import Path

        prompts_dir = Path(__file__).parent.parent / "src" / "rove" / "prompts"
        assert not (prompts_dir / "agent_perceive.txt").exists()
        assert not (prompts_dir / "agent_plan.txt").exists()

    def test_adapter_uses_prompt_manager(self):
        """AzureFoundryAgentAdapter should accept and use a PromptManager."""
        from rove.adapters.azure_foundry_agent import AzureFoundryAgentAdapter
        from rove.utils.prompt_loader import PromptManager

        pm = PromptManager()
        adapter = AzureFoundryAgentAdapter(
            model_id="test", config={"display_name": "Test"}, prompt_manager=pm
        )
        assert adapter._prompts is pm

    def test_perceive_reuses_vlm_prompt(self):
        """Agent perceive should use the same perceive_user.txt as VLM adapters."""
        from rove.adapters.azure_foundry_agent import AzureFoundryAgentAdapter
        from rove.utils.prompt_loader import PromptManager

        pm = PromptManager()
        adapter = AzureFoundryAgentAdapter(
            model_id="test", config={"display_name": "Test"}, prompt_manager=pm
        )
        _system, user = adapter._build_stage_prompts("perceive", "pick bolt", None)
        assert "pick bolt" in user
        assert "objects" in user  # from perceive_user.txt template
        assert "environment_distribution" in user

    def test_plan_reuses_vlm_prompt(self):
        """Agent plan should use the same plan_user.txt as VLM adapters."""
        from rove.adapters.azure_foundry_agent import AzureFoundryAgentAdapter
        from rove.utils.prompt_loader import PromptManager

        pm = PromptManager()
        adapter = AzureFoundryAgentAdapter(
            model_id="test", config={"display_name": "Test"}, prompt_manager=pm
        )
        ctx = {
            "scene": {
                "objects": [{"name": "wrench", "position": [0.3, 0.1, 0.2]}],
                "task_relevant": ["wrench"],
            }
        }
        _system, user = adapter._build_stage_prompts("plan", "grab wrench", ctx)
        assert "grab wrench" in user
        assert "wrench at [0.30, 0.10, 0.20]m" in user  # scene formatted into prompt
        assert "task_repertoire" in user  # from plan_user.txt template

    def test_verify_uses_agent_specific_system(self):
        """Agent verify should use agent_verify_system.txt (dynamics-aware)."""
        from rove.adapters.azure_foundry_agent import AzureFoundryAgentAdapter
        from rove.utils.prompt_loader import PromptManager

        pm = PromptManager()
        adapter = AzureFoundryAgentAdapter(
            model_id="test", config={"display_name": "Test"}, prompt_manager=pm
        )
        system, user = adapter._build_stage_prompts("verify", "pick object", {})
        assert "dynamics" in system.lower() or "physics" in system.lower()
        assert "pick object" in user

    def test_stage_fallbacks_defined(self):
        from rove.adapters.azure_foundry_agent import _STAGE_FALLBACKS

        assert set(_STAGE_FALLBACKS.keys()) == {"perceive", "plan", "act", "verify"}

    def test_verify_fallback_has_extended_fields(self):
        from rove.adapters.azure_foundry_agent import _STAGE_FALLBACKS

        plaus = _STAGE_FALLBACKS["verify"]["action_plausibility"]
        assert "workspace_reachability" in plaus
        assert "task_completion_plausibility" in plaus
        assert "dynamics_consistency" in plaus
        assert "safety_assessment" in plaus

    def test_strip_markdown_fences(self):
        from rove.adapters.azure_foundry_agent import _strip_markdown_fences

        assert _strip_markdown_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
        assert _strip_markdown_fences('```\n{"a": 1}\n```') == '{"a": 1}'
        assert _strip_markdown_fences('{"a": 1}') == '{"a": 1}'


class TestFoundryAgentVerifyDispatch:
    def test_adapter_detected_for_tool_calling(self):
        """AzureFoundryAgentAdapter should be detected by _verify_call dispatch
        via _ensure_agent and _openai_client attributes (even when client is None)."""
        from rove.adapters.azure_foundry_agent import AzureFoundryAgentAdapter

        adapter = AzureFoundryAgentAdapter(
            model_id="test-agent",
            config={"project_endpoint": "https://test.azure.com", "agent_name": "test"},
        )
        # Before initialization, _openai_client is None but the attribute exists
        assert hasattr(adapter, "_ensure_agent")
        assert hasattr(adapter, "_openai_client")
        assert adapter._openai_client is None  # lazily initialized


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
        assert ep.config["agent_name"] == "your-agent-name"
        assert ep.config["project_endpoint"] == ""

    def test_vla_strategies_compare_two_vlas(self):
        from rove.models.config import get_strategies

        strategies = get_strategies()
        # Two VLA strategies exist for side-by-side comparison
        pi05 = strategies["vla_pi05"]
        smolvla = strategies["vla_smolvla"]
        # Same perceive + verify, different act (the whole point)
        assert pi05.perceive == smolvla.perceive == "qwen3-vl-8b"
        assert pi05.verify == smolvla.verify == "bing-grounding-agent"
        assert pi05.act == "pi05-libero"
        assert smolvla.act == "smolvla-450m"
        assert pi05.verify_mode == smolvla.verify_mode == "agent_loop"
