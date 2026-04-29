"""BankReconciler — matches external bank transactions to internal CostReceipts.

The reconciler maintains a SQLite-backed registry of unmatched bank
transactions, internal ledger receipts, and the matches between them.
This allows ÆON to detect discrepancies, missing internal records, or
unauthorized external debits.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from auton.ledger.master_wallet import CostReceipt, MasterWallet
from auton.limbs.banking.plaid_client import BankTransaction
from auton.security.audit_trail import AuditLog


@dataclass(frozen=True, slots=True)
class ReconciliationMatch:
    """A confirmed match between a bank tx and an internal receipt."""

    match_id: int
    bank_transaction_id: str
    receipt_id: int
    matched_at: datetime
    match_type: str  # "auto", "manual", "fuzzy"
    discrepancy: float | None = None  # bank_amount - internal_amount


@dataclass(frozen=True, slots=True)
class UnmatchedBankTx:
    """A bank transaction with no corresponding internal record."""

    transaction_id: str
    account_id: str
    amount: float
    date: str
    name: str
    days_unmatched: int


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """Summary of a reconciliation run."""

    period_start: str
    period_end: str
    total_bank_transactions: int
    total_internal_receipts: int
    auto_matched: int
    manual_matched: int
    unmatched_bank: list[UnmatchedBankTx] = field(default_factory=list)
    unmatched_internal: list[dict[str, Any]] = field(default_factory=list)
    discrepancies: list[dict[str, Any]] = field(default_factory=list)


class BankReconciler:
    """Reconciles external bank transactions against internal ledger receipts.

    Uses exact matching by default (transaction_id == receipt.reason field
    after normalization). Fuzzy matching can be enabled for amount+date
    proximity.

    Stores match state in SQLite so reconciliation survives restarts.
    """

    _TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS bank_transactions (
        transaction_id   TEXT PRIMARY KEY,
        account_id       TEXT NOT NULL,
        amount           REAL NOT NULL,
        currency         TEXT NOT NULL DEFAULT 'USD',
        tx_date          TEXT NOT NULL,
        name             TEXT NOT NULL,
        pending          INTEGER NOT NULL DEFAULT 0,
        category         TEXT,
        merchant_name    TEXT,
        imported_at      TEXT NOT NULL,
        matched          INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS reconciliations (
        match_id         INTEGER PRIMARY KEY AUTOINCREMENT,
        bank_transaction_id TEXT NOT NULL UNIQUE,
        receipt_id       INTEGER NOT NULL,
        matched_at       TEXT NOT NULL,
        match_type       TEXT NOT NULL DEFAULT 'auto',
        discrepancy      REAL
    );

    CREATE TABLE IF NOT EXISTS reconciliation_runs (
        run_id           INTEGER PRIMARY KEY AUTOINCREMENT,
        period_start     TEXT NOT NULL,
        period_end       TEXT NOT NULL,
        ran_at           TEXT NOT NULL,
        auto_matched     INTEGER NOT NULL DEFAULT 0,
        manual_matched   INTEGER NOT NULL DEFAULT 0,
        unmatched_bank   INTEGER NOT NULL DEFAULT 0,
        unmatched_internal INTEGER NOT NULL DEFAULT 0,
        discrepancy_count INTEGER NOT NULL DEFAULT 0
    );

    CREATE INDEX IF NOT EXISTS idx_bank_tx_date ON bank_transactions(tx_date);
    CREATE INDEX IF NOT EXISTS idx_bank_tx_matched ON bank_transactions(matched);
    CREATE INDEX IF NOT EXISTS idx_reconciliations_receipt ON reconciliations(receipt_id);
    """

    def __init__(
        self,
        wallet: MasterWallet,
        db_path: str | Path = "data/aeon_reconciler.db",
        audit_log: AuditLog | None = None,
        fuzzy_tolerance: float = 0.01,
    ) -> None:
        self._wallet = wallet
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._audit_log = audit_log or AuditLog()
        self._fuzzy_tolerance = fuzzy_tolerance
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
            conn.executescript(self._TABLE_SQL)
            conn.commit()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------ #
    # Import bank transactions
    # ------------------------------------------------------------------ #

    def import_bank_transactions(self, transactions: list[BankTransaction]) -> int:
        """Store a batch of bank transactions for reconciliation.

        Returns the number of newly imported transactions.
        """
        imported = 0
        now = self._now()
        with self._conn() as conn:
            for tx in transactions:
                # Skip duplicates
                existing = conn.execute(
                    "SELECT 1 FROM bank_transactions WHERE transaction_id = ?",
                    (tx.transaction_id,),
                ).fetchone()
                if existing:
                    continue

                conn.execute(
                    """
                    INSERT INTO bank_transactions
                    (transaction_id, account_id, amount, currency, tx_date, name,
                     pending, category, merchant_name, imported_at, matched)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tx.transaction_id,
                        tx.account_id,
                        tx.amount,
                        tx.iso_currency_code,
                        tx.date,
                        tx.name,
                        1 if tx.pending else 0,
                        ",".join(tx.category) if tx.category else None,
                        tx.merchant_name,
                        now,
                        0,
                    ),
                )
                imported += 1
            conn.commit()

        self._audit_log.log("reconciler.import", {"count": imported})
        return imported

    # ------------------------------------------------------------------ #
    # Reconciliation core
    # ------------------------------------------------------------------ #

    def reconcile(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        allow_fuzzy: bool = True,
    ) -> ReconciliationReport:
        """Run a reconciliation pass over the given date range.

        Steps:
          1. Load unmatched bank transactions in the range.
          2. Load internal receipts in the range.
          3. Attempt exact matches (amount + normalized reason).
          4. Optionally attempt fuzzy matches (amount proximity + date).
          5. Record matches, unmatched items, and discrepancies.

        Returns a :class:`ReconciliationReport`.
        """
        period_start = start_date or (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
        period_end = end_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

        self._audit_log.pre_log("reconciler.run", {
            "period_start": period_start,
            "period_end": period_end,
        })

        bank_txs = self._get_unmatched_bank_transactions(period_start, period_end)
        receipts = self._get_internal_receipts(period_start, period_end)

        auto_matched = 0
        manual_matched = 0
        unmatched_bank: list[UnmatchedBankTx] = []
        unmatched_internal: list[dict[str, Any]] = []
        discrepancies: list[dict[str, Any]] = []

        receipt_ids_used: set[int] = set()

        with self._conn() as conn:
            for tx in bank_txs:
                # Exact match: bank tx amount == receipt amount and reason contains tx name
                match = self._try_exact_match(tx, receipts, receipt_ids_used)
                if match:
                    discrepancy = round(tx["amount"] - match["amount"], 4)
                    conn.execute(
                        """
                        INSERT INTO reconciliations
                        (bank_transaction_id, receipt_id, matched_at, match_type, discrepancy)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (tx["transaction_id"], match["id"], self._now(), "auto", discrepancy),
                    )
                    conn.execute(
                        "UPDATE bank_transactions SET matched = 1 WHERE transaction_id = ?",
                        (tx["transaction_id"],),
                    )
                    receipt_ids_used.add(match["id"])
                    auto_matched += 1
                    if abs(discrepancy) > 0:
                        discrepancies.append({
                            "transaction_id": tx["transaction_id"],
                            "receipt_id": match["id"],
                            "discrepancy": discrepancy,
                        })
                    continue

                if allow_fuzzy:
                    fuzzy = self._try_fuzzy_match(tx, receipts, receipt_ids_used)
                    if fuzzy:
                        discrepancy = round(tx["amount"] - fuzzy["amount"], 4)
                        conn.execute(
                            """
                            INSERT INTO reconciliations
                            (bank_transaction_id, receipt_id, matched_at, match_type, discrepancy)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (tx["transaction_id"], fuzzy["id"], self._now(), "fuzzy", discrepancy),
                        )
                        conn.execute(
                            "UPDATE bank_transactions SET matched = 1 WHERE transaction_id = ?",
                            (tx["transaction_id"],),
                        )
                        receipt_ids_used.add(fuzzy["id"])
                        auto_matched += 1
                        if abs(discrepancy) > 0:
                            discrepancies.append({
                                "transaction_id": tx["transaction_id"],
                                "receipt_id": fuzzy["id"],
                                "discrepancy": discrepancy,
                            })
                        continue

                # Still unmatched
                tx_date = datetime.strptime(tx["tx_date"], "%Y-%m-%d").date()
                days_unmatched = (datetime.now(timezone.utc).date() - tx_date).days
                unmatched_bank.append(UnmatchedBankTx(
                    transaction_id=tx["transaction_id"],
                    account_id=tx["account_id"],
                    amount=tx["amount"],
                    date=tx["tx_date"],
                    name=tx["name"],
                    days_unmatched=days_unmatched,
                ))

            # Any receipts not matched at all
            for receipt in receipts:
                if receipt["id"] not in receipt_ids_used:
                    unmatched_internal.append({
                        "receipt_id": receipt["id"],
                        "amount": receipt["amount"],
                        "reason": receipt["reason"],
                        "timestamp": receipt["timestamp"],
                    })

            # Log run summary
            conn.execute(
                """
                INSERT INTO reconciliation_runs
                (period_start, period_end, ran_at, auto_matched, manual_matched,
                 unmatched_bank, unmatched_internal, discrepancy_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    period_start,
                    period_end,
                    self._now(),
                    auto_matched,
                    manual_matched,
                    len(unmatched_bank),
                    len(unmatched_internal),
                    len(discrepancies),
                ),
            )
            conn.commit()

        report = ReconciliationReport(
            period_start=period_start,
            period_end=period_end,
            total_bank_transactions=len(bank_txs) + auto_matched,
            total_internal_receipts=len(receipts),
            auto_matched=auto_matched,
            manual_matched=manual_matched,
            unmatched_bank=unmatched_bank,
            unmatched_internal=unmatched_internal,
            discrepancies=discrepancies,
        )

        self._audit_log.log("reconciler.report", {
            "period_start": period_start,
            "period_end": period_end,
            "auto_matched": auto_matched,
            "unmatched_bank": len(unmatched_bank),
            "unmatched_internal": len(unmatched_internal),
            "discrepancies": len(discrepancies),
        })
        return report

    def manual_match(
        self,
        bank_transaction_id: str,
        receipt_id: int,
    ) -> ReconciliationMatch:
        """Manually match a bank transaction to an internal receipt."""
        with self._conn() as conn:
            tx = conn.execute(
                "SELECT amount FROM bank_transactions WHERE transaction_id = ?",
                (bank_transaction_id,),
            ).fetchone()
        # Query the wallet database for the receipt
        with sqlite3.connect(self._wallet._db_path) as wconn:
            receipt = wconn.execute(
                "SELECT amount FROM transactions WHERE id = ?",
                (receipt_id,),
            ).fetchone()
        if not tx or not receipt:
            raise ReconciliationError("Transaction or receipt not found")

        discrepancy = round(tx[0] - receipt[0], 4)

        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO reconciliations
                (bank_transaction_id, receipt_id, matched_at, match_type, discrepancy)
                VALUES (?, ?, ?, ?, ?)
                """,
                (bank_transaction_id, receipt_id, self._now(), "manual", discrepancy),
            )
            conn.execute(
                "UPDATE bank_transactions SET matched = 1 WHERE transaction_id = ?",
                (bank_transaction_id,),
            )
            conn.commit()
            match_id = cur.lastrowid

        self._audit_log.log("reconciler.manual_match", {
            "bank_transaction_id": bank_transaction_id,
            "receipt_id": receipt_id,
            "discrepancy": discrepancy,
        })
        return ReconciliationMatch(
            match_id=match_id,
            bank_transaction_id=bank_transaction_id,
            receipt_id=receipt_id,
            matched_at=datetime.now(timezone.utc),
            match_type="manual",
            discrepancy=discrepancy,
        )

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #

    def get_unmatched_bank_transactions(
        self,
        days: int = 30,
    ) -> list[UnmatchedBankTx]:
        """Return bank transactions that remain unmatched."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = self._conn().execute(
            """
            SELECT transaction_id, account_id, amount, tx_date, name
            FROM bank_transactions
            WHERE matched = 0 AND tx_date >= ?
            ORDER BY tx_date DESC
            """,
            (cutoff,),
        ).fetchall()
        today = datetime.now(timezone.utc).date()
        return [
            UnmatchedBankTx(
                transaction_id=r[0],
                account_id=r[1],
                amount=r[2],
                date=r[3],
                name=r[4],
                days_unmatched=(today - datetime.strptime(r[3], "%Y-%m-%d").date()).days,
            )
            for r in rows
        ]

    def get_reconciliation_history(self, limit: int = 30) -> list[dict[str, Any]]:
        """Return recent reconciliation run summaries."""
        rows = self._conn().execute(
            """
            SELECT period_start, period_end, ran_at, auto_matched, manual_matched,
                   unmatched_bank, unmatched_internal, discrepancy_count
            FROM reconciliation_runs
            ORDER BY run_id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            {
                "period_start": r[0],
                "period_end": r[1],
                "ran_at": r[2],
                "auto_matched": r[3],
                "manual_matched": r[4],
                "unmatched_bank": r[5],
                "unmatched_internal": r[6],
                "discrepancies": r[7],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _get_unmatched_bank_transactions(
        self,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        rows = self._conn().execute(
            """
            SELECT transaction_id, account_id, amount, tx_date, name
            FROM bank_transactions
            WHERE matched = 0 AND tx_date >= ? AND tx_date <= ?
            ORDER BY tx_date DESC
            """,
            (start_date, end_date),
        ).fetchall()
        return [
            {
                "transaction_id": r[0],
                "account_id": r[1],
                "amount": r[2],
                "tx_date": r[3],
                "name": r[4],
            }
            for r in rows
        ]

    def _get_internal_receipts(
        self,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """Query the master wallet transaction table for receipts in range."""
        # Note: master_wallet transactions table is in the wallet's DB,
        # but we query via the wallet's connection here by opening a
        # temporary connection to the wallet DB.
        wallet_path = self._wallet._db_path
        with sqlite3.connect(wallet_path) as conn:
            rows = conn.execute(
                """
                SELECT id, timestamp, amount, reason
                FROM transactions
                WHERE timestamp >= ? AND timestamp <= ?
                ORDER BY id DESC
                """,
                (
                    datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).isoformat(),
                    (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).replace(tzinfo=timezone.utc).isoformat(),
                ),
            ).fetchall()
        return [
            {"id": r[0], "timestamp": r[1], "amount": r[2], "reason": r[3]}
            for r in rows
        ]

    def _try_exact_match(
        self,
        tx: dict[str, Any],
        receipts: list[dict[str, Any]],
        used: set[int],
    ) -> dict[str, Any] | None:
        """Exact match: bank amount equals receipt amount (negated for debits)
        and the receipt reason contains the transaction name."""
        tx_amount = tx["amount"]
        tx_name = tx["name"].lower()
        for receipt in receipts:
            if receipt["id"] in used:
                continue
            receipt_amount = receipt["amount"]
            # Bank tx amounts are positive for debits and negative for credits in Plaid.
            # Internal debits are positive in our ledger. Normalize.
            if abs(abs(tx_amount) - receipt_amount) <= self._fuzzy_tolerance:
                if tx_name in receipt["reason"].lower() or receipt["reason"].lower() in tx_name:
                    return receipt
        return None

    def _try_fuzzy_match(
        self,
        tx: dict[str, Any],
        receipts: list[dict[str, Any]],
        used: set[int],
    ) -> dict[str, Any] | None:
        """Fuzzy match: amount within tolerance and date within 3 days."""
        tx_amount = abs(tx["amount"])
        tx_date = datetime.strptime(tx["tx_date"], "%Y-%m-%d")
        for receipt in receipts:
            if receipt["id"] in used:
                continue
            receipt_amount = receipt["amount"]
            if abs(tx_amount - receipt_amount) <= self._fuzzy_tolerance:
                try:
                    r_date = datetime.fromisoformat(receipt["timestamp"]).replace(tzinfo=None)
                except ValueError:
                    continue
                if abs((tx_date - r_date).days) <= 3:
                    return receipt
        return None


class ReconciliationError(Exception):
    """Raised when a reconciliation operation fails."""
