"""SimulationAnalyzer — performance metrics for a simulation run."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Sequence


@dataclass(frozen=True)
class SimulationMetrics:
    """Aggregated performance metrics for a simulation."""

    total_pnl: float
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown: float
    max_drawdown_pct: float
    win_rate: float
    win_count: int
    loss_count: int
    total_trades: int
    avg_trade_pnl: float


class SimulationAnalyzer:
    """Computes P&L, Sharpe, drawdown, and win rate from a series of trade returns.

    Accepts either :class:`Decimal` or ``float`` values and normalises
    to ``float`` for metric calculations.
    """

    def __init__(self, risk_free_rate_annual: float = 0.0) -> None:
        self._returns: list[float] = []
        self._risk_free_rate_annual = risk_free_rate_annual

    # ------------------------------------------------------------------ #
    # Data ingestion
    # ------------------------------------------------------------------ #
    def add_return(self, ret: float | Decimal) -> None:
        """Register a single-period return (e.g. one trade P&L)."""
        self._returns.append(float(ret))

    def add_returns(self, returns: Iterable[float | Decimal]) -> None:
        """Register multiple returns at once."""
        for r in returns:
            self.add_return(r)

    def reset(self) -> None:
        """Clear all ingested returns."""
        self._returns.clear()

    # ------------------------------------------------------------------ #
    # Metric calculations
    # ------------------------------------------------------------------ #
    def compute(self) -> SimulationMetrics:
        """Return a :class:`SimulationMetrics` snapshot.

        If no returns have been recorded, all values are zero.
        """
        if not self._returns:
            return SimulationMetrics(
                total_pnl=0.0,
                total_return_pct=0.0,
                sharpe_ratio=0.0,
                max_drawdown=0.0,
                max_drawdown_pct=0.0,
                win_rate=0.0,
                win_count=0,
                loss_count=0,
                total_trades=0,
                avg_trade_pnl=0.0,
            )

        total_pnl = sum(self._returns)
        wins = [r for r in self._returns if r > 0]
        losses = [r for r in self._returns if r <= 0]
        win_count = len(wins)
        loss_count = len(losses)
        total_trades = len(self._returns)
        win_rate = win_count / total_trades if total_trades else 0.0
        avg_trade_pnl = total_pnl / total_trades

        # Cumulative drawdown
        peak = 0.0
        running = 0.0
        max_dd = 0.0
        for r in self._returns:
            running += r
            if running > peak:
                peak = running
            dd = peak - running
            if dd > max_dd:
                max_dd = dd

        max_dd_pct = (max_dd / peak) if peak != 0.0 else 0.0

        # Sharpe ratio (annualised, simple)
        sharpe = self._sharpe(self._returns)

        return SimulationMetrics(
            total_pnl=total_pnl,
            total_return_pct=(self._returns[-1] / abs(self._returns[0]) * 100)
            if self._returns and self._returns[0] != 0
            else 0.0,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            max_drawdown_pct=max_dd_pct,
            win_rate=win_rate,
            win_count=win_count,
            loss_count=loss_count,
            total_trades=total_trades,
            avg_trade_pnl=avg_trade_pnl,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _sharpe(self, returns: Sequence[float]) -> float:
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(variance) if variance > 0 else 0.0
        if std == 0:
            return 0.0
        # De-annualise the risk-free rate to a per-trade approximation.
        # Caller should set risk_free_rate_annual to match their horizon.
        rf_per_trade = self._risk_free_rate_annual / len(returns)
        return (mean - rf_per_trade) / std

    @staticmethod
    def from_trades(trades: Iterable[float | Decimal], *, risk_free_rate_annual: float = 0.0) -> SimulationMetrics:
        """Convenience constructor: compute metrics directly from a sequence of trades."""
        analyzer = SimulationAnalyzer(risk_free_rate_annual=risk_free_rate_annual)
        analyzer.add_returns(trades)
        return analyzer.compute()
