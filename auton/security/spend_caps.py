"""Spend caps enforcement for Project ÆON."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from .exceptions import (
    ApprovalRequired,
    BudgetExceeded,
    EmergencyPauseActive,
    SpendCapExceeded,
)
from .config import SpendGuardConfig


@dataclass(frozen=True)
class CapConfig:
    """Configuration for spend caps in a single category."""

    hourly: float = 0.0
    daily: float = 0.0
    weekly: float = 0.0
    monthly: float = 0.0


@dataclass
class Window:
    """A sliding time window and the amount spent within it."""

    start: datetime
    amount: float = 0.0


class SpendGuard:
    """Enforce spend caps per action type and total. Integrates with the ledger.

    Every debit is checked against the guard before execution.
    Supports multi-tier approval: auto-approve small, require confirmation medium,
    block large. Emergency pause can freeze all spending.
    """

    _TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS spend_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL,
        amount REAL NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        recorded_at TEXT NOT NULL,
        approval_status TEXT NOT NULL DEFAULT 'auto'
    );
    CREATE INDEX IF NOT EXISTS idx_spend_category_time
    ON spend_records(category, recorded_at);
    """

    _PAUSE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS pause_state (
        id INTEGER PRIMARY KEY CHECK(id = 1),
        paused INTEGER NOT NULL DEFAULT 0,
        paused_at TEXT,
        reason TEXT
    );
    """

    def __init__(
        self,
        db_path: str = "data/aeon_spend.db",
        wallet=None,
        audit_log=None,
    ) -> None:
        self._db_path = db_path
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._wallet = wallet
        self._audit_log = audit_log
        self._caps: dict[str, SpendGuardConfig] = {}
        self._global_cap: float = 0.0
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.executescript(self._TABLE_SQL)
            conn.executescript(self._PAUSE_TABLE_SQL)
            conn.execute(
                "INSERT OR IGNORE INTO pause_state (id, paused, paused_at, reason) VALUES (1, 0, NULL, NULL);"
            )

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _window_spent(self, conn: sqlite3.Connection, category: str, since: datetime) -> float:
        since_iso = since.isoformat()
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM spend_records WHERE category = ? AND recorded_at > ?;",
            (category, since_iso),
        ).fetchone()
        return float(row[0]) if row else 0.0

    def _total_spent(self, conn: sqlite3.Connection) -> float:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM spend_records;"
        ).fetchone()
        return float(row[0]) if row else 0.0

    def set_global_cap(self, amount: float) -> None:
        """Set a global total cap that cannot be exceeded regardless of category."""
        self._global_cap = amount

    def is_paused(self) -> tuple[bool, str | None]:
        """Return (paused, reason) tuple."""
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT paused, reason FROM pause_state WHERE id = 1;"
            ).fetchone()
        if row is None:
            return False, None
        return bool(row[0]), row[1]

    def pause(self, reason: str) -> None:
        """Emergency pause — block all spending."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE pause_state SET paused = 1, paused_at = ?, reason = ? WHERE id = 1;",
                (self._now().isoformat(), reason),
            )
        if self._audit_log:
            self._audit_log.log("spend_pause", {"reason": reason})

    def resume(self, reason: str = "manual") -> None:
        """Resume spending."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE pause_state SET paused = 0, paused_at = NULL, reason = ? WHERE id = 1;",
                (reason,),
            )
        if self._audit_log:
            self._audit_log.log("spend_resume", {"reason": reason})

    def _require_approval(self, cap: SpendGuardConfig | None, amount: float) -> bool:
        """Return True if this spend requires human confirmation."""
        if cap is None:
            return False
        if cap.auto_approve_threshold > 0 and amount <= cap.auto_approve_threshold:
            return False
        if cap.confirmation_threshold > 0 and amount > cap.confirmation_threshold:
            raise SpendCapExceeded(
                f"Spend exceeds confirmation threshold: {amount:.2f} > {cap.confirmation_threshold:.2f}"
            )
        if cap.confirmation_threshold > 0 and amount > cap.auto_approve_threshold:
            return True
        return False

    def quote_spend(self, category: str, amount: float) -> tuple[bool, str]:
        """Return whether a proposed spend would be allowed without recording it.

        Also checks emergency pause and multi-tier approval.
        """
        try:
            self._check_spend(category, amount)
            return True, ""
        except (SpendCapExceeded, BudgetExceeded, ApprovalRequired, EmergencyPauseActive) as exc:
            return False, str(exc)

    def _check_spend(self, category: str, amount: float) -> None:
        """Internal spend check. Raises on violation."""
        paused, reason = self.is_paused()
        if paused:
            raise EmergencyPauseActive(f"Spending is paused: {reason}")

        cap = self._caps.get(category)
        now = self._now()
        with sqlite3.connect(self._db_path) as conn:
            if cap:
                if cap.hourly > 0:
                    spent = self._window_spent(conn, category, now - timedelta(hours=1))
                    if spent + amount > cap.hourly:
                        raise SpendCapExceeded(
                            f"Hourly cap exceeded for {category}: "
                            f"{spent + amount:.2f} > {cap.hourly:.2f}"
                        )
                if cap.daily > 0:
                    spent = self._window_spent(conn, category, now - timedelta(days=1))
                    if spent + amount > cap.daily:
                        raise SpendCapExceeded(
                            f"Daily cap exceeded for {category}: "
                            f"{spent + amount:.2f} > {cap.daily:.2f}"
                        )
                if cap.weekly > 0:
                    spent = self._window_spent(conn, category, now - timedelta(weeks=1))
                    if spent + amount > cap.weekly:
                        raise SpendCapExceeded(
                            f"Weekly cap exceeded for {category}: "
                            f"{spent + amount:.2f} > {cap.weekly:.2f}"
                        )
                if cap.monthly > 0:
                    spent = self._window_spent(conn, category, now - timedelta(days=30))
                    if spent + amount > cap.monthly:
                        raise SpendCapExceeded(
                            f"Monthly cap exceeded for {category}: "
                            f"{spent + amount:.2f} > {cap.monthly:.2f}"
                        )
                if cap.total > 0:
                    spent = self._window_spent(conn, category, datetime.min.replace(tzinfo=timezone.utc))
                    if spent + amount > cap.total:
                        raise SpendCapExceeded(
                            f"Total cap exceeded for {category}: "
                            f"{spent + amount:.2f} > {cap.total:.2f}"
                        )
            if self._global_cap > 0:
                total_spent = self._total_spent(conn)
                if total_spent + amount > self._global_cap:
                    raise BudgetExceeded(
                        f"Global budget exceeded: {total_spent + amount:.2f} > {self._global_cap:.2f}"
                    )

        # Multi-tier approval check
        if cap and self._require_approval(cap, amount):
            raise ApprovalRequired(
                f"Spend of {amount:.2f} in category '{category}' requires manual confirmation"
            )

    def check_and_record(
        self,
        category: str,
        amount: float,
        reason: str,
        *,
        approval_status: str = "auto",
    ) -> dict[str, Any]:
        """Atomic spend check, ledger debit, and record.

        :returns: A dict receipt resembling CostReceipt.
        """
        self._check_spend(category, amount)

        # Ledger debit
        if self._wallet:
            from auton.ledger.exceptions import InsufficientFundsError
            try:
                receipt = self._wallet.debit(amount, reason)
            except InsufficientFundsError as exc:
                if self._audit_log:
                    self._audit_log.log(
                        "spend_blocked",
                        {"category": category, "amount": amount, "reason": reason, "cause": "insufficient_funds"},
                    )
                raise BudgetExceeded(f"Ledger debit failed: {exc}") from exc
        else:
            receipt = None

        now = self._now()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO spend_records (category, amount, reason, recorded_at, approval_status) VALUES (?, ?, ?, ?, ?);",
                (category, amount, reason, now.isoformat(), approval_status),
            )

        if self._audit_log:
            self._audit_log.log(
                "spend_recorded",
                {"category": category, "amount": amount, "reason": reason, "approval_status": approval_status},
            )

        return {
            "category": category,
            "amount": amount,
            "reason": reason,
            "recorded_at": now.isoformat(),
            "ledger_receipt": receipt.__dict__ if receipt else None,
            "approval_status": approval_status,
        }

    def record_spend(self, category: str, amount: float) -> None:
        """Persist a spend record for *category* without ledger integration."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO spend_records (category, amount, reason, recorded_at) VALUES (?, ?, ?, ?);",
                (category, amount, "manual", self._now().isoformat()),
            )

    # Backward-compatible aliases
    def check_spend(self, category: str, amount: float) -> None:
        """Legacy alias that raises on denial."""
        allowed, reason = self.quote_spend(category, amount)
        if not allowed:
            if "paused" in reason.lower():
                raise EmergencyPauseActive(reason)
            if "requires manual confirmation" in reason:
                raise ApprovalRequired(reason)
            raise SpendCapExceeded(reason)

    def set_cap(
        self,
        category_or_config: str | SpendGuardConfig,
        *,
        hourly: float = 0.0,
        daily: float = 0.0,
        weekly: float = 0.0,
        monthly: float = 0.0,
        auto_approve_threshold: float = 0.0,
        confirmation_threshold: float = 0.0,
    ) -> "SpendGuard":
        """Configure caps for *category*.

        Accepts either a category string (legacy) or a :class:`SpendGuardConfig`.
        """
        if isinstance(category_or_config, SpendGuardConfig):
            self._caps[category_or_config.category] = category_or_config
        else:
            self._caps[category_or_config] = SpendGuardConfig(
                category=category_or_config,
                hourly=hourly,
                daily=daily,
                weekly=weekly,
                monthly=monthly,
                auto_approve_threshold=auto_approve_threshold,
                confirmation_threshold=confirmation_threshold,
            )
        return self

    def get_remaining_budget(self, category: str) -> dict[str, float | None]:
        """Return remaining budget for *category* across all windows."""
        cap = self._caps.get(category)
        if cap is None:
            return {"hourly": None, "daily": None, "weekly": None, "monthly": None, "total": None}

        now = self._now()
        with sqlite3.connect(self._db_path) as conn:
            hourly_spent = self._window_spent(conn, category, now - timedelta(hours=1))
            daily_spent = self._window_spent(conn, category, now - timedelta(days=1))
            weekly_spent = self._window_spent(conn, category, now - timedelta(weeks=1))
            monthly_spent = self._window_spent(conn, category, now - timedelta(days=30))
            total_spent = self._window_spent(conn, category, datetime.min.replace(tzinfo=timezone.utc))

        return {
            "hourly": cap.hourly - hourly_spent if cap.hourly > 0 else None,
            "daily": cap.daily - daily_spent if cap.daily > 0 else None,
            "weekly": cap.weekly - weekly_spent if cap.weekly > 0 else None,
            "monthly": cap.monthly - monthly_spent if cap.monthly > 0 else None,
            "total": cap.total - total_spent if cap.total > 0 else None,
        }

    def get_global_remaining(self) -> float:
        """Return remaining global budget."""
        if self._global_cap <= 0:
            return float("inf")
        with sqlite3.connect(self._db_path) as conn:
            total_spent = self._total_spent(conn)
        return self._global_cap - total_spent


# Backward-compatible aliases
SpendCaps = SpendGuard
