from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from auton.analytics.alpha_engine import AlphaEngine
from auton.analytics.backtester import Backtester
from auton.analytics.dataclasses import AlphaSignal, BacktestResult, RevenueOpportunity, RiskAssessment
from auton.analytics.revenue_engine import RevenueEngine
from auton.analytics.risk_management import RiskManager


class TestAlphaEngine:
    def test_technical_analysis_returns_alpha_signal(self):
        engine = AlphaEngine()
        prices = np.linspace(100, 200, 50) + np.random.randn(50) * 5
        signal = engine.technical_analysis(prices)
        assert isinstance(signal, AlphaSignal)
        assert signal.direction in {"bullish", "bearish", "neutral"}
        assert 0.0 <= signal.strength <= 1.0
        assert 0.0 <= signal.confidence <= 1.0

    def test_technical_analysis_neutral_on_flat_prices(self):
        engine = AlphaEngine()
        prices = np.full(50, 100.0)
        signal = engine.technical_analysis(prices)
        assert signal.direction == "neutral"

    def test_technical_analysis_insufficient_data(self):
        engine = AlphaEngine()
        prices = np.array([100.0, 101.0])
        signal = engine.technical_analysis(prices)
        assert signal.direction == "neutral"
        assert signal.strength == 0.0

    def test_stat_arb_pair_trading(self):
        engine = AlphaEngine()
        x = np.linspace(100, 200, 60)
        y = x * 1.05 + np.random.randn(60) * 2
        pair_prices = np.column_stack([x, y])
        signal = engine.stat_arb(pair_prices)
        assert isinstance(signal, AlphaSignal)
        assert signal.direction in {"long_spread", "short_spread", "neutral"}

    def test_stat_arb_insufficient_data(self):
        engine = AlphaEngine()
        pair_prices = np.array([[100.0, 105.0], [101.0, 106.0]])
        signal = engine.stat_arb(pair_prices)
        assert signal.direction == "neutral"

    def test_sentiment_alpha_bullish(self):
        engine = AlphaEngine()
        scores = np.array([0.8, 0.9, 0.7, 0.85])
        signal = engine.sentiment_alpha(scores)
        assert isinstance(signal, AlphaSignal)
        assert signal.direction in {"bullish", "bearish", "neutral"}

    def test_sentiment_alpha_empty(self):
        engine = AlphaEngine()
        signal = engine.sentiment_alpha(np.array([]))
        assert signal.direction == "neutral"
        assert signal.strength == 0.0

    def test_onchain_alpha(self):
        engine = AlphaEngine()
        flows = np.column_stack([np.array([100.0, 120.0, 110.0]), np.array([90.0, 80.0, 70.0])])
        signal = engine.onchain_alpha(flows)
        assert isinstance(signal, AlphaSignal)
        assert signal.direction in {"bullish", "bearish", "neutral"}

    def test_onchain_alpha_insufficient_data(self):
        engine = AlphaEngine()
        signal = engine.onchain_alpha(np.array([[100.0, 90.0]]))
        assert signal.direction in {"bullish", "bearish", "neutral"}


class TestRevenueEngine:
    def test_scan_market_gaps(self):
        engine = RevenueEngine()
        demand = {"saas": 1000.0, "api": 500.0, "consulting": 200.0}
        supply = {"saas": 300.0, "api": 450.0, "consulting": 250.0}
        ops = engine.scan_market_gaps(demand, supply)
        assert isinstance(ops, list)
        assert all(isinstance(o, RevenueOpportunity) for o in ops)

    def test_build_vs_buy_prefers_build(self):
        engine = RevenueEngine()
        result = engine.build_vs_buy(build_cost=1000.0, build_time=2.0, buy_cost=5000.0, expected_revenue=10000.0)
        assert result["decision"] == "build"
        assert result["build_roi"] > 0.0

    def test_build_vs_buy_prefers_buy(self):
        engine = RevenueEngine()
        result = engine.build_vs_buy(build_cost=10000.0, build_time=6.0, buy_cost=500.0, expected_revenue=1000.0)
        assert result["decision"] == "buy"

    def test_optimize_pricing(self):
        engine = RevenueEngine()
        result = engine.optimize_pricing(demand_elasticity=-1.5, competitor_prices=[10.0, 12.0, 11.0], cost_basis=5.0)
        assert result["optimal_price"] > 5.0
        assert result["expected_profit"] >= 0.0

    def test_optimize_pricing_no_competitors(self):
        engine = RevenueEngine()
        result = engine.optimize_pricing(demand_elasticity=-1.5, competitor_prices=[], cost_basis=5.0)
        assert result["optimal_price"] > 0.0

    def test_evaluate_task_arbitrage_viable(self):
        engine = RevenueEngine()
        result = engine.evaluate_task_arbitrage(task_price=200.0, completion_cost=50.0, time_hours=5.0)
        assert result["viable"] is True
        assert result["hourly_rate"] == 30.0

    def test_evaluate_task_arbitrage_not_viable(self):
        engine = RevenueEngine()
        result = engine.evaluate_task_arbitrage(task_price=50.0, completion_cost=60.0, time_hours=1.0)
        assert result["viable"] is False
        assert result["net_profit"] == -10.0


class TestRiskManager:
    def test_kelly_criterion(self):
        rm = RiskManager()
        kelly = rm.kelly_criterion(win_prob=0.6, win_loss_ratio=2.0)
        assert 0.0 < kelly <= 1.0

    def test_kelly_criterion_zero_ratio(self):
        rm = RiskManager()
        kelly = rm.kelly_criterion(win_prob=0.6, win_loss_ratio=0.0)
        assert kelly == 0.0

    def test_correlation_heatmap(self):
        rm = RiskManager()
        returns = np.random.randn(100, 5)
        heatmap = rm.correlation_heatmap(returns)
        assert heatmap.shape == (5, 5)
        assert np.allclose(np.diag(heatmap), 1.0)

    def test_correlation_heatmap_invalid(self):
        rm = RiskManager()
        with pytest.raises(ValueError):
            rm.correlation_heatmap(np.array([1, 2, 3]))

    def test_check_drawdown(self):
        rm = RiskManager()
        dd = rm.check_drawdown(current_balance=90.0, peak_balance=100.0)
        assert dd == pytest.approx(0.1, rel=1e-3)

    def test_check_drawdown_zero_peak(self):
        rm = RiskManager()
        dd = rm.check_drawdown(current_balance=0.0, peak_balance=0.0)
        assert dd == 0.0

    def test_enforce_survival_reserve(self):
        rm = RiskManager()
        reserve = rm.enforce_survival_reserve(100.0)
        assert reserve == 10.0

    def test_max_position_size(self):
        rm = RiskManager()
        assessment = rm.max_position_size(balance=100.0, tier=0, edge=0.6)
        assert isinstance(assessment, RiskAssessment)
        assert assessment.survival_reserve == 10.0
        assert assessment.tier_cap == 0.02

    def test_max_position_size_zero_tradable(self):
        rm = RiskManager()
        assessment = rm.max_position_size(balance=0.0, tier=0, edge=0.6)
        assert assessment.approved is False
        assert assessment.position_size_pct == 0.0


class TestBacktester:
    def test_run_backtest(self):
        bt = Backtester()

        def strategy(data: np.ndarray) -> str:
            if len(data) < 3:
                return "hold"
            if data[-1] > data[-2]:
                return "buy"
            return "sell"

        prices = np.array([100.0, 101.0, 102.0, 101.0, 100.0, 99.0, 100.0])
        result = bt.run_backtest(strategy, prices, initial_balance=1000.0, cost_per_trade=1.0)
        assert isinstance(result, BacktestResult)
        assert result.trades_executed >= 0
        assert 0.0 <= result.win_rate <= 1.0
        assert 0.0 <= result.max_drawdown <= 1.0

    def test_run_backtest_no_trades(self):
        bt = Backtester()

        def strategy(data: np.ndarray) -> str:
            return "hold"

        prices = np.array([100.0, 101.0, 102.0])
        result = bt.run_backtest(strategy, prices, initial_balance=1000.0, cost_per_trade=1.0)
        assert result.trades_executed == 0
        assert result.total_return == 0.0

    def test_sharpe_ratio_computation(self):
        bt = Backtester()
        returns = np.array([0.01, 0.02, -0.01, 0.015, 0.005])
        sharpe = bt._compute_sharpe(returns)
        assert sharpe > 0.0

    def test_sharpe_ratio_zero_std(self):
        bt = Backtester()
        sharpe = bt._compute_sharpe(np.array([0.0, 0.0, 0.0]))
        assert sharpe == 0.0


class TestDataclasses:
    def test_alpha_signal_immutability(self):
        signal = AlphaSignal(direction="bullish", strength=0.8, confidence=0.9, expected_horizon="short")
        with pytest.raises(FrozenInstanceError):
            signal.strength = 0.5

    def test_revenue_opportunity_immutability(self):
        opp = RevenueOpportunity(niche="saas", expected_roi=2.0, risk_score=0.3, time_to_capture="fast", confidence=0.8)
        with pytest.raises(FrozenInstanceError):
            opp.expected_roi = 3.0

    def test_risk_assessment_immutability(self):
        ra = RiskAssessment(
            position_size_pct=0.01,
            max_drawdown_pct=0.05,
            survival_reserve=10.0,
            kelly_fraction=0.02,
            tier_cap=0.02,
            approved=True,
        )
        with pytest.raises(FrozenInstanceError):
            ra.approved = False

    def test_backtest_result_immutability(self):
        br = BacktestResult(total_return=0.1, sharpe_ratio=1.2, max_drawdown=0.05, win_rate=0.6, trades_executed=10)
        with pytest.raises(FrozenInstanceError):
            br.total_return = 0.2
