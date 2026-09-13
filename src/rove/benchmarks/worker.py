"""One isolated pipeline attempt. Invoked only by the campaign runner."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import yaml

from rove.adapters.registry import AdapterRegistry
from rove.models import ExampleData
from rove.models.config import StrategyConfig, get_strategies, load_config, reset_config_cache
from rove.orchestrator.run_manager import RunManager
from rove.trials.store import TrialStore


def classify(result: dict) -> dict:
    stages = result.get("stages", [])
    if result.get("error") or any(s.get("status") == "error" for s in stages):
        return {"outcome": "unknown", "execution": "error"}
    verify = next(
        (s for s in stages if s["stage"] == "verify" and s["status"] == "completed"), None
    )
    output = verify.get("output", {}) if verify else {}
    if (
        output
        and not output.get("verdict_valid", True)
        and (output.get("evaluator_result") or output.get("check_results"))
    ):
        return {"outcome": "unknown", "execution": "unresolved_evidence"}
    if (
        not output
        or not output.get("verdict_valid", True)
        or type(output.get("success")) is not bool
    ):
        return {"outcome": "unknown", "execution": "invalid_verdict"}
    return {"outcome": "pass" if output["success"] else "fail", "execution": "completed"}


async def execute(request: dict, config_path: Path) -> dict:
    config = request["config"]
    strategy = config["strategies"][request["strategy_id"]]
    used = StrategyConfig.model_validate(strategy).endpoint_refs()
    config["strategies"] = {request["strategy_id"]: strategy}
    config["endpoints"] = {key: value for key, value in config["endpoints"].items() if key in used}
    seed_support = {}
    for endpoint_id, endpoint in config["endpoints"].items():
        supported = endpoint.get("adapter") in {"mock_vlm", "mock_vla", "mock_agent", "mock_sim"}
        seed_support[endpoint_id] = "applied" if supported else "unsupported"
        if supported:
            endpoint.setdefault("config", {})["seed"] = request["seed"]
    config_path.write_text(yaml.safe_dump(config))
    reset_config_cache()
    load_config(config_path)
    task = request["task"]
    robot = ((task.get("example") or {}).get("extras") or {}).get("robot_asset")
    urdf_path = None
    if robot:
        from rove.datasets.robot_assets import robot_asset_path

        urdf_path = str(robot_asset_path(TrialStore(Path(request["trial_root"])), robot))
    manager = RunManager(AdapterRegistry(), max_concurrent=1, urdf_path=urdf_path)

    async def ignore_event(*_args):
        pass

    task = request["task"]
    started = time.monotonic()
    results = await manager.run_strategies(
        [get_strategies()[request["strategy_id"]]],
        task["task"],
        task["image_base64"],
        ignore_event,
        ExampleData.model_validate(task["example"]) if task.get("example") else None,
        trial_contexts={
            request["strategy_id"]: (TrialStore(Path(request["trial_root"])), request["trial_id"])
        }
        if request.get("trial_id")
        else None,
    )
    result = results[0]
    return {
        **classify(result),
        "result": result,
        "seed_support": seed_support,
        "latency_ms": (time.monotonic() - started) * 1000,
    }


def main():
    request_path, output_path = map(Path, sys.argv[1:])
    request = json.loads(request_path.read_text())
    result = asyncio.run(execute(request, request_path.with_suffix(".yaml")))
    output_path.write_text(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
