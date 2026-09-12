"""Export must satisfy the same uncompressed limits its importer enforces."""

import zipfile

import pytest

from rove.trials import exchange
from rove.trials.store import TrialStore


def test_export_budget_includes_manifest_and_exact_limit_roundtrips(tmp_path, monkeypatch):
    root = tmp_path / "source"
    TrialStore(root)
    archive = tmp_path / "original.zip"
    exchange.export_bundle(root, archive)
    with zipfile.ZipFile(archive) as bundle:
        total = sum(info.file_size for info in bundle.infolist())
        manifest_size = bundle.getinfo("manifest.json").file_size
    assert manifest_size > 0
    monkeypatch.setattr(exchange, "MAX_ARCHIVE_BYTES", total - 1)
    with pytest.raises(ValueError, match="Exchange exceeds"):
        exchange.export_bundle(root, tmp_path / "too-large.zip")
    assert not (tmp_path / "too-large.zip").exists()
    monkeypatch.setattr(exchange, "MAX_ARCHIVE_BYTES", total)
    exchange.export_bundle(root, tmp_path / "exact.zip")
    exchange.import_bundle(tmp_path / "exact.zip", tmp_path / "restored")
    assert TrialStore(tmp_path / "restored").list() == []


def test_export_manifest_limit_is_enforced_before_publication(tmp_path, monkeypatch):
    monkeypatch.setattr(exchange, "MAX_MANIFEST_BYTES", 1)
    with pytest.raises(ValueError, match="manifest exceeds"):
        exchange.export_bundle(tmp_path / "source", tmp_path / "too-large.zip")
    assert not (tmp_path / "too-large.zip").exists()
