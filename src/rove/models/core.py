"""Shared data models for ROVE pipeline."""

from __future__ import annotations

__all__ = [
    "ActionPlausibility",
    "ActionPrediction",
    "ExampleData",
    "GraspPlan",
    "GroundTruthCheck",
    "PipelineContext",
    "PipelineStage",
    "PipelineStageResult",
    "SceneAnalysis",
    "SimObservation",
    "StageAssignment",
    "StageCheck",
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
    )  # [{"name": str, "bbox": [x,y,w,h], "position": [x,y,z] (meters, optional), "confidence": float}]
    spatial_relations: list[str] = Field(
        default_factory=list
    )  # ["red bracket is to the left of bin A"]
    task_relevant: list[str] = Field(default_factory=list)  # ["red bracket", "bin A"]
    environment_distribution: str = ""  # description of the environment/domain
    raw_response: str = ""


class TaskPlan(BaseModel):
    strategy: str = ""  # high-level approach description
    reasoning: str = ""  # model's thinking/chain-of-thought
    steps: list[str] = Field(default_factory=list)  # ordered sub-steps
    target_object: str = ""  # optional, for manipulation tasks
    confidence: float = 0.0
    task_repertoire: list[str] = Field(
        default_factory=list
    )  # domain-specific capabilities required
    artifacts: list[str] = Field(default_factory=list)  # measurable success markers
    degradation_profile: list[str] = Field(default_factory=list)  # domain-specific challenges
    raw_response: str = ""


GraspPlan = TaskPlan  # backward compat alias


class ExampleData(BaseModel):
    """Stable container for all data provided by a dataset example.

    Carries ground truth QA, sensor data, and any future example-level
    metadata through the pipeline without polluting function signatures.
    """

    ground_truth: dict | None = None  # eval_qa from manifest
    extras: dict = Field(default_factory=dict)  # proprioception, robot, etc.


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


class StageCheck(BaseModel):
    stage: str  # "perceive" | "plan" | "act"
    passed: bool
    confidence: float
    reasoning: str


class GroundTruthCheck(BaseModel):
    question: str
    choices: list[str]
    correct_answer: str
    pipeline_answer: str  # VLM judge's answer from pipeline outputs
    correct: bool
    confidence: float


class ActionPlausibility(BaseModel):
    """Structured plausibility checks derivable from VLA output without simulation."""

    bounds_check: bool = True  # action deltas within physically plausible ranges
    smoothness: bool = True  # no sudden jumps between consecutive steps
    gripper_consistency: bool = True  # gripper open/close pattern matches task type
    plan_alignment: float = 0.0  # 0-1, how rigorously the VLA trajectory follows the plan steps
    reasoning: str = ""  # explanation of plausibility assessment


class VerificationResult(BaseModel):
    success: bool
    confidence: float  # 0.0 - 1.0
    reasoning: str
    raw_response: str = ""
    completed_stages: list[str] = Field(default_factory=list)
    stage_checks: list[StageCheck] = Field(default_factory=list)
    ground_truth: GroundTruthCheck | None = None
    action_plausibility: ActionPlausibility | None = None


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
    completed_stages: list[str] = Field(default_factory=list)
    ground_truth: dict | None = None

    def to_dict(self) -> dict:
        """Serialize non-empty fields for adapter context bags.

        Excludes image blobs and raw_response to keep context compact.
        """
        d: dict = {"task": self.task}
        if self.scene:
            d["scene"] = self.scene.model_dump(exclude={"raw_response"})
        if self.plan:
            d["plan"] = self.plan.model_dump(
                include={
                    "strategy",
                    "target_object",
                    "steps",
                    "confidence",
                    "task_repertoire",
                    "artifacts",
                    "degradation_profile",
                }
            )
        if self.action:
            action_d = self.action.model_dump(
                include={"action_type", "num_steps", "confidence", "actions"}
            )
            actions = action_d.get("actions", [])
            if actions and len(actions[0]) >= 7:
                # Summarize gripper events for the verifier
                action_d["gripper_events"] = _extract_gripper_events(actions)
            # Cap trajectory to first/last steps to keep prompt compact
            if actions and len(actions) > 10:
                action_d["actions"] = actions[:5] + actions[-5:]
                action_d["actions_truncated"] = True
                action_d["actions_note"] = (
                    f"Showing steps 1-5 and {len(actions) - 4}-{len(actions)} "
                    f"of {len(actions)} total. See gripper_events for full summary."
                )
            d["action"] = action_d
        if self.proprioception:
            d["proprioception"] = self.proprioception
        if self.after_image_base64:
            d["after_image_base64"] = self.after_image_base64
        if self.completed_stages:
            d["completed_stages"] = self.completed_stages
        if self.ground_truth:
            d["ground_truth"] = self.ground_truth
        return d


def _extract_gripper_events(actions: list[list[float]]) -> list[dict]:
    """Extract gripper open/close transitions from a trajectory.

    Scans the last DOF (grip) for transitions between open (>0.5) and
    closed (<0.5) states, returning a compact event list for the verifier.
    """
    events: list[dict] = []
    prev_open = True  # assume starts open
    for i, a in enumerate(actions):
        grip = a[-1]  # last DOF = gripper
        is_open = grip > 0.0
        if is_open != prev_open:
            events.append(
                {
                    "step": i + 1,
                    "action": "open" if is_open else "close",
                    "grip_value": round(grip, 3),
                }
            )
            prev_open = is_open
    return events


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
