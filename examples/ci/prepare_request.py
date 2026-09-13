"""Map a reviewed saved study to an explicit candidate request; never generate grading rules."""

import argparse
import json
from pathlib import Path


def prepare_request(seed, baseline_revision, strategy):
    references = {item["revision_id"]: item for item in seed.get("baselines", [])}
    if baseline_revision not in references:
        raise ValueError("Choose an exact baseline revision returned for this source campaign")
    if not seed.get("case_revision_ids") or not seed.get("contract_id"):
        raise ValueError("The source must retain reusable cases and a saved success contract")
    if not (seed.get("contract") or {}).get("campaign_targets"):
        raise ValueError("Review and save campaign targets before using --require-targets in CI")
    source = seed["source"]
    if source["type"] != "campaign":
        raise ValueError("CI improvement requires a saved source campaign")
    return {
        "name": seed["defaults"]["name"],
        **(
            {"dataset_revision_id": seed["dataset_revision_id"]}
            if seed.get("dataset_revision_id")
            else {"case_revision_ids": seed["case_revision_ids"]}
        ),
        "contract_id": seed["contract_id"],
        "strategies": [strategy],
        "seeds": seed["defaults"]["seeds"],
        "ks": seed["defaults"]["ks"],
        "timeout_s": seed["defaults"]["timeout_s"],
        "source": source,
        "baseline_revision_id": baseline_revision,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seed", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--strategy", required=True)
    args = parser.parse_args()
    try:
        request = prepare_request(json.loads(args.seed.read_text()), args.baseline, args.strategy)
    except (ValueError, KeyError) as error:
        parser.error(str(error))
    args.output.write_text(json.dumps(request, indent=2) + "\n")


if __name__ == "__main__":
    main()
