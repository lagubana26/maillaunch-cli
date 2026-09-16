"""
Persistent campaign state, backed by SQLite.

A campaign is a single `send` invocation over a CSV file. Every
recipient row gets a status (PENDING / SENT / FAILED / SKIPPED) so
that an interrupted campaign can be resumed without re-sending to
anyone already successfully emailed. Daily send counts are derived
from the SENT rows' timestamps, so the 100/day limit holds correctly
across multiple campaigns run on the same day.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, List, Optional

DEFAULT_DB_PATH = Path.home() / ".maillaunch" / "state.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    csv_path TEXT NOT NULL,
    subject_template TEXT NOT NULL,
    body_template TEXT NOT NULL,
    email_column TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'IN_PROGRESS'  -- IN_PROGRESS | COMPLETE
);

CREATE TABLE IF NOT EXISTS recipients (
    campaign_id TEXT NOT NULL,
    row_index INTEGER NOT NULL,
    email TEXT NOT NULL,
    name TEXT,
    data_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING|SENT|FAILED|SKIPPED
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    sent_at TEXT,
    PRIMARY KEY (campaign_id, row_index)
);

CREATE INDEX IF NOT EXISTS idx_recipients_status
    ON recipients (campaign_id, status);
"""


@dataclass
class CampaignRecord:
    id: str
    provider: str
    csv_path: str
    subject_template: str
    body_template: str
    email_column: Optional[str]
    created_at: str
    status: str


def new_campaign_id() -> str:
    return f"c_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


class StateStore:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    # -- Campaign lifecycle -------------------------------------------------

    def create_campaign(
        self,
        provider: str,
        csv_path: str,
        subject_template: str,
        body_template: str,
        email_column: Optional[str],
        recipients: list,  # List[csv_parser.Recipient]
    ) -> str:
        import json

        campaign_id = new_campaign_id()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO campaigns (id, provider, csv_path, subject_template, "
                "body_template, email_column, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'IN_PROGRESS')",
                (
                    campaign_id,
                    provider,
                    csv_path,
                    subject_template,
                    body_template,
                    email_column,
                    datetime.now().isoformat(),
                ),
            )
            conn.executemany(
                "INSERT INTO recipients (campaign_id, row_index, email, name, "
                "data_json, status) VALUES (?, ?, ?, ?, ?, 'PENDING')",
                [
                    (
                        campaign_id,
                        r.row_index,
                        r.email,
                        r.data.get("name") or r.data.get("Name") or "",
                        json.dumps(r.data),
                    )
                    for r in recipients
                ],
            )
        return campaign_id

    def get_campaign(self, campaign_id: str) -> Optional[CampaignRecord]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
            ).fetchone()
        return CampaignRecord(**dict(row)) if row else None

    def latest_incomplete_campaign(self) -> Optional[CampaignRecord]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM campaigns WHERE status = 'IN_PROGRESS' "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return CampaignRecord(**dict(row)) if row else None

    def mark_campaign_complete(self, campaign_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE campaigns SET status = 'COMPLETE' WHERE id = ?",
                (campaign_id,),
            )

    # -- Recipients -----------------------------------------------------

    def pending_recipients(self, campaign_id: str) -> List[sqlite3.Row]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM recipients WHERE campaign_id = ? AND status = 'PENDING' "
                "ORDER BY row_index",
                (campaign_id,),
            ).fetchall()
        return rows

    def mark_sent(self, campaign_id: str, row_index: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE recipients SET status = 'SENT', sent_at = ?, "
                "attempts = attempts + 1 WHERE campaign_id = ? AND row_index = ?",
                (datetime.now().isoformat(), campaign_id, row_index),
            )

    def mark_failed(self, campaign_id: str, row_index: int, error: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE recipients SET status = 'FAILED', last_error = ?, "
                "attempts = attempts + 1 WHERE campaign_id = ? AND row_index = ?",
                (error, campaign_id, row_index),
            )

    def mark_skipped(self, campaign_id: str, row_index: int, reason: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE recipients SET status = 'SKIPPED', last_error = ? "
                "WHERE campaign_id = ? AND row_index = ?",
                (reason, campaign_id, row_index),
            )

    def increment_attempts(self, campaign_id: str, row_index: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE recipients SET attempts = attempts + 1 "
                "WHERE campaign_id = ? AND row_index = ?",
                (campaign_id, row_index),
            )

    def campaign_summary(self, campaign_id: str) -> dict:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as n FROM recipients "
                "WHERE campaign_id = ? GROUP BY status",
                (campaign_id,),
            ).fetchall()
        counts = {r["status"]: r["n"] for r in rows}
        return {
            "sent": counts.get("SENT", 0),
            "failed": counts.get("FAILED", 0),
            "skipped": counts.get("SKIPPED", 0),
            "pending": counts.get("PENDING", 0),
        }

    # -- Daily limit ------------------------------------------------------

    def sent_count_today(self, on_date: Optional[date] = None) -> int:
        on_date = on_date or date.today()
        prefix = on_date.isoformat()  # 'YYYY-MM-DD', matches isoformat() prefix
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as n FROM recipients "
                "WHERE status = 'SENT' AND sent_at LIKE ?",
                (f"{prefix}%",),
            ).fetchone()
        return row["n"] if row else 0
