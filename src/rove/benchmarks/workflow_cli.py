"""Versioned campaign workflows, reports, and explicit CI gates using shared services."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx

from rove.benchmarks.assistant_workflow import AblationDraft, prepare_ablation
from rove.benchmarks.comparison import saved_comparison
from rove.benchmarks.insights import CampaignInsights, MetricsDraftRequest
from rove.benchmarks.report import public_campaign, saved_report, to_csv, to_html
from rove.benchmarks.runner import run_campaign
from rove.benchmarks.seeds import TrialSeedRequest, campaign_seed, trial_seed
from rove.benchmarks.store import CampaignStore
from rove.benchmarks.workflow import LaunchRequest, prepare_launch
from rove.models.config import load_config
from rove.trials.snapshots import sanitize
from rove.ui.cli_support import add_common, emit, read_document, run_cli


def quality_gate(data, minimum=None, require_targets=False):
    if minimum is not None and not 0 <= minimum <= 1:
        raise ValueError("--require-success-rate must be between zero and one")
    reasons = []
    if minimum is not None or require_targets:
        if data["campaign"]["status"] != "completed":
            reasons.append("Campaign is not completed")
        if any(row["unknown"] for row in data["summary"]["tasks"]):
            reasons.append("Planned outcomes remain unresolved; unknown is not success")
    if minimum is not None:
        for strategy in data["campaign"]["spec"]["strategies"]:
            rows = [row for row in data["summary"]["tasks"] if row["strategy_id"] == strategy]
            planned = sum(row["n"] for row in rows)
            rate = sum(row["passed"] for row in rows) / planned if planned else None
            if rate is None or rate < minimum:
                reasons.append(f"{strategy}: observed success rate is below {minimum}")
    if require_targets:
        targets = data["summary"]["campaign_targets"]
        if not targets or any(
            row["status"] != "met" or row.get("unknown_trials", 0) for row in targets
        ):
            reasons.append("Declared campaign targets are missing, unresolved, or not met")
    return {
        "enabled": minimum is not None or require_targets,
        "passed": not reasons if minimum is not None or require_targets else None,
        "reasons": reasons,
        "scope": "declared evaluation assessment; not a physical-success claim",
    }


def write_bundle(data, directory):
    if directory is None:
        return {}
    directory.mkdir(parents=True, exist_ok=True)
    clean = sanitize(data)
    paths = {}
    for extension, content in (
        ("json", json.dumps(clean, indent=2, allow_nan=False)),
        ("html", to_html(clean)),
        ("csv", to_csv(clean)),
    ):
        destination = directory / f"{data['campaign']['id']}.{extension}"
        destination.write_text(content)
        paths[extension] = str(destination.resolve())
    return paths


def main(argv):
    parser = argparse.ArgumentParser(
        prog="rove campaign",
        description="Create reproducible campaigns and automate assessed reports",
    )
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("preview", "run", "metrics-draft"):
        command = commands.add_parser(name)
        command.add_argument("input", help="JSON/YAML request, or '-' for stdin")
    commands.choices["run"].add_argument(
        "--operation-id", help="Stable key for idempotent CI retries"
    )
    commands.choices["run"].add_argument(
        "--revision", help="Declared source commit or experiment revision"
    )
    for name in (
        "show",
        "resume",
        "report",
        "summary",
        "improve",
        "compare",
        "cancel",
        "watch",
        "timeline",
    ):
        command = commands.add_parser(name)
        command.add_argument("campaign_id")
    commands.choices["cancel"].add_argument(
        "--url",
        default="http://127.0.0.1:5001",
        help="Running loopback dashboard that owns this campaign",
    )
    commands.choices["compare"].add_argument(
        "--strategy", help="Candidate strategy; defaults to first for compatibility"
    )
    commands.choices["watch"].add_argument("--interval", type=float, default=2)
    commands.choices["watch"].add_argument("--timeout", type=float, default=300)
    seed = commands.add_parser(
        "seed", help="Seed from one standalone strategy trial, without rerunning it"
    )
    seed.add_argument("trial_id")
    seed.add_argument("--operation-id", required=True)
    seed.add_argument("--name")
    for name in ("ablation-preview", "ablation"):
        command = commands.add_parser(name)
        command.add_argument("campaign_id")
        command.add_argument("input", help="Ablation request JSON/YAML")
        command.add_argument("--operation-id")
    commands.add_parser("list")
    for name, command in commands.choices.items():
        add_common(command)
        if name in {"run", "resume", "report", "ablation"}:
            command.add_argument(
                "--output", type=Path, help="Report bundle directory (HTML, JSON and CSV)"
            )
            command.add_argument(
                "--require-success-rate",
                type=float,
                help="CI gate for every selected strategy; unresolved outcomes fail",
            )
            command.add_argument(
                "--require-targets",
                action="store_true",
                help="Fail if declared targets are missing, unknown or unmet",
            )
        else:
            command.add_argument("--output", type=Path, help="Optional JSON output file")
    args = parser.parse_args(argv)

    def action():
        store = CampaignStore(args.store)
        kind = args.action
        if (
            hasattr(args, "require_success_rate")
            and args.require_success_rate is not None
            and not 0 <= args.require_success_rate <= 1
        ):
            raise ValueError("--require-success-rate must be between zero and one")
        if kind == "list":
            result = {"campaigns": [public_campaign(item) for item in store.list()]}
        elif kind == "seed":
            result = trial_seed(
                args.store,
                args.trial_id,
                TrialSeedRequest(operation_id=args.operation_id, name=args.name),
                config_path=args.config,
            )
        elif kind == "improve":
            result = campaign_seed(args.store, args.campaign_id, config_path=args.config)
        elif kind == "metrics-draft":
            load_config(args.config)
            result = asyncio.run(
                CampaignInsights(args.store).draft(
                    MetricsDraftRequest.model_validate(read_document(args.input))
                )
            )
        elif kind == "summary":
            load_config(args.config)
            result = asyncio.run(CampaignInsights(args.store).summary(args.campaign_id))
        elif kind == "compare":
            result = saved_comparison(args.store, args.campaign_id, args.strategy)
        elif kind == "timeline":
            from rove.benchmarks.trajectory import campaign_timeline

            result = campaign_timeline(args.store, args.campaign_id)
        elif kind == "show":
            result = saved_report(args.store, args.campaign_id)
        elif kind == "cancel":
            from rove.ui.assistant_cli import local_url

            endpoint = (
                local_url(args.url)
                + "/api/campaigns/"
                + quote(args.campaign_id, safe="")
                + "/cancel"
            )
            try:
                with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
                    response = client.post(endpoint)
                    response.raise_for_status()
                    result = response.json()
            except httpx.HTTPError:
                raise ValueError(
                    "The owning local server could not cancel this campaign; no local status was changed"
                ) from None
        elif kind == "watch":
            if not 0.1 <= args.interval <= 60 or not 0 < args.timeout <= 86400:
                raise ValueError("Use interval 0.1..60 seconds and timeout 1..86400 seconds")
            deadline = time.monotonic() + args.timeout
            while store.get(args.campaign_id)["status"] in {"pending", "running", "cancelling"}:
                if time.monotonic() >= deadline:
                    raise ValueError("Campaign is still active after the watch deadline")
                time.sleep(min(args.interval, max(0, deadline - time.monotonic())))
            result = saved_report(args.store, args.campaign_id)
        else:
            if kind in {"run", "preview"}:
                body = read_document(args.input)
                if not isinstance(body, dict):
                    raise ValueError("Campaign request must be a JSON/YAML object")
                if kind == "run":
                    body["operation_id"] = (
                        args.operation_id or body.get("operation_id") or uuid4().hex
                    )
                    if args.revision:
                        body["revision"] = args.revision
                request = LaunchRequest.model_validate(body)
                payload, preview = prepare_launch(args.store, request, load_config(args.config))
                if kind == "preview":
                    emit(preview, args.output)
                    return
                if not preview["ready"]:
                    raise ValueError("; ".join(preview["blockers"]))
                identity = store.create(payload, operation_id=request.operation_id)
            elif kind in {"ablation", "ablation-preview"}:
                body = read_document(args.input)
                if not isinstance(body, dict):
                    raise ValueError("Ablation request must be a JSON/YAML object")
                file_operation = body.pop("operation_id", None)
                operation = args.operation_id or file_operation or uuid4().hex
                body["baseline_campaign_id"] = args.campaign_id
                payload, preview = prepare_ablation(
                    args.store,
                    AblationDraft.model_validate(body),
                    load_config(args.config),
                    operation,
                )
                if kind == "ablation-preview":
                    emit(preview, args.output)
                    return
                if not preview["ready"]:
                    raise ValueError(
                        "; ".join(preview["blockers"] + preview["comparison"]["reasons"])
                    )
                identity = store.create(payload, operation_id=operation)
            else:
                identity = args.campaign_id

            async def progress(row):
                print(
                    f"{row['strategy_id']} / {row['task_id']} / {row['seed']}: execution {row['execution']}",
                    file=sys.stderr,
                    flush=True,
                )

            execution_raised = False
            if kind != "report":
                try:
                    asyncio.run(run_campaign(store, identity, progress))
                except (Exception, KeyboardInterrupt):
                    # Preserve diagnostic artifacts before returning a failed CI status.
                    # Provider exception text may contain private inputs or credentials.
                    execution_raised = True
            data = saved_report(args.store, identity)
            execution_failed = kind != "report" and (
                execution_raised
                or data["campaign"]["status"] != "completed"
                or any(
                    row.get("execution")
                    not in {"completed", "invalid_verdict", "unresolved_evidence"}
                    for row in data["trials"]
                )
            )
            gate = quality_gate(data, args.require_success_rate, args.require_targets)
            data["quality_gate"] = gate
            paths = write_bundle(data, args.output)
            emit({"id": identity, "report": data, "artifacts": paths, "quality_gate": gate})
            if execution_failed:
                raise RuntimeError("Campaign execution failed; inspect the retained trial evidence")
            if gate["enabled"] and not gate["passed"]:
                raise RuntimeError("Quality gate failed: " + "; ".join(gate["reasons"]))
            return
        emit(result, args.output)

    run_cli(parser, action)
