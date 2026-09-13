"""CLI operations reuse configuration and immutable revision validation."""

import json
from pathlib import Path

import pytest

from rove.strategies.cli import main as strategy_main
from rove.ui.config_cli import main as config_main


@pytest.fixture
def configuration(tmp_path):
    target = tmp_path / "rove.yaml"
    target.write_text(Path("examples/benchmarks/rove.yaml").read_text())
    return target


def test_strategy_revision_cli_preserves_original_and_checks_preview(
    configuration, tmp_path, capsys
):
    strategy_main(["list", "--config", str(configuration)])
    identity = next(iter(json.loads(capsys.readouterr().out)["strategies"]))
    strategy_main(["show", identity, "--config", str(configuration)])
    source = json.loads(capsys.readouterr().out)
    definition = dict(source["definition"], display_name="CLI candidate")
    draft = {
        "parent_id": identity,
        "expected_parent_fingerprint": source["fingerprint"],
        "definition": definition,
    }
    path = tmp_path / "revision.json"
    path.write_text(json.dumps(draft))
    strategy_main(["preview", str(path), "--config", str(configuration)])
    prepared = json.loads(capsys.readouterr().out)
    draft.update(expected_preview_hash=prepared["preview_hash"], operation_id="cli-revision")
    path.write_text(json.dumps(draft))
    strategy_main(["save", str(path), "--config", str(configuration)])
    saved = json.loads(capsys.readouterr().out)
    strategy_main(["list", "--config", str(configuration)])
    listed = json.loads(capsys.readouterr().out)["strategies"]
    assert listed[identity] == source["definition"]
    assert listed[saved["strategy_id"]]["display_name"] == "CLI candidate"
    draft["expected_preview_hash"] = "0" * 64
    path.write_text(json.dumps(draft))
    with pytest.raises(SystemExit) as error:
        strategy_main(["save", str(path), "--config", str(configuration)])
    assert error.value.code == 2


def test_config_cli_uses_revision_catalog_and_sanitized_output(configuration, capsys):
    config_main(["validate", "--config", str(configuration)])
    assert json.loads(capsys.readouterr().out)["valid"] is True
    config_main(["models", "--config", str(configuration)])
    assert json.loads(capsys.readouterr().out)["endpoints"]


def test_invalid_config_exits_nonzero(tmp_path):
    with pytest.raises(SystemExit) as error:
        config_main(["validate", "--config", str(tmp_path / "missing.yaml")])
    assert error.value.code == 2
