"""Repeated trials around the existing configured pipeline, with process isolation."""

from __future__ import annotations

import asyncio
import base64
import importlib.metadata
import json
import os
import platform
import signal
import sys
import tempfile
import time
from contextlib import contextmanager, suppress
from pathlib import Path

from rove.benchmarks.models import CampaignSpec, fingerprint
from rove.benchmarks.store import CampaignStore, now
from rove.models.config import RoveConfig, StrategyConfig
from rove.models.verification import AGGREGATION_VERSION


def runtime_fingerprint() -> dict:
    package = Path(__file__).resolve().parents[1]
    resources = {}
    for folder in (package, package.parents[1] / "data" / "urdf"):
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path.suffix in {".py", ".txt", ".urdf", ".xml", ".stl", ".obj"}:
                resources[str(path.relative_to(package.parents[1]))] = fingerprint(
                    path.read_bytes().hex()
                )
    versions = {}
    for name in (
        "rove-eval",
        "pydantic",
        "mujoco",
        "torch",
        "transformers",
        "lerobot",
        "openai",
        "azure-ai-projects",
    ):
        with suppress(importlib.metadata.PackageNotFoundError):
            versions[name] = importlib.metadata.version(name)
    return {
        "source_hash": fingerprint(resources),
        "packages": versions,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "architecture": platform.machine(),
    }


def local_evaluator_versions(config: dict) -> dict:
    from rove.adapters.local_verifier import evaluator_identity

    return {
        key: evaluator_identity(ep["config"])
        for key, ep in config["endpoints"].items()
        if ep.get("adapter") == "local_verifier"
    }


def validate_local_evaluators(campaign: dict) -> None:
    if campaign.get("local_evaluators", {}) != local_evaluator_versions(campaign["config"]):
        raise ValueError("Local evaluator changed; create a new campaign")


def prepare(spec: CampaignSpec, config: RoveConfig) -> dict:
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


@contextmanager
def campaign_lock(store: CampaignStore, campaign_id: str):
    if os.name != "posix":
        raise ValueError("Benchmark workers currently require macOS or Linux")
    import fcntl

    # Validate before constructing a path from an API/CLI identifier.
    store.get(campaign_id)
    if len(campaign_id) != 32 or any(c not in "0123456789abcdef" for c in campaign_id):
        raise ValueError("Invalid campaign ID")
    with (store.root / f"{campaign_id}.lock").open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Campaign is already running") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


async def run_attempt(request: dict, timeout_s: float) -> dict:
    with tempfile.TemporaryDirectory(prefix="rove-trial-") as directory:
        input_path = Path(directory) / "input.json"
        output_path = Path(directory) / "output.json"
        input_path.write_text(json.dumps(request, allow_nan=False))
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "rove.benchmarks.worker",
            str(input_path),
            str(output_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_s)
            if process.returncode or not output_path.exists():
                return {"outcome": "unknown", "execution": "error", "reason": "Trial worker failed"}
            if output_path.stat().st_size > 32_000_000:
                return {
                    "outcome": "unknown",
                    "execution": "error",
                    "reason": "Trial evidence exceeded 32 MB",
                }
            result = json.loads(output_path.read_text())
            if result.get("outcome") not in {"pass", "fail", "unknown"}:
                raise ValueError("Invalid worker outcome")
            return result
        except TimeoutError:
            return {"outcome": "unknown", "execution": "timeout"}
        finally:
            # Kill the entire worker process group, including on cancellation.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()


def trial_status(execution: str) -> str:
    if execution in {"completed", "invalid_verdict", "unresolved_evidence"}:
        return "completed"
    return execution if execution in {"timeout", "cancelled", "interrupted"} else "error"


async def run_campaign(store: CampaignStore, campaign_id: str, progress=None) -> None:
    from rove.trials.store import TrialStore

    trial_store = TrialStore(store.root)
    with campaign_lock(store, campaign_id):
        campaign = store.get(campaign_id)
        # Reconcile two journals after any crash boundary, while holding ownership.
        indexed = {r["trial_id"]: r for r in store.trials(campaign_id) if r.get("trial_id")}
        for shared in trial_store.for_campaign(campaign_id):
            if shared["status"] != "running":
                continue
            prior = indexed.get(shared["id"])
            if prior and prior["execution"] != "running":
                state = prior["execution"]
                trial_store.finish(
                    shared["id"],
                    status=trial_status(state),
                    result=prior,
                )
            else:
                trial_store.finish(
                    shared["id"],
                    status="interrupted",
                    error="Campaign owner interrupted before committing completion",
                )
        validate_local_evaluators(campaign)
        if campaign["runtime"] != runtime_fingerprint():
            raise ValueError("Runtime changed; create a new campaign instead of mixing revisions")
        if campaign["status"] == "completed":
            return
        spec = CampaignSpec.model_validate(campaign["spec"])
        # A worker may have reached the provider before a crash: never silently replay it.
        existing = store.trials(campaign_id)
        for result in existing:
            if result["execution"] == "running":
                result.update(execution="interrupted", outcome="unknown")
                store.finish_trial(campaign_id, result)
                if (
                    result.get("trial_id")
                    and trial_store.get(result["trial_id"])["status"] == "running"
                ):
                    trial_store.finish(result["trial_id"], status="interrupted", result=result)
        done = {(r["task_id"], r["strategy_id"], r["seed"]) for r in existing}
        store.status(campaign_id, "running")
        try:
            # Round-robin by seed reduces ordering effects across configurations.
            # One worker at a time bounds resource use and isolates mutable model state.
            for seed in spec.seeds:
                for task in spec.tasks:
                    for strategy in spec.strategies:
                        if (task.id, strategy, seed) in done:
                            continue
                        validate_local_evaluators(campaign)
                        if campaign["runtime"] != runtime_fingerprint():
                            raise ValueError(
                                "Runtime changed during campaign; start a new campaign"
                            )
                        if store.get(campaign_id)["status"] == "cancelling":
                            store.status(campaign_id, "cancelled")
                            return
                        record = {
                            "task_id": task.id,
                            "strategy_id": strategy,
                            "seed": seed,
                            "started_at": now(),
                            "outcome": "unknown",
                            "execution": "running",
                        }
                        task_snapshot = task.model_dump(mode="json")
                        if task.image_asset:
                            from hashlib import sha256

                            asset_bytes = trial_store.asset_path(task.image_asset).read_bytes()
                            if sha256(asset_bytes).hexdigest() != task.image_asset:
                                raise ValueError(
                                    "Case image integrity changed; repair the asset before execution"
                                )
                        image_bytes = (
                            asset_bytes
                            if task.image_asset
                            else base64.b64decode(task.image_base64, validate=True)
                        )
                        media_type = (
                            "image/png"
                            if image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
                            else "image/jpeg"
                            if image_bytes.startswith(b"\xff\xd8\xff")
                            else "application/octet-stream"
                        )
                        task_snapshot["image_asset"] = trial_store.save_asset(
                            image_bytes, media_type
                        )
                        refs = StrategyConfig.model_validate(
                            campaign["config"]["strategies"][strategy]
                        ).endpoint_refs()
                        system_config = {
                            **campaign["config"],
                            "strategies": {strategy: campaign["config"]["strategies"][strategy]},
                            "endpoints": {
                                k: v
                                for k, v in campaign["config"]["endpoints"].items()
                                if k in refs
                            },
                        }
                        trial_id = trial_store.begin(
                            source="campaign",
                            campaign_id=campaign_id,
                            seed=seed,
                            task=task_snapshot,
                            strategy={"id": strategy, **campaign["config"]["strategies"][strategy]},
                            config={
                                **system_config,
                                "runtime": campaign["runtime"],
                                "evaluation_contract": {
                                    "grading": spec.grading,
                                    "timeout_s": spec.timeout_s,
                                    "suite_version": spec.suite_version,
                                    "contract_id": campaign.get("contract_id"),
                                    "contract": campaign.get("contract"),
                                    "dataset_revision_id": campaign.get("dataset_revision_id"),
                                },
                                "local_evaluators": {
                                    k: v
                                    for k, v in campaign["local_evaluators"].items()
                                    if k in refs
                                },
                            },
                        )
                        record["trial_id"] = trial_id
                        store.save_trial(campaign_id, record)
                        request = {
                            "config": campaign["config"],
                            "task": task.model_dump(mode="json"),
                            "strategy_id": strategy,
                            "seed": seed,
                            "trial_id": trial_id,
                            "trial_root": str(store.root.resolve()),
                        }
                        # Large media stays referenced in frozen campaign records; hydrate only
                        # the bounded, private worker request, never the public report payload.
                        if task.image_asset:
                            request["task"] = {
                                **request["task"],
                                "image_base64": base64.b64encode(image_bytes).decode(),
                            }
                        started = time.monotonic()
                        try:
                            result = await run_attempt(request, spec.timeout_s)
                        except asyncio.CancelledError:
                            record.update(execution="cancelled", finished_at=now())
                            store.finish_trial(campaign_id, record)
                            trial_store.finish(trial_id, status="cancelled", result=record)
                            raise
                        except Exception:
                            result = {
                                "outcome": "unknown",
                                "execution": "error",
                                "reason": "Worker output could not be read",
                            }
                        record.update(
                            result,
                            finished_at=now(),
                            attempt_wall_ms=(time.monotonic() - started) * 1000,
                        )
                        if campaign["runtime"] != runtime_fingerprint():
                            record.update(outcome="unknown", execution="runtime_changed")
                        try:
                            validate_local_evaluators(campaign)
                        except ValueError:
                            record.update(outcome="unknown", execution="evaluator_changed")
                        store.finish_trial(campaign_id, record)
                        execution = record["execution"]
                        trial_store.finish(
                            trial_id,
                            status=trial_status(execution),
                            result=record,
                        )
                        if progress:
                            await progress(record)
            store.status(campaign_id, "completed")
        except asyncio.CancelledError:
            store.status(campaign_id, "cancelled")
            raise
        except BaseException:
            store.status(campaign_id, "interrupted")
            raise
