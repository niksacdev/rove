"""Adapter registry — resolves model IDs to adapter instances from YAML config."""

from __future__ import annotations

import asyncio
import importlib
import logging
from typing import Any

__all__ = ["CAPABILITY_TO_STAGES", "STAGE_TO_CAPABILITY", "AdapterRegistry"]

from rove.adapters.protocols import (
    AgentAdapter,
    SimAdapter,
    StageAdapter,
    VLAAdapter,
    VLMAdapter,
)
from rove.models import PipelineStage
from rove.models.config import get_all_models, get_endpoint_config

logger = logging.getLogger(__name__)

# Adapter class mappings — "adapter_name" → "module.path:ClassName"
_VLM_ADAPTERS = {
    "mock_vlm": "rove.adapters.mock_vlm:MockVLMAdapter",
}

_VLA_ADAPTERS = {
    "mock_vla": "rove.adapters.mock_vla:MockVLAAdapter",
}

_AGENT_ADAPTERS = {
    "mock_agent": "rove.adapters.mock_agent:MockAgentAdapter",
    "azure_foundry_agent": "rove.adapters.azure_foundry_agent:AzureFoundryAgentAdapter",
}

_SIM_ADAPTERS = {
    "mock_sim": "rove.adapters.mock_sim:MockSimAdapter",
}

# Capability → which pipeline stages a model can serve
CAPABILITY_TO_STAGES: dict[str, list[str]] = {
    "scene_analysis": ["perceive"],
    "task_planning": ["plan"],
    "verification": ["verify"],
    "native_grounding": ["perceive"],
    "3d_grounding": ["perceive"],
    "action_execution": ["act"],
}

# Stage → which capability is required
STAGE_TO_CAPABILITY: dict[str, str] = {
    "perceive": "scene_analysis",
    "plan": "task_planning",
    "verify": "verification",
    "act": "action_execution",
}


def _import_class(dotted_path: str) -> type:
    """Import a class from 'module.path:ClassName' format."""
    module_path, class_name = dotted_path.rsplit(":", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _build_adapter(
    model_id: str,
    adapter_map: dict[str, str],
    cache: dict[str, Any],
) -> Any:
    """Shared factory: resolve config → import class → instantiate → cache."""
    if model_id in cache:
        return cache[model_id]

    ep = get_endpoint_config(model_id)
    adapter_name = ep.adapter

    if adapter_name not in adapter_map:
        raise ValueError(
            f"No {ep.type} adapter implementation for '{adapter_name}'. "
            f"Available: {list(adapter_map.keys())}"
        )

    cls = _import_class(adapter_map[adapter_name])
    config = dict(ep.config)
    config["display_name"] = ep.display_name or model_id
    if ep.endpoint:
        config["endpoint"] = ep.endpoint
    instance = cls(model_id=model_id, config=config)
    cache[model_id] = instance
    return instance


def _build_vlm_provider_adapter(model_id: str, cache: dict[str, Any]) -> Any:
    """Build a GenericVLMAdapter backed by a provider (Azure Foundry, LM Studio, etc.)."""
    if model_id in cache:
        return cache[model_id]

    from rove.adapters.generic_vlm import GenericVLMAdapter
    from rove.providers import create_provider
    from rove.utils.prompt_loader import get_prompt_manager

    ep = get_endpoint_config(model_id)
    config = dict(ep.config)
    if ep.endpoint:
        config["endpoint"] = ep.endpoint

    provider = create_provider(ep.provider, config)
    prompt_manager = get_prompt_manager()
    display_name = ep.display_name or model_id

    instance = GenericVLMAdapter(
        model_id=model_id,
        display_name=display_name,
        provider=provider,
        prompt_manager=prompt_manager,
    )
    cache[model_id] = instance
    return instance


class AdapterRegistry:
    """Resolves model IDs to adapter instances, with caching."""

    def __init__(self):
        self._vlm_cache: dict[str, VLMAdapter] = {}
        self._vla_cache: dict[str, VLAAdapter] = {}
        self._agent_cache: dict[str, AgentAdapter] = {}
        self._sim_cache: dict[str, SimAdapter] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    def get_semaphore(self, endpoint_id: str) -> asyncio.Semaphore | None:
        """Get a concurrency semaphore for an endpoint, if max_concurrent is configured."""
        if endpoint_id in self._semaphores:
            return self._semaphores[endpoint_id]
        try:
            ep = get_endpoint_config(endpoint_id)
        except KeyError:
            return None
        if ep.max_concurrent is not None:
            sem = asyncio.Semaphore(ep.max_concurrent)
            self._semaphores[endpoint_id] = sem
            return sem
        return None

    def get_vlm(self, model_id: str) -> VLMAdapter:
        ep = get_endpoint_config(model_id)
        if ep.provider:
            return _build_vlm_provider_adapter(model_id, self._vlm_cache)
        return _build_adapter(model_id, _VLM_ADAPTERS, self._vlm_cache)

    def get_vla(self, model_id: str) -> VLAAdapter:
        return _build_adapter(model_id, _VLA_ADAPTERS, self._vla_cache)

    def get_agent(self, model_id: str) -> AgentAdapter:
        return _build_adapter(model_id, _AGENT_ADAPTERS, self._agent_cache)

    def get_sim(self, model_id: str) -> SimAdapter:
        return _build_adapter(model_id, _SIM_ADAPTERS, self._sim_cache)

    def create_sim(self, model_id: str) -> SimAdapter:
        """Create a fresh (uncached) sim instance — for concurrent isolation."""
        ep = get_endpoint_config(model_id)
        if ep.adapter not in _SIM_ADAPTERS:
            raise ValueError(
                f"No sim adapter implementation for '{ep.adapter}'. "
                f"Available: {list(_SIM_ADAPTERS.keys())}"
            )
        cls = _import_class(_SIM_ADAPTERS[ep.adapter])
        config = dict(ep.config)
        config["display_name"] = ep.display_name or model_id
        return cls(model_id=model_id, config=config)

    def get_adapter_for_stage(self, stage: PipelineStage, model_id: str) -> StageAdapter:
        """Resolve a model for a specific pipeline stage, validating capability."""
        ep = get_endpoint_config(model_id)

        # Agent type — can serve any stage if it has the right capability
        if ep.type == "agent":
            required = STAGE_TO_CAPABILITY.get(stage.value)
            if required and required not in ep.capabilities:
                raise ValueError(
                    f"Agent '{model_id}' lacks capability '{required}' "
                    f"needed for '{stage.value}'. "
                    f"Its capabilities: {ep.capabilities}"
                )
            return self.get_agent(model_id)

        # VLA type — act stage only
        if stage == PipelineStage.ACT:
            if ep.type != "vla":
                raise ValueError(
                    f"Model '{model_id}' (type={ep.type}) cannot serve 'act'. "
                    f"Only VLA or agent models can serve the act stage."
                )
            return self.get_vla(model_id)

        # VLM or LLM type — perceive, plan, verify
        if ep.type not in ("vlm", "llm"):
            raise ValueError(
                f"Model '{model_id}' (type={ep.type}) cannot serve '{stage.value}'. "
                f"Only VLM, LLM, or agent models can serve perceive/plan/verify stages."
            )

        required_capability = STAGE_TO_CAPABILITY.get(stage.value)
        if required_capability and required_capability not in ep.capabilities:
            raise ValueError(
                f"Model '{model_id}' lacks capability '{required_capability}' "
                f"needed for '{stage.value}'. "
                f"Its capabilities: {ep.capabilities}"
            )

        return self.get_vlm(model_id)

    def list_models_by_stage(self) -> dict[str, Any]:
        """Return models grouped by which pipeline stages they can serve."""
        all_models = get_all_models()
        stages: dict[str, list[dict]] = {
            "perceive": [],
            "plan": [],
            "act": [],
            "verify": [],
        }
        sim_list: list[dict] = []

        # VLMs — check capabilities to determine eligible stages
        for model_id, model_cfg in all_models.get("vlm", {}).items():
            adapter_name = model_cfg.get("adapter") or ""
            provider_name = model_cfg.get("provider") or ""
            has_impl = adapter_name in _VLM_ADAPTERS or bool(provider_name)
            capabilities = model_cfg.get("capabilities", [])

            entry = {
                "id": model_id,
                "display_name": model_cfg.get("display_name", model_id),
                "model_type": "vlm",
                "adapter": adapter_name,
                "available": has_impl,
                "deployment_type": model_cfg.get("deployment_type", "unknown"),
            }

            eligible_stages: set[str] = set()
            for cap in capabilities:
                for stage_name in CAPABILITY_TO_STAGES.get(cap, []):
                    eligible_stages.add(stage_name)

            for stage_name in eligible_stages:
                if stage_name in stages:
                    stages[stage_name].append(dict(entry))

        # VLAs — always eligible for act
        for model_id, model_cfg in all_models.get("vla", {}).items():
            adapter_name = model_cfg.get("adapter", "")
            has_impl = adapter_name in _VLA_ADAPTERS
            stages["act"].append(
                {
                    "id": model_id,
                    "display_name": model_cfg.get("display_name", model_id),
                    "model_type": "vla",
                    "adapter": adapter_name,
                    "available": has_impl,
                    "deployment_type": model_cfg.get("deployment_type", "unknown"),
                }
            )

        # Agents — check capabilities, add to eligible stages
        for model_id, model_cfg in all_models.get("agent", {}).items():
            adapter_name = model_cfg.get("adapter", "")
            has_impl = adapter_name in _AGENT_ADAPTERS
            capabilities = model_cfg.get("capabilities", [])

            entry = {
                "id": model_id,
                "display_name": model_cfg.get("display_name", model_id),
                "model_type": "agent",
                "adapter": adapter_name,
                "available": has_impl,
                "deployment_type": model_cfg.get("deployment_type", "unknown"),
            }

            eligible_stages: set[str] = set()
            for cap in capabilities:
                for stage_name in CAPABILITY_TO_STAGES.get(cap, []):
                    eligible_stages.add(stage_name)

            for stage_name in eligible_stages:
                if stage_name in stages:
                    stages[stage_name].append(dict(entry))

        # Sim environments
        for model_id, model_cfg in all_models.get("sim", {}).items():
            adapter_name = model_cfg.get("adapter", "")
            has_impl = adapter_name in _SIM_ADAPTERS
            sim_list.append(
                {
                    "id": model_id,
                    "display_name": model_cfg.get("display_name", model_id),
                    "adapter": adapter_name,
                    "available": has_impl,
                }
            )

        return {"stages": stages, "sim": sim_list}
