from decimal import Decimal
from typing import Dict, Iterable, Optional

from auton.core import EventBus
from auton.core.events import EmergencyLiquidate
from auton.reflexes.dataclasses import LiquidationOrder


class EmergencyLiquidator:
    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._liquidated: Dict[str, str] = {}
        self._subscribed = False

    async def start(self) -> None:
        if not self._subscribed:
            await self._event_bus.subscribe(EmergencyLiquidate, self._on_emergency)
            self._subscribed = True

    async def _on_emergency(self, event: EmergencyLiquidate) -> None:
        for pos in event.positions:
            symbol = pos.get("symbol")
            if symbol:
                await self.liquidate_symbol(symbol, event.reason)

    async def liquidate_symbol(self, symbol: str, reason: str) -> None:
        self._liquidated[symbol] = reason
        await self._event_bus.publish(
            LiquidationOrder,
            LiquidationOrder(symbol=symbol, reason=reason),
        )

    async def liquidate_all_positions(
        self, reason: str, symbols: Optional[Iterable[str]] = None
    ) -> None:
        targets = list(symbols) if symbols is not None else list(self._liquidated.keys())
        for symbol in targets:
            await self.liquidate_symbol(symbol, reason)

    async def check_survival(
        self, balance: Decimal, survival_threshold: Decimal, positions: Iterable[str]
    ) -> None:
        if balance < survival_threshold:
            await self.liquidate_all_positions("survival_threshold_breach", positions)

    def is_liquidated(self, symbol: str) -> bool:
        return symbol in self._liquidated

    def liquidation_reason(self, symbol: str) -> Optional[str]:
        return self._liquidated.get(symbol)
