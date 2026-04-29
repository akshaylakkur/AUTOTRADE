"""Comprehensive tests for the Cortex reasoning engine."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auton.core.config import Capability, TierGate
from auton.core.constants import RISK_LIMITS, TIER_COMPUTE_BUDGETS
from auton.core.event_bus import EventBus
from auton.core.events import Hibernate, TradeSignal
from auton.cortex import (
    Decision,
    DecisionType,
    FailureRecovery,
    MetaCognition,
    ModelRouter,
    Plan,
    RecoveryAction,
    RecoveryStrategy,
    ReasoningReceipt,
    RoutingResult,
    StrategicPlanner,
    TacticalExecutor,
)
from auton.cortex.model_router import AbstractLLMProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def planner() -> StrategicPlanner:
    return StrategicPlanner()


@pytest.fixture
def executor(event_bus: EventBus) -> TacticalExecutor:
    return TacticalExecutor(event_bus=event_bus)


@pytest.fixture
def meta_cognition() -> MetaCognition:
    return MetaCognition()


@pytest.fixture
def recovery(event_bus: EventBus) -> FailureRecovery:
    return FailureRecovery(event_bus=event_bus, max_retries=3, base_backoff=1.0)


@pytest.fixture
def mock_frugal_provider() -> AbstractLLMProvider:
    p = MagicMock(spec=AbstractLLMProvider)
    p.name = "mock-frugal"
    p.estimate_cost.return_value = 0.001
    return p


@pytest.fixture
def mock_deep_provider() -> AbstractLLMProvider:
    p = MagicMock(spec=AbstractLLMProvider)
    p.name = "mock-deep"
    p.estimate_cost.return_value = 0.05
    return p


@pytest.fixture
def router(
    mock_frugal_provider: AbstractLLMProvider,
    mock_deep_provider: AbstractLLMProvider,
) -> ModelRouter:
    return ModelRouter(
        frugal_provider=mock_frugal_provider,
        deep_provider=mock_deep_provider,
        deep_complexity_threshold=0.7,
    )


# ---------------------------------------------------------------------------
# StrategicPlanner
# ---------------------------------------------------------------------------


class TestStrategicPlanner:
    def test_plan_objectives_tier_0(self, planner: StrategicPlanner) -> None:
        plan = planner.plan_objectives(balance=50.0)
        assert isinstance(plan, Plan)
        assert plan.tier == 0
        assert plan.horizon == "daily"
        assert "survive" in plan.goals
        assert "reach_tier_1" in plan.goals
        assert plan.metadata["max_position_pct"] == RISK_LIMITS[0]["max_position_pct"]
        assert plan.metadata["compute_budget"] == TIER_COMPUTE_BUDGETS[0]
        assert 0.0 <= plan.risk_tolerance <= 1.0

    def test_plan_objectives_tier_4(self, planner: StrategicPlanner) -> None:
        plan = planner.plan_objectives(balance=15000.0)
        assert plan.tier == 4
        assert "cross_border_arbitrage" in plan.goals
        assert plan.metadata["compute_budget"] == TIER_COMPUTE_BUDGETS[4]

    def test_plan_objectives_custom_horizon(self, planner: StrategicPlanner) -> None:
        plan = planner.plan_objectives(balance=500.0, horizon="weekly")
        assert plan.horizon == "weekly"
        assert plan.target_revenue >= plan.metadata["compute_budget"] * 7 * 1.5

    def test_plan_objectives_drawdown_reduces_risk(self, planner: StrategicPlanner) -> None:
        plan_normal = planner.plan_objectives(balance=500.0, tier=2)
        plan_drawdown = planner.plan_objectives(
            balance=500.0, tier=2, recent_performance={"drawdown": 0.08}
        )
        assert plan_drawdown.risk_tolerance < plan_normal.risk_tolerance
        assert "recover_drawdown" in plan_drawdown.goals

    def test_get_current_plan(self, planner: StrategicPlanner) -> None:
        assert planner.get_current_plan() is None
        plan = planner.plan_objectives(balance=100.0)
        assert planner.get_current_plan() is plan

    def test_invalid_horizon_raises(self) -> None:
        with pytest.raises(ValueError, match="daily' or 'weekly"):
            StrategicPlanner(default_horizon="monthly")

    def test_plan_objectives_includes_guidance_prompt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AEON_GUIDANCE_PROMPT", "Real estate: find undervalued properties.")
        import importlib
        from auton.core import config
        from auton.cortex import planner as planner_module

        importlib.reload(config)
        importlib.reload(planner_module)
        planner = planner_module.StrategicPlanner()
        plan = planner.plan_objectives(balance=500.0)
        assert any("Real estate" in g for g in plan.goals)


# ---------------------------------------------------------------------------
# TacticalExecutor
# ---------------------------------------------------------------------------


class TestTacticalExecutor:
    @pytest.mark.asyncio
    async def test_evaluate_trade_approved(self, executor: TacticalExecutor) -> None:
        opp = {
            "type": "trade",
            "symbol": "BTCUSD",
            "side": "BUY",
            "quantity": 0.01,
            "price": 50000.0,
            "confidence": 0.85,
            "expected_profit": 10.0,
            "balance": 500.0,
        }
        decision = await executor.evaluate_opportunity(opp)
        assert decision.decision_type == DecisionType.TRADE
        assert decision.symbol == "BTCUSD"
        assert decision.confidence == 0.85

    @pytest.mark.asyncio
    async def test_evaluate_trade_denied_by_tier(self, executor: TacticalExecutor) -> None:
        opp = {
            "type": "trade",
            "symbol": "BTCUSD",
            "side": "BUY",
            "quantity": 0.01,
            "price": 50000.0,
            "confidence": 0.85,
            "expected_profit": 10.0,
            "balance": 10.0,
        }
        with patch.object(TierGate, "is_allowed", return_value=False):
            decision = await executor.evaluate_opportunity(opp)
        assert decision.decision_type == DecisionType.NO_OP
        assert decision.metadata["reason"] == "tier_gate_denied"

    @pytest.mark.asyncio
    async def test_evaluate_trade_low_confidence(self, executor: TacticalExecutor) -> None:
        opp = {
            "type": "trade",
            "symbol": "BTCUSD",
            "side": "BUY",
            "quantity": 0.01,
            "price": 50000.0,
            "confidence": 0.3,
            "expected_profit": 10.0,
            "balance": 500.0,
        }
        decision = await executor.evaluate_opportunity(opp)
        assert decision.decision_type == DecisionType.NO_OP
        assert decision.metadata["reason"] == "insufficient_confidence"

    @pytest.mark.asyncio
    async def test_evaluate_product_launch_approved(self, executor: TacticalExecutor) -> None:
        opp = {
            "type": "product_launch",
            "product_id": "my-saas",
            "confidence": 0.9,
            "expected_profit": 50.0,
            "balance": 500.0,
        }
        decision = await executor.evaluate_opportunity(opp)
        assert decision.decision_type == DecisionType.PRODUCT_LAUNCH
        assert decision.symbol == "my-saas"

    @pytest.mark.asyncio
    async def test_evaluate_product_launch_denied_by_tier(
        self, executor: TacticalExecutor
    ) -> None:
        opp = {
            "type": "product_launch",
            "product_id": "my-saas",
            "confidence": 0.9,
            "expected_profit": 50.0,
            "balance": 50.0,
        }
        decision = await executor.evaluate_opportunity(opp)
        assert decision.decision_type == DecisionType.NO_OP
        assert decision.metadata["capability"] == "SAAS_HOSTING"

    @pytest.mark.asyncio
    async def test_evaluate_unknown_type(self, executor: TacticalExecutor) -> None:
        decision = await executor.evaluate_opportunity({"type": "magic"})
        assert decision.decision_type == DecisionType.NO_OP
        assert decision.metadata["reason"] == "unrecognised_opportunity_type"

    @pytest.mark.asyncio
    async def test_execute_decision_trade(self, executor: TacticalExecutor) -> None:
        received: list[TradeSignal] = []

        async def capture(payload: TradeSignal) -> None:
            received.append(payload)

        await executor._event_bus.subscribe(TradeSignal, capture)

        decision = Decision(
            decision_type=DecisionType.TRADE,
            symbol="ETHUSD",
            side="SELL",
            quantity=0.5,
            price=3000.0,
            confidence=0.75,
            expected_profit=5.0,
        )
        result = await executor.execute_decision(decision)
        assert result["executed"] is True
        assert result["event"] == "TradeSignal"
        assert len(received) == 1
        assert received[0].symbol == "ETHUSD"
        assert received[0].side == "SELL"

    @pytest.mark.asyncio
    async def test_execute_decision_no_event_bus(self) -> None:
        executor_no_bus = TacticalExecutor()
        decision = Decision(
            decision_type=DecisionType.TRADE,
            symbol="BTCUSD",
            side="BUY",
            quantity=0.01,
            price=50000.0,
            confidence=0.8,
            expected_profit=10.0,
        )
        result = await executor_no_bus.execute_decision(decision)
        assert result["executed"] is False
        assert result["reason"] == "no_event_bus"

    @pytest.mark.asyncio
    async def test_execute_decision_no_op(self, executor: TacticalExecutor) -> None:
        decision = Decision(
            decision_type=DecisionType.NO_OP,
            symbol=None,
            side=None,
            quantity=0.0,
            price=None,
            confidence=0.0,
            expected_profit=0.0,
        )
        result = await executor.execute_decision(decision)
        assert result["executed"] is False
        assert result["reason"] == "no_op_decision"

    @pytest.mark.asyncio
    async def test_execute_decision_product_launch(self, executor: TacticalExecutor) -> None:
        received: list[dict[str, Any]] = []

        async def capture(payload: dict[str, Any]) -> None:
            received.append(payload)

        await executor._event_bus.subscribe(dict, capture)

        decision = Decision(
            decision_type=DecisionType.PRODUCT_LAUNCH,
            symbol="saas-v1",
            side=None,
            quantity=0.0,
            price=None,
            confidence=0.9,
            expected_profit=20.0,
        )
        result = await executor.execute_decision(decision)
        assert result["executed"] is True
        assert result["event"] == "ProductLaunch"
        assert len(received) == 1
        assert received[0]["product_id"] == "saas-v1"


# ---------------------------------------------------------------------------
# MetaCognition
# ---------------------------------------------------------------------------


class TestMetaCognition:
    def test_evaluate_reasoning_cost_go(self, meta_cognition: MetaCognition) -> None:
        receipt = meta_cognition.evaluate_reasoning_cost(
            expected_profit=10.0, reasoning_cost=0.01, confidence=0.8
        )
        assert isinstance(receipt, ReasoningReceipt)
        assert receipt.go is True
        assert receipt.expected_value == pytest.approx(7.99, rel=1e-3)
        assert receipt.mode == "frugal"

    def test_evaluate_reasoning_cost_no_go_low_confidence(
        self, meta_cognition: MetaCognition
    ) -> None:
        receipt = meta_cognition.evaluate_reasoning_cost(
            expected_profit=100.0, reasoning_cost=0.01, confidence=0.2
        )
        assert receipt.go is False

    def test_evaluate_reasoning_cost_no_go_negative_ev(
        self, meta_cognition: MetaCognition
    ) -> None:
        receipt = meta_cognition.evaluate_reasoning_cost(
            expected_profit=0.01, reasoning_cost=0.05, confidence=0.9
        )
        assert receipt.go is False
        assert receipt.expected_value < 0

    def test_should_use_deep_mode(self, meta_cognition: MetaCognition) -> None:
        assert meta_cognition.should_use_deep_mode(
            current_burn_rate=1.0, income=10.0, opportunity_size=5.0
        ) is True
        assert meta_cognition.should_use_deep_mode(
            current_burn_rate=10.0, income=1.0, opportunity_size=5.0
        ) is False
        assert meta_cognition.should_use_deep_mode(
            current_burn_rate=1.0, income=10.0, opportunity_size=0.001
        ) is False

    def test_should_use_deep_mode_zero_burn(self, meta_cognition: MetaCognition) -> None:
        assert meta_cognition.should_use_deep_mode(
            current_burn_rate=0.0, income=0.0, opportunity_size=0.2
        ) is True
        assert meta_cognition.should_use_deep_mode(
            current_burn_rate=0.0, income=0.0, opportunity_size=0.001
        ) is False

    def test_receipt_for_opportunity(self, meta_cognition: MetaCognition) -> None:
        receipt = meta_cognition.receipt_for_opportunity(
            expected_profit=50.0,
            confidence=0.75,
            current_burn_rate=1.0,
            income=5.0,
        )
        assert isinstance(receipt, ReasoningReceipt)
        assert receipt.go is True
        assert receipt.mode == "deep"


# ---------------------------------------------------------------------------
# FailureRecovery
# ---------------------------------------------------------------------------


class TestFailureRecovery:
    @pytest.mark.asyncio
    async def test_handle_api_error_retry(self, recovery: FailureRecovery) -> None:
        action = await recovery.handle_api_error(
            error=ConnectionError("timeout"),
            context={"api_name": "binance", "retry_count": 0},
        )
        assert action.strategy == RecoveryStrategy.RETRY_WITH_BACKOFF
        assert action.retry_count == 1
        assert action.backoff_seconds >= 1.0

    @pytest.mark.asyncio
    async def test_handle_api_error_exhausted_switch_source(
        self, recovery: FailureRecovery
    ) -> None:
        action = await recovery.handle_api_error(
            error=ConnectionError("timeout"),
            context={
                "api_name": "binance",
                "retry_count": 3,
                "alternative_sources": ["coinbase", "kraken"],
            },
        )
        assert action.strategy == RecoveryStrategy.SWITCH_DATA_SOURCE
        assert action.metadata["alternative_sources"] == ["coinbase", "kraken"]

    @pytest.mark.asyncio
    async def test_handle_api_error_exhausted_hibernate(
        self, event_bus: EventBus, recovery: FailureRecovery
    ) -> None:
        hibernates: list[Hibernate] = []

        async def capture(payload: Hibernate) -> None:
            hibernates.append(payload)

        await event_bus.subscribe(Hibernate, capture)

        action = await recovery.handle_api_error(
            error=ConnectionError("timeout"),
            context={"api_name": "binance", "retry_count": 3},
        )
        assert action.strategy == RecoveryStrategy.ENTER_HIBERNATION
        assert len(hibernates) == 1
        assert "binance" in hibernates[0].reason

    @pytest.mark.asyncio
    async def test_handle_market_gap_minor(self, recovery: FailureRecovery) -> None:
        action = await recovery.handle_market_gap(
            {"symbol": "BTCUSD", "gap_pct": 0.01}
        )
        assert action.strategy == RecoveryStrategy.RETRY_WITH_BACKOFF

    @pytest.mark.asyncio
    async def test_handle_market_gap_moderate(self, recovery: FailureRecovery) -> None:
        action = await recovery.handle_market_gap(
            {"symbol": "BTCUSD", "gap_pct": 0.03}
        )
        assert action.strategy == RecoveryStrategy.LIQUIDATE_POSITIONS
        assert "Moderate" in action.description

    @pytest.mark.asyncio
    async def test_handle_market_gap_severe(self, event_bus: EventBus, recovery: FailureRecovery) -> None:
        hibernates: list[Hibernate] = []

        async def capture(payload: Hibernate) -> None:
            hibernates.append(payload)

        await event_bus.subscribe(Hibernate, capture)

        action = await recovery.handle_market_gap(
            {"symbol": "BTCUSD", "gap_pct": 0.06}
        )
        assert action.strategy == RecoveryStrategy.ENTER_HIBERNATION
        assert len(hibernates) == 1

    @pytest.mark.asyncio
    async def test_handle_bad_trade_minor(self, recovery: FailureRecovery) -> None:
        action = await recovery.handle_bad_trade(
            {"symbol": "BTCUSD", "pnl": -0.5, "pnl_pct": -0.005}
        )
        assert action.strategy == RecoveryStrategy.DEGRADE_CAPABILITY

    @pytest.mark.asyncio
    async def test_handle_bad_trade_significant(self, recovery: FailureRecovery) -> None:
        action = await recovery.handle_bad_trade(
            {"symbol": "BTCUSD", "pnl": -5.0, "pnl_pct": -0.03}
        )
        assert action.strategy == RecoveryStrategy.LIQUIDATE_POSITIONS
        assert "correlated_positions" in action.metadata

    @pytest.mark.asyncio
    async def test_handle_bad_trade_catastrophic(self, event_bus: EventBus, recovery: FailureRecovery) -> None:
        hibernates: list[Hibernate] = []

        async def capture(payload: Hibernate) -> None:
            hibernates.append(payload)

        await event_bus.subscribe(Hibernate, capture)

        action = await recovery.handle_bad_trade(
            {"symbol": "BTCUSD", "pnl": -15.0, "pnl_pct": -0.08}
        )
        assert action.strategy == RecoveryStrategy.ENTER_HIBERNATION
        assert len(hibernates) == 1


# ---------------------------------------------------------------------------
# ModelRouter
# ---------------------------------------------------------------------------


class TestModelRouter:
    @pytest.mark.asyncio
    async def test_route_frugal(self, router: ModelRouter) -> None:
        result = await router.route(
            prompt="hello", complexity_score=0.2, balance=100.0, burn_rate=1.0
        )
        assert isinstance(result, RoutingResult)
        assert result.mode == "frugal"
        assert result.provider_name == "mock-frugal"
        assert result.estimated_cost == 0.001

    @pytest.mark.asyncio
    async def test_route_deep_by_complexity(self, router: ModelRouter) -> None:
        result = await router.route(
            prompt="complex reasoning", complexity_score=0.8, balance=1000.0, burn_rate=1.0
        )
        assert result.mode == "deep"
        assert result.provider_name == "mock-deep"
        assert result.estimated_cost == 0.05

    @pytest.mark.asyncio
    async def test_route_deep_by_balance(self, router: ModelRouter) -> None:
        result = await router.route(
            prompt="hello", complexity_score=0.5, balance=10000.0, burn_rate=0.1
        )
        assert result.mode == "deep"

    @pytest.mark.asyncio
    async def test_route_forced_frugal_high_burn(self, router: ModelRouter) -> None:
        result = await router.route(
            prompt="hello", complexity_score=0.9, balance=100.0, burn_rate=10.0
        )
        # Even high complexity can't overcome deficit if balance isn't > burn*7
        assert result.mode == "frugal"

    @pytest.mark.asyncio
    async def test_route_no_providers(self) -> None:
        bare_router = ModelRouter()
        result = await bare_router.route(
            prompt="hello", complexity_score=0.5, balance=100.0, burn_rate=1.0
        )
        assert result.provider_name == "none"
        assert result.mode == "frugal"

    @pytest.mark.asyncio
    async def test_route_invalid_complexity(self, router: ModelRouter) -> None:
        with pytest.raises(ValueError, match="complexity_score"):
            await router.route(
                prompt="hello", complexity_score=1.5, balance=100.0, burn_rate=1.0
            )


# ---------------------------------------------------------------------------
# Integration / Smoke
# ---------------------------------------------------------------------------


class TestCortexIntegration:
    @pytest.mark.asyncio
    async def test_end_to_end_smoke(self) -> None:
        """Run a minimal end-to-end cycle through planner, executor, meta, recovery."""
        bus = EventBus()
        planner = StrategicPlanner()
        executor = TacticalExecutor(event_bus=bus)
        meta = MetaCognition()
        recovery = FailureRecovery(event_bus=bus)

        # 1. Plan
        plan = planner.plan_objectives(balance=2500.0, tier=3)
        assert plan.tier == 3
        receipt = meta.receipt_for_opportunity(
            expected_profit=plan.target_revenue,
            confidence=0.8,
            current_burn_rate=plan.metadata["compute_budget"],
            income=plan.target_revenue,
        )
        assert receipt.go is True

        # 2. Evaluate a trade
        opp = {
            "type": "trade",
            "symbol": "BTCUSD",
            "side": "BUY",
            "quantity": 0.01,
            "price": 50000.0,
            "confidence": 0.85,
            "expected_profit": 20.0,
            "balance": 2500.0,
        }
        decision = await executor.evaluate_opportunity(opp)
        assert decision.decision_type == DecisionType.TRADE

        signals: list[TradeSignal] = []
        await bus.subscribe(TradeSignal, lambda p: signals.append(p))  # type: ignore[arg-type]
        await executor.execute_decision(decision)
        await asyncio.sleep(0.05)  # let event bus tasks settle
        assert len(signals) == 1

        # 3. Recovery on API failure
        action = await recovery.handle_api_error(
            error=RuntimeError("rate limit"),
            context={"api_name": "alpaca", "retry_count": 0},
        )
        assert action.strategy == RecoveryStrategy.RETRY_WITH_BACKOFF


class TestGoalGenerator:
    def test_generate_goals_includes_guidance_prompt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AEON_GUIDANCE_PROMPT", "Technology stocks: analyze tech sector for long-term growth opportunities.")
        import importlib
        from auton.core import config
        from auton.cortex import free_will as free_will_module

        importlib.reload(config)
        importlib.reload(free_will_module)
        gen = free_will_module.GoalGenerator()
        goals = gen.generate_goals(balance=500.0, tier=2)
        assert any(g.name == "user_guidance" for g in goals)
        guidance_goal = next(g for g in goals if g.name == "user_guidance")
        assert "Technology stocks" in guidance_goal.description
