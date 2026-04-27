from typing import Any

import numpy as np

from .dataclasses import RevenueOpportunity


class RevenueEngine:
    def scan_market_gaps(self, demand_data: dict[str, float], supply_data: dict[str, float]) -> list[RevenueOpportunity]:
        opportunities = []

        for niche, demand in demand_data.items():
            supply = supply_data.get(niche, 0.0)
            if supply <= 0:
                gap_ratio = float("inf")
            else:
                gap_ratio = demand / supply

            if gap_ratio > 1.5 or supply <= 0:
                expected_roi = min((gap_ratio - 1.0) * 0.5, 5.0)
                risk_score = 1.0 / gap_ratio if gap_ratio > 0 else 1.0
                confidence = min(gap_ratio / 5.0, 1.0)
                opportunities.append(
                    RevenueOpportunity(
                        niche=niche,
                        expected_roi=expected_roi,
                        risk_score=risk_score,
                        time_to_capture="medium",
                        confidence=confidence,
                    )
                )

        return opportunities

    def build_vs_buy(
        self,
        build_cost: float,
        build_time: float,
        buy_cost: float,
        expected_revenue: float,
    ) -> dict[str, Any]:
        if build_time <= 0:
            build_time = 1.0

        build_roi = (expected_revenue - build_cost) / build_cost if build_cost > 0 else 0.0
        buy_roi = (expected_revenue - buy_cost) / buy_cost if buy_cost > 0 else 0.0

        build_annualized = build_roi / build_time
        buy_annualized = buy_roi

        if build_annualized > buy_annualized and build_cost > 0:
            decision = "build"
            score = build_annualized
        elif buy_annualized >= build_annualized and buy_cost > 0:
            decision = "buy"
            score = buy_annualized
        else:
            decision = "none"
            score = 0.0

        return {
            "decision": decision,
            "build_roi": build_roi,
            "buy_roi": buy_roi,
            "build_annualized": build_annualized,
            "buy_annualized": buy_annualized,
            "score": score,
        }

    def optimize_pricing(
        self,
        demand_elasticity: float,
        competitor_prices: list[float],
        cost_basis: float,
    ) -> dict[str, Any]:
        if not competitor_prices:
            competitor_prices = [cost_basis * 2.0]

        avg_competitor = float(np.mean(competitor_prices))
        min_competitor = float(np.min(competitor_prices))

        if demand_elasticity >= 0 or demand_elasticity == 0:
            optimal_price = max(avg_competitor * 0.95, cost_basis * 1.1)
        else:
            markup = -1.0 / demand_elasticity
            optimal_price = max(cost_basis * (1.0 + markup), cost_basis * 1.05)
            optimal_price = min(optimal_price, avg_competitor * 1.05)

        expected_demand = max(1.0 + demand_elasticity * (optimal_price - avg_competitor) / avg_competitor, 0.1)
        expected_profit = (optimal_price - cost_basis) * expected_demand

        return {
            "optimal_price": optimal_price,
            "expected_demand": expected_demand,
            "expected_profit": expected_profit,
            "competitor_benchmark": avg_competitor,
        }

    def evaluate_task_arbitrage(self, task_price: float, completion_cost: float, time_hours: float) -> dict[str, Any]:
        if time_hours <= 0:
            time_hours = 1.0

        net_profit = task_price - completion_cost
        hourly_rate = net_profit / time_hours

        if net_profit > 0 and hourly_rate >= 10.0:
            viable = True
            score = min(hourly_rate / 100.0, 1.0)
        elif net_profit > 0:
            viable = True
            score = min(hourly_rate / 50.0, 0.5)
        else:
            viable = False
            score = 0.0

        return {
            "viable": viable,
            "net_profit": net_profit,
            "hourly_rate": hourly_rate,
            "score": score,
        }
