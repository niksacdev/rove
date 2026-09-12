"""Action-dependent 1D reaching fixture, not a robot physics simulation."""

import math
import uuid

from rove.datasets.robotics import EpisodeRecording, SyntheticEnvironment
from rove.models import SimObservation


class SyntheticSimAdapter:
    def __init__(self, model_id="synthetic-world", config=None):
        self.model_id = model_id
        self.display_name = "Synthetic 1D reaching"
        self.version = "synthetic-reaching-v1"
        self.configure_episode((config or {}).get("environment", {}), "standalone")

    def configure_episode(self, environment, case_id):
        self.environment = SyntheticEnvironment.model_validate(environment)
        self.case_id = case_id
        self._ready = False

    async def reset(self, task):
        self.position = self.environment.initial_position_m
        self.steps = self.violations = self.opportunities = 0
        self.done = self.success = False
        self.reset_id = uuid.uuid4().hex
        self.records = [{"timestamp_s": 0.0, "position_m": self.position, "event": "reset"}]
        self._ready = True
        return await self.get_observation()

    async def step(self, action):
        if not self._ready or self.done:
            raise ValueError("Synthetic world requires reset and an unfinished episode")
        if len(action) != 1 or type(action[0]) not in (float, int) or not math.isfinite(action[0]):
            raise ValueError("Synthetic action requires exactly one finite delta in metres")
        delta = action[0]
        if abs(delta) > self.environment.max_delta_m:
            raise ValueError("Synthetic action exceeds the declared delta limit")
        self.steps += 1
        before = self.position
        self.position += delta
        if self.steps == self.environment.disturbance_step:
            self.position += self.environment.disturbance_delta_m
            self.opportunities += 1
        forbidden = self.environment.forbidden_interval_m
        if forbidden and max(min(before, self.position), forbidden[0]) <= min(
            max(before, self.position), forbidden[1]
        ):
            self.violations += 1
        self.success = (
            abs(self.position - self.environment.target_position_m) <= self.environment.tolerance_m
            and not self.violations
        )
        self.done = (
            self.success or bool(self.violations) or self.steps >= self.environment.max_steps
        )
        self.records.append(
            {
                "timestamp_s": self.steps * self.environment.step_duration_s,
                "position_m": self.position,
                "action_delta_m": delta,
            }
        )
        return await self.get_observation()

    async def get_observation(self):
        if not self._ready:
            raise ValueError("Synthetic world was not reset")
        return SimObservation(proprioception=[self.position], success=self.success, done=self.done)

    def episode_evidence(self):
        if not self._ready or not self.steps:
            raise ValueError("Synthetic outcome requires executed candidate actions")
        return EpisodeRecording(
            origin="synthetic_rollout",
            quality="synthetic",
            case_id=self.case_id,
            reset_id=self.reset_id,
            producing_system=self.version,
            source_clock_id=f"synthetic-step-clock:{self.reset_id}",
            frame="world_1d",
            units={"time": "s", "position": "m"},
            task_completed=self.success,
            constraint_violations=self.violations,
            duration_seconds=self.steps * self.environment.step_duration_s,
            completion_time_s=self.steps * self.environment.step_duration_s
            if self.success
            else None,
            intervention_count=0,
            recovery_opportunities=self.opportunities,
            recoveries=self.opportunities if self.success else 0,
            records=self.records,
        ).model_dump(mode="json")
