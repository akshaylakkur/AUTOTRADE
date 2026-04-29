"""Revenue Tracker: tracks product revenue, churn, LTV, and integrates with Stripe webhooks."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RevenueEvent:
    """A single revenue event (sale, refund, churn)."""

    event_id: str
    product_id: str
    event_type: str  # sale, refund, subscription_created, subscription_cancelled, renewal
    amount: float
    currency: str = "usd"
    customer_id: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata_json: str = "{}"

    def metadata(self) -> dict[str, Any]:
        try:
            return json.loads(self.metadata_json)
        except json.JSONDecodeError:
            return {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "product_id": self.product_id,
            "event_type": self.event_type,
            "amount": self.amount,
            "currency": self.currency,
            "customer_id": self.customer_id,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata(),
        }


@dataclass(frozen=True)
class ProductMetrics:
    """Aggregated metrics for a product."""

    product_id: str
    total_revenue: float
    total_sales: int
    refunds: float
    active_subscriptions: int
    churned_subscriptions: int
    ltv_estimate: float
    mrr: float  # Monthly Recurring Revenue
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "total_revenue": self.total_revenue,
            "total_sales": self.total_sales,
            "refunds": self.refunds,
            "active_subscriptions": self.active_subscriptions,
            "churned_subscriptions": self.churned_subscriptions,
            "ltv_estimate": self.ltv_estimate,
            "mrr": self.mrr,
            "timestamp": self.timestamp.isoformat(),
        }


class RevenueTracker:
    """Tracks product revenue, churn, LTV, and handles Stripe webhooks."""

    _DDL = """
    CREATE TABLE IF NOT EXISTS revenue_events (
        event_id        TEXT PRIMARY KEY,
        product_id      TEXT NOT NULL,
        event_type      TEXT NOT NULL,
        amount          REAL NOT NULL,
        currency        TEXT DEFAULT 'usd',
        customer_id     TEXT DEFAULT '',
        timestamp       TEXT NOT NULL,
        metadata_json   TEXT DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_revenue_product ON revenue_events(product_id);
    CREATE INDEX IF NOT EXISTS idx_revenue_time ON revenue_events(timestamp);
    CREATE INDEX IF NOT EXISTS idx_revenue_customer ON revenue_events(customer_id);
    """

    def __init__(self, db_path: str | Path = "data/revenue.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(self._DDL)
            conn.commit()

    # ------------------------------------------------------------------ #
    # Core event tracking
    # ------------------------------------------------------------------ #
    def record_event(self, event: RevenueEvent) -> None:
        """Record a revenue event."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO revenue_events
                (event_id, product_id, event_type, amount, currency, customer_id, timestamp, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.product_id,
                    event.event_type,
                    event.amount,
                    event.currency,
                    event.customer_id,
                    event.timestamp.isoformat(),
                    event.metadata_json,
                ),
            )
            conn.commit()
        logger.info("Recorded %s event for %s: $%.2f", event.event_type, event.product_id, event.amount)

    def record_sale(
        self,
        event_id: str,
        product_id: str,
        amount: float,
        customer_id: str = "",
        currency: str = "usd",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Convenience method to record a sale."""
        self.record_event(
            RevenueEvent(
                event_id=event_id,
                product_id=product_id,
                event_type="sale",
                amount=amount,
                currency=currency,
                customer_id=customer_id,
                metadata_json=json.dumps(metadata or {}),
            )
        )

    def record_refund(
        self,
        event_id: str,
        product_id: str,
        amount: float,
        customer_id: str = "",
        currency: str = "usd",
    ) -> None:
        """Convenience method to record a refund."""
        self.record_event(
            RevenueEvent(
                event_id=event_id,
                product_id=product_id,
                event_type="refund",
                amount=-abs(amount),
                currency=currency,
                customer_id=customer_id,
            )
        )

    def record_subscription_event(
        self,
        event_id: str,
        product_id: str,
        event_type: str,  # subscription_created, subscription_cancelled, renewal
        amount: float,
        customer_id: str = "",
        currency: str = "usd",
    ) -> None:
        """Record subscription lifecycle events."""
        self.record_event(
            RevenueEvent(
                event_id=event_id,
                product_id=product_id,
                event_type=event_type,
                amount=amount,
                currency=currency,
                customer_id=customer_id,
            )
        )

    # ------------------------------------------------------------------ #
    # Stripe webhook handling
    # ------------------------------------------------------------------ #
    def handle_stripe_webhook(self, payload: dict[str, Any]) -> RevenueEvent | None:
        """Process a Stripe webhook payload and record the event."""
        event_type = payload.get("type", "")
        data = payload.get("data", {}).get("object", {})
        event_id = payload.get("id", f"stripe_{datetime.now(timezone.utc).timestamp()}")

        product_id = data.get("metadata", {}).get("aeon_product_id", "unknown")
        customer_id = data.get("customer", "")
        currency = data.get("currency", "usd")
        amount = (data.get("amount_total") or data.get("amount", 0)) / 100.0  # Stripe uses cents

        if event_type.startswith("checkout.session.completed"):
            event = RevenueEvent(
                event_id=event_id,
                product_id=product_id,
                event_type="sale",
                amount=amount,
                currency=currency,
                customer_id=customer_id,
                metadata_json=json.dumps({"stripe_event": event_type, "session_id": data.get("id", "")}),
            )
            self.record_event(event)
            return event

        if event_type.startswith("invoice.payment_succeeded"):
            event = RevenueEvent(
                event_id=f"{event_id}_renewal",
                product_id=product_id,
                event_type="renewal",
                amount=amount,
                currency=currency,
                customer_id=customer_id,
                metadata_json=json.dumps({"stripe_event": event_type, "invoice_id": data.get("id", "")}),
            )
            self.record_event(event)
            return event

        if event_type.startswith("customer.subscription.deleted"):
            event = RevenueEvent(
                event_id=f"{event_id}_churn",
                product_id=product_id,
                event_type="subscription_cancelled",
                amount=0.0,
                currency=currency,
                customer_id=customer_id,
                metadata_json=json.dumps({"stripe_event": event_type}),
            )
            self.record_event(event)
            return event

        if event_type.startswith("charge.refunded"):
            event = RevenueEvent(
                event_id=f"{event_id}_refund",
                product_id=product_id,
                event_type="refund",
                amount=-abs(amount),
                currency=currency,
                customer_id=customer_id,
                metadata_json=json.dumps({"stripe_event": event_type, "charge_id": data.get("id", "")}),
            )
            self.record_event(event)
            return event

        logger.debug("Unhandled Stripe event type: %s", event_type)
        return None

    # ------------------------------------------------------------------ #
    # Metrics and analytics
    # ------------------------------------------------------------------ #
    def get_product_metrics(self, product_id: str) -> ProductMetrics:
        """Calculate aggregated metrics for a product."""
        with self._conn() as conn:
            total_revenue = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM revenue_events WHERE product_id = ? AND event_type IN ('sale', 'renewal')",
                (product_id,),
            ).fetchone()[0]

            total_sales = conn.execute(
                "SELECT COUNT(*) FROM revenue_events WHERE product_id = ? AND event_type = 'sale'",
                (product_id,),
            ).fetchone()[0]

            refunds = conn.execute(
                "SELECT COALESCE(SUM(ABS(amount)), 0) FROM revenue_events WHERE product_id = ? AND event_type = 'refund'",
                (product_id,),
            ).fetchone()[0]

            active_subs = conn.execute(
                """
                SELECT COUNT(DISTINCT customer_id) FROM revenue_events
                WHERE product_id = ? AND event_type IN ('subscription_created', 'renewal')
                AND customer_id NOT IN (
                    SELECT customer_id FROM revenue_events
                    WHERE product_id = ? AND event_type = 'subscription_cancelled'
                )
                """,
                (product_id, product_id),
            ).fetchone()[0]

            churned = conn.execute(
                "SELECT COUNT(DISTINCT customer_id) FROM revenue_events WHERE product_id = ? AND event_type = 'subscription_cancelled'",
                (product_id,),
            ).fetchone()[0]

            # MRR = sum of renewals in last 30 days
            thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            mrr = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM revenue_events WHERE product_id = ? AND event_type = 'renewal' AND timestamp > ?",
                (product_id, thirty_days_ago),
            ).fetchone()[0]

        # Simple LTV estimate: avg revenue per customer * 12 months
        customers = active_subs + churned
        avg_revenue = total_revenue / customers if customers > 0 else 0.0
        ltv = avg_revenue * 12

        return ProductMetrics(
            product_id=product_id,
            total_revenue=round(total_revenue, 4),
            total_sales=total_sales,
            refunds=round(refunds, 4),
            active_subscriptions=active_subs,
            churned_subscriptions=churned,
            ltv_estimate=round(ltv, 4),
            mrr=round(mrr, 4),
        )

    def get_revenue_time_series(
        self,
        product_id: str,
        days: int = 30,
    ) -> list[dict[str, Any]]:
        """Daily revenue for the last N days."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT date(timestamp) as day,
                       SUM(CASE WHEN event_type IN ('sale', 'renewal') THEN amount ELSE 0 END) as revenue,
                       COUNT(CASE WHEN event_type = 'sale' THEN 1 END) as sales
                FROM revenue_events
                WHERE product_id = ? AND timestamp > ?
                GROUP BY day
                ORDER BY day
                """,
                (product_id, cutoff),
            ).fetchall()
        return [{"date": r["day"], "revenue": r["revenue"], "sales": r["sales"]} for r in rows]

    def get_customer_churn_rate(self, product_id: str, days: int = 30) -> float:
        """Calculate churn rate over the last N days."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(DISTINCT customer_id) FROM revenue_events WHERE product_id = ? AND timestamp > ?",
                (product_id, cutoff),
            ).fetchone()[0]
            churned = conn.execute(
                "SELECT COUNT(DISTINCT customer_id) FROM revenue_events WHERE product_id = ? AND event_type = 'subscription_cancelled' AND timestamp > ?",
                (product_id, cutoff),
            ).fetchone()[0]
        return round(churned / total, 4) if total > 0 else 0.0

    def total_portfolio_revenue(self) -> float:
        """Total revenue across all products."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM revenue_events WHERE event_type IN ('sale', 'renewal')"
            ).fetchone()
        return row[0] if row else 0.0
