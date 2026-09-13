"""Transport parity and preview guards around the shared ABC importer."""

import io
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from rove.api.abc_import import create_abc_router
from rove.datasets.cli import main


@pytest.fixture
def transport(tmp_path, monkeypatch):
    from rove.datasets import abc

    calls = []
    image = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(image, format="PNG")
    fingerprint = "a" * 64
    prepared = SimpleNamespace(
        fingerprint=fingerprint,
        preview=lambda: {"preview_hash": fingerprint, "payload": {"task": "Put bottles"}},
    )

    def prepare(directory, **options):
        calls.append((directory, options))
        return prepared

    monkeypatch.setattr(abc, "prepare_episode", prepare)

    def persist(service, prepared, *, expected_preview_hash=None):
        assert expected_preview_hash in {None, fingerprint}
        asset = service.trials.save_asset(image.getvalue(), "image/png")
        return service.import_case(
            {"name": "ABC bottles", "task": "Put bottles"},
            asset["sha256"],
            operation_id=fingerprint,
        )

    monkeypatch.setattr(abc, "import_prepared", persist)
    root = tmp_path / "store"
    app = FastAPI()
    app.include_router(create_abc_router(root))
    options = {"episode_id": "episode_1", "source_revision": "b" * 64}
    return TestClient(app), root, calls, options


def uploads():
    return {
        "metadata_file": ("../../escape.json", b"{}", "application/json"),
        "states_file": ("states_actions.bin", b"state", "application/octet-stream"),
        "video_file": ("combined_camera-images-rgb.mp4", b"video", "video/mp4"),
    }


def test_preview_no_store_writes_then_guarded_idempotent_import(transport):
    client, root, calls, options = transport
    data = {"options": json.dumps(options)}
    response = client.post("/api/cases/import/abc/preview", files=uploads(), data=data)
    assert response.status_code == 200, response.text
    assert not root.exists()
    assert not calls[-1][0].exists()  # Temporary uploads are cleaned after processing.
    assert calls[-1][1] == {
        **options,
        "split": "unknown",
        "domain": "unknown",
        "frame_index": 0,
        "camera": "top",
    }
    bad = client.post(
        "/api/cases/import/abc", files=uploads(), data={**data, "preview_hash": "0" * 64}
    )
    assert bad.status_code == 422
    assert not root.exists()
    data["preview_hash"] = response.json()["preview_hash"]
    first = client.post("/api/cases/import/abc", files=uploads(), data=data)
    again = client.post("/api/cases/import/abc", files=uploads(), data=data)
    assert first.status_code == again.status_code == 201
    assert first.json()["id"] == again.json()["id"]


@pytest.mark.parametrize(
    "patch",
    [
        {"frame_index": True},
        {"frame_index": -1},
        {"camera": "rear"},
        {"path": "/tmp/private"},
        {"domain": "physical"},
    ],
)
def test_invalid_options_rejected_before_decoder_or_store(transport, patch):
    client, root, calls, options = transport
    response = client.post(
        "/api/cases/import/abc/preview",
        files=uploads(),
        data={"options": json.dumps({**options, **patch})},
    )
    assert response.status_code == 422
    assert calls == []
    assert not root.exists()


def test_oversize_metadata_and_missing_preview_are_rejected(transport):
    client, root, calls, options = transport
    files = uploads()
    files["metadata_file"] = ("meta.json", b" " * (256 * 1024 + 1), "application/json")
    response = client.post(
        "/api/cases/import/abc/preview", files=files, data={"options": json.dumps(options)}
    )
    assert response.status_code == 413
    response = client.post(
        "/api/cases/import/abc", files=uploads(), data={"options": json.dumps(options)}
    )
    assert response.status_code == 422
    assert calls == []
    assert not root.exists()


def test_cli_preview_uses_same_options_without_creating_store(transport, tmp_path, capsys):
    _, root, calls, options = transport
    main(
        [
            "case",
            "import-abc",
            str(tmp_path),
            "--episode-id",
            options["episode_id"],
            "--source-revision",
            options["source_revision"],
            "--camera",
            "right",
            "--frame-index",
            "4",
            "--domain",
            "sim",
            "--split",
            "val",
            "--preview",
            "--store",
            str(root),
        ]
    )
    assert json.loads(capsys.readouterr().out)["preview_hash"] == "a" * 64
    assert not root.exists()
    assert calls[-1][1] == {
        **options,
        "split": "val",
        "domain": "sim",
        "frame_index": 4,
        "camera": "right",
    }


def test_cli_stale_preview_does_not_create_store(transport, tmp_path, capsys):
    _, root, _, options = transport
    with pytest.raises(SystemExit) as failure:
        main(
            [
                "case",
                "import-abc",
                str(tmp_path),
                "--episode-id",
                options["episode_id"],
                "--source-revision",
                options["source_revision"],
                "--preview-hash",
                "0" * 64,
                "--store",
                str(root),
            ]
        )
    assert failure.value.code == 2
    assert "preview it again" in capsys.readouterr().err
    assert not root.exists()
