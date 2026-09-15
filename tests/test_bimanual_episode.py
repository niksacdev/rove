"""Closed-loop, evidence, action and reset regressions without model downloads."""

import json
from dataclasses import dataclass

import numpy as np
import pytest

from rove.bimanual.episode import run_episode, valid_actions


@dataclass
class Randomization:
    seed: int


class Outcome:
    def __init__(self, success=False):
        self.success = success

    def scalar_success(self):
        return self.success


class Environment:
    def __init__(self, success_steps=(), actual_seed=None, scored=True, already_successful=False):
        self.success_steps = success_steps
        self.actual_seed = actual_seed
        self.scored = scored
        self.already_successful = already_successful
        self.steps = 0
        self.actions = []

    def reset(self, *, seed, randomize):
        assert randomize
        self.steps = 0
        return self.get_obs(), {
            "randomization": Randomization(seed if self.actual_seed is None else self.actual_seed)
        }

    def get_obs(self):
        return {"state": self.get_state(), "images": {}, "prompt": "upstream prompt"}

    def get_state(self):
        return np.ones(14, dtype=np.float32) * self.steps

    def capture_state(self):
        return {"qpos": self.get_state(), "qvel": np.zeros(14), "time": self.steps * 0.034}

    def evaluate_task(self):
        return Outcome(self.already_successful) if self.scored else None

    def step(self, action, *, render_obs):
        assert not render_obs
        self.actions.append(action.copy())
        self.steps += 1
        return None, 0.0, False, False, {"task_success": self.steps in self.success_steps}


def run(tmp_path, env, predict=None, emit=None, **kwargs):
    return run_episode(
        env,
        predict or (lambda _: np.zeros((30, 14))),
        reset_seed=7,
        policy_seed=11,
        prompt="put bottles in bin",
        max_steps=5,
        execute_steps=2,
        directory=tmp_path,
        emit=emit or (lambda _: None),
        **kwargs,
    )


def test_observe_replan_and_truncated_prefix_preserve_native_targets(tmp_path):
    env = Environment(success_steps={2})
    observations, events = [], []

    def predict(obs):
        observations.append((obs["state"].copy(), obs["prompt"]))
        actions = np.full((30, 14), 2.3)
        actions[:, [6, 13]] = 1.02
        return actions

    result = run(tmp_path, env, predict, events.append)
    assert [state[0] for state, _ in observations] == [0, 2, 4]
    assert all(prompt == "put bottles in bin" for _, prompt in observations)
    assert len(env.actions) == 5
    assert all(np.allclose(action[[0, 7]], 2.3) for action in env.actions)
    assert result["gripper_out_of_range_steps"] == 5
    assert result["task_success"] is True and result["final_success"] is False
    assert result["simulated_time_s"] == pytest.approx(0.17)
    assert result["policy_seed"] == 11 and result["reset_seed"] == 7
    rows = [json.loads(row) for row in (tmp_path / "trajectory.jsonl").read_text().splitlines()]
    assert [r["state_before"][0] for r in rows] == [0, 1, 2, 3, 4]
    artifacts = [e["id"] for e in events if e["type"] == "artifact"]
    assert "episode.initial_state" in artifacts
    assert all(f"episode.trajectory.{n}" in artifacts for n in (1, 2, 3))


@pytest.mark.parametrize(
    "env",
    [Environment(actual_seed=12), Environment(scored=False), Environment(already_successful=True)],
)
def test_invalid_case_never_calls_policy(tmp_path, env):
    def forbidden(_):
        pytest.fail("Invalid initial conditions must not invoke a model")

    with pytest.raises(ValueError):
        run(tmp_path, env, forbidden)


@pytest.mark.parametrize(
    "actions", [np.zeros((14,)), np.zeros((30, 7)), np.zeros((1, 14)), np.full((30, 14), np.nan)]
)
def test_reject_malformed_chunks(actions):
    with pytest.raises(ValueError):
        valid_actions(actions, 2)


def test_partial_episode_has_evidence_but_no_outcome(tmp_path):
    events, calls = [], []

    def predict(_):
        calls.append(True)
        if len(calls) > 1:
            raise RuntimeError("provider stopped")
        return np.zeros((30, 14))

    with pytest.raises(RuntimeError):
        run(tmp_path, Environment(), predict, events.append)
    assert any(e.get("id") == "episode.trajectory.1" for e in events)
    assert not any(e.get("type") == "result" for e in events)


def test_same_scene_is_independent_of_policy_seed(tmp_path):
    first = run(tmp_path, Environment())["initial_state_digest"]
    assert run(tmp_path, Environment())["initial_state_digest"] == first
