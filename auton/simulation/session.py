"""SimulationSession — manages the lifecycle of a single simulation run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from auton.simulation.analyzer import SimulationAnalyzer, SimulationMetrics
from auton.simulation.clock import SimulationClock
from auton.simulation.recorder import SimulationRecorder
from auton.simulation.wallet import SimulatedWallet


@dataclass
class SimulationConfig:
    """User-supplied parameters for a simulation run."""

    name: str = "unnamed"
    initial_balance: float = 50.0
    start_time: datetime = field(
        default_factory=lambda: datetime(2024, 1, 1, tzinfo=timezone.utc)
    )
    tick_size: timedelta = field(default_factory=lambda: timedelta(seconds=1))
    metadata: dict[str, Any] = field(default_factory=dict)


class SimulationSession:
    """Orchestrates one simulation run.

    Holds the wallet, clock, recorder, and analyzer.  Provides
    convenience methods for advancing time and recording events.
    """

    def __init__(self, config: SimulationConfig | None = None) -> None:
        self.config = config or SimulationConfig()
        self.wallet = SimulatedWallet(initial_balance=self.config.initial_balance)
        self.clock = SimulationClock(start=self.config.start_time)
        self.clock.set_tick_size(self.config.tick_size)
        self.recorder = SimulationRecorder()
        self.analyzer = SimulationAnalyzer()
        self._running = False
        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Mark the session as running and record the start event."""
        self._running = True
        self._started_at = datetime.now(timezone.utc)
        self.recorder.record(
            timestamp=self.clock.now(),
            category="session",
            action="start",
            payload={"config": self._config_dict()},
        )

    def stop(self) -> None:
        """Mark the session as stopped and record the stop event."""
        self._running = False
        self._stopped_at = datetime.now(timezone.utc)
        self.recorder.record(
            timestamp=self.clock.now(),
            category="session",
            action="stop",
            payload={
                "final_balance": self.wallet.get_balance(),
                "transaction_count": self.wallet.get_transaction_count(),
            },
        )

    def reset(self) -> None:
        """Reset all subsystems to their initial state."""
        self.wallet.reset(initial_balance=self.config.initial_balance)
        self.clock.reset(start=self.config.start_time)
        self.clock.set_tick_size(self.config.tick_size)
        self.recorder.reset()
        self.analyzer.reset()
        self._running = False
        self._started_at = None
        self._stopped_at = None

    def is_running(self) -> bool:
        """Return whether the session is currently active."""
        return self._running

    # ------------------------------------------------------------------ #
    # Convenience helpers
    # ------------------------------------------------------------------ #
    def advance(self, delta: timedelta | None = None) -> datetime:
        """Advance the simulation clock by *delta* (or default tick size)."""
        return self.clock.advance(delta)

    def record(
        self,
        category: str,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Record an event at the current simulated time."""
        self.recorder.record(
            timestamp=self.clock.now(),
            category=category,
            action=action,
            payload=payload,
        )

    def analyze(self) -> SimulationMetrics:
        """Return performance metrics for the current run."""
        return self.analyzer.compute()

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #
    def _config_dict(self) -> dict[str, Any]:
        return {
            "name": self.config.name,
            "initial_balance": self.config.initial_balance,
            "start_time": self.config.start_time.isoformat(),
            "tick_size_seconds": self.config.tick_size.total_seconds(),
            "metadata": self.config.metadata,
        }
