"""Durable local campaign records, kept outside the public data mount."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


def now() -> str:
    return datetime.now(UTC).isoformat()


class CampaignStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS campaigns (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS trials (
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id),
                    task_id TEXT NOT NULL, strategy_id TEXT NOT NULL, seed INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (campaign_id, task_id, strategy_id, seed));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / "campaigns.sqlite3", timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, payload: dict) -> str:
        campaign_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO campaigns VALUES (?, ?, 'pending', ?)",
                (campaign_id, now(), json.dumps(payload, allow_nan=False)),
            )
        return campaign_id

    def get(self, campaign_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if row is None:
            raise KeyError(campaign_id)
        return {
            **json.loads(row["payload"]),
            "id": row["id"],
            "created_at": row["created_at"],
            "status": row["status"],
        }

    def list(self) -> list[dict]:
        with self.connect() as db:
            ids = db.execute("SELECT id FROM campaigns ORDER BY created_at DESC").fetchall()
        return [self.get(row["id"]) for row in ids]

    def status(self, campaign_id: str, status: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE campaigns SET status=? WHERE id=?", (status, campaign_id))

    def save_trial(self, campaign_id: str, result: dict) -> None:
        # Completed attempts are immutable: resume can never overwrite a failed attempt.
        with self.connect() as db:
            db.execute(
                "INSERT INTO trials VALUES (?, ?, ?, ?, ?)",
                (
                    campaign_id,
                    result["task_id"],
                    result["strategy_id"],
                    result["seed"],
                    json.dumps(result, allow_nan=False),
                ),
            )

    def trials(self, campaign_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM trials WHERE campaign_id=? ORDER BY task_id, strategy_id, seed",
                (campaign_id,),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def finish_trial(self, campaign_id: str, result: dict) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE trials SET payload=? WHERE campaign_id=? AND task_id=? AND strategy_id=? AND seed=?",
                (
                    json.dumps(result, allow_nan=False),
                    campaign_id,
                    result["task_id"],
                    result["strategy_id"],
                    result["seed"],
                ),
            )
