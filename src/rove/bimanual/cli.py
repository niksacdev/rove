"""Set up and run the ABC comparison through ordinary ROVE trials/campaigns."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from rove.benchmarks.report import saved_report, to_html
from rove.benchmarks.runner import run_campaign
from rove.benchmarks.store import CampaignStore
from rove.bimanual.config import (
    Environment,
    Strategy,
    build_spec,
    freeze_profile,
    read_profile,
    validate_profile,
)
from rove.evaluation.executors import prepare_evaluation
from rove.evaluation.models import EvaluationConfig
from rove.evaluation.trials import run_evaluation_trials
from rove.ui.cli_support import emit, read_document, run_cli


def main(argv):
    parser = argparse.ArgumentParser(
        prog="rove bimanual", description="ABC-VLA and adapted pi0.5 on the same bimanual task"
    )
    commands = parser.add_subparsers(dest="action", required=True)
    init = commands.add_parser("init", help="Write an editable local runtime profile")
    init.add_argument("--output", type=Path, required=True)
    setup = commands.add_parser(
        "setup", help="Register installed checkpoints and fingerprint their runtimes"
    )
    setup.add_argument("--config", type=Path, required=True)
    commands.add_parser("doctor", help="Check registered assets; does not load model weights")
    for name in ("plan", "trial", "run"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--name", default="ABC bimanual comparison")
        cmd.add_argument("--strategy", action="append", required=True)
        cmd.add_argument(
            "--scene",
            action="append",
            type=int,
            required=True,
            help="Fixed initial scene seed; repeat for more cases",
        )
        cmd.add_argument(
            "--seed",
            action="append",
            type=int,
            required=True,
            help="Policy sampling seed; repeat for reliability",
        )
        cmd.add_argument("--max-steps", type=int, default=3540)
        cmd.add_argument("--output", type=Path)
    for cmd in commands.choices.values():
        cmd.add_argument("--store", type=Path, default=Path(".rove/benchmarks"))
    args = parser.parse_args(argv)

    def action():
        if args.action == "init":
            config = EvaluationConfig(
                environment=Environment(
                    source="/path/to/abc", python="/path/to/abc/.venv/bin/python"
                ).model_dump(),
                strategies={
                    "abc-vla": Strategy(
                        kind="abc_vla",
                        label="ABC-VLA",
                        checkpoint="/path/to/vla_abc130k_200000_v2.pt",
                        python="/path/to/abc/.venv/bin/python",
                    ).model_dump(),
                    "pi05": Strategy(
                        kind="pi05",
                        label="Adapted pi0.5",
                        checkpoint="/path/to/abc-adapted-pi05",
                        python="/path/to/lerobot/.venv/bin/python",
                    ).model_dump(),
                },
                grading={"success_mode": "ever"},
            )
            with args.output.open("x") as stream:
                stream.write(config.model_dump_json(indent=2) + "\n")
            emit(
                {
                    "profile": str(args.output.resolve()),
                    "next": "Edit paths, then rove bimanual setup --config PROFILE",
                }
            )
            return
        if args.action == "setup":
            config = freeze_profile(EvaluationConfig.model_validate(read_document(args.config)))
            readiness = validate_profile(config)
            if not readiness["ready"]:
                emit(readiness)
                raise ValueError(
                    "Profile is not ready; install the configured runtimes/checkpoints"
                )
            args.store.mkdir(parents=True, exist_ok=True)
            path = args.store / "bimanual.json"
            temp = path.with_suffix(".tmp")
            temp.write_text(config.model_dump_json(indent=2) + "\n")
            temp.replace(path)
            emit({"profile": str(path.resolve()), **readiness})
            return
        config = read_profile(args.store)
        if args.action == "doctor":
            status = validate_profile(config)
            emit(status)
            if not status["ready"]:
                raise ValueError("Bimanual runtime is not ready")
            return
        spec, config = build_spec(
            config,
            name=args.name,
            strategy_ids=args.strategy,
            reset_seeds=args.scene,
            policy_seeds=args.seed,
            max_steps=args.max_steps,
        )
        readiness = validate_profile(config)
        if args.action == "plan":
            emit(
                {
                    "spec": spec.model_dump(mode="json"),
                    "planned_trials": spec.planned_trials,
                    "readiness": readiness,
                },
                args.output,
            )
            return
        if not readiness["ready"]:
            emit(readiness)
            raise ValueError("Selected model runtimes are not ready")
        if args.action == "trial":
            if len(spec.tasks) != 1 or len(spec.seeds) != 1:
                raise ValueError(
                    "A trial comparison uses one scene and one policy seed; use run for a campaign"
                )
            result = asyncio.run(
                run_evaluation_trials(
                    root=args.store,
                    case=spec.tasks[0],
                    config=config,
                    strategy_ids=spec.strategies,
                    execution=spec.execution,
                    seed=spec.seeds[0],
                    timeout_s=spec.timeout_s,
                )
            )
            emit(result, args.output)
            if any(r["execution"] != "completed" for r in result["results"]):
                raise RuntimeError(
                    "One or more model executions failed; inspect the retained traces"
                )
            return
        store = CampaignStore(args.store)
        campaign_id = store.create(prepare_evaluation(spec, config))
        print(
            json.dumps({"campaign_id": campaign_id, "planned_trials": spec.planned_trials}),
            flush=True,
        )
        asyncio.run(run_campaign(store, campaign_id))
        report = saved_report(args.store, campaign_id)
        if args.output:
            args.output.write_text(to_html(report))
        status = store.get(campaign_id)["status"]
        emit(
            {
                "campaign_id": campaign_id,
                "status": status,
                "report_url": f"/api/campaigns/{campaign_id}/report?format=html",
            }
        )
        if (
            status != "completed"
            or len(report["trials"]) != spec.planned_trials
            or any(
                row.get("execution") != "completed" or row.get("outcome") not in {"pass", "fail"}
                for row in report["trials"]
            )
        ):
            raise RuntimeError(
                "Campaign execution is incomplete or unassessed; the report and recorded trials "
                "have been retained for inspection"
            )

    run_cli(parser, action)
