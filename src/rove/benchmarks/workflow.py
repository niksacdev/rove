"""Customer evaluation services shared by HTTP and future assistant tools."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rove.benchmarks.models import CampaignSpec, fingerprint
from rove.benchmarks.runner import prepare
from rove.benchmarks.seeds import SeedSource, validate_source
from rove.benchmarks.store import CampaignStore
from rove.datasets.service import DatasetService
from rove.models.config import RoveConfig


class LaunchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="Customer evaluation", min_length=1, max_length=160)
    revision: str = Field(default="customer-evaluation", min_length=1, max_length=160)
    case_revision_ids: list[str] = Field(default_factory=list, max_length=1000)
    dataset_revision_id: str | None = None
    contract_id: str = Field(min_length=1, max_length=128)
    strategies: list[str] = Field(min_length=1, max_length=20)
    seeds: list[int] = Field(default=[1, 2, 3], min_length=1, max_length=1000)
    ks: list[int] = Field(default=[1, 3], min_length=1, max_length=100)
    timeout_s: float = Field(default=120, ge=1, le=3600, allow_inf_nan=False)
    operation_id: str | None = Field(default=None, min_length=1, max_length=128)
    source: SeedSource | None = None
    baseline_revision_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def selection_source(self):
        if bool(self.case_revision_ids) == bool(self.dataset_revision_id):
            raise ValueError("Choose exact case revisions or a frozen dataset")
        if len(set(self.case_revision_ids)) != len(self.case_revision_ids):
            raise ValueError("Duplicate case revisions")
        return self


def prepare_launch(root: Path, request: LaunchRequest, config: RoveConfig) -> tuple[dict, dict]:
    service = DatasetService(root)
    contract = service.get_contract(request.contract_id)
    ids = request.case_revision_ids
    dataset = None
    bindings = contract.get("annotation_bindings", [])
    if bindings and not request.dataset_revision_id:
        raise ValueError("Annotation bindings require an exact frozen dataset revision")
    if request.dataset_revision_id:
        dataset = service.get_dataset(request.dataset_revision_id)
        if dataset["contract_id"] != request.contract_id:
            raise ValueError("Dataset and campaign must use the same success contract")
        ids = [m["case_revision_id"] for m in dataset["members"] if m["disposition"] == "included"]
    cases = [service.get_case_revision(identity) for identity in ids]
    tasks = []
    for case in cases:
        service.validate_case_image(case["id"])
        service.validate_case_payload(
            {
                key: case.get(key, {})
                for key in (
                    "name",
                    "task",
                    "candidate_context",
                    "conditions",
                    "recorded_evidence",
                    "reference_data",
                )
            }
        )
        extras = dict(case.get("candidate_context") or {})
        if robot := extras.get("robot_asset"):
            from rove.datasets.robot_assets import robot_asset_path

            robot_asset_path(service.trials, robot)
        recorded = case.get("recorded_evidence") or {}
        if recorded:
            extras["episode"] = recorded.get("episode", recorded)
        if bindings:
            extras["grader_context"] = service.resolve_annotations(dataset, case["id"], bindings)
        if contract["evidence_mode"] == "synthetic_rollout":
            environment = case.get("conditions", {}).get("synthetic_environment")
            if environment is None:
                raise ValueError("Synthetic rollout cases require conditions.synthetic_environment")
            extras["synthetic_environment"] = environment
            extras["case_revision_id"] = case["id"]
        tasks.append(
            {
                "id": case["id"],
                "case_revision_id": case["id"],
                "task": case["task"],
                "image_asset": case["image_asset"]["sha256"],
                "example": {"extras": extras},
            }
        )
    spec = CampaignSpec(
        name=request.name,
        suite_version=request.dataset_revision_id or fingerprint(ids),
        revision=request.revision,
        strategies=request.strategies,
        tasks=tasks,
        seeds=request.seeds,
        ks=request.ks,
        timeout_s=request.timeout_s,
    )
    source = (
        validate_source(root, request.source, ids, config, request.strategies)
        if request.source
        else None
    )
    payload = prepare(spec, config)
    if source:
        payload["source"] = source
    payload.update(
        contract_id=request.contract_id,
        contract=contract,
        dataset_revision_id=request.dataset_revision_id,
        case_revision_ids=ids,
        metric_scope="success_contract",
        annotation_review_ids=sorted(
            {
                identity
                for task in tasks
                for bound in task["example"]["extras"].get("grader_context", {}).values()
                for identity in bound["review_ids"]
            }
        ),
    )
    comparisons = []
    if request.baseline_revision_id:
        from rove.benchmarks.baselines import BaselineStore
        from rove.benchmarks.comparison import compare_campaigns
        from rove.trials.snapshots import content_hash

        if not request.source or request.source.type != "campaign":
            raise ValueError(
                "A saved baseline requires its source campaign in this improvement draft"
            )
        saved = BaselineStore(root).get(request.baseline_revision_id)
        baseline = CampaignStore(root).get(request.source.id)
        if (
            saved["revision_id"] != request.baseline_revision_id
            or saved["campaign_id"] != request.source.id
            or saved["strategy_id"] not in baseline["spec"]["strategies"]
            or saved["campaign_hash"] != content_hash(baseline)
        ):
            raise ValueError("Saved baseline revision does not match this exact source campaign")
        payload["baseline"] = {
            "campaign_id": request.source.id,
            "strategy_id": saved["strategy_id"],
            "revision_id": saved["revision_id"],
        }
        comparisons = [
            {
                "candidate_strategy_id": identity,
                **compare_campaigns(
                    baseline, saved["trial_outcomes"], payload, [], saved["strategy_id"], identity
                ),
            }
            for identity in request.strategies
        ]
    blockers = []
    strategies = []
    criteria = contract["criteria"]
    human = any(c["assessment"] == "human_review" and c["required"] for c in criteria)
    for sid in request.strategies:
        strategy = config.strategies[sid]
        if contract["evidence_mode"] == "synthetic_rollout" and (
            not strategy.act
            or not strategy.sim
            or config.endpoints[strategy.sim].adapter != "synthetic_sim"
        ):
            blockers.append(f"{sid}: synthetic rollout requires an act stage and synthetic_sim")
        from rove.adapters.registry import STAGE_TO_CAPABILITY

        for stage in ("perceive", "plan", "act", "verify"):
            options = strategy.stage_options(stage)
            if options is None:
                continue
            endpoint = config.endpoints[options.endpoint]
            # Match AdapterRegistry's stage/type contract without instantiating an
            # adapter (which may load weights or contact a provider during preview).
            if stage == "act" and endpoint.type == "vla":
                continue
            eligible = (
                endpoint.type == "agent"
                or (stage != "act" and endpoint.type in {"vlm", "llm"})
                or (
                    stage == "verify"
                    and endpoint.type == "verifier"
                    and endpoint.adapter != "forward_kinematics"
                )
            )
            if not eligible:
                blockers.append(
                    f"{sid}: {options.endpoint} type {endpoint.type} cannot serve {stage}"
                )
                continue
            capability = STAGE_TO_CAPABILITY.get(stage)
            if capability and capability not in endpoint.capabilities:
                blockers.append(
                    f"{sid}: {options.endpoint} lacks {capability} capability for {stage}"
                )
        verify = strategy.stage_options("verify")
        bindings = {
            verify.endpoint,
            *(c.endpoint for c in verify.checks if c.required and c.role == "constraint"),
        }
        for binding in contract.get("annotation_bindings", []):
            endpoint = config.endpoints.get(binding["endpoint"])
            if (
                binding["endpoint"] not in bindings
                or endpoint is None
                or endpoint.adapter != "local_verifier"
            ):
                blockers.append(
                    f"{sid}: annotation binding requires a participating configured local verifier"
                )
        for criterion in criteria:
            if (
                criterion["required"]
                and criterion["assessment"] == "configured_verifier"
                and criterion["endpoint"] not in bindings
            ):
                blockers.append(
                    f"{sid}: criterion {criterion['id']} is not bound to its task verifier or a required constraint"
                )
        strategies.append(
            {
                "id": sid,
                "display_name": strategy.display_name or sid,
                "verify": verify.endpoint,
                "checks": [c.model_dump() for c in verify.checks],
            }
        )
    if contract["scope"] == "episode_outcome" and contract["evidence_mode"] == "candidate_output":
        blockers.append(
            "Images and model outputs cannot establish observed episode completion. Choose an output assessment or supply episode evidence."
        )
    if contract["evidence_mode"] == "recorded_episode" and any(
        not case.get("recorded_evidence") for case in cases
    ):
        blockers.append("Every recorded-episode case needs explicitly supplied evidence")
    if contract["evidence_mode"] == "recorded_episode":
        from rove.datasets.robotics import EpisodeRecording

        for case in cases:
            try:
                EpisodeRecording.model_validate(case.get("recorded_evidence", {}))
            except ValueError:
                blockers.append(
                    f"{case['id']}: recorded episodes require versioned schema, identities, units, frame and available evidence"
                )
    episode_bound = any(
        config.endpoints.get(c.get("endpoint")) is not None
        and config.endpoints[c["endpoint"]].config.get("entrypoint")
        == "rove.evaluators.robotics:episode_outcome"
        for c in criteria
        if c.get("assessment") == "configured_verifier"
    )
    metrics = []
    for metric in contract["metrics"]:
        status, reason = "available", "Recorded by the configured trial workflow"
        if metric in {"task_success", "pass_at_k", "pass_pow_k"} and human:
            status, reason = (
                "needs_review",
                "Required SME ratings are collected per trial; pending or disputed assessments remain unknown",
            )
        if metric in {"episode_completion_time", "autonomous_completion", "recovery"}:
            if contract["evidence_mode"] == "synthetic_rollout":
                status, reason = (
                    "available" if episode_bound else "conditional",
                    "Synthetic act/sim evidence must be consumed by the configured episode verifier; completion time requires success and recovery needs actual opportunities",
                )
            elif (
                contract["evidence_mode"] == "recorded_episode"
                and cases
                and all(
                    case.get("recorded_evidence", {}).get("schema_version") == 1 for case in cases
                )
            ):
                status, reason = (
                    "conditional",
                    "Available only for supplied episode measurements and explicit denominators; archived evidence is regrading",
                )
            else:
                status, reason = (
                    "unavailable",
                    "Requires associated episode measurements and explicit denominators; not inferred from model latency or text",
                )
        if (
            metric == "recovery"
            and contract["evidence_mode"] == "synthetic_rollout"
            and not any(
                case.get("conditions", {}).get("synthetic_environment", {}).get("disturbance_step")
                for case in cases
            )
        ):
            status, reason = (
                "unavailable",
                "Selected synthetic cases declare no recovery opportunities",
            )
        if metric in {"pass_at_k", "pass_pow_k"} and max(request.ks) > len(request.seeds):
            reason += "; requested k values above the repetition count will be unavailable"
        metrics.append({"name": metric, "status": status, "reason": reason})
    note = (
        "Recorded episodes assess existing evidence; changing the candidate does not demonstrate new physical performance."
        if contract["evidence_mode"] == "recorded_episode"
        else "Candidate actions execute in the deterministic synthetic environment; results are synthetic, not physical robot performance."
        if contract["evidence_mode"] == "synthetic_rollout"
        else "Scores describe the declared output assessment. They do not prove that a robot executed the task."
    )
    return payload, {
        "ready": not blockers,
        "blockers": blockers,
        "planned_trials": spec.planned_trials,
        "case_revision_ids": ids,
        "contract": contract,
        "campaign_targets": contract.get("campaign_targets", []),
        "strategies": strategies,
        "metrics": metrics,
        "evidence_note": note,
        "baseline": payload.get("baseline"),
        "comparisons": comparisons,
    }


def create_campaign(root: Path, request: LaunchRequest, config: RoveConfig) -> tuple[str, int]:
    if not request.operation_id:
        raise ValueError("A stable operation_id is required to launch a campaign")
    payload, preview = prepare_launch(root, request, config)
    if not preview["ready"]:
        raise ValueError("; ".join(preview["blockers"]))
    campaign_id = CampaignStore(root).create(payload, operation_id=request.operation_id)
    return campaign_id, preview["planned_trials"]


def assessed_trials(root: Path, campaign: dict, trials: list[dict]) -> tuple[list[dict], dict]:
    """Project immutable output assessments without inventing additional executions."""
    contract = campaign.get("contract")
    if not contract:
        return trials, {"scope": "configured_verification", "review_ids": []}
    required = [c for c in contract["criteria"] if c["required"]]
    human_ids = {c["id"] for c in required if c["assessment"] == "human_review"}
    service = DatasetService(root)
    rows, used = [], list(campaign.get("annotation_review_ids", []))
    for trial in trials:
        reviews = []
        if human_ids and trial.get("trial_id"):
            # Grading must not silently ignore older active dissent or successors
            # simply because the history API paginates its presentation.
            while True:
                page = service.list_reviews(
                    trial_id=trial["trial_id"], limit=1000, offset=len(reviews)
                )
                reviews.extend(page)
                if len(page) < 1000:
                    break
        reviews = [
            r
            for r in reviews
            if r["status"] == "final"
            and r["contract_id"] == campaign["contract_id"]
            and r["target_type"] == "trial_output"
        ]
        superseded = {r["supersedes_id"] for r in reviews if r.get("supersedes_id")}
        reviews = [r for r in reviews if r["id"] not in superseded]
        used.extend(r["id"] for r in reviews)
        votes = []
        for review in reviews:
            values = [review["criteria"].get(key, "unknown") for key in human_ids]
            vote = (
                "pass"
                if review["decision"] == "accepted" and all(v == "accepted" for v in values)
                else "fail"
                if review["decision"] == "rejected" or "rejected" in values
                else "unknown"
            )
            votes.append(vote)
        components = (
            [votes[0] if votes and len(set(votes)) == 1 else "unknown"] if human_ids else []
        )
        definition = campaign.get("strategy_definitions", {}).get(trial["strategy_id"], {})
        from rove.models.config import StrategyConfig
        from rove.models.verification import CheckResult

        verify_options = (
            StrategyConfig.model_validate(definition).stage_options("verify")
            if definition
            else None
        )
        stage = next(
            (
                s
                for s in (trial.get("result") or {}).get("stages", [])
                if s.get("stage") == "verify" and s.get("status") == "completed"
            ),
            {},
        )
        verify_output = stage.get("output") or {}
        supplied = verify_output.get("check_results", [])
        checks = {}
        for item in supplied:
            try:
                check = CheckResult.model_validate(item)
            except ValueError:
                continue
            if check.endpoint in checks:
                checks[check.endpoint] = None  # Conflicting/duplicate evidence is unresolved.
            else:
                checks[check.endpoint] = check
        bound = {}
        if verify_options:
            if (
                stage.get("model_id") == verify_options.endpoint
                and verify_output.get("verdict_valid", True)
                and type(verify_output.get("success")) is bool
            ):
                bound[verify_options.endpoint] = "pass" if verify_output["success"] else "fail"
            for configured in verify_options.checks:
                if not configured.required:
                    continue
                check = checks.get(configured.endpoint)
                valid = (
                    check is not None
                    and check.execution == "completed"
                    and check.role == configured.role
                    and check.required
                    and check.result.evidence_quality != "unknown"
                )
                # Diagnostics provide evidence, not a binary task-success vote.
                gate = (
                    check.result.verdict
                    if valid and configured.role == "constraint"
                    else "pass"
                    if valid
                    else "unknown"
                )
                components.append(gate)
                if configured.role == "constraint":
                    bound[configured.endpoint] = gate
        for criterion in required:
            if criterion["assessment"] == "configured_verifier":
                components.append(bound.get(criterion["endpoint"], "unknown"))
        outcome = (
            "fail"
            if "fail" in components
            else "pass"
            if components and all(v == "pass" for v in components)
            else "unknown"
        )
        # Human judgement cannot turn an absent/failed execution into an observed output.
        if trial.get("execution") not in {"completed", "invalid_verdict", "unresolved_evidence"}:
            outcome = "unknown"
        rows.append(
            {
                **trial,
                "outcome": outcome,
                "configured_outcome": trial["outcome"],
                "assessment_ids": [r["id"] for r in reviews],
                "annotation_review_ids": campaign.get("annotation_review_ids", []),
            }
        )
    used.sort()
    return rows, {
        "scope": "success_contract",
        "review_ids": used,
        "assessment_set_hash": fingerprint(used),
        "note": "Contract outcomes use the bound verifier evidence and required SME ratings for each recorded output. Ratings are not additional trials or labels for future outputs.",
    }
