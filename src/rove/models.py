"""Shared data models for ROVE pipeline."""

from __future__ import annotations

__all__ = [
    "ActionPrediction",
    "GraspPlan",
    "PipelineContext",
    "PipelineStage",
    "PipelineStageResult",
    "SceneAnalysis",
    "SimObservation",
    "StageAssignment",
    "StageStatus",
    "Strategy",
    "TaskPlan",
    "TrialResult",
    "VerificationResult",
]

from dataclasses import dataclass, field
from enum import StrEnum


class PipelineStage(StrEnum):
    PERCEIVE = "perceive"
    PLAN = "plan"
    ACT = "act"
    VERIFY = "verify"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class SceneAnalysis:
    objects: list[dict]  # [{"name": "red bracket", "bbox": [x,y,w,h], "confidence": 0.9}]
    spatial_relations: list[str]  # ["red bracket is to the left of bin A"]
    task_relevant: list[str]  # ["red bracket", "bin A"]
    raw_response: str = ""


@dataclass
class TaskPlan:
    strategy: str  # high-level approach description
    reasoning: str  # model's thinking/chain-of-thought
    steps: list[str] = field(default_factory=list)  # ordered sub-steps
    target_object: str = ""  # optional, for manipulation tasks
    confidence: float = 0.0
    raw_response: str = ""


GraspPlan = TaskPlan  # backward compat alias


@dataclass
class ActionPrediction:
    actions: list[list[float]] = field(default_factory=list)  # VLA trajectory (7-DOF deltas)
    tool_calls: list[dict] = field(default_factory=list)  # agent tool invocations
    action_type: str = "trajectory"  # "trajectory" | "tool_calls"
    num_steps: int = 0
    confidence: float = 0.0
    raw_response: str = ""


@dataclass
class SimObservation:
    image_base64: str = ""  # post-step observation image
    proprioception: list[float] = field(default_factory=list)  # joint states
    success: bool = False
    done: bool = False


@dataclass
class VerificationResult:
    success: bool
    confidence: float  # 0.0 - 1.0
    reasoning: str
    raw_response: str = ""


@dataclass
class PipelineStageResult:
    stage: str  # perceive | plan | act | verify
    status: StageStatus
    latency_ms: float = 0.0
    output: dict | None = None  # serialized stage output
    error: str | None = None
    model_id: str = ""


@dataclass
class StageAssignment:
    perceive: str  # model_id for perceive stage
    plan: str  # model_id for plan stage
    act: str  # model_id for act stage
    verify: str  # model_id for verify stage
    sim: str  # sim environment id


@dataclass
class PipelineContext:
    """Accumulates stage outputs as the pipeline progresses."""

    task: str = ""
    image_base64: str = ""
    scene: SceneAnalysis | None = None
    plan: TaskPlan | None = None
    action: ActionPrediction | None = None
    proprioception: list[float] = field(default_factory=list)
    after_image_base64: str = ""

    def to_dict(self) -> dict:
        """Serialize non-empty fields for adapter context bags."""
        d: dict = {"task": self.task}
        if self.scene:
            d["scene"] = {
                "objects": self.scene.objects,
                "spatial_relations": self.scene.spatial_relations,
                "task_relevant": self.scene.task_relevant,
            }
        if self.plan:
            d["plan"] = {
                "strategy": self.plan.strategy,
                "target_object": self.plan.target_object,
                "steps": self.plan.steps,
                "confidence": self.plan.confidence,
            }
        if self.action:
            d["action"] = {
                "action_type": self.action.action_type,
                "num_steps": self.action.num_steps,
                "confidence": self.action.confidence,
            }
        if self.proprioception:
            d["proprioception"] = self.proprioception
        if self.after_image_base64:
            d["after_image_base64"] = self.after_image_base64
        return d


@dataclass
class Strategy:
    """A named pipeline configuration mapping models to stages."""

    id: str
    display_name: str
    description: str
    perceive: str  # model_id
    plan: str  # model_id
    act: str  # model_id
    verify: str  # model_id
    sim: str  # sim_id
    tags: list[str] = field(default_factory=list)


@dataclass
class TrialResult:
    eval_id: str
    task: str
    models: StageAssignment
    stages: list[PipelineStageResult] = field(default_factory=list)
    success: bool = False
    total_latency_ms: float = 0.0
    total_cost_usd: float = 0.0
