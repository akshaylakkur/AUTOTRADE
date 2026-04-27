from decimal import Decimal

from auton.core.constants import RISK_LIMITS
from auton.reflexes.dataclasses import PositionSize


class PositionSizer:
    def calculate_position_size(
        self,
        balance: Decimal | float | str,
        edge: Decimal | float | str,
        odds: Decimal | float | str,
        tier: int,
    ) -> PositionSize:
        balance = Decimal(str(balance))
        edge = Decimal(str(edge))
        odds = Decimal(str(odds))

        if odds <= 0:
            raise ValueError("odds must be positive")

        tier_config = RISK_LIMITS.get(tier, RISK_LIMITS[0])
        tier_cap = Decimal(str(tier_config["max_position_pct"]))
        survival_reserve = Decimal(str(tier_config["survival_reserve_pct"]))

        kelly = edge / odds
        fraction = min(kelly, tier_cap)

        effective_balance = balance * (Decimal("1") - survival_reserve)
        quantity = effective_balance * fraction
        max_loss = quantity

        if quantity < 0:
            quantity = Decimal("0")
            max_loss = Decimal("0")

        return PositionSize(quantity=quantity, max_loss=max_loss)
