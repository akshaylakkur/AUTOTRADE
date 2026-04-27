"""Data classes for limb actions and results."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"
    STOP_LOSS_LIMIT = "STOP_LOSS_LIMIT"
    TAKE_PROFIT = "TAKE_PROFIT"
    TAKE_PROFIT_LIMIT = "TAKE_PROFIT_LIMIT"
    LIMIT_MAKER = "LIMIT_MAKER"


class OrderStatus(str, Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    PENDING_CANCEL = "PENDING_CANCEL"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class TradeOrder:
    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType
    price: float | None = None
    time_in_force: str = "GTC"
    client_order_id: str | None = None


@dataclass(frozen=False, slots=True)
class OrderResult:
    order_id: str
    symbol: str
    side: OrderSide
    status: OrderStatus
    executed_qty: float
    cummulative_quote_qty: float
    price: float
    order_type: OrderType
    time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fills: list[dict[str, Any]] = field(default_factory=list)
    raw_response: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class Product:
    product_id: str
    name: str
    description: str | None
    price_cents: int
    currency: str = "usd"
    active: bool = True
    raw_response: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class CheckoutSession:
    session_id: str
    url: str
    product_id: str
    status: str
    raw_response: dict[str, Any] = field(default_factory=dict, repr=False)
