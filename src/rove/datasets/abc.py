"""Import bounded local ABC exports without exposing demonstrations to candidates.

The layout is published by amazon-far/abc's export_mcap.py. ROVE consumes its
output; it neither downloads gated data nor runs the upstream training stack.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import re
import shutil
import struct
import subprocess  # nosec B404 - local ffmpeg/ffprobe only; no shell or downloaded executables
import tempfile
from dataclasses import dataclass
from pathlib import Path

from rove.trials.snapshots import content_hash

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_METADATA_BYTES = 256 * 1024
MAX_STEPS = 108_000
TICK_NS = 33_333_333
FILES = {
    "episode_metadata.json": "application/json",
    "states_actions.bin": "application/octet-stream",
    "combined_camera-images-rgb.mp4": "video/mp4",
}
WARNINGS = [
    "Demonstrations are reference material, not evidence that a candidate robot completed the task.",
    "The upstream export may fill missing state channels with zeros; state completeness is unknown.",
    "Robot model, calibration and original camera timestamps are not retained by this export.",
    "The top camera can represent a selected stereo eye; its original topic is not retained.",
    "Use episode-level splits and check whether candidate models trained on this public dataset.",
]


@dataclass(frozen=True)
class PreparedEpisode:
    payload: dict
    image_bytes: bytes
    assets: dict[str, bytes]
    warnings: list[str]
    fingerprint: str

    def preview(self) -> dict:
        return {
            "payload": self.payload,
            "thumbnail_data_uri": "data:image/png;base64,"
            + base64.b64encode(self.image_bytes).decode("ascii"),
            "warnings": self.warnings,
            "fingerprint": self.fingerprint,
            "preview_hash": self.fingerprint,
        }


def _files(directory: Path) -> dict[str, bytes]:
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Choose a local episode directory, not a symbolic link")
    output = {}
    for name in FILES:
        path = directory / name
        limit = MAX_METADATA_BYTES if name.endswith(".json") else MAX_FILE_BYTES
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Episode requires a regular local {name}")
        if not 0 < path.stat().st_size <= limit:
            raise ValueError(f"{name} is empty or exceeds the supported size limit")
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        if not 0 < len(data) <= limit:
            raise ValueError(f"{name} changed or exceeds the supported size limit")
        output[name] = data
    return output


def _integer(value, name: str, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"{name} must be an integer between {lower} and {upper}")
    return value


def _run(program: str, arguments: list[str]) -> bytes:
    if program not in {"ffmpeg", "ffprobe"}:
        raise ValueError("Only local ABC video inspection tools are supported")
    executable = shutil.which(program)
    if not executable:
        raise ValueError("ABC imports require ffmpeg and ffprobe installed locally")
    try:
        # Fixed executable/argument list, forced MOV demuxer, local protocols only.
        result = subprocess.run(  # nosec B603 - allowlisted executable, fixed args, local MP4 only
            [executable, *arguments], capture_output=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("ABC video inspection exceeded its limits or could not run") from error
    if result.returncode:
        raise ValueError("ABC video could not be decoded as a local MP4")
    return result.stdout


def _image(
    video: bytes,
    *,
    frame: int,
    steps: int,
    cameras: list[str],
    camera: str,
    camera_height: int = 224,
) -> bytes:
    try:
        from PIL import Image
    except ImportError as error:
        raise ValueError("ABC imports require the rove[data] optional dependency") from error
    with tempfile.TemporaryDirectory(prefix="rove-abc-") as temporary:
        path = Path(temporary) / "episode.mp4"
        path.write_bytes(video)
        common = ["-v", "error", "-protocol_whitelist", "file,pipe", "-f", "mov"]
        try:
            probe = json.loads(
                _run(
                    "ffprobe",
                    [
                        *common,
                        "-select_streams",
                        "v:0",
                        "-show_entries",
                        "stream=width,height,r_frame_rate",
                        "-of",
                        "json",
                        str(path),
                    ],
                )
            )
            stream = probe["streams"][0]
            width, height = stream["width"], stream["height"]
            valid = (
                width == 224
                and height == camera_height * len(cameras)
                and stream["r_frame_rate"] == "30/1"
            )
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise ValueError("ABC video has missing or invalid frame metadata") from error
        if not valid:
            raise ValueError(
                "ABC video dimensions, frame count or 30 Hz clock do not match metadata"
            )
        # Inspect dimensions before decoding, preventing oversized frames from
        # reaching either the frame counter or RGB extraction.
        try:
            counted = json.loads(
                _run(
                    "ffprobe",
                    [
                        *common,
                        "-threads",
                        "1",
                        "-select_streams",
                        "v:0",
                        "-count_frames",
                        "-show_entries",
                        "stream=nb_read_frames",
                        "-of",
                        "json",
                        str(path),
                    ],
                )
            )
            if int(counted["streams"][0]["nb_read_frames"]) != steps:
                raise ValueError("Frame count mismatch")
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise ValueError("ABC video frame count does not match metadata") from error
        rgb = _run(
            "ffmpeg",
            [
                "-nostdin",
                *common,
                "-threads",
                "1",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                f"select=eq(n\\,{frame})",
                "-frames:v",
                "1",
                "-threads",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "pipe:1",
            ],
        )
        if len(rgb) != width * height * 3:
            raise ValueError("ABC video does not contain the selected complete frame")
        image = Image.frombytes("RGB", (width, height), rgb)
        if camera != "combined":
            top = cameras.index(camera) * camera_height
            image = image.crop((0, top, 224, top + camera_height))
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()


def prepare_episode(
    directory: Path,
    *,
    episode_id: str,
    source_revision: str,
    split: str = "unknown",
    domain: str = "unknown",
    frame_index: int = 0,
    camera: str = "top",
) -> PreparedEpisode:
    """Preview a local modern export; only the selected observation becomes input."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", episode_id):
        raise ValueError("A bounded episode ID without path separators is required")
    if not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", source_revision):
        raise ValueError("Source revision must be an immutable 40 or 64 character commit/checksum")
    if split not in {"train", "val", "unknown"}:
        raise ValueError("Split must be train, val or unknown")
    if domain not in {"real", "sim", "unknown"}:
        raise ValueError("Domain must be real, sim or unknown")
    frame_index = _integer(frame_index, "Frame index", 0, MAX_STEPS - 1)
    assets = _files(directory)
    try:
        metadata = json.loads(assets["episode_metadata.json"])
    except (ValueError, UnicodeError) as error:
        raise ValueError("Episode metadata must be a JSON object") from error
    if not isinstance(metadata, dict):
        raise ValueError("Episode metadata must be a JSON object")
    task = metadata.get("task_name")
    if not isinstance(task, str) or not 1 <= len(task.strip()) <= 10000:
        raise ValueError("Episode metadata requires a bounded task_name")
    cameras = metadata.get("cameras")
    if (
        not isinstance(cameras, list)
        or not 1 <= len(cameras) <= 3
        or any(not isinstance(c, str) or c not in {"top", "left", "right"} for c in cameras)
        or len(set(cameras)) != len(cameras)
    ):
        raise ValueError("Episode cameras must be a unique ordered list of top, left and right")
    if camera != "combined" and camera not in cameras:
        raise ValueError("The selected camera is unavailable in this episode")
    resolutions = metadata.get("camera_resolutions")
    simulated_preview = (
        metadata.get("alignment") is None
        and task.startswith("sim_")
        and metadata.get("render_backend") == "mjwarp"
        and metadata.get("fps") == 30
        and metadata.get("image_width") == 224
        and metadata.get("image_height") == 168
    )
    camera_height = 168 if simulated_preview else 224
    if not isinstance(resolutions, dict) or any(
        resolutions.get(c) != [224, camera_height] for c in cameras
    ):
        raise ValueError("ABC camera resolutions do not match a supported stacked export profile")
    if simulated_preview:
        if domain == "real":
            raise ValueError("Simulated ABC preview cannot be labelled as real robot data")
        if metadata.get("episode_id") != episode_id:
            raise ValueError("Supplied episode ID does not match simulated preview metadata")
        domain = "sim"
        timing = {"relative_timestamp_s": frame_index / 30, "clock": "simulation_frame_30hz"}
    else:
        if metadata.get("alignment") != "fixed_clock_30hz_causal":
            raise ValueError(
                "Unsupported ABC export: causal timing metadata is required; re-export MCAPs"
            )
        t0 = _integer(metadata.get("t0_ns"), "t0_ns", 0, 2**63 - 1)
        tick = _integer(metadata.get("tick_ns"), "tick_ns", TICK_NS, TICK_NS)
        timing = {
            "export_timestamp_ns": t0 + (frame_index + 1) * tick,
            "clock": "upstream_fixed_clock_30hz_causal",
        }
    steps = _integer(metadata.get("num_steps"), "num_steps", 1, MAX_STEPS)
    if frame_index >= steps:
        raise ValueError("Selected frame is outside this episode")
    if len(assets["states_actions.bin"]) != steps * 28 * 8:
        raise ValueError("State/action file must contain num_steps rows of 28 float64 values")
    # Never parse the demonstrated actions or future rows into candidate input.
    row = struct.unpack_from("<14d", assets["states_actions.bin"], frame_index * 28 * 8)
    if not all(math.isfinite(value) for value in row):
        raise ValueError("Selected robot state must contain finite values")
    image = _image(
        assets["combined_camera-images-rgb.mp4"],
        frame=frame_index,
        steps=steps,
        cameras=cameras,
        camera=camera,
        camera_height=camera_height,
    )
    source_files = {
        name: {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "media_type": FILES[name],
        }
        for name, data in assets.items()
    }
    warnings = list(WARNINGS)
    if simulated_preview:
        warnings.append(
            "Simulation preview uses relative frame time; absolute source timestamps are unavailable."
        )
    if domain == "unknown":
        warnings.append("Real versus simulated origin has not been supplied.")
    payload = {
        "name": f"{task.replace('_', ' ')[:110]} · frame {frame_index}",
        "task": task.replace("_", " ").strip(),
        "candidate_context": {
            "proprioception": list(row),
            "action_dim": 14,
            "state_dim": 14,
            "control_space": "joint_position",
            "robot_descriptor": {
                "robot_type": "ABC bimanual layout; exact robot model unverified",
                "arm_dof": 12,
                "gripper_dof": 2,
                "action_space": "joint_position",
                "proprioception_space": "joint_position",
                "state_dim_override": 14,
                "gripper_index": None,
            },
            "observation": {
                "frame_index": frame_index,
                **timing,
                "camera": camera,
                "camera_order": cameras if camera == "combined" else [camera],
                "camera_topic_identity": "unknown",
            },
            "robot_state": {
                "left_arm_joint_positions": list(row[:6]),
                "left_gripper_position": row[6],
                "right_arm_joint_positions": list(row[7:13]),
                "right_gripper_position": row[13],
                "gripper_indices": [6, 13],
                "arm_position_units": "radians",
                "gripper_position_units": "source_native_unverified",
                "completeness": "unknown_upstream_may_zero_fill_missing_channels",
                "robot_model": "not_supplied",
                "calibration": "not_supplied",
            },
        },
        "conditions": {"source_domain": domain, "evaluation_scope": "observation_and_plan"},
        "recorded_evidence": {},
        "reference_data": {
            "abc_source": {
                "dataset": "ABC public simulation preview"
                if simulated_preview
                else "XDOF/ABC-130k",
                "source_revision": source_revision.lower(),
                "episode_id": episode_id,
                "split": split,
                "domain": domain,
                "converter": "ABC public simulation export"
                if simulated_preview
                else "amazon-far/abc/export_mcap.py",
                "importer_version": "abc-export-v1",
                "frame_index": frame_index,
                "camera": camera,
                "source_files": source_files,
                "limitations": warnings,
            }
        },
    }
    fingerprint = content_hash(
        {"payload": payload, "image_sha256": hashlib.sha256(image).hexdigest()}
    )
    return PreparedEpisode(payload, image, assets, warnings, fingerprint)


def import_prepared(
    service, prepared: PreparedEpisode, expected_preview_hash: str | None = None
) -> dict:
    """Persist content-addressed references and one idempotent ordinary ROVE case."""
    actual = content_hash(
        {
            "payload": prepared.payload,
            "image_sha256": hashlib.sha256(prepared.image_bytes).hexdigest(),
        }
    )
    if actual != prepared.fingerprint or (
        expected_preview_hash is not None and actual != expected_preview_hash
    ):
        raise ValueError("ABC source or options changed; preview the episode again")
    references = prepared.payload["reference_data"]["abc_source"]["source_files"]
    for name, data in prepared.assets.items():
        if name not in FILES or hashlib.sha256(data).hexdigest() != references[name]["sha256"]:
            raise ValueError("ABC source assets changed after preview")
    if set(prepared.assets) != set(FILES):
        raise ValueError("ABC source assets are incomplete")
    service.validate_case_payload(prepared.payload)
    for name, data in prepared.assets.items():
        service.trials.save_asset(data, FILES[name])
    image = service.trials.save_asset(prepared.image_bytes, "image/png")
    return service.import_case(prepared.payload, image["sha256"], operation_id="abc-" + actual)
