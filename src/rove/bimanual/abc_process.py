"""Standalone ABC simulation runtime. Children inherit the ROVE worker process group."""

import contextlib
import json
import os
import subprocess  # nosec B404 - trusted local model process; arguments never use a shell
import sys
from pathlib import Path

from episode import canonical, run_episode


def emit(value):
    print(canonical(value), file=sys.__stdout__, flush=True)


def run(request, directory):
    import numpy as np
    import torch

    environment, strategy = request["environment"], request["strategy"]
    sys.path.insert(0, environment["source"])
    # External deployment state must not override fixed simulator initial joints.
    os.environ.pop("DEPLOY_INIT_Q", None)
    import abc_sim

    torch.manual_seed(request["seed"])
    np.random.seed(request["seed"])
    env = abc_sim.make_env(
        task=environment["task"],
        prompt=environment["prompt"],
        render_cameras=True,
        camera_backend="mujoco",
        camera_height=168,
        camera_width=224,
        chunk_dim=environment["execute_steps"],
        max_episode_steps=environment["max_steps"],
        terminate_on_success=False,
        physics_dt=0.002,
        control_decimation=17,
    )
    child = None
    try:
        emit({"type": "progress", "stage": "model", "phase": "started", "data": {}})
        if strategy["kind"] == "abc_vla":
            from abc_minimal.policy import VLAInferencePolicy, VLAPolicyConfig

            policy = VLAInferencePolicy(
                Path(strategy["checkpoint"]),
                VLAPolicyConfig(prompt=environment["prompt"], camera_height=168, camera_width=224),
                "cuda",
            )
            if policy.action_dim != 14 or policy.camera_keys != ("top", "left", "right"):
                raise ValueError("ABC checkpoint does not match the bimanual observation contract")
            predict = policy.infer
        else:
            child_request = directory / "pi05-request.json"
            child_request.write_text(
                json.dumps(
                    {
                        "checkpoint": strategy["checkpoint"],
                        "seed": request["seed"],
                        "directory": str(directory),
                    }
                )
            )
            child = subprocess.Popen(  # nosec B603 -- registered local runtime, no shell
                [
                    strategy["python"],
                    str(Path(__file__).with_name("pi05_process.py")),
                    str(child_request),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            if json.loads(child.stdout.readline()).get("ready") is not True:
                raise ValueError("pi0.5 checkpoint could not load")

            def predict(obs):
                np.savez(directory / "observation.npz", state=obs["state"], **obs["images"])
                child.stdin.write(json.dumps({"prompt": obs["prompt"]}) + "\n")
                child.stdin.flush()
                if json.loads(child.stdout.readline()).get("done") is not True:
                    raise ValueError("pi0.5 inference failed")
                return np.load(directory / "actions.npy", allow_pickle=False)

        emit({"type": "progress", "stage": "model", "phase": "completed", "data": {}})

        def frame(obs, step):
            from PIL import Image

            path = directory / f"frame-{step}.png"
            pixels = np.concatenate(
                [obs["images"][c].transpose(1, 2, 0) for c in ("top", "left", "right")], axis=1
            )
            Image.fromarray(pixels).save(path)
            emit(
                {
                    "type": "artifact",
                    "id": f"episode.frame.{step}",
                    "file": path.name,
                    "media_type": "image/png",
                }
            )

        summary = run_episode(
            env,
            predict,
            reset_seed=request["case"]["inputs"]["reset_seed"],
            policy_seed=request["seed"],
            prompt=environment["prompt"],
            max_steps=environment["max_steps"],
            execute_steps=environment["execute_steps"],
            directory=directory,
            emit=emit,
            capture_frame=frame,
        )
        emit({"type": "result", "summary": summary})
    finally:
        env.close()
        if child:
            child.kill()
            child.wait()


if __name__ == "__main__":
    try:
        request_path = Path(sys.argv[1])
        with contextlib.redirect_stdout(sys.stderr):
            run(json.loads(request_path.read_text()), request_path.parent)
    except Exception as error:
        emit({"type": "error", "error_type": type(error).__name__})
        raise SystemExit(1) from None
