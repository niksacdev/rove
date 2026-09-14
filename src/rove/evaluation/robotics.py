"""Compatibility boundary for ROVE's existing robotics pipeline."""

from __future__ import annotations

import base64
from hashlib import sha256

from rove.benchmarks.models import CampaignSpec, fingerprint
from rove.models.config import RoveConfig, StrategyConfig
from rove.models.verification import AGGREGATION_VERSION


def prepare_robotics(spec: CampaignSpec, config: RoveConfig) -> dict:
    from rove.benchmarks.runner import local_evaluator_versions, runtime_fingerprint

    if spec.execution != "robotics":
        raise ValueError("Use the evaluation configuration for a non-robotics executor")
    selected = {}
    endpoints = {}
    for sid in spec.strategies:
        if sid not in config.strategies:
            raise ValueError(f"Unknown strategy: {sid}")
        strategy = config.strategies[sid].model_dump(mode="json")
        selected[sid] = strategy
        for ref in config.strategies[sid].endpoint_refs():
            endpoints[ref] = config.endpoints[ref].model_dump(mode="json")
    frozen = {
        "version": config.version,
        "endpoints": endpoints,
        "strategies": selected,
        "defaults": config.defaults.model_dump(mode="json"),
    }
    runtime = runtime_fingerprint()
    evaluators = {
        sid: {
            "endpoint": endpoints[
                StrategyConfig.model_validate(s).stage_options("verify").endpoint
            ],
            "verify_settings": StrategyConfig.model_validate(s)
            .stage_options("verify")
            .model_dump(mode="json"),
            "checks": {
                c.endpoint: endpoints[c.endpoint]
                for c in StrategyConfig.model_validate(s).stage_options("verify").checks
            },
            "aggregation_version": AGGREGATION_VERSION,
            "local_evaluators": local_evaluator_versions(frozen),
            "verify_mode": s["verify_mode"],
            "pipeline_mode": s["pipeline_mode"],
            "compute_dynamics": s["compute_dynamics"],
            "sim": endpoints.get(s["sim"]),
        }
        for sid, s in selected.items()
    }
    comparisons = {
        sid: fingerprint(
            {
                "suite_version": spec.suite_version,
                "tasks": [t.model_dump(mode="json") for t in spec.tasks],
                "seeds": spec.seeds,
                "timeout_s": spec.timeout_s,
                "grading": spec.grading,
                "evaluator": evaluators[sid],
                "runtime": runtime,
            }
        )
        for sid in selected
    }
    synthetic = any((e.get("adapter") or "").startswith("mock_") for e in endpoints.values())
    return {
        "spec": spec.model_dump(mode="json"),
        "config": frozen,
        "runtime": runtime,
        "local_evaluators": local_evaluator_versions(frozen),
        "config_hash": fingerprint(frozen),
        "comparison_keys": comparisons,
        "strategy_definitions": selected,
        "evidence_kind": "synthetic_or_mixed" if synthetic else "configured_verifier",
        "model_versions": {
            key: {
                "adapter": e.get("adapter"),
                "provider": e.get("provider"),
                "declared_revision": e.get("config", {}).get("revision"),
                "identity_hash": fingerprint(e),
            }
            for key, e in endpoints.items()
        },
        "grading_note": "Scores reflect the configured verify stage. They do not establish observed robot task success. Model revisions are declared, not independently resolved.",
    }


class RoboticsExecutor:
    def validate(self, campaign):
        from rove.benchmarks.runner import validate_local_evaluators

        validate_local_evaluators(campaign)

    def prepare_trial(self, campaign, task, strategy, store):
        task_snapshot = task.model_dump(mode="json")
        if task.image_asset:
            image_bytes = store.asset_path(task.image_asset).read_bytes()
            if sha256(image_bytes).hexdigest() != task.image_asset:
                raise ValueError("Case image integrity changed; repair the asset before execution")
        else:
            image_bytes = base64.b64decode(task.image_base64, validate=True)
        media = (
            "image/png"
            if image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
            else "image/jpeg"
            if image_bytes.startswith(b"\xff\xd8\xff")
            else "application/octet-stream"
        )
        task_snapshot["image_asset"] = store.save_asset(image_bytes, media)
        refs = StrategyConfig.model_validate(
            campaign["config"]["strategies"][strategy]
        ).endpoint_refs()
        system_config = {
            **campaign["config"],
            "strategies": {strategy: campaign["config"]["strategies"][strategy]},
            "endpoints": {k: v for k, v in campaign["config"]["endpoints"].items() if k in refs},
            "local_evaluators": {
                k: v for k, v in campaign["local_evaluators"].items() if k in refs
            },
        }
        worker_task = task.model_dump(mode="json")
        if task.image_asset:
            worker_task["image_base64"] = base64.b64encode(image_bytes).decode()
        return task_snapshot, system_config, worker_task
