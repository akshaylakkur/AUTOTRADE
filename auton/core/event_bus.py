"""Async pub/sub event bus for ÆON."""

from __future__ import annotations

import asyncio
import inspect
import logging
from enum import Enum, auto
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

Callback = Callable[..., Any]


class Priority(Enum):
    """Event priority levels."""

    URGENT = auto()
    NORMAL = auto()
    BACKGROUND = auto()


class EventBus:
    """Async pub/sub event bus with typed event support and priority levels.

    Events are delivered asynchronously and non-blocking.
    Subscribers receive the event payload as a single positional argument.
    Urgent events are processed immediately; background events are queued
    and dispatched via a background worker.
    """

    def __init__(self) -> None:
        self._subscriptions: dict[type, list[Callback]] = {}
        self._priority_subscriptions: dict[type, dict[Priority, list[Callback]]] = {}
        self._background_queue: asyncio.Queue[tuple[type, Any]] = asyncio.Queue()
        self._background_worker: asyncio.Task[Any] | None = None
        self._lock = asyncio.Lock()

    async def _dispatch_background(self) -> None:
        """Worker that dispatches background-priority events sequentially."""
        while True:
            event_type, payload = await self._background_queue.get()
            try:
                await self._dispatch(event_type, payload, priority=Priority.BACKGROUND)
            except Exception:
                logger.exception("EventBus: background dispatch error for %s", event_type.__name__)
            finally:
                self._background_queue.task_done()

    async def _dispatch(
        self, event_type: type, payload: Any, priority: Priority | None = None
    ) -> None:
        """Dispatch an event to matching subscribers."""
        callbacks: list[Callback] = []
        async with self._lock:
            if event_type in self._subscriptions:
                callbacks = list(self._subscriptions[event_type])
            if priority and event_type in self._priority_subscriptions:
                callbacks.extend(self._priority_subscriptions[event_type].get(priority, []))

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

    async def publish(self, event_type: type, payload: Any, priority: Priority = Priority.NORMAL) -> None:
        """Publish an event to all subscribers.

        Args:
            event_type: The type/class of the event.
            payload: The event instance to deliver.
            priority: Urgent events are awaited immediately; background events
                are queued. Defaults to NORMAL.
        """
        if priority == Priority.BACKGROUND:
            await self._background_queue.put((event_type, payload))
            return

        await self._dispatch(event_type, payload, priority=priority)

    async def subscribe(
        self, event_type: type, callback: Callback, priority: Priority | None = None
    ) -> None:
        """Subscribe a callback to an event type.

        Args:
            event_type: The event type to listen for.
            callback: A callable (sync or async) accepting the event payload.
            priority: If provided, the callback only receives events of that
                priority level. If None, the callback receives all events
                regardless of priority.
        """
        async with self._lock:
            if priority is None:
                if event_type not in self._subscriptions:
                    self._subscriptions[event_type] = []
                if callback not in self._subscriptions[event_type]:
                    self._subscriptions[event_type].append(callback)
            else:
                if event_type not in self._priority_subscriptions:
                    self._priority_subscriptions[event_type] = {}
                if priority not in self._priority_subscriptions[event_type]:
                    self._priority_subscriptions[event_type][priority] = []
                if callback not in self._priority_subscriptions[event_type][priority]:
                    self._priority_subscriptions[event_type][priority].append(callback)

    async def unsubscribe(
        self, event_type: type, callback: Callback, priority: Priority | None = None
    ) -> None:
        """Unsubscribe a callback from an event type.

        Args:
            event_type: The event type to stop listening for.
            callback: The previously registered callback.
            priority: If provided, removes the callback only from that
                priority bucket. If None, removes from the general list.
        """
        async with self._lock:
            if priority is None:
                if event_type in self._subscriptions:
                    try:
                        self._subscriptions[event_type].remove(callback)
                    except ValueError:
                        pass
                    if not self._subscriptions[event_type]:
                        del self._subscriptions[event_type]
            else:
                if event_type in self._priority_subscriptions:
                    try:
                        self._priority_subscriptions[event_type][priority].remove(callback)
                    except (KeyError, ValueError):
                        pass
                    if not self._priority_subscriptions[event_type].get(priority):
                        self._priority_subscriptions[event_type].pop(priority, None)
                    if not self._priority_subscriptions[event_type]:
                        del self._priority_subscriptions[event_type]

    def subscriber_count(self, event_type: type) -> int:
        """Return the number of subscribers for an event type."""
        count = len(self._subscriptions.get(event_type, []))
        for bucket in self._priority_subscriptions.get(event_type, {}).values():
            count += len(bucket)
        return count

    async def start(self) -> None:
        """Start the background worker for background-priority events."""
        if self._background_worker is None or self._background_worker.done():
            self._background_worker = asyncio.create_task(self._dispatch_background())

    async def stop(self) -> None:
        """Stop the background worker and drain the queue."""
        if self._background_worker and not self._background_worker.done():
            await self._background_queue.put((type("_Sentinel", (), {}), None))  # noqa: PLC0117
            self._background_worker.cancel()
            try:
                await self._background_worker
            except asyncio.CancelledError:
                pass
