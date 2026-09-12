"""SQLite trial journal with immutable completion and private, addressed assets."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from rove.trials.snapshots import canonical_json, sanitize, snapshot

SCHEMA_VERSION = 2
MAX_EVENT_BYTES = 256 * 1024
MAX_RECORD_BYTES = 8 * 1024 * 1024
MAX_ASSET_BYTES = 64 * 1024 * 1024
TERMINAL = frozenset({"completed", "error", "failed", "cancelled", "interrupted", "timeout"})


def now() -> str:
    return datetime.now(UTC).isoformat()


def _encode(value: object, limit: int = MAX_RECORD_BYTES) -> str:
    encoded = canonical_json(sanitize(value))
    if len(encoded.encode()) > limit:
        raise ValueError(f"Record exceeds {limit} bytes; store large evidence as an asset")
    return encoded


class TrialStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "trials.sqlite3"
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1, SCHEMA_VERSION}:
                raise ValueError(f"Unsupported trial schema version: {version}")
            if version == 0:
                for statement in (
                    """CREATE TABLE trials (
                        id TEXT PRIMARY KEY, source TEXT NOT NULL, campaign_id TEXT,
                        seed INTEGER, created_at TEXT NOT NULL, finished_at TEXT,
                        status TEXT NOT NULL CHECK(status IN
                            ('running','completed','error','failed','cancelled','interrupted','timeout')),
                        task TEXT NOT NULL, strategy TEXT NOT NULL, snapshot TEXT,
                        result TEXT, error TEXT, legacy_key TEXT UNIQUE)""",
                    """CREATE TABLE events (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        trial_id TEXT NOT NULL REFERENCES trials(id),
                        event_id TEXT NOT NULL, source TEXT NOT NULL, source_event_id TEXT,
                        recorded_at TEXT NOT NULL, payload TEXT NOT NULL,
                        UNIQUE(trial_id, source, source_event_id))""",
                    "CREATE INDEX trial_history ON trials(created_at DESC, id DESC)",
                    "CREATE INDEX trial_source_history ON trials(source, created_at DESC, id DESC)",
                    "CREATE INDEX trial_campaign ON trials(campaign_id)",
                    "CREATE INDEX trial_events ON events(trial_id, sequence)",
                    """CREATE TABLE assets (
                        sha256 TEXT PRIMARY KEY, size_bytes INTEGER NOT NULL,
                        media_type TEXT NOT NULL)""",
                ):
                    db.execute(statement)
            if version < 2:
                from rove.datasets.schema import migrate_v2

                migrate_v2(db)
                db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def begin(
        self,
        *,
        source: str,
        task: dict,
        strategy: dict,
        config: dict,
        campaign_id: str | None = None,
        seed: int | None = None,
        trial_id: str | None = None,
    ) -> str:
        trial_id = trial_id or uuid.uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", trial_id):
            raise ValueError("Invalid trial ID")
        if not source or len(source) > 64:
            raise ValueError("A source of at most 64 characters is required")
        frozen = snapshot(task, strategy, config)
        with self.connect() as db:
            db.execute(
                """INSERT INTO trials
                    (id,source,campaign_id,seed,created_at,status,task,strategy,snapshot)
                    VALUES (?,?,?,?,?,'running',?,?,?)""",
                (
                    trial_id,
                    source,
                    campaign_id,
                    seed,
                    now(),
                    _encode(frozen["task"]),
                    _encode(frozen["strategy"]),
                    _encode(frozen),
                ),
            )
        return trial_id

    def finish(
        self,
        trial_id: str,
        *,
        status: str,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        if status not in TERMINAL:
            raise ValueError("finish requires a terminal status")
        encoded = _encode(result) if result is not None else None
        error = sanitize(error)
        if error is not None and (not isinstance(error, str) or len(error) > 16384):
            raise ValueError("Error must be a bounded string")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM trials WHERE id=?", (trial_id,)).fetchone()
            if row is None:
                raise KeyError(trial_id)
            if row["status"] != "running":
                if (row["status"], row["result"], row["error"]) == (status, encoded, error):
                    return
                raise ValueError("Completed trial records are immutable")
            db.execute(
                "UPDATE trials SET status=?,result=?,error=?,finished_at=? WHERE id=?",
                (status, encoded, error, now(), trial_id),
            )

    def append_event(self, trial_id: str, event: dict) -> bool:
        """Durably append; repeated producer event IDs are acknowledged without replay."""
        encoded = _encode(event, MAX_EVENT_BYTES)
        source = str(event.get("source", "rove"))
        source_id = event.get("source_event_id")
        if source_id is not None:
            source_id = str(source_id)
        if len(source) > 128 or (source_id is not None and len(source_id) > 512):
            raise ValueError("Event source identity is too long")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM trials WHERE id=?", (trial_id,)).fetchone()
            if row is None:
                raise KeyError(trial_id)
            duplicate = (
                db.execute(
                    "SELECT payload FROM events WHERE trial_id=? AND source=? AND source_event_id=?",
                    (trial_id, source, source_id),
                ).fetchone()
                if source_id is not None
                else None
            )
            if duplicate:
                if duplicate["payload"] != encoded:
                    raise ValueError("Producer event ID reused with different content")
                return False
            if row["status"] != "running":
                raise ValueError("Completed trial records are immutable")
            db.execute(
                """INSERT INTO events
                    (trial_id,event_id,source,source_event_id,recorded_at,payload)
                    VALUES (?,?,?,?,?,?)""",
                (trial_id, uuid.uuid4().hex, source, source_id, now(), encoded),
            )
        return True

    @staticmethod
    def _record(row: sqlite3.Row) -> dict:
        record = dict(row)
        for name in ("task", "strategy", "snapshot", "result"):
            record[name] = json.loads(record[name]) if record[name] is not None else None
        record["snapshot_id"] = (record["snapshot"] or {}).get("sha256")
        record.pop("legacy_key", None)
        return record

    def get(self, trial_id: str) -> dict:
        with self.connect() as db:
            row = db.execute(
                """SELECT t.*, (SELECT count(*) FROM events e WHERE e.trial_id=t.id)
                    AS event_count FROM trials t WHERE id=?""",
                (trial_id,),
            ).fetchone()
        if row is None:
            raise KeyError(trial_id)
        return self._record(row)

    def list(
        self,
        limit: int = 50,
        offset: int = 0,
        source: str | None = None,
        case_revision_id: str | None = None,
    ) -> list[dict]:
        self._page(limit, offset)
        query = (
            """SELECT t.*, (SELECT count(*) FROM events e WHERE e.trial_id=t.id)
                AS event_count FROM trials t WHERE source=?
                ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?"""
            if source is not None
            else """SELECT t.*, (SELECT count(*) FROM events e WHERE e.trial_id=t.id)
                AS event_count FROM trials t
                ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?"""
        )
        args = (source, limit, offset) if source is not None else (limit, offset)
        if case_revision_id is not None:
            query = (
                """SELECT t.*, (SELECT count(*) FROM events e WHERE e.trial_id=t.id)
                    AS event_count FROM trials t WHERE json_extract(task,'$.case_revision_id')=?
                    AND source=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?"""
                if source is not None
                else """SELECT t.*, (SELECT count(*) FROM events e WHERE e.trial_id=t.id)
                    AS event_count FROM trials t WHERE json_extract(task,'$.case_revision_id')=?
                    ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?"""
            )
            args = (
                (case_revision_id, source, limit, offset)
                if source is not None
                else (case_revision_id, limit, offset)
            )
        with self.connect() as db:
            rows = db.execute(query, args).fetchall()
        return [self._record(row) for row in rows]

    def count(self, source: str | None = None, case_revision_id: str | None = None) -> int:
        with self.connect() as db:
            if case_revision_id is not None:
                if source is not None:
                    return db.execute(
                        "SELECT count(*) FROM trials WHERE json_extract(task,'$.case_revision_id')=? AND source=?",
                        (case_revision_id, source),
                    ).fetchone()[0]
                return db.execute(
                    "SELECT count(*) FROM trials WHERE json_extract(task,'$.case_revision_id')=?",
                    (case_revision_id,),
                ).fetchone()[0]
            if source is None:
                return db.execute("SELECT count(*) FROM trials").fetchone()[0]
            return db.execute("SELECT count(*) FROM trials WHERE source=?", (source,)).fetchone()[0]

    def for_campaign(self, campaign_id: str) -> Iterator[dict]:
        """Iterate all linked trials in bounded pages for ownership-locked recovery."""
        after = ""
        while True:
            with self.connect() as db:
                ids = db.execute(
                    """SELECT id FROM trials WHERE campaign_id=? AND id>?
                        ORDER BY id LIMIT 200""",
                    (campaign_id, after),
                ).fetchall()
            if not ids:
                return
            for row in ids:
                yield self.get(row["id"])
            after = ids[-1]["id"]

    @staticmethod
    def _page(limit: int, offset: int) -> None:
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("Page limit must be 1..1000 and offset nonnegative")

    def events(self, trial_id: str, limit: int = 200, offset: int = 0) -> list[dict]:
        self._page(limit, offset)
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM trials WHERE id=?", (trial_id,)).fetchone():
                raise KeyError(trial_id)
            rows = db.execute(
                "SELECT * FROM events WHERE trial_id=? ORDER BY sequence LIMIT ? OFFSET ?",
                (trial_id, limit, offset),
            ).fetchall()
        return [
            {
                **json.loads(row["payload"]),
                "event_id": row["event_id"],
                "sequence": row["sequence"],
                "recorded_at": row["recorded_at"],
            }
            for row in rows
        ]

    def recover_interrupted(self, *, source: str | None = None) -> int:
        """Mark abandoned attempts unknown; call only after ruling out active workers."""
        with self.connect() as db:
            query = (
                """UPDATE trials SET status='interrupted',finished_at=?,
                    error='Execution interrupted before a terminal record was committed'
                    WHERE status='running' AND source=?"""
                if source is not None
                else """UPDATE trials SET status='interrupted',finished_at=?,
                    error='Execution interrupted before a terminal record was committed'
                    WHERE status='running'"""
            )
            args = (now(), source) if source is not None else (now(),)
            return db.execute(query, args).rowcount

    def save_asset(self, data: bytes, media_type: str) -> dict:
        if not isinstance(data, bytes) or len(data) > MAX_ASSET_BYTES:
            raise ValueError("Asset must be bytes of at most 64 MiB")
        if not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", media_type):
            raise ValueError("Invalid media type")
        digest = hashlib.sha256(data).hexdigest()
        directory = self.root / "assets"
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink():
            raise ValueError("Managed asset directory must not be a symbolic link")
        destination = directory / digest
        fd, temporary = tempfile.mkstemp(prefix=".incoming-", dir=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            with self.connect() as db:
                db.execute(
                    "INSERT OR IGNORE INTO assets VALUES (?,?,?)", (digest, len(data), media_type)
                )
                row = db.execute("SELECT * FROM assets WHERE sha256=?", (digest,)).fetchone()
            return dict(row)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def asset_path(self, digest: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid asset digest")
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM assets WHERE sha256=?", (digest,)).fetchone():
                raise KeyError(digest)
        path = self.root / "assets" / digest
        if path.parent.is_symlink() or path.is_symlink() or not path.is_file():
            raise FileNotFoundError(digest)
        return path

    def asset_metadata(self, digest: str) -> dict:
        self.asset_path(digest)
        with self.connect() as db:
            row = db.execute("SELECT * FROM assets WHERE sha256=?", (digest,)).fetchone()
        return dict(row)

    def backup(self, path: Path) -> None:
        """Create a consistent SQLite backup; assets remain separate immutable files."""
        path = Path(path)
        if path.resolve() == self.path.resolve():
            raise ValueError("Backup destination must differ from live database")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as source, sqlite3.connect(path) as destination:
            source.backup(destination)
        path.chmod(0o600)

    def import_jsonl(self, path: Path) -> int:
        """Import legacy attempts atomically and idempotently, leaving the source untouched.

        A legacy launch containing several strategy results becomes several trials.
        Missing historical configuration remains null instead of using today's settings.
        """
        count = 0
        with self.connect() as db, Path(path).open() as source:
            db.execute("BEGIN IMMEDIATE")
            for line_number, line in enumerate(source, 1):
                if not line.strip():
                    continue
                if len(line.encode()) > MAX_RECORD_BYTES:
                    raise ValueError(f"Legacy line {line_number} exceeds size limit")
                record = json.loads(line)
                if not isinstance(record, dict) or not isinstance(record.get("eval_id"), str):
                    raise ValueError(f"Legacy line {line_number} requires an eval_id")
                trial_ids = record.get("trial_ids")
                known_ids = set()
                if isinstance(trial_ids, dict):
                    for trial_id in trial_ids.values():
                        if (
                            isinstance(trial_id, str)
                            and db.execute(
                                "SELECT 1 FROM trials WHERE id=?",
                                (trial_id,),
                            ).fetchone()
                        ):
                            known_ids.add(trial_id)
                    # Current history JSONL is a projection of already durable trials.
                    # A partial/missing reference is retained below, never discarded.
                    if (
                        trial_ids
                        and all(
                            value in known_ids
                            for value in trial_ids.values()
                            if isinstance(value, str)
                        )
                        and all(isinstance(value, str) for value in trial_ids.values())
                    ):
                        continue
                results = record.get("results") or [None]
                if isinstance(results, dict):
                    results = list(results.values()) or [None]
                if not isinstance(results, list) or any(
                    value is not None and not isinstance(value, dict) for value in results
                ):
                    raise ValueError(f"Invalid legacy results on line {line_number}")
                for index, result in enumerate(results):
                    if (
                        isinstance(trial_ids, dict)
                        and result is not None
                        and trial_ids.get(result.get("strategy_id")) in known_ids
                    ):
                        continue
                    legacy_key = canonical_json([record["eval_id"], index])
                    trial_id = uuid.uuid5(uuid.NAMESPACE_URL, f"rove:legacy:{legacy_key}").hex
                    task = record.get("task")
                    task = task if isinstance(task, dict) else {"instruction": task}
                    strategy = {"id": (result or {}).get("strategy_id")}
                    status = record.get("status", "completed")
                    status = status if status in TERMINAL else "interrupted"
                    inserted = db.execute(
                        """INSERT OR IGNORE INTO trials
                        (id,source,created_at,finished_at,status,task,strategy,snapshot,result,legacy_key)
                        VALUES (?,'legacy',?,?,?,?,?,?,?,?)""",
                        (
                            trial_id,
                            record.get("timestamp") or "unknown",
                            record.get("timestamp"),
                            status,
                            _encode(task),
                            _encode(strategy),
                            _encode(
                                {
                                    "provenance_status": "legacy_partial",
                                    "legacy_provenance": record["provenance"],
                                }
                            )
                            if record.get("provenance") is not None
                            else None,
                            _encode(result) if result is not None else None,
                            legacy_key,
                        ),
                    )
                    count += inserted.rowcount
        return count
