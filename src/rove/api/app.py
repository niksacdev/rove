"""FastAPI application — REST API + SSE streaming + static file serving."""

from __future__ import annotations

import asyncio
import base64
import fcntl
import hashlib
import json
import logging
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import StreamingResponse

from rove.adapters.registry import AdapterRegistry
from rove.api.local_only import LocalOnlyMiddleware
from rove.api.strategy_revisions import create_strategy_revision_router
from rove.api.trials import create_trial_router
from rove.benchmarks.api import create_router
from rove.models import EvaluationProvenance, ExampleData, PipelineStage, StageStatus
from rove.models.config import get_strategies, load_config
from rove.orchestrator.failure_attribution import attribute_failure
from rove.orchestrator.insights import compute_run_insights
from rove.orchestrator.pipeline import EvaluationPipeline
from rove.orchestrator.recording import event_sink
from rove.orchestrator.run_manager import RunManager
from rove.trials.store import TrialStore

logger = logging.getLogger(__name__)

_trial_root = Path(__file__).resolve().parents[3] / ".rove" / "benchmarks"


def trial_store() -> TrialStore:
    return TrialStore(_trial_root)


@asynccontextmanager
async def lifespan(_app):
    db = trial_store()
    # Recovery is safe only with one UI owner; campaign CLI workers have separate ownership.
    with (_trial_root / "ui.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        db.recover_interrupted(source="quick")
        for path in (_legacy_history_path, _history_path):
            if path.exists():
                try:
                    db.import_jsonl(path)
                except (ValueError, OSError):
                    logger.exception(
                        "Legacy history import failed; original file remains unchanged"
                    )
        try:
            from rove.datasets.library import sync_library

            result = await asyncio.to_thread(sync_library, _trial_root, _data_dir)
            if result["summary"]["unavailable"]:
                logger.warning(
                    "Some sample cases could not be imported; inspect the sample library"
                )
        except (ValueError, OSError):
            logger.exception("Sample library import failed; original files remain unchanged")
        try:
            yield
        finally:
            tasks = list(_background_tasks)
            for background in tasks:
                background.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            fcntl.flock(lock, fcntl.LOCK_UN)


app = FastAPI(
    title="ROVE",
    version="0.1.0",
    description="Robot Observation & Vision Evaluation",
    lifespan=lifespan,
)

app.add_middleware(LocalOnlyMiddleware)
app.include_router(create_router(Path(__file__).resolve().parents[3] / ".rove" / "benchmarks"))
app.include_router(create_trial_router(trial_store))
app.include_router(create_strategy_revision_router())

# In-process streaming state; trials and campaign results persist in SQLite.
_data_dir = Path(__file__).parent.parent.parent.parent / "data"
_output_dir = _data_dir / "output"
_legacy_history_path = _output_dir / "history.jsonl"
_history_path = _trial_root / "quick-history.jsonl"
registry = AdapterRegistry()
_evaluations: dict[str, dict[str, Any]] = {}
_eval_queues: dict[str, asyncio.Queue] = {}
_background_tasks: set[asyncio.Task] = set()  # prevent GC of background tasks


def _persist_evaluation(eval_data: dict[str, Any]) -> None:
    """Append a completed evaluation record to history.jsonl."""
    try:
        _history_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        record = {
            "eval_id": eval_data.get("eval_id"),
            "task": eval_data.get("task", ""),
            "timestamp": datetime.now(UTC).isoformat(),
            "status": eval_data.get("status", "completed"),
            "strategy_ids": eval_data.get("strategy_ids", []),
            "results": eval_data.get("results", {}),
            "trial_ids": eval_data.get("trial_ids", {}),
        }
        if "provenance" in eval_data:
            record["provenance"] = eval_data["provenance"]
        if "insights" in eval_data:
            record["insights"] = eval_data["insights"]
        with open(_history_path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        logger.warning("Failed to persist evaluation to history.jsonl", exc_info=True)


def _read_history() -> list[dict[str, Any]]:
    """Read all history records from JSONL, most recent first."""
    records = []
    lines = [
        line
        for path in (_legacy_history_path, _history_path)
        if path.exists()
        for line in path.read_text().splitlines()
    ]
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    records.reverse()
    return records


def _load_example_data(example_filename: str) -> ExampleData | None:
    """Load example data from manifest.json into an ExampleData container."""
    manifest_path = _data_dir / "manifest.json"
    if not manifest_path.exists() or not example_filename:
        return None
    try:
        manifest = json.loads(manifest_path.read_text())
        for entry in manifest:
            if entry.get("filename") == example_filename:
                # Ground truth goes in the dedicated field; everything else in extras
                extras = {}
                for k in (
                    "proprioception",
                    "ground_truth_action",
                    "robot",
                    "action_dim",
                    "state_dim",
                    "control_space",
                    "action_space_desc",
                    "initial_joint_positions",
                    "eval_category",
                    "expected_subtasks",
                    "constraints",
                    "correction",
                    "turns",
                    "acceptable_interpretations",
                    "difficulty",
                    "episode",
                ):
                    if k in entry:
                        extras[k] = entry[k]
                return ExampleData(
                    ground_truth=entry.get("eval_qa"),
                    extras=extras,
                )
    except Exception:
        logger.warning(f"Failed to load example data for {example_filename}")
    return None


async def _run_multi_strategy(
    eval_id: str,
    task: str,
    image_base64: str,
    strategy_ids: list[str],
    example: ExampleData | None = None,
    urdf_path: str | None = None,
    seed: int | None = None,
    trial_ids: dict[str, str] | None = None,
):
    """Background task that runs multiple strategies concurrently via RunManager."""
    queue = _eval_queues[eval_id]
    try:
        all_strategies = get_strategies()
        strategies = []
        for sid in strategy_ids:
            if sid not in all_strategies:
                raise ValueError(f"Strategy '{sid}' not found in rove.yaml")
            strategies.append(all_strategies[sid])

        config = load_config()
        run_manager = RunManager(
            registry,
            max_concurrent=config.defaults.max_concurrent_combinations,
            urdf_path=urdf_path,
        )

        async def on_event(strategy_id: str, event_type: str, data: dict) -> None:
            await queue.put({"event": event_type, "data": data})

        results = await run_manager.run_strategies(
            strategies,
            task,
            image_base64,
            on_event,
            example=example,
            trial_contexts={sid: (trial_store(), tid) for sid, tid in (trial_ids or {}).items()},
        )

        for result in results:
            if trial_ids:
                trial_store().finish(
                    trial_ids[result["strategy_id"]],
                    status="error"
                    if result.get("error")
                    or any(s.get("status") == "error" for s in result.get("stages", []))
                    else "completed",
                    result=result,
                )

        provenance = EvaluationProvenance.build(
            image_base64=image_base64,
            strategy_ids=strategy_ids,
            seed=seed,
        )

        insights = compute_run_insights(results)

        complete_data = {
            "eval_id": eval_id,
            "status": "completed",
            "task": task,
            "results": results,
            "strategy_ids": strategy_ids,
            "provenance": provenance.model_dump(),
            "insights": insights,
            "trial_ids": trial_ids or {},
        }
        _evaluations[eval_id] = complete_data
        _persist_evaluation(complete_data)
        await queue.put({"event": "complete", "data": complete_data})

    except asyncio.CancelledError:
        _finish_open_trials(trial_ids, "cancelled")
        raise
    except Exception as e:
        _finish_open_trials(trial_ids, "error", str(e))
        logger.exception(f"Evaluation {eval_id} failed")
        error_result = {"eval_id": eval_id, "status": "error", "error": str(e)}
        _evaluations[eval_id] = error_result
        await queue.put({"event": "error", "data": error_result})
    finally:
        await queue.put(None)  # sentinel to end SSE stream


async def _run_evaluation(
    eval_id: str,
    task: str,
    image_base64: str,
    perceive_model_id: str,
    plan_model_id: str,
    act_model_id: str,
    verify_model_id: str,
    sim_id: str,
    example: ExampleData | None = None,
    seed: int | None = None,
    trial_id: str | None = None,
):
    """Background task that runs a single pipeline and pushes events to the SSE queue."""
    queue = _eval_queues[eval_id]
    token = (
        event_sink.set(lambda event: trial_store().append_event(trial_id, event))
        if trial_id
        else None
    )
    try:
        perceive = registry.get_adapter_for_stage(PipelineStage.PERCEIVE, perceive_model_id)
        plan = registry.get_adapter_for_stage(PipelineStage.PLAN, plan_model_id)
        act = registry.get_adapter_for_stage(PipelineStage.ACT, act_model_id)
        verify = registry.get_adapter_for_stage(PipelineStage.VERIFY, verify_model_id)
        sim = registry.get_sim(sim_id) if sim_id else None

        pipeline = EvaluationPipeline(
            perceive_adapter=perceive,
            plan_adapter=plan,
            act_adapter=act,
            verify_adapter=verify,
            sim=sim,
        )
        stages: list[dict] = []

        async for stage_result in pipeline.run_trial(
            task,
            image_base64,
            eval_id,
            example=example,
        ):
            stage_dict = stage_result.model_dump()
            if trial_id:
                trial_store().append_event(
                    trial_id,
                    {"event_type": "stage", "stage": stage_dict["stage"], "data": stage_dict},
                )
            if stage_result.status in (StageStatus.COMPLETED, StageStatus.ERROR):
                stages.append(stage_dict)
            await queue.put({"event": "stage", "data": stage_dict})

        total_latency = sum(s.get("latency_ms", 0) for s in stages)
        verify_stage = next((s for s in stages if s["stage"] == "verify"), None)
        success = (
            verify_stage["output"].get("success", False)
            if verify_stage and verify_stage.get("output")
            else False
        )
        verdict_valid = bool(
            verify_stage and (verify_stage.get("output") or {}).get("verdict_valid", False)
        ) and not any(s["status"] == "error" for s in stages)
        outcome = ("pass" if success else "fail") if verdict_valid else "unknown"
        failure_stage, failure_category = attribute_failure(stages, success)
        if not verdict_valid:
            failure_category = "unknown"

        provenance = EvaluationProvenance.build(
            image_base64=image_base64,
            strategy_ids=[],
            resolved_models={
                "perceive": perceive_model_id,
                "plan": plan_model_id,
                "act": act_model_id,
                "verify": verify_model_id,
                "sim": sim_id,
            },
            seed=seed,
        )

        # Wrap as single-strategy result for insights computation
        single_result = {
            "strategy_id": eval_id,
            "display_name": "single",
            "success": success,
            "verdict_valid": verdict_valid,
            "outcome": outcome,
            "total_latency_ms": round(total_latency, 1),
            "failure_stage": failure_stage,
            "failure_category": failure_category,
            "stages": stages,
            "models": {
                "perceive": perceive_model_id,
                "plan": plan_model_id,
                "act": act_model_id,
                "verify": verify_model_id,
                "sim": sim_id,
            },
        }
        insights = compute_run_insights([single_result])

        result = {
            "eval_id": eval_id,
            "status": "completed",
            "task": task,
            "models": single_result["models"],
            "stages": stages,
            "success": success,
            "verdict_valid": verdict_valid,
            "outcome": outcome,
            "failure_stage": failure_stage,
            "failure_category": failure_category,
            "total_latency_ms": round(total_latency, 1),
            "provenance": provenance.model_dump(),
            "insights": insights,
            "trial_ids": {"single": trial_id} if trial_id else {},
        }
        if trial_id:
            trial_store().finish(
                trial_id,
                status="error" if any(s["status"] == "error" for s in stages) else "completed",
                result=result,
            )
        _evaluations[eval_id] = result
        _persist_evaluation(result)
        await queue.put({"event": "complete", "data": result})

    except asyncio.CancelledError:
        _finish_open_trials({"single": trial_id} if trial_id else {}, "cancelled")
        raise
    except Exception as e:
        _finish_open_trials({"single": trial_id} if trial_id else {}, "error", str(e))
        logger.exception(f"Evaluation {eval_id} failed")
        error_result = {"eval_id": eval_id, "status": "error", "error": str(e)}
        _evaluations[eval_id] = error_result
        await queue.put({"event": "error", "data": error_result})
    finally:
        if token is not None:
            event_sink.reset(token)
        await queue.put(None)  # sentinel to end SSE stream


def _finish_open_trials(trial_ids, status, error=None):
    db = trial_store()
    for trial_id in (trial_ids or {}).values():
        if db.get(trial_id)["status"] == "running":
            db.finish(trial_id, status=status, error=error)


def _background_finished(background, trial_ids, urdf_path):
    _background_tasks.discard(background)
    if background.cancelled():
        _finish_open_trials(trial_ids, "cancelled")
    elif background.exception() is not None:
        _finish_open_trials(trial_ids, "error", "Evaluation worker failed")
    if urdf_path:
        Path(urdf_path).unlink(missing_ok=True)


@app.get("/api/strategies")
async def list_strategies():
    """Return all strategies with their model assignments."""
    strategies = get_strategies()
    from rove.models.config import active_config_path
    from rove.strategies.revisions import list_revisions

    provenance = {
        row["strategy_id"]: row for row in list_revisions(active_config_path(), load_config())
    }
    return {
        "strategies": [
            {**s.model_dump(), "revision": provenance.get(s.id)} for s in strategies.values()
        ]
    }


@app.post("/api/evaluate", status_code=202)
async def create_evaluation(
    image: UploadFile = File(...),
    task: str = Form(...),
    strategy_ids: str = Form(default=""),
    perceive_model_id: str = Form(default="mock-vlm"),
    plan_model_id: str = Form(default="mock-vlm"),
    act_model_id: str = Form(default="mock-vla"),
    verify_model_id: str = Form(default="mock-vlm"),
    sim_id: str = Form(default="mock-sim"),
    example_filename: str = Form(default=""),
    case_revision_id: str | None = Form(default=None),
    urdf: UploadFile | None = File(default=None),
    seed: int | None = Form(default=None),
):
    eval_id = str(uuid.uuid4())
    image_bytes = await image.read(16 * 1024 * 1024 + 1)
    if len(image_bytes) > 16 * 1024 * 1024:
        raise HTTPException(413, "Image exceeds 16 MiB")
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")

    ids = [s.strip() for s in strategy_ids.split(",") if s.strip()]
    if strategy_ids and (not ids or len(ids) != len(set(ids))):
        raise HTTPException(422, "Select distinct strategies")
    config = load_config()
    if any(sid not in config.strategies for sid in ids):
        raise HTTPException(422, "Unknown strategy")

    case = None
    if case_revision_id:
        from rove.datasets.service import DatasetService

        if example_filename:
            raise HTTPException(422, "Use either a saved case or a legacy sample reference")
        try:
            service = DatasetService(_trial_root)
            case = service.get_case_revision(case_revision_id)
            service.validate_case_image(case_revision_id)
            service.validate_case_payload(
                {
                    key: case[key]
                    for key in (
                        "name",
                        "task",
                        "candidate_context",
                        "conditions",
                        "recorded_evidence",
                        "reference_data",
                    )
                }
            )
        except (KeyError, FileNotFoundError) as error:
            raise HTTPException(404, "The saved case revision is unavailable") from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if (
            task != case["task"]
            or hashlib.sha256(image_bytes).hexdigest() != case["image_asset"]["sha256"]
        ):
            raise HTTPException(
                422,
                "The image or task differs from the saved case. Save a new case revision or run without the case association.",
            )

    # Save URDF to temp file if provided
    urdf_path: str | None = None
    if urdf is not None:
        urdf_bytes = await urdf.read(4 * 1024 * 1024 + 1)
        if len(urdf_bytes) > 4 * 1024 * 1024:
            raise HTTPException(413, "Robot description exceeds 4 MiB")
        if urdf_bytes:
            suffix = Path(urdf.filename or "robot.urdf").suffix or ".urdf"
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=suffix, prefix="rove_urdf_"
            ) as tmp:
                tmp.write(urdf_bytes)
            urdf_path = tmp.name

    # Load example data (ground truth, proprioception, etc.) from manifest
    example = (
        ExampleData(extras=case["candidate_context"])
        if case
        else _load_example_data(example_filename)
        if example_filename
        else None
    )

    # Each selected system gets one durable identity before any model can be called.
    from rove.benchmarks.runner import local_evaluator_versions, runtime_fingerprint

    db = trial_store()
    asset = db.save_asset(image_bytes, image.content_type or "application/octet-stream")
    task_snapshot = {
        "task": task,
        "image_asset": asset,
        "example": example.model_dump() if example else None,
    }
    if case:
        task_snapshot.update(
            case_id=case["case_id"], case_revision_id=case["id"], case_sha256=case["sha256"]
        )
    selected = {sid: config.strategies[sid].model_dump(mode="json") for sid in ids}
    if not selected:
        selected = {
            "single": {
                "perceive": perceive_model_id,
                "plan": plan_model_id,
                "act": act_model_id,
                "verify": verify_model_id,
                "sim": sim_id,
            }
        }
    runtime = runtime_fingerprint()
    frozen = {
        "defaults": config.defaults.model_dump(mode="json"),
        "runtime": runtime,
        "seed_support": "requested_only" if seed is not None else "not_requested",
    }
    if urdf_path:
        frozen["robot_asset"] = db.save_asset(Path(urdf_path).read_bytes(), "application/xml")
    trial_ids = {}
    try:
        for sid, definition in selected.items():
            refs = config.strategies[sid].endpoint_refs() if ids else set(definition.values())
            system_config = {
                **frozen,
                "strategies": {sid: definition},
                "endpoints": {
                    key: endpoint.model_dump(mode="json")
                    for key, endpoint in config.endpoints.items()
                    if key in refs
                },
            }
            system_config["local_evaluators"] = local_evaluator_versions(system_config)
            trial_ids[sid] = db.begin(
                source="quick",
                task=task_snapshot,
                strategy={"id": sid, **definition},
                config=system_config,
                seed=seed,
            )
    except Exception:
        _finish_open_trials(trial_ids, "error", "Trial preparation failed")
        raise

    _eval_queues[eval_id] = asyncio.Queue()
    _evaluations[eval_id] = {"eval_id": eval_id, "status": "running"}

    # If strategy_ids provided, use multi-strategy RunManager path
    if strategy_ids:
        bg = asyncio.create_task(
            _run_multi_strategy(
                eval_id,
                task,
                image_base64,
                ids,
                example=example,
                urdf_path=urdf_path,
                seed=seed,
                trial_ids=trial_ids,
            )
        )
        _background_tasks.add(bg)
        bg.add_done_callback(lambda done: _background_finished(done, trial_ids, urdf_path))
        return {"eval_id": eval_id, "status": "running", "strategies": ids, "trial_ids": trial_ids}

    # Legacy single-pipeline path
    bg = asyncio.create_task(
        _run_evaluation(
            eval_id,
            task,
            image_base64,
            perceive_model_id,
            plan_model_id,
            act_model_id,
            verify_model_id,
            sim_id,
            example=example,
            seed=seed,
            trial_id=trial_ids["single"],
        )
    )
    _background_tasks.add(bg)
    bg.add_done_callback(lambda done: _background_finished(done, trial_ids, urdf_path))

    return {"eval_id": eval_id, "status": "running", "trial_ids": trial_ids}


@app.get("/api/evaluate/{eval_id}/stream")
async def stream_evaluation(eval_id: str):
    if eval_id not in _eval_queues:
        raise HTTPException(status_code=404, detail="Evaluation not found")

    queue = _eval_queues[eval_id]

    async def event_generator():
        while True:
            msg = await queue.get()
            if msg is None:
                break
            yield f"event: {msg['event']}\ndata: {json.dumps(msg['data'])}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/evaluate/{eval_id}")
async def get_evaluation(eval_id: str):
    if eval_id not in _evaluations:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return _evaluations[eval_id]


@app.get("/api/config")
async def get_config():
    """Return full rove.yaml as JSON (endpoints, strategies, defaults)."""
    return load_config().model_dump()


@app.get("/api/models")
async def list_models():
    return registry.list_models_by_stage()


@app.get("/api/examples")
async def list_examples(eval_category: str | None = None):
    """Return example tasks from data/manifest.json.

    Optional query param ``eval_category`` filters to a single category
    (atomic, multi_stage, situated_correction, constrained, open_ended, negative).
    Response includes a ``categories`` summary for sidebar filtering.
    """
    manifest = _data_dir / "manifest.json"
    if not manifest.exists():
        return {"examples": [], "categories": []}
    from rove.datasets.library import sync_library

    try:
        library = await asyncio.to_thread(sync_library, _trial_root, _data_dir)
    except (ValueError, OSError) as error:
        raise HTTPException(422, "Sample library could not be loaded") from error
    examples = library["examples"]

    # Build category summary counts
    cat_counts: dict[str, int] = {}
    for ex in examples:
        cat = ex.get("eval_category", "uncategorized")
        if not isinstance(cat, str) or not cat.strip():
            cat = "uncategorized"
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    categories = [{"name": k, "count": v} for k, v in sorted(cat_counts.items())]

    # Filter if requested
    if eval_category:
        examples = [ex for ex in examples if ex.get("eval_category") == eval_category]

    return {"examples": examples, "categories": categories, "summary": library["summary"]}


@app.get("/api/mock-models")
async def get_mock_models():
    """Return mock model IDs per stage — server-driven, so dashboard doesn't hardcode."""
    all_stages = registry.list_models_by_stage()
    mock_ids: dict[str, str] = {}
    for stage_name, models in all_stages.get("stages", {}).items():
        for m in models:
            if m.get("available") and m.get("id", "").startswith("mock"):
                mock_ids[stage_name] = m["id"]
                break
    for s in all_stages.get("sim", []):
        if s.get("available") and s.get("id", "").startswith("mock"):
            mock_ids["sim"] = s["id"]
            break
    return mock_ids


@app.get("/api/history")
async def list_history():
    """Return all evaluation history (most recent first)."""
    return _read_history()


@app.get("/api/history/{eval_id}")
async def get_history_entry(eval_id: str):
    """Return a single history entry by eval_id. In-memory first, JSONL fallback."""
    if eval_id in _evaluations:
        return _evaluations[eval_id]
    for record in _read_history():
        if record.get("eval_id") == eval_id:
            return record
    raise HTTPException(status_code=404, detail="History entry not found")


@app.delete("/api/history/{eval_id}")
async def delete_history_entry(eval_id: str):
    """Remove a history entry from the JSONL file."""
    if not _history_path.exists():
        raise HTTPException(status_code=404, detail="No history file")
    lines = _history_path.read_text().splitlines()
    kept = []
    found = False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            if record.get("eval_id") == eval_id:
                found = True
                continue
            kept.append(line)
        except json.JSONDecodeError:
            kept.append(line)
    if not found:
        raise HTTPException(status_code=404, detail="History entry not found")
    _history_path.write_text("\n".join(kept) + ("\n" if kept else ""))
    return {"deleted": eval_id}


# Serve example data
if _data_dir.is_dir():
    app.mount("/data", StaticFiles(directory=str(_data_dir)), name="data")

# Serve frontend (mount entire directory so CSS/JS are served alongside HTML)
_frontend_dir = Path(__file__).parent.parent.parent.parent / "frontend"

if _frontend_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_frontend_dir)), name="frontend-static")


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    index_path = _frontend_dir / "index.html"
    if not index_path.exists():
        return HTMLResponse("<h1>ROVE</h1><p>Frontend not found. Place index.html in frontend/</p>")
    return FileResponse(index_path)
