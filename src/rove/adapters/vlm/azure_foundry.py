"""Azure AI Foundry VLM adapter — supports Qwen2.5-VL, Phi-4-Multimodal, etc."""

from __future__ import annotations

import json
import logging
import os
import re
import time

from rove.models import SceneAnalysis, TaskPlan, VerificationResult

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


class AzureFoundryVLMAdapter:
    """VLM adapter using Azure AI Foundry inference endpoint."""

    def __init__(self, model_id: str, config: dict | None = None):
        self.model_id = model_id
        cfg = config or {}
        self.display_name = cfg.get("display_name", model_id)
        self._model_name = cfg.get("model_name", "")

        endpoint_env = cfg.get("endpoint_env", "AZURE_AI_FOUNDRY_ENDPOINT")
        key_env = cfg.get("api_key_env", "AZURE_AI_FOUNDRY_KEY")

        self._endpoint = os.environ.get(endpoint_env, "")
        self._api_key = os.environ.get(key_env, "")
        self._total_tokens = 0
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client

        if not self._endpoint:
            raise RuntimeError("Azure AI Foundry not configured. Set endpoint env var.")

        from azure.ai.inference import ChatCompletionsClient

        # Try DefaultAzureCredential first, fall back to API key
        try:
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
            kwargs = {}
            if ".openai.azure.com" in self._endpoint:
                kwargs["credential_scopes"] = ["https://cognitiveservices.azure.com/.default"]
            self._client = ChatCompletionsClient(
                endpoint=self._endpoint,
                credential=credential,
                **kwargs,
            )
            logger.info(f"Using DefaultAzureCredential for {self.model_id}")
        except Exception as err:
            if not self._api_key:
                raise RuntimeError(
                    "Azure auth failed: DefaultAzureCredential unavailable and no API key set. "
                    "Run 'az login' or set API key env var."
                ) from err
            from azure.core.credentials import AzureKeyCredential

            self._client = ChatCompletionsClient(
                endpoint=self._endpoint,
                credential=AzureKeyCredential(self._api_key),
            )
            logger.info(f"Using API key fallback for {self.model_id}")

        return self._client

    def _build_image_content(self, image_base64: str) -> dict:
        return {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
        }

    async def analyze_scene(self, image_base64: str, task: str) -> SceneAnalysis:
        from azure.ai.inference.models import SystemMessage, UserMessage

        prompt = (
            "You are a robotics vision system analyzing a workspace image.\n"
            f"Task: {task}\n\n"
            "Analyze the scene and return a JSON object with these fields:\n"
            '- "objects": list of objects, each with "name" (string), "bbox" [x,y,w,h] (approx pixel coords), "confidence" (0-1)\n'
            '- "spatial_relations": list of spatial relationship strings\n'
            '- "task_relevant": list of object names relevant to the task\n\n'
            "Return ONLY valid JSON, no markdown."
        )

        t0 = time.monotonic()
        client = self._get_client()
        response = client.complete(
            model=self._model_name or None,
            messages=[
                SystemMessage(
                    content="You are a precise robotics vision system. Always respond with valid JSON only."
                ),
                UserMessage(
                    content=[
                        {"type": "text", "text": prompt},
                        self._build_image_content(image_base64),
                    ]
                ),
            ],
            max_tokens=1024,
            temperature=0.1,
        )
        latency = (time.monotonic() - t0) * 1000
        raw = response.choices[0].message.content
        logger.info(
            f"analyze_scene latency={latency:.0f}ms tokens={response.usage.total_tokens if response.usage else '?'}"
        )

        if response.usage:
            self._total_tokens += response.usage.total_tokens

        data = _extract_json(raw)
        return SceneAnalysis(
            objects=data.get("objects", []),
            spatial_relations=data.get("spatial_relations", []),
            task_relevant=data.get("task_relevant", []),
            raw_response=raw,
        )

    async def plan_task(self, image_base64: str, task: str, scene: SceneAnalysis) -> TaskPlan:
        from azure.ai.inference.models import SystemMessage, UserMessage

        scene_summary = (
            f"Detected objects: {', '.join(o.get('name', '?') for o in scene.objects)}. "
            f"Spatial relations: {'; '.join(scene.spatial_relations)}. "
            f"Task-relevant: {', '.join(scene.task_relevant)}."
        )

        prompt = (
            "You are a robotics task planner.\n"
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

        client = self._get_client()
        response = client.complete(
            model=self._model_name or None,
            messages=[
                SystemMessage(
                    content="You are a precise robotics task planner. Always respond with valid JSON only."
                ),
                UserMessage(
                    content=[
                        {"type": "text", "text": prompt},
                        self._build_image_content(image_base64),
                    ]
                ),
            ],
            max_tokens=512,
            temperature=0.1,
        )
        raw = response.choices[0].message.content
        if response.usage:
            self._total_tokens += response.usage.total_tokens

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
        from azure.ai.inference.models import SystemMessage, UserMessage

        # Build context-aware prompt
        context_info = ""
        if context:
            plan = context.get("plan", {})
            if plan:
                target = plan.get("target_object", "")
                strategy = plan.get("strategy", "")
                if target:
                    context_info += f"Target object: {target}\n"
                if strategy:
                    context_info += f"Planned strategy: {strategy}\n"

        prompt = (
            "You are a robotics verification system.\n"
            f"Task: {task}\n"
            f"{context_info}\n"
            "You are given two images: BEFORE (first) and AFTER (second) executing the task.\n"
            "Determine if the task was completed successfully.\n\n"
            "Return a JSON object with:\n"
            '- "success": boolean\n'
            '- "confidence": float 0-1\n'
            '- "reasoning": string explaining your assessment\n\n'
            "Return ONLY valid JSON, no markdown."
        )

        client = self._get_client()
        response = client.complete(
            model=self._model_name or None,
            messages=[
                SystemMessage(
                    content="You are a precise robotics verification system. Always respond with valid JSON only."
                ),
                UserMessage(
                    content=[
                        {"type": "text", "text": prompt},
                        {"type": "text", "text": "BEFORE image:"},
                        self._build_image_content(before_image_base64),
                        {"type": "text", "text": "AFTER image:"},
                        self._build_image_content(after_image_base64),
                    ]
                ),
            ],
            max_tokens=512,
            temperature=0.1,
        )
        raw = response.choices[0].message.content
        if response.usage:
            self._total_tokens += response.usage.total_tokens

        data = _extract_json(raw)
        return VerificationResult(
            success=data.get("success", False),
            confidence=float(data.get("confidence", 0.5)),
            reasoning=data.get("reasoning", ""),
            raw_response=raw,
        )

    async def health_check(self) -> bool:
        try:
            self._get_client()
            return True
        except Exception:
            return False
