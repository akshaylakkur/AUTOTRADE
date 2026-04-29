"""Comprehensive pytest suite for auton/core."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from auton.core.config import Capability, TierGate
from auton.core.constants import (
    DEFAULT_MAX_DAILY_DRAWDOWN,
    DEFAULT_SURVIVAL_RESERVE_PCT,
    RISK_LIMITS,
    SEED_BALANCE,
    TIER_COMPUTE_BUDGETS,
    TIER_THRESHOLDS,
)
from auton.core.event_bus import EventBus
from auton.core.events import (
    BalanceChanged,
    CostIncurred,
    DataReceived,
    EmergencyLiquidate,
    Hibernate,
    ReflexTriggered,
    Shutdown,
    TierChanged,
    TradeSignal,
)
from auton.core.state_machine import State, StateMachine


# ---------------------------------------------------------------------------
# Constants tests
# ---------------------------------------------------------------------------


class TestConstants:
    def test_tier_thresholds(self) -> None:
        assert TIER_THRESHOLDS == {
            0: 50.0,
            1: 100.0,
            2: 500.0,
            3: 2500.0,
            4: 10000.0,
        }

    def test_tier_compute_budgets(self) -> None:
        assert TIER_COMPUTE_BUDGETS == {
            0: 0.50,
            1: 1.00,
            2: 5.00,
            3: 20.00,
            4: 100.00,
        }

    def test_risk_limits_structure(self) -> None:
        for tier in range(5):
            limits = RISK_LIMITS[tier]
            assert "max_position_pct" in limits
            assert "max_leverage" in limits
            assert "max_daily_trades" in limits
            assert "survival_reserve_pct" in limits

    def test_defaults(self) -> None:
        assert DEFAULT_SURVIVAL_RESERVE_PCT == 0.10
        assert DEFAULT_MAX_DAILY_DRAWDOWN == 0.10
        assert SEED_BALANCE == 50.00


# ---------------------------------------------------------------------------
# Event dataclass tests
# ---------------------------------------------------------------------------


class TestEvents:
    def test_balance_changed(self) -> None:
        event = BalanceChanged(old_balance=100.0, new_balance=110.0, reason="trade")
        assert event.old_balance == 100.0
        assert event.new_balance == 110.0
        assert event.reason == "trade"
        assert isinstance(event.timestamp, datetime)

    def test_tier_changed(self) -> None:
        event = TierChanged(old_tier=0, new_tier=1, balance=100.0)
        assert event.old_tier == 0
        assert event.new_tier == 1
        assert event.balance == 100.0

    def test_trade_signal(self) -> None:
        event = TradeSignal(symbol="BTC", side="BUY", quantity=0.1, price=50000.0)
        assert event.symbol == "BTC"
        assert event.side == "BUY"
        assert event.quantity == 0.1
        assert event.price == 50000.0

    def test_cost_incurred(self) -> None:
        event = CostIncurred(amount=0.05, category="inference", description="GPT-4 call")
        assert event.amount == 0.05
        assert event.category == "inference"
        assert event.description == "GPT-4 call"

    def test_emergency_liquidate(self) -> None:
        event = EmergencyLiquidate(reason="drawdown", positions=[{"sym": "BTC"}])
        assert event.reason == "drawdown"
        assert event.positions == [{"sym": "BTC"}]

    def test_hibernate(self) -> None:
        event = Hibernate(reason="daily drawdown", duration_seconds=86400.0)
        assert event.reason == "daily drawdown"
        assert event.duration_seconds == 86400.0

    def test_shutdown(self) -> None:
        event = Shutdown(reason="zero balance", final_balance=0.0)
        assert event.reason == "zero balance"
        assert event.final_balance == 0.0

    def test_data_received(self) -> None:
        event = DataReceived(source="binance", data_type="ticker", payload={"price": 50000})
        assert event.source == "binance"
        assert event.data_type == "ticker"
        assert event.payload == {"price": 50000}

    def test_reflex_triggered(self) -> None:
        event = ReflexTriggered(reflex_name="stop_loss", payload={"symbol": "ETH"})
        assert event.reflex_name == "stop_loss"
        assert event.payload == {"symbol": "ETH"}

    def test_events_are_frozen(self) -> None:
        event = BalanceChanged(old_balance=0.0, new_balance=1.0)
        with pytest.raises(AttributeError):
            event.old_balance = 2.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# EventBus tests
# ---------------------------------------------------------------------------


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


class TestEventBus:
    @pytest.mark.asyncio
    async def test_subscribe_and_publish(self, bus: EventBus) -> None:
        received: list[BalanceChanged] = []

        async def handler(event: BalanceChanged) -> None:
            received.append(event)

        await bus.subscribe(BalanceChanged, handler)
        event = BalanceChanged(old_balance=50.0, new_balance=60.0)
        await bus.publish(BalanceChanged, event)
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0].new_balance == 60.0

    @pytest.mark.asyncio
    async def test_sync_subscriber(self, bus: EventBus) -> None:
        received: list[BalanceChanged] = []

        def handler(event: BalanceChanged) -> None:
            received.append(event)

        await bus.subscribe(BalanceChanged, handler)
        event = BalanceChanged(old_balance=10.0, new_balance=20.0)
        await bus.publish(BalanceChanged, event)
        await asyncio.sleep(0.05)

        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self, bus: EventBus) -> None:
        count = 0

        async def a(event: BalanceChanged) -> None:
            nonlocal count
            count += 1

        def b(event: BalanceChanged) -> None:
            nonlocal count
            count += 1

        await bus.subscribe(BalanceChanged, a)
        await bus.subscribe(BalanceChanged, b)
        await bus.publish(BalanceChanged, BalanceChanged(old_balance=0.0, new_balance=1.0))
        await asyncio.sleep(0.05)

        assert count == 2

    @pytest.mark.asyncio
    async def test_unsubscribe(self, bus: EventBus) -> None:
        received: list[BalanceChanged] = []

        async def handler(event: BalanceChanged) -> None:
            received.append(event)

        await bus.subscribe(BalanceChanged, handler)
        await bus.unsubscribe(BalanceChanged, handler)
        await bus.publish(BalanceChanged, BalanceChanged(old_balance=0.0, new_balance=1.0))
        await asyncio.sleep(0.05)

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_no_subscribers_no_error(self, bus: EventBus) -> None:
        await bus.publish(BalanceChanged, BalanceChanged(old_balance=0.0, new_balance=1.0))

    @pytest.mark.asyncio
    async def test_subscriber_count(self, bus: EventBus) -> None:
        async def handler(event: BalanceChanged) -> None:
            pass

        assert bus.subscriber_count(BalanceChanged) == 0
        await bus.subscribe(BalanceChanged, handler)
        assert bus.subscriber_count(BalanceChanged) == 1
        await bus.unsubscribe(BalanceChanged, handler)
        assert bus.subscriber_count(BalanceChanged) == 0

    @pytest.mark.asyncio
    async def test_publish_different_event_types(self, bus: EventBus) -> None:
        balance_events: list[BalanceChanged] = []
        tier_events: list[TierChanged] = []

        async def on_balance(event: BalanceChanged) -> None:
            balance_events.append(event)

        async def on_tier(event: TierChanged) -> None:
            tier_events.append(event)

        await bus.subscribe(BalanceChanged, on_balance)
        await bus.subscribe(TierChanged, on_tier)

        await bus.publish(BalanceChanged, BalanceChanged(old_balance=0.0, new_balance=1.0))
        await bus.publish(TierChanged, TierChanged(old_tier=0, new_tier=1, balance=100.0))
        await asyncio.sleep(0.05)

        assert len(balance_events) == 1
        assert len(tier_events) == 1

    @pytest.mark.asyncio
    async def test_async_subscriber_exception_isolated(self, bus: EventBus) -> None:
        received: list[BalanceChanged] = []

        async def bad(event: BalanceChanged) -> None:
            raise RuntimeError("boom")

        async def good(event: BalanceChanged) -> None:
            received.append(event)

        await bus.subscribe(BalanceChanged, bad)
        await bus.subscribe(BalanceChanged, good)
        await bus.publish(BalanceChanged, BalanceChanged(old_balance=0.0, new_balance=1.0))
        await asyncio.sleep(0.05)

        assert len(received) == 1


# ---------------------------------------------------------------------------
# StateMachine tests
# ---------------------------------------------------------------------------


@pytest.fixture
def sm() -> StateMachine:
    return StateMachine()


class TestStateMachine:
    def test_initial_state(self, sm: StateMachine) -> None:
        assert sm.get_current_state() == State.INIT
        assert sm.current_state == State.INIT

    @pytest.mark.asyncio
    async def test_init_to_running(self, sm: StateMachine) -> None:
        assert await sm.transition_to(State.RUNNING)
        assert sm.get_current_state() == State.RUNNING

    @pytest.mark.asyncio
    async def test_init_to_terminal(self, sm: StateMachine) -> None:
        assert await sm.transition_to(State.TERMINAL)
        assert sm.get_current_state() == State.TERMINAL

    @pytest.mark.asyncio
    async def test_running_to_hibernate(self, sm: StateMachine) -> None:
        await sm.transition_to(State.RUNNING)
        assert await sm.transition_to(State.HIBERNATE)
        assert sm.get_current_state() == State.HIBERNATE

    @pytest.mark.asyncio
    async def test_running_to_terminal(self, sm: StateMachine) -> None:
        await sm.transition_to(State.RUNNING)
        assert await sm.transition_to(State.TERMINAL)
        assert sm.get_current_state() == State.TERMINAL

    @pytest.mark.asyncio
    async def test_hibernate_to_running(self, sm: StateMachine) -> None:
        await sm.transition_to(State.RUNNING)
        await sm.transition_to(State.HIBERNATE)
        assert await sm.transition_to(State.RUNNING)
        assert sm.get_current_state() == State.RUNNING

    @pytest.mark.asyncio
    async def test_hibernate_to_terminal(self, sm: StateMachine) -> None:
        await sm.transition_to(State.RUNNING)
        await sm.transition_to(State.HIBERNATE)
        assert await sm.transition_to(State.TERMINAL)
        assert sm.get_current_state() == State.TERMINAL

    @pytest.mark.asyncio
    async def test_invalid_transitions(self, sm: StateMachine) -> None:
        # INIT -> HIBERNATE is invalid
        assert not await sm.transition_to(State.HIBERNATE)
        assert sm.get_current_state() == State.INIT

        await sm.transition_to(State.RUNNING)
        # RUNNING -> INIT is invalid
        assert not await sm.transition_to(State.INIT)
        assert sm.get_current_state() == State.RUNNING

        await sm.transition_to(State.TERMINAL)
        # TERMINAL -> anything is invalid
        assert not await sm.transition_to(State.RUNNING)
        assert sm.get_current_state() == State.TERMINAL

    @pytest.mark.asyncio
    async def test_transition_callback_sync(self, sm: StateMachine) -> None:
        transitions: list[tuple[State, State]] = []

        def cb(old: State, new: State) -> None:
            transitions.append((old, new))

        sm.on_transition(cb)
        await sm.transition_to(State.RUNNING)
        await sm.transition_to(State.HIBERNATE)

        assert transitions == [(State.INIT, State.RUNNING), (State.RUNNING, State.HIBERNATE)]

    @pytest.mark.asyncio
    async def test_transition_callback_async(self, sm: StateMachine) -> None:
        transitions: list[tuple[State, State]] = []

        async def cb(old: State, new: State) -> None:
            transitions.append((old, new))

        sm.on_transition(cb)
        await sm.transition_to(State.RUNNING)

        assert transitions == [(State.INIT, State.RUNNING)]

    @pytest.mark.asyncio
    async def test_remove_transition_callback(self, sm: StateMachine) -> None:
        called = False

        def cb(old: State, new: State) -> None:
            nonlocal called
            called = True

        sm.on_transition(cb)
        sm.remove_transition_callback(cb)
        await sm.transition_to(State.RUNNING)

        assert not called

    @pytest.mark.asyncio
    async def test_transition_callback_exception_isolated(self, sm: StateMachine) -> None:
        good_called = False

        def bad(old: State, new: State) -> None:
            raise RuntimeError("boom")

        def good(old: State, new: State) -> None:
            nonlocal good_called
            good_called = True

        sm.on_transition(bad)
        sm.on_transition(good)
        assert await sm.transition_to(State.RUNNING)
        assert good_called


# ---------------------------------------------------------------------------
# TierGate tests
# ---------------------------------------------------------------------------


class TestTierGate:
    @pytest.mark.parametrize(
        "balance,expected_tier",
        [
            (0.0, 0),
            (49.99, 0),
            (50.0, 0),
            (99.99, 0),
            (100.0, 1),
            (499.99, 1),
            (500.0, 2),
            (2499.99, 2),
            (2500.0, 3),
            (9999.99, 3),
            (10000.0, 4),
            (50000.0, 4),
        ],
    )
    def test_get_tier(self, balance: float, expected_tier: int) -> None:
        assert TierGate.get_tier(balance) == expected_tier

    def test_is_allowed_tier_0(self) -> None:
        balance = 50.0
        assert TierGate.is_allowed(Capability.SPOT_TRADING, balance)
        assert TierGate.is_allowed(Capability.FREELANCE_TASKS, balance)
        assert not TierGate.is_allowed(Capability.FUTURES_TRADING, balance)
        assert not TierGate.is_allowed(Capability.EQUITIES, balance)
        assert not TierGate.is_allowed(Capability.DEEP_REASONING, balance)

    def test_is_allowed_tier_1(self) -> None:
        balance = 100.0
        assert TierGate.is_allowed(Capability.SPOT_TRADING, balance)
        assert TierGate.is_allowed(Capability.FUTURES_TRADING, balance)
        assert TierGate.is_allowed(Capability.SAAS_HOSTING, balance)
        assert not TierGate.is_allowed(Capability.EQUITIES, balance)

    def test_is_allowed_tier_2(self) -> None:
        balance = 500.0
        assert TierGate.is_allowed(Capability.EQUITIES, balance)
        assert TierGate.is_allowed(Capability.SENTIMENT_FEEDS, balance)
        assert TierGate.is_allowed(Capability.DEEP_REASONING, balance)
        assert not TierGate.is_allowed(Capability.FOREX, balance)

    def test_is_allowed_tier_3(self) -> None:
        balance = 2500.0
        assert TierGate.is_allowed(Capability.FOREX, balance)
        assert TierGate.is_allowed(Capability.OPTIONS, balance)
        assert TierGate.is_allowed(Capability.SPOT_COMPUTE_SCALING, balance)
        assert not TierGate.is_allowed(Capability.CROSS_BORDER_ARBITRAGE, balance)

    def test_is_allowed_tier_4(self) -> None:
        balance = 10000.0
        assert TierGate.is_allowed(Capability.CROSS_BORDER_ARBITRAGE, balance)
        assert TierGate.is_allowed(Capability.HIGH_FREQUENCY_DATA, balance)
        assert TierGate.is_allowed(Capability.EXTERNAL_AI_AGENTS, balance)
        assert TierGate.is_allowed(Capability.LEGAL_ENTITY_FORMATION, balance)

    def test_allowed_capabilities(self) -> None:
        caps = TierGate.allowed_capabilities(100.0)
        assert Capability.SPOT_TRADING in caps
        assert Capability.FUTURES_TRADING in caps
        assert Capability.EQUITIES not in caps


# ---------------------------------------------------------------------------
# AeonConfig tests
# ---------------------------------------------------------------------------


class TestAeonConfig:
    def test_guidance_prompt_default(self) -> None:
        from auton.core.config import AeonConfig

        assert AeonConfig.GUIDANCE_PROMPT == "General profit: find any opportunity to grow the seed balance."

    def test_guidance_prompt_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AEON_GUIDANCE_PROMPT", "Crypto arbitrage: monitor exchange spreads and execute low-risk trades.")
        # Force re-import by reading the attribute after env is set
        import importlib
        from auton.core import config

        importlib.reload(config)
        assert config.AeonConfig.GUIDANCE_PROMPT == "Crypto arbitrage: monitor exchange spreads and execute low-risk trades."
