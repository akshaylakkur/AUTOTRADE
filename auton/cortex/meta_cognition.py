"""Meta-cognition module for ÆON."""

from __future__ import annotations

from typing import Any

from auton.cortex.dataclasses import ReasoningReceipt


class MetaCognition:
    """Decides whether an expensive reasoning step is worth its cost."""

    def __init__(
        self,
        *,
        frugal_cost_per_call: float = 0.001,
        deep_cost_per_call: float = 0.05,
        deep_threshold_multiplier: float = 3.0,
    ) -> None:
        self._frugal_cost = frugal_cost_per_call
        self._deep_cost = deep_cost_per_call
        self._deep_threshold_multiplier = deep_threshold_multiplier

    def evaluate_reasoning_cost(
        self,
        expected_profit: float,
        reasoning_cost: float,
        confidence: float,
    ) -> ReasoningReceipt:
        """Evaluate whether a specific reasoning operation is worth its direct cost.

        Args:
            expected_profit: Estimated profit (USD) if the decision is correct.
            reasoning_cost: Actual cost (USD) of the reasoning pass.
            confidence: Model confidence in the expected profit estimate (0-1).

        Returns:
            A :class:`ReasoningReceipt` with go/no-go and expected value.
        """
        expected_value = expected_profit * confidence - reasoning_cost
        go = expected_value > 0 and confidence > 0.3

        mode = "deep" if reasoning_cost >= self._deep_cost else "frugal"

        return ReasoningReceipt(
            cost=reasoning_cost,
            expected_value=round(expected_value, 4),
            go=go,
            confidence=round(confidence, 4),
            mode=mode,
            metadata={
                "expected_profit": expected_profit,
                "confidence_threshold": 0.3,
            },
        )

    def should_use_deep_mode(
        self,
        current_burn_rate: float,
        income: float,
        opportunity_size: float,
    ) -> bool:
        """Decide between frugal and deep reasoning at the global level.

        Deep mode is engaged when:
        * The opportunity is large relative to burn, **and**
        * The agent is not running a deficit (income >= burn).

        Args:
            current_burn_rate: USD per day spent on all operations.
            income: USD per day earned across all revenue streams.
            opportunity_size: Estimated absolute profit of the opportunity.

        Returns:
            ``True`` if deep reasoning should be used.
        """
        if current_burn_rate <= 0:
            # Avoid division by zero; if we're not burning anything, deep mode is fine.
            return opportunity_size >= self._deep_cost * self._deep_threshold_multiplier

        burn_ratio = current_burn_rate / max(income, 0.0001)
        if burn_ratio > 1.5:
            # Burning much more than we earn: never deep mode.
            return False

        # Deep mode only for high-conviction, high-reward opportunities.
        return opportunity_size >= self._deep_cost * self._deep_threshold_multiplier

    def receipt_for_opportunity(
        self,
        expected_profit: float,
        confidence: float,
        current_burn_rate: float,
        income: float,
    ) -> ReasoningReceipt:
        """Convenience method: produce a receipt using the correct mode and cost.

        Args:
            expected_profit: Estimated profit (USD) if correct.
            confidence: Confidence in the estimate (0-1).
            current_burn_rate: USD per day spent.
            income: USD per day earned.

        Returns:
            A fully-populated :class:`ReasoningReceipt`.
        """
        deep = self.should_use_deep_mode(current_burn_rate, income, expected_profit)
        cost = self._deep_cost if deep else self._frugal_cost
        return self.evaluate_reasoning_cost(expected_profit, cost, confidence)
