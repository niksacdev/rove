"""Deterministic, offline reports. Model text is escaped, never executable markup."""

from __future__ import annotations

import csv
import html
import io
import json
from datetime import datetime, timedelta

from rove.benchmarks.evidence import summarize_evidence
from rove.benchmarks.metrics import summarize


def public_campaign(campaign: dict) -> dict:
    # Endpoint configuration can contain credentials. Never put it in a report/API response.
    result = {k: v for k, v in campaign.items() if k != "config"}
    result["spec"] = {
        **campaign["spec"],
        "tasks": [{"id": t["id"], "task": t["task"]} for t in campaign["spec"]["tasks"]],
    }
    return result


def report_data(
    campaign: dict, trials: list[dict], history: list[tuple[dict, list[dict]]] = ()
) -> dict:
    trends = []
    excluded = 0
    for previous, previous_trials in history:
        summary = summarize(previous, previous_trials)
        for row in summary["strategies"]:
            sid = row["strategy_id"]
            if previous["comparison_keys"].get(sid) != campaign["comparison_keys"].get(sid):
                excluded += 1
                continue
            trends.append(
                {
                    "campaign_id": previous["id"],
                    "created_at": previous["created_at"],
                    "revision": previous["spec"]["revision"],
                    **row,
                }
            )
    return {
        "schema_version": 2,
        "verification": summarize_evidence(campaign, trials),
        "campaign": public_campaign(campaign),
        "summary": summarize(campaign, trials),
        "trials": trials,
        "trends": trends,
        "excluded_history_series": excluded,
    }


def to_csv(data: dict) -> str:
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(
        ["task", "strategy", "seed", "outcome", "execution", "latency_ms", "attempt_wall_ms"]
    )
    for trial in data["trials"]:
        writer.writerow(
            [
                trial.get(k, "")
                for k in (
                    "task_id",
                    "strategy_id",
                    "seed",
                    "outcome",
                    "execution",
                    "latency_ms",
                    "attempt_wall_ms",
                )
            ]
        )
    return stream.getvalue()


def to_html(data: dict) -> str:
    import plotly.graph_objects as go
    from plotly.offline import get_plotlyjs

    campaign = data["campaign"]
    summary = data["summary"]
    escape = html.escape
    charts = []
    for metric, label in (
        ("pass_at_k", "Correctness with retries · pass@k"),
        ("pass_pow_k", "Repeatability · pass^k"),
    ):
        figure = go.Figure()
        for row in summary["strategies"]:
            points = row[metric]
            name = escape(row["strategy_id"])
            figure.add_trace(
                go.Scatter(
                    x=[p["k"] for p in points],
                    y=[p["value"] for p in points],
                    mode="lines+markers",
                    name=name,
                )
            )
            if any(p["value"] is None and p["lower"] is not None for p in points):
                for bound in ("lower", "upper"):
                    figure.add_trace(
                        go.Scatter(
                            x=[p["k"] for p in points],
                            y=[p[bound] for p in points],
                            mode="lines+markers",
                            line={"dash": "dot"},
                            name=f"{name} unresolved {bound} bound",
                        )
                    )
        figure.update_layout(
            title=label,
            xaxis_title="Attempts (k)",
            xaxis={"tickmode": "array", "tickvals": campaign["spec"]["ks"]},
            yaxis_title="Task-macro estimate",
            yaxis={"range": [0, 1], "tickformat": ".0%"},
            template="plotly_white",
        )
        charts.append(figure.to_html(full_html=False, include_plotlyjs=False, div_id=metric))
    trend = go.Figure()
    for row in summary["strategies"]:
        sid = row["strategy_id"]
        previous = sorted(
            (p for p in data["trends"] if p["strategy_id"] == sid), key=lambda p: p["created_at"]
        )
        for metric, label in (("pass_at_k", "pass@"), ("pass_pow_k", "pass^")):
            for k in campaign["spec"]["ks"]:
                points = [
                    (p, next((v["value"] for v in p[metric] if v["k"] == k), None))
                    for p in previous
                ]
                trend.add_trace(
                    go.Scatter(
                        x=[p["created_at"] for p, _ in points],
                        y=[v for _, v in points],
                        mode="lines+markers",
                        text=[escape(p["revision"]) for p, _ in points],
                        name=f"{escape(sid)} {label}{k}",
                        connectgaps=False,
                    )
                )
    trend.update_layout(
        title="Comparable revisions over time",
        xaxis_title="Campaign date",
        xaxis={"tickformat": "%b %d<br>%H:%M"},
        yaxis={"range": [0, 1], "tickformat": ".0%"},
        template="plotly_white",
    )
    dates = {p["created_at"] for p in data["trends"]}
    if len(dates) == 1:
        date = datetime.fromisoformat(next(iter(dates)))
        trend.update_xaxes(range=[date - timedelta(hours=12), date + timedelta(hours=12)])
    charts.append(trend.to_html(full_html=False, include_plotlyjs=False, div_id="history"))
    matrix_rows = {(r["task_id"], r["strategy_id"]): r for r in summary["tasks"]}
    task_ids = [t["id"] for t in campaign["spec"]["tasks"]]
    strategy_ids = campaign["spec"]["strategies"]
    matrix = go.Figure(
        go.Heatmap(
            x=strategy_ids,
            y=task_ids,
            z=[
                [
                    None
                    if matrix_rows[t, s]["unknown"]
                    else matrix_rows[t, s]["passed"] / matrix_rows[t, s]["n"]
                    for s in strategy_ids
                ]
                for t in task_ids
            ],
            zmin=0,
            zmax=1,
            colorscale="Purples",
            colorbar={"tickformat": ".0%"},
            hoverongaps=False,
        )
    )
    matrix.update_layout(
        title="Task success rates · gaps mean unresolved outcomes", template="plotly_white"
    )
    charts.append(matrix.to_html(full_html=False, include_plotlyjs=False, div_id="matrix"))
    metric_table = []
    timing = []
    for row in summary["strategies"]:
        latency = row["latency_p95_ms"]
        timing.append(
            f"<li>{escape(row['strategy_id'])}: "
            + (f"{latency:.0f} ms" if latency is not None else "Unavailable")
            + "</li>"
        )
        for metric, label in (("pass_at_k", "pass@k"), ("pass_pow_k", "pass^k")):
            for point in row[metric]:
                values = [row["strategy_id"], label, point["k"]]
                values.extend(
                    "Unavailable" if point[f] is None else f"{point[f]:.1%}"
                    for f in ("value", "lower", "upper")
                )
                metric_table.append(
                    "<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in values) + "</tr>"
                )
    table = []
    for row in summary["tasks"]:
        ci = row["pass_at_1_wilson_95"]
        interval = f"{ci[0]:.1%} to {ci[1]:.1%}" if ci else "Unavailable"
        table.append(
            "<tr>"
            + "".join(
                f"<td>{escape(str(value))}</td>"
                for value in (
                    row["task_id"],
                    row["strategy_id"],
                    row["passed"],
                    row["failed"],
                    row["unknown"],
                    row["n"],
                    f"{row['coverage']:.0%}",
                    interval,
                )
            )
            + "</tr>"
        )
    evidence = []
    for trial in data["trials"]:
        label = f"{trial['task_id']} / {trial['strategy_id']} / seed {trial['seed']}: {trial['outcome']} ({trial['execution']})"
        evidence.append(
            f"<details><summary>{escape(label)}</summary><pre>{escape(json.dumps(trial, indent=2))}</pre></details>"
        )

    def evidence_table(rows, fields):
        return "".join(
            "<tr>" + "".join(f"<td>{escape(str(row[key]))}</td>" for key in fields) + "</tr>"
            for row in rows
        )

    verification = data.get("verification", {})
    checks_table = evidence_table(
        verification.get("checks", []),
        ("strategy", "endpoint", "role", "required", "passed", "failed", "unknown", "planned"),
    )
    measurements_table = evidence_table(
        verification.get("measurements", []),
        (
            "strategy",
            "endpoint",
            "name",
            "unit",
            "quality",
            "measured",
            "planned",
            "min",
            "max",
            "p95",
        ),
    )
    portfolio = summary["portfolio"]
    manifest = escape(json.dumps(campaign, indent=2))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>ROVE benchmark report</title>
<style>body{{font:16px system-ui;max-width:1150px;margin:40px auto;padding:0 20px;color:#172038;background:#f6f8fc}}
section,details{{background:white;padding:18px;border:1px solid #dce2ee;border-radius:12px;margin:16px 0}}
table{{border-collapse:collapse;width:100%}}td,th{{text-align:left;padding:10px;border-bottom:1px solid #ddd}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere}}.notice{{border-left:4px solid #7657dd;padding:12px;background:#ede9ff}}
.table{{overflow:auto}}h1{{letter-spacing:-.04em}}small{{color:#526078}}</style>
<script>{get_plotlyjs()}</script></head><body>
<p>ROVE / BENCHMARK REPORT</p><h1>{escape(campaign["spec"]["name"])}</h1>
<p>{escape(campaign["spec"]["suite_version"])} · revision {escape(campaign["spec"]["revision"])} · {escape(campaign["status"])}</p>
<p class="notice"><strong>{escape(campaign["evidence_kind"])}</strong> — {escape(campaign["grading_note"])}</p>
<p>{summary["completed_trials"]} of {summary["planned_trials"]} attempts recorded.
Cross-model coverage: {portfolio["tasks_solved"]} / {portfolio["tasks_total"]} tasks solved at least once,
with {portfolio["attempt_budget_per_task"]} planned attempts per task. This is hindsight coverage.</p>
<section><h2>How to read these results</h2><p>Solid points require a verdict for every planned attempt.
Dotted lines bound unresolved outcomes; they are not confidence intervals. Missing points at k greater
than the repeat count mean insufficient trials. Each task has equal weight.</p>
<p>pass@k = 1 - C(n-c,k)/C(n,k); pass^k = C(c,k)/C(n,k), for n repeats and c successes.
Errors, timeouts, interrupted and pending trials remain unknown and cannot inflate a point score.</p>
<p>Task-level pass@1 intervals below are 95% Wilson intervals assuming independent attempts.
Correlated environments or provider changes weaken that assumption. A verifier's confidence is never used as reliability.</p></section>
{"".join(charts)}
<p>{data["excluded_history_series"]} historical configuration series excluded because tasks, evaluator,
environment, runtime or trial policy changed. Empty trends require another comparable campaign.</p>
<section class="table"><h2>Task outcomes</h2><table><thead><tr><th>Task</th><th>Configuration</th><th>Pass</th><th>Fail</th><th>Unknown</th><th>n</th><th>Coverage</th><th>pass@1 95% interval</th></tr></thead>
<tbody>{"".join(table)}</tbody></table></section>
<section class="table"><h2>Metric values</h2><table><thead><tr><th>Configuration</th><th>Metric</th><th>k</th><th>Estimate</th><th>Unresolved lower bound</th><th>Unresolved upper bound</th></tr></thead>
<tbody>{"".join(metric_table)}</tbody></table></section>
<section><h2>Timing and cost</h2><p>95th percentile pipeline wall time for attempts with timing evidence (model loading included). Worker startup and timeout durations are recorded separately in each attempt.</p>
<ul>{"".join(timing)}</ul><p>Cost: unavailable. Provider token/call costs are not measured by this runner.</p></section>
<section class="table"><h2>Configured checks</h2><p>Constraint failures are reported independently. Unknown includes missing, pending and unrequested evidence. FK is diagnostic and cannot establish task success.</p>
<table><thead><tr><th>Configuration</th><th>Check</th><th>Role</th><th>Required</th><th>Pass</th><th>Fail</th><th>Unknown</th><th>Planned</th></tr></thead><tbody>{checks_table}</tbody></table></section>
<section class="table"><h2>Task measurements</h2><p>Only supplied measurements appear. Units and evidence quality are kept separate. Episode completion time comes from episode evidence; it is distinct from pipeline processing time. Missing measurements are unavailable, not zero. Aggregates describe the measured subset; inspect task-level evidence before comparing configurations.</p>
<table><thead><tr><th>Configuration</th><th>Evaluator</th><th>Measurement</th><th>Unit</th><th>Quality</th><th>Measured</th><th>Planned</th><th>Min</th><th>Max</th><th>p95</th></tr></thead><tbody>{measurements_table}</tbody></table></section>
<section><h2>Attempt evidence</h2>{"".join(evidence)}</section>
<details><summary>Configuration identity and provenance</summary><pre>{manifest}</pre></details>
</body></html>"""
