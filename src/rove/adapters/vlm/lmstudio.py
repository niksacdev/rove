"""LM Studio VLM adapter — calls local LM Studio HTTP API for vision-language models."""

from __future__ import annotations

import json
import logging
import os
import re

import httpx

from rove.adapters.vlm.verify_prompts import build_verify_prompt
from rove.models import SceneAnalysis, TaskPlan, VerificationResult

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


class LMStudioVLMAdapter:
    """VLM adapter for models running in LM Studio via its HTTP API.

    LM Studio exposes /api/v1/chat with a multimodal input format:
    - {"type": "text", "content": "..."} for text
    - {"type": "image", "data_url": "data:image/...;base64,..."} for images
    """

    def __init__(self, model_id: str, config: dict | None = None):
        self.model_id = model_id
        cfg = config or {}
        self.display_name = cfg.get("display_name", model_id)
        self._lm_model_id = cfg.get("model_id", model_id)
        self._endpoint = cfg.get("endpoint", "http://127.0.0.1:1234")
        self._max_tokens = cfg.get("max_tokens", 2048)
        self._context_length = cfg.get("context_length", 4096)
        self._temperature = cfg.get("temperature", 0.1)

        api_key_env = cfg.get("api_key_env", "LM_API_TOKEN")
        self._api_key = os.environ.get(api_key_env, "")

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _build_image_input(self, image_base64: str) -> dict:
        """Build LM Studio image input. Adds data URL prefix if not present."""
        if image_base64.startswith("data:"):
            data_url = image_base64
        else:
            data_url = f"data:image/jpeg;base64,{image_base64}"
        return {"type": "image", "data_url": data_url}

    async def _chat(self, text: str, images: list[str] | None = None) -> str:
        """Send a chat request to LM Studio and return the text response."""
        inputs: list[dict] = [{"type": "text", "content": text}]
        for img in images or []:
            inputs.append(self._build_image_input(img))

        payload = {
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

        # LM Studio /api/v1/chat response: {"output": [...], "model_instance_id", "stats", "response_id"}
        # output is a list of content blocks: [{"type": "message", "content": "..."}]
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

    async def analyze_scene(self, image_base64: str, task: str) -> SceneAnalysis:
        prompt = (
            "You are the PERCEIVE stage of a robotics evaluation pipeline. "
            "Your job is to analyze the workspace image and identify all objects, their positions, and spatial relationships.\n\n"
            f"Task: {task}\n\n"
            "Analyze the scene and return a JSON object with these fields:\n"
            '- "objects": list of objects, each with "name" (string), "bbox" [x,y,w,h] (approx pixel coords), "confidence" (0-1)\n'
            '- "spatial_relations": list of spatial relationship strings\n'
            '- "task_relevant": list of object names relevant to the task\n\n'
            "Return ONLY valid JSON, no markdown."
        )
        raw = await self._chat(prompt, [image_base64])
        logger.info(f"analyze_scene raw response length={len(raw)}")
        data = _extract_json(raw)
        return SceneAnalysis(
            objects=data.get("objects", []),
            spatial_relations=data.get("spatial_relations", []),
            task_relevant=data.get("task_relevant", []),
            raw_response=raw,
        )

    async def plan_task(self, image_base64: str, task: str, scene: SceneAnalysis) -> TaskPlan:
        scene_summary = (
            f"Detected objects: {', '.join(o.get('name', '?') for o in scene.objects)}. "
            f"Spatial relations: {'; '.join(scene.spatial_relations)}. "
            f"Task-relevant: {', '.join(scene.task_relevant)}."
        )
        prompt = (
            "You are the PLAN stage of a robotics evaluation pipeline. "
            "Given the scene analysis from the previous perceive stage, your job is to create a step-by-step manipulation plan.\n\n"
            f"Task: {task}\n"
            f"Scene analysis: {scene_summary}\n\n"
            "Plan a strategy to accomplish the task. Return a JSON object with:\n"
            '- "strategy": string (high-level approach description)\n'
            '- "reasoning": string (explain your thinking)\n'
            '- "steps": list of strings (ordered sub-steps to accomplish the task)\n'
            '- "target_object": string (primary object to manipulate, if any)\n'
            '- "confidence": float 0-1\n\n'
            "Return ONLY valid JSON, no markdown."
        )
        raw = await self._chat(prompt, [image_base64])
        data = _extract_json(raw)
        return TaskPlan(
            strategy=data.get("strategy", ""),
            reasoning=data.get("reasoning", ""),
            steps=data.get("steps", []),
            target_object=data.get("target_object", ""),
            confidence=float(data.get("confidence", 0.0)),
            raw_response=raw,
        )

    async def verify_success(
        self,
        before_image_base64: str,
        after_image_base64: str,
        task: str,
        context: dict | None = None,
    ) -> VerificationResult:
        prompt = build_verify_prompt(task, context or {})
        raw = await self._chat(prompt, [before_image_base64, after_image_base64])
        data = _extract_json(raw)
        data["raw_response"] = raw
        return VerificationResult.model_validate(data)

    async def health_check(self) -> bool:
        """Check if LM Studio is reachable."""
        try:
            url = f"{self._endpoint.rstrip('/')}/api/v1/models"
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url, headers=self._build_headers())
                return response.status_code == 200
        except Exception:
            return False
