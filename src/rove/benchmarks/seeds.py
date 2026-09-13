"""Start a new campaign from saved inputs without inventing another historical attempt."""

from collections import Counter
from typing import Literal

from pydantic import Field

from rove.benchmarks.baselines import BaselineStore
from rove.benchmarks.metrics import summarize
from rove.benchmarks.store import CampaignStore
from rove.datasets.models import StrictModel
from rove.datasets.promotion import list_promotions, promote_quick_trial, public_trial_context
from rove.datasets.service import DatasetService
from rove.models.config import active_config_path, load_config
from rove.strategies.revisions import (
    RevisionConflict,
    fingerprint,
    restore_snapshot,
    validate_definition,
)
from rove.trials.snapshots import content_hash, sanitize


class SeedSource(StrictModel):
    type: Literal["trial", "campaign"]
    id: str = Field(min_length=1, max_length=128)
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class TrialSeedRequest(StrictModel):
    operation_id: str = Field(min_length=1, max_length=128)
    name: str | None = Field(default=None, min_length=1, max_length=160)


def _strategy_seed(frozen, definitions, source, config_path):
    selected, rows, blockers = [], [], []
    for sid, definition in definitions.items():
        config = load_config(config_path)
        row = {
            "id": sid,
            "selected_id": None,
            "definition": definition,
            "fingerprint": None,
            "current_fingerprint": None,
            "compatible": False,
        }
        try:
            strategy = validate_definition(definition, config)
            historical = content_hash(
                sanitize(
                    {
                        "definition": strategy.model_dump(mode="json"),
                        "endpoints": {
                            key: frozen.get("endpoints", {}).get(key)
                            for key in strategy.endpoint_refs()
                        },
                        "defaults": frozen.get("defaults"),
                    }
                )
            )
            row["fingerprint"] = historical
            row["current_fingerprint"] = fingerprint(strategy, config)
            if historical != row["current_fingerprint"]:
                raise RevisionConflict(
                    "Endpoint or default configuration changed since this result"
                )
            # A current strategy ID is reusable only if its entire definition still matches.
            if (
                sid in config.strategies
                and fingerprint(config.strategies[sid], config) == historical
            ):
                identity = sid
            else:
                identity = restore_snapshot(
                    config_path, source=source, definition=definition, frozen=frozen
                )["strategy_id"]
            row.update(selected_id=identity, compatible=True)
            selected.append(identity)
        except (ValueError, KeyError) as error:
            blockers.append(
                f"{sid}: {error}. Choose an explicit new strategy or restore its components."
            )
        rows.append(row)
    return selected, rows, blockers


def _recommendations(trials, strategies):
    recommendations = []
    for sid in strategies:
        rows = [trial for trial in trials if trial.get("strategy_id") == sid]
        unresolved = [
            trial
            for trial in rows
            if trial.get("outcome") == "unknown"
            and trial.get("execution") not in {"error", "timeout"}
        ]
        failures = [
            trial
            for trial in rows
            if trial.get("outcome") == "fail" or trial.get("execution") in {"error", "timeout"}
        ]
        stages = Counter((trial.get("result") or {}).get("failure_stage") for trial in failures)
        stages = Counter(
            {
                stage: count
                for stage, count in stages.items()
                if stage in {"perceive", "plan", "act", "verify", "sim"}
            }
        )
        if unresolved or not rows:
            stage, source = "verify", "missing_assessment"
            text = "Complete outstanding reviews and inspect missing outcome evidence before choosing a component to change. An unresolved result does not mean the agent performed poorly."
            cited = unresolved
        elif failures and stages:
            stage, source = stages.most_common(1)[0][0], "recorded_failure"
            text = f"Inspect the recorded {stage} failures. Test one {stage} component change as a hypothesis, keeping cases, criteria and attempt budget fixed."
            cited = [
                trial
                for trial in failures
                if (trial.get("result") or {}).get("failure_stage") == stage
            ]
        elif failures:
            stage, source = "verify", "recorded_outcome"
            text = "Inspect the failed trial evidence before choosing which component to change; the saved results do not identify a failure stage."
            cited = failures
        else:
            stage, source = None, "suggestion"
            text = "The recorded cases passed. Consider a separate campaign with harder held-out cases or a latency study; changed case coverage is a new assessment, not a same-condition ablation."
            cited = rows
        recommendations.append(
            {
                "strategy_id": sid,
                "stage": stage,
                "source": source,
                "text": text,
                "trial_ids": [trial["trial_id"] for trial in cited if trial.get("trial_id")][:30],
            }
        )
    return recommendations


def trial_seed(root, trial_id, request: TrialSeedRequest, *, config_path=None):
    service = DatasetService(root)
    trial = service.trials.get(trial_id)
    if trial["source"] != "quick" or trial["status"] == "running":
        raise ValueError("Choose a finished standalone trial to start a campaign")
    snapshot = trial.get("snapshot") or {}
    frozen = snapshot.get("config") or {}
    source = {"type": "trial", "id": trial_id, "snapshot_sha256": snapshot.get("sha256")}
    SeedSource.model_validate(source)
    case_id = trial["task"].get("case_revision_id")
    robot = frozen.get("robot_asset")
    warnings = [
        "The source trial remains a reference. A new campaign plans fresh attempts and does not count it again."
    ]
    if case_id:
        case = service.get_case_revision(case_id)
        if case["task"] != trial["task"].get("task") or case["image_asset"]["sha256"] != (
            trial["task"].get("image_asset") or {}
        ).get("sha256"):
            raise ValueError(
                "Source case does not match the saved trial observation and instruction"
            )
        if case.get("candidate_context", {}) != public_trial_context(trial, service):
            warnings.append(
                "A new reusable case preserves the exact public inputs and robot description used by this trial; the original case is unchanged."
            )
            case_id = None
    if not case_id:
        promoted = promote_quick_trial(
            root, trial_id, operation_id=request.operation_id, name=request.name
        )
        case_id = promoted["case_revision_id"]
    definitions = frozen.get("strategies") or {}
    selected, strategies, blockers = _strategy_seed(
        frozen, definitions, source, config_path or active_config_path()
    )
    if not definitions:
        blockers.append(
            "This older trial does not retain a complete strategy definition; select a strategy explicitly."
        )
    return {
        "case_revision_ids": [case_id],
        "strategies": selected,
        "source": source,
        "source_strategies": strategies,
        "robot_asset": robot,
        "defaults": {
            "name": request.name or "Campaign from trial",
            "seeds": [0, 1, 2],
            "ks": [1, 3],
            "timeout_s": 120,
        },
        "contract": None,
        "contract_id": None,
        "dataset_revision_id": None,
        "baselines": [],
        "recommendations": _recommendations(
            [
                {
                    "strategy_id": trial["strategy"].get("id"),
                    "result": trial.get("result"),
                    "outcome": "unknown",
                }
            ],
            definitions,
        ),
        "next_steps": [],
        "warnings": warnings,
        "blockers": blockers,
    }


def campaign_seed(root, campaign_id, *, config_path=None):
    from rove.benchmarks.workflow import assessed_trials

    store = CampaignStore(root)
    campaign = store.get(campaign_id)
    if campaign["status"] != "completed":
        raise ValueError("Improvement source must be a completed campaign")
    source = {
        "type": "campaign",
        "id": campaign_id,
        "snapshot_sha256": content_hash(sanitize(campaign)),
    }
    ids = campaign.get("case_revision_ids") or [
        task.get("case_revision_id") for task in campaign["spec"]["tasks"]
    ]
    selected, strategies, blockers = _strategy_seed(
        campaign["config"],
        campaign["config"]["strategies"],
        source,
        config_path or active_config_path(),
    )
    if not ids or any(not identity for identity in ids):
        blockers.append(
            "This older campaign does not retain reusable case revisions. Import its inputs before creating another campaign."
        )
        ids = []
    else:
        for identity in ids:
            DatasetService(root).get_case_revision(identity)
    trials, assessments = assessed_trials(root, campaign, store.trials(campaign_id))
    evidence_hash = content_hash(
        sanitize(
            {
                "campaign": campaign,
                "trials": trials,
                "assessments": assessments,
                "summary": summarize(campaign, trials),
            }
        )
    )
    next_steps = []
    with store.connect() as db:
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='campaign_insights'"
        ).fetchone():
            import json

            row = db.execute(
                "SELECT payload FROM campaign_insights WHERE campaign_id=? AND evidence_fingerprint=? ORDER BY created_at DESC LIMIT 1",
                (campaign_id, evidence_hash),
            ).fetchone()
            if row:
                next_steps = json.loads(row[0]).get("next_steps", [])
    spec = campaign["spec"]
    return {
        "case_revision_ids": ids,
        "strategies": selected,
        "source": source,
        "source_strategies": strategies,
        "robot_asset": None,
        "defaults": {
            "name": (spec["name"] + " · improvement")[:160],
            "seeds": spec["seeds"],
            "ks": spec["ks"],
            "timeout_s": spec["timeout_s"],
        },
        "contract": campaign.get("contract"),
        "contract_id": campaign.get("contract_id"),
        "dataset_revision_id": campaign.get("dataset_revision_id"),
        "baselines": [b for b in BaselineStore(root).list() if b["campaign_id"] == campaign_id],
        "recommendations": _recommendations(trials, spec["strategies"]),
        "next_steps": next_steps,
        "warnings": [
            "Source settings are retained for a new campaign. Changed cases, criteria or attempt budgets do not establish a comparable ablation."
        ],
        "blockers": blockers,
    }


def validate_source(root, source: SeedSource, case_ids, config, strategy_ids):
    """Resolve lineage from immutable records; browser-supplied hashes are never trusted."""
    service = DatasetService(root)
    if source.type == "trial":
        trial = service.trials.get(source.id)
        if trial["source"] != "quick" or trial["status"] == "running":
            raise ValueError("Source must be a finished standalone trial")
        if (trial.get("snapshot") or {}).get("sha256") != source.snapshot_sha256:
            raise ValueError("Source trial snapshot changed; reopen its campaign draft")
        known = {trial["task"].get("case_revision_id")}
        known.update(
            p["case_revision_id"] for p in list_promotions(root, trial_id=source.id, limit=1000)
        )
        if not set(case_ids) & (known - {None}):
            raise ValueError("The source trial's case must remain in the new campaign")
        frozen = trial["snapshot"]["config"]
        matching = [
            service.get_case_revision(identity) for identity in set(case_ids) & (known - {None})
        ]
        public = public_trial_context(trial, service)
        digest = (trial["task"].get("image_asset") or {}).get("sha256")
        if not any(
            case["task"] == trial["task"].get("task")
            and case["image_asset"]["sha256"] == digest
            and case.get("candidate_context", {}) == public
            for case in matching
        ):
            raise ValueError(
                "The source trial's exact observation, public inputs and robot description must remain in its selected case"
            )
    else:
        campaign = CampaignStore(root).get(source.id)
        if campaign["status"] != "completed":
            raise ValueError("Improvement source must be a completed campaign")
        if content_hash(sanitize(campaign)) != source.snapshot_sha256:
            raise ValueError("Source campaign changed; reopen its improvement draft")
        # Iterations may intentionally remove or replace cases. The immutable
        # source remains linked; comparison services report changed coverage.
        frozen = campaign["config"]
    # New strategy IDs are intentional changes. Reusing a historical ID may not
    # silently point at a changed definition, endpoint, or default configuration.
    for sid in set(strategy_ids) & frozen.get("strategies", {}).keys():
        definition = validate_definition(frozen["strategies"][sid], config)
        expected = content_hash(
            sanitize(
                {
                    "definition": definition.model_dump(mode="json"),
                    "endpoints": {
                        key: frozen["endpoints"].get(key) for key in definition.endpoint_refs()
                    },
                    "defaults": frozen.get("defaults"),
                }
            )
        )
        if sid not in config.strategies or fingerprint(config.strategies[sid], config) != expected:
            raise ValueError(
                "Historical strategy changed; select its saved revision or an explicit candidate revision"
            )
    return {
        **source.model_dump(),
        "role": "exploratory_reference" if source.type == "trial" else "improvement_source",
        "historical_trials_counted": False,
    }
