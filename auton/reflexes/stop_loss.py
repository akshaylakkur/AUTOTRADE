from decimal import Decimal
from typing import Dict

from auton.core import EventBus
from auton.core.events import EmergencyLiquidate
from auton.reflexes.dataclasses import StopLossRule


class StopLossEngine:
    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._rules: Dict[str, StopLossRule] = {}

    def add_position(
        self,
        symbol: str,
        entry_price: Decimal | float | str,
        quantity: Decimal | float | str,
        stop_pct: Decimal | float | str,
        trailing: bool = False,
    ) -> None:
        entry = Decimal(str(entry_price))
        rule = StopLossRule(
            symbol=symbol,
            entry_price=entry,
            quantity=Decimal(str(quantity)),
            stop_pct=Decimal(str(stop_pct)),
            trailing=trailing,
            highest_price=entry,
        )
        self._rules[symbol] = rule

    async def check_stop_loss(self, current_prices: Dict[str, Decimal | float | str]) -> None:
        symbols_to_remove: list[str] = []
        for symbol, raw_price in current_prices.items():
            rule = self._rules.get(symbol)
            if rule is None:
                continue
            price = Decimal(str(raw_price))

            if rule.trailing:
                if rule.highest_price is not None and price > rule.highest_price:
                    rule.highest_price = price
                stop_price = (rule.highest_price or rule.entry_price) * (Decimal("1") - rule.stop_pct)
            else:
                stop_price = rule.entry_price * (Decimal("1") - rule.stop_pct)

            if price <= stop_price:
                positions = [{"symbol": symbol, "quantity": float(rule.quantity)}]
                await self._event_bus.publish(
                    EmergencyLiquidate,
                    EmergencyLiquidate(reason="stop_loss_triggered", positions=positions),
                )
                symbols_to_remove.append(symbol)

        for symbol in symbols_to_remove:
            del self._rules[symbol]

    def get_rule(self, symbol: str) -> StopLossRule | None:
        return self._rules.get(symbol)

    def has_position(self, symbol: str) -> bool:
        return symbol in self._rules
