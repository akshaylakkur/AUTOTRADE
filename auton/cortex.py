"""Cortex stub for AEON."""

from typing import Any


class Cortex:
    """Minimal cortex / reasoning engine stub."""

    def __init__(self, event_bus: Any, config: Any) -> None:
        self.event_bus = event_bus
        self.config = config

    async def initialize(self) -> None:
        pass

    async def plan(self) -> None:
        pass

    async def hibernate(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass
