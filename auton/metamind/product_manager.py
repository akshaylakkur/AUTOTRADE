"""Product Manager: decides what to build, estimates cost, tracks lifecycle."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ProductStage(Enum):
    """Lifecycle stages for a SaaS product."""

    IDEATION = "ideation"
    DEVELOPMENT = "development"
    TESTING = "testing"
    DEPLOYED = "deployed"
    MARKETED = "marketed"
    REVENUE = "revenue"
    SUNSET = "sunset"


class ProductCategory(Enum):
    """Categories of products ÆON can build."""

    API_SERVICE = "api_service"
    WEB_APP = "web_app"
    MICROSAAS = "microsaas"
    TOOL = "tool"
    CONTENT = "content"


@dataclass(frozen=True)
class MarketOpportunity:
    """A discovered market opportunity."""

    category: ProductCategory
    name: str
    description: str
    estimated_tam: float  # Total Addressable Market in USD
    competition_level: str  # low, medium, high
    trend_score: float  # 0.0 - 1.0
    data_sources: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "name": self.name,
            "description": self.description,
            "estimated_tam": self.estimated_tam,
            "competition_level": self.competition_level,
            "trend_score": self.trend_score,
            "data_sources": list(self.data_sources),
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass(frozen=True)
class CostEstimate:
    """Estimated cost to build and launch a product."""

    llm_tokens: int
    compute_hours: float
    hosting_setup_cost: float
    marketplace_fees: float
    estimated_dev_time_hours: float
    total_estimated_cost: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "llm_tokens": self.llm_tokens,
            "compute_hours": self.compute_hours,
            "hosting_setup_cost": self.hosting_setup_cost,
            "marketplace_fees": self.marketplace_fees,
            "estimated_dev_time_hours": self.estimated_dev_time_hours,
            "total_estimated_cost": self.total_estimated_cost,
        }


@dataclass(frozen=True)
class ProductRecord:
    """A product in ÆON's portfolio."""

    product_id: str
    name: str
    category: ProductCategory
    stage: ProductStage
    cost_estimate: CostEstimate
    actual_cost: float = 0.0
    revenue: float = 0.0
    deployed_url: str = ""
    marketplace_urls: dict[str, str] = field(default_factory=dict)
    source_paths: list[str] = field(default_factory=list)
    metadata_json: str = "{}"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def metadata(self) -> dict[str, Any]:
        try:
            return json.loads(self.metadata_json)
        except json.JSONDecodeError:
            return {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "name": self.name,
            "category": self.category.value,
            "stage": self.stage.value,
            "cost_estimate": self.cost_estimate.to_dict(),
            "actual_cost": self.actual_cost,
            "revenue": self.revenue,
            "deployed_url": self.deployed_url,
            "marketplace_urls": dict(self.marketplace_urls),
            "source_paths": list(self.source_paths),
            "metadata": self.metadata(),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class ProductManager:
    """Decides what to build, estimates cost, and tracks product lifecycle."""

    _DDL = """
    CREATE TABLE IF NOT EXISTS products (
        product_id      TEXT PRIMARY KEY,
        name            TEXT NOT NULL,
        category        TEXT NOT NULL,
        stage           TEXT NOT NULL,
        cost_estimate   TEXT NOT NULL,
        actual_cost     REAL DEFAULT 0.0,
        revenue         REAL DEFAULT 0.0,
        deployed_url    TEXT DEFAULT '',
        marketplace_urls TEXT DEFAULT '{}',
        source_paths    TEXT DEFAULT '[]',
        metadata_json   TEXT DEFAULT '{}',
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS opportunities (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        category        TEXT NOT NULL,
        name            TEXT NOT NULL,
        description     TEXT NOT NULL,
        estimated_tam   REAL DEFAULT 0.0,
        competition_level TEXT DEFAULT 'medium',
        trend_score     REAL DEFAULT 0.0,
        data_sources    TEXT DEFAULT '[]',
        timestamp       TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_products_stage ON products(stage);
    CREATE INDEX IF NOT EXISTS idx_opportunities_trend ON opportunities(trend_score DESC);
    """

    def __init__(self, db_path: str | Path = "data/product_manager.db") -> None:
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
    # Opportunity discovery
    # ------------------------------------------------------------------ #
    def register_opportunity(self, opp: MarketOpportunity) -> int:
        """Store a discovered market opportunity."""
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO opportunities
                (category, name, description, estimated_tam, competition_level, trend_score, data_sources, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    opp.category.value,
                    opp.name,
                    opp.description,
                    opp.estimated_tam,
                    opp.competition_level,
                    opp.trend_score,
                    json.dumps(opp.data_sources),
                    opp.timestamp.isoformat(),
                ),
            )
            conn.commit()
            logger.info("Registered opportunity: %s (TAM $%.2f)", opp.name, opp.estimated_tam)
            return cur.lastrowid or 0

    def score_opportunities(self, limit: int = 10) -> list[tuple[MarketOpportunity, float]]:
        """Score opportunities by trend, low competition, and TAM."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM opportunities
                ORDER BY trend_score DESC, estimated_tam DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        scored: list[tuple[MarketOpportunity, float]] = []
        for row in rows:
            opp = MarketOpportunity(
                category=ProductCategory(row["category"]),
                name=row["name"],
                description=row["description"],
                estimated_tam=row["estimated_tam"],
                competition_level=row["competition_level"],
                trend_score=row["trend_score"],
                data_sources=json.loads(row["data_sources"]),
                timestamp=datetime.fromisoformat(row["timestamp"]),
            )
            competition_multiplier = {"low": 1.0, "medium": 0.7, "high": 0.4}.get(
                opp.competition_level, 0.5
            )
            score = opp.trend_score * competition_multiplier * min(opp.estimated_tam / 1000.0, 1.0)
            scored.append((opp, round(score, 4)))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    # ------------------------------------------------------------------ #
    # Cost estimation
    # ------------------------------------------------------------------ #
    @staticmethod
    def estimate_cost(category: ProductCategory, complexity: str = "medium") -> CostEstimate:
        """Rough cost estimate based on category and complexity."""
        multipliers = {"low": 0.6, "medium": 1.0, "high": 1.8}
        mult = multipliers.get(complexity, 1.0)

        base = {
            ProductCategory.API_SERVICE: (50_000, 2.0, 5.0, 0.0, 8.0),
            ProductCategory.WEB_APP: (100_000, 4.0, 10.0, 0.0, 16.0),
            ProductCategory.MICROSAAS: (80_000, 3.0, 8.0, 50.0, 12.0),
            ProductCategory.TOOL: (30_000, 1.0, 3.0, 0.0, 4.0),
            ProductCategory.CONTENT: (20_000, 0.5, 0.0, 20.0, 2.0),
        }

        tokens, compute, hosting, marketplace, dev_time = base.get(
            category, (50_000, 2.0, 5.0, 0.0, 8.0)
        )

        llm_cost = (tokens * mult) / 4 * 0.00001  # approx token cost
        compute_cost = compute * mult * 0.50  # $0.50/hr
        total = llm_cost + compute_cost + (hosting * mult) + (marketplace * mult)

        return CostEstimate(
            llm_tokens=int(tokens * mult),
            compute_hours=round(compute * mult, 2),
            hosting_setup_cost=round(hosting * mult, 2),
            marketplace_fees=round(marketplace * mult, 2),
            estimated_dev_time_hours=round(dev_time * mult, 2),
            total_estimated_cost=round(total, 4),
        )

    # ------------------------------------------------------------------ #
    # Product lifecycle
    # ------------------------------------------------------------------ #
    def create_product(
        self,
        product_id: str,
        name: str,
        category: ProductCategory,
        cost_estimate: CostEstimate,
        metadata: dict[str, Any] | None = None,
    ) -> ProductRecord:
        """Register a new product in the portfolio."""
        now = datetime.now(timezone.utc)
        record = ProductRecord(
            product_id=product_id,
            name=name,
            category=category,
            stage=ProductStage.IDEATION,
            cost_estimate=cost_estimate,
            metadata_json=json.dumps(metadata or {}),
            created_at=now,
            updated_at=now,
        )
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO products
                (product_id, name, category, stage, cost_estimate, actual_cost, revenue,
                 deployed_url, marketplace_urls, source_paths, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.product_id,
                    record.name,
                    record.category.value,
                    record.stage.value,
                    json.dumps(record.cost_estimate.to_dict()),
                    record.actual_cost,
                    record.revenue,
                    record.deployed_url,
                    json.dumps(record.marketplace_urls),
                    json.dumps(record.source_paths),
                    record.metadata_json,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
            conn.commit()
        logger.info("Created product %s: %s", product_id, name)
        return record

    def update_stage(self, product_id: str, stage: ProductStage) -> bool:
        """Move a product to a new lifecycle stage."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE products SET stage = ?, updated_at = ? WHERE product_id = ?",
                (stage.value, now, product_id),
            )
            conn.commit()
            if cur.rowcount:
                logger.info("Product %s moved to stage %s", product_id, stage.value)
                return True
        return False

    def record_cost(self, product_id: str, amount: float) -> bool:
        """Add actual development cost to a product."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE products SET actual_cost = actual_cost + ? WHERE product_id = ?",
                (amount, product_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def record_revenue(self, product_id: str, amount: float) -> bool:
        """Add revenue to a product."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE products SET revenue = revenue + ? WHERE product_id = ?",
                (amount, product_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def set_deployed_url(self, product_id: str, url: str) -> bool:
        """Record the deployment URL for a product."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE products SET deployed_url = ? WHERE product_id = ?",
                (url, product_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def add_marketplace_url(self, product_id: str, marketplace: str, url: str) -> bool:
        """Add a marketplace listing URL."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT marketplace_urls FROM products WHERE product_id = ?",
                (product_id,),
            ).fetchone()
            if not row:
                return False
            urls = json.loads(row["marketplace_urls"])
            urls[marketplace] = url
            conn.execute(
                "UPDATE products SET marketplace_urls = ? WHERE product_id = ?",
                (json.dumps(urls), product_id),
            )
            conn.commit()
            return True

    def add_source_path(self, product_id: str, path: str) -> bool:
        """Track a source file or directory for the product."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT source_paths FROM products WHERE product_id = ?",
                (product_id,),
            ).fetchone()
            if not row:
                return False
            paths = json.loads(row["source_paths"])
            if path not in paths:
                paths.append(path)
                conn.execute(
                    "UPDATE products SET source_paths = ? WHERE product_id = ?",
                    (json.dumps(paths), product_id),
                )
                conn.commit()
            return True

    def get_product(self, product_id: str) -> ProductRecord | None:
        """Retrieve a product by ID."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM products WHERE product_id = ?", (product_id,)
            ).fetchone()
        if not row:
            return None
        return self._row_to_product(row)

    def list_products(self, stage: ProductStage | None = None) -> list[ProductRecord]:
        """List all products, optionally filtered by stage."""
        with self._conn() as conn:
            if stage:
                rows = conn.execute(
                    "SELECT * FROM products WHERE stage = ? ORDER BY created_at DESC",
                    (stage.value,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM products ORDER BY created_at DESC"
                ).fetchall()
        return [self._row_to_product(r) for r in rows]

    def portfolio_summary(self) -> dict[str, Any]:
        """High-level portfolio metrics."""
        with self._conn() as conn:
            total_products = conn.execute(
                "SELECT COUNT(*) FROM products"
            ).fetchone()[0]
            total_revenue = conn.execute(
                "SELECT COALESCE(SUM(revenue), 0) FROM products"
            ).fetchone()[0]
            total_cost = conn.execute(
                "SELECT COALESCE(SUM(actual_cost), 0) FROM products"
            ).fetchone()[0]
            stage_counts = conn.execute(
                "SELECT stage, COUNT(*) FROM products GROUP BY stage"
            ).fetchall()

        return {
            "total_products": total_products,
            "total_revenue": round(total_revenue, 4),
            "total_cost": round(total_cost, 4),
            "net_profit": round(total_revenue - total_cost, 4),
            "stage_breakdown": {row[0]: row[1] for row in stage_counts},
        }

    @staticmethod
    def _row_to_product(row: sqlite3.Row) -> ProductRecord:
        return ProductRecord(
            product_id=row["product_id"],
            name=row["name"],
            category=ProductCategory(row["category"]),
            stage=ProductStage(row["stage"]),
            cost_estimate=CostEstimate(**json.loads(row["cost_estimate"])),
            actual_cost=row["actual_cost"],
            revenue=row["revenue"],
            deployed_url=row["deployed_url"],
            marketplace_urls=json.loads(row["marketplace_urls"]),
            source_paths=json.loads(row["source_paths"]),
            metadata_json=row["metadata_json"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
