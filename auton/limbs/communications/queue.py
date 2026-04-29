"""SQLite-backed persistent queue for unsent approval emails."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class QueuedEmail:
    """A single email waiting in the persistent queue."""

    id: int
    recipient: str
    subject: str
    text_body: str
    html_body: str
    proposal_token: str
    retry_count: int
    next_retry_at: datetime
    created_at: datetime


class EmailQueue:
    """Persistent SQLite queue for emails that failed to send.

    Emails are stored in ``data/email_queue.db`` by default and retried
    with exponential backoff by the :class:`EmailClient`.
    """

    _DDL = """
    CREATE TABLE IF NOT EXISTS pending_emails (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        recipient      TEXT    NOT NULL,
        subject        TEXT    NOT NULL,
        text_body      TEXT    NOT NULL,
        html_body      TEXT    NOT NULL,
        proposal_token TEXT    NOT NULL,
        retry_count    INTEGER NOT NULL DEFAULT 0,
        next_retry_at  TEXT    NOT NULL,
        created_at     TEXT    NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_next_retry ON pending_emails(next_retry_at);
    CREATE INDEX IF NOT EXISTS idx_token ON pending_emails(proposal_token);
    """

    def __init__(self, db_path: str | Path = "data/email_queue.db") -> None:
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.executescript(self._DDL)
            conn.commit()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def enqueue(
        self,
        *,
        recipient: str,
        subject: str,
        text_body: str,
        html_body: str,
        proposal_token: str,
        next_retry_at: datetime | None = None,
    ) -> int:
        """Add an email to the queue. Returns the queued email id."""
        if next_retry_at is None:
            next_retry_at = datetime.now(timezone.utc)
        row = await asyncio.to_thread(
            self._insert,
            recipient,
            subject,
            text_body,
            html_body,
            proposal_token,
            next_retry_at.isoformat(),
            datetime.now(timezone.utc).isoformat(),
        )
        return row[0]

    async def dequeue(self, batch_size: int = 10) -> list[QueuedEmail]:
        """Return up to *batch_size* emails whose retry time has passed."""
        rows = await asyncio.to_thread(self._select_due, batch_size)
        return [_row_to_queued(row) for row in rows]

    async def mark_sent(self, email_id: int) -> None:
        """Remove an email from the queue after successful delivery."""
        await asyncio.to_thread(self._delete, email_id)

    async def increment_retry(self, email_id: int, next_retry_at: datetime) -> None:
        """Bump retry count and set next retry time after a failed attempt."""
        await asyncio.to_thread(self._update_retry, email_id, next_retry_at.isoformat())

    async def get_pending_count(self) -> int:
        """Return the number of emails still waiting in the queue."""
        row = await asyncio.to_thread(self._count)
        return row[0] if row else 0

    # ------------------------------------------------------------------ #
    # Sync helpers (executed in asyncio.to_thread)
    # ------------------------------------------------------------------ #

    def _insert(self, *args: Any) -> sqlite3.Row:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                """
                INSERT INTO pending_emails (
                    recipient, subject, text_body, html_body,
                    proposal_token, retry_count, next_retry_at, created_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                RETURNING id
                """,
                args,
            )
            row = cursor.fetchone()
            conn.commit()
            return row
        finally:
            conn.close()

    def _select_due(self, batch_size: int) -> list[sqlite3.Row]:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT * FROM pending_emails
                WHERE next_retry_at <= ?
                ORDER BY next_retry_at ASC
                LIMIT ?
                """,
                (now, batch_size),
            )
            return cursor.fetchall()

    def _delete(self, email_id: int) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("DELETE FROM pending_emails WHERE id = ?", (email_id,))
            conn.commit()

    def _update_retry(self, email_id: int, next_retry_at: str) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE pending_emails
                SET retry_count = retry_count + 1, next_retry_at = ?
                WHERE id = ?
                """,
                (next_retry_at, email_id),
            )
            conn.commit()

    def _count(self) -> sqlite3.Row | None:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT COUNT(*) as cnt FROM pending_emails")
            return cursor.fetchone()


def _row_to_queued(row: sqlite3.Row) -> QueuedEmail:
    return QueuedEmail(
        id=row["id"],
        recipient=row["recipient"],
        subject=row["subject"],
        text_body=row["text_body"],
        html_body=row["html_body"],
        proposal_token=row["proposal_token"],
        retry_count=row["retry_count"],
        next_retry_at=datetime.fromisoformat(row["next_retry_at"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )
