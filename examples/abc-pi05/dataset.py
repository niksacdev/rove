"""Convert pinned ABC prepared training episodes into a local LeRobot dataset.

Usage: python dataset.py --cache /path/to/prepared-cache --output /path/to/new-dataset
    --repo-id local/abc-bottles --split train_sim
    --task-name sim_put_the_plastic_bottles_in_the_bin

No downloads or uploads. Public validation episodes are never used for training.
Only the prepared 30 Hz, three-camera, 14D joint-position format is accepted.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import subprocess  # nosec B404 - local, argument-list ffmpeg/ffprobe calls
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from urllib.parse import unquote, urlparse

ABC_REVISION = "d0e987b61b376f1a1b8777b19149f190b2ba6939"  # pragma: allowlist secret
LEROBOT_REVISION = "89236ea0f4f81a81ca566081e20dd1ff5f823cbe"  # pragma: allowlist secret
CAMERAS = ("top", "left", "right")
JOINTS = [f"{side}_j{i}" for side in ("left", "right") for i in range(1, 7)]
JOINTS = [*JOINTS[:6], "left_gripper", *JOINTS[6:], "right_gripper"]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def task_prompt(value):
    """Match the pinned ABC task_name_to_prompt whitespace/underscore convention."""
    return " ".join(value.replace("-", " ").replace("_", " ").split())


def executable(name):
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"Install {name} for prepared-video conversion")
    return str(Path(path).resolve())


def verify_lerobot_revision():
    """Check the actual installed source, including a clean local validation clone."""
    distribution = importlib.metadata.distribution("lerobot")
    origin = json.loads(distribution.read_text("direct_url.json") or "{}")
    revision = origin.get("vcs_info", {}).get("commit_id")
    url = urlparse(origin.get("url", ""))
    if revision is None and url.scheme == "file":
        path = Path(unquote(url.path))
        if (path / ".git").exists():
            result = subprocess.run(  # nosec B603 - inspect the installed local source, no shell
                [executable("git"), "-C", str(path), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            )
            dirty = subprocess.run(  # nosec B603 - read-only source provenance check
                [executable("git"), "-C", str(path), "status", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
            )
            if not dirty.stdout.strip():
                revision = result.stdout.strip()
    if revision != LEROBOT_REVISION:
        raise ValueError("Install the pinned examples/abc-pi05/requirements.txt runtime")
    return {"version": distribution.version, "source_revision": revision}


@dataclass(frozen=True)
class Episode:
    path: Path
    split: str
    task: str
    prompt: str
    cameras: tuple[str, ...]
    height: int
    length: int
    files: dict[str, str]


def read_episode(path: Path, split: str) -> Episode:
    import numpy as np

    metadata = json.loads((path / "episode_metadata.json").read_text())
    task = metadata.get("task_name")
    if not isinstance(task, str) or not task.strip():
        raise ValueError(f"{path.name}: missing task_name")
    cameras = tuple(metadata.get("cameras", []))
    if len(cameras) != 3 or set(cameras) != set(CAMERAS):
        raise ValueError(f"{path.name}: requires explicit top, left, right camera order")
    resolutions = metadata.get("camera_resolutions", {})
    shapes = [resolutions.get(camera) for camera in cameras]
    if any(shape != shapes[0] for shape in shapes) or shapes[0] not in ([224, 224], [224, 168]):
        raise ValueError(f"{path.name}: only prepared 224x224 or 224x168 cameras are supported")
    fps = metadata.get("fps")
    if fps is None:
        if metadata.get("alignment") != "fixed_clock_30hz_causal":
            raise ValueError(f"{path.name}: missing 30 Hz alignment metadata")
        tick = metadata.get("tick_ns")
        if tick is None or abs(float(tick) - 1e9 / 30) > 1:
            raise ValueError(f"{path.name}: invalid 30 Hz timestamp grid")
    elif abs(float(fps) - 30) > 1e-6:
        raise ValueError(f"{path.name}: only 30 Hz prepared episodes are supported")
    binary = path / "states_actions.bin"
    if not binary.is_file() or not binary.stat().st_size or binary.stat().st_size % (28 * 8):
        raise ValueError(f"{path.name}: expected float64 rows with state14 + action14")
    rows = np.memmap(binary, dtype="<f8", mode="r").reshape(-1, 28)
    if metadata.get("num_steps") != len(rows) or not np.isfinite(rows).all():
        raise ValueError(f"{path.name}: invalid row count or non-finite state/action")
    if ((rows[:, [6, 13, 20, 27]] < 0) | (rows[:, [6, 13, 20, 27]] > 1)).any():
        raise ValueError(f"{path.name}: gripper state/targets must already be in [0,1]")
    prompt = metadata.get("instruction") or task_prompt(task)
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError(f"{path.name}: missing task instruction")
    # ABC's task_name_to_prompt replaces underscores. Keep explicit directives.
    if task_prompt(prompt) == task_prompt(task):
        prompt = task_prompt(task)
    randomization = path / "randomization.json"
    if randomization.exists():
        random_prompt = (json.loads(randomization.read_text()).get("metadata") or {}).get("prompt")
        if random_prompt and task_prompt(random_prompt) != task_prompt(prompt):
            raise ValueError(
                f"{path.name}: randomization directive differs from training instruction"
            )
    filenames = ["episode_metadata.json", "states_actions.bin", "combined_camera-images-rgb.mp4"]
    if randomization.exists():
        filenames.append("randomization.json")
    return Episode(
        path,
        split,
        task,
        prompt,
        cameras,
        shapes[0][1],
        len(rows),
        {name: sha256(path / name) for name in filenames},
    )


def plan(cache: Path, splits: list[str], tasks: list[str]):
    """Freeze selected training episode identities and reject known holdout overlap."""
    if not splits or set(splits) - {"train_real", "train_sim"} or len(set(splits)) != len(splits):
        raise ValueError(
            "Select each of train_real/train_sim at most once; validation splits are forbidden"
        )
    if not tasks or any(not task.strip() for task in tasks):
        raise ValueError("Select explicit source task names; do not silently mix all tasks")
    heldout_ids, heldout_hashes = set(), set()
    heldout = []
    for split in ("val_real", "val_sim"):
        for binary in sorted((cache / split).glob("*/states_actions.bin")):
            digest = sha256(binary)
            heldout_ids.add(binary.parent.name)
            heldout_hashes.add(digest)
            heldout.append(
                {"split": split, "episode_id": binary.parent.name, "states_actions_sha256": digest}
            )
    episodes, seen_ids, seen_hashes = [], set(), set()
    for split in splits:
        if not (cache / split).is_dir():
            raise ValueError(f"Missing selected source split: {split}")
        for metadata in sorted((cache / split).glob("*/episode_metadata.json")):
            if json.loads(metadata.read_text()).get("task_name") not in tasks:
                continue
            ep = read_episode(metadata.parent, split)
            digest = ep.files["states_actions.bin"]
            if ep.path.name in heldout_ids or digest in heldout_hashes:
                raise ValueError(f"{ep.path.name}: training episode overlaps public validation")
            if ep.path.name in seen_ids or digest in seen_hashes:
                raise ValueError(f"{ep.path.name}: duplicate training episode")
            seen_ids.add(ep.path.name)
            seen_hashes.add(digest)
            episodes.append(ep)
    missing = set(tasks) - {ep.task for ep in episodes}
    if missing:
        raise ValueError(f"No selected training episodes for tasks: {sorted(missing)}")
    return episodes, heldout


def probe_video(ep: Episode):
    output = subprocess.run(  # nosec B603 - trusted local executable, no shell
        [
            executable("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,avg_frame_rate",
            "-of",
            "json",
            str(ep.path / "combined_camera-images-rgb.mp4"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    streams = json.loads(output.stdout).get("streams", [])
    if (
        len(streams) != 1
        or streams[0].get("width") != 224
        or streams[0].get("height") != ep.height * 3
    ):
        raise ValueError(f"{ep.path.name}: combined video does not match camera metadata")
    if abs(float(Fraction(streams[0]["avg_frame_rate"])) - 30) > 1e-3:
        raise ValueError(f"{ep.path.name}: combined video is not 30 Hz")


def split_frame(frame, ep):
    """Preserve camera order; center-pad 168-high sim images like pi0.5 does."""
    import numpy as np

    if frame.dtype != np.uint8 or frame.shape != (ep.height * 3, 224, 3):
        raise ValueError("Invalid decoded stacked RGB frame")
    pad = (224 - ep.height) // 2
    result = {}
    for index, camera in enumerate(ep.cameras):
        image = frame[index * ep.height : (index + 1) * ep.height]
        result[f"observation.images.{camera}"] = np.pad(image, ((pad, pad), (0, 0), (0, 0)))
    return result


def frames(ep: Episode):
    import numpy as np

    probe_video(ep)
    frame_bytes = ep.height * 3 * 224 * 3
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(  # nosec B603 - local video decoding, no shell
            [
                executable("ffmpeg"),
                "-v",
                "error",
                "-i",
                str(ep.path / "combined_camera-images-rgb.mp4"),
                "-vsync",
                "0",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=errors,
        )
        try:
            for index in range(ep.length):
                raw = process.stdout.read(frame_bytes)
                if len(raw) != frame_bytes:
                    raise ValueError(f"{ep.path.name}: video ended before state row {index}")
                yield np.frombuffer(raw, dtype=np.uint8).reshape(ep.height * 3, 224, 3)
            if process.stdout.read(1):
                raise ValueError(f"{ep.path.name}: video has more frames than state rows")
            if process.wait() != 0:
                errors.seek(0)
                raise ValueError(
                    f"Video decode failed: {errors.read().decode(errors='replace')[-2000:]}"
                )
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
                process.wait()


def features():
    return {
        "observation.state": {"dtype": "float32", "shape": (14,), "names": JOINTS},
        "action": {"dtype": "float32", "shape": (14,), "names": JOINTS},
        **{
            f"observation.images.{camera}": {
                "dtype": "video",
                "shape": (224, 224, 3),
                "names": ["height", "width", "channels"],
            }
            for camera in CAMERAS
        },
    }


def convert(cache, output, repo_id, splits, tasks, *, dataset_class=None):
    import numpy as np

    output = Path(output)
    if output.exists():
        raise ValueError("Output already exists; choose a new versioned dataset directory")
    episodes, heldout = plan(Path(cache), splits, tasks)
    if dataset_class is None:
        runtime = verify_lerobot_revision()
        from lerobot.datasets.lerobot_dataset import LeRobotDataset

        dataset_class = LeRobotDataset
    else:
        runtime = {"implementation": "injected_test_writer"}
    writer = dataset_class.create(
        repo_id=repo_id,
        root=output,
        fps=30,
        features=features(),
        robot_type="abc_i2rt_yam_bimanual",
        use_videos=True,
    )
    manifest = {
        "schema": "rove.abc-pi05.dataset/v1",
        "status": "running",
        "repo_id": repo_id,
        "abc_revision": ABC_REVISION,
        "lerobot_revision": LEROBOT_REVISION,
        "lerobot_runtime": runtime,
        "fps": 30,
        "action_semantics": "absolute_joint_positions_radians_and_grippers_0_1",
        "joint_names": JOINTS,
        "camera_keys": list(CAMERAS),
        "image_padding": "center_black_to_224_square",
        "episodes": [],
        "excluded_public_validation": heldout,
        "validation_note": "Only locally available public validation files were checked. Pretraining exposure is not established by this audit.",
    }
    manifest_path = output / "rove_dataset_manifest.json"
    write_json(manifest_path, manifest)
    try:
        for index, ep in enumerate(episodes):
            rows = np.memmap(ep.path / "states_actions.bin", dtype="<f8", mode="r").reshape(-1, 28)
            for row, frame in zip(rows, frames(ep), strict=True):
                writer.add_frame(
                    {
                        "observation.state": row[:14].astype(np.float32),
                        "action": row[14:].astype(np.float32),
                        "task": ep.prompt,
                        **split_frame(frame, ep),
                    }
                )
            writer.save_episode(parallel_encoding=False)
            manifest["episodes"].append(
                {
                    "episode_index": index,
                    "source_episode_id": ep.path.name,
                    "source_split": ep.split,
                    "source_task": ep.task,
                    "prompt": ep.prompt,
                    "frames": ep.length,
                    "source_files": ep.files,
                }
            )
            write_json(manifest_path, manifest)
        writer.finalize()
        manifest["output_files"] = {
            str(path.relative_to(output)): sha256(path)
            for path in sorted(output.rglob("*"))
            if path.is_file() and path != manifest_path
        }
        manifest["status"] = "completed"
    except BaseException:
        manifest["status"] = "failed"
        raise
    finally:
        writer.finalize()
        write_json(manifest_path, manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-id", default="local/abc-bottles")
    parser.add_argument(
        "--split", choices=("train_real", "train_sim"), action="append", required=True
    )
    parser.add_argument("--task-name", action="append", required=True)
    args = parser.parse_args()
    result = convert(args.cache, args.output, args.repo_id, args.split, args.task_name)
    print(
        json.dumps(
            {
                "status": result["status"],
                "episodes": len(result["episodes"]),
                "output": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()
