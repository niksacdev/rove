"""Tests for LeRobot VLA adapter."""

import importlib

import pytest

from rove.models.config import get_endpoint_config

# Check if lerobot is installed
try:
    import lerobot  # noqa: F401

    HAS_LEROBOT = True
except ImportError:
    HAS_LEROBOT = False

requires_lerobot = pytest.mark.skipif(not HAS_LEROBOT, reason="lerobot not installed")


class TestLeRobotRegistryEntry:
    """Tests that always run (no lerobot dependency)."""

    def test_smolvla_endpoint_exists(self):
        ep = get_endpoint_config("smolvla-450m")
        assert ep.type == "vla"
        assert ep.adapter == "local_lerobot"

    def test_smolvla_config_fields(self):
        ep = get_endpoint_config("smolvla-450m")
        assert ep.config["model_id"] == "lerobot/smolvla_base"
        assert ep.config["device"] == "mps"
        assert ep.config["chunk_size"] == 10
        assert ep.config["requires_proprio"] is True

    def test_module_importable(self):
        mod = importlib.import_module("rove.adapters.local_lerobot")
        assert hasattr(mod, "LeRobotVLAAdapter")

    def test_adapter_in_registry_map(self):
        from rove.adapters.registry import _VLA_ADAPTERS

        assert "local_lerobot" in _VLA_ADAPTERS

    @pytest.mark.parametrize(
        "failure",
        [
            ModuleNotFoundError("No module named 'torch'", name="torch"),
            ImportError("cannot import name 'PreTrainedConfig' from 'lerobot.policies.pretrained'"),
            ImportError(
                "'av' is required but not installed. Install it with: pip install 'lerobot[dataset]'"
            ),
        ],
    )
    def test_unavailable_runtime_preserves_actual_import_failure(self, monkeypatch, failure):
        from rove.adapters import local_lerobot

        monkeypatch.setattr(local_lerobot, "HAS_LEROBOT", False)
        monkeypatch.setattr(local_lerobot, "_LEROBOT_IMPORT_ERROR", failure)

        with pytest.raises(ImportError) as caught:
            local_lerobot.LeRobotVLAAdapter("pi05", {"device": "cpu"})

        assert caught.value.__cause__ is failure
        message = str(caught.value)
        assert str(failure) in message
        assert "Python 3.12" in message
        assert "uv sync --frozen --extra smolvla --inexact" in message
        assert "Restart ROVE" in message
        assert "upstream version conflicts" in message
        assert "vla_local_inference_guide.md" in message


@requires_lerobot
class TestLeRobotAdapter:
    """Tests that require lerobot to be installed."""

    def test_protocol_compliance(self):
        from rove.adapters.local_lerobot import LeRobotVLAAdapter
        from rove.adapters.protocols import VLAAdapter

        adapter = LeRobotVLAAdapter(
            model_id="test",
            config={"model_id": "lerobot/smolvla_base", "device": "cpu"},
        )
        assert isinstance(adapter, VLAAdapter)

    def test_instantiation_does_not_load_weights(self):
        """An available runtime can be configured without model downloads."""
        from rove.adapters.local_lerobot import LeRobotVLAAdapter

        adapter = LeRobotVLAAdapter(model_id="test", config={"device": "cpu"})
        assert adapter.model_id == "test"
        assert adapter._hf_repo == "lerobot/smolvla_base"
        assert adapter._policy is None
