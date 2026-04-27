"""Async pub/sub event bus for ÆON."""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

Callback = Callable[..., Any]


class EventBus:
    """Async pub/sub event bus with typed event support.

    Events are delivered asynchronously and non-blocking.
    Subscribers receive the event payload as a single positional argument.
    """

    def __init__(self) -> None:
        self._subscriptions: dict[type, list[Callback]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, event_type: type, payload: Any) -> None:
        """Publish an event to all subscribers.

        Args:
            event_type: The type/class of the event.
            payload: The event instance to deliver.
        """
        callbacks = []
        async with self._lock:
            if event_type in self._subscriptions:
                callbacks = list(self._subscriptions[event_type])

        if not callbacks:
            return

        tasks = []
        for callback in callbacks:
            try:
                if inspect.iscoroutinefunction(callback):
                    tasks.append(asyncio.create_task(callback(payload)))
                else:
                    callback(payload)
            except Exception:
                logger.exception("EventBus: exception invoking callback for %s", event_type.__name__)

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for exc in results:
                if isinstance(exc, Exception):
                    logger.exception("EventBus: async callback error for %s", event_type.__name__)

    async def subscribe(self, event_type: type, callback: Callback) -> None:
        """Subscribe a callback to an event type.

        Args:
            event_type: The event type to listen for.
            callback: A callable (sync or async) accepting the event payload.
        """
        async with self._lock:
            if event_type not in self._subscriptions:
                self._subscriptions[event_type] = []
            if callback not in self._subscriptions[event_type]:
                self._subscriptions[event_type].append(callback)

    async def unsubscribe(self, event_type: type, callback: Callback) -> None:
        """Unsubscribe a callback from an event type.

        Args:
            event_type: The event type to stop listening for.
            callback: The previously registered callback.
        """
        async with self._lock:
            if event_type in self._subscriptions:
                try:
                    self._subscriptions[event_type].remove(callback)
                except ValueError:
                    pass
                if not self._subscriptions[event_type]:
                    del self._subscriptions[event_type]

    def subscriber_count(self, event_type: type) -> int:
        """Return the number of subscribers for an event type."""
        return len(self._subscriptions.get(event_type, []))
