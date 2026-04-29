"""Environmental awareness sensor for Project ÆON.

Provides temporal, market-hours, economic-calendar, system-load, and
network-health context so the cortex can ground every decision in
*when* and *under what conditions* it is being made.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import httpx

from auton.core.event_bus import EventBus, Priority
from auton.core.events import EnvironmentalUpdate

logger = logging.getLogger(__name__)


class MarketSession(str, Enum):
    """Discrete market-session states."""

    PRE_MARKET = "pre-market"
    OPEN = "open"
    AFTER_HOURS = "after-hours"
    CLOSED = "closed"
    ALWAYS_OPEN = "always-open"


class ImpactLevel(str, Enum):
    """Impact classification for economic events."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class EconomicEvent:
    """A single item on the economic calendar."""

    date: datetime
    name: str
    impact: ImpactLevel
    asset_class: str  # e.g. "equities", "crypto", "forex"


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    """Complete environmental snapshot at a point in time."""

    current_time: datetime
    timezone: str
    utc_offset: float
    market_session: dict[str, MarketSession]
    upcoming_events: list[EconomicEvent]
    system_load: dict[str, float]
    network_health: dict[str, Any]


class EnvironmentalSensor:
    """Samples environmental context and publishes ``EnvironmentalUpdate`` events.

    The sensor runs a background loop that emits an ``EnvironmentalUpdate``
    on the event bus at a configurable interval.  It can also be sampled
    on-demand via ``sample()`` for inclusion in decision proposals.
    """

    # Endpoints used for network-health latency checks
    _HEALTH_ENDPOINTS: dict[str, str] = {
        "binance": "https://api.binance.com/api/v3/ping",
        "coinbase": "https://api.exchange.coinbase.com/time",
        "google": "https://www.google.com",
    }

    def __init__(
        self,
        event_bus: EventBus | None = None,
        interval_seconds: float = 60.0,
        tz_name: str = "America/New_York",
    ) -> None:
        self._event_bus = event_bus
        self._interval = interval_seconds
        self._tz_name = tz_name
        self._running = False
        self._task: asyncio.Task[Any] | None = None
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def sample(self) -> ContextSnapshot:
        """Capture a single environmental snapshot.

        This is a synchronous-feeling async call: it gathers all metrics
        concurrently and returns a fully populated ``ContextSnapshot``.
        """
        now_utc = datetime.now(timezone.utc)
        tz, utc_offset = self._resolve_timezone(now_utc)

        market_session, _ = self._market_status(now_utc)
        upcoming = self._economic_calendar(now_utc)
        load = await self._system_load()
        health = await self._network_health()

        return ContextSnapshot(
            current_time=now_utc,
            timezone=tz,
            utc_offset=utc_offset,
            market_session=market_session,
            upcoming_events=upcoming,
            system_load=load,
            network_health=health,
        )

    async def start(self) -> None:
        """Begin the background sampling loop."""
        if self._running:
            return
        self._running = True
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._task = asyncio.create_task(self._loop())
        logger.info("EnvironmentalSensor started (interval=%.0fs)", self._interval)

    async def stop(self) -> None:
        """Stop the background loop and release resources."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("EnvironmentalSensor stopped")

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        while self._running:
            try:
                snapshot = await self.sample()
                await self._publish(snapshot)
            except Exception:
                logger.exception("EnvironmentalSensor loop error")
            await asyncio.sleep(self._interval)

    async def _publish(self, snapshot: ContextSnapshot) -> None:
        if self._event_bus is None:
            return
        event = EnvironmentalUpdate(
            timestamp=snapshot.current_time,
            timezone=snapshot.timezone,
            utc_offset=snapshot.utc_offset,
            market_hours={
                k: (v == MarketSession.OPEN or v == MarketSession.ALWAYS_OPEN)
                for k, v in snapshot.market_session.items()
            },
            economic_calendar=[
                {
                    "date": e.date.isoformat(),
                    "name": e.name,
                    "impact": e.impact.value,
                    "asset_class": e.asset_class,
                }
                for e in snapshot.upcoming_events
            ],
            system_load=snapshot.system_load,
            network_health=snapshot.network_health,
        )
        await self._event_bus.publish(EnvironmentalUpdate, event, priority=Priority.NORMAL)

    # ------------------------------------------------------------------
    # Time & market hours
    # ------------------------------------------------------------------

    def _resolve_timezone(self, now: datetime) -> tuple[str, float]:
        """Return the canonical timezone name and UTC offset in hours."""
        try:
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(self._tz_name)
            local = now.astimezone(tz)
            offset = local.utcoffset()
            hours = offset.total_seconds() / 3600.0 if offset else 0.0
            return str(tz), hours
        except Exception:
            return "UTC", 0.0

    def _market_status(
        self, now: datetime
    ) -> tuple[dict[str, MarketSession], dict[str, bool]]:
        """Determine the current market session for each asset class.

        Returns:
            A tuple of (session_map, open_bool_map).
        """
        sessions: dict[str, MarketSession] = {}
        hours: dict[str, bool] = {}

        # US Equities — 09:30-16:00 ET, Mon-Fri
        try:
            from zoneinfo import ZoneInfo

            et = ZoneInfo("America/New_York")
            local = now.astimezone(et)
            weekday = local.weekday()
            if weekday >= 5:
                sessions["us_equities"] = MarketSession.CLOSED
                hours["us_equities"] = False
            else:
                market_open = local.replace(hour=9, minute=30, second=0, microsecond=0)
                market_close = local.replace(hour=16, minute=0, second=0, microsecond=0)
                if local < market_open:
                    sessions["us_equities"] = MarketSession.PRE_MARKET
                    hours["us_equities"] = False
                elif market_open <= local < market_close:
                    sessions["us_equities"] = MarketSession.OPEN
                    hours["us_equities"] = True
                else:
                    sessions["us_equities"] = MarketSession.AFTER_HOURS
                    hours["us_equities"] = False
        except Exception:
            sessions["us_equities"] = MarketSession.CLOSED
            hours["us_equities"] = False

        # Crypto — 24/7
        sessions["crypto"] = MarketSession.ALWAYS_OPEN
        hours["crypto"] = True

        # Forex sessions (UTC-based)
        # Tokyo 00:00-09:00 UTC
        # London 08:00-17:00 UTC
        # New York 13:00-22:00 UTC
        utc_hour = now.hour
        forex_open = False
        if 0 <= utc_hour < 9:
            sessions["forex_tokyo"] = MarketSession.OPEN
            forex_open = True
        else:
            sessions["forex_tokyo"] = MarketSession.CLOSED

        if 8 <= utc_hour < 17:
            sessions["forex_london"] = MarketSession.OPEN
            forex_open = True
        else:
            sessions["forex_london"] = MarketSession.CLOSED

        if 13 <= utc_hour < 22:
            sessions["forex_ny"] = MarketSession.OPEN
            forex_open = True
        else:
            sessions["forex_ny"] = MarketSession.CLOSED

        hours["forex"] = forex_open

        return sessions, hours

    # ------------------------------------------------------------------
    # Economic calendar (placeholder — real fetch can be added later)
    # ------------------------------------------------------------------

    def _economic_calendar(self, now: datetime) -> list[EconomicEvent]:
        """Return a list of upcoming economic events.

        Currently returns mock data.  In production this would query an
        external calendar API (e.g. ForexFactory, TradingEconomics).
        """
        # Placeholder events relative to *now*
        return [
            EconomicEvent(
                date=now + timedelta(days=1),
                name="Non-Farm Payrolls",
                impact=ImpactLevel.HIGH,
                asset_class="equities",
            ),
            EconomicEvent(
                date=now + timedelta(days=3),
                name="FOMC Statement",
                impact=ImpactLevel.HIGH,
                asset_class="equities",
            ),
            EconomicEvent(
                date=now + timedelta(days=7),
                name="CPI Release",
                impact=ImpactLevel.HIGH,
                asset_class="equities",
            ),
            EconomicEvent(
                date=now + timedelta(days=2),
                name="ECB Interest Rate Decision",
                impact=ImpactLevel.MEDIUM,
                asset_class="forex",
            ),
        ]

    async def fetch_economic_calendar(self, days_ahead: int = 7) -> list[EconomicEvent]:
        """Async hook for fetching live economic-calendar data.

        Subclasses or future integrations can override this to query a
        real provider.  The default falls back to the mock placeholder.
        """
        now = datetime.now(timezone.utc)
        return self._economic_calendar(now)

    # ------------------------------------------------------------------
    # System load
    # ------------------------------------------------------------------

    async def _system_load(self) -> dict[str, float]:
        """Return CPU and memory utilisation percentages.

        Prefers ``psutil`` when available; falls back to basic OS probes.
        """
        try:
            import psutil

            return {
                "cpu_percent": psutil.cpu_percent(interval=None),
                "memory_percent": psutil.virtual_memory().percent,
            }
        except Exception:
            pass

        # Fallback — try to read /proc on Linux
        load: dict[str, float] = {"cpu_percent": -1.0, "memory_percent": -1.0}
        try:
            if platform.system() == "Linux":
                with open("/proc/loadavg") as f:
                    avg1 = float(f.read().split()[0])
                    # Rough heuristic: loadavg / cpu_count as pseudo-utilisation
                    cpus = os.cpu_count() or 1
                    load["cpu_percent"] = min(100.0, (avg1 / cpus) * 100.0)

                mem_info: dict[str, int] = {}
                with open("/proc/meminfo") as f:
                    for line in f:
                        if ":" in line:
                            key, val = line.split(":", 1)
                            mem_info[key.strip()] = int(val.strip().split()[0])
                total = mem_info.get("MemTotal", 1)
                available = mem_info.get("MemAvailable", total)
                load["memory_percent"] = ((total - available) / total) * 100.0
        except Exception:
            pass

        return load

    # ------------------------------------------------------------------
    # Network health
    # ------------------------------------------------------------------

    async def _network_health(self) -> dict[str, Any]:
        """Measure latency to key external APIs.

        Returns a dict keyed by service name with ``latency_ms`` and
        ``status`` (``reachable`` / ``unreachable`` / ``error``).
        """
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))

        results: dict[str, Any] = {}
        for name, url in self._HEALTH_ENDPOINTS.items():
            start = time.perf_counter()
            try:
                resp = await self._client.get(url)
                latency = (time.perf_counter() - start) * 1000.0
                results[name] = {
                    "latency_ms": round(latency, 2),
                    "status": "reachable" if resp.status_code < 500 else "degraded",
                    "status_code": resp.status_code,
                }
            except Exception as exc:
                results[name] = {
                    "latency_ms": -1.0,
                    "status": "unreachable",
                    "error": str(exc),
                }
        return results
