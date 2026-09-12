"""Explicit trajectory fixture for testing the real act/sim/verify path."""

from rove.models import ActionPrediction, ActionSpace, VLACapabilities


class SyntheticActionAdapter:
    def __init__(self, model_id="synthetic-actions", config=None):
        self.model_id = model_id
        self.display_name = "Synthetic trajectory fixture"
        self.actions = (config or {}).get("actions", [[0.5], [0.5]])

    @property
    def capabilities(self):
        return VLACapabilities(native_action_dim=1, native_action_space=ActionSpace.EEF_DELTA)

    async def predict_action(
        self, image_base64, task, proprioception=None, plan=None, embodiment=None
    ):
        return ActionPrediction(
            actions=self.actions,
            num_steps=len(self.actions),
            declared_action_dim=1,
            action_space=ActionSpace.EEF_DELTA,
        )

    async def health_check(self):
        return True
