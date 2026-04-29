"""Decision engine for ÆON — opportunity evaluation, risk assessment, and autonomous resource allocation."""

from __future__ import annotations

import asyncio
import heapq
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from auton.core.config import Capability, TierGate
from auton.core.constants import RISK_LIMITS, SEED_BALANCE
from auton.core.event_bus import EventBus
from auton.core.events import BalanceChanged, DecisionMade
from auton.ledger.master_wallet import MasterWallet

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Opportunity:
    """A scored economic opportunity."""

    id: str
    opportunity_type: str  # e.g. "trade", "product_launch", "compute_upgrade"
    expected_return: float
    risk_score: float  # 0.0 (safe) to 1.0 (extreme)
    capital_required: float
    time_horizon_hours: float
    confidence: float = 0.0  # 0.0 to 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class OpportunityScore:
    """Result of evaluating an opportunity."""

    opportunity_id: str
    total_score: float  # 0.0 to 1.0
    return_score: float
    risk_score: float
    capital_score: float
    time_score: float
    approved: bool
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """Detailed risk evaluation for an opportunity or portfolio."""

    overall_risk: float  # 0.0 to 1.0
    max_drawdown_estimate: float
    liquidity_risk: float
    concentration_risk: float
    tail_risk: float
    tier_limit_breach: bool
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResourceDecision:
    """A concrete resource-allocation decision produced by the autonomous engine.

    Fields:
        action: Human-readable description of the decision.
        expected_roi: Expected return on investment (ratio, e.g. 0.05 = 5%).
        confidence: Model confidence in the ROI estimate (0-1).
        risk_score: Aggregated risk score (0-1, higher = riskier).
        time_horizon: Estimated hours until revenue realisation.
        required_budget: USD required to execute the decision.
        strategy: Strategy category ("trading", "saas", "arbitrage", "content", "api").
    """

    action: str
    expected_roi: float
    confidence: float
    risk_score: float
    time_horizon: float
    required_budget: float
    strategy: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class ResourceAllocation:
    """Budget allocation produced by :class:`ResourceAllocator`."""

    decision_id: str
    amount: float
    score: float
    risk_score: float
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# OpportunityEvaluator
# ---------------------------------------------------------------------------

class OpportunityEvaluator:
    """Scores opportunities across return, risk, capital, and time dimensions.

    The composite score is a weighted product of four sub-scores:

    * **return** — higher expected return is better.
    * **risk** — lower risk is better.
    * **capital** — opportunities requiring less capital are preferred when
      balance is low (tier 0), while larger allocations become acceptable at
      higher tiers.
    * **time** — shorter time horizons are preferred for survival tiers.
    """

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        min_confidence: float = 0.3,
        max_risk_threshold: float = 0.8,
    ) -> None:
        self._weights = weights or {
            "return": 0.35,
            "risk": 0.30,
            "capital": 0.20,
            "time": 0.15,
        }
        total = sum(self._weights.values())
        if total <= 0:
            raise ValueError("weights must sum to > 0")
        self._weights = {k: v / total for k, v in self._weights.items()}
        self._min_confidence = min_confidence
        self._max_risk_threshold = max_risk_threshold

    def evaluate(
        self,
        opportunity: Opportunity,
        balance: float,
        tier: int | None = None,
    ) -> OpportunityScore:
        """Score a single opportunity.

        Args:
            opportunity: The opportunity to evaluate.
            balance: Current ledger balance.
            tier: Operational tier (auto-derived from balance if None).

        Returns:
            An :class:`OpportunityScore` with sub-scores and approval flag.
        """
        resolved_tier = tier if tier is not None else TierGate.get_tier(balance)
        risk_limits = RISK_LIMITS.get(resolved_tier, RISK_LIMITS[0])

        # Confidence gate
        if opportunity.confidence < self._min_confidence:
            return OpportunityScore(
                opportunity_id=opportunity.id,
                total_score=0.0,
                return_score=0.0,
                risk_score=0.0,
                capital_score=0.0,
                time_score=0.0,
                approved=False,
                metadata={"reason": "insufficient_confidence", "threshold": self._min_confidence},
            )

        # Hard risk gate
        if opportunity.risk_score > self._max_risk_threshold:
            return OpportunityScore(
                opportunity_id=opportunity.id,
                total_score=0.0,
                return_score=0.0,
                risk_score=0.0,
                capital_score=0.0,
                time_score=0.0,
                approved=False,
                metadata={"reason": "risk_exceeds_threshold", "threshold": self._max_risk_threshold},
            )

        # Tier capability check
        capability = self._capability_for_type(opportunity.opportunity_type)
        if capability and not TierGate.is_allowed(capability, balance):
            return OpportunityScore(
                opportunity_id=opportunity.id,
                total_score=0.0,
                return_score=0.0,
                risk_score=0.0,
                capital_score=0.0,
                time_score=0.0,
                approved=False,
                metadata={"reason": "tier_gate_denied", "capability": capability.name},
            )

        return_score = self._score_return(opportunity.expected_return, balance, resolved_tier)
        risk_score = self._score_risk(opportunity.risk_score, resolved_tier, risk_limits)
        capital_score = self._score_capital(opportunity.capital_required, balance, resolved_tier, risk_limits)
        time_score = self._score_time(opportunity.time_horizon_hours, resolved_tier)

        total = (
            return_score ** self._weights["return"]
            * risk_score ** self._weights["risk"]
            * capital_score ** self._weights["capital"]
            * time_score ** self._weights["time"]
        )

        # Approval requires total above a tier-dependent floor
        floor = max(0.1, 0.5 - (resolved_tier * 0.05))
        approved = total >= floor and opportunity.risk_score <= risk_limits.get("max_leverage", 10.0) / 10.0

        return OpportunityScore(
            opportunity_id=opportunity.id,
            total_score=round(total, 4),
            return_score=round(return_score, 4),
            risk_score=round(risk_score, 4),
            capital_score=round(capital_score, 4),
            time_score=round(time_score, 4),
            approved=approved,
            metadata={
                "floor": floor,
                "tier": resolved_tier,
                "capability": capability.name if capability else None,
            },
        )

    def evaluate_batch(
        self,
        opportunities: list[Opportunity],
        balance: float,
        tier: int | None = None,
    ) -> list[OpportunityScore]:
        """Evaluate a batch of opportunities."""
        return [self.evaluate(opp, balance, tier) for opp in opportunities]

    def rank(self, scores: list[OpportunityScore]) -> list[OpportunityScore]:
        """Return scores sorted by total_score descending."""
        return sorted(scores, key=lambda s: s.total_score, reverse=True)

    @staticmethod
    def _capability_for_type(opp_type: str) -> Capability | None:
        mapping: dict[str, Capability] = {
            "trade": Capability.SPOT_TRADING,
            "futures_trade": Capability.FUTURES_TRADING,
            "product_launch": Capability.SAAS_HOSTING,
            "compute_upgrade": Capability.SPOT_COMPUTE_SCALING,
            "hire_contractor": Capability.HIRE_CONTRACTORS,
            "arbitrage": Capability.MULTI_EXCHANGE_ARBITRAGE,
            "backtest": Capability.STRATEGY_BACKTESTING,
            "sentiment_feed": Capability.SENTIMENT_FEEDS,
            "on_chain": Capability.ON_CHAIN_DATA,
            "equities": Capability.EQUITIES,
            "forex": Capability.FOREX,
            "options": Capability.OPTIONS,
            "cross_border_arbitrage": Capability.CROSS_BORDER_ARBITRAGE,
            "custom_algorithm": Capability.CUSTOM_ALGORITHM_DEPLOYMENT,
            "high_frequency_data": Capability.HIGH_FREQUENCY_DATA,
            "external_ai_agents": Capability.EXTERNAL_AI_AGENTS,
            "venture_reinvestment": Capability.VENTURE_REINVESTMENT,
            "legal_entity": Capability.LEGAL_ENTITY_FORMATION,
        }
        return mapping.get(opp_type)

    @staticmethod
    def _score_return(expected_return: float, balance: float, tier: int) -> float:
        """Return a score (0-1) where higher expected return is better."""
        if balance <= 0:
            return 0.0
        ratio = expected_return / balance
        # At higher tiers, larger absolute returns are expected
        base = min(1.0, ratio * (10.0 + tier * 2))
        return max(0.0, base)

    @staticmethod
    def _score_risk(risk_score: float, tier: int, risk_limits: dict[str, float]) -> float:
        """Return a score (0-1) where lower risk is better."""
        # Map risk_score into a penalty curve
        max_leverage = risk_limits.get("max_leverage", 1.0)
        # Higher leverage tolerance means slightly more risk acceptance
        tolerance = 0.3 + (max_leverage / 10.0) * 0.4
        penalty = min(1.0, risk_score / tolerance) if tolerance > 0 else 1.0
        return max(0.0, 1.0 - penalty ** 2)

    @staticmethod
    def _score_capital(
        capital_required: float,
        balance: float,
        tier: int,
        risk_limits: dict[str, float],
    ) -> float:
        """Return a score (0-1) where capital fit is evaluated."""
        if balance <= 0:
            return 0.0
        max_position_pct = risk_limits.get("max_position_pct", 0.02)
        deployable = balance * (1.0 - risk_limits.get("survival_reserve_pct", 0.10))
        max_allowed = deployable * max_position_pct
        if capital_required <= max_allowed:
            # Preferred: uses most of allowed without exceeding
            return 0.5 + 0.5 * (capital_required / max_allowed)
        if capital_required <= deployable:
            # Tolerable but suboptimal
            return 0.3 * (1.0 - (capital_required - max_allowed) / (deployable - max_allowed))
        return 0.0

    @staticmethod
    def _score_time(time_horizon_hours: float, tier: int) -> float:
        """Return a score (0-1) where shorter horizons are preferred at low tiers."""
        if time_horizon_hours <= 0:
            return 1.0
        # Tier 0: strongly prefer < 24h; Tier 5: comfortable with multi-week
        preferred = 24.0 * (1.0 + tier * 0.5)
        return max(0.0, 1.0 - (time_horizon_hours / (preferred * 3.0)))


# ---------------------------------------------------------------------------
# RiskEngine
# ---------------------------------------------------------------------------

class RiskEngine:
    """Evaluates and aggregates risk for individual opportunities and portfolios.

    The engine uses tier-specific limits from :data:`RISK_LIMITS` and
    computes concentration, liquidity, and tail-risk estimates.
    """

    def __init__(self, tail_risk_alpha: float = 0.05) -> None:
        self._tail_risk_alpha = tail_risk_alpha

    def assess(
        self,
        opportunity: Opportunity,
        balance: float,
        tier: int | None = None,
    ) -> RiskAssessment:
        """Assess risk for a single opportunity."""
        resolved_tier = tier if tier is not None else TierGate.get_tier(balance)
        risk_limits = RISK_LIMITS.get(resolved_tier, RISK_LIMITS[0])

        max_position_pct = risk_limits.get("max_position_pct", 0.02)
        survival_reserve_pct = risk_limits.get("survival_reserve_pct", 0.10)
        deployable = balance * (1.0 - survival_reserve_pct)
        max_allowed = deployable * max_position_pct

        concentration_risk = 0.0
        if deployable > 0:
            concentration_risk = min(1.0, opportunity.capital_required / deployable)

        liquidity_risk = min(1.0, opportunity.capital_required / max(balance, 1.0))

        # Estimate max drawdown using a simple VaR-like heuristic
        max_drawdown = opportunity.risk_score * (opportunity.capital_required / max(balance, 1.0))

        # Tail risk decays with tier (higher tiers have more buffers)
        tail_risk = opportunity.risk_score * (1.0 - resolved_tier * 0.12)
        tail_risk = max(0.0, min(1.0, tail_risk))

        tier_limit_breach = opportunity.capital_required > max_allowed

        overall = min(1.0, (concentration_risk * 0.35 + liquidity_risk * 0.25 + tail_risk * 0.25 + max_drawdown * 0.15))

        return RiskAssessment(
            overall_risk=round(overall, 4),
            max_drawdown_estimate=round(max_drawdown, 4),
            liquidity_risk=round(liquidity_risk, 4),
            concentration_risk=round(concentration_risk, 4),
            tail_risk=round(tail_risk, 4),
            tier_limit_breach=tier_limit_breach,
            metadata={
                "tier": resolved_tier,
                "max_allowed": round(max_allowed, 4),
                "deployable": round(deployable, 4),
            },
        )

    def assess_portfolio(
        self,
        opportunities: list[Opportunity],
        balance: float,
        tier: int | None = None,
    ) -> RiskAssessment:
        """Aggregate risk across a portfolio of opportunities."""
        resolved_tier = tier if tier is not None else TierGate.get_tier(balance)
        risk_limits = RISK_LIMITS.get(resolved_tier, RISK_LIMITS[0])

        survival_reserve_pct = risk_limits.get("survival_reserve_pct", 0.10)
        deployable = balance * (1.0 - survival_reserve_pct)
        total_capital = sum(o.capital_required for o in opportunities)

        concentration_risk = min(1.0, total_capital / deployable) if deployable > 0 else 0.0
        liquidity_risk = min(1.0, total_capital / max(balance, 1.0))

        # Weighted average risk score
        avg_risk = (
            sum(o.risk_score * o.capital_required for o in opportunities) / total_capital
            if total_capital > 0 else 0.0
        )

        max_drawdown = avg_risk * (total_capital / max(balance, 1.0))
        tail_risk = max(0.0, min(1.0, avg_risk * (1.0 - resolved_tier * 0.12)))

        max_position_pct = risk_limits.get("max_position_pct", 0.02)
        max_single = deployable * max_position_pct
        tier_limit_breach = any(o.capital_required > max_single for o in opportunities)

        overall = min(1.0, (concentration_risk * 0.35 + liquidity_risk * 0.25 + tail_risk * 0.25 + max_drawdown * 0.15))

        return RiskAssessment(
            overall_risk=round(overall, 4),
            max_drawdown_estimate=round(max_drawdown, 4),
            liquidity_risk=round(liquidity_risk, 4),
            concentration_risk=round(concentration_risk, 4),
            tail_risk=round(tail_risk, 4),
            tier_limit_breach=tier_limit_breach,
            metadata={
                "tier": resolved_tier,
                "opportunity_count": len(opportunities),
                "total_capital": round(total_capital, 4),
                "avg_risk": round(avg_risk, 4),
            },
        )

    def within_limits(
        self,
        opportunity: Opportunity,
        balance: float,
        tier: int | None = None,
    ) -> bool:
        """Quick check whether the opportunity respects tier limits."""
        assessment = self.assess(opportunity, balance, tier)
        return not assessment.tier_limit_breach and assessment.overall_risk < 0.7


# ---------------------------------------------------------------------------
# MultiObjectiveOptimizer
# ---------------------------------------------------------------------------

class MultiObjectiveOptimizer:
    """Pareto-aware multi-objective optimizer for resource decisions.

    Optimises across three objectives:
    1. **Maximise profit** (expected ROI)
    2. **Minimise risk** (risk_score)
    3. **Minimise time-to-revenue** (time_horizon)
    """

    def __init__(
        self,
        profit_weight: float = 0.40,
        risk_weight: float = 0.35,
        time_weight: float = 0.25,
    ) -> None:
        total = profit_weight + risk_weight + time_weight
        if total <= 0:
            raise ValueError("weights must sum to > 0")
        self._profit_weight = profit_weight / total
        self._risk_weight = risk_weight / total
        self._time_weight = time_weight / total

    def optimise(
        self,
        decisions: list[ResourceDecision],
    ) -> list[tuple[ResourceDecision, float]]:
        """Score each decision and return them sorted by composite score descending.

        The composite score is a weighted sum of normalised profit, inverted
        risk, and inverted time horizon.
        """
        if not decisions:
            return []

        max_roi = max(d.expected_roi for d in decisions) or 1.0
        max_risk = max(d.risk_score for d in decisions) or 1.0
        max_time = max(d.time_horizon for d in decisions) or 1.0

        scored: list[tuple[ResourceDecision, float]] = []
        for d in decisions:
            profit_score = d.expected_roi / max_roi
            risk_score = 1.0 - (d.risk_score / max_risk) if max_risk > 0 else 1.0
            time_score = 1.0 - (d.time_horizon / max_time) if max_time > 0 else 1.0

            composite = (
                profit_score * self._profit_weight
                + risk_score * self._risk_weight
                + time_score * self._time_weight
            )
            scored.append((d, round(composite, 4)))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def pareto_frontier(
        self,
        decisions: list[ResourceDecision],
    ) -> list[ResourceDecision]:
        """Return the non-dominated subset of decisions.

        A decision dominates another when it is better or equal on all
        three objectives and strictly better on at least one.
        """
        if not decisions:
            return []

        frontier: list[ResourceDecision] = []
        for d in decisions:
            dominated = False
            for other in decisions:
                if other is d:
                    continue
                if (
                    other.expected_roi >= d.expected_roi
                    and other.risk_score <= d.risk_score
                    and other.time_horizon <= d.time_horizon
                    and (
                        other.expected_roi > d.expected_roi
                        or other.risk_score < d.risk_score
                        or other.time_horizon < d.time_horizon
                    )
                ):
                    dominated = True
                    break
            if not dominated:
                frontier.append(d)
        return frontier


# ---------------------------------------------------------------------------
# ResourceAllocator
# ---------------------------------------------------------------------------

class ResourceAllocator:
    """Distributes budget across strategies while respecting tier limits.

    Strategies are weighted by their composite scores from the
    :class:`MultiObjectiveOptimizer`.  The allocator respects survival
    reserves and per-strategy position caps.
    """

    def __init__(
        self,
        survival_reserve_pct: float = 0.10,
        max_position_pct: float | None = None,
        min_allocation: float = 1.0,
    ) -> None:
        self._survival_reserve_pct = survival_reserve_pct
        self._max_position_pct = max_position_pct
        self._min_allocation = min_allocation
        self._history: list[dict[str, Any]] = []

    def allocate(
        self,
        balance: float,
        decisions: list[tuple[ResourceDecision, float]],
    ) -> list[ResourceAllocation]:
        """Produce a diversified capital allocation across strategies.

        Args:
            balance: Current ledger balance.
            decisions: List of (ResourceDecision, score) tuples from the optimiser.

        Returns:
            A list of :class:`ResourceAllocation` objects keyed by strategy.
        """
        if not decisions:
            return []

        total_score = sum(score for _, score in decisions)
        if total_score <= 0:
            return []

        deployable = balance * (1.0 - self._survival_reserve_pct)
        if deployable <= 0:
            return []

        # Determine max_position_pct from tier if not overridden
        max_position_pct = self._max_position_pct
        if max_position_pct is None:
            tier = TierGate.get_tier(balance)
            limits = RISK_LIMITS.get(tier, RISK_LIMITS[0])
            max_position_pct = limits["max_position_pct"]

        max_single = deployable * max_position_pct
        allocations: list[ResourceAllocation] = []

        for decision, score in decisions:
            raw = (score / total_score) * deployable
            capped = min(raw, max_single)
            # Respect per-decision budget cap if set in metadata
            per_decision_max = decision.metadata.get("max_allocation")
            if per_decision_max is not None:
                capped = min(capped, float(per_decision_max))
            amount = round(max(0.0, capped), 4)
            if amount < self._min_allocation:
                amount = 0.0
            allocations.append(ResourceAllocation(
                decision_id=decision.action,
                amount=amount,
                score=round(score, 4),
                risk_score=round(decision.risk_score, 4),
                metadata={
                    "strategy": decision.strategy,
                    "max_single": round(max_single, 4),
                    "deployable": round(deployable, 4),
                    "expected_roi": decision.expected_roi,
                    **decision.metadata,
                },
            ))

        self._history.append({
            "balance": balance,
            "deployable": round(deployable, 4),
            "count": len(allocations),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return allocations

    def get_history(self) -> list[dict[str, Any]]:
        return list(self._history)

    def total_allocated(self, allocations: list[ResourceAllocation]) -> float:
        """Sum of allocated amounts."""
        return round(sum(a.amount for a in allocations), 4)

    def reserve_amount(self, balance: float) -> float:
        """Amount kept in survival reserve."""
        return round(balance * self._survival_reserve_pct, 4)


# ---------------------------------------------------------------------------
# DecisionQueue
# ---------------------------------------------------------------------------

class DecisionQueue:
    """Priority queue of :class:`ResourceDecision` sorted by risk-adjusted return.

    Items are ranked by ``expected_roi / (risk_score + epsilon)`` so that
    high-return, low-risk actions are executed first.
    """

    def __init__(self, max_size: int = 100) -> None:
        self._max_size = max_size
        self._heap: list[tuple[float, int, ResourceDecision]] = []
        self._counter = 0
        self._history: list[dict[str, Any]] = []

    def _priority(self, decision: ResourceDecision) -> float:
        """Higher priority = executed first."""
        return decision.expected_roi / max(decision.risk_score, 0.001)

    def push(self, decision: ResourceDecision) -> None:
        """Add a decision to the queue."""
        priority = -self._priority(decision)  # min-heap => negate for descending
        self._counter += 1
        heapq.heappush(self._heap, (priority, self._counter, decision))
        if len(self._heap) > self._max_size:
            # Evict lowest-priority item
            self._heap = heapq.nsmallest(self._max_size, self._heap)
            heapq.heapify(self._heap)

    def pop(self) -> ResourceDecision | None:
        """Remove and return the highest-priority decision."""
        if not self._heap:
            return None
        _, _, decision = heapq.heappop(self._heap)
        self._history.append({
            "action": decision.action,
            "strategy": decision.strategy,
            "expected_roi": decision.expected_roi,
            "risk_score": decision.risk_score,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return decision

    def peek(self) -> ResourceDecision | None:
        """Return the highest-priority decision without removing it."""
        if not self._heap:
            return None
        return self._heap[0][2]

    def __len__(self) -> int:
        return len(self._heap)

    def list_all(self) -> list[ResourceDecision]:
        """Return all queued decisions sorted by priority (highest first)."""
        return [d for _, _, d in sorted(self._heap, key=lambda x: x[0])]

    def get_history(self) -> list[dict[str, Any]]:
        return list(self._history)


# ---------------------------------------------------------------------------
# AutonomousDecisionSystem
# ---------------------------------------------------------------------------

class AutonomousDecisionSystem:
    """Coordinator that wires the optimiser, allocator, and queue to the event bus and ledger.

    Subscribes to :class:`BalanceChanged` events for real-time feedback and
    publishes :class:`DecisionMade` events whenever a concrete decision is
    committed.
    """

    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        ledger: MasterWallet | None = None,
        optimiser: MultiObjectiveOptimizer | None = None,
        allocator: ResourceAllocator | None = None,
        queue: DecisionQueue | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._ledger = ledger
        self._optimiser = optimiser or MultiObjectiveOptimizer()
        self._allocator = allocator or ResourceAllocator()
        self._queue = queue or DecisionQueue()
        self._subscribed = False

    async def start(self) -> None:
        """Subscribe to ledger events if an event bus is available."""
        if self._event_bus is not None and not self._subscribed:
            await self._event_bus.subscribe(BalanceChanged, self._on_balance_changed)
            self._subscribed = True

    async def stop(self) -> None:
        """Unsubscribe from ledger events."""
        if self._event_bus is not None and self._subscribed:
            await self._event_bus.unsubscribe(BalanceChanged, self._on_balance_changed)
            self._subscribed = False

    async def _on_balance_changed(self, event: BalanceChanged) -> None:
        """React to balance changes by re-evaluating the decision queue."""
        logger.info(
            "AutonomousDecisionSystem: balance changed %.4f -> %.4f (%s)",
            event.old_balance,
            event.new_balance,
            event.reason,
        )
        # If balance dropped significantly, flush low-confidence decisions
        if event.new_balance < event.old_balance * 0.95:
            self._prudent_flush()

    def _prudent_flush(self) -> None:
        """Remove high-risk decisions from the queue when capital shrinks."""
        kept: list[tuple[float, int, ResourceDecision]] = []
        for priority, counter, decision in self._queue._heap:
            if decision.risk_score < 0.5:
                kept.append((priority, counter, decision))
        self._queue._heap = kept
        heapq.heapify(self._queue._heap)

    async def submit_decisions(self, decisions: list[ResourceDecision]) -> list[ResourceAllocation]:
        """Optimise, allocate, and enqueue a batch of decisions.

        Returns the produced allocations.
        """
        # 1. Multi-objective optimisation
        scored = self._optimiser.optimise(decisions)

        # 2. Budget allocation
        balance = self._ledger.get_balance() if self._ledger else SEED_BALANCE
        allocations = self._allocator.allocate(balance, scored)

        # 3. Enqueue funded decisions
        for decision, score in scored:
            # Only enqueue if we have an allocation for it
            if any(a.decision_id == decision.action for a in allocations if a.amount > 0):
                decision_with_amount = ResourceDecision(
                    action=decision.action,
                    expected_roi=decision.expected_roi,
                    confidence=decision.confidence,
                    risk_score=decision.risk_score,
                    time_horizon=decision.time_horizon,
                    required_budget=next(
                        (a.amount for a in allocations if a.decision_id == decision.action),
                        decision.required_budget,
                    ),
                    strategy=decision.strategy,
                    metadata={**decision.metadata, "optimiser_score": score},
                )
                self._queue.push(decision_with_amount)

        # 4. Publish DecisionMade events
        if self._event_bus is not None:
            for decision, _ in scored:
                await self._event_bus.publish(
                    DecisionMade,
                    DecisionMade(
                        action=decision.action,
                        expected_roi=decision.expected_roi,
                        confidence=decision.confidence,
                        risk_score=decision.risk_score,
                        required_budget=decision.required_budget,
                        strategy=decision.strategy,
                        metadata=decision.metadata,
                    ),
                )

        return allocations

    async def execute_next(self) -> ResourceDecision | None:
        """Pop and return the highest-priority decision from the queue."""
        decision = self._queue.pop()
        if decision is not None:
            logger.info(
                "AutonomousDecisionSystem: executing decision %s (strategy=%s, roi=%.4f, risk=%.4f)",
                decision.action,
                decision.strategy,
                decision.expected_roi,
                decision.risk_score,
            )
        return decision

    def queue_length(self) -> int:
        return len(self._queue)


