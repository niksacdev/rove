"""Read-only iteration history from persisted campaign lineage, never name similarity."""

from __future__ import annotations

from collections import defaultdict

from rove.benchmarks.baselines import BaselineStore
from rove.benchmarks.comparison import compare_campaigns
from rove.benchmarks.metrics import summarize
from rove.benchmarks.store import CampaignStore, now
from rove.models.config import StrategyConfig
from rove.trials.snapshots import content_hash, sanitize


def _hash(value):
    return content_hash(sanitize(value))


def _changes(before, after):
    def selected(c):
        config = c.get("config", {})
        return {"strategies": config.get("strategies"), "endpoints": config.get("endpoints")}

    fields = {
        "cases": (
            [
                before["spec"].get("tasks"),
                before.get("case_revision_ids"),
                before.get("dataset_revision_id"),
                before["spec"].get("suite_version"),
            ],
            [
                after["spec"].get("tasks"),
                after.get("case_revision_ids"),
                after.get("dataset_revision_id"),
                after["spec"].get("suite_version"),
            ],
        ),
        "success_metrics": (
            [
                before.get("contract"),
                before.get("contract_id"),
                before.get("metric_scope"),
                before.get("local_evaluators"),
                before["spec"].get("grading"),
            ],
            [
                after.get("contract"),
                after.get("contract_id"),
                after.get("metric_scope"),
                after.get("local_evaluators"),
                after["spec"].get("grading"),
            ],
        ),
        "strategies": (selected(before), selected(after)),
        "repetitions": (before["spec"].get("seeds"), after["spec"].get("seeds")),
        "environment": (
            [
                before.get("runtime"),
                before.get("config", {}).get("defaults"),
                before["spec"].get("timeout_s"),
            ],
            [
                after.get("runtime"),
                after.get("config", {}).get("defaults"),
                after["spec"].get("timeout_s"),
            ],
        ),
    }
    return [name for name, (old, new) in fields.items() if _hash(old) != _hash(new)]


def _strategy_fingerprint(campaign, definition):
    config = campaign.get("config", {})
    try:
        refs = StrategyConfig.model_validate(definition).endpoint_refs()
    except ValueError:
        return None
    return _hash(
        {
            "definition": definition,
            "endpoints": {key: config.get("endpoints", {}).get(key) for key in refs},
            "model_versions": {key: campaign.get("model_versions", {}).get(key) for key in refs},
            "defaults": config.get("defaults"),
        }
    )


def _outcomes(campaign, trials):
    result = summarize(campaign, trials)
    rows = []
    for strategy in result["strategies"]:
        tasks = [row for row in result["tasks"] if row["strategy_id"] == strategy["strategy_id"]]
        rows.append(
            {
                **strategy,
                **{key: sum(row[key] for row in tasks) for key in ("passed", "failed", "unknown")},
            }
        )
    return rows


def campaign_timeline(root, campaign_id):
    """Return the connected lineage component; invalid/missing references remain explicit."""
    from rove.benchmarks.workflow import assessed_trials

    store = CampaignStore(root)
    store.get(campaign_id)
    with store.connect() as db:
        if db.execute("SELECT count(*) FROM campaigns").fetchone()[0] > 2000:
            raise ValueError(
                "Timeline currently supports up to 2,000 stored campaigns; use an isolated study store"
            )
    campaigns = {c["id"]: c for c in store.list()}
    adjacency, raw_edges, warnings = defaultdict(set), [], defaultdict(list)
    baseline_store = BaselineStore(root)
    for child in campaigns.values():
        source, reference = child.get("source") or {}, child.get("baseline") or {}
        parent_id = reference.get("campaign_id") or (
            source.get("id") if source.get("type") == "campaign" else None
        )
        if not parent_id:
            continue
        parent = campaigns.get(parent_id)
        if not parent or parent_id == child["id"]:
            warnings[child["id"]].append(
                "Source campaign is missing or self-referential; lineage was not joined."
            )
            continue
        saved = None
        if reference:
            try:
                saved = baseline_store.get(reference["revision_id"])
                if (
                    saved["revision_id"] != reference["revision_id"]
                    or saved["campaign_id"] != parent_id
                    or saved["strategy_id"] != reference.get("strategy_id")
                    or saved["campaign_hash"] != content_hash(parent)
                ):
                    raise ValueError("Baseline reference does not match its saved campaign")
            except (KeyError, ValueError):
                warnings[child["id"]].append(
                    "Exact saved baseline could not be validated; lineage was not joined."
                )
                continue
        elif source.get("snapshot_sha256") != _hash(parent):
            warnings[child["id"]].append(
                "Source campaign snapshot differs; lineage was not joined."
            )
            continue
        adjacency[parent_id].add(child["id"])
        adjacency[child["id"]].add(parent_id)
        raw_edges.append((parent_id, child["id"], saved))
    component, queue = set(), [campaign_id]
    while queue:
        identity = queue.pop()
        if identity in component:
            continue
        component.add(identity)
        queue.extend(adjacency[identity] - component)
        if len(component) > 200:
            raise ValueError("Connected timeline exceeds 200 campaigns; use a smaller study store")
    assessed, nodes = {}, []
    for identity in sorted(component, key=lambda key: (campaigns[key]["created_at"], key)):
        campaign = campaigns[identity]
        trials, assessments = assessed_trials(root, campaign, store.trials(identity))
        assessed[identity] = trials
        definitions = campaign.get("config", {}).get("strategies", {})
        nodes.append(
            {
                "id": identity,
                "name": campaign["spec"]["name"],
                "created_at": campaign["created_at"],
                "status": campaign["status"],
                "revision": campaign["spec"].get("revision"),
                "assessment_scope": "current_assessments",
                "evidence_kind": campaign.get("evidence_kind", "unknown"),
                "case_count": len(campaign["spec"]["tasks"]),
                "repetitions": len(campaign["spec"]["seeds"]),
                "contract_id": campaign.get("contract_id"),
                "case_revision_ids": campaign.get("case_revision_ids", []),
                "strategy_versions": [
                    {
                        "id": key,
                        "name": value.get("display_name") or key,
                        "fingerprint": _strategy_fingerprint(campaign, value),
                    }
                    for key, value in definitions.items()
                ],
                "assessment_set_hash": assessments.get("assessment_set_hash"),
                "outcomes": _outcomes(campaign, trials),
                "warnings": warnings[identity],
                "report_url": f"/api/campaigns/{identity}/report?format=html",
                "results_url": f"/static/datasets.html?step=review&campaign={identity}",
            }
        )
    edges = []
    for parent_id, child_id, saved in raw_edges:
        if parent_id not in component or child_id not in component:
            continue
        parent, child = campaigns[parent_id], campaigns[child_id]
        parents, children = parent["spec"]["strategies"], child["spec"]["strategies"]
        pairs = (
            [(saved["strategy_id"], candidate) for candidate in children]
            if saved
            else [(strategy, strategy) for strategy in children if strategy in parents]
            if len(parents) > 1
            else [(parents[0], candidate) for candidate in children]
        )
        comparisons = []
        for old, new in pairs:
            result = compare_campaigns(
                parent,
                saved["trial_outcomes"] if saved else assessed[parent_id],
                child,
                assessed[child_id],
                old,
                new,
            )
            comparisons.append(
                {
                    "baseline_strategy_id": old,
                    "candidate_strategy_id": new,
                    **{
                        key: result.get(key)
                        for key in (
                            "comparable",
                            "reasons",
                            "paired_counts",
                            "paired_coverage",
                            "scope",
                            "evidence_note",
                        )
                    },
                    "baseline_outcomes": _outcomes(
                        {**parent, "spec": {**parent["spec"], "strategies": [old]}},
                        saved["trial_outcomes"] if saved else assessed[parent_id],
                    ),
                }
            )
        unmatched = [
            strategy for strategy in children if not any(pair[1] == strategy for pair in pairs)
        ]
        changes = _changes(parent, child)
        reasons = list(dict.fromkeys(reason for item in comparisons for reason in item["reasons"]))
        if unmatched:
            reasons.append(
                "Choose an exact baseline strategy to compare new strategy identities from a multi-strategy source."
            )
        comparable = (
            bool(comparisons) and not unmatched and all(item["comparable"] for item in comparisons)
        )
        edges.append(
            {
                "parent_id": parent_id,
                "campaign_id": child_id,
                "relationship": "frozen_baseline" if saved else "source_campaign",
                "baseline_revision_id": saved["revision_id"] if saved else None,
                "changes": changes,
                "comparable": comparable,
                "reasons": reasons,
                "comparisons": comparisons,
                "unmatched_strategies": unmatched,
            }
        )
    return {
        "campaign_id": campaign_id,
        "as_of": now(),
        "nodes": nodes,
        "edges": edges,
        "meaning": "Connected saved iterations. Node outcomes use current assessments; baseline edges use frozen reference outcomes. Changed conditions do not establish improvement. No significance or hardware-performance claim.",
        "source": "persisted_campaign_lineage",
    }
