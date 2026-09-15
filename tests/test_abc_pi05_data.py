"""Data integrity and training-plan regressions without downloading model weights."""

import importlib.util
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/abc-pi05"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


data = load_module("abc_pi05_dataset", EXAMPLE / "dataset.py")
# train.py is also directly executable from the example directory.
with pytest.MonkeyPatch.context() as mp:
    mp.setitem(sys.modules, "dataset", data)
    training = load_module("abc_pi05_train", EXAMPLE / "train.py")
weights = load_module("abc_pi05_weights", EXAMPLE.parents[1] / "src/rove/bimanual/pi05_weights.py")


def make_episode(
    cache, *, split="train_sim", name="episode_one", length=2, marker=1.2, cameras=None
):
    episode = cache / split / name
    episode.mkdir(parents=True)
    metadata = {
        "task_name": "sim_put_the_plastic_bottles_in_the_bin",
        "num_steps": length,
        "fps": 30,
        "cameras": cameras or ["top", "left", "right"],
        "camera_resolutions": {cam: [224, 168] for cam in ("top", "left", "right")},
    }
    data.write_json(episode / "episode_metadata.json", metadata)
    rows = np.zeros((length, 28), dtype="<f8")
    rows[:, 0] = marker  # Arm radians can exceed1; never clamp to a generic normalized Box.
    rows[:, 14] = marker + 0.1
    rows[:, [6, 13, 20, 27]] = 0.5
    rows.tofile(episode / "states_actions.bin")
    (episode / "combined_camera-images-rgb.mp4").write_bytes(b"video")
    return episode


def mutate_metadata(ep, **kwargs):
    path = ep / "episode_metadata.json"
    data.write_json(path, {**json.loads(path.read_text()), **kwargs})


TASK = "sim_put_the_plastic_bottles_in_the_bin"


def test_plan_selects_explicit_training_tasks_and_excludes_public_validation(tmp_path):
    make_episode(tmp_path)
    make_episode(tmp_path, split="val_sim", name="episode_holdout", marker=1.5)
    eps, holdout = data.plan(tmp_path, ["train_sim"], [TASK])
    assert [ep.path.name for ep in eps] == ["episode_one"]
    assert holdout[0]["episode_id"] == "episode_holdout"
    with pytest.raises(ValueError, match="validation splits"):
        data.plan(tmp_path, ["val_sim"], [TASK])
    with pytest.raises(ValueError, match="No selected training episodes"):
        data.plan(tmp_path, ["train_sim"], ["different-task"])


@pytest.mark.parametrize("same_id", [False, True])
def test_plan_rejects_identity_or_content_leakage(tmp_path, same_id):
    make_episode(tmp_path)
    make_episode(
        tmp_path,
        split="val_real",
        name="episode_one" if same_id else "different_id",
        marker=1.5 if same_id else 1.2,
    )
    with pytest.raises(ValueError, match="overlaps public validation"):
        data.plan(tmp_path, ["train_sim"], [TASK])


def test_duplicate_training_episode_is_not_additional_data(tmp_path):
    make_episode(tmp_path)
    make_episode(tmp_path, split="train_real", name="duplicate")
    with pytest.raises(ValueError, match="duplicate training"):
        data.plan(tmp_path, ["train_sim", "train_real"], [TASK])


@pytest.mark.parametrize(
    "problem",
    [
        "missing_camera",
        "wrong_fps",
        "wrong_rows",
        "bad_gripper",
        "nonfinite",
        "truncated_binary",
        "instruction_conflict",
    ],
)
def test_reject_bad_alignment_or_robot_contract(tmp_path, problem):
    ep = make_episode(tmp_path)
    if problem == "missing_camera":
        mutate_metadata(ep, cameras=["top", "left"])
    elif problem == "wrong_fps":
        mutate_metadata(ep, fps=25)
    elif problem == "wrong_rows":
        mutate_metadata(ep, num_steps=5)
    elif problem == "truncated_binary":
        with (ep / "states_actions.bin").open("ab") as handle:
            handle.write(b"x")
    elif problem == "instruction_conflict":
        data.write_json(
            ep / "randomization.json", {"metadata": {"prompt": "put red object in blue bin"}}
        )
    else:
        rows = np.fromfile(ep / "states_actions.bin", dtype="<f8").reshape(-1, 28)
        rows[0, 20 if problem == "bad_gripper" else 2] = (
            1.1 if problem == "bad_gripper" else float("nan")
        )
        rows.tofile(ep / "states_actions.bin")
    with pytest.raises(ValueError):
        data.read_episode(ep, "train_sim")


def test_camera_order_padding_and_joint_targets_remain_distinct(tmp_path):
    ep = data.read_episode(make_episode(tmp_path, cameras=["right", "top", "left"]), "train_sim")
    frame = np.concatenate(
        [np.full((168, 224, 3), value, dtype=np.uint8) for value in (10, 20, 30)]
    )
    images = data.split_frame(frame, ep)
    assert images["observation.images.top"][28:196].mean() == 20
    assert images["observation.images.left"][28:196].mean() == 30
    assert images["observation.images.right"][28:196].mean() == 10
    assert images["observation.images.top"][:28].sum() == 0
    assert data.features()["action"]["names"][6::7] == ["left_gripper", "right_gripper"]


class DatasetDouble:
    @classmethod
    def create(cls, **kwargs):
        obj = cls()
        obj.root = kwargs["root"]
        (obj.root / "meta").mkdir(parents=True)
        obj.records = []
        obj.episodes = []
        obj.info = {"fps": kwargs["fps"], "features": kwargs["features"], "total_episodes": 0}
        return obj

    def add_frame(self, record):
        self.records.append(record)

    def save_episode(self, parallel_encoding=False):
        self.episodes.append(self.records)
        self.records = []

    def finalize(self):
        self.info["total_episodes"] = len(self.episodes)
        data.write_json(self.root / "meta/info.json", self.info)
        stats = {
            key: {"q01": [0.0] * 14, "q99": [2.0] * 14} for key in ("observation.state", "action")
        }
        data.write_json(self.root / "meta/stats.json", stats)


def convert_double(tmp_path, monkeypatch):
    cache, output = tmp_path / "cache", tmp_path / "converted"
    make_episode(cache)
    monkeypatch.setattr(
        data, "frames", lambda ep: iter([np.zeros((504, 224, 3), dtype=np.uint8)] * ep.length)
    )
    manifest = data.convert(
        cache, output, "local/bottles", ["train_sim"], [TASK], dataset_class=DatasetDouble
    )
    return output, manifest


def test_conversion_finalizes_fingerprints_and_separates_state_action(tmp_path, monkeypatch):
    written = []
    original = DatasetDouble.add_frame

    def remember(self, record):
        written.append(record)
        original(self, record)

    monkeypatch.setattr(DatasetDouble, "add_frame", remember)
    output, manifest = convert_double(tmp_path, monkeypatch)
    assert manifest["status"] == "completed"
    assert written[0]["observation.state"][0] == np.float32(1.2)
    assert written[0]["action"][0] == np.float32(1.3)
    assert written[0]["task"] == "sim put the plastic bottles in the bin"
    assert len(manifest["episodes"][0]["source_files"]) == 3
    training.validate_dataset(output)
    (output / "meta/stats.json").write_text("{}")
    with pytest.raises(ValueError, match="changed after conversion"):
        training.validate_dataset(output)


def test_failed_video_does_not_publish_completed_dataset(tmp_path, monkeypatch):
    cache, output = tmp_path / "cache", tmp_path / "converted"
    make_episode(cache)
    monkeypatch.setattr(data, "frames", lambda ep: iter([]))
    with pytest.raises(ValueError):
        data.convert(
            cache, output, "local/bottles", ["train_sim"], [TASK], dataset_class=DatasetDouble
        )
    assert json.loads((output / "rove_dataset_manifest.json").read_text())["status"] == "failed"
    with pytest.raises(ValueError, match="successfully converted"):
        training.validate_dataset(output)


def test_training_recipe_uses_local_dataset_weights_and_retains_saved_checkpoints(
    tmp_path, monkeypatch
):
    output, _ = convert_double(tmp_path, monkeypatch)
    checkpoint = tmp_path / "base"
    checkpoint.mkdir()
    data.write_json(checkpoint / "config.json", {"type": "pi05"})
    (checkpoint / "model.safetensors").write_bytes(b"test-weights-only-not-inference")
    recipe = training.training_plan(output, checkpoint, tmp_path / "trained", steps=20)
    args = recipe["native_training_arguments"]
    assert "--policy.type=pi05" in args
    assert f"--policy.pretrained_path={checkpoint}" in args
    assert "--policy.n_action_steps=15" in args
    assert "--save_freq=20" in args
    assert "--policy.push_to_hub=false" in args
    assert "--dataset.eval_split=0" in args
    assert recipe["status"] == "planned"
    assert recipe["prompts"] == ["sim put the plastic bottles in the bin"]


def test_checkpoint_provenance_travels_with_weights_without_local_paths(tmp_path, monkeypatch):
    dataset, _ = convert_double(tmp_path, monkeypatch)
    base = tmp_path / "private-base-location"
    base.mkdir()
    data.write_json(base / "config.json", {"type": "pi05"})
    (base / "model.safetensors").write_bytes(b"synthetic-parent-checkpoint")
    recipe = training.training_plan(dataset, base, tmp_path / "trained", steps=20, seed=12)
    checkpoint = tmp_path / "trained/checkpoints/last/pretrained_model"
    checkpoint.mkdir(parents=True)
    (checkpoint / "model.safetensors").write_bytes(b"synthetic-adapted-checkpoint")
    data.write_json(checkpoint / "config.json", {"type": "pi05"})
    receipt_hashes = training.write_checkpoint_provenance(checkpoint, recipe)
    manifest_path = checkpoint / training.PORTABLE_MANIFEST
    portable = json.loads(manifest_path.read_text())
    assert portable["dataset_manifest_sha256"] == data.sha256(
        dataset / "rove_dataset_manifest.json"
    )
    assert portable["base_weights_sha256"] == data.sha256(base / "model.safetensors")
    assert portable["base_config_sha256"] == data.sha256(base / "config.json")
    assert portable["seed"] == 12 and portable["training_steps"] == 20
    assert portable["source_splits"] == ["train_sim"]
    assert portable["prompts"] == ["sim put the plastic bottles in the bin"]
    assert portable["lerobot_revision"] == data.LEROBOT_REVISION
    assert set(portable["recipe_source_sha256"]) == {"train.py", "dataset.py", "pi05_weights.py"}
    assert training.PORTABLE_MANIFEST not in portable["checkpoint_files"]
    assert receipt_hashes[training.PORTABLE_MANIFEST] == data.sha256(manifest_path)
    assert str(tmp_path) not in manifest_path.read_text()
    assert "native_training_arguments" not in portable
    assert "output" not in portable
    with pytest.raises(ValueError, match="already has a training manifest"):
        training.write_checkpoint_provenance(checkpoint, recipe)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_ffmpeg_decodes_frame_order_and_checks_exact_length(tmp_path):
    ep_path = make_episode(tmp_path)
    video = ep_path / "combined_camera-images-rgb.mp4"
    frames = np.stack([np.full((504, 224, 3), value, dtype=np.uint8) for value in (30, 90)])
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            "224x504",
            "-r",
            "30",
            "-i",
            "pipe:0",
            "-c:v",
            "libx264",
            "-crf",
            "0",
            str(video),
        ],
        input=frames.tobytes(),
        check=True,
    )
    decoded = list(data.frames(data.read_episode(ep_path, "train_sim")))
    assert len(decoded) == 2
    assert decoded[0].mean() == pytest.approx(30, abs=2)
    assert decoded[1].mean() == pytest.approx(90, abs=2)
    mutate_metadata(ep_path, num_steps=1)
    (ep_path / "states_actions.bin").write_bytes(
        (ep_path / "states_actions.bin").read_bytes()[: 28 * 8]
    )
    with pytest.raises(ValueError, match="more frames"):
        list(data.frames(data.read_episode(ep_path, "train_sim")))


def test_strict_loader_never_returns_random_model_when_weights_fail(tmp_path, monkeypatch):
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    (checkpoint / "model.safetensors").write_bytes(b"invalid")
    config = types.SimpleNamespace(
        type="pi05", use_visual_memory=False, use_proprioceptive_memory=False
    )
    modules = {
        "lerobot.configs.policies": types.SimpleNamespace(PreTrainedConfig=None),
        "lerobot.policies.pi05.modeling_pi05": types.SimpleNamespace(PI05Policy=None),
        "safetensors.torch": types.SimpleNamespace(
            load_file=lambda *a, **k: (_ for _ in ()).throw(ValueError("corrupt weights"))
        ),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    with pytest.raises(ValueError, match="corrupt weights"):
        weights.load_local_pi05(checkpoint, config=config)


def test_strict_loader_propagates_incompatible_tensor_shapes(tmp_path, monkeypatch):
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    (checkpoint / "model.safetensors").write_bytes(b"shape-test")
    config = types.SimpleNamespace(
        type="pi05", use_visual_memory=False, use_proprioceptive_memory=False
    )

    class PolicyDouble:
        def __init__(self, config):
            pass

        def _fix_pytorch_state_dict_keys(self, values, config):
            return values

        def load_state_dict(self, values, strict):
            assert strict is True
            assert "model.projection.weight" in values
            raise RuntimeError("size mismatch")

    for name, module in {
        "lerobot.configs.policies": types.SimpleNamespace(PreTrainedConfig=None),
        "lerobot.policies.pi05.modeling_pi05": types.SimpleNamespace(PI05Policy=PolicyDouble),
        "safetensors.torch": types.SimpleNamespace(
            load_file=lambda *a, **k: {"projection.weight": object()}
        ),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    with pytest.raises(RuntimeError, match="size mismatch"):
        weights.load_local_pi05(checkpoint, config=config)
