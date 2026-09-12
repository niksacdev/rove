"""Configuration loader — reads rove.yaml, validates with Pydantic, resolves env vars."""

from __future__ import annotations

__all__ = [
    "CostConfig",
    "DefaultsConfig",
    "EndpointConfig",
    "ObservabilityConfig",
    "RoveConfig",
    "RuntimeContract",
    "StrategyConfig",
    "active_config_path",
    "effective_runtime_contract",
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
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rove.models.verification import StageConfig, VerifyStageConfig

if TYPE_CHECKING:
    from rove.models.core import Strategy


# =============================================================================
# Pydantic config models
# =============================================================================


class CostConfig(BaseModel):
    type: str  # "per_token" | "fixed"
    per_call: float = 0.0
    input_per_1k: float = 0.0
    output_per_1k: float = 0.0


class RuntimeContract(BaseModel):
    """Declared execution boundaries; these fields do not attest a customer's reset."""

    model_config = ConfigDict(extra="forbid")
    memory: str = "unknown"
    reset: str = "unknown"
    telemetry_coverage: str = "stage_only"

    @model_validator(mode="after")
    def supported(self):
        if self.memory not in {
            "unknown",
            "stateless",
            "fresh_per_stage",
            "fresh_per_trial",
            "customer_managed",
        }:
            raise ValueError("Unsupported stage memory declaration")
        if self.reset not in {
            "unknown",
            "none",
            "fresh_process_session",
            "fresh_environment",
            "customer_managed",
        }:
            raise ValueError("Unsupported stage reset declaration")
        if self.telemetry_coverage not in {
            "unknown",
            "stage_only",
            "customer_reported",
            "sdk_events",
            "sdk_events_and_native_spans",
        }:
            raise ValueError("Unsupported telemetry coverage declaration")
        return self


class ObservabilityConfig(BaseModel):
    """Optional, bounded monitoring export; the local trial journal is independent."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    enabled: bool = False
    endpoint: str | None = None
    request_timeout_s: float = Field(default=1, gt=0, le=5)
    shutdown_timeout_s: float = Field(default=0.1, ge=0, le=2)
    queue_capacity: int = Field(default=64, ge=1, le=1000)
    max_payload_bytes: int = Field(default=1_000_000, ge=1024, le=4_000_000)

    @model_validator(mode="after")
    def explicit_endpoint(self):
        from ipaddress import ip_address
        from urllib.parse import urlsplit

        if self.enabled and not self.endpoint:
            raise ValueError("Enabled observability requires an explicit OTLP HTTP endpoint")
        if self.endpoint:
            url = urlsplit(self.endpoint)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError(
                    "Use an explicit HTTP(S) collector endpoint without credentials or query parameters"
                )
            try:
                local = ip_address(url.hostname).is_loopback
                _ = url.port
            except ValueError:
                local = False
            if not local:
                raise ValueError("The local monitoring profile requires a numeric loopback address")
        return self


class EndpointConfig(BaseModel):
    type: str  # "vlm" | "vla" | "llm" | "grounding" | "agent" | "sim"
    adapter: str | None = None
    provider: str | None = None
    display_name: str = ""
    deployment_type: str = "local"
    endpoint: str | None = None
    config: dict[str, Any] = {}
    capabilities: list[str] = []
    cost: CostConfig | None = None
    max_concurrent: int | None = None
    availability: dict[str, Any] = {}
    notes: str = ""
    runtime_contract: RuntimeContract | None = None

    @model_validator(mode="after")
    def _require_adapter_or_provider(self) -> EndpointConfig:
        if not self.adapter and not self.provider:
            raise ValueError("EndpointConfig must have at least one of 'adapter' or 'provider'")
        if self.adapter == "copilot_agent" and self.runtime_contract is not None:
            expected = effective_runtime_contract(self)
            for key in self.runtime_contract.model_fields_set:
                if getattr(self.runtime_contract, key) != expected[key]:
                    raise ValueError(
                        "Copilot stages require the implemented fresh-stage runtime contract"
                    )
        return self


def effective_runtime_contract(endpoint: EndpointConfig) -> dict:
    """Report implemented Copilot scope or an explicitly attributed direct declaration."""
    if endpoint.adapter == "copilot_agent":
        return {
            "memory": "fresh_per_stage",
            "reset": "fresh_process_session",
            "telemetry_coverage": "sdk_events_and_native_spans"
            if endpoint.config.get("native_traces", True)
            else "sdk_events",
            "attribution": "rove_implemented",
        }
    return {
        **(endpoint.runtime_contract or RuntimeContract()).model_dump(),
        "attribution": "customer_declared" if endpoint.runtime_contract else "unspecified",
    }


class StrategyConfig(BaseModel):
    display_name: str = ""
    description: str = ""
    perceive: str | StageConfig | None = None
    plan: str | StageConfig | None = None
    act: str | StageConfig | None = None
    verify: str | VerifyStageConfig
    sim: str | None = None
    compute_dynamics: bool = False
    pipeline_mode: str = "sequential"  # "sequential" | "parallel"
    verify_mode: str = "auto"  # "auto" | "agent_loop" | "precompute"
    tags: list[str] = []
    latency_budget: dict[str, int] = {}  # per-stage overrides (ms), empty = use defaults

    @model_validator(mode="after")
    def validate_stage_options(self):
        if self.pipeline_mode not in {"sequential", "parallel"}:
            raise ValueError("Invalid pipeline_mode")
        if self.verify_mode not in {"auto", "precompute", "agent_loop"}:
            raise ValueError("Invalid verify_mode")
        if isinstance(self.verify, VerifyStageConfig):
            if (
                self.verify.mode != "auto"
                and self.verify_mode != "auto"
                and self.verify.mode != self.verify_mode
            ):
                raise ValueError("Conflicting verify_mode and verify.mode")
            if self.compute_dynamics and self.verify.checks:
                raise ValueError("Use either compute_dynamics or verify.checks, not both")
        for stage, budget in self.latency_budget.items():
            if stage not in {"perceive", "plan", "act", "verify"} or budget <= 0:
                raise ValueError("Invalid stage latency budget")
        return self

    def stage_options(self, name: str) -> StageConfig | None:
        value = getattr(self, name)
        if value is None:
            return None
        cls = VerifyStageConfig if name == "verify" else StageConfig
        return cls(endpoint=value) if isinstance(value, str) else value

    def endpoint_refs(self) -> set[str]:
        refs = {
            self.stage_options(name).endpoint
            for name in ("perceive", "plan", "act", "verify")
            if self.stage_options(name)
        }
        if self.sim:
            refs.add(self.sim)
        refs.update(c.endpoint for c in self.stage_options("verify").checks)
        return refs


class DefaultsConfig(BaseModel):
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    grounding: str = ""
    sim: str = ""
    trials: int = 1
    mode: str = "full"
    max_concurrent_combinations: int = 9
    timeout_per_step_ms: int = 30000
    max_retries_per_step: int = 2
    latency_budget_ms: int = 10000  # 10s total pipeline latency target
    latency_budget: dict[str, int] = {
        "perceive": 3000,
        "plan": 3000,
        "act": 3000,
        "verify": 1000,
    }


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
                if isinstance(ref, StageConfig):
                    ref = ref.endpoint
                if ref is not None and ref not in self.endpoints:
                    raise ValueError(
                        f"Strategy '{sid}' references endpoint '{ref}' "
                        f"in '{field}' but it is not defined in endpoints."
                    )
            verify = strat.stage_options("verify")
            for check in verify.checks:
                if check.endpoint not in self.endpoints:
                    raise ValueError(
                        f"Check endpoint '{check.endpoint}' is not defined in endpoints"
                    )
                ep = self.endpoints[check.endpoint]
                if ep.type != "verifier" or "diagnostic_check" not in ep.capabilities:
                    raise ValueError(
                        f"Check '{check.endpoint}' requires a verifier with diagnostic_check capability"
                    )
                if ep.adapter == "forward_kinematics" and check.role != "diagnostic":
                    raise ValueError("Forward kinematics is a diagnostic, not a task constraint")
            ep = self.endpoints[verify.endpoint]
            mode = verify.mode if verify.mode != "auto" else strat.verify_mode
            if ep.type == "verifier" and mode == "agent_loop":
                raise ValueError("Local task evaluators require precompute mode")
            if ep.type == "verifier" and (
                "verification" not in ep.capabilities or ep.adapter == "forward_kinematics"
            ):
                raise ValueError(
                    "Task evaluator requires verification capability; FK is diagnostic only"
                )
            # At least one optional stage must be present
            optional = [getattr(strat, f) for f in ("perceive", "plan", "act")]
            if not any(optional):
                raise ValueError(f"Strategy '{sid}' must define at least one of perceive/plan/act")
        return self


# =============================================================================
# Config loading
# =============================================================================

_config_cache: RoveConfig | None = None
_config_path: Path | None = None
_config_cache_key: tuple | None = None


def _find_config_path() -> Path:
    """Search common locations for rove.yaml."""
    search_paths = [
        Path.cwd() / "rove.yaml",
        Path.cwd() / "config" / "rove.yaml",
        Path(__file__).parent.parent.parent.parent / "rove.yaml",
    ]
    for p in search_paths:
        if p.exists():
            return p
    raise FileNotFoundError(
        "rove.yaml not found. Searched: " + ", ".join(str(p) for p in search_paths)
    )


def active_config_path() -> Path:
    """Current process configuration, including an explicitly selected worker snapshot."""
    return _config_path or _find_config_path().resolve()


def load_config(config_path: str | Path | None = None) -> RoveConfig:
    """Load and validate rove.yaml. Returns a RoveConfig instance."""
    global _config_cache, _config_path, _config_cache_key
    from rove.strategies.revisions import catalog_path, overlay

    path = Path(config_path).resolve() if config_path else active_config_path()
    catalog = catalog_path(path)
    yaml_stat = path.stat()
    catalog_stat = catalog.stat() if catalog.exists() else None
    key = (
        path,
        yaml_stat.st_mtime_ns,
        yaml_stat.st_size,
        (catalog_stat.st_mtime_ns, catalog_stat.st_size) if catalog_stat else None,
    )
    if _config_cache is not None and key == _config_cache_key:
        return _config_cache

    with open(path) as f:
        raw = yaml.safe_load(f)

    _config_cache = overlay(RoveConfig.model_validate(raw), path)
    _config_path, _config_cache_key = path, key
    return _config_cache


def reset_config_cache() -> None:
    """Reset the config cache (for testing)."""
    global _config_cache, _config_path, _config_cache_key
    _config_cache = None
    _config_path = None
    _config_cache_key = None


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
    from rove.models.core import Strategy

    config = load_config()
    strategies: dict[str, Strategy] = {}
    for sid, entry in config.strategies.items():
        strategies[sid] = Strategy(
            id=sid,
            display_name=entry.display_name or sid,
            description=entry.description,
            perceive=entry.stage_options("perceive").endpoint
            if entry.stage_options("perceive")
            else None,
            plan=entry.stage_options("plan").endpoint if entry.stage_options("plan") else None,
            act=entry.stage_options("act").endpoint if entry.stage_options("act") else None,
            verify=entry.stage_options("verify").endpoint
            if entry.stage_options("verify")
            else None,
            sim=entry.sim,
            compute_dynamics=entry.compute_dynamics,
            pipeline_mode=entry.pipeline_mode,
            verify_mode=(
                entry.stage_options("verify").mode
                if entry.stage_options("verify").mode != "auto"
                else entry.verify_mode
            ),
            stage_timeouts={
                name: option.timeout_ms
                or entry.latency_budget.get(name)
                or config.defaults.timeout_per_step_ms
                for name in ("perceive", "plan", "act", "verify")
                if (option := entry.stage_options(name))
            },
            verification_checks=entry.stage_options("verify").checks,
            tags=list(entry.tags),
        )
    return strategies
