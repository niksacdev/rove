"""Run redistribution-safe causal fixtures through normal customer campaign services."""

import argparse
import asyncio
import json
from pathlib import Path

import yaml

from rove.benchmarks.report import report_data, to_csv, to_html
from rove.benchmarks.runner import run_campaign
from rove.benchmarks.store import CampaignStore
from rove.benchmarks.workflow import LaunchRequest, assessed_trials, create_campaign
from rove.datasets.service import DatasetService
from rove.models.config import RoveConfig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True)
    args = parser.parse_args()
    service = DatasetService(args.store)
    directory = Path(__file__).resolve().parent
    # The illustration is a UI placeholder; the structured 1D world is authoritative.
    asset = service.trials.save_asset(
        (directory.parent / "customer-cases" / "clear-target.png").read_bytes(), "image/png"
    )
    cases = [
        service.import_case(
            {
                "name": name,
                "task": "Move the point from 0 m to 1 m using world-axis deltas. Avoid forbidden positions.",
                "conditions": {"synthetic_environment": environment},
            },
            asset["sha256"],
        )
        for name, environment in [
            ("Clear reach", {}),
            ("Forbidden interval", {"forbidden_interval_m": [0.25, 0.3]}),
            ("Reach after disturbance", {"disturbance_step": 1, "disturbance_delta_m": -0.5}),
        ]
    ]
    contract = service.create_contract(
        {
            "name": "Synthetic reaching v1",
            "scope": "episode_outcome",
            "evidence_mode": "synthetic_rollout",
            "criteria": [
                {
                    "id": "completion",
                    "description": "Reach the target without crossing forbidden positions",
                    "assessment": "configured_verifier",
                    "endpoint": "episode-outcome",
                }
            ],
            "metrics": [
                "task_success",
                "pass_at_k",
                "pass_pow_k",
                "pipeline_latency",
                "episode_completion_time",
                "autonomous_completion",
                "recovery",
                "constraint_outcomes",
            ],
            "campaign_targets": [
                {"metric": "task_success", "operator": "gte", "threshold": 0.6, "unit": "fraction"}
            ],
        }
    )
    config = RoveConfig.model_validate(yaml.safe_load((directory / "rove.yaml").read_text()))
    request = LaunchRequest(
        name="Action-dependent synthetic robotics",
        case_revision_ids=[case["id"] for case in cases],
        contract_id=contract["id"],
        strategies=["baseline", "candidate"],
        seeds=[1, 2, 3],
        ks=[1, 3],
        operation_id="synthetic-demo-" + cases[0]["id"],
    )
    identity, count = create_campaign(args.store, request, config)
    store = CampaignStore(args.store)
    asyncio.run(run_campaign(store, identity))
    campaign = store.get(identity)
    trials, assessments = assessed_trials(args.store, campaign, store.trials(identity))
    data = report_data(campaign, trials, [])
    data["assessments"] = assessments
    output = args.store / "reports"
    output.mkdir(exist_ok=True)
    for suffix, body in [
        ("json", json.dumps(data, indent=2)),
        ("html", to_html(data)),
        ("csv", to_csv(data)),
    ]:
        (output / f"{identity}.{suffix}").write_text(body)
    print(f"{identity}: {count} planned synthetic attempts; reports in {output.resolve()}")


if __name__ == "__main__":
    main()
