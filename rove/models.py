"""Shared data models for ROVE pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class PipelineStage(str, Enum):
    PERCEIVE = "perceive"
    PLAN = "plan"
    ACT = "act"
    VERIFY = "verify"


class StageStatus(str, Enum):
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
class TrialResult:
    eval_id: str
    task: str
    models: StageAssignment
    stages: list[PipelineStageResult] = field(default_factory=list)
    success: bool = False
    total_latency_ms: float = 0.0
    total_cost_usd: float = 0.0
