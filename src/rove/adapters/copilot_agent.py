"""Configured Copilot strategy adapter; customer adapters remain directly callable."""

from __future__ import annotations

import os

from rove.models import SceneAnalysis, TaskPlan, VerificationResult
from rove.orchestrator.recording import record_event, stage_span
from rove.runtime.copilot import CopilotRuntime, CopilotRuntimeConfig


class CopilotAgentAdapter:
    def __init__(self, model_id: str, config: dict | None = None, **_kwargs):
        self.model_id = model_id
        self.config = dict(config or {})
        self.display_name = self.config.get("display_name", model_id)

    def _runtime(self, stage: str) -> CopilotRuntime:
        # Verification has a fresh grader scope. A candidate cannot choose its role.
        schemas = {"perceive": SceneAnalysis, "plan": TaskPlan, "verify": VerificationResult}
        if stage not in schemas:
            raise ValueError(
                "Copilot act requires an explicit robot tool contract; use a direct adapter"
            )
        import json

        provider = dict(self.config.get("provider", {}))
        if key_env := provider.pop("api_key_env", None):
            key = os.environ.get(key_env)
            if not key:
                raise ValueError("Configured Copilot provider credential is unavailable")
            provider["api_key"] = key
        settings = CopilotRuntimeConfig(
            model=self.config.get("model", self.model_id),
            provider=provider,
            role="grader" if stage == "verify" else "candidate",
            timeout_seconds=self.config.get("timeout_seconds", 60),
            cli_path=self.config.get("cli_path", "copilot"),
            telemetry=self.config.get("telemetry"),
            native_traces=self.config.get("native_traces", True),
            system_message=(
                "Evaluate only the supplied robotics evidence. Return a JSON object matching "
                "this schema. A proposed action or model statement is not observed robot success. "
                + json.dumps(schemas[stage].model_json_schema())
            ),
        )
        return CopilotRuntime(settings, event_sink=record_event)

    async def run_stage(self, stage, image_base64, task, context=None):
        if stage != "verify":
            # Candidate context is an explicit interface, never the full grading record.
            supplied = context or {}
            context = {
                key: supplied[key]
                for key in ("scene", "plan", "proprioception", "embodiment")
                if key in supplied
            }
            if metadata := supplied.get("task_metadata"):
                context["task_metadata"] = {
                    key: metadata[key]
                    for key in ("constraints", "correction", "eval_category")
                    if key in metadata
                }
        with stage_span(
            stage,
            self.model_id,
            {
                "memory": "fresh_per_stage",
                "reset": "fresh_process_session",
                "telemetry_coverage": "sdk_events_and_native_spans"
                if self.config.get("native_traces", True)
                else "sdk_events",
                "attribution": "rove_implemented",
            },
        ):
            return await self._runtime(stage).run_stage(stage, image_base64, task, context)

    async def health_check(self) -> bool:
        try:
            return await self._runtime("plan").health_check()
        except ValueError:
            return False
