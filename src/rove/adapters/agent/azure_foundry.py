"""Azure AI Foundry Agent adapter — wraps Foundry agents that execute their own tools.

Requires: pip install rove-eval[foundry]
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class AzureFoundryAgentAdapter:
    """Agent adapter using Azure AI Foundry's agent service.

    The Foundry agent runs its own tools (ROS2, code interpreter, etc.).
    ROVE captures the agent's output without controlling tool execution.
    """

    def __init__(self, model_id: str = "foundry-agent", config: dict | None = None):
        self.model_id = model_id
        cfg = config or {}
        self.display_name = cfg.get("display_name", "Foundry Agent")
        self._endpoint_env = cfg.get("endpoint_env", "AZURE_FOUNDRY_AGENT_ENDPOINT")
        self._agent_id = cfg.get("agent_id", "")

        # Guarded import — only needed when actually used
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client

        try:
            from azure.ai.agents import AgentsClient
            from azure.identity import DefaultAzureCredential
        except ImportError:
            raise ImportError(
                "Azure AI Foundry agent SDK not installed. "
                "Install with: pip install rove-eval[foundry]\n"
                "Required packages: azure-ai-agents>=1.1.0, azure-ai-projects>=1.0.0"
            )

        import os
        endpoint = os.environ.get(self._endpoint_env, "")
        if not endpoint:
            raise RuntimeError(
                f"Azure AI Foundry agent not configured. "
                f"Set {self._endpoint_env} environment variable."
            )

        self._client = AgentsClient(
            endpoint=endpoint,
            credential=DefaultAzureCredential(),
        )
        return self._client

    async def run_stage(
        self,
        stage: str,
        image_base64: str,
        task: str,
        context: dict | None = None,
    ) -> dict:
        """Execute a pipeline stage via the Foundry agent.

        Creates a thread, sends the task + image + context, runs the agent,
        and captures the response. The agent executes its own tools.
        """
        client = self._get_client()

        # Build message content
        message_content = f"Stage: {stage}\nTask: {task}\n"
        if context:
            import json
            message_content += f"Context: {json.dumps(context)}\n"
        if image_base64:
            message_content += f"[Image provided as base64, {len(image_base64)} chars]\n"

        # Create thread → add message → run agent → capture result
        thread = client.threads.create()
        client.messages.create(
            thread_id=thread.id,
            role="user",
            content=message_content,
        )

        run = client.runs.create_and_process(
            thread_id=thread.id,
            agent_id=self._agent_id,
        )

        # Get the agent's response
        messages = client.messages.list(thread_id=thread.id)
        last_message = None
        for msg in messages:
            if msg.role == "assistant":
                last_message = msg
                break

        if last_message is None:
            return {"error": "No response from agent", "stage": stage}

        # Extract text content
        response_text = ""
        for content_part in last_message.content:
            if hasattr(content_part, "text"):
                response_text += content_part.text.value

        # Try to parse as JSON, fall back to raw text
        import json
        try:
            return json.loads(response_text)
        except (json.JSONDecodeError, ValueError):
            return {
                "raw_response": response_text,
                "stage": stage,
            }

    async def health_check(self) -> bool:
        try:
            self._get_client()
            return True
        except Exception:
            return False
