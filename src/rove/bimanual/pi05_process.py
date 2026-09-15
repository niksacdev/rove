"""Persistent pi0.5 inference child; executed only by the ABC episode process."""

import contextlib
import json
import sys
from pathlib import Path


def serve(request):
    import numpy as np
    import torch
    from pi05_weights import load_policy

    torch.manual_seed(request["seed"])
    np.random.seed(request["seed"])
    policy, preprocess, postprocess = load_policy(Path(request["checkpoint"]), "cuda")
    policy.reset()
    for processor in (preprocess, postprocess):
        if hasattr(processor, "reset"):
            processor.reset()
    directory = Path(request["directory"])
    print('{"ready":true}', file=sys.__stdout__, flush=True)
    for line in sys.stdin:
        message = json.loads(line)
        if message.get("stop"):
            break
        with np.load(directory / "observation.npz", allow_pickle=False) as arrays:
            batch = {
                "observation.state": torch.from_numpy(arrays["state"].copy()),
                "task": message["prompt"],
            }
            for camera in ("top", "left", "right"):
                batch[f"observation.images.{camera}"] = (
                    torch.from_numpy(arrays[camera].copy()).float() / 255.0
                )
        with torch.inference_mode():
            chunk = policy.predict_action_chunk(preprocess(batch))
            action = postprocess(chunk)[0].detach().cpu().float().numpy()
        np.save(directory / "actions.npy", action, allow_pickle=False)
        print('{"done":true}', file=sys.__stdout__, flush=True)


if __name__ == "__main__":
    try:
        with contextlib.redirect_stdout(sys.stderr):
            serve(json.loads(Path(sys.argv[1]).read_text()))
    except Exception as error:
        # Model exceptions can contain private tokens/paths. Parent exposes only type.
        print(json.dumps({"error": type(error).__name__}), file=sys.__stdout__, flush=True)
        raise SystemExit(1) from None
