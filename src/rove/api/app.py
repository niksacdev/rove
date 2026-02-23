"""FastAPI application — REST API + SSE streaming + static file serving."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from starlette.responses import StreamingResponse

from rove.adapters.registry import AdapterRegistry
from rove.config import get_strategies, load_config
from rove.models import PipelineStage, StageStatus
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
registry = AdapterRegistry()
_evaluations: dict[str, dict[str, Any]] = {}
_eval_queues: dict[str, asyncio.Queue] = {}
_background_tasks: set[asyncio.Task] = set()  # prevent GC of background tasks


async def _run_multi_strategy(
    eval_id: str,
    task: str,
    image_base64: str,
    strategy_ids: list[str],
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

        results = await run_manager.run_strategies(strategies, task, image_base64, on_event)

        complete_data = {
            "eval_id": eval_id,
            "status": "completed",
            "task": task,
            "results": results,
        }
        _evaluations[eval_id] = complete_data
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

        async for stage_result in pipeline.run_trial(task, image_base64, eval_id):
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
):
    eval_id = str(uuid.uuid4())
    image_bytes = await image.read()
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")

    _eval_queues[eval_id] = asyncio.Queue()
    _evaluations[eval_id] = {"eval_id": eval_id, "status": "running"}

    # If strategy_ids provided, use multi-strategy RunManager path
    if strategy_ids:
        ids = [s.strip() for s in strategy_ids.split(",") if s.strip()]
        bg = asyncio.create_task(_run_multi_strategy(eval_id, task, image_base64, ids))
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


# Serve frontend
_frontend_dir = Path(__file__).parent.parent.parent.parent / "frontend"


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    index_path = _frontend_dir / "index.html"
    if not index_path.exists():
        return HTMLResponse("<h1>ROVE</h1><p>Frontend not found. Place index.html in frontend/</p>")
    return FileResponse(index_path)
