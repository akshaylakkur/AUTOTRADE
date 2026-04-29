"""Tests for the expansionism and decision engine modules."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from auton.core.config import Capability
from auton.core.constants import RISK_LIMITS, SEED_BALANCE
from auton.core.event_bus import EventBus
from auton.core.events import (
    BalanceChanged,
    DecisionMade,
    GoalGenerated,
    OpportunityDiscovered,
    SimulationCompleted,
    StrategySwitched,
)
from auton.cortex.consequence_modeler import (
    ConsequenceModeler,
    MonteCarloSimulator,
    OutcomeDistribution,
    WorstCaseAnalyzer,
)
from auton.cortex.decision_engine import (
    AutonomousDecisionSystem,
    DecisionQueue,
    MultiObjectiveOptimizer,
    Opportunity,
    OpportunityEvaluator,
    OpportunityScore,
    ResourceAllocation,
    ResourceAllocator,
    ResourceDecision,
    RiskAssessment,
    RiskEngine,
)
from auton.cortex.expansionism import (
    Allocation,
    ArbitrageExpansion,
    CapitalAllocator,
    CapabilityRegistry,
    ContentExpansion,
    ExpansionController,
    ExpansionStrategy,
    Goal,
    GoalPlanner,
    Milestone,
    NovelStrategyProposer,
    SaaSExpansion,
    StrategyPerformance,
    TradingExpansion,
    WealthTierManager,
)
from auton.cortex.free_will import FreeWillEngine, GoalGenerator, SerendipityEngine


# ---------------------------------------------------------------------------
# WealthTierManager
# ---------------------------------------------------------------------------


class TestWealthTierManager:
    def test_init(self) -> None:
        mgr = WealthTierManager()
        assert mgr.current_tier == 0
        assert mgr.get_tier(50.0) == 0

    def test_tier_thresholds(self) -> None:
        mgr = WealthTierManager()
        assert mgr.get_tier(49.0) == 0
        assert mgr.get_tier(50.0) == 0
        assert mgr.get_tier(499.0) == 0
        assert mgr.get_tier(500.0) == 1
        assert mgr.get_tier(999.0) == 1
        assert mgr.get_tier(1_000.0) == 2
        assert mgr.get_tier(9_999.0) == 2
        assert mgr.get_tier(10_000.0) == 3
        assert mgr.get_tier(99_999.0) == 3
        assert mgr.get_tier(100_000.0) == 4
        assert mgr.get_tier(999_999.0) == 4
        assert mgr.get_tier(1_000_000.0) == 5

    def test_update_and_history(self) -> None:
        mgr = WealthTierManager()
        record = mgr.update(500.0)
        assert record["old_tier"] == 0
        assert record["new_tier"] == 1
        # Tier 0 capabilities are already unlocked; tier 1 adds new ones
        assert "FUTURES_TRADING" in record["unlocked"] or any(
            "FUTURES_TRADING" in r["unlocked"] for r in mgr.get_history()
        )
        assert mgr.current_tier == 1
        assert len(mgr.get_history()) == 1

    def test_capabilities_for_tier(self) -> None:
        mgr = WealthTierManager()
        caps = mgr.capabilities_for_tier(1)
        assert Capability.SPOT_TRADING in caps
        assert Capability.FUTURES_TRADING in caps

    def test_is_unlocked(self) -> None:
        mgr = WealthTierManager()
        assert mgr.is_unlocked(Capability.SPOT_TRADING, 50.0)
        assert not mgr.is_unlocked(Capability.FUTURES_TRADING, 50.0)
        assert mgr.is_unlocked(Capability.FUTURES_TRADING, 500.0)

    def test_next_threshold(self) -> None:
        mgr = WealthTierManager()
        assert mgr.next_threshold(50.0) == 500.0
        assert mgr.next_threshold(500.0) == 1_000.0
        assert mgr.next_threshold(1_000_000.0) is None

    def test_progress_to_next(self) -> None:
        mgr = WealthTierManager()
        assert mgr.progress_to_next(50.0) == 0.0
        assert mgr.progress_to_next(275.0) == pytest.approx(0.5, abs=0.01)
        assert mgr.progress_to_next(499.0) == pytest.approx(449.0 / 450.0, abs=0.001)
        assert mgr.progress_to_next(500.0) == 0.0
        assert mgr.progress_to_next(750.0) == pytest.approx(0.5, abs=0.01)
        assert mgr.progress_to_next(1_000_000.0) == 1.0

    def test_custom_thresholds(self) -> None:
        custom = {0: 10.0, 1: 100.0}
        mgr = WealthTierManager(thresholds=custom)
        assert mgr.get_tier(50.0) == 0
        assert mgr.get_tier(100.0) == 1


# ---------------------------------------------------------------------------
# CapabilityRegistry
# ---------------------------------------------------------------------------


class TestCapabilityRegistry:
    def test_register_and_activate(self, tmp_path: Path) -> None:
        reg = CapabilityRegistry(path=tmp_path / "caps.json")
        reg.register("alpha", requires=["beta"])
        reg.register("beta")
        assert reg.is_available("alpha")
        assert reg.is_available("beta")
        assert not reg.is_active("alpha")

        assert reg.activate("beta")
        assert reg.is_active("beta")
        assert reg.activate("alpha")
        assert reg.is_active("alpha")

    def test_missing_requirements(self, tmp_path: Path) -> None:
        reg = CapabilityRegistry(path=tmp_path / "caps.json")
        reg.register("alpha", requires=["beta"])
        assert reg.missing_requirements("alpha") == ["beta"]
        reg.register("beta")
        reg.activate("beta")
        assert reg.missing_requirements("alpha") == []

    def test_deactivate_cascade(self, tmp_path: Path) -> None:
        reg = CapabilityRegistry(path=tmp_path / "caps.json")
        reg.register("a")
        reg.register("b", requires=["a"])
        reg.activate("a")
        reg.activate("b")
        assert reg.is_active("b")
        reg.deactivate("a")
        assert not reg.is_active("a")
        assert not reg.is_active("b")

    def test_persistence(self, tmp_path: Path) -> None:
        path = tmp_path / "caps.json"
        reg = CapabilityRegistry(path=path)
        reg.register("x")
        reg.activate("x")
        del reg

        reg2 = CapabilityRegistry(path=path)
        assert reg2.is_available("x")
        assert reg2.is_active("x")

    def test_bulk_sync(self, tmp_path: Path) -> None:
        reg = CapabilityRegistry(path=tmp_path / "caps.json")
        reg.register("SPOT_TRADING")
        reg.register("FUTURES_TRADING")
        caps = [Capability.SPOT_TRADING]
        result = reg.bulk_sync_with_tiers(caps)
        assert "SPOT_TRADING" in result["activated"]
        assert "FUTURES_TRADING" in result["deactivated"] or "FUTURES_TRADING" not in reg.list_active()


# ---------------------------------------------------------------------------
# GoalPlanner
# ---------------------------------------------------------------------------


class TestGoalPlanner:
    def test_add_and_get_goal(self) -> None:
        gp = GoalPlanner()
        goal = Goal(
            name="test",
            description="desc",
            milestones=[Milestone(name="m1", target_value=100.0)],
        )
        gp.add_goal(goal)
        assert gp.list_goals() == ["test"]
        assert gp.get_goal("test") == goal

    def test_update_milestone(self) -> None:
        gp = GoalPlanner()
        goal = Goal(
            name="g",
            description="d",
            milestones=[
                Milestone(name="m1", target_value=100.0),
                Milestone(name="m2", target_value=200.0),
            ],
        )
        gp.add_goal(goal)
        updated = gp.update_milestone("g", "m1", 50.0)
        assert updated is not None
        assert updated.current_value == 50.0
        assert not updated.completed
        assert gp.goal_progress("g") == 0.25

        updated2 = gp.update_milestone("g", "m1", 100.0)
        assert updated2 is not None
        assert updated2.completed
        assert gp.goal_progress("g") == 0.5

    def test_is_goal_complete(self) -> None:
        gp = GoalPlanner()
        goal = Goal(
            name="g",
            description="d",
            milestones=[Milestone(name="m1", target_value=10.0)],
        )
        gp.add_goal(goal)
        assert not gp.is_goal_complete("g")
        gp.update_milestone("g", "m1", 10.0)
        assert gp.is_goal_complete("g")

    def test_update_missing_goal(self) -> None:
        gp = GoalPlanner()
        assert gp.update_milestone("missing", "m", 10.0) is None

    def test_suggest_goals(self) -> None:
        gp = GoalPlanner()
        goals = gp.suggest_goals(0, 50.0)
        assert any(g.name == "survival" for g in goals)
        goals = gp.suggest_goals(4, 200_000.0)
        assert any(g.name == "dominance" for g in goals)

    def test_history(self) -> None:
        gp = GoalPlanner()
        gp.add_goal(Goal(
            name="g",
            description="d",
            milestones=[Milestone(name="m1", target_value=10.0)],
        ))
        gp.update_milestone("g", "m1", 5.0)
        assert len(gp.get_history()) == 1
        assert gp.get_history()[0]["goal"] == "g"


# ---------------------------------------------------------------------------
# CapitalAllocator
# ---------------------------------------------------------------------------


class TestCapitalAllocator:
    def test_empty_opportunities(self) -> None:
        alloc = CapitalAllocator()
        assert alloc.allocate(1000.0, []) == []

    def test_basic_allocation(self) -> None:
        alloc = CapitalAllocator(survival_reserve_pct=0.10, max_position_pct=0.50)
        opps = [
            {"id": "a", "score": 0.8},
            {"id": "b", "score": 0.2},
        ]
        result = alloc.allocate(1_000.0, opps)
        assert len(result) == 2
        total = alloc.total_allocated(result)
        assert total <= 900.0  # deployable = 1000 * 0.9
        assert result[0].amount > result[1].amount

    def test_max_position_cap(self) -> None:
        alloc = CapitalAllocator(survival_reserve_pct=0.10, max_position_pct=0.05)
        opps = [{"id": "a", "score": 1.0}]
        result = alloc.allocate(1_000.0, opps)
        assert result[0].amount <= 50.0  # 1000 * 0.9 * 0.05

    def test_min_allocation_filter(self) -> None:
        alloc = CapitalAllocator(min_allocation=5.0)
        opps = [{"id": "a", "score": 0.01}]
        result = alloc.allocate(10.0, opps)
        assert result[0].amount == 0.0

    def test_per_opportunity_max(self) -> None:
        alloc = CapitalAllocator(survival_reserve_pct=0.0, max_position_pct=1.0)
        opps = [{"id": "a", "score": 1.0, "max_allocation": 5.0}]
        result = alloc.allocate(1_000.0, opps)
        assert result[0].amount == 5.0

    def test_reserve_amount(self) -> None:
        alloc = CapitalAllocator(survival_reserve_pct=0.10)
        assert alloc.reserve_amount(1_000.0) == 100.0

    def test_history(self) -> None:
        alloc = CapitalAllocator()
        alloc.allocate(1_000.0, [{"id": "a", "score": 1.0}])
        assert len(alloc.get_history()) == 1


# ---------------------------------------------------------------------------
# OpportunityEvaluator
# ---------------------------------------------------------------------------


class TestOpportunityEvaluator:
    def test_score_high_quality_trade(self) -> None:
        ev = OpportunityEvaluator()
        opp = Opportunity(
            id="o1",
            opportunity_type="trade",
            expected_return=20.0,
            risk_score=0.1,
            capital_required=10.0,
            time_horizon_hours=2.0,
            confidence=0.9,
        )
        score = ev.evaluate(opp, balance=1000.0, tier=2)
        assert isinstance(score, OpportunityScore)
        assert score.approved is True
        assert score.total_score > 0.5
        assert score.return_score > 0.0
        assert score.risk_score > 0.0
        assert score.capital_score > 0.0
        assert score.time_score > 0.0

    def test_low_confidence_rejected(self) -> None:
        ev = OpportunityEvaluator(min_confidence=0.5)
        opp = Opportunity(
            id="o1",
            opportunity_type="trade",
            expected_return=100.0,
            risk_score=0.1,
            capital_required=10.0,
            time_horizon_hours=1.0,
            confidence=0.2,
        )
        score = ev.evaluate(opp, balance=1000.0)
        assert not score.approved
        assert score.total_score == 0.0
        assert score.metadata.get("reason") == "insufficient_confidence"

    def test_high_risk_rejected(self) -> None:
        ev = OpportunityEvaluator(max_risk_threshold=0.5)
        opp = Opportunity(
            id="o1",
            opportunity_type="trade",
            expected_return=100.0,
            risk_score=0.9,
            capital_required=10.0,
            time_horizon_hours=1.0,
            confidence=0.9,
        )
        score = ev.evaluate(opp, balance=1000.0)
        assert not score.approved
        assert score.metadata.get("reason") == "risk_exceeds_threshold"

    def test_tier_gate_denied(self) -> None:
        ev = OpportunityEvaluator()
        opp = Opportunity(
            id="o1",
            opportunity_type="futures_trade",
            expected_return=50.0,
            risk_score=0.2,
            capital_required=10.0,
            time_horizon_hours=2.0,
            confidence=0.9,
        )
        score = ev.evaluate(opp, balance=50.0)
        assert not score.approved
        assert score.metadata.get("reason") == "tier_gate_denied"

    def test_evaluate_batch_and_rank(self) -> None:
        ev = OpportunityEvaluator()
        opps = [
            Opportunity(
                id="a",
                opportunity_type="trade",
                expected_return=10.0,
                risk_score=0.1,
                capital_required=5.0,
                time_horizon_hours=1.0,
                confidence=0.8,
            ),
            Opportunity(
                id="b",
                opportunity_type="trade",
                expected_return=5.0,
                risk_score=0.5,
                capital_required=5.0,
                time_horizon_hours=4.0,
                confidence=0.8,
            ),
        ]
        scores = ev.evaluate_batch(opps, balance=1_000.0)
        assert len(scores) == 2
        ranked = ev.rank(scores)
        assert ranked[0].opportunity_id == "a"

    def test_invalid_weights(self) -> None:
        with pytest.raises(ValueError, match="weights must sum"):
            OpportunityEvaluator(weights={"return": 0.0, "risk": 0.0})


# ---------------------------------------------------------------------------
# RiskEngine
# ---------------------------------------------------------------------------


class TestRiskEngine:
    def test_assess_low_risk(self) -> None:
        engine = RiskEngine()
        opp = Opportunity(
            id="r1",
            opportunity_type="trade",
            expected_return=10.0,
            risk_score=0.05,
            capital_required=10.0,
            time_horizon_hours=1.0,
            confidence=0.9,
        )
        assessment = engine.assess(opp, balance=1_000.0, tier=2)
        assert isinstance(assessment, RiskAssessment)
        assert assessment.overall_risk < 0.5
        assert not assessment.tier_limit_breach

    def test_assess_high_concentration(self) -> None:
        engine = RiskEngine()
        opp = Opportunity(
            id="r1",
            opportunity_type="trade",
            expected_return=100.0,
            risk_score=0.3,
            capital_required=500.0,
            time_horizon_hours=1.0,
            confidence=0.9,
        )
        assessment = engine.assess(opp, balance=1_000.0, tier=0)
        assert assessment.concentration_risk > 0.4
        assert assessment.tier_limit_breach is True

    def test_within_limits(self) -> None:
        engine = RiskEngine()
        safe = Opportunity(
            id="safe",
            opportunity_type="trade",
            expected_return=5.0,
            risk_score=0.05,
            capital_required=5.0,
            time_horizon_hours=1.0,
            confidence=0.9,
        )
        assert engine.within_limits(safe, balance=1_000.0, tier=2)

        risky = Opportunity(
            id="risky",
            opportunity_type="trade",
            expected_return=500.0,
            risk_score=0.9,
            capital_required=900.0,
            time_horizon_hours=1.0,
            confidence=0.9,
        )
        assert not engine.within_limits(risky, balance=1_000.0, tier=2)

    def test_assess_portfolio(self) -> None:
        engine = RiskEngine()
        opps = [
            Opportunity(
                id="a",
                opportunity_type="trade",
                expected_return=10.0,
                risk_score=0.1,
                capital_required=10.0,
                time_horizon_hours=1.0,
                confidence=0.9,
            ),
            Opportunity(
                id="b",
                opportunity_type="trade",
                expected_return=10.0,
                risk_score=0.2,
                capital_required=10.0,
                time_horizon_hours=1.0,
                confidence=0.9,
            ),
        ]
        assessment = engine.assess_portfolio(opps, balance=1_000.0, tier=2)
        assert assessment.overall_risk < 0.5
        assert not assessment.tier_limit_breach

    def test_portfolio_tier_limit_breach(self) -> None:
        engine = RiskEngine()
        opps = [
            Opportunity(
                id="a",
                opportunity_type="trade",
                expected_return=10.0,
                risk_score=0.1,
                capital_required=500.0,
                time_horizon_hours=1.0,
                confidence=0.9,
            ),
        ]
        assessment = engine.assess_portfolio(opps, balance=1_000.0, tier=0)
        assert assessment.tier_limit_breach is True


# ---------------------------------------------------------------------------
# ResourceDecision
# ---------------------------------------------------------------------------


class TestResourceDecision:
    def test_fields(self) -> None:
        d = ResourceDecision(
            action="buy BTC",
            expected_roi=0.05,
            confidence=0.8,
            risk_score=0.2,
            time_horizon=2.0,
            required_budget=10.0,
            strategy="trading",
        )
        assert d.action == "buy BTC"
        assert d.expected_roi == 0.05
        assert d.strategy == "trading"


# ---------------------------------------------------------------------------
# MultiObjectiveOptimizer
# ---------------------------------------------------------------------------


class TestMultiObjectiveOptimizer:
    def test_optimise_sorts_descending(self) -> None:
        opt = MultiObjectiveOptimizer()
        decisions = [
            ResourceDecision("a", expected_roi=0.10, confidence=0.8, risk_score=0.1, time_horizon=1.0, required_budget=10.0),
            ResourceDecision("b", expected_roi=0.05, confidence=0.8, risk_score=0.5, time_horizon=4.0, required_budget=10.0),
        ]
        scored = opt.optimise(decisions)
        assert scored[0][0].action == "a"
        assert scored[0][1] > scored[1][1]

    def test_pareto_frontier(self) -> None:
        opt = MultiObjectiveOptimizer()
        decisions = [
            ResourceDecision("a", expected_roi=0.10, confidence=0.8, risk_score=0.1, time_horizon=1.0, required_budget=10.0),
            ResourceDecision("b", expected_roi=0.05, confidence=0.8, risk_score=0.5, time_horizon=4.0, required_budget=10.0),
            ResourceDecision("c", expected_roi=0.12, confidence=0.8, risk_score=0.05, time_horizon=0.5, required_budget=10.0),
        ]
        frontier = opt.pareto_frontier(decisions)
        actions = {d.action for d in frontier}
        # c dominates a (better roi, lower risk, shorter time), so a is not in frontier
        assert "c" in actions
        assert "a" not in actions
        assert "b" not in actions

    def test_empty_input(self) -> None:
        opt = MultiObjectiveOptimizer()
        assert opt.optimise([]) == []
        assert opt.pareto_frontier([]) == []

    def test_invalid_weights(self) -> None:
        with pytest.raises(ValueError, match="weights must sum"):
            MultiObjectiveOptimizer(profit_weight=0.0, risk_weight=0.0, time_weight=0.0)


# ---------------------------------------------------------------------------
# ResourceAllocator
# ---------------------------------------------------------------------------


class TestResourceAllocator:
    def test_basic_allocation(self) -> None:
        alloc = ResourceAllocator(survival_reserve_pct=0.10, max_position_pct=0.50)
        decisions = [
            (ResourceDecision("a", expected_roi=0.10, confidence=0.8, risk_score=0.1, time_horizon=1.0, required_budget=10.0), 0.8),
            (ResourceDecision("b", expected_roi=0.05, confidence=0.8, risk_score=0.5, time_horizon=4.0, required_budget=10.0), 0.2),
        ]
        result = alloc.allocate(1_000.0, decisions)
        assert len(result) == 2
        total = alloc.total_allocated(result)
        assert total <= 900.0
        assert result[0].amount >= result[1].amount

    def test_empty_decisions(self) -> None:
        alloc = ResourceAllocator()
        assert alloc.allocate(1_000.0, []) == []

    def test_reserve_amount(self) -> None:
        alloc = ResourceAllocator(survival_reserve_pct=0.10)
        assert alloc.reserve_amount(1_000.0) == 100.0


# ---------------------------------------------------------------------------
# DecisionQueue
# ---------------------------------------------------------------------------


class TestDecisionQueue:
    def test_push_and_pop(self) -> None:
        q = DecisionQueue()
        d1 = ResourceDecision("a", expected_roi=0.10, confidence=0.8, risk_score=0.1, time_horizon=1.0, required_budget=10.0)
        d2 = ResourceDecision("b", expected_roi=0.20, confidence=0.8, risk_score=0.1, time_horizon=1.0, required_budget=10.0)
        q.push(d1)
        q.push(d2)
        assert len(q) == 2
        # Higher expected_roi / risk = higher priority
        assert q.pop() == d2

    def test_peek(self) -> None:
        q = DecisionQueue()
        d = ResourceDecision("a", expected_roi=0.10, confidence=0.8, risk_score=0.1, time_horizon=1.0, required_budget=10.0)
        q.push(d)
        assert q.peek() == d
        assert len(q) == 1

    def test_max_size_eviction(self) -> None:
        q = DecisionQueue(max_size=2)
        for i in range(5):
            q.push(ResourceDecision(str(i), expected_roi=i * 0.01, confidence=0.5, risk_score=0.1, time_horizon=1.0, required_budget=1.0))
        assert len(q) == 2

    def test_list_all_sorted(self) -> None:
        q = DecisionQueue()
        d1 = ResourceDecision("a", expected_roi=0.10, confidence=0.8, risk_score=0.1, time_horizon=1.0, required_budget=10.0)
        d2 = ResourceDecision("b", expected_roi=0.20, confidence=0.8, risk_score=0.1, time_horizon=1.0, required_budget=10.0)
        q.push(d1)
        q.push(d2)
        all_decisions = q.list_all()
        assert all_decisions[0] == d2
        assert all_decisions[1] == d1


# ---------------------------------------------------------------------------
# AutonomousDecisionSystem
# ---------------------------------------------------------------------------


class TestAutonomousDecisionSystem:
    @pytest.mark.asyncio
    async def test_submit_and_publish(self) -> None:
        bus = EventBus()
        received: list[DecisionMade] = []
        await bus.subscribe(DecisionMade, lambda e: received.append(e))

        ads = AutonomousDecisionSystem(event_bus=bus)
        decisions = [
            ResourceDecision("buy BTC", expected_roi=0.05, confidence=0.8, risk_score=0.2, time_horizon=2.0, required_budget=10.0, strategy="trading"),
        ]
        allocations = await ads.submit_decisions(decisions)
        assert len(allocations) == 1
        await asyncio.sleep(0.05)
        assert len(received) == 1
        assert received[0].action == "buy BTC"

    @pytest.mark.asyncio
    async def test_execute_next(self) -> None:
        bus = EventBus()
        ads = AutonomousDecisionSystem(event_bus=bus)
        # Directly push a decision into the queue to test popping
        ads._queue.push(ResourceDecision("buy BTC", expected_roi=0.05, confidence=0.8, risk_score=0.2, time_horizon=2.0, required_budget=10.0, strategy="trading"))
        next_decision = await ads.execute_next()
        assert next_decision is not None
        assert next_decision.action == "buy BTC"
        assert ads.queue_length() == 0

    @pytest.mark.asyncio
    async def test_balance_changed_prudent_flush(self) -> None:
        bus = EventBus()
        ads = AutonomousDecisionSystem(event_bus=bus)
        await ads.start()

        q = ads._queue
        q.push(ResourceDecision("safe", expected_roi=0.05, confidence=0.8, risk_score=0.2, time_horizon=2.0, required_budget=10.0))
        q.push(ResourceDecision("risky", expected_roi=0.20, confidence=0.8, risk_score=0.8, time_horizon=2.0, required_budget=10.0))

        await bus.publish(BalanceChanged, BalanceChanged(old_balance=100.0, new_balance=90.0, reason="loss"))
        await asyncio.sleep(0.05)
        # After a 10% drop, high-risk decisions should be flushed
        assert len(q) == 1
        assert q.peek().action == "safe"

        await ads.stop()


# ---------------------------------------------------------------------------
# ExpansionStrategy
# ---------------------------------------------------------------------------


class TestTradingExpansion:
    def test_generate_opportunities_bullish(self) -> None:
        strat = TradingExpansion()
        opps = strat.generate_opportunities(balance=1_000.0, tier=0, market_data={"momentum": 0.8})
        assert len(opps) == 1
        assert opps[0].opportunity_type == "trade"
        assert opps[0].confidence > 0.0

    def test_generate_opportunities_no_momentum(self) -> None:
        strat = TradingExpansion()
        opps = strat.generate_opportunities(balance=1_000.0, tier=0, market_data={"momentum": 0.0})
        assert len(opps) == 0

    def test_evaluate_performance(self) -> None:
        strat = TradingExpansion()
        strat.record({"return": 10.0, "risk": 0.2})
        strat.record({"return": -5.0, "risk": 0.3})
        perf = strat.evaluate_performance(strat.get_history())
        assert perf.strategy_name == "trading"
        assert perf.total_return == 5.0
        assert perf.win_rate == 0.5

    def test_should_enter_market(self) -> None:
        strat = TradingExpansion()
        strat.activate()
        assert strat.should_enter_market("BTCUSD", balance=100.0, tier=0)
        strat.deactivate()
        assert not strat.should_enter_market("BTCUSD", balance=100.0, tier=0)


class TestSaaSExpansion:
    def test_tier_gate(self) -> None:
        strat = SaaSExpansion()
        assert not strat.generate_opportunities(balance=1_000.0, tier=0)
        opps = strat.generate_opportunities(balance=1_000.0, tier=1)
        assert len(opps) == 1
        assert opps[0].opportunity_type == "product_launch"


class TestArbitrageExpansion:
    def test_spread_gate(self) -> None:
        strat = ArbitrageExpansion()
        assert not strat.generate_opportunities(balance=1_000.0, tier=1, market_data={"spread": 0.0})
        opps = strat.generate_opportunities(balance=1_000.0, tier=1, market_data={"spread": 0.05})
        assert len(opps) == 1
        assert opps[0].opportunity_type == "arbitrage"


class TestContentExpansion:
    def test_always_generates(self) -> None:
        strat = ContentExpansion()
        opps = strat.generate_opportunities(balance=100.0, tier=0)
        assert len(opps) == 1
        assert opps[0].opportunity_type == "content"


# ---------------------------------------------------------------------------
# ExpansionController
# ---------------------------------------------------------------------------


class TestExpansionController:
    def test_register_and_select(self) -> None:
        ctrl = ExpansionController()
        ctrl.register(TradingExpansion())
        ctrl.register(SaaSExpansion())
        active = ctrl.select_strategies(balance=1_000.0, tier=1)
        assert "trading" in active
        assert "saas" in active

    def test_tier_gate_filters(self) -> None:
        ctrl = ExpansionController()
        ctrl.register(SaaSExpansion())
        active = ctrl.select_strategies(balance=1_000.0, tier=0)
        assert "saas" not in active

    def test_performance_gate_demotes_loser(self) -> None:
        ctrl = ExpansionController(performance_lookback=2, win_rate_threshold=0.6)
        strat = TradingExpansion()
        strat.activate()
        ctrl.register(strat)
        # Two losses = win_rate 0.0 < 0.6
        strat.record({"return": -10.0, "risk": 0.5})
        strat.record({"return": -5.0, "risk": 0.5})
        active = ctrl.select_strategies(balance=1_000.0, tier=0)
        assert "trading" not in active

    def test_market_entry_exit(self) -> None:
        ctrl = ExpansionController()
        strat = TradingExpansion()
        ctrl.register(strat)
        strat.activate()
        assert ctrl.enter_market("BTCUSD", "trading", balance=100.0, tier=0)
        assert len(strat.get_history()) == 1
        assert strat.get_history()[0]["event"] == "enter_market"

    @pytest.mark.asyncio
    async def test_strategy_switched_event(self) -> None:
        bus = EventBus()
        received: list[StrategySwitched] = []
        await bus.subscribe(StrategySwitched, lambda e: received.append(e))

        ctrl = ExpansionController(event_bus=bus)
        ctrl.register(TradingExpansion())
        ctrl.register(ContentExpansion())
        # Force switch by starting empty then activating both
        ctrl.select_strategies(balance=1_000.0, tier=0)
        await asyncio.sleep(0.05)
        assert len(received) >= 1


# ---------------------------------------------------------------------------
# NovelStrategyProposer
# ---------------------------------------------------------------------------


class TestNovelStrategyProposer:
    def test_propose_returns_candidates(self) -> None:
        proposer = NovelStrategyProposer(rng_seed=42)
        proposals = proposer.propose(balance=1_000.0, tier=2, existing_strategies=["trading"])
        assert len(proposals) > 0
        assert all("name" in p for p in proposals)
        assert all(p["name"] != "trading" for p in proposals)

    def test_simulate(self) -> None:
        proposer = NovelStrategyProposer(rng_seed=42)
        dna = {"name": "test", "risk": 0.2, "horizon": 24.0, "tier": 0}
        result = proposer.simulate(dna, balance=1_000.0, iterations=100)
        assert "mean_return" in result
        assert "sharpe" in result
        assert result["iterations"] == 100

    def test_history(self) -> None:
        proposer = NovelStrategyProposer()
        proposer.propose(balance=100.0, tier=0, existing_strategies=[])
        assert len(proposer.get_history()) > 0


# ---------------------------------------------------------------------------
# ConsequenceModeler
# ---------------------------------------------------------------------------


class TestConsequenceModeler:
    def test_simulate_distribution(self) -> None:
        modeler = ConsequenceModeler(rng_seed=42)
        decision = ResourceDecision("test", expected_roi=0.05, confidence=0.8, risk_score=0.2, time_horizon=2.0, required_budget=10.0)
        dist = modeler.simulate(decision, iterations=500)
        assert isinstance(dist, OutcomeDistribution)
        assert dist.iterations == 500
        assert dist.worst_case <= dist.mean <= dist.best_case

    def test_zero_budget(self) -> None:
        modeler = ConsequenceModeler(rng_seed=42)
        decision = ResourceDecision("test", expected_roi=0.05, confidence=0.8, risk_score=0.2, time_horizon=2.0, required_budget=0.0)
        dist = modeler.simulate(decision, iterations=100)
        assert dist.mean == 0.0

    @pytest.mark.asyncio
    async def test_publish_simulation(self) -> None:
        bus = EventBus()
        received: list[SimulationCompleted] = []
        await bus.subscribe(SimulationCompleted, lambda e: received.append(e))

        modeler = ConsequenceModeler(event_bus=bus, rng_seed=42)
        decision = ResourceDecision("test", expected_roi=0.05, confidence=0.8, risk_score=0.2, time_horizon=2.0, required_budget=10.0)
        dist = modeler.simulate(decision, iterations=100)
        await modeler.publish_simulation(decision, dist)
        await asyncio.sleep(0.05)
        assert len(received) == 1
        assert received[0].simulation_type == "consequence_model"


# ---------------------------------------------------------------------------
# MonteCarloSimulator
# ---------------------------------------------------------------------------


class TestMonteCarloSimulator:
    def test_run_basic(self) -> None:
        sim = MonteCarloSimulator(rng_seed=42)
        dist = sim.run(model=lambda: 1.0, iterations=100)
        assert dist.mean == 1.0
        assert dist.std == 0.0

    def test_run_parameterised(self) -> None:
        sim = MonteCarloSimulator(rng_seed=42)
        results = sim.run_parameterised(
            model=lambda x: x * 2.0,
            param_sets=[{"x": 1.0}, {"x": 2.0}],
            iterations=50,
        )
        assert len(results) == 2
        assert results[0][1].mean == 2.0
        assert results[1][1].mean == 4.0


# ---------------------------------------------------------------------------
# WorstCaseAnalyzer
# ---------------------------------------------------------------------------


class TestWorstCaseAnalyzer:
    def test_analyze(self) -> None:
        analyzer = WorstCaseAnalyzer()
        decision = ResourceDecision("test", expected_roi=0.05, confidence=0.8, risk_score=0.2, time_horizon=2.0, required_budget=10.0)
        result = analyzer.analyze(decision, balance=1_000.0)
        assert result["expected_loss"] > 0
        assert 0.0 <= result["survival_probability"] <= 1.0
        assert len(result["scenario_results"]) == 5

    def test_is_survivable(self) -> None:
        analyzer = WorstCaseAnalyzer()
        safe = ResourceDecision("safe", expected_roi=0.05, confidence=0.8, risk_score=0.1, time_horizon=2.0, required_budget=1.0)
        assert analyzer.is_survivable(safe, balance=1_000.0)

        risky = ResourceDecision("risky", expected_roi=0.50, confidence=0.8, risk_score=0.9, time_horizon=2.0, required_budget=500.0)
        assert not analyzer.is_survivable(risky, balance=1_000.0)

    def test_zero_balance(self) -> None:
        analyzer = WorstCaseAnalyzer()
        decision = ResourceDecision("test", expected_roi=0.05, confidence=0.8, risk_score=0.2, time_horizon=2.0, required_budget=10.0)
        result = analyzer.analyze(decision, balance=0.0)
        assert result["survival_probability"] == 0.0


# ---------------------------------------------------------------------------
# FreeWillEngine
# ---------------------------------------------------------------------------


class TestFreeWillEngine:
    def test_explore_adds_candidates(self) -> None:
        engine = FreeWillEngine(exploration_rate=1.0, rng_seed=42)
        opps = [Opportunity("o1", "trade", expected_return=10.0, risk_score=0.1, capital_required=5.0, time_horizon_hours=1.0, confidence=0.8)]
        result = engine.explore(opps, balance=100.0, tier=0)
        assert len(result) > len(opps)
        extras = [r for r in result if r.opportunity_type == "exploration"]
        assert len(extras) >= 1

    def test_explore_no_action(self) -> None:
        engine = FreeWillEngine(exploration_rate=0.0, rng_seed=42)
        opps = [Opportunity("o1", "trade", expected_return=10.0, risk_score=0.1, capital_required=5.0, time_horizon_hours=1.0, confidence=0.8)]
        result = engine.explore(opps, balance=100.0, tier=0)
        assert len(result) == len(opps)

    def test_effective_rate_decays(self) -> None:
        engine = FreeWillEngine(exploration_rate=0.20, tier_decay=0.05)
        assert engine.effective_rate(0) == pytest.approx(0.20, abs=0.01)
        assert engine.effective_rate(1) == pytest.approx(0.15, abs=0.01)
        assert engine.effective_rate(10) == pytest.approx(0.01, abs=0.01)


# ---------------------------------------------------------------------------
# SerendipityEngine
# ---------------------------------------------------------------------------


class TestSerendipityEngine:
    def test_evaluate_promotes_low_confidence(self) -> None:
        engine = SerendipityEngine(rng_seed=42)
        decisions = [
            ResourceDecision("low_conf", expected_roi=0.50, confidence=0.2, risk_score=0.3, time_horizon=2.0, required_budget=5.0),
            ResourceDecision("high_conf", expected_roi=0.10, confidence=0.9, risk_score=0.1, time_horizon=2.0, required_budget=5.0),
        ]
        approved = engine.evaluate(decisions, balance=1_000.0)
        # Only the low-confidence candidate is eligible; stochastic gate may let it through
        assert all(a.confidence < 0.30 for a in approved)

    def test_budget_cap(self) -> None:
        engine = SerendipityEngine(max_serendipity_budget_pct=0.01, rng_seed=42)
        decisions = [
            ResourceDecision("big", expected_roi=0.50, confidence=0.2, risk_score=0.3, time_horizon=2.0, required_budget=50.0),
        ]
        approved = engine.evaluate(decisions, balance=100.0)
        # Budget is $1, required is $50 => should be rejected
        assert len(approved) == 0


# ---------------------------------------------------------------------------
# GoalGenerator
# ---------------------------------------------------------------------------


class TestGoalGenerator:
    def test_generates_survival_goal(self) -> None:
        gen = GoalGenerator()
        goals = gen.generate_goals(balance=50.0, tier=0)
        assert any(g.name == "urgent_survival" for g in goals)

    def test_generates_tier_aspiration(self) -> None:
        gen = GoalGenerator()
        goals = gen.generate_goals(balance=50.0, tier=0)
        assert any(g.name == "reach_tier_1" for g in goals)

    def test_diversify_when_win_rate_low(self) -> None:
        gen = GoalGenerator()
        goals = gen.generate_goals(balance=1_000.0, tier=2, recent_performance={"win_rate": 0.2})
        assert any(g.name == "diversify_revenue" for g in goals)

    @pytest.mark.asyncio
    async def test_publish_goal_generated(self) -> None:
        bus = EventBus()
        received: list[GoalGenerated] = []
        await bus.subscribe(GoalGenerated, lambda e: received.append(e))

        gen = GoalGenerator(event_bus=bus)
        gen.generate_goals(balance=50.0, tier=0)
        await asyncio.sleep(0.05)
        assert len(received) >= 1
        assert any(r.goal_name == "urgent_survival" for r in received)

    @pytest.mark.asyncio
    async def test_discover_opportunity(self) -> None:
        bus = EventBus()
        received: list[OpportunityDiscovered] = []
        await bus.subscribe(OpportunityDiscovered, lambda e: received.append(e))

        gen = GoalGenerator(event_bus=bus)
        await gen.discover_opportunity("trading", "BTC dip", estimated_value=100.0, confidence=0.6)
        await asyncio.sleep(0.05)
        assert len(received) == 1
        assert received[0].domain == "trading"
