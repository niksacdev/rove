"""Fail-closed loading for the pinned LeRobot pi0.5 implementation.

The upstream loader catches weight-loading exceptions and returns a model. An
evaluation must never mistake that uninitialized model for a trained policy.
This uses the upstream key conversion with a strict local safetensors load.
"""

from pathlib import Path

CAMERA_FEATURES = (
    "observation.images.top",
    "observation.images.left",
    "observation.images.right",
)


def load_policy(checkpoint: Path, device: str):
    """Load an ABC-adapted local checkpoint and its saved normalization pipelines."""
    from lerobot.configs.policies import PreTrainedConfig
    from lerobot.policies.factory import make_pre_post_processors

    checkpoint = Path(checkpoint).resolve(strict=True)
    config = PreTrainedConfig.from_pretrained(checkpoint, local_files_only=True)
    if tuple(config.output_features["action"].shape) != (14,):
        raise ValueError("pi0.5 checkpoint must be adapted to ABC's 14D bimanual actions")
    if tuple(config.input_features["observation.state"].shape) != (14,):
        raise ValueError("pi0.5 checkpoint must consume ABC's 14D bimanual state")
    if set(config.image_features) != set(CAMERA_FEATURES):
        raise ValueError("pi0.5 checkpoint must consume the ABC top, left and right cameras")
    if config.use_relative_actions:
        raise ValueError("This comparison recipe requires absolute joint-position actions")
    config.device = device
    config.compile_model = False
    policy = load_local_pi05(checkpoint, config=config).to(device).eval()
    preprocessor, postprocessor = make_pre_post_processors(
        config,
        pretrained_path=str(checkpoint),
        preprocessor_overrides={"device_processor": {"device": device}},
    )
    return policy, preprocessor, postprocessor


def load_local_pi05(checkpoint, *, config=None, **kwargs):
    from lerobot.configs.policies import PreTrainedConfig
    from lerobot.policies.pi05.modeling_pi05 import PI05Policy
    from safetensors.torch import load_file

    checkpoint = Path(checkpoint).resolve(strict=True)
    if not checkpoint.is_dir() or not (checkpoint / "model.safetensors").is_file():
        raise ValueError("A local pi0.5 snapshot with model.safetensors is required")
    config = config or PreTrainedConfig.from_pretrained(checkpoint, local_files_only=True)
    if config.type != "pi05":
        raise ValueError("The checkpoint must use policy type pi05")
    if config.use_visual_memory or config.use_proprioceptive_memory:
        raise ValueError("This first comparison requires a pi0.5 checkpoint without memory")
    state_dict = load_file(str(checkpoint / "model.safetensors"), device="cpu")
    if not state_dict:
        raise ValueError("Empty pi0.5 checkpoint")
    # The native constructor accepts dataset_stats/dataset_meta as extra kwargs.
    policy = PI05Policy(config, **kwargs)
    converted = policy._fix_pytorch_state_dict_keys(state_dict, config)
    converted = {
        key if key.startswith("model.") else f"model.{key}": value
        for key, value in converted.items()
    }
    policy.load_state_dict(converted, strict=True)
    policy.eval()
    return policy


def install_strict_training_loader():
    """Use the same failure behavior inside LeRobot's ordinary training CLI."""
    from lerobot.policies.pi05.modeling_pi05 import PI05Policy

    def from_pretrained(cls, pretrained_name_or_path, *, config=None, **kwargs):
        # Local snapshots pin weights; this runner does not download or resume.
        kwargs.pop("revision", None)
        return load_local_pi05(pretrained_name_or_path, config=config, **kwargs)

    PI05Policy.from_pretrained = classmethod(from_pretrained)
