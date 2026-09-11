"""LeRobot VLA adapter — local inference for SmolVLA, pi0.5, and other LeRobot policies."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from functools import partial
from typing import Any

import numpy as np

from rove.models import ActionPrediction, ActionSpace, RobotEmbodiment, TaskPlan, VLACapabilities

logger = logging.getLogger(__name__)

try:
    import torch
    from lerobot.policies.factory import get_policy_class
    from lerobot.policies.pretrained import PreTrainedConfig, PreTrainedPolicy
    from PIL import Image

    HAS_LEROBOT = True
except ImportError:
    HAS_LEROBOT = False



class LeRobotVLAAdapter:
    """VLA adapter for LeRobot-compatible policies (SmolVLA, pi0.5, etc.).

    Loads a pretrained policy from HuggingFace and runs local inference.
    Automatically detects the policy type and builds the correct observation batch.
    """

    def __init__(self, model_id: str, config: dict[str, Any] | None = None):
        if not HAS_LEROBOT:
            raise ImportError(
                "lerobot is required for LeRobotVLAAdapter. "
                "Install with: uv pip install rove-eval[smolvla]"
            )

        self.model_id = model_id
        cfg = config or {}
        self.display_name = cfg.get("display_name", model_id)
        self._hf_repo = cfg.get("model_id", "lerobot/smolvla_base")
        self._device = cfg.get("device", "mps")
        self._chunk_size = cfg.get("chunk_size", 10)
        self._requires_proprio = cfg.get("requires_proprio", True)
        self._trained_robot = cfg.get("trained_robot")  # e.g., "so100", "panda"
        self._policy: PreTrainedPolicy | None = None
        self._tokenizer = None
        self._tokenizer_config = cfg.get("tokenizer", {})

        # Build capabilities from YAML config; fall back to sensible defaults
        caps_data = cfg.get("vla_capabilities", {})
        self._capabilities = (
            VLACapabilities(**caps_data) if caps_data else VLACapabilities(native_action_dim=7)
        )

    @property
    def capabilities(self) -> VLACapabilities:
        """Return VLA capabilities.

        Pre-load: returns config-driven values from rove.yaml.
        Post-load: refined from the actual checkpoint config.
        """
        return self._capabilities

    def _load_model(self) -> PreTrainedPolicy:
        """Load and cache the pretrained policy (called in executor)."""
        if self._policy is not None:
            return self._policy

        logger.info("Loading LeRobot policy %s on %s", self._hf_repo, self._device)

        config = PreTrainedConfig.from_pretrained(self._hf_repo)

        # Disable torch.compile on MPS — inductor doesn't support welford_combine
        if self._device == "mps" and hasattr(config, "compile_model"):
            config.compile_model = False

        policy_cls = get_policy_class(config.type)

        try:
            policy = policy_cls.from_pretrained(self._hf_repo, config=config)
            # MPS doesn't fully support BFloat16 matmul — cast to Float32
            if self._device == "mps":
                policy = policy.to(dtype=torch.float32)
            policy = policy.to(self._device)
        except (RuntimeError, AssertionError):
            logger.warning("Device %s failed, falling back to cpu", self._device)
            policy = policy_cls.from_pretrained(self._hf_repo, config=config)
            policy = policy.to(dtype=torch.float32).to("cpu")

        policy.train(False)
        self._policy = policy

        # Refine capabilities from the actual checkpoint config
        self._refine_capabilities(policy.config)

        return policy

    def _refine_capabilities(self, config) -> None:
        """Refine capabilities from actual checkpoint config after model load."""
        action_feature = config.output_features.get("action")
        native_dim = (
            action_feature.shape[0] if action_feature else self._capabilities.native_action_dim
        )
        is_pi05 = self._is_pi05(config)
        self._capabilities = VLACapabilities(
            native_action_dim=native_dim,
            native_action_space=self._capabilities.native_action_space,
            max_action_dim=32 if is_pi05 else self._capabilities.max_action_dim,
            supported_robot_types=None if is_pi05 else self._capabilities.supported_robot_types,
        )

    def _get_tokenizer(self, policy: PreTrainedPolicy):
        """Get the native tokenizer or an explicitly pinned compatible fallback."""
        if self._tokenizer is not None:
            return self._tokenizer

        from transformers import AutoTokenizer

        # Try extracting from the loaded policy model
        for attr_path in [
            "model.vlm_with_expert.processor.tokenizer",  # SmolVLA
            "model.paligemma_with_expert.paligemma.processor.tokenizer",  # pi0.5
        ]:
            obj = policy
            try:
                for part in attr_path.split("."):
                    obj = getattr(obj, part)
                if obj is not None and hasattr(obj, "encode"):
                    self._tokenizer = obj
                    logger.info("Using policy's built-in tokenizer via %s", attr_path)
                    return self._tokenizer
            except AttributeError:
                continue

        # An unrelated tokenizer can silently change policy behavior. Only use
        # an explicitly selected, immutable fallback reviewed for this checkpoint.
        import re

        name = self._tokenizer_config.get("model_id", "")
        revision = self._tokenizer_config.get("revision", "")
        if not name or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise RuntimeError(
                "Policy has no native tokenizer. Configure a compatible tokenizer "
                "with model_id and an immutable 40-character revision."
            )
        self._tokenizer = AutoTokenizer.from_pretrained(
            name, revision=revision, trust_remote_code=False
        )
        return self._tokenizer

    def _is_pi05(self, config) -> bool:
        return getattr(config, "type", "") == "pi05"

    def _decode_image(self, image_base64: str) -> Image.Image:
        """Decode base64 image to PIL."""
        data = base64.b64decode(image_base64)
        return Image.open(io.BytesIO(data)).convert("RGB")

    def _prepare_batch(
        self,
        policy: PreTrainedPolicy,
        image: Image.Image,
        task: str,
        proprioception: list[float] | None,
    ) -> dict[str, torch.Tensor]:
        """Build the observation batch dict with correct feature keys for the policy."""
        device = next(policy.parameters()).device
        config = policy.config

        if self._is_pi05(config):
            return self._prepare_batch_pi05(policy, image, task, proprioception, device, config)
        return self._prepare_batch_default(policy, image, task, proprioception, device, config)

    def _prepare_batch_default(
        self, policy, image, task, proprioception, device, config
    ) -> dict[str, torch.Tensor]:
        """Default batch preparation (SmolVLA and similar)."""
        import torchvision.transforms.functional as F

        batch: dict[str, torch.Tensor] = {}

        img_tensor = F.to_tensor(image).unsqueeze(0).to(device)
        image_keys = list(config.image_features.keys())
        if image_keys:
            batch[image_keys[0]] = img_tensor

        # State / proprioception
        state_dim = config.input_features.get("observation.state")
        if state_dim is not None:
            expected_dim = state_dim.shape[0]
            if proprioception is not None:
                state = proprioception[:expected_dim]
                state = state + [0.0] * (expected_dim - len(state))
            else:
                state = [0.0] * expected_dim
            batch["observation.state"] = torch.tensor([state], dtype=torch.float32, device=device)

        tokenizer = self._get_tokenizer(policy)
        tokens = tokenizer(
            task,
            return_tensors="pt",
            padding="max_length",
            max_length=64,
            truncation=True,
        )
        batch["observation.language.tokens"] = tokens["input_ids"].to(device)
        batch["observation.language.attention_mask"] = tokens["attention_mask"].bool().to(device)

        return batch

    def _prepare_batch_pi05(
        self, policy, image, task, proprioception, device, config
    ) -> dict[str, torch.Tensor]:
        """pi0.5 batch preparation — discretized state in prompt, multi-camera support."""
        import torchvision.transforms.functional as F

        batch: dict[str, torch.Tensor] = {}

        # --- Images ---
        # pi0.5 expects images at model's image_resolution (224x224)
        target_h, target_w = getattr(config, "image_resolution", (224, 224))
        img_resized = image.resize((target_w, target_h), Image.LANCZOS)
        img_tensor = F.to_tensor(img_resized).unsqueeze(0).to(device)  # (1, 3, H, W)

        image_keys = list(config.image_features.keys())
        for key in image_keys:
            if "empty_camera" in key:
                # Empty camera slots: fill with -1 (SigLIP padding value)
                h, w = config.input_features[key].shape[1], config.input_features[key].shape[2]
                batch[key] = torch.full((1, 3, h, w), -1.0, device=device)
            else:
                # Use our single image for all real camera slots
                batch[key] = img_tensor

        # --- State (discretized into 256 bins, embedded in prompt) ---
        max_state_dim = getattr(config, "max_state_dim", 32)
        state_feature = config.input_features.get("observation.state")
        expected_dim = state_feature.shape[0] if state_feature else 8

        if proprioception is not None:
            state_vals = proprioception[:expected_dim]
            state_vals = state_vals + [0.0] * (expected_dim - len(state_vals))
        else:
            state_vals = [0.0] * expected_dim

        # Pad to max_state_dim
        padded_state = state_vals + [0.0] * (max_state_dim - len(state_vals))

        # For pi0.5, state also goes as a tensor in the batch (for _preprocess flow)
        batch["observation.state"] = torch.tensor(
            [padded_state], dtype=torch.float32, device=device
        )

        # Discretize state to 256 bins (matching openpi's PaligemmaTokenizer.tokenize)
        state_arr = np.array(padded_state, dtype=np.float64)
        # Clamp to [-1, 1] — state should ideally be normalized, but clamp for safety
        state_arr = np.clip(state_arr, -1.0, 1.0)
        bins = np.linspace(-1, 1, 256 + 1)[:-1]
        discretized = np.digitize(state_arr, bins) - 1
        state_str = " ".join(map(str, discretized))

        # Build the full prompt: "Task: ..., State: ...;\nAction: "
        cleaned_task = task.strip().replace("_", " ").replace("\n", " ")
        full_prompt = f"Task: {cleaned_task}, State: {state_str};\nAction: "

        # --- Tokenize ---
        tokenizer = self._get_tokenizer(policy)
        max_length = getattr(config, "tokenizer_max_length", 200)
        tokens = tokenizer(
            full_prompt,
            return_tensors="pt",
            padding="max_length",
            max_length=max_length,
            truncation=True,
        )
        batch["observation.language.tokens"] = tokens["input_ids"].to(device)
        batch["observation.language.attention_mask"] = tokens["attention_mask"].bool().to(device)

        return batch

    def _run_inference(
        self,
        image_base64: str,
        task: str,
        proprioception: list[float] | None,
        embodiment: RobotEmbodiment | None,
    ) -> ActionPrediction:
        """Synchronous inference — runs in executor thread."""
        from rove.adapters.normalizer import slice_to_target_dim

        policy = self._load_model()
        image = self._decode_image(image_base64)

        batch = self._prepare_batch(policy, image, task, proprioception)

        n_steps = getattr(policy.config, "n_action_steps", self._chunk_size)
        is_pi05 = self._is_pi05(policy.config)

        all_actions: list[list[float]] = []
        with torch.inference_mode():
            for _ in range(n_steps):
                raw_action = policy.select_action(batch)
                if raw_action.dim() > 1:
                    raw_action = raw_action.squeeze(0)
                all_actions.append(raw_action.cpu().tolist())

        raw_dim = len(all_actions[0]) if all_actions else 0

        # Slice pi0.5's padded 32-dim output to target robot's action dim
        target_dim = raw_dim
        if is_pi05 and embodiment is not None:
            target_dim = embodiment.action_dim
            all_actions = slice_to_target_dim(all_actions, target_dim)

        return ActionPrediction(
            actions=all_actions,
            action_type="trajectory",
            num_steps=len(all_actions),
            confidence=0.8,
            raw_response=f"LeRobot policy {self._hf_repo} on {self._device}",
            declared_action_dim=target_dim,
            raw_action_dim=raw_dim,
            action_space=embodiment.action_space if embodiment else ActionSpace.EEF_DELTA,
            gripper_index=embodiment.gripper_index if embodiment else None,
        )

    async def predict_action(
        self,
        image_base64: str,
        task: str,
        proprioception: list[float] | None = None,
        plan: TaskPlan | None = None,
        embodiment: RobotEmbodiment | None = None,
    ) -> ActionPrediction:
        """Predict action trajectory from current observation."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            partial(self._run_inference, image_base64, task, proprioception, embodiment),
        )

    async def health_check(self) -> bool:
        """Check if lerobot is available and model can be loaded."""
        if not HAS_LEROBOT:
            return False
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._load_model)
            return True
        except Exception:
            logger.exception("LeRobot health check failed for %s", self._hf_repo)
            return False
