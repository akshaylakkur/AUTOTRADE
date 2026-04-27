"""Senses stub for AEON."""

from typing import Any


class Senses:
    """Minimal senses / data ingestion stub."""

    def __init__(self, event_bus: Any, config: Any) -> None:
        self.event_bus = event_bus
        self.config = config

    async def initialize(self) -> None:
        pass

    async def ingest(self) -> None:
        pass

    async def hibernate(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass
