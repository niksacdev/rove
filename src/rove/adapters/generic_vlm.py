"""Generic VLM adapter — single provider-backed adapter for all HTTP VLM/LLM services."""

from __future__ import annotations

from rove.models import SceneAnalysis, TaskPlan, VerificationResult
from rove.providers import Provider
from rove.utils.prompt_loader import PromptManager


def _format_scene(scene: SceneAnalysis) -> str:
    """Format a SceneAnalysis into a text summary for the plan prompt."""
    parts = []
    for o in scene.objects:
        name = o.get("name", "?")
        pos = o.get("position")
        if pos:
            parts.append(f"{name} at [{pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f}]m")
        else:
            parts.append(name)
    return (
        f"Detected objects: {', '.join(parts)}. "
        f"Spatial relations: {'; '.join(scene.spatial_relations)}. "
        f"Task-relevant: {', '.join(scene.task_relevant)}."
    )


class GenericVLMAdapter:
    """VLM adapter that delegates HTTP transport to an injected Provider.

    One class handles all HTTP-backed VLMs (Azure Foundry, LM Studio, future providers).
    Prompt formatting and protocol implementation live here; auth/retry/HTTP live in providers.
    """

    def __init__(
        self,
        model_id: str,
        display_name: str,
        provider: Provider,
        prompt_manager: PromptManager,
    ):
        self.model_id = model_id
        self.display_name = display_name
        self._provider = provider
        self._prompts = prompt_manager

    async def _call(
        self,
        prompt: str,
        output_model: type,
        images: list[str],
        instructions: str | None = None,
    ) -> object:
        """Shared call pattern: prompt → provider → parse → validate."""
        data = await self._provider.get_response(
            prompt,
            images=images,
            instructions=instructions,
        )
        return output_model.model_validate(data)

    async def analyze_scene(self, image_base64: str, task: str) -> SceneAnalysis:
        prompt = self._prompts.render("perceive_user", task=task)
        system = self._prompts.load("system")
        return await self._call(prompt, SceneAnalysis, [image_base64], instructions=system)

    async def plan_task(self, image_base64: str, task: str, scene: SceneAnalysis) -> TaskPlan:
        prompt = self._prompts.render("plan_user", task=task, scene_summary=_format_scene(scene))
        system = self._prompts.load("system")
        return await self._call(prompt, TaskPlan, [image_base64], instructions=system)

    async def verify_success(
        self,
        before_image_base64: str,
        after_image_base64: str,
        task: str,
        context: dict | None = None,
    ) -> VerificationResult:
        prompt = self._prompts.render_verify(task, context or {})
        system = self._prompts.render_verify_system()
        return await self._call(
            prompt,
            VerificationResult,
            [before_image_base64, after_image_base64],
            instructions=system,
        )

    async def health_check(self) -> bool:
        return await self._provider.health_check()
