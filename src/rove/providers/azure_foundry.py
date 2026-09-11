"""Azure AI Foundry provider — uses AIProjectClient + OpenAI Responses API.

Requires: pip install rove-eval[azure]
"""

from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> dict:
    """Extract JSON from VLM response — handles raw JSON, markdown code blocks, and brace matching."""
    text = text.strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try markdown code block
    md_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if md_match:
        try:
            return json.loads(md_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Brace matching — find first { ... }
    start = text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break

    raise ValueError(f"Could not extract JSON from response: {text[:200]}")


class AzureFoundryProvider:
    """Provider for Azure AI Foundry inference via the OpenAI Responses API.

    Uses ``AIProjectClient.get_openai_client()`` to get an authenticated
    ``AzureOpenAI`` client, then calls the Responses API for inference.
    """

    def __init__(self, config: dict):
        endpoint_env = config.get("endpoint_env", "PROJECT_ENDPOINT")
        self._endpoint = os.environ.get(endpoint_env, "")
        self._model_name = config.get("model_name", "")
        self._max_tokens = config.get("max_tokens", 1024)
        self._temperature = config.get("temperature", 0.1)
        self._api_version = config.get("api_version", "2025-04-01-preview")
        self._total_tokens = 0
        self._project_client = None
        self._openai_client = None

    def _ensure_client(self):
        if self._openai_client is not None:
            return self._openai_client

        if not self._endpoint:
            raise RuntimeError("Azure AI Foundry not configured. Set PROJECT_ENDPOINT env var.")

        try:
            from azure.ai.projects import AIProjectClient
            from azure.identity import DefaultAzureCredential
        except ImportError as err:
            raise ImportError(
                "Azure AI Foundry SDK not installed. "
                "Install with: pip install rove-eval[azure]\n"
                "Required: azure-ai-projects>=1.0.0, openai"
            ) from err

        self._project_client = AIProjectClient(
            endpoint=self._endpoint,
            credential=DefaultAzureCredential(),
        )
        self._openai_client = self._project_client.get_openai_client(
            api_version=self._api_version,
        )
        logger.info("Azure Foundry provider initialized via AIProjectClient")
        return self._openai_client

    async def get_response(
        self,
        prompt: str,
        images: list[str] | None = None,
        instructions: str | None = None,
    ) -> dict:
        """Send prompt + images via Responses API and return parsed JSON."""
        content: list[dict] = [{"type": "input_text", "text": prompt}]
        for img in images or []:
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{img}",
                }
            )

        client = self._ensure_client()
        kwargs: dict = {
            "model": self._model_name,
            "input": [{"role": "user", "content": content}],
            "max_output_tokens": self._max_tokens,
            "temperature": self._temperature,
        }
        if instructions:
            kwargs["instructions"] = instructions

        response = client.responses.create(**kwargs)
        raw = response.output_text
        if response.usage:
            self._total_tokens += response.usage.total_tokens

        data = _extract_json(raw)
        data["raw_response"] = raw
        return data

    async def get_response_with_tools(
        self,
        input_items: list,
        tools: list[dict],
        instructions: str | None = None,
    ) -> object:
        """Send multi-turn input with tool definitions via Responses API.

        Returns the raw response object so the caller can inspect
        function_call items in response.output.
        """
        client = self._ensure_client()
        kwargs: dict = {
            "model": self._model_name,
            "input": input_items,
            "max_output_tokens": self._max_tokens,
            "temperature": self._temperature,
        }
        if instructions:
            kwargs["instructions"] = instructions
        if tools:
            kwargs["tools"] = tools

        response = client.responses.create(**kwargs)
        if response.usage:
            self._total_tokens += response.usage.total_tokens
        return response

    async def health_check(self) -> bool:
        try:
            self._ensure_client()
            return True
        except Exception:
            return False
