"""SimulationRecorder — captures every decision and outcome in a run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable


@dataclass
class RecordedEvent:
    """A single recorded decision or outcome."""

    timestamp: datetime
    category: str
    action: str
    payload: dict[str, Any] = field(default_factory=dict)


class SimulationRecorder:
    """Append-only log of simulation events.

    Each event has a *category* (e.g. ``decision``, ``trade``,
    ``outcome``), an *action* string, and an arbitrary payload.
    """

    def __init__(self) -> None:
        self._events: list[RecordedEvent] = []

    # ------------------------------------------------------------------ #
    # Recording
    # ------------------------------------------------------------------ #
    def record(
        self,
        timestamp: datetime,
        category: str,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> RecordedEvent:
        """Append an event to the log and return it."""
        event = RecordedEvent(
            timestamp=timestamp,
            category=category,
            action=action,
            payload=payload or {},
        )
        self._events.append(event)
        return event

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def get_events(
        self,
        category: str | None = None,
        action: str | None = None,
    ) -> Iterable[RecordedEvent]:
        """Yield events, optionally filtered."""
        for ev in self._events:
            if category is not None and ev.category != category:
                continue
            if action is not None and ev.action != action:
                continue
            yield ev

    def get_event_count(
        self,
        category: str | None = None,
        action: str | None = None,
    ) -> int:
        """Return the number of matching events."""
        return sum(1 for _ in self.get_events(category, action))

    def get_last_event(self) -> RecordedEvent | None:
        """Return the most recently recorded event, if any."""
        return self._events[-1] if self._events else None

    # ------------------------------------------------------------------ #
    # Reset
    # ------------------------------------------------------------------ #
    def reset(self) -> None:
        """Clear all recorded events."""
        self._events.clear()
