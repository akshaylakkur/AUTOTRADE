from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class PositionSize:
    quantity: Decimal
    max_loss: Decimal


@dataclass
class StopLossRule:
    symbol: str
    entry_price: Decimal
    quantity: Decimal
    stop_pct: Decimal
    trailing: bool = False
    highest_price: Optional[Decimal] = None


@dataclass(frozen=True)
class HealthStatus:
    name: str
    endpoint: str
    healthy: bool
    last_checked: datetime
    latency_ms: float


@dataclass(frozen=True)
class ApiDown:
    name: str
    endpoint: str


@dataclass(frozen=True)
class ApiRecovered:
    name: str


@dataclass(frozen=True)
class LiquidationOrder:
    symbol: str
    reason: str
