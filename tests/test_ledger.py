"""Comprehensive pytest suite for the ÆON ledger."""

from __future__ import annotations

import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from auton.ledger import (
    BurnAnalyzer,
    CostCategory,
    CostTracker,
    InsufficientFundsError,
    LedgerError,
    MasterWallet,
    PnLEngine,
    RunwayReport,
)
from auton.ledger.master_wallet import CostReceipt


# ==================================================================== #
# MasterWallet
# ==================================================================== #
class TestMasterWallet:
    def test_initial_balance_is_zero(self):
        wallet = MasterWallet()
        assert wallet.get_balance() == 0.0

    def test_credit_increases_balance(self):
        wallet = MasterWallet()
        receipt = wallet.credit(100.0, "seed")
        assert wallet.get_balance() == 100.0
        assert isinstance(receipt, CostReceipt)
        assert receipt.amount == 100.0
        assert receipt.reason == "seed"
        assert receipt.running_balance == 100.0
        assert receipt.id == 1

    def test_credit_requires_positive_amount(self):
        wallet = MasterWallet()
        with pytest.raises(LedgerError):
            wallet.credit(0, "zero")
        with pytest.raises(LedgerError):
            wallet.credit(-5, "neg")

    def test_debit_decreases_balance(self):
        wallet = MasterWallet()
        wallet.credit(100.0, "seed")
        receipt = wallet.debit(30.0, "compute")
        assert wallet.get_balance() == 70.0
        assert receipt.amount == 30.0
        assert receipt.running_balance == 70.0

    def test_debit_requires_positive_amount(self):
        wallet = MasterWallet()
        wallet.credit(10.0, "seed")
        with pytest.raises(LedgerError):
            wallet.debit(0, "zero")
        with pytest.raises(LedgerError):
            wallet.debit(-5, "neg")

    def test_debit_raises_on_insufficient_funds(self):
        wallet = MasterWallet()
        wallet.credit(10.0, "seed")
        with pytest.raises(InsufficientFundsError):
            wallet.debit(20.0, "overspend")

    def test_transaction_history_order_and_limit(self):
        wallet = MasterWallet()
        wallet.credit(10.0, "a")
        wallet.credit(20.0, "b")
        wallet.debit(5.0, "c")
        history = list(wallet.get_transaction_history(limit=2))
        assert len(history) == 2
        assert history[0].reason == "c"
        assert history[1].reason == "b"

    def test_transaction_history_receipt_fields(self):
        wallet = MasterWallet()
        wallet.credit(42.0, "test")
        tx = next(wallet.get_transaction_history(limit=1))
        assert tx.id == 1
        assert tx.amount == 42.0
        assert tx.reason == "test"
        assert tx.running_balance == 42.0
        assert isinstance(tx.timestamp, datetime)

    def test_persistence_across_instances(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        wallet1 = MasterWallet(path)
        wallet1.credit(500.0, "persist")
        del wallet1
        wallet2 = MasterWallet(path)
        assert wallet2.get_balance() == 500.0
        Path(path).unlink()

    def test_atomicity_of_debit(self):
        wallet = MasterWallet()
        wallet.credit(100.0, "seed")
        with pytest.raises(InsufficientFundsError):
            wallet.debit(200.0, "too_much")
        assert wallet.get_balance() == 100.0


# ==================================================================== #
# CostTracker
# ==================================================================== #
class TestCostTracker:
    def test_record_cost_deducts_wallet(self):
        wallet = MasterWallet()
        wallet.credit(1000.0, "seed")
        tracker = CostTracker(wallet)
        tracker.record_cost(CostCategory.COMPUTE, 50.0, "AWS")
        assert wallet.get_balance() == 950.0

    def test_record_cost_invalid_amount(self):
        wallet = MasterWallet()
        wallet.credit(100.0, "seed")
        tracker = CostTracker(wallet)
        with pytest.raises(LedgerError):
            tracker.record_cost(CostCategory.COMPUTE, 0)
        with pytest.raises(LedgerError):
            tracker.record_cost(CostCategory.COMPUTE, -10)

    def test_daily_costs_aggregation(self):
        wallet = MasterWallet()
        wallet.credit(10_000.0, "seed")
        tracker = CostTracker(wallet)
        tracker.record_cost(CostCategory.INFERENCE, 10.0, "gpt-4")
        tracker.record_cost(CostCategory.COMPUTE, 20.0, "ec2")
        tracker.record_cost(CostCategory.INFERENCE, 5.0, "gpt-4")
        daily = list(tracker.get_daily_costs(days=1))
        assert len(daily) == 1
        assert daily[0].total == 35.0
        assert daily[0].by_category[CostCategory.INFERENCE] == 15.0
        assert daily[0].by_category[CostCategory.COMPUTE] == 20.0

    def test_cost_breakdown(self):
        wallet = MasterWallet()
        wallet.credit(10_000.0, "seed")
        tracker = CostTracker(wallet)
        tracker.record_cost(CostCategory.TRADING_FEE, 1.0)
        tracker.record_cost(CostCategory.TRADING_FEE, 2.0)
        tracker.record_cost(CostCategory.LABOR, 5.0)
        breakdown = tracker.get_cost_breakdown(days=1)
        assert breakdown[CostCategory.TRADING_FEE] == 3.0
        assert breakdown[CostCategory.LABOR] == 5.0

    def test_cost_history(self):
        wallet = MasterWallet()
        wallet.credit(100.0, "seed")
        tracker = CostTracker(wallet)
        tracker.record_cost(CostCategory.EGRESS, 5.0, "cdn")
        hist = list(tracker.get_cost_history(limit=1))
        assert len(hist) == 1
        assert hist[0].category == CostCategory.EGRESS
        assert hist[0].amount == 5.0
        assert hist[0].details == "cdn"

    def test_cost_tracker_obeys_insufficient_funds(self):
        wallet = MasterWallet()
        tracker = CostTracker(wallet)
        with pytest.raises(InsufficientFundsError):
            tracker.record_cost(CostCategory.COMPUTE, 1.0)

    def test_daily_costs_empty_when_no_data(self):
        wallet = MasterWallet()
        wallet.credit(100.0, "seed")
        tracker = CostTracker(wallet)
        daily = list(tracker.get_daily_costs(days=1))
        assert daily == []


# ==================================================================== #
# PnLEngine
# ==================================================================== #
class TestPnLEngine:
    def test_record_trade_realized_pnl(self):
        engine = PnLEngine()
        trade = engine.record_trade(
            symbol="BTCUSD",
            entry_price=Decimal("30000"),
            exit_price=Decimal("31000"),
            quantity=Decimal("1"),
            fees=Decimal("10"),
        )
        assert trade.pnl == Decimal("990")
        assert engine.get_realized_pnl() == Decimal("990")

    def test_record_trade_loss(self):
        engine = PnLEngine()
        engine.record_trade(
            symbol="ETHUSD",
            entry_price=Decimal("2000"),
            exit_price=Decimal("1800"),
            quantity=Decimal("2"),
            fees=Decimal("5"),
        )
        assert engine.get_realized_pnl() == Decimal("-405")

    def test_realized_pnl_by_symbol(self):
        engine = PnLEngine()
        engine.record_trade("A", Decimal("10"), Decimal("12"), Decimal("1"))
        engine.record_trade("B", Decimal("100"), Decimal("90"), Decimal("1"))
        assert engine.get_realized_pnl("A") == Decimal("2")
        assert engine.get_realized_pnl("B") == Decimal("-10")

    def test_unrealized_pnl(self):
        engine = PnLEngine()
        engine.add_position("BTCUSD", Decimal("2"), Decimal("30000"))
        prices = {"BTCUSD": Decimal("32000")}
        assert engine.get_unrealized_pnl(prices) == Decimal("4000")

    def test_unrealized_pnl_missing_price(self):
        engine = PnLEngine()
        engine.add_position("BTCUSD", Decimal("1"), Decimal("30000"))
        assert engine.get_unrealized_pnl({}) == Decimal("0")

    def test_add_position_validation(self):
        engine = PnLEngine()
        with pytest.raises(ValueError):
            engine.add_position("X", Decimal("0"), Decimal("1"))
        with pytest.raises(ValueError):
            engine.add_position("X", Decimal("1"), Decimal("-1"))

    def test_record_trade_validation(self):
        engine = PnLEngine()
        with pytest.raises(ValueError):
            engine.record_trade("X", Decimal("1"), Decimal("1"), Decimal("0"))
        with pytest.raises(ValueError):
            engine.record_trade("X", Decimal("-1"), Decimal("1"), Decimal("1"))

    def test_reconcile(self):
        engine = PnLEngine()
        engine.add_position("BTCUSD", Decimal("1.5"), Decimal("30000"))
        engine.add_position("BTCUSD", Decimal("0.5"), Decimal("31000"))
        assert engine.reconcile()["BTCUSD"] == Decimal("2")

    def test_open_positions_iterable(self):
        engine = PnLEngine()
        engine.add_position("A", Decimal("1"), Decimal("10"))
        engine.add_position("B", Decimal("2"), Decimal("20"))
        positions = list(engine.get_open_positions())
        assert len(positions) == 2
        assert {p.symbol for p in positions} == {"A", "B"}

    def test_fifo_consumption(self):
        engine = PnLEngine()
        engine.add_position("BTCUSD", Decimal("1"), Decimal("30000"))
        engine.add_position("BTCUSD", Decimal("1"), Decimal("31000"))
        engine.record_trade("BTCUSD", Decimal("30000"), Decimal("32000"), Decimal("1"))
        remaining = list(engine.get_open_positions())
        assert len(remaining) == 1
        assert remaining[0].cost_basis == Decimal("31000")


# ==================================================================== #
# BurnAnalyzer
# ==================================================================== #
class TestBurnAnalyzer:
    def test_project_time_to_death_basic(self):
        analyzer = BurnAnalyzer()
        td = analyzer.project_time_to_death(1000.0, 100.0)
        assert td == timedelta(days=10)

    def test_project_time_to_death_with_income(self):
        analyzer = BurnAnalyzer()
        td = analyzer.project_time_to_death(1000.0, 100.0, 20.0)
        assert td == timedelta(days=12.5)

    def test_project_time_to_death_infinite_runway(self):
        analyzer = BurnAnalyzer()
        assert analyzer.project_time_to_death(1000.0, 50.0, 50.0) == timedelta.max
        assert analyzer.project_time_to_death(1000.0, 50.0, 60.0) == timedelta.max

    def test_project_time_to_death_negative_inputs(self):
        analyzer = BurnAnalyzer()
        with pytest.raises(ValueError):
            analyzer.project_time_to_death(100, -1)
        with pytest.raises(ValueError):
            analyzer.project_time_to_death(100, 10, -1)

    def test_get_burn_rate(self):
        analyzer = BurnAnalyzer()
        assert analyzer.get_burn_rate([100.0, 200.0, 300.0]) == 200.0

    def test_get_burn_rate_empty(self):
        analyzer = BurnAnalyzer()
        assert analyzer.get_burn_rate([]) == 0.0

    def test_get_runway_basic(self):
        analyzer = BurnAnalyzer()
        report = analyzer.get_runway(1000.0, 100.0)
        assert report.balance == 1000.0
        assert report.daily_burn_rate == 100.0
        assert report.net_daily_burn == 100.0
        assert report.runway_days == 10.0
        assert report.runway_hours == 240.0
        assert report.runway_timedelta == timedelta(days=10)

    def test_get_runway_with_income(self):
        analyzer = BurnAnalyzer()
        report = analyzer.get_runway(1000.0, 100.0, 20.0)
        assert report.net_daily_burn == 80.0
        assert report.runway_days == 12.5

    def test_get_runway_infinite(self):
        analyzer = BurnAnalyzer()
        report = analyzer.get_runway(1000.0, 50.0, 50.0)
        assert report.runway_days == float("inf")
        assert report.runway_hours == float("inf")
        assert report.runway_timedelta == timedelta.max

    def test_get_runway_negative_inputs(self):
        analyzer = BurnAnalyzer()
        with pytest.raises(ValueError):
            analyzer.get_runway(100, -1)
        with pytest.raises(ValueError):
            analyzer.get_runway(100, 10, -1)

    def test_runway_report_fields(self):
        analyzer = BurnAnalyzer()
        report = analyzer.get_runway(500.0, 50.0, 10.0)
        assert isinstance(report, RunwayReport)
        assert report.daily_income == 10.0


# ==================================================================== #
# Integration / End-to-end
# ==================================================================== #
class TestLedgerIntegration:
    def test_full_cycle(self):
        wallet = MasterWallet()
        wallet.credit(10_000.0, "initial")
        tracker = CostTracker(wallet)
        tracker.record_cost(CostCategory.COMPUTE, 200.0, "aws")
        tracker.record_cost(CostCategory.INFERENCE, 50.0, "openai")
        assert wallet.get_balance() == 9750.0
        assert tracker.get_cost_breakdown(days=1)[CostCategory.COMPUTE] == 200.0

    def test_many_concurrent_debits_simulation(self):
        wallet = MasterWallet()
        wallet.credit(100.0, "seed")
        for _ in range(100):
            wallet.debit(0.5, "micro")
        assert wallet.get_balance() == 50.0
        assert len(list(wallet.get_transaction_history(limit=200))) == 101
