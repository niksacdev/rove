"""The disabled CI example preserves reviewed source conditions and explicit references."""

import copy
import runpy
from pathlib import Path

import pytest

prepare_request = runpy.run_path(str(Path(__file__).parents[1] / "examples/ci/prepare_request.py"))[
    "prepare_request"
]


def seed():
    return {
        "baselines": [{"revision_id": "baseline-v1"}],
        "case_revision_ids": ["case-v1"],
        "contract_id": "contract-v1",
        "contract": {"campaign_targets": [{"metric": "success_rate", "threshold": 0.9}]},
        "source": {"type": "campaign", "campaign_id": "original", "snapshot_sha256": "digest"},
        "defaults": {"name": "Candidate", "seeds": [1, 2, 3], "ks": [1, 3], "timeout_s": 30},
    }


def test_request_retains_exact_source_cases_rules_and_defaults():
    original = seed()
    before = copy.deepcopy(original)
    request = prepare_request(original, "baseline-v1", "candidate-v2")
    assert request["baseline_revision_id"] == "baseline-v1"
    assert request["strategies"] == ["candidate-v2"]
    assert request["source"] == original["source"]
    assert request["case_revision_ids"] == ["case-v1"]
    assert request["contract_id"] == "contract-v1"
    assert request["seeds"] == [1, 2, 3]
    assert original == before
    original["dataset_revision_id"] = "dataset-v1"
    request = prepare_request(original, "baseline-v1", "candidate-v2")
    assert request["dataset_revision_id"] == "dataset-v1"
    assert "case_revision_ids" not in request


@pytest.mark.parametrize("mutation", ["baseline", "targets", "cases", "source"])
def test_request_rejects_unreviewed_or_unrelated_reference(mutation):
    draft = seed()
    if mutation == "baseline":
        draft["baselines"] = []
    elif mutation == "targets":
        draft["contract"]["campaign_targets"] = []
    elif mutation == "cases":
        draft["case_revision_ids"] = []
    else:
        draft["source"]["type"] = "trial"
    with pytest.raises(ValueError):
        prepare_request(draft, "baseline-v1", "candidate-v2")
