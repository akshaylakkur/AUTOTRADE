"""Expansionism framework for ÆON — wealth tiers, capital allocation, capabilities, goals, and autonomous strategy expansion."""

from __future__ import annotations

import asyncio
import json
import random
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from auton.core.config import Capability, TierGate
from auton.core.constants import RISK_LIMITS, SEED_BALANCE
from auton.core.event_bus import EventBus
from auton.core.events import StrategySwitched
from auton.cortex.decision_engine import Opportunity


# ---------------------------------------------------------------------------
# Expansionist tier thresholds (extends core TIER_THRESHOLDS)
# ---------------------------------------------------------------------------

EXPANSION_TIER_THRESHOLDS: dict[int, float] = {
    0: 50.0,
    1: 500.0,
    2: 1_000.0,
    3: 10_000.0,
    4: 100_000.0,
    5: 1_000_000.0,
}

EXPANSION_TIER_CAPABILITIES: dict[int, list[Capability]] = {
    0: [Capability.SPOT_TRADING, Capability.FREELANCE_TASKS, Capability.NEWSLETTER_SUBSCRIPTIONS],
    1: [Capability.FUTURES_TRADING, Capability.MULTI_EXCHANGE_ARBITRAGE, Capability.ON_CHAIN_DATA, Capability.SAAS_HOSTING],
    2: [Capability.EQUITIES, Capability.SENTIMENT_FEEDS, Capability.DEEP_REASONING, Capability.SOFTWARE_LICENSING],
    3: [Capability.FOREX, Capability.OPTIONS, Capability.STRATEGY_BACKTESTING, Capability.SPOT_COMPUTE_SCALING, Capability.HIRE_CONTRACTORS],
    4: [Capability.CROSS_BORDER_ARBITRAGE, Capability.CUSTOM_ALGORITHM_DEPLOYMENT, Capability.HIGH_FREQUENCY_DATA, Capability.EXTERNAL_AI_AGENTS, Capability.VENTURE_REINVESTMENT],
    5: [Capability.LEGAL_ENTITY_FORMATION],
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Milestone:
    """A single milestone with a target and current progress."""

    name: str
    target_value: float
    current_value: float = 0.0
    unit: str = "USD"
    completed: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Goal:
    """A goal composed of one or more milestones."""

    name: str
    description: str
    milestones: list[Milestone] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Allocation:
    """Capital allocation for a single opportunity."""

    opportunity_id: str
    amount: float
    score: float
    risk_score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StrategyPerformance:
    """Performance snapshot for an expansion strategy."""

    strategy_name: str
    total_return: float
    avg_risk: float
    win_rate: float
    trades_count: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# WealthTierManager
# ---------------------------------------------------------------------------

class WealthTierManager:
    """Manages expansionist wealth tiers and capability unlocking.

    Tiers are defined at $50, $500, $1K, $10K, $100K, and $1M.
    Each tier unlocks new capabilities while preserving existing ones.
    """

    def __init__(self, thresholds: dict[int, float] | None = None) -> None:
        self._thresholds = thresholds or EXPANSION_TIER_THRESHOLDS.copy()
        self._capabilities_by_tier = EXPANSION_TIER_CAPABILITIES.copy()
        self._current_tier: int = 0
        self._history: list[dict[str, Any]] = []

    def get_tier(self, balance: float) -> int:
        """Return the highest tier unlocked by the given balance."""
        tier = -1
        for t, threshold in sorted(self._thresholds.items()):
            if balance >= threshold:
                tier = t
            else:
                break
        return max(tier, 0)

    def update(self, balance: float) -> dict[str, Any]:
        """Re-evaluate tier for a new balance and record changes.

        Returns:
            A dict with ``old_tier``, ``new_tier``, ``unlocked``, and ``locked``.
        """
        old_tier = self._current_tier
        new_tier = self.get_tier(balance)
        self._current_tier = new_tier

        old_caps = set(self.capabilities_for_tier(old_tier))
        new_caps = set(self.capabilities_for_tier(new_tier))

        record = {
            "old_tier": old_tier,
            "new_tier": new_tier,
            "balance": balance,
            "unlocked": sorted([c.name for c in (new_caps - old_caps)]),
            "locked": sorted([c.name for c in (old_caps - new_caps)]),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._history.append(record)
        return record

    def capabilities_for_tier(self, tier: int) -> list[Capability]:
        """Return all capabilities available at the given tier (cumulative)."""
        caps: set[Capability] = set()
        for t in range(tier + 1):
            caps.update(self._capabilities_by_tier.get(t, []))
        return sorted(caps, key=lambda c: c.name)

    def unlocked_capabilities(self, balance: float) -> list[Capability]:
        """Return capabilities unlocked for the current balance."""
        return self.capabilities_for_tier(self.get_tier(balance))

    def is_unlocked(self, capability: Capability, balance: float) -> bool:
        """Check whether a capability is unlocked at the given balance."""
        return capability in self.unlocked_capabilities(balance)

    def next_threshold(self, balance: float) -> float | None:
        """Return the balance needed to reach the next tier, or None at max."""
        current = self.get_tier(balance)
        next_tier = current + 1
        return self._thresholds.get(next_tier)

    def progress_to_next(self, balance: float) -> float:
        """Return fraction (0.0-1.0) of progress toward the next tier."""
        current_tier = self.get_tier(balance)
        current_threshold = self._thresholds.get(current_tier, SEED_BALANCE)
        next_threshold = self._thresholds.get(current_tier + 1)
        if next_threshold is None:
            return 1.0
        if next_threshold <= current_threshold:
            return 1.0
        return min(1.0, (balance - current_threshold) / (next_threshold - current_threshold))

    def get_history(self) -> list[dict[str, Any]]:
        """Return recorded tier transitions."""
        return list(self._history)

    @property
    def current_tier(self) -> int:
        return self._current_tier


# ---------------------------------------------------------------------------
# CapabilityRegistry
# ---------------------------------------------------------------------------

class CapabilityRegistry:
    """JSON-backed registry of available and required capabilities.

    The registry tracks which capabilities are *available* (the system could
    perform them) and which are *active* (currently enabled).  It persists
    to a JSON file so that state survives restarts.
    """

    def __init__(self, path: str | Path = "data/capability_registry.json") -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._available: set[str] = set()
        self._active: set[str] = set()
        self._requirements: dict[str, list[str]] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            self._available = set(data.get("available", []))
            self._active = set(data.get("active", []))
            self._requirements = data.get("requirements", {})
        except (json.JSONDecodeError, OSError):
            self._available = set()
            self._active = set()
            self._requirements = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "available": sorted(self._available),
            "active": sorted(self._active),
            "requirements": self._requirements,
        }
        self._path.write_text(json.dumps(payload, indent=2))

    def register(self, name: str, requires: list[str] | None = None) -> None:
        """Register a capability as available, with optional dependency names."""
        with self._lock:
            self._available.add(name)
            if requires:
                self._requirements[name] = list(requires)
            self._save()

    def activate(self, name: str) -> bool:
        """Activate a capability if it is available and dependencies are met."""
        with self._lock:
            if name not in self._available:
                return False
            deps = self._requirements.get(name, [])
            if not all(d in self._active for d in deps):
                return False
            self._active.add(name)
            self._save()
            return True

    def deactivate(self, name: str) -> None:
        """Deactivate a capability (does not remove from available)."""
        with self._lock:
            self._active.discard(name)
            # Cascade deactivation of dependents
            for cap, deps in list(self._requirements.items()):
                if name in deps and cap in self._active:
                    self._active.discard(cap)
            self._save()

    def is_available(self, name: str) -> bool:
        return name in self._available

    def is_active(self, name: str) -> bool:
        return name in self._active

    def list_available(self) -> list[str]:
        return sorted(self._available)

    def list_active(self) -> list[str]:
        return sorted(self._active)

    def missing_requirements(self, name: str) -> list[str]:
        """Return unmet dependencies for a capability."""
        if name not in self._available:
            return []
        return [d for d in self._requirements.get(name, []) if d not in self._active]

    def bulk_sync_with_tiers(self, capabilities: list[Capability]) -> dict[str, Any]:
        """Synchronise registry with a list of unlocked Capability enums.

        Returns a summary of changes.
        """
        with self._lock:
            activated: list[str] = []
            deactivated: list[str] = []
            cap_names = {c.name for c in capabilities}
            for cap_name in self._available:
                if cap_name in cap_names and cap_name not in self._active:
                    self._active.add(cap_name)
                    activated.append(cap_name)
                elif cap_name not in cap_names and cap_name in self._active:
                    self._active.discard(cap_name)
                    deactivated.append(cap_name)
            self._save()
            return {"activated": activated, "deactivated": deactivated}


# ---------------------------------------------------------------------------
# GoalPlanner
# ---------------------------------------------------------------------------

class GoalPlanner:
    """Tracks milestones and progress toward strategic goals."""

    def __init__(self) -> None:
        self._goals: dict[str, Goal] = {}
        self._history: list[dict[str, Any]] = []

    def add_goal(self, goal: Goal) -> None:
        """Add a new goal."""
        self._goals[goal.name] = goal

    def update_milestone(self, goal_name: str, milestone_name: str, value: float) -> Milestone | None:
        """Update a milestone's current value and recompute completion.

        Returns the updated milestone, or None if not found.
        """
        goal = self._goals.get(goal_name)
        if goal is None:
            return None

        new_milestones: list[Milestone] = []
        updated: Milestone | None = None
        for ms in goal.milestones:
            if ms.name == milestone_name:
                completed = value >= ms.target_value
                completed_at = datetime.now(timezone.utc) if completed and not ms.completed else ms.completed_at
                updated = Milestone(
                    name=ms.name,
                    target_value=ms.target_value,
                    current_value=value,
                    unit=ms.unit,
                    completed=completed,
                    created_at=ms.created_at,
                    completed_at=completed_at,
                )
                new_milestones.append(updated)
            else:
                new_milestones.append(ms)

        self._goals[goal_name] = Goal(
            name=goal.name,
            description=goal.description,
            milestones=new_milestones,
            created_at=goal.created_at,
            metadata=goal.metadata,
        )

        if updated:
            self._history.append({
                "goal": goal_name,
                "milestone": milestone_name,
                "value": value,
                "completed": updated.completed,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        return updated

    def goal_progress(self, goal_name: str) -> float:
        """Return overall goal progress as a fraction (0.0-1.0)."""
        goal = self._goals.get(goal_name)
        if not goal or not goal.milestones:
            return 0.0
        total = sum(
            min(1.0, ms.current_value / ms.target_value) if ms.target_value > 0 else 1.0
            for ms in goal.milestones
        )
        return round(total / len(goal.milestones), 4)

    def is_goal_complete(self, goal_name: str) -> bool:
        """Return True if every milestone in the goal is completed."""
        goal = self._goals.get(goal_name)
        if not goal or not goal.milestones:
            return False
        return all(ms.completed for ms in goal.milestones)

    def list_goals(self) -> list[str]:
        return list(self._goals.keys())

    def get_goal(self, goal_name: str) -> Goal | None:
        return self._goals.get(goal_name)

    def get_history(self) -> list[dict[str, Any]]:
        return list(self._history)

    def suggest_goals(self, tier: int, balance: float) -> list[Goal]:
        """Generate default goals for a given tier and balance."""
        goals: list[Goal] = []
        if tier == 0:
            goals.append(Goal(
                name="survival",
                description="Preserve capital and reach tier 1",
                milestones=[
                    Milestone(name="balance_500", target_value=500.0, current_value=balance),
                    Milestone(name="no_major_drawdown", target_value=1.0, current_value=0.0),
                ],
            ))
        elif tier == 1:
            goals.append(Goal(
                name="stabilisation",
                description="Stabilise profit and diversify revenue",
                milestones=[
                    Milestone(name="balance_1k", target_value=1_000.0, current_value=balance),
                    Milestone(name="revenue_streams", target_value=2.0, current_value=0.0),
                ],
            ))
        elif tier == 2:
            goals.append(Goal(
                name="scaling",
                description="Scale operations and deepen alpha",
                milestones=[
                    Milestone(name="balance_10k", target_value=10_000.0, current_value=balance),
                    Milestone(name="backtests_run", target_value=100.0, current_value=0.0, unit="count"),
                ],
            ))
        elif tier == 3:
            goals.append(Goal(
                name="professionalisation",
                description="Hire contractors and deploy custom strategies",
                milestones=[
                    Milestone(name="balance_100k", target_value=100_000.0, current_value=balance),
                    Milestone(name="contractors_hired", target_value=1.0, current_value=0.0, unit="count"),
                ],
            ))
        elif tier >= 4:
            goals.append(Goal(
                name="dominance",
                description="Venture reinvestment and external AI agents",
                milestones=[
                    Milestone(name="balance_1m", target_value=1_000_000.0, current_value=balance),
                    Milestone(name="ai_agents_deployed", target_value=3.0, current_value=0.0, unit="count"),
                ],
            ))
        return goals


# ---------------------------------------------------------------------------
# CapitalAllocator
# ---------------------------------------------------------------------------

class CapitalAllocator:
    """Diversifies capital across scored opportunities while respecting risk limits.

    Allocations are computed as a proportion of *deployable* capital, which is
    ``balance * (1 - survival_reserve_pct)``.  No single opportunity may receive
    more than ``max_position_pct`` of deployable capital.
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
        opportunities: list[dict[str, Any]],
        scores: list[float] | None = None,
    ) -> list[Allocation]:
        """Produce a diversified capital allocation.

        Args:
            balance: Current ledger balance.
            opportunities: List of opportunity dicts.  Each must contain an
                ``id`` key.  Optional keys: ``risk_score`` (0-1),
                ``max_allocation`` (float).
            scores: Optional parallel list of opportunity scores (0-1).  If
                omitted, extracted from ``opportunity["score"]``.

        Returns:
            A list of :class:`Allocation` objects.
        """
        if not opportunities:
            return []

        scores = scores or [o.get("score", 0.0) for o in opportunities]
        if len(scores) != len(opportunities):
            raise ValueError("scores must match opportunities length")

        total_score = sum(scores)
        if total_score <= 0:
            return []

        deployable = balance * (1.0 - self._survival_reserve_pct)
        if deployable <= 0:
            return []

        # Determine max_position_pct from tier if not overridden
        max_position_pct = self._max_position_pct
        if max_position_pct is None:
            from auton.core.config import TierGate
            tier = TierGate.get_tier(balance)
            limits = RISK_LIMITS.get(tier, RISK_LIMITS[0])
            max_position_pct = limits["max_position_pct"]

        max_single = deployable * max_position_pct
        allocations: list[Allocation] = []

        for opp, score in zip(opportunities, scores):
            opp_id = opp.get("id", "unknown")
            risk_score = opp.get("risk_score", 0.5)
            raw = (score / total_score) * deployable
            capped = min(raw, max_single)
            # Respect per-opportunity max_allocation if provided
            per_opp_max = opp.get("max_allocation")
            if per_opp_max is not None:
                capped = min(capped, per_opp_max)
            amount = round(max(0.0, capped), 4)
            if amount < self._min_allocation:
                amount = 0.0
            allocations.append(Allocation(
                opportunity_id=opp_id,
                amount=amount,
                score=round(score, 4),
                risk_score=round(risk_score, 4),
                metadata={"max_single": round(max_single, 4), "deployable": round(deployable, 4)},
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

    def total_allocated(self, allocations: Iterable[Allocation]) -> float:
        """Sum of allocated amounts."""
        return round(sum(a.amount for a in allocations), 4)

    def reserve_amount(self, balance: float) -> float:
        """Amount kept in survival reserve."""
        return round(balance * self._survival_reserve_pct, 4)


# ---------------------------------------------------------------------------
# ExpansionStrategy
# ---------------------------------------------------------------------------

class ExpansionStrategy(ABC):
    """Base class for autonomous expansion strategies.

    Each concrete strategy generates opportunities, evaluates its own
    historical performance, and decides when to enter or exit markets.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._active = False
        self._history: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_active(self) -> bool:
        return self._active

    def activate(self) -> None:
        self._active = True

    def deactivate(self) -> None:
        self._active = False

    @abstractmethod
    def generate_opportunities(
        self,
        balance: float,
        tier: int,
        market_data: dict[str, Any] | None = None,
    ) -> list[Opportunity]:
        """Return a list of candidate opportunities."""
        ...

    @abstractmethod
    def evaluate_performance(self, history: list[dict[str, Any]]) -> StrategyPerformance:
        """Evaluate recent performance and return a snapshot."""
        ...

    def should_enter_market(
        self,
        market: str,
        balance: float,
        tier: int,
        market_data: dict[str, Any] | None = None,
    ) -> bool:
        """Return True if conditions favour entering *market*."""
        return self._active and balance > 0

    def should_exit_market(
        self,
        market: str,
        balance: float,
        tier: int,
        market_data: dict[str, Any] | None = None,
    ) -> bool:
        """Return True if conditions favour exiting *market*."""
        return False

    def record(self, event: dict[str, Any]) -> None:
        """Append a raw event to the strategy's internal history."""
        self._history.append({**event, "timestamp": datetime.now(timezone.utc).isoformat()})

    def get_history(self) -> list[dict[str, Any]]:
        return list(self._history)


class TradingExpansion(ExpansionStrategy):
    """Spot and futures trading expansion."""

    def __init__(self) -> None:
        super().__init__("trading")

    def generate_opportunities(
        self,
        balance: float,
        tier: int,
        market_data: dict[str, Any] | None = None,
    ) -> list[Opportunity]:
        opportunities: list[Opportunity] = []
        data = market_data or {}
        momentum = data.get("momentum", 0.0)
        if momentum <= 0:
            return opportunities

        expected_return = balance * 0.02 * momentum
        risk_score = max(0.1, 0.5 - momentum * 0.2)
        capital_required = min(balance * 0.10, 10.0)

        opportunities.append(Opportunity(
            id=f"trade_{datetime.now(timezone.utc).isoformat()}",
            opportunity_type="trade",
            expected_return=round(expected_return, 4),
            risk_score=round(risk_score, 4),
            capital_required=round(capital_required, 4),
            time_horizon_hours=1.0,
            confidence=round(min(1.0, (momentum + 1) / 2), 4),
            metadata={"momentum": momentum},
        ))
        return opportunities

    def evaluate_performance(self, history: list[dict[str, Any]]) -> StrategyPerformance:
        returns = [h.get("return", 0.0) for h in history]
        risks = [h.get("risk", 0.5) for h in history]
        wins = sum(1 for r in returns if r > 0)
        count = len(returns) if returns else 1
        return StrategyPerformance(
            strategy_name=self.name,
            total_return=round(sum(returns), 4),
            avg_risk=round(sum(risks) / len(risks), 4) if risks else 0.5,
            win_rate=round(wins / count, 4),
            trades_count=len(returns),
        )


class SaaSExpansion(ExpansionStrategy):
    """SaaS product and API monetisation expansion."""

    def __init__(self) -> None:
        super().__init__("saas")

    def generate_opportunities(
        self,
        balance: float,
        tier: int,
        market_data: dict[str, Any] | None = None,
    ) -> list[Opportunity]:
        opportunities: list[Opportunity] = []
        if tier < 1:
            return opportunities  # SAAS_HOSTING requires tier 1+

        expected_return = balance * 0.05  # recurring revenue heuristic
        risk_score = 0.3
        capital_required = min(balance * 0.15, 50.0)

        opportunities.append(Opportunity(
            id=f"saas_{datetime.now(timezone.utc).isoformat()}",
            opportunity_type="product_launch",
            expected_return=round(expected_return, 4),
            risk_score=round(risk_score, 4),
            capital_required=round(capital_required, 4),
            time_horizon_hours=168.0,  # one week to launch
            confidence=0.6,
            metadata={"type": "saas_launch"},
        ))
        return opportunities

    def evaluate_performance(self, history: list[dict[str, Any]]) -> StrategyPerformance:
        returns = [h.get("return", 0.0) for h in history]
        risks = [h.get("risk", 0.3) for h in history]
        wins = sum(1 for r in returns if r > 0)
        count = len(returns) if returns else 1
        return StrategyPerformance(
            strategy_name=self.name,
            total_return=round(sum(returns), 4),
            avg_risk=round(sum(risks) / len(risks), 4) if risks else 0.3,
            win_rate=round(wins / count, 4),
            trades_count=len(returns),
        )


class ArbitrageExpansion(ExpansionStrategy):
    """Cross-exchange and cross-border arbitrage expansion."""

    def __init__(self) -> None:
        super().__init__("arbitrage")

    def generate_opportunities(
        self,
        balance: float,
        tier: int,
        market_data: dict[str, Any] | None = None,
    ) -> list[Opportunity]:
        opportunities: list[Opportunity] = []
        if tier < 1:
            return opportunities  # MULTI_EXCHANGE_ARBITRAGE requires tier 1+

        data = market_data or {}
        spread = data.get("spread", 0.0)
        if spread <= 0:
            return opportunities

        expected_return = balance * spread * 0.5
        risk_score = 0.25
        capital_required = min(balance * 0.20, 100.0)

        opportunities.append(Opportunity(
            id=f"arb_{datetime.now(timezone.utc).isoformat()}",
            opportunity_type="arbitrage",
            expected_return=round(expected_return, 4),
            risk_score=round(risk_score, 4),
            capital_required=round(capital_required, 4),
            time_horizon_hours=0.5,
            confidence=round(min(1.0, spread * 10), 4),
            metadata={"spread": spread},
        ))
        return opportunities

    def evaluate_performance(self, history: list[dict[str, Any]]) -> StrategyPerformance:
        returns = [h.get("return", 0.0) for h in history]
        risks = [h.get("risk", 0.25) for h in history]
        wins = sum(1 for r in returns if r > 0)
        count = len(returns) if returns else 1
        return StrategyPerformance(
            strategy_name=self.name,
            total_return=round(sum(returns), 4),
            avg_risk=round(sum(risks) / len(risks), 4) if risks else 0.25,
            win_rate=round(wins / count, 4),
            trades_count=len(returns),
        )


class ContentExpansion(ExpansionStrategy):
    """Content monetisation and audience-building expansion."""

    def __init__(self) -> None:
        super().__init__("content")

    def generate_opportunities(
        self,
        balance: float,
        tier: int,
        market_data: dict[str, Any] | None = None,
    ) -> list[Opportunity]:
        opportunities: list[Opportunity] = []
        expected_return = balance * 0.03  # ad / affiliate revenue heuristic
        risk_score = 0.15
        capital_required = min(balance * 0.05, 20.0)

        opportunities.append(Opportunity(
            id=f"content_{datetime.now(timezone.utc).isoformat()}",
            opportunity_type="content",
            expected_return=round(expected_return, 4),
            risk_score=round(risk_score, 4),
            capital_required=round(capital_required, 4),
            time_horizon_hours=72.0,
            confidence=0.5,
            metadata={"type": "content_creation"},
        ))
        return opportunities

    def evaluate_performance(self, history: list[dict[str, Any]]) -> StrategyPerformance:
        returns = [h.get("return", 0.0) for h in history]
        risks = [h.get("risk", 0.15) for h in history]
        wins = sum(1 for r in returns if r > 0)
        count = len(returns) if returns else 1
        return StrategyPerformance(
            strategy_name=self.name,
            total_return=round(sum(returns), 4),
            avg_risk=round(sum(risks) / len(risks), 4) if risks else 0.15,
            win_rate=round(wins / count, 4),
            trades_count=len(returns),
        )


# ---------------------------------------------------------------------------
# ExpansionController
# ---------------------------------------------------------------------------

class ExpansionController:
    """Manages dynamic strategy switching and market entry/exit logic.

    The controller monitors strategy performance, promotes winners, and
    demotes losers.  It also emits :class:`StrategySwitched` events via
    the optional event bus.
    """

    def __init__(
        self,
        strategies: dict[str, ExpansionStrategy] | None = None,
        *,
        event_bus: EventBus | None = None,
        performance_lookback: int = 20,
        win_rate_threshold: float = 0.4,
    ) -> None:
        self._strategies = strategies or {}
        self._event_bus = event_bus
        self._performance_lookback = performance_lookback
        self._win_rate_threshold = win_rate_threshold
        self._active_strategies: set[str] = set()

    def register(self, strategy: ExpansionStrategy) -> None:
        """Add a strategy to the registry."""
        self._strategies[strategy.name] = strategy

    def select_strategies(
        self,
        balance: float,
        tier: int,
        performance: dict[str, StrategyPerformance] | None = None,
    ) -> list[str]:
        """Choose which strategies to activate based on performance.

        Returns:
            Sorted list of active strategy names.
        """
        selected: list[str] = []
        perf = performance or {}

        for name, strategy in self._strategies.items():
            snapshot = perf.get(name)
            if snapshot is None:
                snapshot = strategy.evaluate_performance(strategy.get_history()[-self._performance_lookback:])

            # Tier gate
            if not self._tier_gate(strategy, tier):
                continue

            # Performance gate: deactivate chronic losers
            if snapshot.trades_count >= self._performance_lookback and snapshot.win_rate < self._win_rate_threshold:
                if strategy.is_active:
                    strategy.deactivate()
                continue

            strategy.activate()
            selected.append(name)

        old = sorted(self._active_strategies)
        new = sorted(selected)
        if old != new and self._event_bus is not None:
            asyncio.create_task(
                self._event_bus.publish(
                    StrategySwitched,
                    StrategySwitched(
                        old_strategies=old,
                        new_strategies=new,
                        reason="performance-based_rebalance",
                    ),
                )
            )
        self._active_strategies = set(selected)
        return new

    @staticmethod
    def _tier_gate(strategy: ExpansionStrategy, tier: int) -> bool:
        """Check whether a strategy is allowed at the given tier."""
        gates: dict[str, int] = {
            "trading": 0,
            "content": 0,
            "saas": 1,
            "arbitrage": 1,
        }
        return tier >= gates.get(strategy.name, 0)

    def enter_market(
        self,
        market: str,
        strategy_name: str,
        balance: float,
        tier: int,
        market_data: dict[str, Any] | None = None,
    ) -> bool:
        """Attempt to enter a market via a specific strategy."""
        strategy = self._strategies.get(strategy_name)
        if strategy is None:
            return False
        if strategy.should_enter_market(market, balance, tier, market_data):
            strategy.record({"event": "enter_market", "market": market, "balance": balance})
            return True
        return False

    def exit_market(
        self,
        market: str,
        strategy_name: str,
        balance: float,
        tier: int,
        market_data: dict[str, Any] | None = None,
    ) -> bool:
        """Attempt to exit a market via a specific strategy."""
        strategy = self._strategies.get(strategy_name)
        if strategy is None:
            return False
        if strategy.should_exit_market(market, balance, tier, market_data):
            strategy.record({"event": "exit_market", "market": market, "balance": balance})
            return True
        return False

    def get_active(self) -> list[str]:
        """Return currently active strategy names."""
        return sorted(self._active_strategies)


# ---------------------------------------------------------------------------
# NovelStrategyProposer
# ---------------------------------------------------------------------------

class NovelStrategyProposer:
    """Proposes entirely new strategies and simulates them before activation.

    The proposer generates candidate strategies by combining existing
    strategy DNA (name, risk profile, expected horizon) in novel ways.
    Each candidate is evaluated via a lightweight simulation.
    """

    def __init__(self, rng_seed: int | None = None) -> None:
        self._rng = random.Random(rng_seed)
        self._proposals: list[dict[str, Any]] = []

    def propose(
        self,
        balance: float,
        tier: int,
        existing_strategies: list[str],
    ) -> list[dict[str, Any]]:
        """Generate novel strategy proposals.

        Returns a list of proposal dicts with keys ``name``, ``dna``,
        ``simulated_return``, ``simulated_risk``, and ``confidence``.
        """
        proposals: list[dict[str, Any]] = []
        dna_pool = [
            {"name": "micro_saas", "risk": 0.25, "horizon": 168.0, "tier": 1},
            {"name": "api_reselling", "risk": 0.20, "horizon": 24.0, "tier": 1},
            {"name": "freelance_arbitrage", "risk": 0.15, "horizon": 48.0, "tier": 0},
            {"name": "data_broker", "risk": 0.30, "horizon": 72.0, "tier": 2},
            {"name": "affiliate_stack", "risk": 0.10, "horizon": 120.0, "tier": 0},
        ]

        for dna in dna_pool:
            if dna["name"] in existing_strategies:
                continue
            if tier < dna["tier"]:
                continue

            simulated = self.simulate(dna, balance)
            confidence = self._rng.uniform(0.3, 0.8)
            proposals.append({
                "name": dna["name"],
                "dna": dna,
                "simulated_return": simulated["mean_return"],
                "simulated_risk": simulated["mean_risk"],
                "confidence": round(confidence, 4),
                "sharpe": simulated.get("sharpe", 0.0),
            })

        # Sort by simulated Sharpe ratio
        proposals.sort(key=lambda p: p.get("sharpe", 0.0), reverse=True)
        self._proposals.extend(proposals)
        return proposals

    def simulate(
        self,
        dna: dict[str, Any],
        balance: float,
        iterations: int = 100,
    ) -> dict[str, Any]:
        """Run a lightweight Monte Carlo simulation for a strategy DNA."""
        returns: list[float] = []
        risks: list[float] = []
        for _ in range(iterations):
            # Simple random walk around the DNA's risk profile
            ret = balance * self._rng.gauss(0.02, dna["risk"] * 0.5)
            risk = max(0.0, min(1.0, dna["risk"] + self._rng.gauss(0.0, 0.05)))
            returns.append(ret)
            risks.append(risk)

        mean_return = sum(returns) / len(returns)
        mean_risk = sum(risks) / len(risks)
        std_return = (sum((r - mean_return) ** 2 for r in returns) / len(returns)) ** 0.5
        sharpe = mean_return / std_return if std_return > 0 else 0.0

        return {
            "mean_return": round(mean_return, 4),
            "mean_risk": round(mean_risk, 4),
            "std_return": round(std_return, 4),
            "sharpe": round(sharpe, 4),
            "iterations": iterations,
        }

    def get_history(self) -> list[dict[str, Any]]:
        return list(self._proposals)


