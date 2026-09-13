"""Every documented command is reachable without starting the dashboard."""

import json
import sys

import httpx
import pytest

from rove.ui import cli, history_cli
from rove.ui.cli_support import read_document


@pytest.mark.parametrize(
    "command",
    [
        "trial",
        "campaign",
        "baseline",
        "data",
        "strategy",
        "config",
        "assistant",
        "history",
        "benchmark",
        "exchange",
    ],
)
def test_command_help_dispatch(command, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["rove", command, "--help"])
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_recent_history_cli_routes_removal_through_same_running_host(monkeypatch, capsys):
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"deleted": "evaluation-1"})

    client = httpx.Client
    monkeypatch.setattr(
        history_cli.httpx,
        "Client",
        lambda **kw: client(transport=httpx.MockTransport(handle), **kw),
    )
    history_cli.main(["remove", "evaluation-1", "--url", "http://127.0.0.1:5010"])
    assert calls == [("DELETE", "/api/history/evaluation-1")]
    assert json.loads(capsys.readouterr().out) == {"deleted": "evaluation-1"}
    with pytest.raises(SystemExit):
        history_cli.main(["remove", "evaluation-1", "--url", "https://example.com"])
    assert len(calls) == 1


def test_document_input_errors_do_not_echo_private_source(tmp_path):
    document = tmp_path / "input.yaml"
    document.write_text("private_marker: [unclosed")
    with pytest.raises(ValueError, match="valid JSON or YAML") as error:
        read_document(document)
    assert "private_marker" not in str(error.value)


def test_document_stdin_is_bounded(monkeypatch):
    import io

    class BoundedInput(io.StringIO):
        def read(self, size=-1):
            assert size == 16 * 1024 * 1024 + 1
            return super().read(size)

    monkeypatch.setattr(sys, "stdin", BoundedInput("x" * (16 * 1024 * 1024 + 1)))
    with pytest.raises(ValueError, match="exceeds 16 MiB"):
        read_document("-")
