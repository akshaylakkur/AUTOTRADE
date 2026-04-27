"""Strategy Journal: immutably logs every decision and outcome."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from auton.metamind.dataclasses import DecisionType, JournalEntry

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    decision_type TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    outcome TEXT NOT NULL,
    pnl REAL NOT NULL DEFAULT 0.0,
    cost REAL NOT NULL DEFAULT 0.0,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_journal_timestamp ON journal(timestamp);
CREATE INDEX IF NOT EXISTS idx_journal_decision_type ON journal(decision_type);
"""


class StrategyJournal:
    """Immutable SQLite-backed journal for decisions and outcomes."""

    def __init__(self, db_path: Path | str = Path("strategy_journal.db")) -> None:
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def log_decision(self, entry: JournalEntry) -> int:
        """Persist a :class:`JournalEntry` and return its row id."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO journal (timestamp, decision_type, reasoning, outcome, pnl, cost, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.timestamp.isoformat(),
                    entry.decision_type.value,
                    entry.reasoning,
                    entry.outcome,
                    entry.pnl,
                    entry.cost,
                    entry.metadata_json,
                ),
            )
            row_id = cursor.lastrowid
            logger.debug("Logged journal entry id=%s type=%s", row_id, entry.decision_type.value)
            return row_id

    def get_recent_entries(self, limit: int = 100) -> list[JournalEntry]:
        """Return the most recent *limit* entries, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM journal ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def analyze_win_rate(self, strategy_name: str) -> dict[str, Any]:
        """Analyze win rate for a given strategy label stored in metadata."""
        with self._connect() as conn:
            total_row = conn.execute(
                "SELECT COUNT(*) as total FROM journal WHERE metadata_json LIKE ?",
                (f'%"strategy_name":"{strategy_name}"%',),
            ).fetchone()
            wins_row = conn.execute(
                "SELECT COUNT(*) as wins FROM journal WHERE metadata_json LIKE ? AND pnl > 0",
                (f'%"strategy_name":"{strategy_name}"%',),
            ).fetchone()
            avg_pnl_row = conn.execute(
                "SELECT AVG(pnl) as avg_pnl FROM journal WHERE metadata_json LIKE ?",
                (f'%"strategy_name":"{strategy_name}"%',),
            ).fetchone()
            avg_cost_row = conn.execute(
                "SELECT AVG(cost) as avg_cost FROM journal WHERE metadata_json LIKE ?",
                (f'%"strategy_name":"{strategy_name}"%',),
            ).fetchone()

        total = total_row["total"] if total_row else 0
        wins = wins_row["wins"] if wins_row else 0
        avg_pnl = avg_pnl_row["avg_pnl"] if avg_pnl_row and avg_pnl_row["avg_pnl"] is not None else 0.0
        avg_cost = avg_cost_row["avg_cost"] if avg_cost_row and avg_cost_row["avg_cost"] is not None else 0.0

        return {
            "strategy_name": strategy_name,
            "total_trades": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": wins / total if total else 0.0,
            "avg_pnl": round(avg_pnl, 6),
            "avg_cost": round(avg_cost, 6),
        }

    def log_adaptation(
        self,
        reasoning: str,
        outcome: str,
        before_metrics: dict[str, Any],
        after_metrics: dict[str, Any],
        cost: float = 0.0,
    ) -> int:
        """Log a self-modification attempt with before/after metrics."""
        metadata = {
            "adaptation": True,
            "before": before_metrics,
            "after": after_metrics,
        }
        entry = JournalEntry(
            timestamp=datetime.now(timezone.utc),
            decision_type=DecisionType.ADAPTATION,
            reasoning=reasoning,
            outcome=outcome,
            cost=cost,
            metadata_json=json.dumps(metadata, default=str),
        )
        return self.log_decision(entry)

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> JournalEntry:
        return JournalEntry(
            id=row["id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            decision_type=DecisionType(row["decision_type"]),
            reasoning=row["reasoning"],
            outcome=row["outcome"],
            pnl=row["pnl"],
            cost=row["cost"],
            metadata_json=row["metadata_json"],
        )
