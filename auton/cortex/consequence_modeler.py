"""Consequence modeling for ÆON — outcome simulation, Monte Carlo, and worst-case analysis."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from auton.core.event_bus import EventBus
from auton.core.events import SimulationCompleted
from auton.cortex.decision_engine import ResourceDecision

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class OutcomeDistribution:
    """Summary statistics for a simulated outcome distribution."""

    mean: float
    median: float
    std: float
    percentile_5: float
    percentile_95: float
    worst_case: float
    best_case: float
    iterations: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """Result of simulating a single scenario."""

    scenario_name: str
    outcome: float
    probability: float
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ConsequenceModeler
# ---------------------------------------------------------------------------

class ConsequenceModeler:
    """Simulates outcomes of :class:`ResourceDecision` objects before execution.

    The modeler combines deterministic heuristics with stochastic sampling
    to produce an :class:`OutcomeDistribution` that the decision engine can
    use to filter or rank decisions.
    """

    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        rng_seed: int | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._rng = random.Random(rng_seed)

    def simulate(
        self,
        decision: ResourceDecision,
        iterations: int = 1_000,
        balance: float = 0.0,
    ) -> OutcomeDistribution:
        """Run a Monte Carlo simulation for a single decision.

        Args:
            decision: The decision to simulate.
            iterations: Number of Monte Carlo trials.
            balance: Current ledger balance (used to contextualise outcomes).

        Returns:
            An :class:`OutcomeDistribution` summarising the simulation.
        """
        outcomes = self._generate_outcomes(decision, iterations, balance)
        return self._summarise(outcomes, iterations)

    def _generate_outcomes(
        self,
        decision: ResourceDecision,
        iterations: int,
        balance: float,
    ) -> list[float]:
        """Produce a list of simulated profit/loss outcomes."""
        outcomes: list[float] = []
        base = decision.required_budget if decision.required_budget > 0 else balance * 0.01
        if base <= 0:
            return [0.0] * iterations

        for _ in range(iterations):
            # Expected return with noise proportional to risk
            noise = self._rng.gauss(0.0, decision.risk_score)
            roi = decision.expected_roi + noise
            # Clamp to plausible bounds
            roi = max(-1.0, min(roi, 5.0))
            outcome = base * roi
            outcomes.append(outcome)
        return outcomes

    @staticmethod
    def _summarise(outcomes: list[float], iterations: int) -> OutcomeDistribution:
        """Compute summary statistics from a list of outcomes."""
        if not outcomes:
            return OutcomeDistribution(
                mean=0.0,
                median=0.0,
                std=0.0,
                percentile_5=0.0,
                percentile_95=0.0,
                worst_case=0.0,
                best_case=0.0,
                iterations=iterations,
            )

        sorted_outcomes = sorted(outcomes)
        n = len(sorted_outcomes)
        mean = sum(sorted_outcomes) / n
        median = sorted_outcomes[n // 2] if n % 2 else (sorted_outcomes[n // 2 - 1] + sorted_outcomes[n // 2]) / 2
        variance = sum((x - mean) ** 2 for x in sorted_outcomes) / n
        std = variance ** 0.5
        p5_idx = max(0, int(n * 0.05) - 1)
        p95_idx = min(n - 1, int(n * 0.95) - 1)

        return OutcomeDistribution(
            mean=round(mean, 4),
            median=round(median, 4),
            std=round(std, 4),
            percentile_5=round(sorted_outcomes[p5_idx], 4),
            percentile_95=round(sorted_outcomes[p95_idx], 4),
            worst_case=round(sorted_outcomes[0], 4),
            best_case=round(sorted_outcomes[-1], 4),
            iterations=iterations,
        )

    async def publish_simulation(
        self,
        decision: ResourceDecision,
        distribution: OutcomeDistribution,
    ) -> None:
        """Publish a :class:`SimulationCompleted` event if an event bus is wired."""
        if self._event_bus is None:
            return
        await self._event_bus.publish(
            SimulationCompleted,
            SimulationCompleted(
                simulation_type="consequence_model",
                mean_outcome=distribution.mean,
                worst_case=distribution.worst_case,
                best_case=distribution.best_case,
                metadata={
                    "decision_action": decision.action,
                    "strategy": decision.strategy,
                    "std": distribution.std,
                    "iterations": distribution.iterations,
                },
            ),
        )


# ---------------------------------------------------------------------------
# MonteCarloSimulator
# ---------------------------------------------------------------------------

class MonteCarloSimulator:
    """General-purpose Monte Carlo simulator for arbitrary outcome models."""

    def __init__(self, rng_seed: int | None = None) -> None:
        self._rng = random.Random(rng_seed)

    def run(
        self,
        model: Callable[[], float],
        iterations: int = 1_000,
    ) -> OutcomeDistribution:
        """Execute *model* ``iterations`` times and summarise.

        Args:
            model: A callable returning a single scalar outcome.
            iterations: Number of trials.

        Returns:
            An :class:`OutcomeDistribution`.
        """
        outcomes: list[float] = []
        for _ in range(iterations):
            try:
                outcome = model()
            except Exception:
                logger.exception("MonteCarloSimulator: model raised an exception")
                outcome = 0.0
            outcomes.append(outcome)
        return ConsequenceModeler._summarise(outcomes, iterations)

    def run_parameterised(
        self,
        model: Callable[..., float],
        param_sets: list[dict[str, Any]],
        iterations: int = 1_000,
    ) -> list[tuple[dict[str, Any], OutcomeDistribution]]:
        """Run Monte Carlo across multiple parameter sets.

        Returns:
            A list of (param_dict, OutcomeDistribution) tuples.
        """
        results: list[tuple[dict[str, Any], OutcomeDistribution]] = []
        for params in param_sets:
            outcomes: list[float] = []
            for _ in range(iterations):
                try:
                    outcome = model(**params)
                except Exception:
                    logger.exception("MonteCarloSimulator: model raised an exception")
                    outcome = 0.0
                outcomes.append(outcome)
            results.append((params, ConsequenceModeler._summarise(outcomes, iterations)))
        return results


# ---------------------------------------------------------------------------
# WorstCaseAnalyzer
# ---------------------------------------------------------------------------

class WorstCaseAnalyzer:
    """Analyzes worst-case scenarios for a decision before commitment.

    The analyzer enumerates a set of adverse scenarios (market crash,
    liquidity freeze, execution failure, etc.) and computes the expected
    loss and survival probability for each.
    """

    DEFAULT_SCENARIOS: list[dict[str, Any]] = [
        {"name": "market_crash", "multiplier": -0.50, "probability": 0.05},
        {"name": "liquidity_freeze", "multiplier": -0.30, "probability": 0.10},
        {"name": "execution_failure", "multiplier": -1.00, "probability": 0.05},
        {"name": "regulatory_shock", "multiplier": -0.20, "probability": 0.03},
        {"name": "counterparty_default", "multiplier": -0.40, "probability": 0.02},
    ]

    def __init__(self, scenarios: list[dict[str, Any]] | None = None) -> None:
        self._scenarios = scenarios or list(self.DEFAULT_SCENARIOS)

    def analyze(
        self,
        decision: ResourceDecision,
        balance: float,
    ) -> dict[str, Any]:
        """Evaluate the decision under every adverse scenario.

        Returns:
            A dict with keys ``expected_loss``, ``survival_probability``,
            ``scenario_results``, and ``max_drawdown_pct``.
        """
        if balance <= 0:
            return {
                "expected_loss": 0.0,
                "survival_probability": 0.0,
                "scenario_results": [],
                "max_drawdown_pct": 0.0,
            }

        results: list[ScenarioResult] = []
        total_loss = 0.0
        for scenario in self._scenarios:
            loss = decision.required_budget * abs(scenario["multiplier"])
            total_loss += loss * scenario["probability"]
            results.append(ScenarioResult(
                scenario_name=scenario["name"],
                outcome=-loss,
                probability=scenario["probability"],
                metadata={"multiplier": scenario["multiplier"]},
            ))

        expected_loss = round(total_loss, 4)
        max_loss = max(abs(r.outcome) for r in results) if results else 0.0
        survival_probability = max(0.0, 1.0 - (max_loss / balance)) if balance > 0 else 0.0
        max_drawdown_pct = round(max_loss / balance, 4) if balance > 0 else 0.0

        return {
            "expected_loss": expected_loss,
            "survival_probability": round(survival_probability, 4),
            "scenario_results": results,
            "max_drawdown_pct": max_drawdown_pct,
            "decision_action": decision.action,
            "required_budget": decision.required_budget,
        }

    def is_survivable(
        self,
        decision: ResourceDecision,
        balance: float,
        min_survival_probability: float = 0.90,
    ) -> bool:
        """Return True if the decision keeps survival probability above the threshold."""
        result = self.analyze(decision, balance)
        return result["survival_probability"] >= min_survival_probability
