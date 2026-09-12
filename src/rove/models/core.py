"""Shared data models for ROVE pipeline."""

from __future__ import annotations

__all__ = [
    "ActionPlausibility",
    "ActionPrediction",
    "ActionSpace",
    "EvaluationProvenance",
    "ExampleData",
    "GraspPlan",
    "GroundTruthCheck",
    "PipelineContext",
    "PipelineStage",
    "PipelineStageResult",
    "RobotEmbodiment",
    "SceneAnalysis",
    "SimObservation",
    "StageAssignment",
    "StageCheck",
    "StageStatus",
    "Strategy",
    "TaskPlan",
    "TrialResult",
    "VLACapabilities",
    "VerificationResult",
]

import hashlib
import sys
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from rove.models.verification import CheckConfig, CheckResult, EvaluatorResult


class EvaluationProvenance(BaseModel):
    """Mandatory metadata captured at evaluation start for reproducibility."""

    timestamp: str = ""  # ISO 8601 UTC
    hostname: str = ""
    rove_version: str = ""
    python_version: str = ""
    config_hash: str = ""  # SHA256 of rove.yaml contents
    seed: int | None = None
    image_sha256: str = ""  # fingerprint of input image
    strategy_ids: list[str] = Field(default_factory=list)
    resolved_models: dict = Field(default_factory=dict)
    package_versions: dict = Field(default_factory=dict)

    @staticmethod
    def compute_image_hash(image_base64: str) -> str:
        """SHA256 of the raw base64 string (not decoded bytes)."""
        return hashlib.sha256(image_base64.encode()).hexdigest()[:16]

    @staticmethod
    def compute_config_hash(config_path: str | None = None) -> str:
        """SHA256 of rove.yaml contents."""
        from pathlib import Path

        if config_path is None:
            candidates = [
                Path("rove.yaml"),
                Path(__file__).parent.parent.parent.parent / "rove.yaml",
            ]
            for c in candidates:
                if c.exists():
                    config_path = str(c)
                    break
        if config_path is None:
            return ""
        try:
            content = Path(config_path).read_bytes()
            return hashlib.sha256(content).hexdigest()[:16]
        except Exception:
            return ""

    @staticmethod
    def collect_package_versions() -> dict[str, str]:
        """Collect versions of key dependencies."""
        versions: dict[str, str] = {}
        for pkg in ("fastapi", "pydantic", "torch", "transformers", "mujoco", "lerobot"):
            try:
                from importlib.metadata import version

                versions[pkg] = version(pkg)
            except Exception:  # nosec B110
                pass
        return versions

    @classmethod
    def build(
        cls,
        image_base64: str,
        strategy_ids: list[str],
        resolved_models: dict | None = None,
        seed: int | None = None,
    ) -> EvaluationProvenance:
        """Build provenance snapshot for current environment."""
        from datetime import UTC, datetime
        from importlib.metadata import version

        try:
            rove_ver = version("rove-eval")
        except Exception:
            rove_ver = "dev"

        return cls(
            timestamp=datetime.now(UTC).isoformat(),
            hostname="",  # Avoid leaking a personal/work machine name in shared reports
            rove_version=rove_ver,
            python_version=sys.version.split()[0],
            config_hash=cls.compute_config_hash(),
            seed=seed,
            image_sha256=cls.compute_image_hash(image_base64),
            strategy_ids=strategy_ids,
            resolved_models=resolved_models or {},
            package_versions=cls.collect_package_versions(),
        )


class ActionSpace(StrEnum):
    """Coordinate frame of the action vector."""

    JOINT_POSITION = "joint_position"
    JOINT_DELTA = "joint_delta"
    EEF_DELTA = "eef_delta"
    EEF_ABSOLUTE = "eef_absolute"


class RobotEmbodiment(BaseModel):
    """Robot descriptor carried by tasks. Built from URDF + manifest metadata.

    NOT a full URDF parser — only the metadata needed to broker VLA inference.
    """

    robot_type: str  # "panda", "ur5e", "so100"
    arm_dof: int  # independent revolute/prismatic joints
    gripper_dof: int = 1  # 0 for suction/fixed-tool
    action_space: ActionSpace = ActionSpace.EEF_DELTA
    proprioception_space: ActionSpace = ActionSpace.JOINT_POSITION
    gripper_index: int | None = None  # which dim is gripper (typically last)
    state_dim_override: int | None = None  # when state_dim != action_dim
    urdf_path: str | None = None
    embodiment_tag: str | None = None  # for GR00T-style models

    @property
    def action_dim(self) -> int:
        return self.arm_dof + self.gripper_dof

    @property
    def state_dim(self) -> int:
        return self.state_dim_override or self.action_dim


class VLACapabilities(BaseModel):
    """What a VLA adapter declares about its model checkpoint."""

    native_action_dim: int  # raw output dim (32 for pi0.5, 6 for SmolVLA-base)
    native_action_space: ActionSpace = ActionSpace.EEF_DELTA
    supported_robot_types: list[str] | None = None  # None = cross-embodiment
    max_action_dim: int | None = None  # for padded models (pi0.5 = 32)
    requires_embodiment_tag: bool = False  # GR00T needs this


class PipelineStage(StrEnum):
    PERCEIVE = "perceive"
    PLAN = "plan"
    ACT = "act"
    DYNAMICS = "dynamics"
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
    # Structured reasoning (Task → Subtask → Move → Action)
    subtask_reasoning: str = ""  # WHY this subtask is next (subtask decomposition)
    action_reasoning: str = ""  # spatial/motion reasoning ("object is behind robot, move backward")
    constraints_acknowledged: list[str] = Field(
        default_factory=list
    )  # echoed user constraints ("don't go near human", "glass only on left")
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
    # Provenance fields — set by adapters to record what coordinate frame actions are in
    declared_action_dim: int | None = None  # len(actions[0]) after slicing
    action_space: ActionSpace | None = None  # coordinate frame
    gripper_index: int | None = None  # which dim is gripper
    raw_action_dim: int | None = None  # native dim before slicing (32 for pi0.5)


class SimObservation(BaseModel):
    image_base64: str = ""  # post-step observation image
    proprioception: list[float] = Field(default_factory=list)  # joint states
    success: bool = False
    done: bool = False


class StageCheck(BaseModel, extra="allow"):
    """Per-stage check from the verifier. Extra fields from LLM are preserved."""

    stage: str = ""  # "perceive" | "plan" | "act" | "dynamics" etc.
    passed: bool = True
    confidence: float = 0.0
    reasoning: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_stage_field(cls, data: Any) -> Any:
        """Accept 'source' as alias for 'stage' — LLMs use both."""
        if isinstance(data, dict) and "stage" not in data and "source" in data:
            data["stage"] = data.pop("source")
        return data


class GroundTruthCheck(BaseModel):
    question: str
    choices: list[str]
    correct_answer: str
    pipeline_answer: str  # VLM judge's answer from pipeline outputs
    correct: bool
    confidence: float


class ActionPlausibility(BaseModel, extra="allow"):
    """Structured plausibility checks derivable from VLA output without simulation.

    All fields accept floats (0-1 scores) or None (not assessed).
    Bool values are coerced to 0.0/1.0.
    """

    bounds_check: float | None = None  # 0-1, action deltas within physically plausible ranges
    smoothness: float | None = None  # 0-1, no sudden jumps between consecutive steps
    gripper_consistency: float | None = None  # 0-1, gripper open/close pattern matches task type
    plan_alignment: float | None = (
        None  # 0-1, how rigorously the VLA trajectory follows the plan steps
    )
    reasoning: str = ""  # explanation of plausibility assessment
    # Extended fields for dynamics-aware verification
    workspace_reachability: float | None = (
        None  # 0-1, does trajectory stay within reachable workspace?
    )
    task_completion_plausibility: float | None = (
        None  # 0-1, does endpoint displacement match task intent?
    )
    dynamics_consistency: float | None = None  # 0-1, does LLM assessment agree with physics data?
    safety_assessment: float | None = (
        None  # 0-1, composite: torque + collision + singularity safety
    )
    evidence_quality: str = ""  # "hard", "estimated", "perception_only"


class VerificationResult(BaseModel):
    evaluator_result: EvaluatorResult | None = None
    evaluator_version: str = ""
    check_results: list[CheckResult] = Field(default_factory=list)
    aggregation_version: str = ""
    success: bool
    verdict_valid: bool = True  # False for parser fallbacks; not an observed task failure.
    confidence: float  # 0.0 - 1.0
    reasoning: str
    raw_response: str = ""
    completed_stages: list[str] = Field(default_factory=list)
    stage_checks: list[StageCheck] = Field(default_factory=list)
    ground_truth: GroundTruthCheck | None = None
    action_plausibility: ActionPlausibility | None = None
    dynamics_analysis: dict | None = None
    resolution_path: str = ""  # actionable suggestions for VLA improvement
    verify_turns: int = 1  # how many turns the verify loop took
    contradictions: list[str] = Field(default_factory=list)  # physics vs LLM disagreements


class PipelineStageResult(BaseModel):
    stage: str  # perceive | plan | act | dynamics | verify
    status: StageStatus
    latency_ms: float = 0.0
    output: dict | None = None  # serialized stage output
    error: str | None = None
    model_id: str = ""
    phase: str = ""  # "execution" | "evaluation" (empty for sequential mode)


class StageAssignment(BaseModel):
    perceive: str | None  # model_id for perceive stage (None if skipped)
    plan: str | None  # model_id for plan stage (None if skipped)
    act: str | None  # model_id for act stage (None if skipped)
    verify: str  # model_id for verify stage
    sim: str  # sim environment id


class PipelineContext(BaseModel):
    """Accumulates stage outputs as the pipeline progresses."""

    sim_steps: int = 0
    episode_evidence: dict = Field(default_factory=dict)
    grader_context: dict = Field(default_factory=dict)
    check_results: dict[str, CheckResult] = Field(default_factory=dict)
    task: str = ""
    image_base64: str = ""
    scene: SceneAnalysis | None = None
    plan: TaskPlan | None = None
    action: ActionPrediction | None = None
    proprioception: list[float] = Field(default_factory=list)
    after_image_base64: str = ""
    completed_stages: list[str] = Field(default_factory=list)
    ground_truth: dict | None = None
    dynamics_analysis: dict | None = None
    # Task metadata from manifest (constraints, category, correction, expected_subtasks)
    # Flows from ExampleData.extras into prompts so real adapters can honor them
    task_metadata: dict = Field(default_factory=dict)
    # Typed robot descriptor built from URDF + manifest metadata
    robot_embodiment: RobotEmbodiment | None = None

    def to_dict(self) -> dict:
        """Serialize non-empty fields for adapter context bags.

        Excludes image blobs and raw_response to keep context compact.
        """
        d: dict = {"task": self.task}
        if self.task_metadata:
            d["task_metadata"] = self.task_metadata
        if self.scene:
            d["scene"] = self.scene.model_dump(exclude={"raw_response"})
        if self.plan:
            d["plan"] = self.plan.model_dump(
                include={
                    "strategy",
                    "target_object",
                    "steps",
                    "confidence",
                    "subtask_reasoning",
                    "action_reasoning",
                    "constraints_acknowledged",
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
        if self.dynamics_analysis:
            d["dynamics_analysis"] = self.dynamics_analysis
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
    sim: str | None = None  # sim_id (None when no sim available)
    compute_dynamics: bool = False
    pipeline_mode: str = "sequential"  # "sequential" | "parallel"
    verify_mode: str = "auto"  # "auto" | "agent_loop" | "precompute"
    tags: list[str] = Field(default_factory=list)
    stage_timeouts: dict[str, int] = Field(default_factory=dict)
    verification_checks: list[CheckConfig] = Field(default_factory=list)


class TrialResult(BaseModel):
    eval_id: str
    task: str
    models: StageAssignment
    stages: list[PipelineStageResult] = Field(default_factory=list)
    success: bool = False
    total_latency_ms: float = 0.0
    total_cost_usd: float = 0.0
