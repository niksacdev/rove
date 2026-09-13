"""Bounded workflow assistance; only the host can confirm a prepared mutation.

Enable with ROVE_ASSISTANT_ENDPOINT naming an explicitly configured copilot_agent
endpoint. Proposals expire after ten minutes and after server restart: prepare again
to confirm. No confirmation token, write tool or ambient resource enters the model
session. This local API inherits the application's loopback/security boundary.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from rove.benchmarks.assistant_workflow import (
    AblationDraft,
    BaselineDraft,
    ContractDraft,
    FreezeDraft,
    ImportDraft,
    RevisionDraft,
    execute_operation,
    prepare_operation,
)
from rove.benchmarks.baselines import BaselineStore
from rove.benchmarks.comparison import compare_campaigns
from rove.benchmarks.metrics import summarize
from rove.benchmarks.store import CampaignStore
from rove.benchmarks.workflow import LaunchRequest, assessed_trials, prepare_launch
from rove.datasets.service import DatasetService
from rove.models.config import load_config
from rove.runtime.assistant import assistant_settings
from rove.runtime.copilot import CopilotRuntime, RuntimeTool
from rove.trials.snapshots import canonical_json, content_hash, sanitize
from rove.trials.store import TrialStore, now

Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[\w.-]+$")]


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AssistantContext(Arguments):
    case_revision_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    dataset_revision_id: Identifier | None = None
    contract_id: Identifier | None = None
    campaign_id: Identifier | None = None
    trial_id: Identifier | None = None
    asset_sha256s: list[Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]] = Field(
        default_factory=list, max_length=20
    )
    baseline_revision_id: Identifier | None = None


class AskRequest(Arguments):
    message: str = Field(min_length=1, max_length=8000)
    context: AssistantContext = Field(default_factory=AssistantContext)


class ConfirmRequest(Arguments):
    operation_id: Identifier
    confirmation_token: str = Field(min_length=32, max_length=128)
    confirmed: Literal[True]


class Page(Arguments):
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0, le=100_000)


class Record(Arguments):
    id: Identifier


class ReviewPage(Page):
    case_revision_id: Identifier | None = None
    trial_id: Identifier | None = None


class CampaignDraft(LaunchRequest):
    """The model may choose existing records, never its operation identity."""

    model_config = ConfigDict(extra="forbid", strict=True)
    case_revision_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    dataset_revision_id: Identifier | None = None
    contract_id: Identifier
    strategies: list[Identifier] = Field(min_length=1, max_length=20)
    seeds: list[int] = Field(default=[1, 2, 3], min_length=1, max_length=100)
    operation_id: None = None


class Answer(Arguments):
    answer: str = Field(min_length=1, max_length=16_000)


@dataclass
class Proposal:
    request: LaunchRequest | dict
    preview: dict
    digest: str
    token: str
    expires: float
    launched: bool = False
    result: dict | None = None
    kind: str = "launch_campaign"
    payload: dict | None = None


def create_assistant_router(
    root: Path,
    launch,
    *,
    config_loader=load_config,
    runtime_factory=CopilotRuntime,
    endpoint_id: str | None = None,
    clock=time.monotonic,
) -> APIRouter:
    """Mount alongside the customer workflow router using its launch callback."""
    router = APIRouter()
    proposals: dict[str, Proposal] = {}

    def settings():
        return assistant_settings(config_loader=config_loader, endpoint_id=endpoint_id)

    @router.get("/api/assistant/status")
    async def status():
        try:
            available = await runtime_factory(settings()).health_check()
            reason = (
                None if available else "Install the pinned Copilot runtime to enable assistance"
            )
        except Exception:
            available, reason = (
                False,
                "Configure an explicit Copilot assistant endpoint and runtime",
            )
        return {
            "available": available,
            "reason": reason,
            "capabilities": [
                "read_evidence",
                "preview_case_import",
                "preview_case_revision",
                "preview_contract",
                "preview_freeze",
                "preview_baseline",
                "preview_ablation",
                "read_comparison",
                "preview_campaign",
                "confirmed_launch",
            ],
        }

    @router.post("/api/assistant/ask")
    async def ask(request: AskRequest):
        try:
            runtime_settings = settings()
        except Exception as error:
            raise HTTPException(503, "Workflow assistant is not configured") from error
        service = DatasetService(root)
        evidence: dict[str, dict] = {}
        prepared: list[dict] = []
        calls = 0

        def link(label, url):
            evidence[url] = {"label": label, "url": url}

        def inspect_case(args):
            record = service.get_case_revision(args.id)
            link("Case revision", "/static/datasets.html")
            return record

        def inspect_trial(args):
            store = TrialStore(root)
            record = store.get(args.id)
            link("Trial evidence", "/static/history.html?trial=" + args.id)
            return {
                "id": args.id,
                "status": record["status"],
                "result": record.get("result"),
                "snapshot_hash": (record.get("snapshot") or {}).get("sha256"),
                "events": store.events(args.id, limit=30),
                "events_limit": 30,
            }

        def inspect_campaign(args):
            store = CampaignStore(root)
            campaign = store.get(args.id)
            trials, assessments = assessed_trials(root, campaign, store.trials(args.id))
            link("Campaign report", "/static/benchmarks.html?campaign=" + args.id)
            return {
                "id": args.id,
                "name": campaign.get("name"),
                "status": campaign["status"],
                "contract": campaign.get("contract"),
                "summary": summarize(campaign, trials),
                "assessments": assessments,
            }

        def strategies(_args):
            return [
                {
                    "id": key,
                    "display_name": value.display_name,
                    "stages": {
                        name: value.stage_options(name).endpoint
                        for name in ("perceive", "plan", "act", "verify")
                        if value.stage_options(name)
                    },
                }
                for key, value in config_loader().strategies.items()
            ]

        def preview(args):
            # Identity and confirmation authorization are exclusively host generated.
            operation_id = str(uuid4())
            launch_request = LaunchRequest.model_validate(
                {**args.model_dump(), "operation_id": operation_id}
            )
            payload, report = prepare_launch(root, launch_request, config_loader())
            if len(canonical_json(report).encode()) > 64_000:
                return {"error": "Preview is too large; select fewer cases or a smaller contract"}
            if not report["ready"]:
                return {"preview": report, "confirmation_available": False}
            for key in list(proposals):
                if proposals[key].expires <= clock():
                    del proposals[key]
            if len(proposals) >= 100 or len(prepared) >= 3:
                return {
                    "error": "Proposal limit reached; confirm or wait for existing drafts to expire"
                }
            proposal = Proposal(
                launch_request, report, content_hash(payload), str(uuid4()), clock() + 600
            )
            proposals[operation_id] = proposal
            prepared.append(
                {
                    "operation_id": operation_id,
                    "confirmation_token": proposal.token,
                    "kind": "launch_campaign",
                    "request": sanitize(launch_request.model_dump()),
                    "preview": sanitize(report),
                }
            )
            # The token is never part of a model-visible tool result or context.
            return {"operation_id": operation_id, "preview": report, "requires_confirmation": True}

        def prepare_mutation(kind, args):
            operation_id = str(uuid4())
            body = args.model_dump()
            if kind == "import_case" and args.image_sha256 not in request.context.asset_sha256s:
                raise ValueError("Import must use an asset explicitly attached by the user")
            payload, report = prepare_operation(root, kind, body, config_loader(), operation_id)
            if not report["ready"]:
                return {"preview": report, "confirmation_available": False}
            for key in list(proposals):
                if proposals[key].expires <= clock():
                    del proposals[key]
            if len(proposals) >= 100 or len(prepared) >= 3:
                return {"error": "Proposal limit reached"}
            if len(canonical_json(report).encode()) > 64_000:
                return {"error": "Preview is too large; use fewer cases or smaller metadata"}
            proposal = Proposal(
                body,
                report,
                content_hash(payload),
                str(uuid4()),
                clock() + 600,
                kind=kind,
                payload=payload,
            )
            proposals[operation_id] = proposal
            prepared.append(
                {
                    "operation_id": operation_id,
                    "confirmation_token": proposal.token,
                    "kind": kind,
                    "request": sanitize(body),
                    "preview": sanitize(report),
                }
            )
            return {"operation_id": operation_id, "preview": report, "requires_confirmation": True}

        def inspect_comparison(args):
            store = CampaignStore(root)
            candidate = store.get(args.id)
            reference = candidate.get("baseline")
            if not reference:
                raise ValueError("Campaign has no baseline comparison")
            baseline = store.get(reference["campaign_id"])
            baseline_trials = assessed_trials(root, baseline, store.trials(baseline["id"]))[0]
            if reference.get("revision_id"):
                baseline_trials = BaselineStore(root).get(reference["revision_id"])[
                    "trial_outcomes"
                ]
            candidate_trials = assessed_trials(root, candidate, store.trials(candidate["id"]))[0]
            link("Campaign comparison", "/static/datasets.html?campaign=" + args.id)
            return compare_campaigns(
                baseline,
                baseline_trials,
                candidate,
                candidate_trials,
                reference["strategy_id"],
                candidate["spec"]["strategies"][0],
            )

        def tool(name, description, schema, handler):
            def bounded(arguments):
                nonlocal calls
                calls += 1
                if calls > 16:
                    return {"error": "Tool call limit reached"}
                try:
                    args = schema.model_validate(arguments)
                    result = sanitize(handler(args))
                    if len(canonical_json(result).encode()) > 64_000:
                        return {
                            "error": "Result is too large; request a smaller page or narrower record"
                        }
                    return result
                except (KeyError, FileNotFoundError):
                    return {"error": "Referenced record is unavailable"}
                except (ValidationError, ValueError, TypeError):
                    return {
                        "error": "Invalid tool arguments or incompatible evaluation configuration"
                    }
                except Exception:
                    return {"error": "The requested read or preview could not be completed"}

            return RuntimeTool(
                name, description, schema.model_json_schema(), bounded, frozenset({"assistant"})
            )

        tools = (
            tool(
                "inspect_baseline",
                "Read the frozen outcome and measurement projection of an exact named baseline revision",
                Record,
                lambda a: BaselineStore(root).get(a.id),
            ),
            tool(
                "list_reviews",
                "Find existing review revisions for a case or trial; never write ratings",
                ReviewPage,
                lambda a: service.list_reviews(
                    a.case_revision_id, a.trial_id, limit=a.limit, offset=a.offset
                ),
            ),
            tool(
                "list_campaigns",
                "Find recorded campaigns for baseline selection",
                Page,
                lambda a: [
                    {"id": c["id"], "name": c["spec"]["name"], "status": c["status"]}
                    for c in CampaignStore(root).list()[a.offset : a.offset + a.limit]
                ],
            ),
            tool(
                "preview_import_case",
                "Prepare case metadata using an explicitly user-attached managed image",
                ImportDraft,
                lambda a: prepare_mutation("import_case", a),
            ),
            tool(
                "preview_revise_case",
                "Prepare a new immutable case revision using an exact current head",
                RevisionDraft,
                lambda a: prepare_mutation("revise_case", a),
            ),
            tool(
                "preview_contract",
                "Prepare a new immutable success contract or revision of one",
                ContractDraft,
                lambda a: prepare_mutation("create_contract", a),
            ),
            tool(
                "preview_freeze",
                "Prepare frozen membership from existing finalized reviews; never create judgments",
                FreezeDraft,
                lambda a: prepare_mutation("freeze_dataset", a),
            ),
            tool(
                "preview_baseline",
                "Prepare a named pinned campaign assessment baseline",
                BaselineDraft,
                lambda a: prepare_mutation("save_baseline", a),
            ),
            tool(
                "preview_ablation",
                "Prepare a comparable candidate against a recorded or named baseline",
                AblationDraft,
                lambda a: prepare_mutation("launch_ablation", a),
            ),
            tool(
                "list_baselines",
                "Read named baseline heads and frozen assessment identities",
                Arguments,
                lambda a: BaselineStore(root).list(),
            ),
            tool(
                "inspect_comparison",
                "Read actual baseline versus candidate outcomes and limitations",
                Record,
                inspect_comparison,
            ),
            tool(
                "inspect_dataset",
                "Read exact frozen membership and selected review identities",
                Record,
                lambda a: service.get_dataset(a.id),
            ),
            tool(
                "inspect_contract",
                "Read an immutable success contract",
                Record,
                lambda a: service.get_contract(a.id),
            ),
            tool(
                "inspect_review",
                "Read an existing review without creating judgments",
                Record,
                lambda a: service.get_review(a.id),
            ),
            tool(
                "list_cases",
                "List imported case revisions",
                Page,
                lambda a: service.list_cases(a.limit, a.offset),
            ),
            tool(
                "list_datasets",
                "List frozen dataset revisions",
                Page,
                lambda a: service.list_datasets(a.limit, a.offset),
            ),
            tool(
                "list_contracts",
                "List configured success contracts",
                Page,
                lambda a: service.list_contracts(a.limit, a.offset),
            ),
            tool(
                "list_strategies",
                "List strategy identifiers and stage assignments",
                Arguments,
                strategies,
            ),
            tool("inspect_case", "Read a case revision and readiness", Record, inspect_case),
            tool(
                "inspect_trial",
                "Read a trial's output and first 30 trace events",
                Record,
                inspect_trial,
            ),
            tool(
                "inspect_campaign",
                "Read measured campaign outcomes and limitations",
                Record,
                inspect_campaign,
            ),
            tool(
                "preview_campaign",
                "Validate an existing case/contract/strategy selection without launching",
                CampaignDraft,
                preview,
            ),
        )
        try:
            runtime = runtime_factory(runtime_settings, tools=tools)
            if not await runtime.health_check():
                raise HTTPException(
                    503, "Install the configured Copilot runtime to enable assistance"
                )
            output = await runtime.run_stage(
                "assist", "", request.message, request.context.model_dump(exclude_none=True)
            )
            answer = Answer.model_validate({k: v for k, v in output.items() if k != "_runtime"})
        except (Exception, asyncio.CancelledError) as error:
            # Do not orphan hidden proposals after invalid output, timeout or provider failure.
            for item in prepared:
                proposals.pop(item["operation_id"], None)
            if isinstance(error, asyncio.CancelledError | HTTPException):
                raise
            raise HTTPException(
                502, "Assistant could not complete this request; no campaign was launched"
            ) from error
        return {
            "answer": sanitize(answer.answer),
            "evidence": list(evidence.values()),
            "proposals": prepared,
        }

    @router.post("/api/assistant/confirm")
    async def confirm(request: ConfirmRequest):
        proposal = proposals.get(request.operation_id)
        if proposal is None or proposal.expires <= clock():
            raise HTTPException(409, "Preview expired or unavailable; prepare a new preview")
        if not hmac.compare_digest(request.confirmation_token, proposal.token):
            raise HTTPException(403, "Confirmation does not match the host preview")
        if proposal.launched:
            return proposal.result
        try:
            if proposal.kind != "launch_campaign":
                store = TrialStore(root)
                with store.connect() as db:
                    saved = db.execute(
                        "SELECT result FROM assistant_operations WHERE operation_id=?",
                        (request.operation_id,),
                    ).fetchone()
                if saved:
                    proposal.result, proposal.launched = json.loads(saved["result"]), True
                    return proposal.result
                with store.connect() as db:
                    committed = db.execute(
                        "SELECT result_id FROM workflow_mutations WHERE operation_id=?",
                        (request.operation_id,),
                    ).fetchone()
                    committed_baseline = db.execute(
                        "SELECT id FROM baseline_revisions WHERE operation_id=?",
                        (request.operation_id,),
                    ).fetchone()
                committed_campaign = False
                if proposal.kind == "launch_ablation":
                    from uuid import NAMESPACE_URL, uuid5

                    try:
                        CampaignStore(root).get(
                            uuid5(NAMESPACE_URL, "rove-campaign:" + request.operation_id).hex
                        )
                        committed_campaign = True
                    except KeyError:
                        pass
                if committed or committed_baseline or committed_campaign:
                    # A crash/queue failure after the source transaction must resume
                    # that exact approved operation, not revalidate a newer head.
                    payload = proposal.payload
                else:
                    payload, report = prepare_operation(
                        root, proposal.kind, proposal.request, config_loader(), request.operation_id
                    )
                    if not report["ready"] or content_hash(payload) != proposal.digest:
                        raise HTTPException(
                            409, "Prepared inputs or evidence changed; prepare a new preview"
                        )
                result = execute_operation(
                    root, proposal.kind, proposal.request, payload, request.operation_id, launch
                )
                with store.connect() as db:
                    db.execute(
                        "INSERT OR IGNORE INTO assistant_operations VALUES (?,?,?,?,?)",
                        (
                            request.operation_id,
                            proposal.kind,
                            proposal.digest,
                            canonical_json(result),
                            now(),
                        ),
                    )
                proposal.result, proposal.launched = result, True
                return result
            config = config_loader()
            payload, report = prepare_launch(root, proposal.request, config)
            if not report["ready"] or content_hash(payload) != proposal.digest:
                raise HTTPException(409, "Evaluation configuration changed; prepare a new preview")
            # Persist exactly the payload that was revalidated against the approval,
            # rather than preparing it again and risking a different runtime snapshot.
            identity = CampaignStore(root).create(
                payload, operation_id=proposal.request.operation_id
            )
            planned = report["planned_trials"]
            proposal.result = {
                "id": identity,
                "planned_trials": planned,
                "history_url": "/static/history.html",
            }
            # The campaign store durably deduplicates operation_id. A launch failure can
            # be retried with the same confirmation; an already running job is not relaunched.
            if CampaignStore(root).get(identity)["status"] == "pending":
                launch(identity)
            proposal.launched = True
            return proposal.result
        except HTTPException:
            raise
        except (KeyError, ValueError, FileNotFoundError) as error:
            raise HTTPException(409, "Evaluation inputs changed; prepare a new preview") from error
        except Exception as error:
            raise HTTPException(
                503, "Operation unavailable; retry this same confirmation"
            ) from error

    return router
