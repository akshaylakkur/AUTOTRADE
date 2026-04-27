"""Async event bus for AEON module communication."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable


class EventBus:
    """Simple async event bus for decoupled module communication."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable]] = {}
        self._logger = logging.getLogger("aeon.event_bus")

    def subscribe(self, event_name: str, handler: Callable) -> None:
        self._handlers.setdefault(event_name, []).append(handler)

    def unsubscribe(self, event_name: str, handler: Callable) -> None:
        handlers = self._handlers.get(event_name, [])
        if handler in handlers:
            handlers.remove(handler)

    async def emit(self, event_name: str, payload: Any = None) -> None:
        handlers = self._handlers.get(event_name, [])
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(payload)
                else:
                    handler(payload)
            except Exception as exc:
                self._logger.error(
                    f"Event handler error for {event_name}: {exc}",
                    extra={"event": "handler_error", "event_name": event_name, "error": str(exc)},
                )
