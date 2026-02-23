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

from enum import StrEnum

from pydantic import BaseModel, Field


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


class SceneAnalysis(BaseModel):
    objects: list[dict] = Field(
        default_factory=list
    )  # [{"name": "red bracket", "bbox": [x,y,w,h], "confidence": 0.9}]
    spatial_relations: list[str] = Field(
        default_factory=list
    )  # ["red bracket is to the left of bin A"]
    task_relevant: list[str] = Field(default_factory=list)  # ["red bracket", "bin A"]
    raw_response: str = ""


class TaskPlan(BaseModel):
    strategy: str = ""  # high-level approach description
    reasoning: str = ""  # model's thinking/chain-of-thought
    steps: list[str] = Field(default_factory=list)  # ordered sub-steps
    target_object: str = ""  # optional, for manipulation tasks
    confidence: float = 0.0
    raw_response: str = ""


GraspPlan = TaskPlan  # backward compat alias


class ActionPrediction(BaseModel):
    actions: list[list[float]] = Field(default_factory=list)  # VLA trajectory (7-DOF deltas)
    tool_calls: list[dict] = Field(default_factory=list)  # agent tool invocations
    action_type: str = "trajectory"  # "trajectory" | "tool_calls"
    num_steps: int = 0
    confidence: float = 0.0
    raw_response: str = ""


class SimObservation(BaseModel):
    image_base64: str = ""  # post-step observation image
    proprioception: list[float] = Field(default_factory=list)  # joint states
    success: bool = False
    done: bool = False


class VerificationResult(BaseModel):
    success: bool
    confidence: float  # 0.0 - 1.0
    reasoning: str
    raw_response: str = ""


class PipelineStageResult(BaseModel):
    stage: str  # perceive | plan | act | verify
    status: StageStatus
    latency_ms: float = 0.0
    output: dict | None = None  # serialized stage output
    error: str | None = None
    model_id: str = ""


class StageAssignment(BaseModel):
    perceive: str | None  # model_id for perceive stage (None if skipped)
    plan: str | None  # model_id for plan stage (None if skipped)
    act: str | None  # model_id for act stage (None if skipped)
    verify: str  # model_id for verify stage
    sim: str  # sim environment id


class PipelineContext(BaseModel):
    """Accumulates stage outputs as the pipeline progresses."""

    task: str = ""
    image_base64: str = ""
    scene: SceneAnalysis | None = None
    plan: TaskPlan | None = None
    action: ActionPrediction | None = None
    proprioception: list[float] = Field(default_factory=list)
    after_image_base64: str = ""

    def to_dict(self) -> dict:
        """Serialize non-empty fields for adapter context bags.

        Excludes image blobs and raw_response to keep context compact.
        """
        d: dict = {"task": self.task}
        if self.scene:
            d["scene"] = self.scene.model_dump(exclude={"raw_response"})
        if self.plan:
            d["plan"] = self.plan.model_dump(
                include={"strategy", "target_object", "steps", "confidence"}
            )
        if self.action:
            d["action"] = self.action.model_dump(include={"action_type", "num_steps", "confidence"})
        if self.proprioception:
            d["proprioception"] = self.proprioception
        if self.after_image_base64:
            d["after_image_base64"] = self.after_image_base64
        return d


class Strategy(BaseModel):
    """A named pipeline configuration mapping models to stages."""

    id: str
    display_name: str
    description: str
    perceive: str | None = None  # model_id (None to skip stage)
    plan: str | None = None  # model_id (None to skip stage)
    act: str | None = None  # model_id (None to skip stage)
    verify: str = ""  # model_id (required)
    sim: str = ""  # sim_id (required)
    tags: list[str] = Field(default_factory=list)


class TrialResult(BaseModel):
    eval_id: str
    task: str
    models: StageAssignment
    stages: list[PipelineStageResult] = Field(default_factory=list)
    success: bool = False
    total_latency_ms: float = 0.0
    total_cost_usd: float = 0.0
