"""Generate a reproducible mock report; does not call external models or robots."""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
from pathlib import Path

from rove.adapters.mock_sim import MockSimAdapter
from rove.adapters.mock_vla import MockVLAAdapter
from rove.adapters.mock_vlm import MockVLMAdapter
from rove.models import EvaluationProvenance, StageStatus
from rove.orchestrator.pipeline import EvaluationPipeline


async def build_report(seed: int = 42) -> dict:
    config = {"seed": seed, "mock_latency_ms": [0, 0]}
    image = base64.b64encode(Path("data/libero/libero_000.jpg").read_bytes()).decode()
    pipeline = EvaluationPipeline(
        perceive_adapter=MockVLMAdapter(config=config),
        plan_adapter=MockVLMAdapter(config=config),
        act_adapter=MockVLAAdapter(config=config),
        verify_adapter=MockVLMAdapter(config=config),
        sim=MockSimAdapter(config=config),
        pipeline_mode="parallel",
        compute_dynamics=True,
        urdf_path="data/urdf/panda/panda.urdf",
    )
    stages = []
    async for stage in pipeline.run_trial("Pick up the bowl", image):
        if stage.status in (StageStatus.COMPLETED, StageStatus.ERROR):
            stages.append(stage.model_dump(mode="json"))
    if any(s["status"] == "error" for s in stages):
        raise RuntimeError("Mock demo failed; inspect the pipeline regression suite")
    return {
        "kind": "seeded_mock_demo",
        "claim": "Demonstrates evaluation plumbing, not robot task success",
        "seed_applied_to_mock_adapters": True,
        "provenance": EvaluationProvenance.build(image, ["mock"], seed=seed).model_dump(),
        "stages": stages,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=Path("data/output/demo.json"))
    args = parser.parse_args()
    report = asyncio.run(build_report(args.seed))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Wrote mock evidence report: {args.output}")


if __name__ == "__main__":
    main()
