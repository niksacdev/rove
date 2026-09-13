"""Local assistant automation retains explicit host confirmation and token boundaries."""

import json
import stat

import httpx
import pytest

from rove.ui import assistant_cli


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://192.168.1.3:5001",
        "http://localhost:5001",
        "http://user:password@127.0.0.1",  # pragma: allowlist secret - rejected dummy URL
        "http://127.0.0.1/path",
        "http://127.0.0.1?token=x",
    ],
)
def test_assistant_rejects_remote_or_ambiguous_targets(url):
    with pytest.raises(ValueError):
        assistant_cli.local_url(url)


def test_assistant_proposal_file_is_private_and_confirmed_explicitly(tmp_path, monkeypatch, capsys):
    token = "test-confirmation-" + "x" * 32
    proposals = [
        {"operation_id": "proposal-1", "confirmation_token": token, "preview": {"ready": True}}
    ]
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("ask"):
            return httpx.Response(
                200, json={"answer": "Review this proposal", "proposals": proposals}
            )
        assert json.loads(request.content)["confirmed"] is True
        assert json.loads(request.content)["confirmation_token"] == token
        return httpx.Response(200, json={"created": True})

    client = httpx.Client
    monkeypatch.setattr(
        assistant_cli.httpx,
        "Client",
        lambda **kw: client(transport=httpx.MockTransport(handler), **kw),
    )
    request = tmp_path / "ask.json"
    request.write_text(json.dumps({"message": "Prepare a campaign"}))
    saved = tmp_path / "proposal.json"
    assistant_cli.main(["ask", str(request), "--proposal-output", str(saved)])
    assert token not in capsys.readouterr().out
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert json.loads(saved.read_text())["proposals"][0]["confirmation_token"] == token
    with pytest.raises(SystemExit):
        assistant_cli.main(["confirm", str(saved)])
    assert len(calls) == 1
    with pytest.raises(SystemExit):
        assistant_cli.main(["confirm", str(saved), "--confirmed", "--url", "http://127.0.0.1:5002"])
    assert len(calls) == 1
    assistant_cli.main(["confirm", str(saved), "--confirmed"])
    assert json.loads(capsys.readouterr().out)["created"] is True


def test_assistant_failure_does_not_echo_token(tmp_path, monkeypatch, capsys):
    token = "test-confirmation-" + "z" * 32
    saved = tmp_path / "proposal.json"
    saved.write_text(
        json.dumps(
            {
                "server_url": "http://127.0.0.1:5001",
                "proposals": [{"operation_id": "op", "confirmation_token": token}],
            }
        )
    )
    client = httpx.Client
    monkeypatch.setattr(
        assistant_cli.httpx,
        "Client",
        lambda **kw: client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(409, json={"detail": token})
            ),
            **kw,
        ),
    )
    with pytest.raises(SystemExit) as error:
        assistant_cli.main(["confirm", str(saved), "--confirmed"])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert token not in output.out + output.err


def test_invalid_confirmation_does_not_echo_rejected_token(tmp_path, capsys):
    token = "private-confirmation-value-" * 20
    saved = tmp_path / "invalid.json"
    saved.write_text(
        json.dumps(
            {
                "server_url": "http://127.0.0.1:5001",
                "proposals": [{"operation_id": "op", "confirmation_token": token}],
            }
        )
    )
    with pytest.raises(SystemExit) as error:
        assistant_cli.main(["confirm", str(saved), "--confirmed"])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert "private-confirmation" not in output.out + output.err
    assert "string_too_long" in output.err
