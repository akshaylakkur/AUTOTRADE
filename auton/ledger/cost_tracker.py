"""Compute Cost Tracker — categorizes and records operational costs."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Iterable

from auton.ledger.exceptions import LedgerError
from auton.ledger.master_wallet import CostReceipt, MasterWallet


class CostCategory(Enum):
    INFERENCE = "INFERENCE"
    DATA_SUBSCRIPTION = "DATA_SUBSCRIPTION"
    TRADING_FEE = "TRADING_FEE"
    COMPUTE = "COMPUTE"
    EGRESS = "EGRESS"
    PLATFORM_FEE = "PLATFORM_FEE"
    LABOR = "LABOR"


@dataclass(frozen=True)
class CostRecord:
    """Immutable record of a single categorized cost."""

    id: int
    timestamp: datetime
    category: CostCategory
    amount: float
    details: str


@dataclass
class DailyCost:
    """Aggregation of costs for a single calendar day."""

    day: date
    total: float = 0.0
    by_category: dict[CostCategory, float] = field(default_factory=dict)


class CostTracker:
    """Tracks categorized operational costs backed by SQLite.

    Integrates with :class:`MasterWallet` so every recorded cost is
    immediately deducted from the on-chain balance.
    """

    _DDL = """
    CREATE TABLE IF NOT EXISTS costs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp   TEXT    NOT NULL,
        category    TEXT    NOT NULL,
        amount      REAL    NOT NULL CHECK(amount > 0),
        details     TEXT    NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_cost_time ON costs(timestamp);
    CREATE INDEX IF NOT EXISTS idx_cost_cat  ON costs(category);
    """

    def __init__(
        self,
        wallet: MasterWallet,
        db_path: str | Path = ":memory:",
    ) -> None:
        self._wallet = wallet
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

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def record_cost(
        self,
        category: CostCategory,
        amount: float,
        details: str = "",
    ) -> CostReceipt:
        """Record a categorized cost and atomically debit the wallet.

        Returns the :class:`CostReceipt` from the wallet debit.
        """
        if amount <= 0:
            raise LedgerError("Cost amount must be positive")

        reason = f"{category.value}: {details}" if details else category.value
        receipt = self._wallet.debit(amount, reason)

        ts = receipt.timestamp.isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO costs (timestamp, category, amount, details)
                VALUES (?, ?, ?, ?)
                """,
                (ts, category.value, amount, details),
            )
            conn.commit()

        return receipt

    def get_daily_costs(self, days: int = 30) -> Iterable[DailyCost]:
        """Return cost aggregations for the last *days* calendar days."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = (
            self._conn()
            .execute(
                """
                SELECT timestamp, category, amount
                FROM costs
                WHERE timestamp >= ?
                ORDER BY timestamp DESC
                """,
                (cutoff,),
            )
            .fetchall()
        )

        buckets: dict[date, DailyCost] = {}
        for ts_str, cat_str, amt in rows:
            day = datetime.fromisoformat(ts_str).date()
            bucket = buckets.setdefault(day, DailyCost(day=day))
            bucket.total += amt
            cat = CostCategory(cat_str)
            bucket.by_category[cat] = bucket.by_category.get(cat, 0.0) + amt

        return (buckets[d] for d in sorted(buckets, reverse=True))

    def get_cost_breakdown(self, days: int = 30) -> dict[CostCategory, float]:
        """Return a flat map of category -> total spend for the last *days* days."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = (
            self._conn()
            .execute(
                """
                SELECT category, SUM(amount)
                FROM costs
                WHERE timestamp >= ?
                GROUP BY category
                """,
                (cutoff,),
            )
            .fetchall()
        )
        return {CostCategory(cat): float(total) for cat, total in rows}

    def get_cost_history(self, limit: int = 100) -> Iterable[CostRecord]:
        """Yield the most recent cost records, newest first."""
        rows = (
            self._conn()
            .execute(
                """
                SELECT id, timestamp, category, amount, details
                FROM costs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            .fetchall()
        )
        for row in rows:
            yield CostRecord(
                id=row[0],
                timestamp=datetime.fromisoformat(row[1]),
                category=CostCategory(row[2]),
                amount=row[3],
                details=row[4],
            )
