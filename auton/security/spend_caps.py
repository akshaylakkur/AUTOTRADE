"""Hard spend caps for Project ÆON."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing_extensions import Self

from .exceptions import SpendCapExceeded


@dataclass(frozen=True)
class CapConfig:
    """Configuration for spend caps in a single category."""

    hourly: float = 0.0
    daily: float = 0.0
    weekly: float = 0.0


@dataclass
class Window:
    """A sliding time window and the amount spent within it."""

    start: datetime
    amount: float = 0.0


class SpendCaps:
    """Enforce hourly, daily, and weekly spend limits per category.

    Spending is tracked in SQLite so limits survive process restarts.
    """

    _TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS spend_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL,
        amount REAL NOT NULL,
        recorded_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_spend_category_time
    ON spend_records(category, recorded_at);
    """

    def __init__(self, db_path: str = "aeon_spend.db") -> None:
        self._db_path = db_path
        self._caps: dict[str, CapConfig] = {}
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.executescript(self._TABLE_SQL)

    def set_cap(self, category: str, *, hourly: float = 0.0, daily: float = 0.0, weekly: float = 0.0) -> Self:
        """Configure caps for *category*.  A value of ``0`` means no cap."""
        self._caps[category] = CapConfig(hourly=hourly, daily=daily, weekly=weekly)
        return self

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _window_spent(self, conn: sqlite3.Connection, category: str, since: datetime) -> float:
        since_iso = since.isoformat()
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM spend_records WHERE category = ? AND recorded_at > ?;",
            (category, since_iso),
        ).fetchone()
        return float(row[0]) if row else 0.0

    def check_spend(self, category: str, amount: float) -> None:
        """Raise :exc:`SpendCapExceeded` if *amount* would breach a cap."""
        cap = self._caps.get(category)
        if cap is None:
            return

        now = self._now()
        with sqlite3.connect(self._db_path) as conn:
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

    def record_spend(self, category: str, amount: float) -> None:
        """Persist a spend record for *category*."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO spend_records (category, amount, recorded_at) VALUES (?, ?, ?);",
                (category, amount, self._now().isoformat()),
            )

    def get_remaining_budget(self, category: str) -> dict[str, float | None]:
        """Return remaining budget for *category* across all windows.

        A value of ``None`` means no cap is configured for that window.
        """
        cap = self._caps.get(category)
        if cap is None:
            return {"hourly": None, "daily": None, "weekly": None}

        now = self._now()
        with sqlite3.connect(self._db_path) as conn:
            hourly_spent = self._window_spent(conn, category, now - timedelta(hours=1))
            daily_spent = self._window_spent(conn, category, now - timedelta(days=1))
            weekly_spent = self._window_spent(conn, category, now - timedelta(weeks=1))

        return {
            "hourly": cap.hourly - hourly_spent if cap.hourly > 0 else None,
            "daily": cap.daily - daily_spent if cap.daily > 0 else None,
            "weekly": cap.weekly - weekly_spent if cap.weekly > 0 else None,
        }
