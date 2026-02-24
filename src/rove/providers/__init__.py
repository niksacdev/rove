"""Provider abstractions — HTTP service clients for VLM/LLM adapters."""

from __future__ import annotations

__all__ = ["Provider", "create_provider"]

import importlib
from typing import Protocol, runtime_checkable


@runtime_checkable
class Provider(Protocol):
    """Protocol for HTTP-backed model providers.

    Providers handle auth, retry, HTTP transport, and token tracking.
    Adapters own prompt formatting and protocol implementation.
    """

    async def get_response(
        self,
        prompt: str,
        images: list[str] | None = None,
        instructions: str | None = None,
    ) -> dict:
        """Send a prompt (with optional images) and return parsed JSON response."""
        ...

    async def health_check(self) -> bool:
        """Check if the provider's service is reachable."""
        ...


# Lazy provider registry — "provider_name" → "module.path:ClassName"
_PROVIDERS: dict[str, str] = {
    "azure_foundry": "rove.providers.azure_foundry:AzureFoundryProvider",
    "lmstudio": "rove.providers.lmstudio:LMStudioProvider",
}


def create_provider(provider_name: str, config: dict) -> Provider:
    """Create a provider instance by name with the given config."""
    if provider_name not in _PROVIDERS:
        raise ValueError(
            f"Unknown provider '{provider_name}'. Available: {list(_PROVIDERS.keys())}"
        )
    dotted_path = _PROVIDERS[provider_name]
    module_path, class_name = dotted_path.rsplit(":", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls(config)
