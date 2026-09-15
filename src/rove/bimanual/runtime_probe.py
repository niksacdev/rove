"""Small dependency fingerprint, executable in either isolated interpreter."""

import contextlib
import hashlib
import importlib.metadata
import json
import os
import platform
import sys


def health(kind, source):
    if platform.system() != "Linux":
        return {
            "issues": [
                "Learned-policy evaluation requires Linux with an NVIDIA GPU; local data inspection remains available"
            ]
        }
    issues = []
    try:
        import torch

        if not torch.cuda.is_available():
            issues.append(
                "CUDA is unavailable in this interpreter; install its CUDA runtime and provide an NVIDIA GPU"
            )
        if kind == "abc_vla":
            sys.path.insert(0, source)
            os.environ.pop("DEPLOY_INIT_Q", None)
            import abc_sim
            from abc_minimal.policy import VLAInferencePolicy  # noqa: F401

            env = abc_sim.make_env(task="put_plastic_bottles_in_bin", render_cameras=False)
            env.close()
        else:
            from lerobot.policies.pi05.modeling_pi05 import PI05Policy  # noqa: F401
    except Exception as error:
        issues.append(
            f"{kind} dependencies or simulator assets could not load ({type(error).__name__}); follow the isolated runtime setup"
        )
    return {"issues": issues}


if __name__ == "__main__":
    if sys.argv[1:2] == ["health"]:
        with contextlib.redirect_stdout(sys.stderr):
            status = health(sys.argv[2], sys.argv[3])
        print(json.dumps(status))
        raise SystemExit(0)
    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "packages": sorted(
                    (
                        d.metadata["Name"],
                        d.version,
                        d.read_text("direct_url.json"),
                        hashlib.sha256((d.read_text("RECORD") or "").encode()).hexdigest(),
                    )
                    for d in importlib.metadata.distributions()
                ),
            }
        )
    )
