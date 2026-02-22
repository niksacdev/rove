"""Configuration loader — reads rove.yaml, validates with Pydantic, resolves env vars."""

from __future__ import annotations

__all__ = [
    "CostConfig",
    "DefaultsConfig",
    "EndpointConfig",
    "RoveConfig",
    "StrategyConfig",
    "find_model_config",
    "get_all_models",
    "get_endpoint_config",
    "get_model_config",
    "get_strategies",
    "load_config",
    "reset_config_cache",
    "resolve_env",
]

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, model_validator

if TYPE_CHECKING:
    from rove.models import Strategy


# =============================================================================
# Pydantic config models
# =============================================================================


class CostConfig(BaseModel):
    type: str  # "per_token" | "fixed"
    per_call: float = 0.0
    input_per_1k: float = 0.0
    output_per_1k: float = 0.0


class EndpointConfig(BaseModel):
    type: str  # "vlm" | "vla" | "llm" | "grounding" | "agent" | "sim"
    adapter: str
    display_name: str = ""
    deployment_type: str = "local"
    endpoint: str | None = None
    config: dict[str, Any] = {}
    capabilities: list[str] = []
    cost: CostConfig | None = None
    max_concurrent: int | None = None
    availability: dict[str, Any] = {}
    notes: str = ""


class StrategyConfig(BaseModel):
    display_name: str = ""
    description: str = ""
    perceive: str
    plan: str
    act: str
    verify: str
    sim: str
    tags: list[str] = []


class DefaultsConfig(BaseModel):
    grounding: str = ""
    sim: str = ""
    trials: int = 1
    mode: str = "full"
    max_concurrent_combinations: int = 9
    timeout_per_step_ms: int = 30000
    max_retries_per_step: int = 2


class RoveConfig(BaseModel):
    version: str = "1.0"
    endpoints: dict[str, EndpointConfig]
    strategies: dict[str, StrategyConfig] = {}
    defaults: DefaultsConfig = DefaultsConfig()

    @model_validator(mode="after")
    def _validate_strategy_refs(self) -> RoveConfig:
        """Ensure every endpoint ref in strategies actually exists."""
        for sid, strat in self.strategies.items():
            for field in ("perceive", "plan", "act", "verify", "sim"):
                ref = getattr(strat, field)
                if ref not in self.endpoints:
                    raise ValueError(
                        f"Strategy '{sid}' references endpoint '{ref}' "
                        f"in '{field}' but it is not defined in endpoints."
                    )
        return self


# =============================================================================
# Config loading
# =============================================================================

_config_cache: RoveConfig | None = None


def _find_config_path() -> Path:
    """Search common locations for rove.yaml."""
    search_paths = [
        Path.cwd() / "rove.yaml",
        Path.cwd() / "config" / "rove.yaml",
        Path(__file__).parent.parent.parent / "rove.yaml",
    ]
    for p in search_paths:
        if p.exists():
            return p
    raise FileNotFoundError(
        "rove.yaml not found. Searched: " + ", ".join(str(p) for p in search_paths)
    )


def load_config(config_path: str | Path | None = None) -> RoveConfig:
    """Load and validate rove.yaml. Returns a RoveConfig instance."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    path = Path(config_path) if config_path else _find_config_path()

    with open(path) as f:
        raw = yaml.safe_load(f)

    _config_cache = RoveConfig.model_validate(raw)
    return _config_cache


def reset_config_cache() -> None:
    """Reset the config cache (for testing)."""
    global _config_cache
    _config_cache = None


# =============================================================================
# Public API — backward-compatible wrappers
# =============================================================================


def resolve_env(key: str) -> str | None:
    """Resolve an environment variable name to its value."""
    return os.environ.get(key)


def get_endpoint_config(endpoint_id: str) -> EndpointConfig:
    """Get config for a specific endpoint by ID."""
    config = load_config()
    if endpoint_id not in config.endpoints:
        raise KeyError(
            f"Endpoint '{endpoint_id}' not found. Available: {list(config.endpoints.keys())}"
        )
    return config.endpoints[endpoint_id]


def get_model_config(model_type: str, model_id: str) -> dict[str, Any]:
    """Get config for a specific model by type and ID (backward compat).

    Returns the endpoint config as a dict, validating the type matches.
    """
    ep = get_endpoint_config(model_id)
    if ep.type != model_type:
        raise KeyError(f"Endpoint '{model_id}' has type '{ep.type}', expected '{model_type}'")
    return ep.model_dump()


def get_all_models() -> dict[str, dict[str, Any]]:
    """Return all endpoints grouped by type, with IDs included (backward compat).

    Returns {type: {id: config_dict}} matching the old nested structure.
    """
    config = load_config()
    result: dict[str, dict[str, Any]] = {}
    for ep_id, ep in config.endpoints.items():
        if ep.type not in result:
            result[ep.type] = {}
        entry = ep.model_dump()
        entry["id"] = ep_id
        result[ep.type][ep_id] = entry
    return result


def find_model_config(model_id: str) -> tuple[str, dict[str, Any]]:
    """Find a model by ID across all endpoints (backward compat).

    Returns (type, config_dict) tuple.
    """
    ep = get_endpoint_config(model_id)
    return ep.type, ep.model_dump()


def get_strategies() -> dict[str, Strategy]:
    """Load all strategies from rove.yaml."""
    from rove.models import Strategy

    config = load_config()
    strategies: dict[str, Strategy] = {}
    for sid, entry in config.strategies.items():
        strategies[sid] = Strategy(
            id=sid,
            display_name=entry.display_name or sid,
            description=entry.description,
            perceive=entry.perceive,
            plan=entry.plan,
            act=entry.act,
            verify=entry.verify,
            sim=entry.sim,
            tags=list(entry.tags),
        )
    return strategies
