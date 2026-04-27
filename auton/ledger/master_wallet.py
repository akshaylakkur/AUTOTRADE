"""The Master Wallet — single source of truth for all balances."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from auton.ledger.exceptions import InsufficientFundsError, LedgerError


@dataclass(frozen=True)
class CostReceipt:
    """Immutable receipt returned by every debit operation."""

    id: int
    timestamp: datetime
    amount: float
    reason: str
    running_balance: float


class MasterWallet:
    """SQLite-backed wallet that tracks every credit and debit atomically."""

    _DDL = """
    CREATE TABLE IF NOT EXISTS transactions (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp     TEXT    NOT NULL,
        type          TEXT    NOT NULL CHECK(type IN ('CREDIT', 'DEBIT')),
        amount        REAL    NOT NULL CHECK(amount > 0),
        reason        TEXT    NOT NULL,
        running_balance REAL  NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_tx_time ON transactions(timestamp);
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._local = threading.local()
        self._ensure_schema()

    # ------------------------------------------------------------------ #
    # Connection management (one connection per thread)
    # ------------------------------------------------------------------ #
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def _ensure_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(self._DDL)
            conn.commit()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def get_balance(self) -> float:
        """Return the current running balance (0.0 if no transactions)."""
        row = (
            self._conn()
            .execute(
                "SELECT running_balance FROM transactions ORDER BY id DESC LIMIT 1"
            )
            .fetchone()
        )
        return row[0] if row else 0.0

    def credit(self, amount: float, reason: str) -> CostReceipt:
        """Add funds to the wallet and return a receipt."""
        if amount <= 0:
            raise LedgerError("Credit amount must be positive")

        conn = self._conn()
        ts = datetime.now(timezone.utc).isoformat()
        with conn:
            cursor = conn.execute(
                "SELECT running_balance FROM transactions ORDER BY id DESC LIMIT 1"
            )
            row = cursor.fetchone()
            new_balance = (row[0] if row else 0.0) + amount
            cur = conn.execute(
                """
                INSERT INTO transactions (timestamp, type, amount, reason, running_balance)
                VALUES (?, 'CREDIT', ?, ?, ?)
                """,
                (ts, amount, reason, new_balance),
            )
            return CostReceipt(
                id=cur.lastrowid,
                timestamp=datetime.fromisoformat(ts),
                amount=amount,
                reason=reason,
                running_balance=new_balance,
            )

    def debit(self, amount: float, reason: str) -> CostReceipt:
        """Deduct funds from the wallet and return a receipt.

        Raises:
            InsufficientFundsError: If the debit would drop the balance below zero.
        """
        if amount <= 0:
            raise LedgerError("Debit amount must be positive")

        conn = self._conn()
        ts = datetime.now(timezone.utc).isoformat()
        with conn:
            cursor = conn.execute(
                "SELECT running_balance FROM transactions ORDER BY id DESC LIMIT 1"
            )
            row = cursor.fetchone()
            current = row[0] if row else 0.0
            if current < amount:
                raise InsufficientFundsError(
                    f"Balance {current:.4f} insufficient for debit {amount:.4f}"
                )
            new_balance = current - amount
            cur = conn.execute(
                """
                INSERT INTO transactions (timestamp, type, amount, reason, running_balance)
                VALUES (?, 'DEBIT', ?, ?, ?)
                """,
                (ts, amount, reason, new_balance),
            )
            return CostReceipt(
                id=cur.lastrowid,
                timestamp=datetime.fromisoformat(ts),
                amount=amount,
                reason=reason,
                running_balance=new_balance,
            )

    def get_transaction_history(self, limit: int = 100) -> Iterable[CostReceipt]:
        """Yield the most recent transactions, newest first."""
        rows = (
            self._conn()
            .execute(
                """
                SELECT id, timestamp, amount, reason, running_balance
                FROM transactions
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            .fetchall()
        )
        for row in rows:
            yield CostReceipt(
                id=row[0],
                timestamp=datetime.fromisoformat(row[1]),
                amount=row[2],
                reason=row[3],
                running_balance=row[4],
            )
