"""Typed preparation/execution of user-confirmed local workflow operations."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from rove.benchmarks.baselines import BaselineInput, BaselineStore
from rove.benchmarks.comparison import compare_campaigns
from rove.benchmarks.store import CampaignStore
from rove.benchmarks.workflow import LaunchRequest, prepare_launch
from rove.datasets.models import CaseInput, FreezeInput, SuccessContract
from rove.datasets.service import ConflictError, DatasetService
from rove.trials.snapshots import content_hash


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ImportDraft(Draft):
    metadata: CaseInput
    image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class RevisionDraft(ImportDraft):
    case_id: str = Field(min_length=1, max_length=128)
    expected_head_revision_id: str = Field(min_length=1, max_length=128)


class ContractDraft(Draft):
    contract: SuccessContract
    parent_contract_id: str | None = Field(default=None, min_length=1, max_length=128)


class FreezeDraft(Draft):
    dataset: FreezeInput


class BaselineDraft(Draft):
    name: str = Field(min_length=1, max_length=160)
    campaign_id: str = Field(min_length=1, max_length=128)
    strategy_id: str = Field(min_length=1, max_length=128)
    pinned: bool = True
    baseline_id: str | None = None
    expected_head_revision_id: str | None = None


class AblationDraft(Draft):
    baseline_campaign_id: str = Field(min_length=1, max_length=128)
    baseline_strategy_id: str = Field(min_length=1, max_length=128)
    candidate_strategy_id: str = Field(min_length=1, max_length=128)
    baseline_revision_id: str | None = None
    name: str = Field(default="Candidate ablation", min_length=1, max_length=160)
    intended_change: str = Field(default="", max_length=2000)


def prepare_ablation(root, args, config, operation_id):
    baseline = CampaignStore(root).get(args.baseline_campaign_id)
    if not baseline.get("case_revision_ids") or not baseline.get("contract_id"):
        raise ValueError("Ablations require versioned cases and a success contract")
    if args.baseline_strategy_id not in baseline["spec"]["strategies"]:
        raise ValueError("Choose a recorded baseline strategy")
    request = LaunchRequest(
        name=args.name,
        case_revision_ids=[]
        if baseline.get("dataset_revision_id")
        else baseline["case_revision_ids"],
        dataset_revision_id=baseline.get("dataset_revision_id"),
        contract_id=baseline["contract_id"],
        strategies=[args.candidate_strategy_id],
        seeds=baseline["spec"]["seeds"],
        ks=baseline["spec"]["ks"],
        timeout_s=baseline["spec"]["timeout_s"],
        operation_id=operation_id,
    )
    candidate, preview = prepare_launch(root, request, config)
    candidate["baseline"] = {
        "campaign_id": args.baseline_campaign_id,
        "strategy_id": args.baseline_strategy_id,
    }
    if args.baseline_revision_id:
        saved = BaselineStore(root).get(args.baseline_revision_id)
        if (
            saved["revision_id"] != args.baseline_revision_id
            or saved["campaign_id"] != args.baseline_campaign_id
            or saved["strategy_id"] != args.baseline_strategy_id
        ):
            raise ValueError("Named baseline revision does not match the campaign and strategy")
        candidate["baseline"]["revision_id"] = saved["revision_id"]
    candidate["intended_change"] = args.intended_change
    comparison = compare_campaigns(
        baseline, [], candidate, [], args.baseline_strategy_id, args.candidate_strategy_id
    )
    return candidate, {
        **preview,
        "comparison": comparison,
        "ready": preview["ready"] and comparison["comparable"],
    }


def prepare_operation(root, kind, body, config, operation_id):
    service = DatasetService(root)
    if kind in {"import_case", "revise_case"}:
        args = (ImportDraft if kind == "import_case" else RevisionDraft).model_validate(body)
        metadata = service.validate_case_payload(args.metadata.model_dump())
        asset = service._image(args.image_sha256)
        if kind == "import_case" and (
            metadata.get("reference_data") or metadata.get("recorded_evidence")
        ):
            raise ValueError(
                "Assistant imports cannot invent private labels or episode evidence; supply them through the case editor"
            )
        if kind == "revise_case":
            previous = service.get_case(args.case_id)
            if any(
                metadata.get(key, {}) != previous.get(key, {})
                for key in ("reference_data", "recorded_evidence")
            ):
                raise ValueError(
                    "Assistant revisions preserve existing private labels and recorded evidence exactly"
                )
            if previous["head_revision_id"] != args.expected_head_revision_id:
                raise ConflictError("Case changed; prepare the latest revision")
        payload = {**args.model_dump(), "metadata": metadata, "asset": asset}
        return payload, {
            "ready": True,
            "operation": kind,
            "case": metadata,
            "image_asset": asset,
            "note": "Private reference data stays outside candidate context. No SME judgment is created.",
        }
    if kind == "create_contract":
        args = ContractDraft.model_validate(body)
        previous = (
            service.get_contract(args.parent_contract_id) if args.parent_contract_id else None
        )
        payload = args.model_dump()
        return payload, {
            "ready": True,
            "operation": kind,
            "contract": payload["contract"],
            "parent_contract_id": args.parent_contract_id,
            "note": "Creates a new immutable success contract; existing campaigns retain their original criteria.",
            "previous_contract": previous,
        }
    if kind == "freeze_dataset":
        args = FreezeDraft.model_validate(body)
        for member in args.dataset.members:
            if member.disposition == "included" and not member.review_ids:
                raise ValueError(
                    "Assistant freezing requires explicit existing review revisions for every included case"
                )
            for review_id in member.review_ids:
                if service.get_review(review_id)["status"] != "final":
                    raise ValueError(
                        "Only finalized existing reviews may enter an assistant freeze"
                    )
        preview = service.preview_freeze(args.dataset.model_dump())
        payload = {**args.model_dump(), "expected_preview_hash": preview["preview_hash"]}
        return payload, {
            **preview,
            "ready": True,
            "operation": kind,
            "note": "Freezes only the displayed case and existing review revisions. No ratings or reusable labels are invented.",
        }
    if kind == "save_baseline":
        args = BaselineDraft.model_validate(body)
        request = BaselineInput(
            **args.model_dump(exclude={"baseline_id"}), operation_id=operation_id
        )
        if (
            args.baseline_id
            and BaselineStore(root).get(args.baseline_id)["revision_id"]
            != args.expected_head_revision_id
        ):
            raise ConflictError("Named baseline changed; prepare again")
        capture = BaselineStore(root).capture(request)
        return {
            "request": request.model_dump(),
            "baseline_id": args.baseline_id,
            "capture_hash": content_hash(capture),
        }, {**capture, "ready": True, "operation": kind}
    if kind == "launch_ablation":
        return prepare_ablation(root, AblationDraft.model_validate(body), config, operation_id)
    raise ValueError("Unsupported operation")


def execute_operation(root, kind, body, payload, operation_id, launch):
    service = DatasetService(root)
    if kind == "import_case":
        result = service.import_case(
            payload["metadata"], payload["image_sha256"], operation_id=operation_id
        )
    elif kind == "revise_case":
        result = service.revise_case(
            payload["case_id"],
            payload["metadata"],
            expected_head_revision_id=payload["expected_head_revision_id"],
            image_sha256=payload["image_sha256"],
            operation_id=operation_id,
        )
    elif kind == "create_contract":
        result = {
            **service.create_contract(payload["contract"]),
            "parent_contract_id": payload["parent_contract_id"],
        }
    elif kind == "freeze_dataset":
        result = service.freeze(
            payload["dataset"],
            expected_preview_hash=payload["expected_preview_hash"],
            operation_id=operation_id,
        )
    elif kind == "save_baseline":
        result = BaselineStore(root).save(
            BaselineInput.model_validate(payload["request"]),
            payload["baseline_id"],
            expected_capture_hash=payload["capture_hash"],
        )
    elif kind == "launch_ablation":
        identity = CampaignStore(root).create(payload, operation_id=operation_id)
        if CampaignStore(root).get(identity)["status"] == "pending":
            launch(identity)
        result = {
            "id": identity,
            "planned_trials": len(payload["spec"]["tasks"])
            * len(payload["spec"]["seeds"])
            * len(payload["spec"]["strategies"]),
        }
    else:
        raise ValueError("Unsupported operation")
    return {"kind": kind, **result}
