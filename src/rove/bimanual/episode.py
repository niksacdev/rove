"""Closed-loop episode contract. Also importable in ABC's isolated Python runtime."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path

import numpy as np


def plain(value):
    if is_dataclass(value):
        return plain(asdict(value))
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, tuple | list):
        return [plain(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


def canonical(value):
    return json.dumps(plain(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def valid_actions(actions, execute_steps):
    values = np.asarray(actions, dtype=np.float32)
    if values.ndim != 2 or values.shape[1] != 14 or not execute_steps <= len(values) <= 1000:
        raise ValueError("Policy must return at least the execution horizon of 14D actions")
    if not np.isfinite(values).all():
        raise ValueError("Policy returned non-finite actions")
    # Arm targets are absolute radians, not the placeholder [-1,1] Gym box.
    return values


def run_episode(
    env,
    predict,
    *,
    reset_seed,
    policy_seed,
    prompt,
    max_steps,
    execute_steps,
    directory: Path,
    emit,
    capture_frame=None,
):
    """Run fresh observations after each executed prefix; record every control step."""
    obs, reset_info = env.reset(seed=reset_seed, randomize=True)
    initial = {
        "state": plain(env.capture_state()),
        "reset": plain(reset_info),
        "requested_seed": reset_seed,
    }
    randomization = initial["reset"].get("randomization")
    if not randomization or randomization.get("seed") != reset_seed:
        raise ValueError("Simulator did not confirm the requested initial scene seed")
    evaluator = env.evaluate_task()
    if evaluator is None:
        raise ValueError("This simulator task has no outcome evaluator")
    if evaluator.scalar_success():
        raise ValueError("Case is already successful before the first model action")
    initial["digest"] = hashlib.sha256(canonical(initial).encode()).hexdigest()
    initial_path = directory / "initial.json"
    initial_path.write_text(canonical(initial))
    emit(
        {
            "type": "artifact",
            "id": "episode.initial_state",
            "file": initial_path.name,
            "media_type": "application/json",
        }
    )
    if capture_frame:
        capture_frame(obs, 0)
    steps, successes, latencies, gripper_out_of_range_steps = 0, [], [], 0
    final_info = {}
    start_sim_time = float(env.capture_state()["time"])
    trajectory = directory / "trajectory.jsonl"
    with trajectory.open("w") as log:
        while steps < max_steps:
            obs["prompt"] = prompt
            emit(
                {
                    "type": "progress",
                    "stage": "policy",
                    "phase": "started",
                    "span_id": f"policy-{len(latencies) + 1}",
                    "data": {"replan": len(latencies) + 1},
                }
            )
            begin = time.perf_counter()
            actions = valid_actions(predict(obs), execute_steps)
            elapsed = time.perf_counter() - begin
            latencies.append(elapsed)
            emit(
                {
                    "type": "progress",
                    "stage": "policy",
                    "phase": "completed",
                    "span_id": f"policy-{len(latencies)}",
                    "data": {
                        "replan": len(latencies),
                        "inference_s": elapsed,
                        "predicted_actions": len(actions),
                        "completed_steps": steps,
                        "max_steps": max_steps,
                    },
                }
            )
            segment = []
            for action in actions[: min(execute_steps, max_steps - steps)]:
                if (action[[6, 13]] < 0).any() or (action[[6, 13]] > 1).any():
                    gripper_out_of_range_steps += 1
                state_before = obs["state"]
                # Request images once per prefix, not 15 redundant render passes.
                _, reward, terminated, truncated, info = env.step(action, render_obs=False)
                steps += 1
                if type(info.get("task_success")) not in (bool, np.bool_):
                    raise ValueError("Simulator stopped supplying a measured task outcome")
                successes.append(bool(info["task_success"]))
                final_info = info
                row = (
                    canonical(
                        {
                            "step": steps,
                            "state_before": state_before,
                            "action": action,
                            "state_after": env.capture_state(),
                            "reward": reward,
                            "evaluation": info,
                        }
                    )
                    + "\n"
                )
                log.write(row)
                segment.append(row)
                # get_obs updates state and images at the replan boundary below.
                obs["state"] = env.get_state()
                if terminated or truncated:
                    break
            log.flush()
            # Commit each completed action prefix before the next model call. A
            # hard process-group timeout must not erase the preceding trajectory.
            part = directory / f"trajectory-{len(latencies)}.jsonl"
            part.write_text("".join(segment))
            emit(
                {
                    "type": "artifact",
                    "id": f"episode.trajectory.{len(latencies)}",
                    "file": part.name,
                    "media_type": "application/x-ndjson",
                }
            )
            obs = env.get_obs()
            emit(
                {
                    "type": "progress",
                    "stage": "episode",
                    "phase": "progress",
                    "data": {
                        "completed_steps": steps,
                        "max_steps": max_steps,
                        "task_success": successes[-1],
                    },
                }
            )
            if capture_frame and (len(latencies) % 10 == 0 or steps >= max_steps):
                capture_frame(obs, steps)
            if terminated or truncated:
                break
    if steps < max_steps:
        raise ValueError("Episode ended before its shared control budget")
    summary = {
        "task_success": any(successes),
        "final_success": successes[-1],
        "episode_steps": steps,
        "simulated_time_s": float(env.capture_state()["time"]) - start_sim_time,
        "replans": len(latencies),
        "inference_latency_mean_s": float(np.mean(latencies)),
        "gripper_out_of_range_steps": gripper_out_of_range_steps,
        "inference_latency_p95_s": float(np.percentile(latencies, 95)),
        "inference_wall_s": sum(latencies),
        "reset_seed": reset_seed,
        "policy_seed": policy_seed,
        "initial_state_digest": initial["digest"],
        "prompt": prompt,
        "final_evaluation": plain(final_info),
        "scope": "abc_simulation",
    }
    emit(
        {
            "type": "artifact",
            "id": "episode.trajectory",
            "file": trajectory.name,
            "media_type": "application/x-ndjson",
        }
    )
    return summary
