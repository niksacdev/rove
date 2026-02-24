"""LeRobot VLA adapter — SmolVLA action prediction via HuggingFace LeRobot."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from functools import partial
from typing import Any

from rove.models import ActionPrediction, TaskPlan

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
    """VLA adapter for SmolVLA (and future LeRobot-compatible policies).

    Loads a pretrained policy from HuggingFace and runs local inference.
    Action output is a 7-DOF end-effector delta trajectory via flow matching.
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
        self._policy: PreTrainedPolicy | None = None

    def _load_model(self) -> PreTrainedPolicy:
        """Load and cache the pretrained policy (called in executor)."""
        if self._policy is not None:
            return self._policy

        logger.info("Loading LeRobot policy %s on %s", self._hf_repo, self._device)

        # Resolve the concrete policy class (e.g. SmolVLAPolicy) from the
        # config stored in the HF repo, then call from_pretrained on it.
        config = PreTrainedConfig.from_pretrained(self._hf_repo)
        policy_cls = get_policy_class(config.type)

        try:
            policy = policy_cls.from_pretrained(self._hf_repo)
            policy = policy.to(self._device)
        except (RuntimeError, AssertionError):
            logger.warning("Device %s failed, falling back to cpu", self._device)
            policy = policy_cls.from_pretrained(self._hf_repo)
            policy = policy.to("cpu")

        # Set to inference mode (no gradient tracking)
        policy.train(False)
        self._policy = policy
        return policy

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

        batch: dict[str, torch.Tensor] = {}

        # Image: convert PIL → (1, 3, H, W) float tensor in [0, 1]
        import torchvision.transforms.functional as F

        img_tensor = F.to_tensor(image)  # (3, H, W), [0, 1]
        img_tensor = img_tensor.unsqueeze(0).to(device)  # (1, 3, H, W)

        # Assign to the first image feature key; mask missing cameras
        image_keys = list(config.image_features.keys())
        if image_keys:
            batch[image_keys[0]] = img_tensor

        # State / proprioception
        state_dim = config.input_features.get("observation.state")
        if state_dim is not None:
            expected_dim = state_dim.shape[0]
            if proprioception is not None:
                state = proprioception[:expected_dim]
                # Pad if shorter
                state = state + [0.0] * (expected_dim - len(state))
            else:
                state = [0.0] * expected_dim
            batch["observation.state"] = torch.tensor([state], dtype=torch.float32, device=device)

        # Language tokens: tokenize task string via the VLM's processor
        tokenizer = policy.model.vlm_with_expert.processor.tokenizer
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

    def _run_inference(
        self,
        image_base64: str,
        task: str,
        proprioception: list[float] | None,
    ) -> ActionPrediction:
        """Synchronous inference — runs in executor thread."""
        policy = self._load_model()
        image = self._decode_image(image_base64)

        batch = self._prepare_batch(policy, image, task, proprioception)

        with torch.inference_mode():
            raw_action = policy.select_action(batch)

        # raw_action: Tensor of shape (chunk_size, action_dim) or (action_dim,)
        if raw_action.dim() == 1:
            raw_action = raw_action.unsqueeze(0)

        actions = raw_action.cpu().tolist()

        return ActionPrediction(
            actions=actions,
            action_type="trajectory",
            num_steps=len(actions),
            confidence=0.8,
            raw_response=f"LeRobot policy {self._hf_repo} on {self._device}",
        )

    async def predict_action(
        self,
        image_base64: str,
        task: str,
        proprioception: list[float] | None = None,
        plan: TaskPlan | None = None,
    ) -> ActionPrediction:
        """Predict action trajectory from current observation."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            partial(self._run_inference, image_base64, task, proprioception),
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
