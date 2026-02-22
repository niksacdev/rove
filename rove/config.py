"""Configuration loader — reads rove.yaml and resolves environment variables."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


_config_cache: dict | None = None


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load rove.yaml from the given path or search common locations."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    if config_path is None:
        search_paths = [
            Path.cwd() / "rove.yaml",
            Path.cwd() / "config" / "rove.yaml",
            Path(__file__).parent.parent / "rove.yaml",
        ]
        for p in search_paths:
            if p.exists():
                config_path = p
                break
        else:
            raise FileNotFoundError(
                "rove.yaml not found. Searched: " + ", ".join(str(p) for p in search_paths)
            )

    with open(config_path) as f:
        _config_cache = yaml.safe_load(f)
    return _config_cache


def resolve_env(key: str) -> str | None:
    """Resolve an environment variable name to its value."""
    return os.environ.get(key)


def get_model_config(model_type: str, model_id: str) -> dict[str, Any]:
    """Get config for a specific model by type and ID."""
    config = load_config()
    models = config.get("models", {})
    type_models = models.get(model_type, {})
    if model_id not in type_models:
        raise KeyError(f"Model '{model_id}' not found in models.{model_type}")
    return type_models[model_id]


def get_all_models() -> dict[str, dict[str, Any]]:
    """Return all models grouped by type, with IDs included."""
    config = load_config()
    models = config.get("models", {})
    result: dict[str, dict[str, Any]] = {}
    for model_type, type_models in models.items():
        result[model_type] = {}
        for model_id, model_config in type_models.items():
            entry = dict(model_config)
            entry["id"] = model_id
            entry["type"] = model_type
            result[model_type][model_id] = entry
    return result


def find_model_config(model_id: str) -> tuple[str, dict[str, Any]]:
    """Find a model by ID across all model type sections.

    Returns (model_type, model_config) tuple.
    """
    config = load_config()
    models = config.get("models", {})
    for model_type, type_models in models.items():
        if model_id in type_models:
            return model_type, type_models[model_id]
    raise KeyError(
        f"Model '{model_id}' not found in any models section. "
        f"Available sections: {list(models.keys())}"
    )


def reset_config_cache() -> None:
    """Reset the config cache (for testing)."""
    global _config_cache
    _config_cache = None
