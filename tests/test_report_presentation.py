"""Presentation must preserve the scoring denominator and evidence boundaries."""

from __future__ import annotations

import copy
from html.parser import HTMLParser

import plotly.graph_objects as go
import pytest

from rove.benchmarks.metrics import summarize
from rove.benchmarks.report import to_html


class Elements(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.tags = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


@pytest.fixture
def report():
    campaign = {
        "id": "campaign-1",
        "created_at": "2026-09-12T10:30:00+00:00",
        "status": "completed",
        "evidence_kind": "synthetic",
        "grading_note": "Synthetic outputs are not physical success evidence.",
        "spec": {
            "name": "Warehouse placement baseline",
            "suite_version": "fixtures-v1",
            "revision": "baseline",
            "tasks": [{"id": "pick", "task": "Pick a cube"}, {"id": "place", "task": "Place it"}],
            "strategies": ["baseline", "candidate"],
            "seeds": [1, 2],
            "ks": [1, 2, 3],
        },
        "contract": {
            "scope": "plan_quality",
            "evidence_mode": "candidate_output",
            "criteria": [{"id": "clearance", "required": True, "assessment": "human_review"}],
        },
    }
    trials = [
        {
            "task_id": "pick",
            "strategy_id": "baseline",
            "seed": 1,
            "outcome": "pass",
            "execution": "completed",
            "trial_id": "trial-1",
            "latency_ms": 120,
        },
        {
            "task_id": "pick",
            "strategy_id": "baseline",
            "seed": 2,
            "outcome": "fail",
            "execution": "completed",
        },
        {
            "task_id": "place",
            "strategy_id": "baseline",
            "seed": 1,
            "outcome": "unknown",
            "configured_outcome": "pass",
            "execution": "invalid_verdict",
        },
        {
            "task_id": "place",
            "strategy_id": "candidate",
            "seed": 1,
            "outcome": "unknown",
            "execution": "error",
        },
    ]
    return {
        "campaign": campaign,
        "trials": trials,
        "summary": summarize(campaign, trials),
        "assessments": {"scope": "success_contract", "review_ids": ["review-1"]},
        "trends": [],
        "excluded_history_series": 1,
        "verification": {"checks": [], "measurements": []},
    }


def charts(monkeypatch):
    captured = {}
    original = go.Figure.to_html

    def capture(self, *args, **kwargs):
        captured[kwargs["div_id"]] = self.to_plotly_json()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(go.Figure, "to_html", capture)
    return captured


def test_hero_preserves_unknown_pending_and_invalid_counts(report):
    before = copy.deepcopy(report)
    rendered = to_html(report)
    tags = Elements(rendered).tags
    values = {
        attrs["data-stat"]: int(attrs["data-value"]) for _, attrs in tags if "data-stat" in attrs
    }
    assert values == {"passed": 1, "failed": 1, "unknown": 6, "recorded": 4}
    meter = next(attrs for _, attrs in tags if attrs.get("role") == "meter")
    assert meter["aria-valuenow"] == "25.00"
    assert 'data-invalid-count="1"' in rendered
    assert "execution diagnostics, separate from contract assessments" in rendered
    assert "Missing or conflicting ratings" in rendered
    assert "raw configured verifier results, separate from success-contract assessments" in rendered
    assert report == before


def test_curves_and_counts_are_actual_metrics_not_resolved_only_scores(report, monkeypatch):
    figures = charts(monkeypatch)
    to_html(report)
    bars = figures["outcomes"]["data"]
    assert [list(trace["x"]) for trace in bars] == [[1, 0], [1, 0], [2, 4]]
    for metric in ("pass_at_k", "pass_pow_k"):
        figure = figures[metric]
        assert figure["layout"]["meta"] == {
            "assessment_scope": "plan_quality",
            "metric_scope": "success_contract",
        }
        for row in report["summary"]["strategies"]:
            line = next(trace for trace in figure["data"] if trace["name"] == row["strategy_id"])
            assert list(line["y"]) == [point["value"] for point in row[metric]]
            assert list(line["y"]) == [None, None, None]
            lower = next(
                trace
                for trace in figure["data"]
                if trace["name"] == f"{row['strategy_id']} unresolved lower bound"
            )
            assert list(lower["y"]) == [point["lower"] for point in row[metric]]
            assert lower["line"]["dash"] == "dot"
            assert lower["y"][-1] is None  # k exceeds available repeats.
    assert figures["matrix"]["data"][0]["z"] == [[0.5, None], [None, None]]


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("plan_quality", "Plan quality"),
        ("scene_understanding", "Scene understanding"),
        ("episode_outcome", "Episode outcome"),
    ],
)
def test_scope_labels_never_imply_physical_success_from_plan_grading(report, scope, expected):
    report["campaign"]["contract"]["scope"] = scope
    rendered = to_html(report)
    assert f'<span class="scope-label">{expected}</span>' in rendered
    assert "synthetic" in rendered
    if scope == "episode_outcome":
        assert "limited to the supplied evidence and its quality" in rendered
    else:
        assert "not physical task completion" in rendered


def test_noncontract_report_does_not_claim_human_review_or_business_success(report):
    report["campaign"].pop("contract")
    report.pop("assessments")
    rendered = to_html(report)
    assert "Configured verifier assessment" in rendered
    assert "no SME rating is implied" in rendered
    assert "does not by itself establish physical task success" in rendered


def test_export_escapes_every_user_surface_and_encodes_links(report, monkeypatch):
    attack = '</script><img src=x onerror="alert(1)">'
    report["campaign"]["spec"]["name"] = attack
    report["campaign"]["spec"]["strategies"][0] = attack
    report["campaign"]["spec"]["tasks"][0]["id"] = attack
    report["campaign"]["id"] = 'campaign" onclick="alert(1)'
    for trial in report["trials"]:
        if trial["strategy_id"] == "baseline":
            trial["strategy_id"] = attack
        if trial["task_id"] == "pick":
            trial["task_id"] = attack
        trial["result"] = {"text": attack}
    report["trials"][0]["trial_id"] = 'trial" onclick="alert(1)'
    report["summary"] = summarize(report["campaign"], report["trials"])
    figures = charts(monkeypatch)
    rendered = to_html(report)
    assert attack not in rendered
    assert "&lt;img" in rendered
    tags = Elements(rendered).tags
    assert not any(tag in {"img", "iframe"} for tag, _ in tags)
    assert not any(key.startswith("on") for _, attrs in tags for key in attrs)
    links = [attrs["href"] for tag, attrs in tags if tag == "a"]
    assert "/static/history.html?trial=trial%22%20onclick%3D%22alert%281%29" in links
    assert (
        "/static/datasets.html?step=review&campaign=campaign%22%20onclick%3D%22alert%281%29"
        in links
    )
    assert figures["matrix"]["data"][0]["x"][0].startswith("&lt;/script&gt;")
    assert figures["matrix"]["data"][0]["y"][0].startswith("&lt;/script&gt;")


def test_empty_report_is_offline_accessible_and_explains_missing_evidence(report):
    report["trials"] = []
    report["summary"] = summarize(report["campaign"], [])
    rendered = to_html(report)
    tags = Elements(rendered).tags
    assert not any(tag == "script" and "src" in attrs for tag, attrs in tags)
    assert not any(tag == "link" and attrs.get("rel") == "stylesheet" for tag, attrs in tags)
    assert 'id="theme-toggle"' in rendered
    assert "[data-theme=light]" in rendered
    assert "No attempt evidence recorded yet" in rendered
    assert "No comparable history yet" in rendered
    assert "No task measurements were supplied" in rendered
    assert "No complete point estimate yet" in rendered
    assert "Trial links open the running ROVE app" in rendered
    assert 'data-stat="unknown" data-value="8"' in rendered
    assert any(attrs.get("aria-label") == "Report sections" for _, attrs in tags)


def test_measurements_keep_quality_units_and_assessment_source_distinct(report):
    report["verification"]["measurements"] = [
        {
            "strategy": "baseline",
            "endpoint": "episode",
            "name": "completion_time",
            "unit": "s",
            "quality": "observed",
            "measured": 1,
            "planned": 4,
            "min": 5,
            "max": 5,
            "p95": 5,
        },
        {
            "strategy": "baseline",
            "endpoint": "rollout",
            "name": "completion_time",
            "unit": "ms",
            "quality": "synthetic",
            "measured": 2,
            "planned": 4,
            "min": 20,
            "max": 40,
            "p95": 40,
        },
    ]
    rendered = to_html(report)
    assert "2 measured series" in rendered
    assert "<td>s</td><td>observed</td><td>1</td><td>4</td>" in rendered
    assert "<td>ms</td><td>synthetic</td><td>2</td><td>4</td>" in rendered
    assert "aggregates describe the measured subset" in rendered
    assert "Episode completion time is distinct from pipeline processing time" in rendered


def test_resolved_curve_keeps_full_point_estimates_without_unresolved_traces(report, monkeypatch):
    spec = report["campaign"]["spec"]
    report["trials"] = [
        {
            "task_id": task["id"],
            "strategy_id": strategy,
            "seed": seed,
            "outcome": "pass" if seed == 1 else "fail",
            "execution": "completed",
        }
        for task in spec["tasks"]
        for strategy in spec["strategies"]
        for seed in spec["seeds"]
    ]
    report["summary"] = summarize(report["campaign"], report["trials"])
    figures = charts(monkeypatch)
    to_html(report)
    assert list(figures["pass_at_k"]["data"][0]["y"]) == [0.5, 1.0, None]
    assert list(figures["pass_pow_k"]["data"][0]["y"]) == [0.5, 0.0, None]
    assert len(figures["pass_at_k"]["data"]) == 2


def test_declared_robotics_targets_preserve_unknown_and_scope(report):
    report["summary"]["robotics"] = [
        {
            "strategy_id": "baseline",
            "metrics": {
                "autonomous_completion": {
                    "value": None,
                    "unit": "fraction",
                    "quality": "synthetic",
                    "aggregation": "ratio",
                    "known_trials": 1,
                    "planned_trials": 4,
                    "denominator": 1,
                    "reason": "Missing intervention evidence",
                }
            },
        }
    ]
    report["summary"]["campaign_targets"] = [
        {
            "strategy_id": "baseline",
            "metric": "autonomous_completion",
            "operator": "gte",
            "threshold": 0.9,
            "unit": "fraction",
            "value": None,
            "status": "unknown",
            "denominator": 1,
            "unknown_trials": 3,
        }
    ]
    html = to_html(report)
    assert "Declared outcomes and campaign targets" in html
    assert "Missing intervention evidence" in html
    assert "synthetic" in html
    assert "autonomous_completion" in html
    assert "Target thresholds and results" in html
