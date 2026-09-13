"""CLI access to the same campaign runner and reports used by the dashboard."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import yaml

from rove.benchmarks.models import CampaignSpec
from rove.benchmarks.report import saved_report, to_csv, to_html
from rove.benchmarks.runner import prepare, run_campaign
from rove.benchmarks.store import CampaignStore
from rove.models.config import load_config


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        description="Run reproducible benchmark campaigns (macOS/Linux)"
    )
    parser.add_argument("action", choices=["run", "resume", "report", "list"])
    parser.add_argument(
        "target", nargs="?", help="Manifest path for run; campaign ID for resume/report"
    )
    parser.add_argument("--config", type=Path, default=Path("rove.yaml"))
    parser.add_argument("--store", type=Path, default=Path(".rove/benchmarks"))
    parser.add_argument("--output", type=Path, default=Path(".rove/reports"))
    args = parser.parse_args(argv)
    if args.action != "list" and not args.target:
        parser.error("target is required")
    store = CampaignStore(args.store)
    if args.action == "list":
        for campaign in store.list():
            print(campaign["id"], campaign["status"], campaign["spec"]["name"])
        return
    try:
        if args.action == "run":
            spec = CampaignSpec.model_validate(yaml.safe_load(Path(args.target).read_text()))
            config = load_config(args.config)
            campaign_id = store.create(prepare(spec, config))
            print(f"Campaign {campaign_id}: {spec.planned_trials} planned attempts", flush=True)
        else:
            campaign_id = args.target

        async def progress(record):
            print(
                f"{record['task_id']} / {record['strategy_id']} / {record['seed']}: "
                f"{record['outcome']} ({record['execution']})",
                flush=True,
            )

        if args.action != "report":
            asyncio.run(run_campaign(store, campaign_id, progress))
        data = saved_report(args.store, campaign_id)
        args.output.mkdir(parents=True, exist_ok=True)
        for extension, content in (
            ("html", to_html(data)),
            ("json", json.dumps(data, indent=2)),
            ("csv", to_csv(data)),
        ):
            destination = args.output / f"{campaign_id}.{extension}"
            destination.write_text(content)
            print(f"Report: {destination.resolve()}")
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error))
