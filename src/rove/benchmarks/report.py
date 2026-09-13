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
            same_assessment = all(
                previous.get(key) == campaign.get(key)
                for key in ("contract_id", "contract", "dataset_revision_id", "metric_scope")
            )
            if not same_assessment or previous["comparison_keys"].get(sid) != campaign[
                "comparison_keys"
            ].get(sid):
                excluded += 1
                continue
            trends.append(
                {
                    "campaign_id": previous["id"],
                    "created_at": previous["created_at"],
                    "revision": previous["spec"]["revision"],
                    "metric_scope": previous.get("metric_scope", "configured_verification"),
                    "contract_id": previous.get("contract_id"),
                    "assessment_ids": sorted(
                        {
                            identity
                            for trial in previous_trials
                            if trial["strategy_id"] == sid
                            for identity in trial.get("assessment_ids", [])
                        }
                    ),
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
        [
            "task",
            "strategy",
            "seed",
            "outcome",
            "execution",
            "latency_ms",
            "attempt_wall_ms",
            "trial_id",
            "configured_outcome",
            "assessment_ids",
            "metric_scope",
            "contract_id",
            "dataset_revision_id",
            "assessment_set_hash",
        ]
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
            + [
                trial.get("trial_id", ""),
                trial.get("configured_outcome", trial.get("outcome", "")),
                json.dumps(trial.get("assessment_ids", [])),
                data.get("assessments", {}).get("scope", "configured_verification"),
                data["campaign"].get("contract_id", ""),
                data["campaign"].get("dataset_revision_id", ""),
                data.get("assessments", {}).get("assessment_set_hash", ""),
            ]
        )
    return stream.getvalue()


_REPORT_STYLE = """
:root{--f-bg:#181c1c;--f-surface:#212727;--f-elevated:#2a3131;--f-border:#343d3c;--f-text:#fff;--f-text-secondary:#d1d5db;--f-text-muted:#9ca3af;--accent:#81c9bf;--pass:#4ade80;--fail:#fb7185;--unknown:#a1a1aa;color-scheme:dark}

[data-theme=light]{--f-bg:#f7f7f2;--f-surface:#fff;--f-elevated:#eef1eb;--f-border:#e2e4e8;--f-text:#1a1b1e;--f-text-secondary:#374151;--f-text-muted:#626a76;--accent:#0d665f;--pass:#15803d;--fail:#be123c;--unknown:#71717a;color-scheme:light}

*{box-sizing:border-box}
body{margin:0;background:var(--f-bg);color:var(--f-text);font:14px Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;line-height:1.6}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
button{font:inherit;cursor:pointer;padding:7px 12px;background:var(--f-elevated);border:1px solid var(--f-border);border-radius:6px;color:var(--f-text)}
a:focus-visible,button:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:4px}
.topbar{display:flex;align-items:center;gap:12px;padding:12px max(24px,calc((100vw - 1240px)/2));border-bottom:1px solid var(--f-border)}
.brand{font-weight:700;letter-spacing:-.03em;color:var(--f-text)}
.brand svg{vertical-align:middle;margin-right:8px}
.topbar span{color:var(--f-text-muted);font-size:12px}
.topbar button{margin-left:auto}
main{max-width:1240px;margin:auto;padding:36px 24px 64px}
h1,h2,h3,p{margin-top:0}
h1{font-size:clamp(28px,4vw,44px);line-height:1.15;letter-spacing:-.045em;margin:12px 0 16px;overflow-wrap:anywhere}
h2{font-size:20px;letter-spacing:-.025em;margin-bottom:8px}
h3{font-size:15px;margin-bottom:6px}
p{color:var(--f-text-secondary)}
.eyebrow{font-size:11px;font-weight:600;letter-spacing:.11em;text-transform:uppercase;color:var(--accent)}
.subtitle,.muted{color:var(--f-text-muted)}
.meta{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0}
.badge{border:1px solid var(--f-border);border-radius:5px;background:var(--f-elevated);padding:3px 8px;font-size:11px;color:var(--f-text-secondary)}
.notice{border-left:3px solid var(--accent);padding:12px 16px;background:var(--f-elevated);border-radius:4px;font-size:12px;margin:18px 0}
.notice strong{color:var(--f-text)}
.jump-links{display:flex;gap:22px;font-size:12px;margin:24px 0}
.hero{border:1px solid var(--f-border);border-radius:10px;padding:24px;background:var(--f-surface)}
.hero-head{display:flex;justify-content:space-between;gap:20px}
.hero-head p{max-width:730px;font-size:13px;margin-bottom:18px}
.scope-label{font-size:12px;color:var(--accent)}
.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}
.stat{padding:16px;background:var(--f-bg);border:1px solid var(--f-border);border-radius:7px}
.stat strong{display:block;font-size:36px;line-height:1.2;letter-spacing:-.04em;margin:6px 0}
.stat small{color:var(--f-text-muted);font-size:11px}
.stat .label{font-size:12px;color:var(--f-text-secondary)}
.pass{color:var(--pass)}
.fail{color:var(--fail)}
.unknown{color:var(--unknown)}
.coverage{margin-top:20px}
.coverage-head{display:flex;justify-content:space-between;gap:12px;font-size:12px}
.coverage-track{height:7px;border-radius:9px;background:var(--f-elevated);overflow:hidden;margin-top:8px}
.coverage-fill{height:100%;background:#289488}
.context-line{font-size:12px;color:var(--f-text-muted);margin:16px 0 0}
.section{margin-top:36px;scroll-margin-top:20px}
.section-intro{font-size:13px;max-width:860px}
.panel{background:var(--f-surface);border:1px solid var(--f-border);border-radius:9px;padding:22px;min-width:0}
.chart-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}
.chart-panel{padding:20px 8px 4px;overflow:hidden}
.chart-panel h3,.chart-panel>p{margin-left:14px;margin-right:14px}
.chart-panel>p{font-size:12px;min-height:40px}
.chart{min-width:0}
.js-plotly-plot{width:100%}
.strategy-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(235px,1fr));gap:14px;margin-top:16px}
.strategy-card{padding:16px;border:1px solid var(--f-border);border-radius:7px;background:var(--f-surface);overflow-wrap:anywhere}
.strategy-metrics{display:flex;gap:18px;margin-top:14px}
.strategy-metrics strong{display:block;font-size:18px}
.strategy-metrics small{color:var(--f-text-muted);font-size:10px}
.strategy-card p{font-size:12px;margin:6px 0 0}
.table{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:12px}
th,td{text-align:left;padding:12px 10px;border-bottom:1px solid var(--f-border);vertical-align:top}
th{color:var(--f-text-muted);font-weight:500;white-space:nowrap}
td{overflow-wrap:anywhere}
caption{text-align:left;color:var(--f-text-muted);padding-bottom:12px;font-size:12px}
details{border:1px solid var(--f-border);border-radius:7px;background:var(--f-surface);margin:12px 0;padding:14px 18px}
summary{cursor:pointer;font-size:13px;color:var(--f-text-secondary)}
details>p{font-size:12px;margin:14px 0 0}
pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:440px;overflow:auto;background:var(--f-bg);padding:16px;border-radius:5px;font:11px/1.7 ui-monospace,SFMono-Regular,monospace}
.evidence-heading{display:flex;justify-content:space-between;gap:12px;align-items:center}
.evidence-heading a{font-size:12px;white-space:nowrap}
.empty{padding:26px;text-align:center;border:1px dashed var(--f-border);border-radius:8px;color:var(--f-text-muted);font-size:13px}
.footnote{font-size:11px;color:var(--f-text-muted);margin-top:16px}
footer{border-top:1px solid var(--f-border);margin-top:36px;padding-top:20px;font-size:11px;color:var(--f-text-muted)}
.skip{position:absolute;left:-10000px}
.skip:focus{left:20px;top:10px;background:var(--f-surface);padding:10px;z-index:99}
@media(max-width:760px){main{padding:24px 14px}
.topbar{padding:12px 14px}
.stats{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.chart-grid{grid-template-columns:1fr}
.hero{padding:18px}
.hero-head{display:block}
.stat{padding:12px}
.stat strong{font-size:30px}
.jump-links{gap:14px;flex-wrap:wrap}
.panel{padding:16px}
.chart-panel{padding:18px 4px 0}
.evidence-heading{display:block}
}
@media print{body{--f-bg:#fff;--f-surface:#fff;--f-elevated:#eef1eb;--f-border:#ccc;--f-text:#111;--f-text-secondary:#333;--f-text-muted:#555;--accent:#0d665f;color-scheme:light}
.topbar button,.jump-links{display:none}
main{padding:20px}
.panel,.hero,.stat{break-inside:avoid}
details[open] pre{max-height:none}
.section{margin-top:24px}
}

"""

_REPORT_SCRIPT = """
(function(){
 const root=document.documentElement,button=document.getElementById('theme-toggle');
 function apply(theme){
  root.dataset.theme=theme;button.textContent=theme==='dark'?'Light theme':'Dark theme';
  button.setAttribute('aria-label','Switch to '+(theme==='dark'?'light':'dark')+' theme');
  const css=getComputedStyle(root),color=css.getPropertyValue('--f-text-secondary').trim(),grid=css.getPropertyValue('--f-border').trim();
  document.querySelectorAll('.js-plotly-plot').forEach(plot=>Plotly.relayout(plot,{'font.color':color,'xaxis.gridcolor':grid,'yaxis.gridcolor':grid,'xaxis.zerolinecolor':grid,'yaxis.zerolinecolor':grid}));
 }
 let theme='dark';try{theme=localStorage.getItem('rove-theme')==='light'?'light':'dark'}catch(e){}
 apply(theme);button.addEventListener('click',()=>{theme=root.dataset.theme==='dark'?'light':'dark';apply(theme);try{localStorage.setItem('rove-theme',theme)}catch(e){}});
})();
"""


def to_html(data: dict) -> str:
    """Render a self-contained report without changing its assessed metric projection."""
    from urllib.parse import quote

    import plotly.graph_objects as go
    from plotly.offline import get_plotlyjs

    campaign, summary = data["campaign"], data["summary"]
    spec = campaign["spec"]

    def escape(value):
        return html.escape(str(value))

    palette = ["#70bdb4", "#38bdf8", "#fbbf24", "#fb7185", "#2dd4bf", "#afba99"]
    assessments = data.get("assessments", {})
    contract = campaign.get("contract") or {}
    scope = contract.get("scope", "configured_verification")
    scope_label = {
        "plan_quality": "Plan quality",
        "scene_understanding": "Scene understanding",
        "episode_outcome": "Episode outcome",
    }.get(scope, "Configured verification")
    source_label = (
        "Success contract assessment"
        if assessments.get("scope") == "success_contract"
        else "Configured verifier assessment"
    )
    scope_note = (
        "These outcomes assess plans or observations, not physical task completion."
        if scope in {"plan_quality", "scene_understanding"}
        else "Episode outcome claims are limited to the supplied evidence and its quality."
        if scope == "episode_outcome"
        else "A passing configured check does not by itself establish physical task success."
    )
    human = any(
        criterion.get("required") and criterion.get("assessment") == "human_review"
        for criterion in contract.get("criteria", [])
    )
    human_note = (
        "Required SME ratings contribute to these outcomes. Missing or conflicting ratings "
        "remain unresolved unless another required criterion establishes failure. "
        "Ratings are not extra trials."
        if human
        else "Outcomes come from the configured verification evidence; no SME rating is implied."
    )
    planned = summary["planned_trials"]
    passed = sum(row["passed"] for row in summary["tasks"])
    failed = sum(row["failed"] for row in summary["tasks"])
    unknown = sum(row["unknown"] for row in summary["tasks"])
    resolved = passed + failed
    coverage = resolved / planned if planned else 0
    invalid = sum(trial.get("execution") == "invalid_verdict" for trial in data["trials"])
    execution_issues = sum(
        trial.get("execution") not in {"completed", "invalid_verdict", "unresolved_evidence"}
        for trial in data["trials"]
    )

    def chart(figure, identity, label, height=330):
        figure.update_layout(
            template=None,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font={"family": "Inter, system-ui, sans-serif", "size": 11, "color": "#d1d5db"},
            colorway=palette,
            margin={"l": 62, "r": 24, "t": 24, "b": 70},
            height=height,
            legend={"orientation": "h", "y": -0.28, "x": 0},
            hoverlabel={"bgcolor": "#212727", "font_color": "#fff"},
            meta={
                "assessment_scope": scope,
                "metric_scope": assessments.get("scope", "configured_verification"),
            },
        )
        figure.update_xaxes(gridcolor="#343d3c", zerolinecolor="#343d3c", automargin=True)
        figure.update_yaxes(gridcolor="#343d3c", zerolinecolor="#343d3c", automargin=True)
        markup = figure.to_html(
            full_html=False,
            include_plotlyjs=False,
            div_id=identity,
            config={"responsive": True, "displaylogo": False, "displayModeBar": False},
        )
        return f'<div class="chart" role="img" aria-label="{escape(label)}">{markup}</div>'

    def display_cell(value):
        if value is None:
            return "Unavailable"
        if isinstance(value, float):
            return f"{value:.3f}".rstrip("0").rstrip(".")
        return value

    def rows_table(rows, fields):
        return "".join(
            "<tr>"
            + "".join(f"<td>{escape(display_cell(row.get(key)))}</td>" for key in fields)
            + "</tr>"
            for row in rows
        )

    strategy_cards, outcome_totals = [], []
    for row in summary["strategies"]:
        tasks = [task for task in summary["tasks"] if task["strategy_id"] == row["strategy_id"]]
        counts = {key: sum(task[key] for task in tasks) for key in ("passed", "failed", "unknown")}
        outcome_totals.append(counts)
        latency = row["latency_p95_ms"]
        latency_label = "Unavailable" if latency is None else f"{latency / 1000:.2f} s"
        strategy_cards.append(
            f'<article class="strategy-card"><h3>{escape(row["strategy_id"])}</h3>'
            f'<p><span class="pass">{counts["passed"]} pass</span> · '
            f'<span class="fail">{counts["failed"]} fail</span> · {counts["unknown"]} unknown</p>'
            f'<div class="strategy-metrics"><div><strong>{row["coverage"]:.0%}</strong><small>Verdict coverage</small></div>'
            f"<div><strong>{latency_label}</strong><small>p95 pipeline time</small></div></div></article>"
        )
    outcome = go.Figure()
    for field, label, color in (
        ("passed", "Pass", "#22c55e"),
        ("failed", "Fail", "#f43f5e"),
        ("unknown", "Unknown", "#8b8b96"),
    ):
        outcome.add_trace(
            go.Bar(
                y=[escape(row["strategy_id"]) for row in summary["strategies"]],
                x=[counts[field] for counts in outcome_totals],
                orientation="h",
                name=label,
                marker_color=color,
                hovertemplate="%{y}<br>" + label + ": %{x} trials<extra></extra>",
            )
        )
    outcome.update_layout(barmode="stack", bargap=0.45)
    outcome.update_xaxes(
        title="Planned trials · unresolved outcomes stay in the denominator",
        dtick=1 if planned < 20 else None,
    )
    outcome.update_yaxes(autorange="reversed")
    outcome_chart = chart(
        outcome,
        "outcomes",
        "Pass, fail and unknown planned trial counts by strategy",
        max(250, 90 + 65 * len(summary["strategies"])),
    )

    reliability = []
    for metric, title, description in (
        (
            "pass_at_k",
            "Can it succeed with retries?",
            "pass@k · Estimated chance of at least one successful attempt in k runs of the same strategy.",
        ),
        (
            "pass_pow_k",
            "Can it succeed every time?",
            "pass^k · Estimated chance that all k attempts succeed for the same strategy.",
        ),
    ):
        figure = go.Figure()
        any_value = False
        for index, row in enumerate(summary["strategies"]):
            points = row[metric]
            name, color = escape(row["strategy_id"]), palette[index % len(palette)]
            any_value |= any(point["value"] is not None for point in points)
            figure.add_trace(
                go.Scatter(
                    x=[point["k"] for point in points],
                    y=[point["value"] for point in points],
                    mode="lines+markers",
                    name=name,
                    legendgroup=name,
                    line={"color": color, "width": 3},
                    marker={"size": 7},
                    connectgaps=False,
                    hovertemplate="k=%{x}<br>Estimate: %{y:.1%}<extra>%{fullData.name}</extra>",
                )
            )
            if any(point["value"] is None and point["lower"] is not None for point in points):
                for bound in ("lower", "upper"):
                    figure.add_trace(
                        go.Scatter(
                            x=[point["k"] for point in points],
                            y=[point[bound] for point in points],
                            mode="lines+markers",
                            legendgroup=name,
                            line={"color": color, "dash": "dot", "width": 2},
                            name=f"{name} unresolved {bound} bound",
                            connectgaps=False,
                            hovertemplate="k=%{x}<br>Unresolved "
                            + bound
                            + ": %{y:.1%}<extra>%{fullData.name}</extra>",
                        )
                    )
        figure.update_xaxes(title="Attempts (k)", tickmode="array", tickvals=spec["ks"])
        figure.update_yaxes(title="Task-macro estimate", range=[-0.03, 1.03], tickformat=".0%")
        status = (
            ""
            if any_value
            else '<p class="notice">No complete point estimate yet. Dotted bounds show what unresolved outcomes could change.</p>'
        )
        reliability.append(
            f'<article class="panel chart-panel"><h3>{title}</h3><p>{description}</p>{chart(figure, metric, description)}{status}</article>'
        )

    trend = go.Figure()
    for row in summary["strategies"]:
        sid = row["strategy_id"]
        previous = sorted(
            (point for point in data["trends"] if point["strategy_id"] == sid),
            key=lambda point: point["created_at"],
        )
        for metric, label in (("pass_at_k", "pass@"), ("pass_pow_k", "pass^")):
            for k in spec["ks"]:
                points = [
                    (
                        point,
                        next((value["value"] for value in point[metric] if value["k"] == k), None),
                    )
                    for point in previous
                ]
                trend.add_trace(
                    go.Scatter(
                        x=[point["created_at"] for point, _ in points],
                        y=[value for _, value in points],
                        mode="lines+markers",
                        text=[escape(point["revision"]) for point, _ in points],
                        name=f"{escape(sid)} {label}{k}",
                        connectgaps=False,
                    )
                )
    trend.update_xaxes(title="Campaign date", tickformat="%b %d<br>%H:%M")
    trend.update_yaxes(range=[-0.03, 1.03], tickformat=".0%")
    dates = {point["created_at"] for point in data["trends"]}
    if len(dates) == 1:
        date = datetime.fromisoformat(next(iter(dates)))
        trend.update_xaxes(range=[date - timedelta(hours=12), date + timedelta(hours=12)])
    trend_chart = (
        chart(trend, "history", "Comparable campaign revisions over time")
        if dates
        else '<div class="empty">No comparable history yet. Run the same cases, evaluator and trial policy again to build a trend.</div>'
    )

    matrix_rows = {(row["task_id"], row["strategy_id"]): row for row in summary["tasks"]}
    task_ids, strategy_ids = [task["id"] for task in spec["tasks"]], spec["strategies"]
    matrix = go.Figure(
        go.Heatmap(
            x=[escape(sid) for sid in strategy_ids],
            y=[escape(tid) for tid in task_ids],
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
            colorscale=[[0, "#24433f"], [1, "#70bdb4"]],
            colorbar={"tickformat": ".0%", "thickness": 10},
            hoverongaps=False,
            hovertemplate="%{y}<br>%{x}<br>Pass rate: %{z:.0%}<extra></extra>",
        )
    )
    matrix.update_yaxes(autorange="reversed")
    matrix_chart = chart(
        matrix,
        "matrix",
        "Resolved task success rates; blank cells are unresolved",
        max(300, min(800, 130 + len(task_ids) * 40)),
    )
    metric_table, task_table = [], []
    for row in summary["strategies"]:
        for metric, label in (("pass_at_k", "pass@k"), ("pass_pow_k", "pass^k")):
            for point in row[metric]:
                values = [row["strategy_id"], label, point["k"]]
                values.extend(
                    "Unavailable" if point[field] is None else f"{point[field]:.1%}"
                    for field in ("value", "lower", "upper")
                )
                metric_table.append(
                    "<tr>" + "".join(f"<td>{escape(value)}</td>" for value in values) + "</tr>"
                )
    for row in summary["tasks"]:
        interval = row["pass_at_1_wilson_95"]
        values = (
            row["task_id"],
            row["strategy_id"],
            row["passed"],
            row["failed"],
            row["unknown"],
            row["n"],
            f"{row['coverage']:.0%}",
            f"{interval[0]:.1%} to {interval[1]:.1%}" if interval else "Unavailable",
        )
        task_table.append(
            "<tr>" + "".join(f"<td>{escape(value)}</td>" for value in values) + "</tr>"
        )

    evidence = []
    for trial in data["trials"]:
        label = f"{trial['task_id']} / {trial['strategy_id']} / seed {trial['seed']}: {trial['outcome']}"
        link = (
            f'<a href="/static/history.html?trial={quote(str(trial["trial_id"]), safe="")}">Open trial in ROVE ↗</a>'
            if trial.get("trial_id")
            else '<span class="muted">No durable trial link</span>'
        )
        evidence.append(
            f'<details><summary>{escape(label)}</summary><div class="evidence-heading"><p>Execution: {escape(trial["execution"])}</p>{link}</div><pre>{escape(json.dumps(trial, indent=2))}</pre></details>'
        )
    verification = data.get("verification", {})
    checks_table = rows_table(
        verification.get("checks", []),
        ("strategy", "endpoint", "role", "required", "passed", "failed", "unknown", "planned"),
    )
    measurements_table = rows_table(
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
    robotics_rows = []
    for strategy in summary.get("robotics", []):
        for name, measurement in strategy.get("metrics", {}).items():
            robotics_rows.append(
                {"strategy": strategy["strategy_id"], "metric": name, **measurement}
            )
    robotics_table = rows_table(
        robotics_rows,
        (
            "strategy",
            "metric",
            "value",
            "unit",
            "quality",
            "aggregation",
            "known_trials",
            "planned_trials",
            "denominator",
            "reason",
        ),
    )
    targets_table = rows_table(
        summary.get("campaign_targets", []),
        (
            "strategy_id",
            "metric",
            "operator",
            "threshold",
            "unit",
            "value",
            "status",
            "denominator",
            "unknown_trials",
        ),
    )
    robotics_section = ""
    if robotics_rows or summary.get("campaign_targets"):
        robotics_section = (
            '<section class="section" id="business-outcomes"><h2>Declared outcomes and campaign targets</h2>'
            "<p>These measures use the frozen success contract. Missing required evidence leaves targets unknown. Synthetic rollout and recorded episode evidence retain their distinct scope.</p>"
            '<div class="table"><table><thead><tr><th>Strategy</th><th>Metric</th><th>Value</th><th>Unit</th><th>Quality</th><th>Aggregation</th><th>Known</th><th>Planned</th><th>Eligible denominator</th><th>Availability</th></tr></thead><tbody>'
            + robotics_table
            + "</tbody></table></div>"
            '<details><summary>Target thresholds and results</summary><div class="table"><table><thead><tr><th>Strategy</th><th>Metric</th><th>Operator</th><th>Threshold</th><th>Unit</th><th>Value</th><th>Status</th><th>Denominator</th><th>Unknown</th></tr></thead><tbody>'
            + targets_table
            + "</tbody></table></div></details></section>"
        )
    portfolio = summary["portfolio"]
    campaign_url = (
        f"/static/datasets.html?step=review&campaign={quote(str(campaign['id']), safe='')}"
    )
    invalid_note = f"{invalid} invalid configured verdicts · {execution_issues} execution issues. These are execution diagnostics, separate from contract assessments."
    return f'''<!doctype html><html lang="en" data-theme="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(spec["name"])} · ROVE campaign report</title>
<style>{_REPORT_STYLE}</style><script>{get_plotlyjs()}</script></head><body>
<a class="skip" href="#report">Skip to report</a><header class="topbar"><a class="brand" href="/">ROVE</a><a href="/static/benchmarks.html">Results</a><span>/ Campaign report</span><button id="theme-toggle" type="button">Light theme</button></header>
<main id="report"><div class="eyebrow">Robot Observation &amp; Vision Evaluation</div><h1>{escape(spec["name"])}</h1>
<p class="subtitle">An evidence-backed view of your robotics agent pipeline.</p>
<div class="meta"><span class="badge">{escape(campaign["status"])}</span><span class="badge">{escape(spec["suite_version"])}</span><span class="badge">Revision {escape(spec["revision"])}</span><span class="badge">{len(task_ids)} cases · {len(strategy_ids)} strategies · {len(spec["seeds"])} repeats</span></div>
<p class="notice"><strong>{escape(campaign["evidence_kind"])}</strong> · {escape(campaign["grading_note"])}</p>
<nav class="jump-links" aria-label="Report sections"><a href="#outcomes">Outcomes</a><a href="#reliability">Reliability</a><a href="#tasks">Cases &amp; measurements</a><a href="#evidence">Evidence</a></nav>
<section class="hero" id="outcomes"><div class="hero-head"><div><div class="eyebrow">{escape(source_label)}</div><h2>What did the trials establish?</h2><p>{scope_note}</p></div><span class="scope-label">{escape(scope_label)}</span></div>
<div class="stats"><div class="stat" data-stat="passed" data-value="{passed}"><span class="label">Assessed pass</span><strong class="pass">{passed}</strong><small>of {planned} planned trials</small></div>
<div class="stat" data-stat="failed" data-value="{failed}"><span class="label">Assessed fail</span><strong class="fail">{failed}</strong><small>failed the assessment criteria</small></div>
<div class="stat" data-stat="unknown" data-value="{unknown}"><span class="label">Unknown</span><strong class="unknown">{unknown}</strong><small>includes pending or unresolved</small></div>
<div class="stat" data-stat="recorded" data-value="{summary["completed_trials"]}"><span class="label">Attempts recorded</span><strong>{summary["completed_trials"]}<small> / {planned}</small></strong><small>recorded does not mean passed</small></div></div>
<div class="coverage"><div class="coverage-head"><span>Verdict coverage · {resolved} / {planned} trials resolved</span><strong>{coverage:.0%}</strong></div><div class="coverage-track" role="meter" aria-label="Verdict coverage" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{coverage * 100:.2f}"><div class="coverage-fill" style="width:{coverage * 100:.2f}%"></div></div></div>
<p class="context-line">{human_note}</p><p class="context-line" data-invalid-count="{invalid}">{invalid_note}</p></section>
<section class="section"><h2>Compare strategies</h2><p class="section-intro">{portfolio["tasks_solved"]} / {portfolio["tasks_total"]} cases succeeded at least once across the selected strategies, with {portfolio["attempt_budget_per_task"]} planned attempts per case. This is hindsight coverage, not a deployed selection policy.</p><div class="panel chart-panel">{outcome_chart}</div><div class="strategy-grid">{"".join(strategy_cards)}</div></section>
{robotics_section}
<section class="section" id="reliability"><h2>Correctness and repeatability</h2><p class="section-intro">Each case has equal weight. Unknown outcomes stay visible as bounds; they cannot inflate a point estimate.</p><div class="chart-grid">{"".join(reliability)}</div>
<details><summary>How to read the curves and uncertainty</summary><p>Solid points require a verdict for every planned attempt. Dotted lines bound unresolved outcomes; they are not confidence intervals. Missing points at k greater than the repeat count mean insufficient trials.</p><p>pass@k = 1 - C(n-c,k)/C(n,k); pass^k = C(c,k)/C(n,k), for n repeats and c successes. Errors, timeouts, interrupted and pending trials remain unknown.</p><p>Task-level pass@1 intervals are 95% Wilson intervals assuming independent attempts. Correlated environments or provider changes weaken that assumption. A verifier's confidence is never used as reliability.</p></details>
<details><summary>Exact metric values and unresolved bounds</summary><div class="table"><table><thead><tr><th>Strategy</th><th>Metric</th><th>k</th><th>Estimate</th><th>Unresolved lower bound</th><th>Unresolved upper bound</th></tr></thead><tbody>{"".join(metric_table)}</tbody></table></div></details></section>
<section class="section"><h2>Progress over comparable campaigns</h2><p class="section-intro">Only matching cases, evaluator, environment, runtime and trial policy enter these trends.</p><div class="panel chart-panel">{trend_chart}</div><p class="footnote">{data["excluded_history_series"]} historical configuration series excluded because comparison conditions changed. {"One comparable date is available; another campaign is needed to show change." if len(dates) == 1 else ""}</p></section>
<section class="section" id="tasks"><h2>Task outcomes</h2><p class="section-intro">Find the cases that fail consistently or still lack enough evidence.</p><div class="panel chart-panel">{matrix_chart}<p>Blank cells mean unresolved outcomes, not zero success.</p></div>
<details><summary>Case outcomes, coverage and pass@1 intervals</summary><div class="table"><table><thead><tr><th>Case</th><th>Strategy</th><th>Pass</th><th>Fail</th><th>Unknown</th><th>n</th><th>Coverage</th><th>pass@1 95% interval</th></tr></thead><tbody>{"".join(task_table)}</tbody></table></div></details>
<details><summary>Configured checks · {len(verification.get("checks", []))} check summaries</summary><p>These are raw configured verifier results, separate from success-contract assessments. Constraint failures are reported independently. Unknown includes missing, pending and unrequested evidence. FK is diagnostic and cannot establish task success.</p><div class="table"><table><thead><tr><th>Strategy</th><th>Check</th><th>Role</th><th>Required</th><th>Pass</th><th>Fail</th><th>Unknown</th><th>Planned</th></tr></thead><tbody>{checks_table}</tbody></table></div>{"" if checks_table else "<p>No configured check summaries were supplied.</p>"}</details>
<details><summary>Task measurements · {len(verification.get("measurements", []))} measured series</summary><p>Only supplied measurements appear. Units and evidence quality are kept separate. Episode completion time is distinct from pipeline processing time. Missing measurements are unavailable, not zero; aggregates describe the measured subset.</p><div class="table"><table><thead><tr><th>Strategy</th><th>Evaluator</th><th>Measurement</th><th>Unit</th><th>Quality</th><th>Measured</th><th>Planned</th><th>Min</th><th>Max</th><th>p95</th></tr></thead><tbody>{measurements_table}</tbody></table></div>{"" if measurements_table else "<p>No task measurements were supplied.</p>"}</details>
<p class="footnote">Pipeline timing is the 95th percentile among attempts with timing evidence, including model loading. Worker startup and timeout durations are recorded separately. Cost: unavailable; provider token/call costs are not measured by this runner.</p></section>
<section class="section" id="evidence"><h2>Inspect the supporting evidence</h2><p class="section-intro">Recorded outputs and execution states remain inspectable. Trial links open the running ROVE app; the evidence below is embedded in this offline report.</p>{"".join(evidence) or '<div class="empty">No attempt evidence recorded yet. Pending trials remain unknown above.</div>'}
<details><summary>Assessment revisions used in this report</summary><p>{escape(assessments.get("note", "Metrics reflect the configured verification stage."))}</p><pre>{escape(json.dumps(assessments, indent=2))}</pre></details>
<details><summary>Configuration identity and provenance</summary><pre>{escape(json.dumps(campaign, indent=2))}</pre></details></section>
<footer>ROVE · Campaign {escape(campaign["id"])} · {escape(campaign.get("created_at", ""))}<br>Charts, summaries and recorded evidence work offline. <a href="{campaign_url}">Review campaign in ROVE ↗</a> requires the running app.</footer></main><script>{_REPORT_SCRIPT}</script></body></html>'''


def saved_report(root, campaign_id):
    """Project saved assessments identically for CLI and dashboard reports."""
    from rove.benchmarks.store import CampaignStore
    from rove.benchmarks.workflow import assessed_trials

    store = CampaignStore(root)
    campaign = store.get(campaign_id)
    history = [
        (item, assessed_trials(root, item, store.trials(item["id"]))[0]) for item in store.list()
    ]
    trials, assessments = assessed_trials(root, campaign, store.trials(campaign_id))
    result = report_data(campaign, trials, history)
    result["assessments"] = assessments
    return result
