"""Local customer-data workflow; all mutations use the same validated services."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from rove.benchmarks.assistant_workflow import AblationDraft, prepare_ablation
from rove.benchmarks.baselines import BaselineInput, BaselineStore
from rove.benchmarks.comparison import compare_campaigns
from rove.benchmarks.metrics import summarize
from rove.benchmarks.store import CampaignStore
from rove.benchmarks.workflow import LaunchRequest, assessed_trials, create_campaign, prepare_launch
from rove.datasets.service import ConflictError, DatasetService
from rove.models.config import load_config
from rove.trials.store import TrialStore


@contextmanager
def errors():
    try:
        yield
    except ConflictError as error:
        raise HTTPException(409, str(error)) from error
    except (KeyError, FileNotFoundError) as error:
        raise HTTPException(
            404, "The referenced case, asset, revision or trial is unavailable"
        ) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


def create_dataset_router(root: Path, launch) -> APIRouter:
    router = APIRouter()

    def service():
        return DatasetService(root)

    async def upload(image: UploadFile) -> str:
        data = await image.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024:
            raise HTTPException(413, "Image exceeds 16 MiB")
        media = (
            "image/png"
            if data.startswith(b"\x89PNG\r\n\x1a\n")
            else "image/jpeg"
            if data.startswith(b"\xff\xd8\xff")
            else "application/octet-stream"
        )
        return TrialStore(root).save_asset(data, media)["sha256"]

    @router.post("/api/trials/{trial_id}/promote", status_code=201)
    async def promote(trial_id: str, payload: dict):
        from rove.datasets.promotion import promote_quick_trial

        with errors():
            if set(payload) - {"operation_id", "name"}:
                raise ValueError("Use operation_id and optional name")
            return promote_quick_trial(
                root, trial_id, operation_id=payload.get("operation_id"), name=payload.get("name")
            )

    @router.get("/api/cases")
    async def cases(
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        q: str = Query("", max_length=200),
        category: str = Query("", max_length=160),
    ):
        with errors():
            return service().search_cases(limit, offset, q=q, category=category)

    @router.post("/api/cases", status_code=201)
    async def import_case(image: UploadFile = File(...), metadata: str = Form(...)):
        with errors():
            if len(metadata.encode()) > 256 * 1024:
                raise HTTPException(413, "Case metadata exceeds 256 KiB")
            return service().import_case(json.loads(metadata), await upload(image))

    @router.post("/api/cases/examples", status_code=201)
    async def examples():
        directory = Path(__file__).resolve().parents[3] / "examples" / "customer-cases"
        with errors():
            records = json.loads((directory / "cases.json").read_text())
            imported = []
            for record in records:
                filename = record.pop("image")
                data = (directory / filename).read_bytes()
                digest = TrialStore(root).save_asset(data, "image/png")["sha256"]
                imported.append(service().import_case(record, digest))
            return {
                "cases": imported,
                "evidence_note": "Synthetic illustrations for testing the workflow; no robot was executed.",
            }

    @router.get("/api/cases/{case_id}")
    async def case(case_id: str):
        with errors():
            try:
                return service().get_case_revision(case_id)
            except KeyError:
                return service().get_case(case_id)

    @router.post("/api/cases/{case_id}/revisions", status_code=201)
    async def revise(
        case_id: str, metadata: str = Form(...), image: UploadFile | None = File(None)
    ):
        with errors():
            data = json.loads(metadata)
            if not isinstance(data, dict):
                raise ValueError("Case revision metadata must be a JSON object")
            if len(metadata.encode()) > 256 * 1024:
                raise ValueError("Case metadata exceeds 256 KiB")
            expected = data.pop("expected_head_revision_id", None)
            if not expected:
                raise ValueError("expected_head_revision_id is required")
            return service().revise_case(
                case_id,
                data,
                expected_head_revision_id=expected,
                image_sha256=await upload(image) if image else None,
            )

    @router.get("/api/success-contracts")
    async def contracts():
        return {"contracts": service().list_contracts(limit=1000)}

    @router.post("/api/success-contracts", status_code=201)
    async def contract(payload: dict):
        with errors():
            return service().create_contract(payload)

    @router.get("/api/reviews")
    async def reviews(
        case_revision_id: str | None = None,
        trial_id: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        with errors():
            return {
                "reviews": service().list_reviews(
                    case_revision_id, trial_id, limit=limit, offset=offset
                )
            }

    @router.post("/api/reviews", status_code=201)
    async def review(payload: dict):
        with errors():
            return service().create_review(payload)

    @router.patch("/api/reviews/{review_id}")
    async def update_review(review_id: str, payload: dict):
        with errors():
            expected = payload.pop("expected_revision", None)
            if type(expected) is not int:
                raise ValueError("expected_revision is required")
            return service().update_review(review_id, payload, expected_revision=expected)

    @router.get("/api/datasets")
    async def datasets(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        return {
            "datasets": service().list_datasets(limit, offset),
            "limit": limit,
            "offset": offset,
        }

    @router.get("/api/datasets/{revision_id}")
    async def dataset(revision_id: str):
        with errors():
            return service().get_dataset(revision_id)

    @router.post("/api/datasets/preview")
    async def preview_dataset(payload: dict):
        with errors():
            return service().preview_freeze(payload)

    @router.post("/api/datasets", status_code=201)
    async def freeze(payload: dict):
        with errors():
            expected = payload.pop("expected_preview_hash", None)
            if not expected:
                raise ValueError("Preview the exact dataset membership before freezing")
            return service().freeze(payload, expected_preview_hash=expected)

    @router.post("/api/campaigns/preview")
    async def preview(request: LaunchRequest):
        with errors():
            _, result = prepare_launch(root, request, load_config())
            return result

    @router.post("/api/campaigns/from-cases", status_code=202)
    async def create(request: LaunchRequest):
        with errors():
            campaign_id, planned = create_campaign(root, request, load_config())
            if CampaignStore(root).get(campaign_id)["status"] == "pending":
                launch(campaign_id)
            return {
                "id": campaign_id,
                "planned_trials": planned,
                "history_url": "/static/history.html",
            }

    @router.get("/api/campaigns/{campaign_id}/assessments")
    async def assessments(campaign_id: str):
        with errors():
            store = CampaignStore(root)
            campaign = store.get(campaign_id)
            trials, evidence = assessed_trials(root, campaign, store.trials(campaign_id))
            return {
                "campaign_id": campaign_id,
                "trials": trials,
                "summary": summarize(campaign, trials),
                "assessments": evidence,
            }

    @router.get("/api/baselines")
    async def named_baselines():
        return {"baselines": BaselineStore(root).list()}

    @router.get("/api/baselines/{baseline_id}")
    async def named_baseline(baseline_id: str):
        with errors():
            return BaselineStore(root).get(baseline_id)

    @router.get("/api/baselines/{baseline_id}/revisions")
    async def baseline_revisions(baseline_id: str):
        with errors():
            return {"revisions": BaselineStore(root).revisions(baseline_id)}

    @router.post("/api/baselines", status_code=201)
    async def save_baseline(request: BaselineInput):
        with errors():
            return BaselineStore(root).save(request)

    @router.patch("/api/baselines/{baseline_id}")
    async def revise_baseline(baseline_id: str, request: BaselineInput):
        with errors():
            return BaselineStore(root).save(request, baseline_id)

    def ablation(baseline_id: str, payload: dict):
        operation_id = payload.get("operation_id")
        args = AblationDraft.model_validate(
            {
                "baseline_campaign_id": baseline_id,
                **{key: value for key, value in payload.items() if key != "operation_id"},
            }
        )
        candidate, preview = prepare_ablation(root, args, load_config(), operation_id)
        return candidate, preview, preview["comparison"]

    @router.post("/api/campaigns/{baseline_id}/ablation-preview")
    async def preview_ablation(baseline_id: str, payload: dict):
        with errors():
            _, preview, comparison = ablation(baseline_id, payload)
            return {**preview, "comparison": comparison}

    @router.post("/api/campaigns/{baseline_id}/ablation", status_code=202)
    async def create_ablation(baseline_id: str, payload: dict):
        with errors():
            candidate, preview, comparison = ablation(baseline_id, payload)
            if not preview["ready"] or not comparison["comparable"]:
                raise ValueError("; ".join(preview["blockers"] + comparison["reasons"]))
            operation_id = payload.get("operation_id")
            if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 128:
                raise ValueError("A stable operation_id is required")
            campaign_id = CampaignStore(root).create(candidate, operation_id=operation_id)
            if CampaignStore(root).get(campaign_id)["status"] == "pending":
                launch(campaign_id)
            return {"id": campaign_id, "planned_trials": preview["planned_trials"]}

    @router.get("/api/campaigns/{candidate_id}/comparison")
    async def comparison(candidate_id: str):
        with errors():
            store = CampaignStore(root)
            candidate = store.get(candidate_id)
            reference = candidate.get("baseline")
            if not reference:
                raise ValueError("This campaign has no baseline reference")
            baseline = store.get(reference["campaign_id"])
            base_trials, base_assessments = assessed_trials(
                root, baseline, store.trials(baseline["id"])
            )
            if reference.get("revision_id"):
                named = BaselineStore(root).get(reference["revision_id"])
                base_trials = named["trial_outcomes"]
                base_assessments = named["assessments"]
            candidate_trials, candidate_assessments = assessed_trials(
                root, candidate, store.trials(candidate_id)
            )
            result = compare_campaigns(
                baseline,
                base_trials,
                candidate,
                candidate_trials,
                reference["strategy_id"],
                candidate["spec"]["strategies"][0],
            )
            return {
                **result,
                "baseline_trials": base_trials,
                "candidate_trials": candidate_trials,
                "baseline_assessments": base_assessments,
                "candidate_assessments": candidate_assessments,
            }

    return router
