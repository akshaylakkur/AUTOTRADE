"""Limbs stub for AEON."""

from typing import Any


class Limbs:
    """Minimal limbs / action interfaces stub."""

    def __init__(self, event_bus: Any, vault: Any) -> None:
        self.event_bus = event_bus
        self.vault = vault

    async def initialize(self) -> None:
        pass

    async def execute(self) -> None:
        pass

    async def hibernate(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass
