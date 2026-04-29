"""SQLite-backed rollback journal for self-modification."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class PatchRecord:
    """Immutable record of a single patch."""

    patch_id: str
    timestamp: datetime
    file_path: str
    author: str
    reason: str
    pre_snapshot: str
    diff_text: str
    test_result_json: str
    cost: float


class RollbackJournal:
    """SQLite-backed journal of every file mutation for atomic rollback."""

    _DDL = """
    CREATE TABLE IF NOT EXISTS patches (
        patch_id      TEXT    PRIMARY KEY,
        timestamp     TEXT    NOT NULL,
        file_path     TEXT    NOT NULL,
        author        TEXT    NOT NULL,
        reason        TEXT    NOT NULL,
        pre_snapshot  TEXT    NOT NULL,
        diff_text     TEXT    NOT NULL,
        test_result_json TEXT NOT NULL DEFAULT '{}',
        cost          REAL    NOT NULL DEFAULT 0.0
    );
    CREATE INDEX IF NOT EXISTS idx_patches_file ON patches(file_path);
    CREATE INDEX IF NOT EXISTS idx_patches_time ON patches(timestamp);
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._local = threading.local()
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def _ensure_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(self._DDL)
            conn.commit()

    @contextmanager
    def _connect(self):
        conn = self._conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def record_snapshot(
        self,
        patch_id: str,
        file_path: Path,
        content: str,
        author: str = "",
        reason: str = "",
        diff_text: str = "",
        cost: float = 0.0,
    ) -> None:
        """Persist a pre-patch snapshot."""
        ts = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO patches
                (patch_id, timestamp, file_path, author, reason, pre_snapshot, diff_text, cost)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (patch_id, ts, str(file_path), author, reason, content, diff_text, cost),
            )

    def get_snapshot(self, patch_id: str, file_path: Path) -> str | None:
        """Return the pre-patch snapshot for *patch_id* and *file_path*."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT pre_snapshot FROM patches WHERE patch_id = ? AND file_path = ?",
                (patch_id, str(file_path)),
            ).fetchone()
        return row[0] if row else None

    def update_test_result(self, patch_id: str, test_result: dict[str, Any]) -> None:
        """Attach test results to a patch record."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE patches SET test_result_json = ? WHERE patch_id = ?",
                (json.dumps(test_result, default=str), patch_id),
            )

    def list_patches(self, file_path: Path | None = None) -> list[PatchRecord]:
        """Return patches, newest first."""
        with self._connect() as conn:
            if file_path is not None:
                rows = conn.execute(
                    "SELECT * FROM patches WHERE file_path = ? ORDER BY timestamp DESC",
                    (str(file_path),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM patches ORDER BY timestamp DESC"
                ).fetchall()
        return [
            PatchRecord(
                patch_id=row[0],
                timestamp=datetime.fromisoformat(row[1]),
                file_path=row[2],
                author=row[3],
                reason=row[4],
                pre_snapshot=row[5],
                diff_text=row[6],
                test_result_json=row[7],
                cost=row[8],
            )
            for row in rows
        ]

    def get_last_patch(self, file_path: Path) -> PatchRecord | None:
        """Return the most recent patch for *file_path*, if any."""
        patches = self.list_patches(file_path)
        return patches[0] if patches else None
