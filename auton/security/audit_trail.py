"""Immutable audit trail with hash chaining for Project ÆON."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .exceptions import AuditError

# Default PII redaction patterns
_PII_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),  # credit cards
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),  # emails
    re.compile(r"\b(?:\+\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),  # phones
    re.compile(r"(?i)(password|passwd|pwd)\s*=\s*['\"][^'\"]+['\"]"),
    re.compile(r"(?i)(api[_-]?key|apikey|secret[_-]?key)\s*=\s*['\"][^'\"]+['\"]"),
    re.compile(r"(?i)(token|bearer)\s+['\"]?[a-zA-Z0-9_\-]{20,}['\"]?"),
    re.compile(r"(?i)(sk-[a-zA-Z0-9]{20,})"),
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),  # UUIDs (often API keys)
)


class AuditLog:
    """Append-only JSONL log of every action ÆON takes. Immutable.

    SQLite entries use SHA-256 hash chaining; JSONL files in
    ``cold_storage/audit/`` serve as offline tamper-evident archive.
    Also logs every financial transaction, credential access, and major decision.
    """

    _TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        action_type TEXT NOT NULL,
        parameters_json TEXT NOT NULL,
        result_json TEXT,
        prev_hash TEXT NOT NULL,
        entry_hash TEXT UNIQUE NOT NULL,
        severity TEXT NOT NULL DEFAULT 'info'
    );
    CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action_type);
    CREATE INDEX IF NOT EXISTS idx_audit_severity ON audit_log(severity);
    CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
    """

    def __init__(
        self,
        db_path: str = "data/aeon_audit.db",
        jsonl_dir: str = "cold_storage/audit/",
    ) -> None:
        self._db_path = db_path
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._jsonl_dir = Path(jsonl_dir)
        self._jsonl_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @staticmethod
    def _redact_value(value: Any) -> Any:
        """Recursively redact PII from strings and nested structures."""
        if isinstance(value, str):
            redacted = value
            for pattern in _PII_PATTERNS:
                redacted = pattern.sub("[REDACTED]", redacted)
            return redacted
        if isinstance(value, dict):
            return {k: AuditLog._redact_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [AuditLog._redact_value(item) for item in value]
        return value

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            # Step 1: create base table without indexes (indexes reference severity)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    result_json TEXT,
                    prev_hash TEXT NOT NULL,
                    entry_hash TEXT UNIQUE NOT NULL
                );
                """
            )
            # Step 2: migrate — add severity column if missing
            try:
                conn.execute("SELECT severity FROM audit_log LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE audit_log ADD COLUMN severity TEXT NOT NULL DEFAULT 'info';")

            # Step 3: create indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action_type);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_severity ON audit_log(severity);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);")
            conn.commit()

    @staticmethod
    def _compute_hash(
        timestamp: str,
        action_type: str,
        parameters_json: str,
        result_json: str | None,
        prev_hash: str,
    ) -> str:
        data = f"{timestamp}|{action_type}|{parameters_json}|{result_json}|{prev_hash}"
        return hashlib.sha256(data.encode()).hexdigest()

    def _last_hash(self, conn: sqlite3.Connection) -> str:
        row = conn.execute(
            "SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1;"
        ).fetchone()
        return row[0] if row else "0" * 64

    def _append_jsonl(self, entry: dict[str, Any]) -> None:
        filename = f"aeon_audit_{datetime.now(timezone.utc).date().isoformat()}.jsonl"
        filepath = self._jsonl_dir / filename
        line = json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"
        with open(filepath, "a", encoding="utf-8") as f:
            os.write(f.fileno(), line.encode())

    def pre_log(
        self,
        action_type: str,
        parameters: dict[str, Any],
        severity: str = "info",
    ) -> int:
        """Record an action *before* it executes, returning a log ID."""
        timestamp = datetime.now(timezone.utc).isoformat()
        parameters = self._redact_value(parameters)
        parameters_json = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(self._db_path) as conn:
            prev_hash = self._last_hash(conn)
            entry_hash = self._compute_hash(timestamp, action_type, parameters_json, None, prev_hash)
            cur = conn.execute(
                """
                INSERT INTO audit_log (timestamp, action_type, parameters_json, result_json, prev_hash, entry_hash, severity)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (timestamp, action_type, parameters_json, None, prev_hash, entry_hash, severity),
            )
            log_id = cur.lastrowid
        self._append_jsonl({
            "id": log_id,
            "timestamp": timestamp,
            "action_type": action_type,
            "parameters": parameters,
            "result": None,
            "prev_hash": prev_hash,
            "entry_hash": entry_hash,
            "severity": severity,
        })
        return log_id

    def post_log(self, log_id: int, result: dict[str, Any]) -> None:
        """Update a pre-logged entry with its result."""
        result = self._redact_value(result)
        result_json = json.dumps(result, sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT timestamp, action_type, parameters_json, prev_hash FROM audit_log WHERE id = ?;",
                (log_id,),
            ).fetchone()
            if row is None:
                raise AuditError(f"No pre_log entry found for id {log_id}")
            timestamp, action_type, parameters_json, prev_hash = row
            entry_hash = self._compute_hash(timestamp, action_type, parameters_json, result_json, prev_hash)
            conn.execute(
                """
                UPDATE audit_log
                SET result_json = ?, entry_hash = ?
                WHERE id = ?;
                """,
                (result_json, entry_hash, log_id),
            )
        self._append_jsonl({
            "id": log_id,
            "timestamp": timestamp,
            "action_type": action_type,
            "parameters": json.loads(parameters_json),
            "result": result,
            "prev_hash": prev_hash,
            "entry_hash": entry_hash,
        })

    def log(
        self,
        action_type: str,
        parameters: dict[str, Any],
        result: dict[str, Any] | None = None,
        severity: str = "info",
    ) -> str:
        """Append a complete entry to the audit trail.

        Returns the entry hash.
        """
        log_id = self.pre_log(action_type, parameters, severity=severity)
        if result is not None:
            self.post_log(log_id, result)
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT entry_hash FROM audit_log WHERE id = ?;", (log_id,)
            ).fetchone()
        return row[0] if row else ""

    def verify_chain(self) -> bool:
        """Walk the chain and verify all hashes.

        Returns ``True`` if the chain is intact, ``False`` otherwise.
        """
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT timestamp, action_type, parameters_json, result_json, prev_hash, entry_hash
                FROM audit_log ORDER BY id ASC;
                """
            ).fetchall()

        if not rows:
            return True

        expected_prev = "0" * 64
        for timestamp, action_type, parameters_json, result_json, prev_hash, entry_hash in rows:
            if prev_hash != expected_prev:
                return False
            computed = self._compute_hash(timestamp, action_type, parameters_json, result_json, prev_hash)
            if computed != entry_hash:
                return False
            expected_prev = entry_hash

        return True

    def export_jsonl(self, dt: date | None = None) -> Path:
        """Return the path to the JSONL file for *dt* (defaults to today)."""
        dt = dt or datetime.now(timezone.utc).date()
        filename = f"aeon_audit_{dt.isoformat()}.jsonl"
        return self._jsonl_dir / filename

    def export_range(self, start_dt: date, end_dt: date) -> list[Path]:
        """Return paths to JSONL files in the given date range inclusive."""
        paths = []
        current = start_dt
        while current <= end_dt:
            p = self.export_jsonl(current)
            if p.exists():
                paths.append(p)
            current += timedelta(days=1)
        return paths

    def query_by_action(
        self,
        action_type: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query audit log entries by action type."""
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, timestamp, action_type, parameters_json, result_json, entry_hash, severity
                FROM audit_log WHERE action_type = ? ORDER BY id DESC LIMIT ?;
                """,
                (action_type, limit),
            ).fetchall()
        return [
            {
                "id": r[0],
                "timestamp": r[1],
                "action_type": r[2],
                "parameters": json.loads(r[3]),
                "result": json.loads(r[4]) if r[4] else None,
                "entry_hash": r[5],
                "severity": r[6],
            }
            for r in rows
        ]

    def query_by_severity(
        self,
        severity: str = "warning",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query audit log entries at or above a severity level."""
        severities = {"debug": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}
        threshold = severities.get(severity, 1)
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, timestamp, action_type, parameters_json, result_json, entry_hash, severity
                FROM audit_log ORDER BY id DESC;
                """
            ).fetchall()
        result = []
        for r in rows:
            if severities.get(r[6], 1) >= threshold:
                result.append({
                    "id": r[0],
                    "timestamp": r[1],
                    "action_type": r[2],
                    "parameters": json.loads(r[3]),
                    "result": json.loads(r[4]) if r[4] else None,
                    "entry_hash": r[5],
                    "severity": r[6],
                })
            if len(result) >= limit:
                break
        return result


# Backward-compatible alias
AuditTrail = AuditLog
