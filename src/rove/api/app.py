"""FastAPI application — REST API + SSE streaming + static file serving."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import StreamingResponse

from rove.adapters.registry import AdapterRegistry
from rove.models import ExampleData, PipelineStage, StageStatus
from rove.models.config import get_strategies, load_config
from rove.orchestrator.pipeline import EvaluationPipeline
from rove.orchestrator.run_manager import RunManager

logger = logging.getLogger(__name__)

app = FastAPI(title="ROVE", version="0.1.0", description="Robot Observation & Vision Evaluation")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state (no database for demo)
_data_dir = Path(__file__).parent.parent.parent.parent / "data"
_history_path = _data_dir / "history.jsonl"
registry = AdapterRegistry()
_evaluations: dict[str, dict[str, Any]] = {}
_eval_queues: dict[str, asyncio.Queue] = {}
_background_tasks: set[asyncio.Task] = set()  # prevent GC of background tasks


def _persist_evaluation(eval_data: dict[str, Any]) -> None:
    """Append a completed evaluation record to history.jsonl."""
    try:
        _data_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "eval_id": eval_data.get("eval_id"),
            "task": eval_data.get("task", ""),
            "timestamp": datetime.now(UTC).isoformat(),
            "status": eval_data.get("status", "completed"),
            "strategy_ids": eval_data.get("strategy_ids", []),
            "results": eval_data.get("results", {}),
        }
        with open(_history_path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        logger.warning("Failed to persist evaluation to history.jsonl", exc_info=True)


def _read_history() -> list[dict[str, Any]]:
    """Read all history records from JSONL, most recent first."""
    if not _history_path.exists():
        return []
    records = []
    for line in _history_path.read_text().splitlines():
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
            registry, max_concurrent=config.defaults.max_concurrent_combinations
        )

        async def on_event(strategy_id: str, event_type: str, data: dict) -> None:
            await queue.put({"event": event_type, "data": data})

        results = await run_manager.run_strategies(
            strategies,
            task,
            image_base64,
            on_event,
            example=example,
        )

        complete_data = {
            "eval_id": eval_id,
            "status": "completed",
            "task": task,
            "results": results,
            "strategy_ids": strategy_ids,
        }
        _evaluations[eval_id] = complete_data
        _persist_evaluation(complete_data)
        await queue.put({"event": "complete", "data": complete_data})

    except Exception as e:
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
):
    """Background task that runs a single pipeline and pushes events to the SSE queue."""
    queue = _eval_queues[eval_id]
    try:
        perceive = registry.get_adapter_for_stage(PipelineStage.PERCEIVE, perceive_model_id)
        plan = registry.get_adapter_for_stage(PipelineStage.PLAN, plan_model_id)
        act = registry.get_adapter_for_stage(PipelineStage.ACT, act_model_id)
        verify = registry.get_adapter_for_stage(PipelineStage.VERIFY, verify_model_id)
        sim = registry.get_sim(sim_id)

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
            if stage_result.status in (StageStatus.COMPLETED, StageStatus.ERROR):
                stages.append(stage_dict)
            await queue.put({"event": "stage", "data": stage_dict})

        total_latency = sum(s.get("latency_ms", 0) for s in stages)
        verify_stage = next((s for s in stages if s["stage"] == "verify"), None)
        success = (
            verify_stage["output"]["success"]
            if verify_stage and verify_stage.get("output")
            else False
        )

        result = {
            "eval_id": eval_id,
            "status": "completed",
            "task": task,
            "models": {
                "perceive": perceive_model_id,
                "plan": plan_model_id,
                "act": act_model_id,
                "verify": verify_model_id,
                "sim": sim_id,
            },
            "stages": stages,
            "success": success,
            "total_latency_ms": round(total_latency, 1),
        }
        _evaluations[eval_id] = result
        _persist_evaluation(result)
        await queue.put({"event": "complete", "data": result})

    except Exception as e:
        logger.exception(f"Evaluation {eval_id} failed")
        error_result = {"eval_id": eval_id, "status": "error", "error": str(e)}
        _evaluations[eval_id] = error_result
        await queue.put({"event": "error", "data": error_result})
    finally:
        await queue.put(None)  # sentinel to end SSE stream


@app.get("/api/strategies")
async def list_strategies():
    """Return all strategies with their model assignments."""
    strategies = get_strategies()
    return {"strategies": [s.model_dump() for s in strategies.values()]}


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
):
    eval_id = str(uuid.uuid4())
    image_bytes = await image.read()
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")

    # Load example data (ground truth, proprioception, etc.) from manifest
    example = _load_example_data(example_filename) if example_filename else None

    _eval_queues[eval_id] = asyncio.Queue()
    _evaluations[eval_id] = {"eval_id": eval_id, "status": "running"}

    # If strategy_ids provided, use multi-strategy RunManager path
    if strategy_ids:
        ids = [s.strip() for s in strategy_ids.split(",") if s.strip()]
        bg = asyncio.create_task(
            _run_multi_strategy(eval_id, task, image_base64, ids, example=example)
        )
        _background_tasks.add(bg)
        bg.add_done_callback(_background_tasks.discard)
        return {"eval_id": eval_id, "status": "running", "strategies": ids}

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
        )
    )
    _background_tasks.add(bg)
    bg.add_done_callback(_background_tasks.discard)

    return {"eval_id": eval_id, "status": "running"}


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
async def list_examples():
    """Return example tasks from data/manifest.json."""
    manifest = _data_dir / "manifest.json"
    if not manifest.exists():
        return {"examples": []}
    return json.loads(manifest.read_text())


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
