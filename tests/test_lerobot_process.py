"""Isolated VLA boundary tests; no ML dependencies or model downloads required."""

import asyncio
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from rove.adapters.lerobot_process import run_worker, validate_prediction
from rove.adapters.lerobot_worker import (
    action_horizon,
    asset_manifest,
    load_policy,
    observation_contract,
    verify_asset_manifest,
    verify_runtime_versions,
)

POLICY = {
    "output_features": {"action": {"shape": [2]}},
    "input_features": {
        "observation.state": {"type": "STATE", "shape": [2]},
        "observation.images.front": {"type": "VISUAL", "shape": [3, 8, 8]},
        "observation.images.wrist": {"type": "VISUAL", "shape": [3, 8, 8]},
    },
}


def prediction():
    return {
        "actions": [[0.1, 0.2]],
        "num_steps": 1,
        "declared_action_dim": 2,
        "action_space": None,
        "execution_eligible": False,
    }


@pytest.mark.parametrize("state", [None, [], [1], [1, 2, 3], [1, float("nan")], [True, 1]])
def test_strict_state_contract_rejects_missing_invalid_and_mismatched_values(state):
    with pytest.raises(ValueError):
        observation_contract({}, {"proprioception": state}, POLICY)


def test_probe_records_assumptions_and_preserves_supplied_observation_keys():
    state, key, warnings = observation_contract({"input_mode": "observation_probe"}, {}, POLICY)
    assert state == [0.0, 0.0]
    assert key == "observation.images.front"
    assert any("not measured" in warning for warning in warnings)
    assert any("wrist" in warning for warning in warnings)
    assert any("not evidence" in warning for warning in warnings)
    with pytest.raises(ValueError, match="exactly 2"):
        observation_contract(
            {"input_mode": "observation_probe"}, {"proprioception": [1, 2, 3]}, POLICY
        )


def test_measured_state_is_unchanged():
    state, _key, warnings = observation_contract({}, {"proprioception": [0.2, -0.8]}, POLICY)
    assert state == [0.2, -0.8]
    assert not any("Zero state" in warning for warning in warnings)


@pytest.mark.parametrize(
    "mutation",
    [
        {"state_dim_override": 3},
        {"arm_dof": 3, "state_dim_override": 2},
        {"robot_type": "other"},
        {"proprioception_space": "eef_delta"},
        {"action_space": "joint_delta"},
    ],
)
def test_strict_embodiment_mismatch_is_rejected(mutation):
    embodiment = {
        "arm_dof": 1,
        "gripper_dof": 1,
        "robot_type": "test",
        "proprioception_space": "joint_position",
        "action_space": "eef_delta",
    }
    with pytest.raises(ValueError, match="embodiment"):
        observation_contract(
            {
                "robot_type": "test",
                "proprioception_space": "joint_position",
                "action_space": "eef_delta",
            },
            {"proprioception": [0.2, 0.3], "embodiment": {**embodiment, **mutation}},
            POLICY,
        )


def test_horizon_cannot_silently_start_second_chunk():
    policy = {"chunk_size": 50, "n_action_steps": 10}
    assert action_horizon({"chunk_size": 10}, policy) == 10
    with pytest.raises(ValueError, match="n_action_steps"):
        action_horizon({"chunk_size": 11}, policy)


def test_strict_embodiment_accepts_legacy_action_space_alias():
    state, _key, _warnings = observation_contract(
        {"action_space": "ee_delta"},
        {
            "proprioception": [0.2, 0.3],
            "embodiment": {"arm_dof": 1, "gripper_dof": 1, "action_space": "eef_delta"},
        },
        POLICY,
    )
    assert state == [0.2, 0.3]


@pytest.mark.parametrize(
    "mutation",
    [
        {"actions": [[float("inf"), 1]]},
        {"actions": [[1]]},
        {"num_steps": 2},
        {"declared_action_dim": 0},
        {"actions": [[True, 1]]},
        {"action_space": "eef_delta"},
        {"execution_eligible": True},
    ],
)
def test_invalid_worker_results_never_become_probe_evidence(mutation):
    with pytest.raises(ValueError):
        validate_prediction({**prediction(), **mutation}, {"input_mode": "observation_probe"})


async def test_bridge_has_no_dependency_on_main_environment_lerobot_and_filters_credentials(
    monkeypatch,
):
    from rove.adapters import local_lerobot

    captured = {}

    class Process:
        returncode = 0

        async def wait(self):
            return 0

    async def spawn(*args, **kwargs):
        captured.update(kwargs)
        request = json.loads(Path(args[2]).read_text())
        assert "secret" not in request["config"]
        Path(args[3]).write_text(json.dumps(prediction()))
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(local_lerobot, "HAS_LEROBOT", False)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-only-credential")
    adapter = local_lerobot.LeRobotVLAAdapter(
        "test",
        {
            "runtime_python": sys.executable,
            "input_mode": "observation_probe",
            "secret": "not-for-worker",  # pragma: allowlist secret — synthetic test value
        },
    )
    result = await adapter.predict_action("aW1hZ2U=", "pick target")
    assert result.actions == [[0.1, 0.2]]
    assert "AZURE_OPENAI_API_KEY" not in captured["env"]
    assert captured["env"]["HF_HUB_OFFLINE"] == "1"
    assert "PYTHONPATH" not in captured["env"]


async def test_timeout_kills_and_reaps_isolated_worker(monkeypatch):
    class Process:
        returncode = None
        killed = False

        async def wait(self):
            if self.returncode is None:
                await asyncio.sleep(10)
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9

    process = Process()

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(TimeoutError):
        await run_worker(sys.executable, {"runtime_timeout_s": 0.01}, {"operation": "health"})
    assert process.killed


async def test_cancellation_kills_and_reaps_isolated_worker(monkeypatch):
    started = asyncio.Event()

    class Process:
        returncode = None
        wait_calls = 0
        killed = False

        async def wait(self):
            self.wait_calls += 1
            started.set()
            if self.returncode is None:
                await asyncio.sleep(10)
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9

    process = Process()

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(run_worker(sys.executable, {}, {"operation": "health"}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed
    assert process.wait_calls == 2


@pytest.mark.parametrize("role", ["checkpoint", "tokenizer"])
def test_content_manifest_rejects_changed_assets_even_same_size(tmp_path, role):
    checkpoint = tmp_path / "checkpoint"
    tokenizer = tmp_path / "tokenizer"
    checkpoint.mkdir()
    tokenizer.mkdir()
    (checkpoint / "model.safetensors").write_bytes(b"initial")
    (tokenizer / "tokenizer.json").write_bytes(b"initial")
    manifest = checkpoint / "rove-manifest.json"
    manifest.write_text(json.dumps(asset_manifest(checkpoint, tokenizer)))
    assert len(verify_asset_manifest(checkpoint, tokenizer)) == 64
    target = (
        checkpoint / "model.safetensors" if role == "checkpoint" else tokenizer / "tokenizer.json"
    )
    target.write_bytes(b"changed")
    with pytest.raises(ValueError, match="assets changed"):
        verify_asset_manifest(checkpoint, tokenizer)


def test_refrozen_assets_cannot_replay_old_pinned_configuration(tmp_path):
    checkpoint, tokenizer = tmp_path / "checkpoint", tmp_path / "tokenizer"
    checkpoint.mkdir()
    tokenizer.mkdir()
    weights = checkpoint / "model.safetensors"
    weights.write_bytes(b"initial")
    manifest = checkpoint / "rove-manifest.json"
    manifest.write_text(json.dumps(asset_manifest(checkpoint, tokenizer)))
    pinned = verify_asset_manifest(checkpoint, tokenizer)
    assert verify_asset_manifest(checkpoint, tokenizer, pinned) == pinned
    weights.write_bytes(b"changed")
    manifest.write_text(json.dumps(asset_manifest(checkpoint, tokenizer)))
    with pytest.raises(ValueError, match="pinned endpoint"):
        verify_asset_manifest(checkpoint, tokenizer, pinned)


def test_runtime_upgrade_cannot_replay_old_pinned_configuration(monkeypatch):
    from rove.adapters import lerobot_worker

    pinned = {
        "torch": "2.11.0",
        "lerobot": "0.6.1",
        "transformers": "5.5.4",
        "safetensors": "0.8.0",
    }
    monkeypatch.setattr(lerobot_worker, "version", pinned.__getitem__)
    assert verify_runtime_versions(pinned) == pinned
    actual = {**pinned, "transformers": "5.10.0"}
    monkeypatch.setattr(lerobot_worker, "version", actual.__getitem__)
    with pytest.raises(
        ValueError, match=r"transformers version 5\.10\.0 differs from pinned 5\.5\.4"
    ):
        verify_runtime_versions(pinned)
    assert verify_runtime_versions() == actual


@pytest.mark.parametrize("pins", [{"torch": "2.11.0"}, [], {"unexpected": "1.0"}])
def test_partial_or_invalid_runtime_pin_is_rejected(pins):
    with pytest.raises(ValueError, match="must pin"):
        verify_runtime_versions(pins)


def test_pi_load_failure_cannot_return_random_or_partial_weights(monkeypatch, tmp_path):
    module = ModuleType("safetensors.torch")
    module.load_file = lambda path: {"weight": "tensor"}
    monkeypatch.setitem(sys.modules, "safetensors.torch", module)

    class Policy:
        def __init__(self, config):
            pass

        def _fix_pytorch_state_dict_keys(self, tensors, config):
            return tensors

        def load_state_dict(self, tensors, strict):
            assert strict is True
            assert tensors == {"model.weight": "tensor"}
            raise RuntimeError("Missing pretrained weights")

        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            pytest.fail("Unsafe upstream loader must never be called for Pi05")

    with pytest.raises(RuntimeError, match="Missing pretrained weights"):
        load_policy(Policy, SimpleNamespace(type="pi05"), tmp_path)
