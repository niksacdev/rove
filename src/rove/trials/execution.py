"""Direct recorded trial execution for command-line and library callers."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from rove.adapters.registry import AdapterRegistry
from rove.benchmarks.runner import local_evaluator_versions, runtime_fingerprint
from rove.datasets.robot_assets import robot_asset_path
from rove.datasets.service import DatasetService
from rove.models import ExampleData
from rove.models.config import get_strategies, load_config
from rove.orchestrator.run_manager import RunManager
from rove.trials.evidence import verified_asset
from rove.trials.store import TrialStore


async def run_trials(
    *,
    root: Path,
    config_path: Path,
    strategy_ids: list[str],
    case_revision_id=None,
    task=None,
    image_bytes=None,
    urdf_bytes=None,
    candidate_context=None,
    seed=None,
    on_event=None,
):
    """Run the same observation against selected strategies, recording before execution."""
    config = load_config(config_path)
    if not strategy_ids or len(strategy_ids) != len(set(strategy_ids)):
        raise ValueError("Choose at least one strategy without duplicates")
    if set(strategy_ids) - config.strategies.keys():
        raise ValueError("Selected strategy is unavailable in this configuration")
    if seed is not None and not 0 <= seed <= 2**32 - 1:
        raise ValueError("Seed must be between 0 and 2^32-1")
    service = DatasetService(root)
    case = service.get_case_revision(case_revision_id) if case_revision_id else None
    if case:
        if candidate_context is not None:
            raise ValueError(
                "Saved cases already define candidate context; create a revision to change it"
            )
        _, original = verified_asset(service.trials, case["image_asset"]["sha256"])
        if (task is not None and task != case["task"]) or (
            image_bytes is not None and image_bytes != original
        ):
            raise ValueError(
                "Task or image differs from the saved case; create a revision or run without --case"
            )
        task, image_bytes, candidate_context = case["task"], original, case["candidate_context"]
    if not task or not image_bytes:
        raise ValueError("Provide --case or both --task and --image")
    context = candidate_context or {}
    service.validate_case_payload(
        {"name": "Trial input", "task": task, "candidate_context": context}
    )
    media = "image/png" if image_bytes.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg"
    image = service.trials.save_asset(image_bytes, media)
    service._image(image["sha256"])
    robot = context.get("robot_asset")
    if urdf_bytes is not None:
        if not urdf_bytes or len(urdf_bytes) > 4 * 1024 * 1024:
            raise ValueError("Robot description must contain 1 byte to 4 MiB")
        robot = service.trials.save_asset(urdf_bytes, "application/xml")
    urdf_path = str(robot_asset_path(service.trials, robot)) if robot else None
    task_snapshot = {
        "task": task,
        "image_asset": image,
        "example": {"ground_truth": None, "extras": context},
    }
    if case:
        task_snapshot.update(
            case_id=case["case_id"], case_revision_id=case["id"], case_sha256=case["sha256"]
        )
    runtime = runtime_fingerprint()
    ids = {}
    strategies = get_strategies()
    store = TrialStore(root)
    try:
        for sid in strategy_ids:
            definition = config.strategies[sid]
            frozen = {
                "defaults": config.defaults.model_dump(mode="json"),
                "runtime": runtime,
                "seed_support": "requested_only" if seed is not None else "not_requested",
                "strategies": {sid: definition.model_dump(mode="json")},
                "endpoints": {
                    key: config.endpoints[key].model_dump(mode="json")
                    for key in definition.endpoint_refs()
                },
            }
            if robot:
                frozen["robot_asset"] = robot
            frozen["local_evaluators"] = local_evaluator_versions(frozen)
            ids[sid] = store.begin(
                source="quick",
                task=task_snapshot,
                strategy={"id": sid, **definition.model_dump(mode="json")},
                config=frozen,
                seed=seed,
            )

        async def event(sid, kind, data):
            if on_event:
                await on_event(sid, kind, data)

        results = await RunManager(
            AdapterRegistry(),
            max_concurrent=config.defaults.max_concurrent_combinations,
            urdf_path=urdf_path,
        ).run_strategies(
            [strategies[sid] for sid in strategy_ids],
            task,
            base64.b64encode(image_bytes).decode(),
            event,
            example=ExampleData(extras=context),
            trial_contexts={sid: (store, identity) for sid, identity in ids.items()},
        )
        for result in results:
            failed = result.get("error") or any(
                stage.get("status") == "error" for stage in result.get("stages", [])
            )
            store.finish(
                ids[result["strategy_id"]], status="error" if failed else "completed", result=result
            )
        return {
            "trial_ids": ids,
            "results": results,
            "case_revision_id": case_revision_id,
            "seed_support": "requested_only" if seed is not None else "not_requested",
        }
    except BaseException as error:
        for identity in ids.values():
            if store.get(identity)["status"] == "running":
                store.finish(
                    identity,
                    status="cancelled" if isinstance(error, asyncio.CancelledError) else "error",
                    error="Trial execution interrupted before completion",
                )
        raise
