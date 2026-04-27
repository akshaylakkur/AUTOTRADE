from datetime import datetime, timedelta, timezone
from decimal import Decimal

from auton.core import EventBus
from auton.core.events import Hibernate


class CircuitBreakers:
    HIBERNATE_HOURS = 24

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._hibernate_until: datetime | None = None
        self._drawdown_triggered = False

    async def check_drawdown(
        self,
        current_balance: Decimal | float | str,
        start_of_day_balance: Decimal | float | str,
    ) -> None:
        if self.is_hibernating():
            return

        current = Decimal(str(current_balance))
        start = Decimal(str(start_of_day_balance))

        if start <= 0:
            return

        drawdown = (start - current) / start
        if drawdown >= Decimal("0.10"):
            self._hibernate_until = datetime.now(timezone.utc) + timedelta(hours=self.HIBERNATE_HOURS)
            self._drawdown_triggered = True
            await self._event_bus.publish(
                Hibernate,
                Hibernate(
                    reason="daily_drawdown_limit",
                    duration_seconds=self.HIBERNATE_HOURS * 3600,
                ),
            )

    def is_hibernating(self) -> bool:
        if self._hibernate_until is None:
            return False
        if datetime.now(timezone.utc) >= self._hibernate_until:
            self._hibernate_until = None
            return False
        return True

    def was_triggered(self) -> bool:
        return self._drawdown_triggered
