"""Typed event dataclasses for the ÆON event bus."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True, slots=True)
class BalanceChanged:
    """Emitted when the master balance changes."""

    old_balance: float
    new_balance: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str = ""


@dataclass(frozen=True, slots=True)
class TierChanged:
    """Emitted when the operational tier changes."""

    old_tier: int
    new_tier: int
    balance: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class TradeSignal:
    """Emitted when the Cortex generates a trade signal."""

    symbol: str
    side: str  # "BUY" or "SELL"
    quantity: float
    price: float | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CostIncurred:
    """Emitted when an operational cost is deducted."""

    amount: float
    category: str  # e.g. "inference", "data", "compute", "trading_fees"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    description: str = ""


@dataclass(frozen=True, slots=True)
class EmergencyLiquidate:
    """Emitted when the Reflexes trigger emergency liquidation."""

    reason: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    positions: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Hibernate:
    """Emitted when the system enters hibernation."""

    reason: str
    duration_seconds: float | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class Shutdown:
    """Emitted when the system initiates a graceful shutdown."""

    reason: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    final_balance: float | None = None


@dataclass(frozen=True, slots=True)
class DataReceived:
    """Emitted when the Senses ingest new data."""

    source: str
    data_type: str
    payload: Any
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class ReflexTriggered:
    """Emitted when a reflex (execution layer) action fires."""

    reflex_name: str
    payload: Any
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
