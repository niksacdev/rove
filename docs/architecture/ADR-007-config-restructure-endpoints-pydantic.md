# ADR-007: Config Restructure — Flat Endpoints + Pydantic Validation

**Date**: 2026-02-22
**Status**: Accepted

## Context

The original `rove.yaml` structured model configurations as nested `models.{type}.{id}`:

```yaml
models:
  vlm:
    gpt-4o:
      adapter: azure_openai
      config:
        endpoint_env: AZURE_OPENAI_ENDPOINT
```

This had three problems:

1. **"Model" is misleading** — simulation environments (`mujoco-libero`) and agents are not models, yet live under `models:`.
2. **No validation** — config loaded as raw dicts with zero schema enforcement. Typos in field names silently passed. `pydantic>=2.0` was a dependency but unused.
3. **Endpoint URL indirection** — URLs were indirected through env vars (`endpoint_env: AZURE_OPENAI_ENDPOINT`) when only auth secrets need env var protection. This made it impossible to see where endpoints point without checking environment state.

## Decision

### 1. Flatten `models.{type}.{id}` → `endpoints.{id}`

Every entry is a flat key under `endpoints:` with an explicit `type` field:

```yaml
endpoints:
  gpt-4o:
    type: vlm
    adapter: azure_openai
    endpoint: "https://your-resource.openai.azure.com/"
    config:
      api_key_env: AZURE_OPENAI_KEY
```

- `type` values: `vlm | vla | llm | grounding | agent | sim`
- URLs go directly in YAML as `endpoint:` (placeholder values in repo)
- Only auth secrets (`api_key_env`) remain as env var references
- Strategies and defaults sections unchanged

### 2. Pydantic validation

`config.py` now defines Pydantic models (`RoveConfig`, `EndpointConfig`, `StrategyConfig`, `DefaultsConfig`, `CostConfig`) and validates on load. A `model_validator` ensures strategy endpoint references exist.

### 3. Backward-compatible wrappers

Existing public API preserved via wrappers that delegate to the Pydantic model:
- `get_model_config(type, id)` → validates type matches, returns dict
- `find_model_config(id)` → returns `(type, dict)`
- `get_all_models()` → regroups flat endpoints into `{type: {id: dict}}`
- `get_strategies()` → unchanged return type

New API:
- `load_config()` → returns `RoveConfig` (was raw dict)
- `get_endpoint_config(id)` → returns `EndpointConfig`

### 4. URL/secret separation

- **In YAML**: endpoint URLs (placeholder values committed to repo)
- **In `.env`**: only auth secrets (API keys)
- `.env.example` documents required secrets

## Consequences

- Config typos caught at startup with clear Pydantic error messages
- IDE autocompletion works on `EndpointConfig` fields
- `registry.py` simplified — `get_endpoint_config()` returns typed object instead of nested dict navigation
- Frontend JS updated to regroup flat `endpoints` by `type` for display
- No breaking changes to `get_strategies()`, `list_models_by_stage()`, or `/api/models` endpoint
