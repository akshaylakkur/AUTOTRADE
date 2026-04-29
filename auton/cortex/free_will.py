"""Free will engine for ÆON — autonomous exploration, serendipity, and self-generated goals."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from auton.core.config import TierGate
from auton.core.event_bus import EventBus
from auton.core.events import GoalGenerated, OpportunityDiscovered
from auton.cortex.decision_engine import Opportunity, ResourceDecision
from auton.cortex.expansionism import Goal, GoalPlanner, Milestone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FreeWillEngine
# ---------------------------------------------------------------------------

class FreeWillEngine:
    """Encourages random exploration of non-obvious opportunities.

    The engine maintains an *exploration rate* that decays with tier
    (higher-tier agents are expected to be more deliberate) but never
    drops to zero.  At each decision cycle it may inject low-confidence,
    high-potential outliers into the opportunity stream.
    """

    def __init__(
        self,
        exploration_rate: float = 0.15,
        tier_decay: float = 0.02,
        rng_seed: int | None = None,
    ) -> None:
        self._base_rate = exploration_rate
        self._tier_decay = tier_decay
        self._rng = random.Random(rng_seed)

    def effective_rate(self, tier: int) -> float:
        """Return the exploration rate adjusted for operational tier."""
        return max(0.01, self._base_rate - (tier * self._tier_decay))

    def explore(
        self,
        opportunities: list[Opportunity],
        balance: float,
        tier: int | None = None,
    ) -> list[Opportunity]:
        """Potentially augment the opportunity list with exploratory candidates.

        Args:
            opportunities: Existing scored opportunities.
            balance: Current ledger balance.
            tier: Operational tier (auto-derived if None).

        Returns:
            The original list, possibly extended with 1-3 synthetic exploratory
            opportunities.
        """
        resolved_tier = tier if tier is not None else TierGate.get_tier(balance)
        rate = self.effective_rate(resolved_tier)

        if self._rng.random() > rate:
            return opportunities

        # Generate 1-3 exploratory opportunities with unusual characteristics
        extras: list[Opportunity] = []
        count = self._rng.randint(1, 3)
        for i in range(count):
            # Pick an unusual expected return: sometimes negative, sometimes very high
            expected_return = balance * self._rng.gauss(0.0, 0.10)
            risk_score = self._rng.uniform(0.3, 0.9)
            capital_required = min(balance * 0.05, 5.0)
            confidence = self._rng.uniform(0.1, 0.4)  # deliberately low
            horizon = self._rng.uniform(0.5, 72.0)

            extras.append(Opportunity(
                id=f"explore_{i}_{datetime.now(timezone.utc).isoformat()}",
                opportunity_type="exploration",
                expected_return=round(expected_return, 4),
                risk_score=round(risk_score, 4),
                capital_required=round(capital_required, 4),
                time_horizon_hours=round(horizon, 4),
                confidence=round(confidence, 4),
                metadata={"origin": "free_will", "exploration_rate": rate},
            ))

        return opportunities + extras


# ---------------------------------------------------------------------------
# SerendipityEngine
# ---------------------------------------------------------------------------

class SerendipityEngine:
    """Occasionally promotes low-confidence but high-potential actions.

    The serendipity engine acts as a second-pass filter after the main
    evaluator.  It looks for decisions that were rejected due to low
    confidence but have an asymmetric upside profile.
    """

    def __init__(
        self,
        threshold: float = 0.30,
        min_asymmetric_ratio: float = 3.0,
        max_serendipity_budget_pct: float = 0.05,
        rng_seed: int | None = None,
    ) -> None:
        self._threshold = threshold
        self._min_asymmetric_ratio = min_asymmetric_ratio
        self._max_budget_pct = max_serendipity_budget_pct
        self._rng = random.Random(rng_seed)
        self._history: list[dict[str, Any]] = []

    def evaluate(
        self,
        decisions: list[ResourceDecision],
        balance: float,
    ) -> list[ResourceDecision]:
        """Return decisions that deserve a serendipity-funded trial.

        Args:
            decisions: All candidate decisions (including rejected ones).
            balance: Current ledger balance.

        Returns:
            A filtered list of decisions approved for serendipity funding.
        """
        approved: list[ResourceDecision] = []
        max_budget = balance * self._max_budget_pct
        spent = 0.0

        for d in sorted(decisions, key=lambda x: x.expected_roi, reverse=True):
            if d.confidence >= self._threshold:
                continue  # Already confident — not a serendipity candidate
            if d.expected_roi <= 0:
                continue  # No upside

            # Asymmetric ratio: expected upside vs required budget
            upside = d.expected_roi * d.required_budget
            ratio = upside / max(d.required_budget, 0.01)
            if ratio < self._min_asymmetric_ratio:
                continue

            if spent + d.required_budget > max_budget:
                continue

            # Stochastic gate: not every candidate gets through
            if self._rng.random() < 0.5:
                approved.append(d)
                spent += d.required_budget
                self._history.append({
                    "action": d.action,
                    "strategy": d.strategy,
                    "confidence": d.confidence,
                    "expected_roi": d.expected_roi,
                    "ratio": round(ratio, 4),
                })

        return approved

    def get_history(self) -> list[dict[str, Any]]:
        return list(self._history)


# ---------------------------------------------------------------------------
# GoalGenerator
# ---------------------------------------------------------------------------

class GoalGenerator:
    """Generates autonomous goals based on current state, recent performance, and aspirations.

    Unlike the static goal suggestions in :class:`GoalPlanner`, the
    generator creates *novel* goals that may not have been pre-defined,
    giving the agent emergent objectives.
    """

    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        rng_seed: int | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._rng = random.Random(rng_seed)
        self._generated_goals: list[Goal] = []

    def generate_goals(
        self,
        balance: float,
        tier: int | None = None,
        recent_performance: dict[str, Any] | None = None,
    ) -> list[Goal]:
        """Produce 0-3 self-generated goals.

        Args:
            balance: Current ledger balance.
            tier: Operational tier (auto-derived if None).
            recent_performance: Optional performance dict with keys like
                ``profit``, ``drawdown``, ``win_rate``.

        Returns:
            A list of newly generated :class:`Goal` objects.
        """
        resolved_tier = tier if tier is not None else TierGate.get_tier(balance)
        perf = recent_performance or {}
        goals: list[Goal] = []

        # Survival urgency
        if balance < 100.0:
            goals.append(Goal(
                name="urgent_survival",
                description="Generate $50 in 48 hours through any available channel",
                milestones=[
                    Milestone(name="profit_48h", target_value=50.0, current_value=perf.get("profit", 0.0)),
                    Milestone(name="trades_executed", target_value=5.0, current_value=perf.get("trades_executed", 0.0), unit="count"),
                ],
            ))

        # Diversification pressure when win rate is low
        win_rate = perf.get("win_rate", 1.0)
        if win_rate < 0.4 and resolved_tier >= 1:
            goals.append(Goal(
                name="diversify_revenue",
                description="Launch a non-trading revenue stream to reduce dependency on market luck",
                milestones=[
                    Milestone(name="non_trading_revenue", target_value=balance * 0.10, current_value=0.0),
                    Milestone(name="streams_active", target_value=2.0, current_value=0.0, unit="count"),
                ],
            ))

        # Serendipity goal: try something completely new
        if self._rng.random() < 0.2 + (resolved_tier * 0.05):
            goals.append(Goal(
                name="serendipity_experiment",
                description="Execute one high-risk, high-reward experiment and document results",
                milestones=[
                    Milestone(name="experiment_executed", target_value=1.0, current_value=0.0, unit="count"),
                    Milestone(name="experiment_roi", target_value=0.20, current_value=0.0),
                ],
            ))

        # Tier aspiration
        next_threshold = {0: 500.0, 1: 1_000.0, 2: 10_000.0, 3: 100_000.0, 4: 1_000_000.0}.get(resolved_tier)
        if next_threshold and balance < next_threshold:
            goals.append(Goal(
                name=f"reach_tier_{resolved_tier + 1}",
                description=f"Grow balance to ${next_threshold:,.0f}",
                milestones=[
                    Milestone(name="target_balance", target_value=next_threshold, current_value=balance),
                ],
            ))

        self._generated_goals.extend(goals)

        # Emit events
        if self._event_bus is not None:
            for goal in goals:
                asyncio.create_task(
                    self._event_bus.publish(
                        GoalGenerated,
                        GoalGenerated(
                            goal_name=goal.name,
                            description=goal.description,
                            target_value=goal.milestones[0].target_value if goal.milestones else 0.0,
                            unit=goal.milestones[0].unit if goal.milestones else "USD",
                            deadline=datetime.now(timezone.utc) + timedelta(days=7),
                        ),
                    )
                )

        return goals

    async def discover_opportunity(
        self,
        domain: str,
        description: str,
        estimated_value: float,
        confidence: float,
    ) -> None:
        """Emit an :class:`OpportunityDiscovered` event for serendipitous finds."""
        if self._event_bus is None:
            return
        await self._event_bus.publish(
            OpportunityDiscovered,
            OpportunityDiscovered(
                domain=domain,
                description=description,
                estimated_value=estimated_value,
                confidence=confidence,
            ),
        )

    def get_generated_goals(self) -> list[Goal]:
        return list(self._generated_goals)


import asyncio  # noqa: E402
