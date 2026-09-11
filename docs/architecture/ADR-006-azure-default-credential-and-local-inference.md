# ADR-006: Azure DefaultAzureCredential and Local Model Inference

**Status**: Accepted
**Date**: 2026-02-22
**Deciders**: @niksac

## Context

The Azure AI Foundry adapter (`azure_foundry.py`) used API keys (`AZURE_AI_FOUNDRY_KEY`) for authentication. This is acceptable for prototyping but not for production:

1. **API keys are static secrets** — they must be rotated manually, can be leaked in environment variables or config files, and provide no audit trail.
2. **Azure best practice** is `DefaultAzureCredential` from `azure-identity`, which supports managed identity, service principal, Azure CLI login, and other credential types transparently.
3. **Local model inference** via tools like LMStudio provides an OpenAI-compatible API that ROVE should support for self-hosted models.

## Decisions

### D1: Use DefaultAzureCredential for all Azure operations

Replace `AzureKeyCredential` with `DefaultAzureCredential` from `azure-identity` SDK:

```python
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
client = ChatCompletionsClient(
    endpoint=endpoint,
    credential=credential,
)
```

**Credential scopes**:

- Azure AI Foundry Serverless endpoints (`.models.ai.azure.com`): No explicit `credential_scopes` needed — the SDK infers the correct scope.
- Azure OpenAI endpoints (`.openai.azure.com`): Requires `credential_scopes=["https://cognitiveservices.azure.com/.default"]`.

**IAM requirements**: The calling identity needs the `Cognitive Services User` role assignment on the Azure resource.

**Fallback**: If `DefaultAzureCredential` fails (e.g., no Azure identity available), the adapter falls back to `AzureKeyCredential` if an API key environment variable is set. This preserves backward compatibility for environments where Entra ID is not configured.

### D2: Detect endpoint type by URL pattern

```python
if ".openai.azure.com" in endpoint:
    # Azure OpenAI — needs explicit scope
    credential_scopes = ["https://cognitiveservices.azure.com/.default"]
elif ".models.ai.azure.com" in endpoint:
    # Foundry Serverless — SDK handles scope
    credential_scopes = None
```

### D3: Support LMStudio and OpenAI-compatible local inference

LMStudio exposes an OpenAI-compatible API at `http://localhost:1234/v1`. ROVE supports this via adapter config:

```yaml
models:
  vlm:
    qwen3-vl-8b-local:
      adapter: openai_compatible
      display_name: "Qwen3-VL 8B (LMStudio)"
      deployment_type: local
      config:
        endpoint: "http://localhost:1234/v1"
        api_key: "lm-studio"    # dummy key, LMStudio doesn't validate
        model_name: "qwen3-vl-8b"
```

**Key properties**:

- Same `image_url` base64 format as Azure OpenAI: `{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}`
- Streaming supported via `stream=True`
- No authentication required (dummy API key accepted)
- Works with any OpenAI-compatible server (Ollama, vLLM, text-generation-inference)

### D4: Remove API key environment variables from rove.yaml

Replace `api_key_env: AZURE_AI_FOUNDRY_KEY` entries with `auth_mode: default_credential`. API key references remain only as fallback documentation.

```yaml
# Before:
config:
  endpoint_env: AZURE_AI_FOUNDRY_ENDPOINT
  api_key_env: AZURE_AI_FOUNDRY_KEY

# After:
config:
  endpoint_env: AZURE_AI_FOUNDRY_ENDPOINT
  auth_mode: default_credential  # uses DefaultAzureCredential
```

## Consequences

- **Positive**: No static API keys in environment variables. Supports managed identity, service principal, and developer credentials seamlessly.
- **Positive**: LMStudio support enables fully offline evaluation without any cloud dependency.
- **Positive**: OpenAI-compatible adapter works with vLLM, Ollama, and other local inference servers.
- **Negative**: `azure-identity` becomes a required dependency for Azure adapters. Added to `[azure]` optional dependency group.
- **Negative**: Developers must authenticate via `az login` or set up service principal for local development with Azure endpoints.

## Files

| File | Change |
|------|--------|
| `src/rove/adapters/vlm/azure_foundry.py` | Replace AzureKeyCredential with DefaultAzureCredential + fallback |
| `rove.yaml` | Replace `api_key_env` with `auth_mode: default_credential` |
| `pyproject.toml` | Add `azure-identity` to `[azure]` optional deps |
