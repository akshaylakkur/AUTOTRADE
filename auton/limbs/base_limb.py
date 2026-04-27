"""Abstract base limb with event bus and ledger integration."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from auton.core.event_bus import EventBus
    from auton.core.tier_gate import TierGate
    from auton.ledger.ledger import Ledger


class BaseLimb(ABC):
    """Abstract base class for all action limbs.

    Each limb is an async-capable interface to an external service.
    Limbs emit lifecycle events via the event bus and report
    operational costs to the ledger.
    """

    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        ledger: Ledger | None = None,
        tier_gate: TierGate | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._ledger = ledger
        self._tier_gate = tier_gate

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @abstractmethod
    async def execute(self, action: Any) -> Any:
        """Execute a generic action payload."""

    @abstractmethod
    async def get_cost_estimate(self, action: Any) -> float:
        """Return the estimated cost (in USD) of executing *action*."""

    @abstractmethod
    def is_available(self, tier: int) -> bool:
        """Return whether this limb can be used at the given operational tier."""

    @abstractmethod
    async def health_check(self) -> dict[str, Any]:
        """Return a health status dictionary for monitoring and failover."""

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._event_bus is not None:
            try:
                asyncio.get_running_loop()
                # Fire-and-forget so we never block the caller.
                asyncio.create_task(
                    self._event_bus.emit(event_type, {"limb": self.name, **payload})
                )
            except RuntimeError:
                pass

    async def _charge(self, amount: float, description: str) -> None:
        if self._ledger is not None:
            await self._ledger.charge(amount, description, source=self.name)

    def __repr__(self) -> str:
        return f"<{self.name}(event_bus={self._event_bus is not None}, ledger={self._ledger is not None})>"
