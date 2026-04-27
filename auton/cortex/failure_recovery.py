"""Autonomous failure recovery for ÆON."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from auton.core.event_bus import EventBus
from auton.core.events import Hibernate
from auton.cortex.dataclasses import RecoveryAction, RecoveryStrategy

logger = logging.getLogger(__name__)


class FailureRecovery:
    """Handles errors autonomously without human intervention."""

    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        max_retries: int = 3,
        base_backoff: float = 1.0,
    ) -> None:
        self._event_bus = event_bus
        self._max_retries = max_retries
        self._base_backoff = base_backoff

    async def handle_api_error(
        self,
        error: Exception,
        context: dict[str, Any],
    ) -> RecoveryAction:
        """Recover from an external API failure.

        Strategy:
        1. Retry with exponential backoff up to *max_retries*.
        2. If retries exhausted, switch data source if available.
        3. Otherwise enter hibernation.

        Args:
            error: The exception raised by the API call.
            context: Dict containing at least ``api_name`` and optionally
                ``retry_count``, ``alternative_sources``.

        Returns:
            A :class:`RecoveryAction` describing the chosen strategy.
        """
        retry_count = context.get("retry_count", 0)
        api_name = context.get("api_name", "unknown")

        if retry_count < self._max_retries:
            backoff = self._base_backoff * (2 ** retry_count) + random.uniform(0, 1)
            logger.warning(
                "API error on %s (attempt %d/%d); retrying in %.2fs",
                api_name,
                retry_count + 1,
                self._max_retries,
                backoff,
            )
            return RecoveryAction(
                strategy=RecoveryStrategy.RETRY_WITH_BACKOFF,
                description=f"Retry {api_name} after API error: {error}",
                retry_count=retry_count + 1,
                backoff_seconds=round(backoff, 2),
                metadata={"api_name": api_name, "error": str(error)},
            )

        alt_sources = context.get("alternative_sources", [])
        if alt_sources:
            return RecoveryAction(
                strategy=RecoveryStrategy.SWITCH_DATA_SOURCE,
                description=f"Switch from {api_name} to alternative source",
                retry_count=retry_count,
                backoff_seconds=0.0,
                metadata={
                    "api_name": api_name,
                    "alternative_sources": alt_sources,
                    "error": str(error),
                },
            )

        # No alternatives left: hibernate
        await self._emit_hibernate(f"API failure on {api_name}; no alternatives remaining")
        return RecoveryAction(
            strategy=RecoveryStrategy.ENTER_HIBERNATION,
            description=f"Enter hibernation after repeated API failure on {api_name}",
            retry_count=retry_count,
            backoff_seconds=0.0,
            metadata={"api_name": api_name, "error": str(error)},
        )

    async def handle_market_gap(
        self,
        gap_info: dict[str, Any],
    ) -> RecoveryAction:
        """Recover from a detected market gap (liquidity vacuum, exchange downtime, etc.).

        Strategy:
        * If the gap is small (<2%), retry with backoff.
        * If the gap is moderate (2-5%), liquidate exposed positions.
        * If the gap is severe (>5%), enter hibernation.

        Args:
            gap_info: Dict with ``symbol``, ``gap_pct``, and optionally ``positions``.

        Returns:
            A :class:`RecoveryAction`.
        """
        symbol = gap_info.get("symbol", "UNKNOWN")
        gap_pct = gap_info.get("gap_pct", 0.0)

        if gap_pct < 0.02:
            backoff = self._base_backoff + random.uniform(0, 1)
            return RecoveryAction(
                strategy=RecoveryStrategy.RETRY_WITH_BACKOFF,
                description=f"Minor market gap on {symbol} ({gap_pct:.2%}); retry shortly",
                retry_count=0,
                backoff_seconds=round(backoff, 2),
                metadata={"symbol": symbol, "gap_pct": gap_pct},
            )

        if gap_pct < 0.05:
            return RecoveryAction(
                strategy=RecoveryStrategy.LIQUIDATE_POSITIONS,
                description=f"Moderate market gap on {symbol} ({gap_pct:.2%}); liquidate exposed positions",
                retry_count=0,
                backoff_seconds=0.0,
                metadata={"symbol": symbol, "gap_pct": gap_pct, "positions": gap_info.get("positions", [])},
            )

        await self._emit_hibernate(f"Severe market gap on {symbol} ({gap_pct:.2%})")
        return RecoveryAction(
            strategy=RecoveryStrategy.ENTER_HIBERNATION,
            description=f"Severe market gap on {symbol} ({gap_pct:.2%}); hibernating",
            retry_count=0,
            backoff_seconds=0.0,
            metadata={"symbol": symbol, "gap_pct": gap_pct},
        )

    async def handle_bad_trade(
        self,
        trade_result: dict[str, Any],
    ) -> RecoveryAction:
        """Recover from a losing or otherwise bad trade.

        Strategy depends on the realised loss relative to balance:
        * <1% loss: degrade capability (trade smaller next time).
        * 1-5% loss: liquidate correlated positions to stop bleeding.
        * >5% loss: enter hibernation to prevent revenge trading.

        Args:
            trade_result: Dict with ``symbol``, ``pnl``, ``balance``,
                ``pnl_pct``, and optionally ``correlated_positions``.

        Returns:
            A :class:`RecoveryAction`.
        """
        symbol = trade_result.get("symbol", "UNKNOWN")
        pnl = trade_result.get("pnl", 0.0)
        pnl_pct = trade_result.get("pnl_pct", 0.0)

        if pnl_pct < -0.05:
            await self._emit_hibernate(f"Catastrophic trade loss on {symbol}: {pnl_pct:.2%}")
            return RecoveryAction(
                strategy=RecoveryStrategy.ENTER_HIBERNATION,
                description=f"Catastrophic loss on {symbol} ({pnl_pct:.2%}); hibernating",
                retry_count=0,
                backoff_seconds=0.0,
                metadata={"symbol": symbol, "pnl": pnl, "pnl_pct": pnl_pct},
            )

        if pnl_pct < -0.01:
            return RecoveryAction(
                strategy=RecoveryStrategy.LIQUIDATE_POSITIONS,
                description=f"Significant loss on {symbol} ({pnl_pct:.2%}); liquidating correlated positions",
                retry_count=0,
                backoff_seconds=0.0,
                metadata={
                    "symbol": symbol,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "correlated_positions": trade_result.get("correlated_positions", []),
                },
            )

        return RecoveryAction(
            strategy=RecoveryStrategy.DEGRADE_CAPABILITY,
            description=f"Minor loss on {symbol} ({pnl_pct:.2%}); degrading position sizing",
            retry_count=0,
            backoff_seconds=0.0,
            metadata={"symbol": symbol, "pnl": pnl, "pnl_pct": pnl_pct},
        )

    async def _emit_hibernate(self, reason: str) -> None:
        if self._event_bus is not None:
            try:
                await self._event_bus.publish(Hibernate, Hibernate(reason=reason))
            except Exception:
                logger.exception("FailureRecovery: failed to emit Hibernate event")
