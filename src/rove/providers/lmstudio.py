"""LM Studio provider — HTTP client for local LM Studio API."""

from __future__ import annotations

import json
import logging
import os
import re

import httpx

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> dict:
    """Extract JSON from VLM response.

    Handles: raw JSON, <think>...</think> blocks (Qwen3), markdown code blocks,
    and brace-matching fallback.
    """
    text = text.strip()

    # Strip Qwen3 thinking tags
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

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

    logger.error(f"Could not extract JSON from response: {text[:500]}")
    raise ValueError(f"Could not extract JSON from response: {text[:200]}")


class LMStudioProvider:
    """Provider for models running in LM Studio via its HTTP API.

    Handles LM Studio's native /api/v1/chat format and OpenAI-compatible
    /v1/chat/completions fallback. Strips Qwen3 thinking tags.
    """

    def __init__(self, config: dict):
        self._lm_model_id = config.get("model_id", "")
        self._endpoint = config.get("endpoint", "http://127.0.0.1:1234")
        self._max_tokens = config.get("max_tokens", 2048)
        self._context_length = config.get("context_length", 4096)
        self._temperature = config.get("temperature", 0.1)

        api_key_env = config.get("api_key_env", "LM_API_TOKEN")
        self._api_key = os.environ.get(api_key_env, "")

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    @staticmethod
    def _build_image_input(image_base64: str) -> dict:
        """Build LM Studio image input. Adds data URL prefix if not present."""
        if image_base64.startswith("data:"):
            data_url = image_base64
        else:
            data_url = f"data:image/jpeg;base64,{image_base64}"
        return {"type": "image", "data_url": data_url}

    async def _chat(
        self,
        text: str,
        images: list[str] | None = None,
        instructions: str | None = None,
    ) -> str:
        """Send a chat request to LM Studio and return the text response."""
        inputs: list[dict] = []
        if instructions:
            inputs.append({"type": "text", "content": f"[System] {instructions}\n\n"})
        inputs.append({"type": "text", "content": text})
        for img in images or []:
            inputs.append(self._build_image_input(img))

        payload: dict = {
            "model": self._lm_model_id,
            "input": inputs,
            "context_length": self._context_length,
            "temperature": self._temperature,
        }

        url = f"{self._endpoint.rstrip('/')}/api/v1/chat"

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, json=payload, headers=self._build_headers())
            response.raise_for_status()

        data = response.json()
        logger.debug(f"LM Studio raw response keys: {list(data.keys())}")

        # LM Studio /api/v1/chat response: {"output": [...]}
        if "output" in data:
            output = data["output"]
            if isinstance(output, str):
                return output
            if isinstance(output, list):
                parts = [
                    item.get("content", "")
                    for item in output
                    if isinstance(item, dict) and item.get("content")
                ]
                return "".join(parts)
        # OpenAI-compatible /v1/chat/completions format
        if "choices" in data:
            return data["choices"][0]["message"]["content"]

        raise ValueError(f"Unexpected LM Studio response format: {list(data.keys())}")

    async def get_response(
        self,
        prompt: str,
        images: list[str] | None = None,
        instructions: str | None = None,
    ) -> dict:
        """Send prompt + images to LM Studio and return parsed JSON."""
        raw = await self._chat(prompt, images, instructions=instructions)
        data = _extract_json(raw)
        data["raw_response"] = raw
        return data

    async def health_check(self) -> bool:
        """Check if LM Studio is reachable."""
        try:
            url = f"{self._endpoint.rstrip('/')}/api/v1/models"
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url, headers=self._build_headers())
                return response.status_code == 200
        except Exception:
            return False
