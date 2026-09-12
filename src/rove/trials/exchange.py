"""Versioned relational exchange: JSONL tables and addressed assets, never a database replacement."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from rove.benchmarks.store import CampaignStore
from rove.trials.evidence import verified_asset
from rove.trials.snapshots import canonical_json
from rove.trials.store import SCHEMA_VERSION, TrialStore

FORMAT_VERSION = 1
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
TABLES = {
    "trials": (
        "trials",
        "events",
        "assets",
        "success_contracts",
        "cases",
        "case_revisions",
        "reviews",
        "dataset_revisions",
        "dataset_members",
        "dataset_member_reviews",
        "quick_promotions",
        "evidence_refs",
        "named_baselines",
        "baseline_revisions",
        "assistant_operations",
        "workflow_mutations",
    ),
    "campaigns": ("campaigns", "trials"),
}


def _table(db, table, remaining):
    # Both database and table names are selected exclusively from TABLES.
    if not any(table in names for names in TABLES.values()):
        raise ValueError("Unsupported exchange table")
    columns = [dict(r) for r in db.execute(f'PRAGMA table_info("{table}")')]
    data = bytearray()
    count = 0
    for row in db.execute(f'SELECT * FROM "{table}"'):  # nosec B608
        encoded = (canonical_json(dict(row)) + "\n").encode()
        if len(data) + len(encoded) > remaining:
            raise ValueError("Exchange exceeds 512 MiB; partition the dataset before export")
        data.extend(encoded)
        count += 1
    return columns, count, bytes(data)


def export_bundle(root: Path, output: Path) -> dict:
    """Consistent read transactions; exporting running evaluations is rejected."""
    root, output = Path(root), Path(output)
    if output.exists():
        raise ValueError("Export destination already exists")
    store, campaigns = TrialStore(root), CampaignStore(root)
    files, tables, assets = {}, [], []
    with store.connect() as trials_db, campaigns.connect() as campaign_db:
        trials_db.execute("BEGIN")
        campaign_db.execute("BEGIN")
        if trials_db.execute("SELECT count(*) FROM trials WHERE status='running'").fetchone()[0]:
            raise ValueError("Finish active trials before exporting a frozen exchange")
        if campaign_db.execute(
            "SELECT count(*) FROM campaigns WHERE status IN ('pending','running')"
        ).fetchone()[0]:
            raise ValueError("Finish active campaigns before exporting a frozen exchange")
        for database, names in TABLES.items():
            db = trials_db if database == "trials" else campaign_db
            for name in names:
                columns, row_count, data = _table(
                    db, name, MAX_ARCHIVE_BYTES - sum(map(len, files.values()))
                )
                path = f"tables/{database}/{name}.jsonl"
                files[path] = data
                tables.append(
                    {
                        "database": database,
                        "name": name,
                        "path": path,
                        "columns": [{"name": c["name"], "type": c["type"]} for c in columns],
                        "rows": row_count,
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
        for row in trials_db.execute("SELECT * FROM assets ORDER BY sha256"):
            if row["size_bytes"] + sum(map(len, files.values())) > MAX_ARCHIVE_BYTES:
                raise ValueError("Exchange exceeds 512 MiB; partition the dataset before export")
            metadata, data = verified_asset(store, row["sha256"])
            path = f"assets/{row['sha256']}"
            files[path] = data
            assets.append({**metadata, "path": path})
    _check_lineage(
        {
            (database, name): [
                json.loads(line) for line in files[f"tables/{database}/{name}.jsonl"].splitlines()
            ]
            for database, name in (
                ("trials", "trials"),
                ("campaigns", "campaigns"),
                ("campaigns", "trials"),
            )
        }
    )
    total = sum(len(data) for data in files.values())
    if total > MAX_ARCHIVE_BYTES:
        raise ValueError("Exchange exceeds 512 MiB; partition the dataset before export")
    manifest = {
        "format": "rove-relational-exchange",
        "version": FORMAT_VERSION,
        "trial_schema_version": SCHEMA_VERSION,
        "tables": tables,
        "assets": assets,
        "note": "Private evaluation data. JSON columns retain their original payloads; no grades, timestamps or lineage are regenerated.",
    }
    manifest_bytes = canonical_json(manifest).encode()
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise ValueError("Exchange manifest exceeds limit")
    if total + len(manifest_bytes) > MAX_ARCHIVE_BYTES:
        raise ValueError("Exchange exceeds 512 MiB; partition the dataset before export")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".rove-export-", dir=output.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", manifest_bytes)
            for path, data in files.items():
                archive.writestr(path, data)
        # Link provides no-clobber publication, including concurrent exporters.
        os.link(temp, output)
    finally:
        Path(temp).unlink(missing_ok=True)
    return manifest


def _publish_directory(source: Path, destination: Path) -> None:
    """Atomic no-replace publication on the supported macOS/Linux local platforms."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        # RENAME_EXCL from the platform's sys/stdio.h.
        result = rename(os.fsencode(source), os.fsencode(destination), 0x00000004)
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        rename = libc.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        # AT_FDCWD and RENAME_NOREPLACE from Linux fcntl/renameat2.
        result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    else:
        raise ValueError("Atomic exchange import requires macOS or Linux rename support")
    if result:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def _check_lineage(rows) -> None:
    """Reject torn cross-database snapshots; neither DB has cross-file SQL FKs."""
    campaigns = {r["id"]: r for r in rows[("campaigns", "campaigns")]}
    trials = {r["id"]: r for r in rows[("trials", "trials")]}
    if any(r["status"] in {"pending", "running"} for r in campaigns.values()):
        raise ValueError("Exchange contains active campaigns")
    if any(r["status"] == "running" for r in trials.values()):
        raise ValueError("Exchange contains active trials")
    if any(r.get("campaign_id") and r["campaign_id"] not in campaigns for r in trials.values()):
        raise ValueError("Exchange contains missing campaign lineage")
    for row in rows[("campaigns", "trials")]:
        payload = json.loads(row["payload"])
        identity = payload.get("trial_id")
        if identity and (
            identity not in trials or trials[identity]["campaign_id"] != row["campaign_id"]
        ):
            raise ValueError("Exchange contains inconsistent shared trial lineage")


def import_bundle(archive_path: Path, destination: Path) -> dict:
    """Validate hashes/schema/FKs in staging and publish only to a fresh directory."""
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Import requires a new destination directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".rove-import-", dir=destination.parent))
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or "manifest.json" not in names:
                raise ValueError("Duplicate archive entries or missing manifest")
            if sum(info.file_size for info in archive.infolist()) > MAX_ARCHIVE_BYTES:
                raise ValueError("Exchange expands beyond 512 MiB")
            if archive.getinfo("manifest.json").file_size > MAX_MANIFEST_BYTES:
                raise ValueError("Exchange manifest exceeds limit")
            manifest = json.loads(archive.read("manifest.json"))
            if (
                manifest.get("format"),
                manifest.get("version"),
                manifest.get("trial_schema_version"),
            ) != ("rove-relational-exchange", FORMAT_VERSION, SCHEMA_VERSION):
                raise ValueError("Unsupported exchange format or schema version")
            expected_tables = {
                (database, name) for database, tables in TABLES.items() for name in tables
            }
            table_specs = manifest.get("tables", [])
            if {(t["database"], t["name"]) for t in table_specs} != expected_tables or len(
                table_specs
            ) != len(expected_tables):
                raise ValueError("Exchange table membership does not match schema")
            allowed = {"manifest.json"}
            rows = {}
            for table in table_specs:
                database, name = table["database"], table["name"]
                path = f"tables/{database}/{name}.jsonl"
                if table["path"] != path:
                    raise ValueError("Invalid table path")
                allowed.add(path)
                data = archive.read(path)
                if hashlib.sha256(data).hexdigest() != table["sha256"]:
                    raise ValueError("Exchange table hash mismatch")
                values = [json.loads(line) for line in data.splitlines() if line.strip()]
                if len(values) != table["rows"]:
                    raise ValueError("Exchange row count mismatch")
                rows[(database, name)] = values
            store, campaigns = TrialStore(stage), CampaignStore(stage)
            for asset in manifest.get("assets", []):
                digest = asset["sha256"]
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(c not in "0123456789abcdef" for c in digest)
                ):
                    raise ValueError("Invalid asset identity")
                path = f"assets/{digest}"
                if asset["path"] != path or path in allowed:
                    raise ValueError("Invalid or duplicate asset path")
                allowed.add(path)
                data = archive.read(path)
                if hashlib.sha256(data).hexdigest() != digest or len(data) != asset["size_bytes"]:
                    raise ValueError("Exchange asset integrity check failed")
                store.save_asset(data, asset["media_type"])
            if set(names) != allowed:
                raise ValueError("Unexpected exchange entry")
            asset_rows = rows[("trials", "assets")]
            expected_assets = {r["sha256"] for r in asset_rows}
            if expected_assets != {a["sha256"] for a in manifest["assets"]}:
                raise ValueError("Asset table and manifest disagree")
            for database, factory in (("trials", store), ("campaigns", campaigns)):
                with factory.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("PRAGMA defer_foreign_keys=ON")
                    for name in TABLES[database]:
                        spec = next(
                            t
                            for t in table_specs
                            if t["database"] == database and t["name"] == name
                        )
                        columns = [r["name"] for r in db.execute(f'PRAGMA table_info("{name}")')]
                        actual = [
                            {"name": r["name"], "type": r["type"]}
                            for r in db.execute(f'PRAGMA table_info("{name}")')
                        ]
                        if actual != spec["columns"]:
                            raise ValueError("Exchange column schema mismatch")
                        values = rows[(database, name)]
                        if any(
                            not isinstance(row, dict) or set(row) != set(columns) for row in values
                        ):
                            raise ValueError("Exchange row columns mismatch")
                        if name == "assets" and database == "trials":
                            saved = [
                                dict(r) for r in db.execute("SELECT * FROM assets ORDER BY sha256")
                            ]
                            if sorted(saved, key=lambda r: r["sha256"]) != sorted(
                                values, key=lambda r: r["sha256"]
                            ):
                                raise ValueError("Asset metadata differs from manifest")
                            continue
                        placeholders = ",".join("?" for _ in columns)
                        # Identifiers derive from this installed schema, never archive SQL.
                        db.executemany(
                            f'INSERT INTO "{name}" VALUES ({placeholders})',  # nosec B608
                            [tuple(row[c] for c in columns) for row in values],
                        )
                    if db.execute("PRAGMA foreign_key_check").fetchall():
                        raise ValueError("Exchange has broken relational references")
            if any(row["status"] == "running" for row in rows[("trials", "trials")]):
                raise ValueError("Exchange contains active trials")
        _check_lineage(rows)
        _publish_directory(stage, destination)
        return manifest
    finally:
        shutil.rmtree(stage, ignore_errors=True)
