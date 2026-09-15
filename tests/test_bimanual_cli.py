"""Automation distinguishes an assessed policy failure from a broken evaluation."""

import json

import pytest

from rove.benchmarks.store import CampaignStore
from rove.bimanual import cli
from rove.bimanual.config import Environment, Strategy
from rove.evaluation.models import EvaluationConfig


@pytest.mark.parametrize(
    "execution,outcome,status,record,failed",
    [
        ("completed", "pass", "completed", True, False),
        ("completed", "fail", "completed", True, False),
        ("error", "unknown", "completed", True, True),
        ("timeout", "unknown", "completed", True, True),
        ("completed", "unknown", "completed", True, True),
        ("cancelled", "unknown", "cancelled", True, True),
        ("interrupted", "unknown", "interrupted", True, True),
        ("completed", "pass", "completed", False, True),
    ],
)
def test_campaign_command_retains_report_before_execution_failure(
    tmp_path, monkeypatch, capsys, execution, outcome, status, record, failed
):
    config = EvaluationConfig(
        environment=Environment(source="/abc", python="/abc/python").model_dump(),
        strategies={
            "abc-vla": Strategy(
                kind="abc_vla", label="ABC", checkpoint="/weights.pt", python="/abc/python"
            ).model_dump()
        },
    )
    monkeypatch.setattr(cli, "read_profile", lambda _: config)
    monkeypatch.setattr(cli, "validate_profile", lambda _: {"ready": True})

    async def execute(store, campaign_id):
        campaign = store.get(campaign_id)
        if record:
            store.save_trial(
                campaign_id,
                {
                    "task_id": campaign["spec"]["tasks"][0]["id"],
                    "strategy_id": "abc-vla",
                    "seed": 11,
                    "execution": execution,
                    "outcome": outcome,
                    "result": {},
                },
            )
        store.status(campaign_id, status)

    monkeypatch.setattr(cli, "run_campaign", execute)
    root, output = tmp_path / "store", tmp_path / "report.html"
    args = [
        "run",
        "--strategy",
        "abc-vla",
        "--scene",
        "0",
        "--seed",
        "11",
        "--store",
        str(root),
        "--output",
        str(output),
    ]
    if failed:
        with pytest.raises(SystemExit) as stopped:
            cli.main(args)
        assert stopped.value.code == 2
    else:
        cli.main(args)

    captured = capsys.readouterr()
    campaign_id = json.loads(captured.out.splitlines()[0])["campaign_id"]
    assert '"report_url"' in captured.out
    assert output.is_file() and "ABC bimanual comparison" in output.read_text()
    store = CampaignStore(root)
    assert store.get(campaign_id)["status"] == status
    assert len(store.trials(campaign_id)) == int(record)
    if failed:
        assert "report and recorded trials have been retained" in captured.err
