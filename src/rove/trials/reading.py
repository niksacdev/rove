"""Authoritative trial views shared by local CLI and HTTP history."""

from rove.trials.store import TrialStore


def with_assessments(db: TrialStore, rows: list[dict]) -> list[dict]:
    from rove.benchmarks.store import CampaignStore
    from rove.benchmarks.workflow import assessed_trials

    campaigns = CampaignStore(db.root)
    projections = {}
    for row in rows:
        identity = row.get("campaign_id")
        if not identity or identity in projections:
            continue
        try:
            campaign = campaigns.get(identity)
        except KeyError:
            continue
        if not campaign.get("contract"):
            continue
        projected, metadata = assessed_trials(db.root, campaign, campaigns.trials(identity))
        projections[identity] = ({r.get("trial_id"): r for r in projected}, metadata)
    result = []
    for row in rows:
        if projection := projections.get(row.get("campaign_id")):
            outcomes, metadata = projection
            assessed = outcomes.get(row["id"], {})
            row = {
                **row,
                "assessment": {
                    **metadata,
                    "outcome": assessed.get("outcome", "unknown"),
                    "review_ids": assessed.get("assessment_ids", []),
                },
            }
        result.append(row)
    return result
