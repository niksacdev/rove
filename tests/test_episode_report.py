"""Episode timing and outcome summaries stay distinct from worker wall time."""

from __future__ import annotations

import copy

import pytest

from rove.benchmarks.report import (
    episode_comparison,
    episode_measurement_section,
    report_data,
    to_html,
)


@pytest.fixture
def episode_report():
    campaign = {
        "id": "a" * 32,
        "created_at": "2026-09-15T00:00:00+00:00",
        "status": "completed",
        "spec": {
            "name": "Bimanual comparison",
            "suite_version": "abc-test",
            "revision": "1",
            "execution": "abc-bimanual",
            "tasks": [{"id": "bottles", "task": "Put bottles in the bin"}],
            "strategies": ["abc-vla", "pi05"],
            "seeds": [1, 2, 3],
            "ks": [1, 2, 3],
        },
        "strategy_definitions": {"abc-vla": {}, "pi05": {}},
        "evidence_kind": "executor_assessment",
        "grading_note": "Synthetic report fixture; no models ran.",
    }

    def measurement(name, value, unit, quality="observed"):
        return {
            "name": name,
            "value": value,
            "unit": unit,
            "quality": quality,
            "evidence_refs": ["episode.summary"],
        }

    trials = []
    for seed, success, latency in [(1, 1, 0.2), (2, 0, 0.4)]:
        trials.append(
            {
                "task_id": "bottles",
                "strategy_id": "abc-vla",
                "seed": seed,
                "outcome": "pass" if success else "fail",
                "execution": "completed",
                "latency_ms": 9000,
                "result": {
                    "assessment": {
                        "measurements": [
                            measurement("episode_success", 1, "boolean"),
                            measurement("final_success", success, "boolean"),
                            measurement("episode_steps", 30 * seed, "steps"),
                            measurement("inference_latency_mean_s", latency, "s"),
                            measurement("inference_latency_p95_s", 2 * latency, "s"),
                        ]
                    }
                },
            }
        )
    trials.append(
        {
            "task_id": "bottles",
            "strategy_id": "pi05",
            "seed": 1,
            "outcome": "unknown",
            "execution": "timeout",
            "result": {
                "assessment": {
                    "measurements": [measurement("final_success", None, "boolean", "unknown")]
                }
            },
        }
    )
    return report_data(campaign, trials)


def test_episode_performance_has_measured_means_and_missing_coverage(episode_report):
    section = episode_measurement_section(episode_report)
    assert "100.0%" in section  # Goal reached at some point in both measured episodes.
    assert "50.0%" in section  # Only one of those episodes ends with the goal satisfied.
    assert "<strong>45</strong>" in section
    assert "<strong>0.3</strong>" in section
    assert "<strong>0.6</strong>" in section
    assert "2 / 3 episodes measured" in section
    assert "Unavailable</strong>" in section
    assert "0 / 3 episodes measured" in section
    assert "not a pooled p95" in section
    assert "observed in simulation" in section
    assert "collision" not in section


def test_episode_measurements_precede_reliability_and_keep_time_scope(episode_report):
    html = to_html(episode_report)
    assert html.index('id="episode-measurements"') < html.index('id="reliability"')
    assert "Simulated episode outcome" in html
    assert "9.00 s" in html
    assert "p95 execution and assessment time" in html
    assert "Simulated duration is not wall-clock cycle time" in html


def test_evidence_qualities_and_units_are_not_pooled(episode_report):
    other = copy.deepcopy(episode_report["verification"]["measurement_records"][3])
    other.update(value=100, quality="estimated")
    episode_report["verification"]["measurement_records"].append(other)
    other = {**other, "value": 200, "quality": "observed", "unit": "ms"}
    episode_report["verification"]["measurement_records"].append(other)
    section = episode_measurement_section(episode_report)
    assert "<strong>100</strong>" in section
    assert "<strong>200</strong>" in section
    assert "<strong>0.3</strong>" in section
    assert "s · estimated" in section
    assert "ms · observed in simulation" in section


def test_conflicting_duplicates_do_not_inflate_coverage(episode_report):
    records = episode_report["verification"]["measurement_records"]
    records.append({**records[3], "value": 99})
    section = episode_measurement_section(episode_report)
    assert "<strong>0.4</strong>" in section
    assert "<strong>49.7</strong>" not in section
    assert "1 / 3 episodes measured" in section


def test_unavailable_measurements_and_html_labels_remain_safe(episode_report):
    episode_report["campaign"]["spec"]["strategies"].append("<script>alert(1)</script>")
    section = episode_measurement_section(episode_report)
    assert "&lt;script&gt;" in section
    assert "<script>" not in section
    episode_report["verification"]["measurement_records"] = []
    assert episode_measurement_section(episode_report) == ""


def test_initial_state_differences_are_prominent_without_changing_pass_metrics(episode_report):
    trials = episode_report["trials"]
    for index, trial in enumerate(trials):
        trial["result"]["output"] = {
            "scope": "abc_simulation",
            "initial_state_digest": str(index + 1) * 64,
        }
    data = report_data(episode_report["campaign"], trials)
    comparison = data["summary"]["episode_comparison"]
    assert comparison["matched_initial_states"] is False
    assert comparison["mismatched_cases"] == ["bottles"]
    assert data["summary"]["strategies"] == episode_report["summary"]["strategies"]
    html = to_html(data)
    assert html.index("Initial simulator states differed") < html.index('id="outcomes"')
    assert "this is not a matched comparison" in html


def test_missing_reset_evidence_does_not_claim_matched_conditions(episode_report):
    comparison = episode_comparison(episode_report["campaign"], episode_report["trials"])
    assert comparison["matched_initial_states"] is None
    assert comparison["missing_initial_states"] == 6
    assert comparison["status"] == "incomplete"


def test_only_complete_matching_actual_resets_claim_matched_conditions(episode_report):
    campaign = episode_report["campaign"]
    trials = [
        {
            "task_id": "bottles",
            "strategy_id": strategy,
            "seed": seed,
            "result": {"output": {"scope": "abc_simulation", "initial_state_digest": "a" * 64}},
        }
        for strategy in campaign["spec"]["strategies"]
        for seed in campaign["spec"]["seeds"]
    ]
    comparison = episode_comparison(campaign, trials)
    assert comparison["matched_initial_states"] is True
    assert comparison["recorded_initial_states"] == 6
    trials[0]["result"]["output"]["scope"] = "synthetic"
    assert episode_comparison(campaign, trials)["matched_initial_states"] is None
