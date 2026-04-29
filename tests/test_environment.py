"""Tests for the environmental awareness sensor."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auton.core.event_bus import EventBus, Priority
from auton.core.events import EnvironmentalUpdate
from auton.senses.environment import (
    ContextSnapshot,
    EconomicEvent,
    EnvironmentalSensor,
    ImpactLevel,
    MarketSession,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def sensor(event_bus: EventBus) -> EnvironmentalSensor:
    return EnvironmentalSensor(event_bus=event_bus, interval_seconds=0.1)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


def test_economic_event_frozen() -> None:
    evt = EconomicEvent(
        date=datetime.now(timezone.utc),
        name="NFP",
        impact=ImpactLevel.HIGH,
        asset_class="equities",
    )
    with pytest.raises(AttributeError):
        evt.name = "CPI"


def test_context_snapshot_frozen() -> None:
    snap = ContextSnapshot(
        current_time=datetime.now(timezone.utc),
        timezone="UTC",
        utc_offset=0.0,
        market_session={},
        upcoming_events=[],
        system_load={},
        network_health={},
    )
    with pytest.raises(AttributeError):
        snap.timezone = "EST"


# ---------------------------------------------------------------------------
# Market hours
# ---------------------------------------------------------------------------


def test_market_session_enum() -> None:
    assert MarketSession.OPEN == "open"
    assert MarketSession.CLOSED == "closed"
    assert MarketSession.PRE_MARKET == "pre-market"
    assert MarketSession.AFTER_HOURS == "after-hours"
    assert MarketSession.ALWAYS_OPEN == "always-open"


@pytest.mark.asyncio
async def test_sample_structure(sensor: EnvironmentalSensor) -> None:
    snapshot = await sensor.sample()
    assert isinstance(snapshot, ContextSnapshot)
    assert snapshot.current_time.tzinfo is not None
    assert isinstance(snapshot.market_session, dict)
    assert "crypto" in snapshot.market_session
    assert "us_equities" in snapshot.market_session
    assert isinstance(snapshot.upcoming_events, list)
    assert isinstance(snapshot.system_load, dict)
    assert isinstance(snapshot.network_health, dict)


@pytest.mark.asyncio
async def test_crypto_always_open(sensor: EnvironmentalSensor) -> None:
    snapshot = await sensor.sample()
    assert snapshot.market_session["crypto"] == MarketSession.ALWAYS_OPEN


@pytest.mark.asyncio
async def test_economic_calendar_mock_data(sensor: EnvironmentalSensor) -> None:
    snapshot = await sensor.sample()
    assert len(snapshot.upcoming_events) > 0
    for evt in snapshot.upcoming_events:
        assert isinstance(evt, EconomicEvent)
        assert evt.date.tzinfo is not None
        assert evt.impact in {ImpactLevel.HIGH, ImpactLevel.MEDIUM, ImpactLevel.LOW}


# ---------------------------------------------------------------------------
# Background loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_stop(sensor: EnvironmentalSensor) -> None:
    await sensor.start()
    assert sensor._running
    await sensor.stop()
    assert not sensor._running


@pytest.mark.asyncio
async def test_publish_on_event_bus(event_bus: EventBus, sensor: EnvironmentalSensor) -> None:
    received: list[EnvironmentalUpdate] = []

    async def collect(evt: EnvironmentalUpdate) -> None:
        received.append(evt)

    await event_bus.subscribe(EnvironmentalUpdate, collect)
    await event_bus.start()

    # Mock slow IO so the loop fires quickly
    sensor._network_health = AsyncMock(return_value={"mock": True})
    sensor._system_load = AsyncMock(return_value={"cpu_percent": 5.0})

    await sensor.start()
    await asyncio.sleep(0.25)
    await sensor.stop()

    assert len(received) >= 1
    update = received[0]
    assert isinstance(update, EnvironmentalUpdate)
    assert update.timestamp.tzinfo is not None
    assert "crypto" in update.market_hours


# ---------------------------------------------------------------------------
# Network health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_network_health_returns_results(sensor: EnvironmentalSensor) -> None:
    health = await sensor._network_health()
    assert isinstance(health, dict)
    # At least one endpoint should be present or attempted
    assert len(health) > 0


@pytest.mark.asyncio
async def test_network_health_graceful_failure(sensor: EnvironmentalSensor) -> None:
    # Force an unreachable endpoint
    sensor._HEALTH_ENDPOINTS = {"fake": "http://localhost:9/nope"}
    health = await sensor._network_health()
    assert health["fake"]["status"] == "unreachable"
    assert "error" in health["fake"]


# ---------------------------------------------------------------------------
# System load
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_load_returns_dict(sensor: EnvironmentalSensor) -> None:
    load = await sensor._system_load()
    assert isinstance(load, dict)
    # Keys may or may not be present depending on platform / psutil
    for key in ("cpu_percent", "memory_percent"):
        if key in load:
            assert isinstance(load[key], float)


# ---------------------------------------------------------------------------
# Economic calendar hook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_economic_calendar_hook(sensor: EnvironmentalSensor) -> None:
    events = await sensor.fetch_economic_calendar(days_ahead=7)
    assert isinstance(events, list)
    assert len(events) > 0
    for evt in events:
        assert isinstance(evt, EconomicEvent)
