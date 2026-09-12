"""Adapter Protocols — the contracts for all model adapters."""

from __future__ import annotations

__all__ = [
    "AgentAdapter",
    "SimAdapter",
    "StageAdapter",
    "VLAAdapter",
    "VLMAdapter",
    "VerifierAdapter",
]

from typing import Protocol, runtime_checkable

from rove.models import (
    ActionPrediction,
    RobotEmbodiment,
    SceneAnalysis,
    SimObservation,
    TaskPlan,
    VerificationResult,
    VLACapabilities,
)
from rove.models.verification import EvaluatorContext, EvaluatorResult


@runtime_checkable
class VLMAdapter(Protocol):
    """Protocol for Vision-Language Model adapters."""

    model_id: str
    display_name: str

    async def analyze_scene(self, image_base64: str, task: str) -> SceneAnalysis:
        """Perceive step: analyze workspace image and identify objects."""
        ...

    async def plan_task(
        self, image_base64: str, task: str, scene: SceneAnalysis, **kwargs
    ) -> TaskPlan:
        """Plan step: generate a task strategy given scene analysis.

        Optional kwargs:
            task_metadata: dict with eval_category, constraints, correction, expected_subtasks
        """
        ...

    async def verify_success(
        self,
        before_image_base64: str,
        after_image_base64: str,
        task: str,
        context: dict | None = None,
    ) -> VerificationResult:
        """Verify step: compare before/after images to check task success."""
        ...

    async def health_check(self) -> bool:
        """Check if the adapter is ready to serve requests."""
        ...


@runtime_checkable
class VLAAdapter(Protocol):
    """Protocol for Vision-Language-Action (VLA) model adapters."""

    model_id: str
    display_name: str
    capabilities: VLACapabilities

    async def predict_action(
        self,
        image_base64: str,
        task: str,
        proprioception: list[float] | None = None,
        plan: TaskPlan | None = None,
        embodiment: RobotEmbodiment | None = None,
    ) -> ActionPrediction:
        """Predict action(s) given current observation and task."""
        ...

    async def health_check(self) -> bool: ...


@runtime_checkable
class AgentAdapter(Protocol):
    """Protocol for AI Foundry agents that can serve multiple pipeline stages."""

    model_id: str
    display_name: str

    async def run_stage(
        self,
        stage: str,
        image_base64: str,
        task: str,
        context: dict | None = None,
    ) -> dict:
        """Execute any pipeline stage. Returns stage-appropriate output as dict."""
        ...

    async def health_check(self) -> bool: ...


@runtime_checkable
class SimAdapter(Protocol):
    """Protocol for simulation environment adapters."""

    model_id: str
    display_name: str

    async def reset(self, task: str) -> SimObservation:
        """Reset the sim environment for a new episode."""
        ...

    async def step(self, action: list[float]) -> SimObservation:
        """Execute one action step and return the new observation."""
        ...

    async def get_observation(self) -> SimObservation:
        """Get current observation without stepping."""
        ...


@runtime_checkable
class VerifierAdapter(Protocol):
    model_id: str
    display_name: str
    version: str

    async def evaluate(self, context: EvaluatorContext) -> EvaluatorResult: ...


StageAdapter = VLMAdapter | VLAAdapter | AgentAdapter | VerifierAdapter
