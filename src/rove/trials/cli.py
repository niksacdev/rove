"""Run and inspect durable robotics trials without a dashboard or HTTP server."""

import argparse
import asyncio
import sys
from pathlib import Path

from rove.benchmarks.seeds import TrialSeedRequest, trial_seed
from rove.datasets.promotion import promote_quick_trial
from rove.trials.execution import run_trials
from rove.trials.reading import with_assessments
from rove.trials.store import TrialStore
from rove.trials.traces import trial_trace
from rove.ui.cli_support import add_common, emit, read_document, run_cli


def read_bounded(path, limit):
    with path.open("rb") as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError(f"Input exceeds its {limit}-byte limit")
    return value


def main(argv):
    parser = argparse.ArgumentParser(
        prog="rove trial",
        description="Run one observation across strategies and inspect its durable trials",
    )
    commands = parser.add_subparsers(dest="action", required=True)
    run = commands.add_parser(
        "run", help="Compare configured strategies on the same task, image and robot"
    )
    run.add_argument(
        "--strategy",
        action="append",
        required=True,
        help="Strategy ID; repeat for multiple strategies",
    )
    run.add_argument("--case", dest="case_revision_id")
    run.add_argument("--task")
    run.add_argument("--image", type=Path)
    run.add_argument("--urdf", type=Path)
    run.add_argument(
        "--context", type=Path, help="Public candidate context JSON/YAML (unbound inputs only)"
    )
    run.add_argument("--seed", type=int)
    listing = commands.add_parser("list")
    listing.add_argument("--source")
    listing.add_argument("--case", dest="case_revision_id")
    listing.add_argument("--limit", type=int, default=50)
    listing.add_argument("--offset", type=int, default=0)
    for name in ("show", "events", "trace", "evidence", "seed", "promote"):
        command = commands.add_parser(name)
        command.add_argument("trial_id")
        if name in {"seed", "promote"}:
            command.add_argument("--operation-id", required=True)
            command.add_argument("--name")
        if name == "events":
            command.add_argument("--limit", type=int, default=200)
            command.add_argument("--offset", type=int, default=0)
    for command in commands.choices.values():
        add_common(command)
        command.add_argument("--output", type=Path, help="Write JSON to a file instead of stdout")
    args = parser.parse_args(argv)

    def action():
        store = TrialStore(args.store)
        if args.action == "run":

            async def progress(sid, kind, data):
                if kind in {"stage_start", "stage_complete", "strategy_complete", "strategy_error"}:
                    print(f"{sid}: {kind} {data.get('stage', '')}", file=sys.stderr, flush=True)

            result = asyncio.run(
                run_trials(
                    root=args.store,
                    config_path=args.config,
                    strategy_ids=args.strategy,
                    case_revision_id=args.case_revision_id,
                    task=args.task,
                    image_bytes=read_bounded(args.image, 16 * 1024 * 1024) if args.image else None,
                    urdf_bytes=read_bounded(args.urdf, 4 * 1024 * 1024) if args.urdf else None,
                    candidate_context=read_document(args.context) if args.context else None,
                    seed=args.seed,
                    on_event=progress,
                )
            )
        elif args.action == "list":
            result = {
                "trials": with_assessments(
                    store,
                    store.list(
                        source=args.source,
                        case_revision_id=args.case_revision_id,
                        limit=args.limit,
                        offset=args.offset,
                    ),
                ),
                "total": store.count(source=args.source, case_revision_id=args.case_revision_id),
            }
        elif args.action == "seed":
            result = trial_seed(
                args.store,
                args.trial_id,
                TrialSeedRequest(operation_id=args.operation_id, name=args.name),
                config_path=args.config,
            )
        elif args.action == "promote":
            result = promote_quick_trial(
                args.store, args.trial_id, operation_id=args.operation_id, name=args.name
            )
        else:
            store.get(args.trial_id)
            result = (
                with_assessments(store, [store.get(args.trial_id)])[0]
                if args.action == "show"
                else store.events(args.trial_id, args.limit, args.offset)
                if args.action == "events"
                else trial_trace(store, args.trial_id)
                if args.action == "trace"
                else store.evidence(args.trial_id)
            )
        emit(result, args.output)
        if args.action == "run" and any(
            store.get(identity)["status"] == "error" for identity in result["trial_ids"].values()
        ):
            raise RuntimeError(
                "One or more strategies could not execute; retained trial results are in the JSON output"
            )

    run_cli(parser, action)
