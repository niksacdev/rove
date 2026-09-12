"""Paired campaign validity and honest unknown-outcome accounting."""

import copy
import json

import pytest

from rove.benchmarks.comparison import compare_campaigns
from rove.benchmarks.models import fingerprint


def campaign(strategy_id="baseline", endpoint_id="planner"):
    endpoints = {
        endpoint_id: {
            "type": "agent",
            "adapter": "copilot_agent",
            "display_name": "Planner",
            "config": {
                "model": "model-v1",
                "provider": {
                    "base_url": "https://model.test/v1",
                    "api_key": "private-key-one",  # pragma: allowlist secret
                },
            },
        },
        "judge": {"type": "vlm", "adapter": "mock_vlm", "config": {"model": "judge-v1"}},
        "sim": {"type": "sim", "adapter": "mock_sim", "config": {"scene": "workcell-v1"}},
    }
    definition = {"plan": endpoint_id, "verify": "judge", "sim": "sim"}
    return {
        "spec": {
            "name": "A campaign",
            "suite_version": "suite-v1",
            "revision": "v1",
            "strategies": [strategy_id],
            "tasks": [{"id": "pick", "task": "Pick the red cube", "image_base64": "aW1hZ2U="}],
            "seeds": [1, 2, 3],
            "ks": [1, 2, 4],
            "timeout_s": 60,
            "grading": "verify_stage_judgment",
        },
        "config": {
            "endpoints": endpoints,
            "strategies": {strategy_id: definition},
            "defaults": {"timeout_per_step_ms": 30000, "sim": "not-frozen-unused"},
        },
        "runtime": {"source_hash": "same-code", "packages": {"github-copilot-sdk": "1.0.13"}},
        "model_versions": {
            key: {"identity_hash": fingerprint(value), "declared_revision": "v1"}
            for key, value in endpoints.items()
        },
        "local_evaluators": {},
    }


def trials(strategy="baseline", outcomes=("pass", "fail", "pass"), task="pick"):
    return [
        {
            "task_id": task,
            "strategy_id": strategy,
            "seed": seed,
            "outcome": outcome,
            "execution": "completed",
        }
        for seed, outcome in enumerate(outcomes, 1)
    ]


def compare(base, candidate, left=None, right=None):
    a, b = base["spec"]["strategies"][0], candidate["spec"]["strategies"][0]
    return compare_campaigns(
        base,
        trials(a) if left is None else left,
        candidate,
        trials(b) if right is None else right,
        a,
        b,
    )


def test_renamed_strategy_endpoint_display_names_and_credentials_are_equivalent():
    baseline = campaign()
    candidate = campaign("candidate", "renamed-planner")
    candidate["config"]["strategies"]["candidate"]["display_name"] = "New visible name"
    endpoint = candidate["config"]["endpoints"]["renamed-planner"]
    endpoint["display_name"] = "Renamed planner"
    endpoint["config"]["provider"]["api_key"] = (
        "new-private-key"  # pragma: allowlist secret — synthetic redaction fixture
    )
    candidate["model_versions"]["renamed-planner"]["identity_hash"] = fingerprint(endpoint)
    result = compare(baseline, candidate)
    assert result["comparable"] is True
    assert result["differences"] == []
    assert result["case_comparisons"][0]["status"] == "unchanged"


def test_changed_candidate_model_is_an_explicit_ablation_not_an_incompatibility():
    baseline, candidate = campaign(), campaign("candidate")
    candidate["config"]["endpoints"]["planner"]["config"]["model"] = "model-v2"
    candidate["model_versions"]["planner"]["identity_hash"] = "new-independent-weights-hash"
    result = compare(baseline, candidate)
    assert result["comparable"]
    assert any(d["path"].endswith("configuration.config.model") for d in result["differences"])
    assert any(d["candidate"] == "new-independent-weights-hash" for d in result["differences"])
    assert "private-key" not in json.dumps(result)


@pytest.mark.parametrize(
    "mutation, reason",
    [
        (lambda c: c["config"]["endpoints"]["judge"]["config"].update(model="judge-v2"), "Grader"),
        (
            lambda c: c["config"]["endpoints"]["sim"]["config"].update(scene="workcell-v2"),
            "Environment",
        ),
        (lambda c: c["spec"]["tasks"][0].update(image_base64="bmV3LWltYWdl"), "Case"),
        (lambda c: c["runtime"].update(source_hash="new-runtime"), "Runtime"),
        (lambda c: c["spec"].update(seeds=[1, 2]), "repeat"),
        (lambda c: c["spec"].update(timeout_s=20), "timeout"),
        (lambda c: c.update(dataset_revision_id="new-dataset"), "dataset_revision"),
        (lambda c: c.update(contract_id="new-contract"), "contract_id"),
        (lambda c: c.update(metric_scope="human_review"), "Metric outcome"),
    ],
)
def test_changed_conditions_are_rejected(mutation, reason):
    baseline, candidate = campaign(), campaign("candidate")
    mutation(candidate)
    result = compare(baseline, candidate)
    assert not result["comparable"]
    assert any(reason in item for item in result["reasons"])
    assert result["paired_counts"] is None
    assert all(row["status"] == "not_comparable" for row in result["case_comparisons"])


def test_renamed_grader_is_allowed_but_grader_identity_change_is_not():
    baseline, candidate = campaign(), campaign("candidate")
    candidate["config"]["endpoints"]["new-judge"] = candidate["config"]["endpoints"].pop("judge")
    candidate["model_versions"]["new-judge"] = candidate["model_versions"].pop("judge")
    candidate["config"]["strategies"]["candidate"]["verify"] = "new-judge"
    assert compare(baseline, candidate)["comparable"]
    candidate["model_versions"]["new-judge"]["identity_hash"] = "changed-judge-weights"
    assert not compare(baseline, candidate)["comparable"]


def test_required_checks_are_part_of_the_grading_contract():
    baseline, candidate = campaign(), campaign("candidate")
    for payload in (baseline, candidate):
        sid = payload["spec"]["strategies"][0]
        payload["config"]["strategies"][sid]["verify"] = {
            "endpoint": "judge",
            "checks": [{"endpoint": "judge", "role": "constraint", "required": True}],
        }
    candidate["config"]["strategies"]["candidate"]["verify"]["checks"][0]["required"] = False
    assert not compare(baseline, candidate)["comparable"]


def test_case_revision_must_match_but_display_alias_can_change():
    baseline, candidate = campaign(), campaign("candidate")
    for payload in (baseline, candidate):
        payload["spec"]["tasks"][0]["case_revision_id"] = "case-r1"
    candidate["spec"]["tasks"][0]["id"] = "new-alias"
    result = compare(baseline, candidate, right=trials("candidate", task="new-alias"))
    assert result["comparable"]
    assert result["case_comparisons"][0]["case_id"] == "case-r1"
    candidate["spec"]["tasks"][0]["case_revision_id"] = "case-r2"
    assert not compare(baseline, candidate)["comparable"]


def test_missing_planned_attempts_stay_unknown_with_existing_metric_bounds():
    baseline, candidate = campaign(), campaign("candidate")
    result = compare(
        baseline,
        candidate,
        left=trials(outcomes=("pass",)),
        right=trials("candidate", outcomes=("fail", "pass")),
    )
    assert result["comparable"]
    assert result["baseline"]["planned_trials"] == 3
    row = result["baseline"]["tasks"][0]
    assert row["unknown"] == 2
    assert row["pass_at_k"][0] == {"k": 1, "value": None, "lower": pytest.approx(1 / 3), "upper": 1}
    assert row["pass_pow_k"][1]["value"] is None
    assert result["paired_counts"]["pass_to_fail"] == 1
    assert result["paired_counts"]["unknown"] == 2
    assert result["paired_coverage"] == pytest.approx(1 / 3)
    assert "confidence_interval" not in result["baseline"]["strategies"][0]


def test_unfinished_trial_cannot_contribute_a_pass():
    baseline, candidate = campaign(), campaign("candidate")
    rows = trials()
    rows[0]["execution"] = "running"
    result = compare(baseline, candidate, left=rows)
    assert result["baseline"]["tasks"][0]["passed"] == 1
    assert result["baseline"]["tasks"][0]["unknown"] == 1


def test_paired_counts_show_both_improvements_and_regressions():
    result = compare(
        campaign(),
        campaign("candidate"),
        left=trials(outcomes=("pass", "fail", "pass")),
        right=trials("candidate", outcomes=("fail", "pass", "pass")),
    )
    assert result["case_comparisons"][0]["status"] == "mixed"
    assert result["paired_counts"] == {
        "pass_to_pass": 1,
        "pass_to_fail": 1,
        "fail_to_pass": 1,
        "fail_to_fail": 0,
        "unknown": 0,
    }
    assert "no significance" in result["evidence_note"]


def test_static_recorded_episode_is_only_regrading_even_if_outcomes_pass():
    baseline, candidate = campaign(), campaign("candidate")
    for payload in (baseline, candidate):
        payload["spec"]["tasks"][0]["example"] = {
            "extras": {"episode": {"success": True, "source": "recorded"}}
        }
    candidate["config"]["endpoints"]["planner"]["config"]["model"] = "new-model"
    result = compare(baseline, candidate)
    assert result["comparable"]
    assert result["scope"] == "recorded_evidence_regrade"
    assert "cannot show" in result["evidence_note"]
    assert "No explicit frozen success contract" in result["evidence_note"]
    for payload in (baseline, candidate):
        payload["contract"] = {"scope": "physical_completion"}
    result = compare(baseline, candidate)
    assert not result["comparable"]
    assert any("fresh execution evidence" in item for item in result["reasons"])


def test_contract_and_inputs_are_not_mutated_and_secrets_never_exposed():
    baseline, candidate = campaign(), campaign("candidate")
    for payload in (baseline, candidate):
        payload["contract"] = {
            "scope": "plan_quality",
            "api_key": "secret-contract",  # pragma: allowlist secret
        }
    candidate["contract"]["api_key"] = (
        "different-secret"  # pragma: allowlist secret — synthetic redaction fixture
    )
    original = copy.deepcopy((baseline, candidate))
    result = compare(baseline, candidate)
    serialized = json.dumps(result)
    assert "secret-contract" not in serialized and "different-secret" not in serialized
    assert "private-key-one" not in serialized
    assert (baseline, candidate) == original


def test_duplicate_conflict_is_not_last_write_wins_and_out_of_plan_is_rejected():
    baseline, candidate = campaign(), campaign("candidate")
    rows = trials()
    rows.append({**rows[0], "outcome": "fail"})
    result = compare(baseline, candidate, left=rows)
    assert not result["comparable"]
    assert result["baseline"]["tasks"][0]["unknown"] == 1
    rows = [*trials(), {**trials()[0], "seed": 100}]
    assert not compare(baseline, candidate, left=rows)["comparable"]


def test_missing_runtime_and_missing_local_verifier_identity_are_not_comparable():
    baseline, candidate = campaign(), campaign("candidate")
    baseline.pop("runtime")
    assert not compare(baseline, candidate)["comparable"]
    baseline = campaign()
    baseline["config"]["endpoints"]["judge"]["adapter"] = "local_verifier"
    assert not compare(baseline, candidate)["comparable"]


def test_supplied_review_outcomes_and_metric_scope_are_preserved():
    baseline, candidate = campaign(), campaign("candidate")
    for payload in (baseline, candidate):
        payload.update(metric_scope="final_human_reviews", contract={"scope": "plan_quality"})
    result = compare(baseline, candidate, left=trials(outcomes=("unknown", "pass", "unknown")))
    assert result["comparable"]
    assert result["metric_scope"] == "final_human_reviews"
    assert result["baseline"]["tasks"][0]["unknown"] == 2


def test_nested_credential_diffs_are_redacted_before_recursion():
    baseline, candidate = campaign(), campaign("candidate")
    baseline["contract"] = {"credentials": {"value": "secret-value"}}
    candidate["contract"] = {"credentials": {"value": "changed-secret-value"}}
    result = compare(baseline, candidate)
    assert "secret-value" not in json.dumps(result)
    baseline["contract"] = {"items": [{"credentials": {"value": "secret-value"}}]}
    candidate["contract"] = {"items": [{"credentials": {"value": "changed-secret-value"}}]}
    assert "secret-value" not in json.dumps(compare(baseline, candidate))


def test_tool_descriptions_inside_grader_configuration_affect_behavior():
    baseline, candidate = campaign(), campaign("candidate")
    baseline["config"]["endpoints"]["judge"]["config"]["tools"] = [
        {"description": "Grade evidence"}
    ]
    candidate["config"]["endpoints"]["judge"]["config"]["tools"] = [
        {"description": "Always return pass"}
    ]
    result = compare(baseline, candidate)
    assert not result["comparable"]
    assert any("Grader" in reason for reason in result["reasons"])
