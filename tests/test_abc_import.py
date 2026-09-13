"""ABC imports retain causal observations and privately preserve source evidence."""

import hashlib
import io
import json
import shutil
import struct
import subprocess
from dataclasses import replace

import pytest
from PIL import Image

from rove.datasets import abc
from rove.datasets.service import DatasetService

REVISION = "a" * 40


@pytest.fixture
def episode(tmp_path, monkeypatch):
    directory = tmp_path / "episode_example"
    directory.mkdir()
    metadata = {
        "task_name": "put_bottles_in_bin",
        "cameras": ["top", "left", "right"],
        "camera_resolutions": {camera: [224, 224] for camera in ["top", "left", "right"]},
        "alignment": "fixed_clock_30hz_causal",
        "t0_ns": 1_000_000_000,
        "tick_ns": abc.TICK_NS,
        "num_steps": 3,
    }
    (directory / "episode_metadata.json").write_text(json.dumps(metadata))
    rows = [float(i) for i in range(84)]
    (directory / "states_actions.bin").write_bytes(struct.pack("<84d", *rows))
    (directory / "combined_camera-images-rgb.mp4").write_bytes(b"fixture-video")
    image = io.BytesIO()
    Image.new("RGB", (224, 224), "blue").save(image, format="PNG")
    monkeypatch.setattr(abc, "_image", lambda *args, **kwargs: image.getvalue())
    return directory


def prepare(episode, **options):
    return abc.prepare_episode(
        episode, episode_id=episode.name, source_revision=REVISION, **options
    )


def change_metadata(episode, **changes):
    path = episode / "episode_metadata.json"
    metadata = json.loads(path.read_text())
    metadata.update(changes)
    path.write_text(json.dumps(metadata))


def test_causal_frame_and_state_only_reach_candidate(episode):
    prepared = prepare(episode, frame_index=1)
    context = prepared.payload["candidate_context"]
    assert context["observation"]["export_timestamp_ns"] == 1_000_000_000 + 2 * abc.TICK_NS
    assert context["robot_state"]["left_arm_joint_positions"] == list(range(28, 34))
    assert context["robot_state"]["right_gripper_position"] == 41
    assert "ground_truth_action" not in json.dumps(context)
    assert "actions" not in context
    assert all(value < 42 for value in context["proprioception"])
    assert "source_files" not in context
    assert prepared.payload["recorded_evidence"] == {}
    assert context["robot_state"]["robot_model"] == "not_supplied"
    assert "unknown" in context["robot_state"]["completeness"]


def test_import_is_idempotent_with_private_source_assets(episode, tmp_path):
    service = DatasetService(tmp_path / "store")
    prepared = prepare(episode)
    first = abc.import_prepared(service, prepared, prepared.fingerprint)
    second = abc.import_prepared(service, prepare(episode))
    assert first["case_revision_id"] == second["case_revision_id"]
    assert len(prepared.preview()["thumbnail_data_uri"]) > 100
    for data in prepared.assets.values():
        digest = hashlib.sha256(data).hexdigest()
        assert service.trials.asset_path(digest).read_bytes() == data
    with service.trials.connect() as db:
        assert db.execute("SELECT count(*) FROM cases").fetchone()[0] == 1


def test_changes_create_distinct_identity_and_stale_preview_rejects(episode, tmp_path):
    first = prepare(episode)
    second = prepare(episode, frame_index=1)
    assert first.fingerprint != second.fingerprint
    service = DatasetService(tmp_path / "store")
    with pytest.raises(ValueError, match="preview"):
        abc.import_prepared(service, second, first.fingerprint)
    changed = replace(first, assets={**first.assets, "states_actions.bin": b"changed"})
    with pytest.raises(ValueError, match="assets changed"):
        abc.import_prepared(service, changed)
    first.payload["task"] = "changed after preview"
    with pytest.raises(ValueError, match="preview"):
        abc.import_prepared(service, first)


@pytest.mark.parametrize(
    "changes",
    [
        {"alignment": None},
        {"tick_ns": 1},
        {"t0_ns": True},
        {"num_steps": 4},
        {"cameras": ["top", "top"]},
        {"cameras": []},
        {"cameras": [{}]},
        {"camera_resolutions": []},
        {"camera_resolutions": {}},
        {"task_name": ""},
        {"num_steps": 0},
    ],
)
def test_invalid_metadata_is_rejected(episode, changes):
    change_metadata(episode, **changes)
    with pytest.raises(ValueError):
        prepare(episode)


@pytest.mark.parametrize(
    "options",
    [
        {"frame_index": 3},
        {"frame_index": -1},
        {"frame_index": True},
        {"camera": "future"},
        {"split": "random"},
        {"domain": "physical-ish"},
    ],
)
def test_invalid_selection_is_rejected(episode, options):
    with pytest.raises(ValueError):
        prepare(episode, **options)


def test_revision_must_be_immutable_and_episode_id_is_not_a_path(episode):
    with pytest.raises(ValueError, match="immutable"):
        abc.prepare_episode(episode, episode_id="abc", source_revision="main")
    with pytest.raises(ValueError, match="episode ID"):
        abc.prepare_episode(episode, episode_id="../abc", source_revision=REVISION)


def test_symlink_and_nonfinite_current_state_rejected(episode, tmp_path):
    path = episode / "states_actions.bin"
    original = path.read_bytes()
    path.write_bytes(struct.pack("<d", float("nan")) + original[8:])
    with pytest.raises(ValueError, match="finite"):
        prepare(episode)
    path.unlink()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(original)
    path.symlink_to(outside)
    with pytest.raises(ValueError, match="regular local"):
        prepare(episode)


def test_future_actions_are_not_interpreted_as_current_state(episode):
    path = episode / "states_actions.bin"
    content = bytearray(path.read_bytes())
    struct.pack_into("<d", content, 14 * 8, float("nan"))
    path.write_bytes(content)
    assert prepare(episode).payload["candidate_context"]["robot_state"][
        "left_arm_joint_positions"
    ] == list(range(6))


def test_file_size_limit_before_decode(episode, monkeypatch):
    monkeypatch.setattr(abc, "MAX_FILE_BYTES", 10)
    with pytest.raises(ValueError, match="size limit"):
        prepare(episode)


def test_decoder_requires_local_bounded_commands(monkeypatch):
    monkeypatch.setattr(abc.shutil, "which", lambda name: "/usr/bin/" + name)
    seen = []

    def run(args, **kwargs):
        seen.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, b"ok", b"")

    monkeypatch.setattr(abc.subprocess, "run", run)
    assert abc._run("ffprobe", ["-f", "mov"]) == b"ok"
    assert seen[0][1]["timeout"] == 60
    assert "shell" not in seen[0][1]


def test_decoder_failure_and_timeout_do_not_leak_stderr(monkeypatch):
    monkeypatch.setattr(abc.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        abc.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 1, b"", b"private path"),
    )
    with pytest.raises(ValueError, match="local MP4"):
        abc._run("ffmpeg", [])

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("private path", 60)

    monkeypatch.setattr(abc.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="exceeded"):
        abc._run("ffmpeg", [])


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="Local FFmpeg required"
)
def test_real_video_frame_selection_crop_and_cardinality(tmp_path):
    # Separate colors across time and camera bands catches frame-index and crop mistakes.
    width, height = 224, 672
    raw = b""
    for top in ["red", "green", "blue"]:
        image = Image.new("RGB", (width, height), top)
        image.paste(Image.new("RGB", (224, 224), "yellow"), (0, 224))
        image.paste(Image.new("RGB", (224, 224), "white"), (0, 448))
        raw += image.tobytes()
    path = tmp_path / "example.mp4"
    subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pixel_format",
            "rgb24",
            "-video_size",
            "224x672",
            "-framerate",
            "30",
            "-i",
            "pipe:0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(path),
        ],
        input=raw,
        check=True,
        timeout=20,
    )
    data = abc._image(
        path.read_bytes(), frame=2, steps=3, cameras=["top", "left", "right"], camera="top"
    )
    with Image.open(io.BytesIO(data)) as image:
        red, green, blue = image.getpixel((100, 100))
        assert blue > 200 and red < 20 and green < 20
    left = abc._image(
        path.read_bytes(), frame=1, steps=3, cameras=["top", "left", "right"], camera="left"
    )
    with Image.open(io.BytesIO(left)) as image:
        red, green, blue = image.getpixel((100, 100))
        assert red > 200 and green > 200 and blue < 20
    with pytest.raises(ValueError, match="frame count"):
        abc._image(
            path.read_bytes(), frame=0, steps=4, cameras=["top", "left", "right"], camera="top"
        )


def test_verified_simulation_profile_uses_relative_clock_without_private_metadata(episode):
    change_metadata(
        episode,
        alignment=None,
        t0_ns=None,
        tick_ns=None,
        task_name="sim_put_bottles_in_bin",
        episode_id=episode.name,
        render_backend="mjwarp",
        fps=30.0,
        image_width=224,
        image_height=168,
        camera_resolutions={c: [224, 168] for c in ["top", "left", "right"]},
        source_episode_dir="/private/customer/source",
    )
    prepared = prepare(episode, frame_index=2)
    context = prepared.payload["candidate_context"]
    assert context["observation"]["relative_timestamp_s"] == 2 / 30
    assert "export_timestamp_ns" not in context["observation"]
    assert prepared.payload["conditions"]["source_domain"] == "sim"
    assert "/private/customer" not in json.dumps(prepared.payload)
    assert (
        prepared.payload["reference_data"]["abc_source"]["dataset"]
        == "ABC public simulation preview"
    )
    assert "export_mcap" not in prepared.payload["reference_data"]["abc_source"]["converter"]
    with pytest.raises(ValueError, match="labelled as real"):
        prepare(episode, domain="real")


def test_campaign_receives_current_state_and_bimanual_descriptor_without_reference(
    episode, tmp_path
):
    from rove.benchmarks.workflow import LaunchRequest, prepare_launch
    from rove.models.config import load_config

    root = tmp_path / "store"
    service = DatasetService(root)
    case = abc.import_prepared(service, prepare(episode, frame_index=1))
    contract = service.create_contract(
        {
            "name": "Observation",
            "scope": "plan_quality",
            "evidence_mode": "candidate_output",
            "criteria": [{"id": "plan", "description": "Feasible plan"}],
        }
    )
    launch, _ = prepare_launch(
        root,
        LaunchRequest(
            case_revision_ids=[case["case_revision_id"]],
            contract_id=contract["id"],
            strategies=["mock"],
            seeds=[0],
            ks=[1],
        ),
        load_config(),
    )
    extras = launch["spec"]["tasks"][0]["example"]["extras"]
    assert extras["proprioception"] == list(range(28, 42))
    assert extras["robot_descriptor"]["arm_dof"] == 12
    assert extras["robot_descriptor"]["gripper_dof"] == 2
    assert extras["action_dim"] == 14
    assert extras["control_space"] == "joint_position"
    assert all(
        key not in extras
        for key in [
            "reference_data",
            "source_files",
            "episode",
            "ground_truth_action",
            "robot_asset",
        ]
    )


@pytest.mark.asyncio
async def test_imported_bimanual_state_reaches_vla_and_rejects_seven_dimensional_model(episode):
    from rove.adapters.mock_vla import MockVLAAdapter
    from rove.adapters.mock_vlm import MockVLMAdapter
    from rove.models import ExampleData
    from rove.orchestrator.pipeline import EvaluationPipeline

    seen = {}

    class CapturingVLA(MockVLAAdapter):
        async def predict_action(
            self, image_base64, task, proprioception=None, plan=None, embodiment=None
        ):
            seen.update(state=proprioception, embodiment=embodiment)
            return await super().predict_action(
                image_base64, task, proprioception, plan, embodiment
            )

    prepared = prepare(episode, frame_index=1)
    example = ExampleData(extras=prepared.payload["candidate_context"])
    adapter = CapturingVLA(
        config={
            "mock_latency_ms": [0, 0],
            "vla_capabilities": {
                "native_action_dim": 14,
                "max_action_dim": 14,
                "native_action_space": "joint_position",
            },
        }
    )
    pipeline = EvaluationPipeline(
        act_adapter=adapter, verify_adapter=MockVLMAdapter(config={"mock_latency_ms": [0, 0]})
    )
    results = [
        result
        async for result in pipeline.run_trial(prepared.payload["task"], "image", example=example)
    ]
    assert results
    assert seen["state"] == list(range(28, 42))
    assert seen["embodiment"].action_dim == 14
    assert seen["embodiment"].state_dim == 14
    assert seen["embodiment"].arm_dof == 12
    assert seen["embodiment"].gripper_dof == 2
    assert seen["embodiment"].urdf_path is None
    limited = EvaluationPipeline(
        act_adapter=MockVLAAdapter(
            config={"vla_capabilities": {"native_action_dim": 7, "max_action_dim": 7}}
        )
    )
    with pytest.raises(ValueError, match="robot requires 14"):
        _ = [result async for result in limited.run_trial("task", "image", example=example)]
