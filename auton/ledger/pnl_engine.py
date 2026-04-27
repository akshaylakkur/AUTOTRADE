"""Profit & Loss Engine — realized and unrealized P&L tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable


@dataclass
class Position:
    """A single open position with FIFO cost-basis tracking."""

    symbol: str
    quantity: Decimal
    cost_basis: Decimal  # per unit
    fees: Decimal = Decimal("0")


@dataclass
class RealizedTrade:
    """A closed trade with realized P&L."""

    symbol: str
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    fees: Decimal
    pnl: Decimal
    timestamp: str = field(default_factory=lambda: __import__("datetime").datetime.now(timezone.utc).isoformat())


class PnLEngine:
    """Tracks realized and unrealized profit / loss.

    * FIFO cost basis is used for realized P&L calculations.
    * Unrealized P&L requires a live price feed via :meth:`get_unrealized_pnl`.
    """

    def __init__(self) -> None:
        self._positions: dict[str, list[Position]] = {}
        self._realized: list[RealizedTrade] = []

    # ------------------------------------------------------------------ #
    # Position & trade recording
    # ------------------------------------------------------------------ #
    def record_trade(
        self,
        symbol: str,
        entry_price: Decimal,
        exit_price: Decimal,
        quantity: Decimal,
        fees: Decimal = Decimal("0"),
    ) -> RealizedTrade:
        """Close a position and record realized P&L.

        Args:
            symbol: Trading pair or asset identifier.
            entry_price: Average entry price.
            exit_price: Average exit price.
            quantity: Number of units closed.
            fees: Trading fees associated with this close.

        Returns:
            A :class:`RealizedTrade` summarizing the outcome.
        """
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if entry_price < 0 or exit_price < 0:
            raise ValueError("prices must be non-negative")

        pnl = (exit_price - entry_price) * quantity - fees
        trade = RealizedTrade(
            symbol=symbol,
            quantity=quantity,
            entry_price=entry_price,
            exit_price=exit_price,
            fees=fees,
            pnl=pnl,
        )
        self._realized.append(trade)

        # Update internal FIFO positions
        self._consume_position(symbol, quantity)

        return trade

    def _consume_position(self, symbol: str, quantity: Decimal) -> None:
        """Reduce open position by *quantity* using FIFO."""
        remaining = quantity
        stack = self._positions.setdefault(symbol, [])
        while remaining > 0 and stack:
            oldest = stack[0]
            if oldest.quantity > remaining:
                oldest.quantity -= remaining
                remaining = Decimal("0")
            else:
                remaining -= oldest.quantity
                stack.pop(0)

    def add_position(
        self,
        symbol: str,
        quantity: Decimal,
        cost_basis: Decimal,
        fees: Decimal = Decimal("0"),
    ) -> None:
        """Record an open position (e.g. after a buy without a matching sell)."""
        if quantity <= 0 or cost_basis < 0:
            raise ValueError("invalid quantity or cost_basis")
        self._positions.setdefault(symbol, []).append(
            Position(
                symbol=symbol,
                quantity=quantity,
                cost_basis=cost_basis,
                fees=fees,
            )
        )

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def get_realized_pnl(self, symbol: str | None = None) -> Decimal:
        """Return total realized P&L, optionally filtered by symbol."""
        trades = self._realized
        if symbol:
            trades = [t for t in trades if t.symbol == symbol]
        return sum((t.pnl for t in trades), Decimal("0"))

    def get_unrealized_pnl(self, current_prices: dict[str, Decimal]) -> Decimal:
        """Return total unrealized P&L given a map of symbol -> current price."""
        total = Decimal("0")
        for symbol, stack in self._positions.items():
            price = current_prices.get(symbol)
            if price is None:
                continue
            for pos in stack:
                total += (price - pos.cost_basis) * pos.quantity
        return total

    def get_open_positions(self) -> Iterable[Position]:
        """Yield all open positions across symbols."""
        for stack in self._positions.values():
            yield from stack

    def reconcile(self) -> dict[str, Decimal]:
        """Return a map of symbol -> net quantity still held.

        Useful for sanity-checking against exchange balances.
        """
        result: dict[str, Decimal] = {}
        for symbol, stack in self._positions.items():
            result[symbol] = sum(p.quantity for p in stack)
        return result
