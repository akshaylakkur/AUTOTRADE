"""Comprehensive pytest suite for auton/reflexes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from auton.core.constants import RISK_LIMITS
from auton.core.event_bus import EventBus
from auton.core.events import EmergencyLiquidate, Hibernate
from auton.reflexes.api_health import APIHealthMonitor
from auton.reflexes.circuit_breakers import CircuitBreakers
from auton.reflexes.dataclasses import ApiDown, ApiRecovered, LiquidationOrder, PositionSize
from auton.reflexes.emergency_liquidator import EmergencyLiquidator
from auton.reflexes.position_sizer import PositionSizer
from auton.reflexes.stop_loss import StopLossEngine


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


# ---------------------------------------------------------------------------
# StopLossEngine tests
# ---------------------------------------------------------------------------


class TestStopLossEngine:
    @pytest.mark.asyncio
    async def test_no_trigger_when_price_above_stop(self, bus: EventBus) -> None:
        engine = StopLossEngine(bus)
        engine.add_position("BTC", 50000, 1.0, 0.05)
        await engine.check_stop_loss({"BTC": 49000})
        assert engine.has_position("BTC")

    @pytest.mark.asyncio
    async def test_trigger_stop_loss(self, bus: EventBus) -> None:
        received: list[EmergencyLiquidate] = []

        async def handler(event: EmergencyLiquidate) -> None:
            received.append(event)

        await bus.subscribe(EmergencyLiquidate, handler)
        engine = StopLossEngine(bus)
        engine.add_position("BTC", 50000, 1.0, 0.05)
        await engine.check_stop_loss({"BTC": 47499})
        await asyncio.sleep(0.05)

        assert not engine.has_position("BTC")
        assert len(received) == 1
        assert received[0].reason == "stop_loss_triggered"
        assert received[0].positions == [{"symbol": "BTC", "quantity": 1.0}]

    @pytest.mark.asyncio
    async def test_trigger_exact_stop_price(self, bus: EventBus) -> None:
        received: list[EmergencyLiquidate] = []

        async def handler(event: EmergencyLiquidate) -> None:
            received.append(event)

        await bus.subscribe(EmergencyLiquidate, handler)
        engine = StopLossEngine(bus)
        engine.add_position("BTC", 50000, 1.0, 0.05)
        await engine.check_stop_loss({"BTC": 47500})
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert not engine.has_position("BTC")

    @pytest.mark.asyncio
    async def test_trailing_stop_adjusts_up(self, bus: EventBus) -> None:
        received: list[EmergencyLiquidate] = []

        async def handler(event: EmergencyLiquidate) -> None:
            received.append(event)

        await bus.subscribe(EmergencyLiquidate, handler)
        engine = StopLossEngine(bus)
        engine.add_position("BTC", 50000, 1.0, 0.05, trailing=True)

        # Price rises to 60000 -> stop moves to 57000
        await engine.check_stop_loss({"BTC": 60000})
        assert engine.has_position("BTC")
        rule = engine.get_rule("BTC")
        assert rule is not None
        assert rule.highest_price == Decimal("60000")

        # Price drops to 58000 -> still above 57000, no trigger
        await engine.check_stop_loss({"BTC": 58000})
        assert engine.has_position("BTC")

        # Price drops to 56999 -> trigger
        await engine.check_stop_loss({"BTC": 56999})
        await asyncio.sleep(0.05)
        assert len(received) == 1
        assert not engine.has_position("BTC")

    @pytest.mark.asyncio
    async def test_multiple_symbols(self, bus: EventBus) -> None:
        received: list[EmergencyLiquidate] = []

        async def handler(event: EmergencyLiquidate) -> None:
            received.append(event)

        await bus.subscribe(EmergencyLiquidate, handler)
        engine = StopLossEngine(bus)
        engine.add_position("BTC", 50000, 1.0, 0.05)
        engine.add_position("ETH", 3000, 10.0, 0.10)

        await engine.check_stop_loss({"BTC": 47499, "ETH": 2700})
        await asyncio.sleep(0.05)

        assert len(received) == 2
        assert not engine.has_position("BTC")
        assert not engine.has_position("ETH")

    @pytest.mark.asyncio
    async def test_ignores_unknown_symbols(self, bus: EventBus) -> None:
        engine = StopLossEngine(bus)
        engine.add_position("BTC", 50000, 1.0, 0.05)
        await engine.check_stop_loss({"SOL": 20})
        assert engine.has_position("BTC")


# ---------------------------------------------------------------------------
# EmergencyLiquidator tests
# ---------------------------------------------------------------------------


class TestEmergencyLiquidator:
    @pytest.mark.asyncio
    async def test_liquidate_symbol_emits_order(self, bus: EventBus) -> None:
        received: list[LiquidationOrder] = []

        async def handler(event: LiquidationOrder) -> None:
            received.append(event)

        await bus.subscribe(LiquidationOrder, handler)
        liquidator = EmergencyLiquidator(bus)
        await liquidator.start()
        await liquidator.liquidate_symbol("BTC", "manual")
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0].symbol == "BTC"
        assert received[0].reason == "manual"
        assert liquidator.is_liquidated("BTC")

    @pytest.mark.asyncio
    async def test_listens_for_emergency_liquidate(self, bus: EventBus) -> None:
        received: list[LiquidationOrder] = []

        async def handler(event: LiquidationOrder) -> None:
            received.append(event)

        await bus.subscribe(LiquidationOrder, handler)
        liquidator = EmergencyLiquidator(bus)
        await liquidator.start()

        await bus.publish(
            EmergencyLiquidate,
            EmergencyLiquidate(
                reason="stop_loss_triggered",
                positions=[{"symbol": "BTC", "quantity": 1.0}],
            ),
        )
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0].symbol == "BTC"
        assert liquidator.is_liquidated("BTC")

    @pytest.mark.asyncio
    async def test_liquidate_all_positions(self, bus: EventBus) -> None:
        received: list[LiquidationOrder] = []

        async def handler(event: LiquidationOrder) -> None:
            received.append(event)

        await bus.subscribe(LiquidationOrder, handler)
        liquidator = EmergencyLiquidator(bus)
        await liquidator.liquidate_symbol("BTC", "manual")
        await liquidator.liquidate_symbol("ETH", "manual")
        await liquidator.liquidate_all_positions("panic")
        await asyncio.sleep(0.05)

        assert len(received) == 4  # 2 initial + 2 all-positions
        assert liquidator.liquidation_reason("BTC") == "panic"
        assert liquidator.liquidation_reason("ETH") == "panic"

    @pytest.mark.asyncio
    async def test_check_survival_trigger(self, bus: EventBus) -> None:
        received: list[LiquidationOrder] = []

        async def handler(event: LiquidationOrder) -> None:
            received.append(event)

        await bus.subscribe(LiquidationOrder, handler)
        liquidator = EmergencyLiquidator(bus)
        await liquidator.check_survival(
            balance=Decimal("90"),
            survival_threshold=Decimal("100"),
            positions=["BTC", "ETH"],
        )
        await asyncio.sleep(0.05)

        assert len(received) == 2
        assert liquidator.is_liquidated("BTC")
        assert liquidator.is_liquidated("ETH")

    @pytest.mark.asyncio
    async def test_check_survival_no_trigger(self, bus: EventBus) -> None:
        received: list[LiquidationOrder] = []

        async def handler(event: LiquidationOrder) -> None:
            received.append(event)

        await bus.subscribe(LiquidationOrder, handler)
        liquidator = EmergencyLiquidator(bus)
        await liquidator.check_survival(
            balance=Decimal("110"),
            survival_threshold=Decimal("100"),
            positions=["BTC", "ETH"],
        )
        await asyncio.sleep(0.05)

        assert len(received) == 0


# ---------------------------------------------------------------------------
# APIHealthMonitor tests
# ---------------------------------------------------------------------------


class TestAPIHealthMonitor:
    @pytest.mark.asyncio
    async def test_register_and_default_healthy(self, bus: EventBus) -> None:
        monitor = APIHealthMonitor(bus)
        monitor.register_api("binance", "https://api.binance.com/health", 30)
        assert monitor.is_healthy("binance")

    @pytest.mark.asyncio
    async def test_check_health_healthy(self, bus: EventBus) -> None:
        async def mock_client(url: str) -> int:
            return 200

        monitor = APIHealthMonitor(bus, http_client=mock_client)
        monitor.register_api("binance", "https://api.binance.com/health", 30)
        await monitor.check_health()
        assert monitor.is_healthy("binance")
        status = monitor.get_status("binance")
        assert status is not None
        assert status.healthy
        assert status.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_check_health_unhealthy_emits_api_down(self, bus: EventBus) -> None:
        received: list[ApiDown] = []

        async def handler(event: ApiDown) -> None:
            received.append(event)

        await bus.subscribe(ApiDown, handler)

        async def mock_client(url: str) -> int:
            return 500

        monitor = APIHealthMonitor(bus, http_client=mock_client)
        monitor.register_api("binance", "https://api.binance.com/health", 30)
        await monitor.check_health()
        await asyncio.sleep(0.05)

        assert not monitor.is_healthy("binance")
        assert len(received) == 1
        assert received[0].name == "binance"

    @pytest.mark.asyncio
    async def test_check_health_recovered_emits_api_recovered(self, bus: EventBus) -> None:
        received_down: list[ApiDown] = []
        received_up: list[ApiRecovered] = []

        async def on_down(event: ApiDown) -> None:
            received_down.append(event)

        async def on_up(event: ApiRecovered) -> None:
            received_up.append(event)

        await bus.subscribe(ApiDown, on_down)
        await bus.subscribe(ApiRecovered, on_up)

        call_count = 0

        async def mock_client(url: str) -> int:
            nonlocal call_count
            call_count += 1
            return 500 if call_count == 1 else 200

        monitor = APIHealthMonitor(bus, http_client=mock_client)
        monitor.register_api("binance", "https://api.binance.com/health", 30)

        await monitor.check_health()
        await asyncio.sleep(0.05)
        assert len(received_down) == 1

        await monitor.check_health()
        await asyncio.sleep(0.05)
        assert len(received_up) == 1
        assert received_up[0].name == "binance"
        assert monitor.is_healthy("binance")

    @pytest.mark.asyncio
    async def test_check_health_exception_emits_api_down(self, bus: EventBus) -> None:
        received: list[ApiDown] = []

        async def handler(event: ApiDown) -> None:
            received.append(event)

        await bus.subscribe(ApiDown, handler)

        async def mock_client(url: str) -> int:
            raise ConnectionError("timeout")

        monitor = APIHealthMonitor(bus, http_client=mock_client)
        monitor.register_api("binance", "https://api.binance.com/health", 30)
        await monitor.check_health()
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0].name == "binance"

    @pytest.mark.asyncio
    async def test_get_failover_when_unhealthy(self, bus: EventBus) -> None:
        async def mock_client(url: str) -> int:
            return 500

        monitor = APIHealthMonitor(bus, http_client=mock_client)
        monitor.register_api(
            "binance",
            "https://api.binance.com/health",
            30,
            failover_endpoint="https://api2.binance.com/health",
        )
        await monitor.check_health()
        assert monitor.get_failover("binance") == "https://api2.binance.com/health"

    @pytest.mark.asyncio
    async def test_get_failover_when_healthy(self, bus: EventBus) -> None:
        async def mock_client(url: str) -> int:
            return 200

        monitor = APIHealthMonitor(bus, http_client=mock_client)
        monitor.register_api(
            "binance",
            "https://api.binance.com/health",
            30,
            failover_endpoint="https://api2.binance.com/health",
        )
        await monitor.check_health()
        assert monitor.get_failover("binance") is None

    @pytest.mark.asyncio
    async def test_no_http_client_defaults_healthy(self, bus: EventBus) -> None:
        monitor = APIHealthMonitor(bus)
        monitor.register_api("binance", "https://api.binance.com/health", 30)
        await monitor.check_health()
        assert monitor.is_healthy("binance")


# ---------------------------------------------------------------------------
# PositionSizer tests
# ---------------------------------------------------------------------------


class TestPositionSizer:
    def test_tier_0_kelly_within_cap(self) -> None:
        sizer = PositionSizer()
        result = sizer.calculate_position_size(
            balance=Decimal("1000"),
            edge=Decimal("0.01"),
            odds=Decimal("1.0"),
            tier=0,
        )
        # Kelly = 0.01 / 1.0 = 0.01, tier cap = 0.02 -> fraction = 0.01
        # effective_balance = 1000 * 0.9 = 900
        # quantity = 900 * 0.01 = 9
        assert result.quantity == Decimal("9")
        assert result.max_loss == Decimal("9")

    def test_tier_0_kelly_capped(self) -> None:
        sizer = PositionSizer()
        result = sizer.calculate_position_size(
            balance=Decimal("1000"),
            edge=Decimal("0.05"),
            odds=Decimal("1.0"),
            tier=0,
        )
        # Kelly = 0.05, tier cap = 0.02 -> fraction = 0.02
        # quantity = 900 * 0.02 = 18
        assert result.quantity == Decimal("18")

    def test_tier_3_lower_cap(self) -> None:
        sizer = PositionSizer()
        result = sizer.calculate_position_size(
            balance=Decimal("1000"),
            edge=Decimal("0.05"),
            odds=Decimal("1.0"),
            tier=3,
        )
        # Kelly = 0.05, tier cap = 0.01 -> fraction = 0.01
        # quantity = 900 * 0.01 = 9
        assert result.quantity == Decimal("9")

    def test_survival_reserve_applied(self) -> None:
        sizer = PositionSizer()
        result = sizer.calculate_position_size(
            balance=Decimal("100"),
            edge=Decimal("0.02"),
            odds=Decimal("1.0"),
            tier=0,
        )
        # effective_balance = 90, fraction = 0.02 -> 1.8
        assert result.quantity == Decimal("1.8")
        assert result.max_loss == Decimal("1.8")

    def test_negative_edge_returns_zero(self) -> None:
        sizer = PositionSizer()
        result = sizer.calculate_position_size(
            balance=Decimal("1000"),
            edge=Decimal("-0.01"),
            odds=Decimal("1.0"),
            tier=0,
        )
        assert result.quantity == Decimal("0")
        assert result.max_loss == Decimal("0")

    def test_zero_odds_raises(self) -> None:
        sizer = PositionSizer()
        with pytest.raises(ValueError, match="odds must be positive"):
            sizer.calculate_position_size(
                balance=Decimal("1000"),
                edge=Decimal("0.01"),
                odds=Decimal("0"),
                tier=0,
            )

    def test_negative_odds_raises(self) -> None:
        sizer = PositionSizer()
        with pytest.raises(ValueError, match="odds must be positive"):
            sizer.calculate_position_size(
                balance=Decimal("1000"),
                edge=Decimal("0.01"),
                odds=Decimal("-1.0"),
                tier=0,
            )

    def test_returns_position_size_dataclass(self) -> None:
        sizer = PositionSizer()
        result = sizer.calculate_position_size(
            balance=Decimal("1000"),
            edge=Decimal("0.01"),
            odds=Decimal("2.0"),
            tier=1,
        )
        assert isinstance(result, PositionSize)
        assert isinstance(result.quantity, Decimal)
        assert isinstance(result.max_loss, Decimal)


# ---------------------------------------------------------------------------
# CircuitBreakers tests
# ---------------------------------------------------------------------------


class TestCircuitBreakers:
    @pytest.mark.asyncio
    async def test_drawdown_below_threshold_no_hibernate(self, bus: EventBus) -> None:
        received: list[Hibernate] = []

        async def handler(event: Hibernate) -> None:
            received.append(event)

        await bus.subscribe(Hibernate, handler)
        cb = CircuitBreakers(bus)
        await cb.check_drawdown(
            current_balance=Decimal("950"),
            start_of_day_balance=Decimal("1000"),
        )
        await asyncio.sleep(0.05)

        assert len(received) == 0
        assert not cb.is_hibernating()

    @pytest.mark.asyncio
    async def test_drawdown_at_exact_threshold_emits_hibernate(self, bus: EventBus) -> None:
        received: list[Hibernate] = []

        async def handler(event: Hibernate) -> None:
            received.append(event)

        await bus.subscribe(Hibernate, handler)
        cb = CircuitBreakers(bus)
        await cb.check_drawdown(
            current_balance=Decimal("900"),
            start_of_day_balance=Decimal("1000"),
        )
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0].reason == "daily_drawdown_limit"
        assert received[0].duration_seconds == 86400.0
        assert cb.is_hibernating()
        assert cb.was_triggered()

    @pytest.mark.asyncio
    async def test_drawdown_above_threshold_emits_hibernate(self, bus: EventBus) -> None:
        received: list[Hibernate] = []

        async def handler(event: Hibernate) -> None:
            received.append(event)

        await bus.subscribe(Hibernate, handler)
        cb = CircuitBreakers(bus)
        await cb.check_drawdown(
            current_balance=Decimal("850"),
            start_of_day_balance=Decimal("1000"),
        )
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert cb.is_hibernating()

    @pytest.mark.asyncio
    async def test_no_hibernate_when_already_hibernating(self, bus: EventBus) -> None:
        received: list[Hibernate] = []

        async def handler(event: Hibernate) -> None:
            received.append(event)

        await bus.subscribe(Hibernate, handler)
        cb = CircuitBreakers(bus)
        await cb.check_drawdown(
            current_balance=Decimal("850"),
            start_of_day_balance=Decimal("1000"),
        )
        await asyncio.sleep(0.05)
        assert len(received) == 1

        # Second check should not emit another Hibernate
        await cb.check_drawdown(
            current_balance=Decimal("800"),
            start_of_day_balance=Decimal("1000"),
        )
        await asyncio.sleep(0.05)
        assert len(received) == 1

    def test_is_hibernating_expires(self, bus: EventBus) -> None:
        cb = CircuitBreakers(bus)
        # Force a past hibernate timestamp
        cb._hibernate_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert not cb.is_hibernating()
        assert cb._hibernate_until is None

    def test_not_hibernating_initially(self, bus: EventBus) -> None:
        cb = CircuitBreakers(bus)
        assert not cb.is_hibernating()
        assert not cb.was_triggered()

    @pytest.mark.asyncio
    async def test_zero_start_balance_no_crash(self, bus: EventBus) -> None:
        cb = CircuitBreakers(bus)
        await cb.check_drawdown(
            current_balance=Decimal("0"),
            start_of_day_balance=Decimal("0"),
        )
        assert not cb.is_hibernating()
