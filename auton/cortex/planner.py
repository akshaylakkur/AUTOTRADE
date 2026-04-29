"""Strategic planner for ÆON."""

from __future__ import annotations

from typing import Any

from auton.core.config import AeonConfig, Capability, TierGate
from auton.core.constants import RISK_LIMITS, TIER_COMPUTE_BUDGETS
from auton.core.reasoning_log import get_reasoning_log
from auton.cortex.dataclasses import Plan


class StrategicPlanner:
    """Generates tier-aware strategic plans with daily/weekly economic objectives."""

    def __init__(self, default_horizon: str = "daily") -> None:
        if default_horizon not in {"daily", "weekly"}:
            raise ValueError("default_horizon must be 'daily' or 'weekly'")
        self._default_horizon = default_horizon
        self._current_plan: Plan | None = None

    def plan_objectives(
        self,
        balance: float,
        tier: int | None = None,
        recent_performance: dict[str, Any] | None = None,
        *,
        horizon: str | None = None,
    ) -> Plan:
        """Generate a strategic plan based on current state and tier.

        Args:
            balance: Current realized balance in USD.
            tier: Operational tier (0-4).  If None, derived from balance.
            recent_performance: Optional dict with keys like ``profit``, ``drawdown``,
                ``win_rate``, ``trades_executed``.
            horizon: ``"daily"`` or ``"weekly"``.  Falls back to the instance default.

        Returns:
            A :class:`Plan` dataclass with goals, targets, and priorities.
        """
        resolved_tier = tier if tier is not None else TierGate.get_tier(balance)
        resolved_horizon = horizon or self._default_horizon
        performance = recent_performance or {}

        risk_limits = RISK_LIMITS.get(resolved_tier, RISK_LIMITS[0])
        compute_budget = TIER_COMPUTE_BUDGETS.get(resolved_tier, TIER_COMPUTE_BUDGETS[0])

        target_revenue = self._compute_target_revenue(
            balance, resolved_tier, resolved_horizon, compute_budget, performance
        )
        risk_tolerance = self._compute_risk_tolerance(resolved_tier, performance)
        goals = self._build_goals(resolved_tier, performance)
        capability_priorities = self._build_capability_priorities(resolved_tier)

        plan = Plan(
            goals=goals + [f"User guidance: {AeonConfig.GUIDANCE_PROMPT}"],
            target_revenue=target_revenue,
            risk_tolerance=risk_tolerance,
            capability_priorities=capability_priorities,
            tier=resolved_tier,
            horizon=resolved_horizon,
            metadata={
                "compute_budget": compute_budget,
                "max_position_pct": risk_limits["max_position_pct"],
                "max_leverage": risk_limits["max_leverage"],
                "max_daily_trades": risk_limits["max_daily_trades"],
                "survival_reserve_pct": risk_limits["survival_reserve_pct"],
                **performance,
            },
        )
        self._current_plan = plan
        return plan

    def get_current_plan(self) -> Plan | None:
        """Return the most recently generated plan, if any."""
        return self._current_plan

    @staticmethod
    def _compute_target_revenue(
        balance: float,
        tier: int,
        horizon: str,
        compute_budget: float,
        performance: dict[str, Any],
    ) -> float:
        """Compute a revenue target that covers compute costs plus profit margin."""
        multiplier = 7.0 if horizon == "weekly" else 1.0
        base_cost = compute_budget * multiplier

        # Adjust upward for higher tiers expecting higher returns
        tier_multiplier = 1.0 + (tier * 0.25)

        # If we're underperforming, lower targets to survive
        drawdown = performance.get("drawdown", 0.0)
        if drawdown and drawdown > 0.05:
            tier_multiplier *= 0.5

        return round(base_cost * tier_multiplier * 1.5, 2)

    @staticmethod
    def _compute_risk_tolerance(tier: int, performance: dict[str, Any]) -> float:
        """Return a risk-tolerance score (0-1) adjusted for tier and recent drawdown."""
        base_tolerance = 0.2 + (tier * 0.15)
        drawdown = performance.get("drawdown", 0.0)
        if drawdown and drawdown > 0.05:
            base_tolerance *= max(0.1, 1.0 - drawdown * 5)
        win_rate = performance.get("win_rate")
        if win_rate is not None:
            base_tolerance *= 0.8 + (win_rate * 0.4)
        return round(min(max(base_tolerance, 0.0), 1.0), 2)

    @staticmethod
    def _build_goals(tier: int, performance: dict[str, Any]) -> list[str]:
        """Assemble a list of strategic goals appropriate to the tier."""
        goals: list[str] = ["survive"]
        drawdown = performance.get("drawdown", 0.0)

        if drawdown and drawdown > 0.05:
            goals.append("recover_drawdown")
            goals.append("minimize_burn")
            return goals

        if tier == 0:
            goals.extend(["reach_tier_1", "frugal_trading", "reduce_costs"])
        elif tier == 1:
            goals.extend(["stabilize_profit", "explore_arbitrage", "diversify_revenue"])
        elif tier == 2:
            goals.extend(["reach_tier_3", "deep_reasoning_alpha", "scale_products"])
        elif tier == 3:
            goals.extend(["multi_asset_portfolio", "hire_contractors", "automated_backtesting"])
        elif tier >= 4:
            goals.extend(["cross_border_arbitrage", "venture_reinvestment", "external_ai_agents"])

        return goals

    @staticmethod
    def _build_capability_priorities(tier: int) -> list[str]:
        """Return capability names sorted by priority for the given tier."""
        if tier == 0:
            return ["SPOT_TRADING", "FREELANCE_TASKS", "NEWSLETTER_SUBSCRIPTIONS"]
        elif tier == 1:
            return ["FUTURES_TRADING", "MULTI_EXCHANGE_ARBITRAGE", "ON_CHAIN_DATA", "SAAS_HOSTING"]
        elif tier == 2:
            return ["EQUITIES", "SENTIMENT_FEEDS", "DEEP_REASONING", "SOFTWARE_LICENSING"]
        elif tier == 3:
            return [
                "FOREX",
                "OPTIONS",
                "STRATEGY_BACKTESTING",
                "SPOT_COMPUTE_SCALING",
                "HIRE_CONTRACTORS",
            ]
        else:
            return [
                "CROSS_BORDER_ARBITRAGE",
                "CUSTOM_ALGORITHM_DEPLOYMENT",
                "HIGH_FREQUENCY_DATA",
                "EXTERNAL_AI_AGENTS",
                "VENTURE_REINVESTMENT",
            ]
