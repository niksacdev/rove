"""Azure AI Foundry Agent adapter — wraps Foundry agents via Responses API.

Uses azure-ai-projects async client + OpenAI Responses API for stateless,
single-turn agent execution with server-side tool calls (e.g. Bing grounding).

Requires: pip install rove-eval[azure]
"""

from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

# Stage-specific system prompts that tell the agent what JSON schema to return
_STAGE_PROMPTS: dict[str, str] = {
    "perceive": (
        "You are a robotics perception agent. Analyze the scene and return JSON with:\n"
        '{"objects": [{"name": str, "bbox": [x,y,w,h], "confidence": float}], '
        '"spatial_relations": [str], "task_relevant": [str], "raw_response": str}\n'
        "Return ONLY valid JSON, no markdown fences."
    ),
    "plan": (
        "You are a robotics task planning agent. Given a scene analysis and task, "
        "create an execution plan. Return JSON with:\n"
        '{"strategy": str, "reasoning": str, "steps": [str], '
        '"target_object": str, "confidence": float, "raw_response": str}\n'
        "Return ONLY valid JSON, no markdown fences."
    ),
    "act": (
        "You are a robotics action agent. Given a plan and task, determine "
        "tool calls for execution. Return JSON with:\n"
        '{"action_type": "tool_calls", "tool_calls": [{"tool": str, "args": dict}], '
        '"num_steps": int, "confidence": float, "raw_response": str}\n'
        "Return ONLY valid JSON, no markdown fences."
    ),
    "verify": (
        "You are a robotics verification agent. Assess whether the task was completed "
        "successfully. Return JSON with:\n"
        '{"success": bool, "confidence": float, "reasoning": str, "raw_response": str}\n'
        "Return ONLY valid JSON, no markdown fences."
    ),
}

# Fallback defaults when JSON parsing fails
_STAGE_FALLBACKS: dict[str, dict] = {
    "perceive": {"objects": [], "spatial_relations": [], "task_relevant": []},
    "plan": {
        "strategy": "unknown",
        "reasoning": "",
        "steps": [],
        "target_object": "",
        "confidence": 0.0,
    },
    "act": {"action_type": "tool_calls", "tool_calls": [], "num_steps": 0, "confidence": 0.0},
    "verify": {"success": False, "confidence": 0.0, "reasoning": "Could not parse agent response"},
}


def _strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` fences from LLM output."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


class AzureFoundryAgentAdapter:
    """Agent adapter using Azure AI Foundry's Responses API.

    Uses stateless single-turn calls via OpenAI Responses API. The Foundry
    agent's tools (Bing grounding, code interpreter, etc.) execute server-side.
    """

    def __init__(self, model_id: str = "foundry-agent", config: dict | None = None):
        self.model_id = model_id
        cfg = config or {}
        self.display_name = cfg.get("display_name", "Foundry Agent")
        self._project_endpoint = cfg.get(
            "project_endpoint",
            os.environ.get("AZURE_AI_FOUNDRY_ENDPOINT", ""),
        )
        self._agent_name = cfg.get("agent_name", "")

        # Lazy-initialized
        self._ai_client = None
        self._openai_client = None
        self._agent_model: str | None = None
        self._agent_instructions: str | None = None
        self._agent_temperature: float | None = None

    async def _ensure_client(self) -> None:
        """Lazily create the AIProjectClient and async OpenAI client."""
        if self._openai_client is not None:
            return

        try:
            from azure.ai.projects.aio import AIProjectClient
            from azure.identity.aio import DefaultAzureCredential
        except ImportError as err:
            raise ImportError(
                "Azure AI Foundry SDK not installed. "
                "Install with: pip install rove-eval[azure]\n"
                "Required: azure-ai-projects>=2.0.0b3, azure-identity>=1.19.0, openai>=1.0.0"
            ) from err

        if not self._project_endpoint:
            raise RuntimeError(
                "Azure AI Foundry project endpoint not configured. "
                "Set 'project_endpoint' in rove.yaml config or "
                "AZURE_AI_FOUNDRY_ENDPOINT environment variable."
            )

        credential = DefaultAzureCredential()
        self._ai_client = AIProjectClient(
            endpoint=self._project_endpoint,
            credential=credential,
        )
        self._openai_client = self._ai_client.get_openai_client()

    async def _ensure_agent(self) -> None:
        """Lookup agent by name and cache its instructions/model/temperature."""
        if self._agent_model is not None:
            return

        await self._ensure_client()

        if not self._agent_name:
            raise RuntimeError("agent_name not configured. Set 'agent_name' in rove.yaml config.")

        try:
            agent = await self._ai_client.agents.get(self._agent_name)
        except Exception as exc:
            raise RuntimeError(f"Failed to get Foundry agent '{self._agent_name}': {exc}") from exc

        # Extract model/instructions/temperature from the latest version definition
        # agents.get() returns the full version data inline
        defn = None
        if agent.versions and agent.versions.latest:
            latest = agent.versions.latest
            defn = getattr(latest, "definition", None) or latest.get("definition")

        if defn:
            # defn may be a model object or a dict depending on SDK version
            if isinstance(defn, dict):
                self._agent_model = defn.get("model")
                self._agent_instructions = defn.get("instructions", "")
                self._agent_temperature = defn.get("temperature")
            else:
                self._agent_model = getattr(defn, "model", None)
                self._agent_instructions = getattr(defn, "instructions", None) or ""
                self._agent_temperature = getattr(defn, "temperature", None)
        else:
            self._agent_model = None
            self._agent_instructions = ""
            self._agent_temperature = None

        logger.info(
            "Resolved Foundry agent '%s' → model=%s",
            self._agent_name,
            self._agent_model,
        )

    async def run_stage(
        self,
        stage: str,
        image_base64: str,
        task: str,
        context: dict | None = None,
    ) -> dict:
        """Execute a pipeline stage via the Foundry agent's Responses API."""
        try:
            from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential
        except ImportError:
            retry = None

        async def _call() -> dict:
            await self._ensure_agent()
            return await self._responses_call(stage, image_base64, task, context)

        if retry is not None:

            def _is_transient(exc: BaseException) -> bool:
                """Retry on 429, 503, or timeout errors."""
                exc_str = str(exc).lower()
                return any(k in exc_str for k in ("429", "503", "timeout", "rate limit"))

            _call_with_retry = retry(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=1, max=10),
                retry=retry_if_exception(_is_transient),
            )(_call)
            return await _call_with_retry()

        return await _call()

    async def _responses_call(
        self,
        stage: str,
        image_base64: str,
        task: str,
        context: dict | None,
    ) -> dict:
        """Single-turn Responses API call."""
        stage_prompt = _STAGE_PROMPTS.get(stage, _STAGE_PROMPTS["verify"])

        # Build instructions combining agent instructions + stage prompt
        instructions = self._agent_instructions or ""
        if instructions:
            instructions += "\n\n"
        instructions += stage_prompt

        # Build input items
        user_parts: list[dict] = []
        user_parts.append({"type": "input_text", "text": f"Task: {task}"})

        if context:
            user_parts.append(
                {
                    "type": "input_text",
                    "text": f"Context: {json.dumps(context, default=str)}",
                }
            )

        if image_base64:
            user_parts.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{image_base64}",
                }
            )

        input_items = [{"role": "user", "content": user_parts}]

        kwargs: dict = {
            "model": self._agent_model,
            "instructions": instructions,
            "input": input_items,
        }
        if self._agent_temperature is not None:
            kwargs["temperature"] = self._agent_temperature

        response = await self._openai_client.responses.create(**kwargs)

        # Extract text from response output
        response_text = ""
        for item in response.output:
            if hasattr(item, "content"):
                for part in item.content:
                    if hasattr(part, "text"):
                        response_text += part.text

        if not response_text:
            logger.warning("Empty response from Foundry agent for stage '%s'", stage)
            fallback = dict(_STAGE_FALLBACKS.get(stage, {}))
            fallback["raw_response"] = "empty response"
            return fallback

        # Parse JSON
        cleaned = _strip_markdown_fences(response_text)
        try:
            result = json.loads(cleaned)
            if "raw_response" not in result:
                result["raw_response"] = response_text
            return result
        except (json.JSONDecodeError, ValueError):
            logger.warning(
                "Could not parse JSON from Foundry agent (stage=%s), using fallback. Raw text: %s",
                stage,
                response_text[:200],
            )
            fallback = dict(_STAGE_FALLBACKS.get(stage, {}))
            fallback["raw_response"] = response_text
            return fallback

    async def health_check(self) -> bool:
        """Check if the adapter can connect to Azure AI Foundry."""
        try:
            await self._ensure_client()
            return True
        except Exception:
            return False
