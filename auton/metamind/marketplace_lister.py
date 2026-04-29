"""Marketplace Lister: lists SaaS products on Stripe Marketplace, Gumroad, etc."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MarketplaceError(Exception):
    """Error during marketplace listing."""


@dataclass(frozen=True)
class ListingRecord:
    """A product listing on a marketplace."""

    listing_id: str
    product_id: str
    marketplace: str
    listing_url: str
    status: str  # pending, live, paused, removed
    price_cents: int = 0
    currency: str = "usd"
    sales_count: int = 0
    revenue: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "listing_id": self.listing_id,
            "product_id": self.product_id,
            "marketplace": self.marketplace,
            "listing_url": self.listing_url,
            "status": self.status,
            "price_cents": self.price_cents,
            "currency": self.currency,
            "sales_count": self.sales_count,
            "revenue": self.revenue,
            "timestamp": self.timestamp.isoformat(),
        }


class MarketplaceLister:
    """Lists and manages SaaS products on external marketplaces."""

    _DDL = """
    CREATE TABLE IF NOT EXISTS listings (
        listing_id      TEXT PRIMARY KEY,
        product_id      TEXT NOT NULL,
        marketplace     TEXT NOT NULL,
        listing_url     TEXT DEFAULT '',
        status          TEXT NOT NULL DEFAULT 'pending',
        price_cents     INTEGER DEFAULT 0,
        currency        TEXT DEFAULT 'usd',
        sales_count     INTEGER DEFAULT 0,
        revenue         REAL DEFAULT 0.0,
        timestamp       TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_listings_product ON listings(product_id);
    CREATE INDEX IF NOT EXISTS idx_listings_marketplace ON listings(marketplace);
    """

    def __init__(self, db_path: str | Path = "data/marketplace.db") -> None:
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

    def _record_listing(self, record: ListingRecord) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO listings
                (listing_id, product_id, marketplace, listing_url, status,
                 price_cents, currency, sales_count, revenue, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.listing_id,
                    record.product_id,
                    record.marketplace,
                    record.listing_url,
                    record.status,
                    record.price_cents,
                    record.currency,
                    record.sales_count,
                    record.revenue,
                    record.timestamp.isoformat(),
                ),
            )
            conn.commit()

    # ------------------------------------------------------------------ #
    # Stripe Marketplace / Product Portal
    # ------------------------------------------------------------------ #
    def list_on_stripe(
        self,
        listing_id: str,
        product_id: str,
        name: str,
        description: str,
        price_cents: int,
        recurring: bool = False,
        api_key: str = "",
    ) -> ListingRecord:
        """Create a product and price on Stripe."""
        key = api_key or os.environ.get("STRIPE_SECRET_KEY", "")
        if not key:
            raise MarketplaceError("Stripe API key not provided")

        try:
            import urllib.request
            import urllib.error

            # Create product
            product_payload = urllib.parse.urlencode({
                "name": name,
                "description": description,
            }).encode()
            req = urllib.request.Request(
                "https://api.stripe.com/v1/products",
                data=product_payload,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                product_data = json.loads(resp.read().decode())
                stripe_product_id = product_data["id"]

            # Create price
            price_payload = urllib.parse.urlencode({
                "product": stripe_product_id,
                "unit_amount": str(price_cents),
                "currency": "usd",
                **({"recurring[interval]": "month"} if recurring else {}),
            }).encode()
            req = urllib.request.Request(
                "https://api.stripe.com/v1/prices",
                data=price_payload,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                price_data = json.loads(resp.read().decode())
                price_id = price_data["id"]

            url = f"https://buy.stripe.com/test_{price_id}"
            record = ListingRecord(
                listing_id=listing_id,
                product_id=product_id,
                marketplace="stripe",
                listing_url=url,
                status="live",
                price_cents=price_cents,
                currency="usd",
            )
            self._record_listing(record)
            logger.info("Listed product %s on Stripe: %s", product_id, url)
            return record
        except urllib.error.HTTPError as exc:
            body = exc.read().decode() if exc.fp else str(exc)
            raise MarketplaceError(f"Stripe API error: {body}") from exc
        except Exception as exc:
            raise MarketplaceError(f"Stripe listing failed: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Gumroad
    # ------------------------------------------------------------------ #
    def list_on_gumroad(
        self,
        listing_id: str,
        product_id: str,
        name: str,
        description: str,
        price_cents: int,
        api_key: str = "",
    ) -> ListingRecord:
        """Create a product on Gumroad."""
        key = api_key or os.environ.get("GUMROAD_API_KEY", "")
        if not key:
            raise MarketplaceError("Gumroad API key not provided")

        try:
            import urllib.request
            import urllib.error

            payload = json.dumps({
                "name": name,
                "description": description,
                "price": price_cents,
                "currency": "usd",
            }).encode()
            req = urllib.request.Request(
                "https://api.gumroad.com/v2/products",
                data=payload,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
                product_url = data.get("short_url", "")

            record = ListingRecord(
                listing_id=listing_id,
                product_id=product_id,
                marketplace="gumroad",
                listing_url=product_url,
                status="live",
                price_cents=price_cents,
                currency="usd",
            )
            self._record_listing(record)
            logger.info("Listed product %s on Gumroad: %s", product_id, product_url)
            return record
        except urllib.error.HTTPError as exc:
            body = exc.read().decode() if exc.fp else str(exc)
            raise MarketplaceError(f"Gumroad API error: {body}") from exc
        except Exception as exc:
            raise MarketplaceError(f"Gumroad listing failed: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Generic listing (placeholder for other platforms)
    # ------------------------------------------------------------------ #
    def list_product(
        self,
        listing_id: str,
        product_id: str,
        marketplace: str,
        name: str,
        description: str,
        price_cents: int,
        **kwargs: Any,
    ) -> ListingRecord:
        """Dispatch listing to the correct marketplace handler."""
        if marketplace == "stripe":
            return self.list_on_stripe(
                listing_id, product_id, name, description, price_cents,
                recurring=kwargs.get("recurring", False),
                api_key=kwargs.get("api_key", ""),
            )
        if marketplace == "gumroad":
            return self.list_on_gumroad(
                listing_id, product_id, name, description, price_cents,
                api_key=kwargs.get("api_key", ""),
            )
        raise MarketplaceError(f"Unsupported marketplace: {marketplace}")

    # ------------------------------------------------------------------ #
    # Status and revenue updates
    # ------------------------------------------------------------------ #
    def update_sales(
        self,
        listing_id: str,
        sales_count: int | None = None,
        revenue: float | None = None,
        status: str | None = None,
    ) -> bool:
        """Update sales metrics for a listing."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM listings WHERE listing_id = ?", (listing_id,)
            ).fetchone()
            if not row:
                return False

            new_sales = sales_count if sales_count is not None else row["sales_count"]
            new_revenue = revenue if revenue is not None else row["revenue"]
            new_status = status if status is not None else row["status"]

            conn.execute(
                "UPDATE listings SET sales_count = ?, revenue = ?, status = ? WHERE listing_id = ?",
                (new_sales, new_revenue, new_status, listing_id),
            )
            conn.commit()
            return True

    def get_listing(self, listing_id: str) -> ListingRecord | None:
        """Retrieve a single listing record."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM listings WHERE listing_id = ?", (listing_id,)
            ).fetchone()
        if not row:
            return None
        return self._row_to_record(row)

    def list_listings(self, product_id: str | None = None) -> list[ListingRecord]:
        """List all listings, optionally filtered by product."""
        with self._conn() as conn:
            if product_id:
                rows = conn.execute(
                    "SELECT * FROM listings WHERE product_id = ? ORDER BY timestamp DESC",
                    (product_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM listings ORDER BY timestamp DESC"
                ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def total_revenue(self, product_id: str | None = None) -> float:
        """Total revenue across all listings or for a specific product."""
        with self._conn() as conn:
            if product_id:
                row = conn.execute(
                    "SELECT COALESCE(SUM(revenue), 0) FROM listings WHERE product_id = ?",
                    (product_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COALESCE(SUM(revenue), 0) FROM listings"
                ).fetchone()
        return row[0] if row else 0.0

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ListingRecord:
        return ListingRecord(
            listing_id=row["listing_id"],
            product_id=row["product_id"],
            marketplace=row["marketplace"],
            listing_url=row["listing_url"],
            status=row["status"],
            price_cents=row["price_cents"],
            currency=row["currency"],
            sales_count=row["sales_count"],
            revenue=row["revenue"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
        )
