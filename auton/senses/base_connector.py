"""Abstract base connector for the Senses data ingestion framework."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from auton.senses.dataclasses import MarketData


class EventBus(Protocol):
    """Protocol for the core event bus."""

    async def emit(self, event: object) -> None:
        ...


@dataclass(frozen=True)
class DataReceived:
    """Event emitted when a connector receives data."""

    connector: str
    payload: dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class BaseConnector(ABC):
    """Abstract base class for all data connectors.

    Connectors are responsible for ingesting external data and emitting
    ``DataReceived`` events on the event bus. Each connector reports its
    own subscription cost so the Ledger can decide whether to keep it
    enabled.
    """

    def __init__(
        self,
        event_bus: EventBus | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._connected = False
        self._lifetime_cost = 0.0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    async def connect(self) -> None:
        """Open any persistent connection or session."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close the connection and release resources."""

    # ------------------------------------------------------------------
    # Data fetch
    # ------------------------------------------------------------------

    @abstractmethod
    async def fetch_data(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch raw data from the external source.

        Args:
            params: Source-specific query parameters.

        Returns:
            Raw JSON-like data from the source.
        """

    # ------------------------------------------------------------------
    # Cost & tier gating
    # ------------------------------------------------------------------

    @abstractmethod
    def get_subscription_cost(self) -> dict[str, float]:
        """Return the connector's subscription cost.

        Returns:
            A dict with at least ``monthly`` and ``daily`` keys (in USD).
        """

    @abstractmethod
    def is_available(self, tier: int) -> bool:
        """Return whether this connector can be used at the given tier."""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _emit_data(self, payload: dict[str, Any]) -> None:
        """Emit a ``DataReceived`` event on the event bus."""
        if self._event_bus is None:
            return
        event = DataReceived(
            connector=self.__class__.__name__,
            payload=payload,
        )
        await self._event_bus.emit(event)

    def _track_cost(self, amount: float) -> None:
        """Track a marginal cost incurred by this connector."""
        self._lifetime_cost += amount

    @property
    def connected(self) -> bool:
        """Whether the connector is currently connected."""
        return self._connected

    @property
    def lifetime_cost(self) -> float:
        """Total marginal cost accumulated by this connector (USD)."""
        return self._lifetime_cost
