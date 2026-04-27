"""Vault stub for AEON."""

from typing import Any


class Vault:
    """Minimal vault / key management stub."""

    def __init__(self, event_bus: Any) -> None:
        self.event_bus = event_bus

    async def initialize(self) -> None:
        pass

    async def revoke_all_keys(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass
