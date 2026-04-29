"""SimulationClock — deterministic time control for backtests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Final


class SimulationClock:
    """Replaces real time with a simulated timeline.

    Supports fast-forward, pause, and rewind so that backtests can
    run deterministically at any speed.
    """

    def __init__(self, start: datetime | None = None) -> None:
        self._now: datetime = start or datetime.now(timezone.utc)
        self._paused: bool = False
        self._tick_size: timedelta = timedelta(seconds=1)

    # ------------------------------------------------------------------ #
    # Time queries
    # ------------------------------------------------------------------ #
    def now(self) -> datetime:
        """Return the current simulated time."""
        return self._now

    def isoformat(self) -> str:
        """Return the current simulated time as an ISO-8601 string."""
        return self._now.isoformat()

    # ------------------------------------------------------------------ #
    # Flow control
    # ------------------------------------------------------------------ #
    def advance(self, delta: timedelta | None = None) -> datetime:
        """Move time forward by *delta* (default tick size).

        Raises:
            RuntimeError: If the clock is paused.
        """
        if self._paused:
            raise RuntimeError("Clock is paused")
        step = delta if delta is not None else self._tick_size
        self._now += step
        return self._now

    def fast_forward(self, delta: timedelta) -> datetime:
        """Jump forward by *delta*, bypassing pause state."""
        self._now += delta
        return self._now

    def rewind(self, delta: timedelta) -> datetime:
        """Jump backward by *delta*."""
        self._now -= delta
        return self._now

    def set_time(self, t: datetime) -> datetime:
        """Set the clock to an absolute point in time."""
        self._now = t
        return self._now

    # ------------------------------------------------------------------ #
    # Pause / resume
    # ------------------------------------------------------------------ #
    def pause(self) -> None:
        """Freeze the clock so :meth:`advance` raises."""
        self._paused = True

    def resume(self) -> None:
        """Unfreeze the clock."""
        self._paused = False

    def is_paused(self) -> bool:
        """Return whether the clock is currently paused."""
        return self._paused

    # ------------------------------------------------------------------ #
    # Tick size
    # ------------------------------------------------------------------ #
    def set_tick_size(self, delta: timedelta) -> None:
        """Change the default step used by :meth:`advance`."""
        if delta.total_seconds() <= 0:
            raise ValueError("tick size must be positive")
        self._tick_size = delta

    def get_tick_size(self) -> timedelta:
        """Return the current default tick size."""
        return self._tick_size

    # ------------------------------------------------------------------ #
    # Reset
    # ------------------------------------------------------------------ #
    def reset(self, start: datetime | None = None) -> datetime:
        """Reset the clock to *start* (or now) and clear pause state."""
        self._now = start or datetime.now(timezone.utc)
        self._paused = False
        self._tick_size = timedelta(seconds=1)
        return self._now
