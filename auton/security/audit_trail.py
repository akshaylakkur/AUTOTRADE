"""Immutable audit trail with hash chaining for Project ÆON."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .exceptions import AuditError


class AuditTrail:
    """Tamper-evident audit log backed by SQLite.

    Each entry contains the SHA-256 hash of the previous entry, forming a
    chain that can be verified with :meth:`verify_chain`.
    """

    _TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        action_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        prev_hash TEXT NOT NULL,
        entry_hash TEXT UNIQUE NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action_type);
    """

    def __init__(self, db_path: str = "aeon_audit.db") -> None:
        self._db_path = db_path
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.executescript(self._TABLE_SQL)

    @staticmethod
    def _compute_hash(
        timestamp: str,
        action_type: str,
        payload_json: str,
        prev_hash: str,
    ) -> str:
        data = f"{timestamp}|{action_type}|{payload_json}|{prev_hash}"
        return hashlib.sha256(data.encode()).hexdigest()

    def _last_hash(self, conn: sqlite3.Connection) -> str:
        row = conn.execute(
            "SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1;"
        ).fetchone()
        return row[0] if row else "0" * 64

    def log(self, action_type: str, payload: dict[str, Any]) -> str:
        """Append a new entry to the audit trail.

        Returns the entry hash.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(self._db_path) as conn:
            prev_hash = self._last_hash(conn)
            entry_hash = self._compute_hash(timestamp, action_type, payload_json, prev_hash)
            conn.execute(
                """
                INSERT INTO audit_log (timestamp, action_type, payload_json, prev_hash, entry_hash)
                VALUES (?, ?, ?, ?, ?);
                """,
                (timestamp, action_type, payload_json, prev_hash, entry_hash),
            )
        return entry_hash

    def verify_chain(self) -> bool:
        """Walk the chain and verify all hashes.

        Returns ``True`` if the chain is intact, ``False`` otherwise.
        """
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT timestamp, action_type, payload_json, prev_hash, entry_hash
                FROM audit_log ORDER BY id ASC;
                """
            ).fetchall()

        if not rows:
            return True

        expected_prev = "0" * 64
        for timestamp, action_type, payload_json, prev_hash, entry_hash in rows:
            if prev_hash != expected_prev:
                return False
            computed = self._compute_hash(timestamp, action_type, payload_json, prev_hash)
            if computed != entry_hash:
                return False
            expected_prev = entry_hash

        return True
