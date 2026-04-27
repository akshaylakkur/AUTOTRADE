"""Reflexes stub for AEON."""

from typing import Any


class Reflexes:
    """Minimal reflexes / execution layer stub."""

    def __init__(self, event_bus: Any) -> None:
        self.event_bus = event_bus

    async def initialize(self) -> None:
        pass

    async def monitor(self) -> None:
        pass

    async def liquidate_all(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass
